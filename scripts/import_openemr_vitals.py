#!/usr/bin/env python3
"""Import grouped Synthea vital-sign observations into OpenEMR encounter vitals."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
import urllib3

from detect_openemr import detect
from import_openemr import get_access_token, load_json, save_json


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OBSERVATIONS_CSV = ROOT / "output/gta-100-v2/csv/observations.csv"
CLIENT_FILE = ROOT / ".local/openemr-client.json"
PATIENT_MAP_FILE = ROOT / ".local/patient-import-map.json"
ENCOUNTER_MAP_FILE = ROOT / ".local/encounter-import-map.json"
VITAL_MAP_FILE = ROOT / ".local/vital-import-map.json"
DEFAULT_TIMEZONE = "America/Toronto"

REQUIRED_COLUMNS = (
    "DATE",
    "PATIENT",
    "ENCOUNTER",
    "CATEGORY",
    "CODE",
    "DESCRIPTION",
    "VALUE",
    "UNITS",
    "TYPE",
)

SUPPORTED_CODES = {
    "8480-6": {
        "field": "bps",
        "description": "Systolic blood pressure",
        "source_unit": "mm[Hg]",
        "target_unit": "mm[Hg]",
    },
    "8462-4": {
        "field": "bpd",
        "description": "Diastolic blood pressure",
        "source_unit": "mm[Hg]",
        "target_unit": "mm[Hg]",
    },
    "29463-7": {
        "field": "weight",
        "description": "Body weight",
        "source_unit": "kg",
        "target_unit": "lb",
    },
    "8302-2": {
        "field": "height",
        "description": "Body height",
        "source_unit": "cm",
        "target_unit": "in",
    },
    "8310-5": {
        "field": "temperature",
        "description": "Body temperature",
        "source_unit": "Cel",
        "target_unit": "degF",
    },
    "8867-4": {
        "field": "pulse",
        "description": "Heart rate",
        "source_unit": "/min",
        "target_unit": "/min",
    },
    "9279-1": {
        "field": "respiration",
        "description": "Respiratory rate",
        "source_unit": "/min",
        "target_unit": "/min",
    },
    "2708-6": {
        "field": "oxygen_saturation",
        "description": "Oxygen saturation",
        "source_unit": "%",
        "target_unit": "%",
    },
    "9843-4": {
        "field": "head_circ",
        "description": "Head circumference",
        "source_unit": "cm",
        "target_unit": "in",
    },
}


def clean(value: Any) -> str:
    return str(value or "").strip()


def stable_number(value: float, decimals: int = 2) -> str:
    rounded = round(value, decimals)
    if rounded == 0:
        rounded = 0.0
    return f"{rounded:.{decimals}f}".rstrip("0").rstrip(".")


def numeric(value: str, label: str) -> float:
    try:
        number = float(clean(value))
    except ValueError as exc:
        raise ValueError(f"{label} is not numeric: {value!r}") from exc

    if not math.isfinite(number):
        raise ValueError(f"{label} is not finite: {value!r}")

    return number


def source_key(patient_id: str, encounter_id: str, source_date: str) -> str:
    canonical = "\x1f".join((patient_id, encounter_id, source_date))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def source_signature(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        clean(row.get("code")),
        clean(row.get("description")),
        clean(row.get("value")),
        clean(row.get("units")),
    )


def openemr_datetime(value: str, timezone_name: str) -> str:
    text = clean(value)
    if not text:
        raise ValueError("Vital DATE is empty.")

    normalized = text.replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Unsupported Synthea date/time: {text}") from exc

    if parsed.tzinfo is None:
        raise ValueError(
            f"Synthea vital date/time has no timezone offset: {text}"
        )

    try:
        target_zone = ZoneInfo(timezone_name)
    except Exception as exc:
        raise ValueError(f"Unknown timezone: {timezone_name}") from exc

    return (
        parsed.astimezone(target_zone)
        .replace(tzinfo=None, microsecond=0)
        .strftime("%Y-%m-%d %H:%M:%S")
    )


def convert_field(field: str, row: dict[str, Any]) -> str:
    value = numeric(clean(row.get("value")), field)
    units = clean(row.get("units"))

    expected_units = {
        "bps": "mm[Hg]",
        "bpd": "mm[Hg]",
        "weight": "kg",
        "height": "cm",
        "temperature": "Cel",
        "pulse": "/min",
        "respiration": "/min",
        "oxygen_saturation": "%",
        "head_circ": "cm",
    }

    expected = expected_units[field]
    if units != expected:
        raise ValueError(
            f"{field} expected source unit {expected!r}, received {units!r}"
        )

    if field == "weight":
        return stable_number(value * 2.2046226218487757)

    if field in {"height", "head_circ"}:
        return stable_number(value / 2.54)

    if field == "temperature":
        return stable_number((value * 9.0 / 5.0) + 32.0)

    return stable_number(value)


def read_grouped_vitals(path: Path, timezone_name: str) -> tuple[
    list[dict[str, Any]],
    dict[str, int],
]:
    if not path.is_file():
        raise RuntimeError(f"Observations CSV was not found: {path}")

    grouped: dict[
        tuple[str, str, str],
        dict[str, list[dict[str, Any]]],
    ] = defaultdict(lambda: defaultdict(list))

    strict_vital_rows = 0
    supported_rows = 0

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        missing = [
            column for column in REQUIRED_COLUMNS if column not in fieldnames
        ]
        if missing:
            raise RuntimeError(
                "Observations CSV is missing columns: " + ", ".join(missing)
            )

        for line_number, row in enumerate(reader, start=2):
            if clean(row.get("CATEGORY")).casefold() != "vital-signs":
                continue

            strict_vital_rows += 1
            code = clean(row.get("CODE"))
            supported = SUPPORTED_CODES.get(code)

            if supported is None:
                continue

            supported_rows += 1
            patient_id = clean(row.get("PATIENT"))
            encounter_id = clean(row.get("ENCOUNTER"))
            source_date = clean(row.get("DATE"))

            if not patient_id or not encounter_id or not source_date:
                raise RuntimeError(
                    f"Supported vital row {line_number} is missing "
                    "PATIENT, ENCOUNTER, or DATE."
                )

            field = clean(supported["field"])
            grouped[(patient_id, encounter_id, source_date)][field].append(
                {
                    "line": line_number,
                    "code": code,
                    "description": clean(row.get("DESCRIPTION")),
                    "value": clean(row.get("VALUE")),
                    "units": clean(row.get("UNITS")),
                    "type": clean(row.get("TYPE")),
                }
            )

    groups: list[dict[str, Any]] = []
    exact_duplicate_extra_rows = 0
    conflicting_field_groups = 0

    for (patient_id, encounter_id, source_date), fields in grouped.items():
        selected_fields: dict[str, dict[str, Any]] = {}
        conflicts: dict[str, list[dict[str, Any]]] = {}
        exact_duplicates: dict[str, list[dict[str, Any]]] = {}

        for field, rows in fields.items():
            by_signature: dict[
                tuple[str, str, str, str],
                list[dict[str, Any]],
            ] = defaultdict(list)

            for row in rows:
                by_signature[source_signature(row)].append(row)

            if len(by_signature) == 1:
                selected_fields[field] = rows[0]
                if len(rows) > 1:
                    exact_duplicates[field] = rows
                    exact_duplicate_extra_rows += len(rows) - 1
            else:
                conflicts[field] = rows
                conflicting_field_groups += 1

        payload: dict[str, Any] = {
            "date": openemr_datetime(source_date, timezone_name),
        }

        conversion_errors: dict[str, str] = {}
        for field, row in selected_fields.items():
            try:
                payload[field] = convert_field(field, row)
            except ValueError as error:
                conversion_errors[field] = str(error)

        for field in conversion_errors:
            payload.pop(field, None)

        key = source_key(patient_id, encounter_id, source_date)

        groups.append(
            {
                "source_key": key,
                "synthea_patient_id": patient_id,
                "synthea_encounter_id": encounter_id,
                "source_date": source_date,
                "payload": payload,
                "selected_source_fields": selected_fields,
                "exact_duplicates": exact_duplicates,
                "conflicts": conflicts,
                "conversion_errors": conversion_errors,
                "importable": len(payload) > 1,
            }
        )

    groups.sort(
        key=lambda item: (
            clean(item.get("source_date")),
            clean(item.get("synthea_patient_id")),
            clean(item.get("synthea_encounter_id")),
        )
    )

    stats = {
        "strict_vital_rows": strict_vital_rows,
        "supported_rows": supported_rows,
        "grouped_vital_forms": len(groups),
        "importable_groups": sum(
            1 for group in groups if group["importable"]
        ),
        "conflict_only_or_conversion_error_groups": sum(
            1 for group in groups if not group["importable"]
        ),
        "exact_duplicate_extra_rows": exact_duplicate_extra_rows,
        "conflicting_field_groups": conflicting_field_groups,
    }

    return groups, stats


def response_json(response: requests.Response, label: str) -> Any:
    if not response.ok:
        raise RuntimeError(
            f"{label} returned HTTP {response.status_code}: "
            f"{response.text[:2000]}"
        )

    try:
        body = response.json()
    except requests.JSONDecodeError as exc:
        raise RuntimeError(f"{label} returned invalid JSON.") from exc

    if isinstance(body, dict):
        validation_errors = body.get("validationErrors") or []
        internal_errors = body.get("internalErrors") or []

        if validation_errors or internal_errors:
            raise RuntimeError(
                f"{label} returned API errors: "
                + json.dumps(
                    {
                        "validationErrors": validation_errors,
                        "internalErrors": internal_errors,
                    },
                    ensure_ascii=False,
                )
            )

        serialized = json.dumps(body, ensure_ascii=False)
        error_markers = (
            "INVALID_VALUE",
            "TOO_LONG",
            "validation error",
            "not authorized",
            "insufficient scope",
        )
        if any(
            marker.casefold() in serialized.casefold()
            for marker in error_markers
        ):
            raise RuntimeError(
                f"{label} returned an API error body: {serialized}"
            )

    return body


def response_records(response: requests.Response, label: str) -> list[dict[str, Any]]:
    body = response_json(response, label)

    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]

    if not isinstance(body, dict):
        raise RuntimeError(
            f"{label} returned unexpected JSON type: "
            f"{type(body).__name__}"
        )

    data = body.get("data", body)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def api_get_records(
    session: requests.Session,
    api_base_url: str,
    token: str,
    path: str,
    *,
    empty_on_404: bool = False,
) -> list[dict[str, Any]]:
    response = session.get(
        f"{api_base_url}/{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        verify=False,
        timeout=30,
    )

    if empty_on_404 and response.status_code == 404:
        return []

    return response_records(response, f"GET {path}")


def normalized_datetime_text(value: Any) -> str:
    text = clean(value).replace("T", " ")
    if text.endswith("Z"):
        text = text[:-1]
    if "+" in text[10:]:
        text = text.split("+", 1)[0]
    return text[:19]


def optional_float(value: Any) -> float | None:
    text = clean(value)
    if not text:
        return None

    try:
        number = float(text)
    except ValueError:
        return None

    if not math.isfinite(number):
        return None

    return number


def values_close(left: float, right: float) -> bool:
    tolerance = max(0.06, abs(right) * 0.0005)
    return abs(left - right) <= tolerance


def existing_vital_id(record: dict[str, Any]) -> Any:
    return (
        record.get("vid")
        or record.get("vital_id")
        or record.get("vitalId")
        or record.get("id")
    )


def existing_vital_matches(
    group: dict[str, Any],
    record: dict[str, Any],
) -> bool:
    expected_date = normalized_datetime_text(
        group["payload"].get("date")
    )
    actual_date = normalized_datetime_text(
        record.get("date")
        or record.get("vital_date")
        or record.get("measurement_date")
    )

    if not expected_date or actual_date != expected_date:
        return False

    compared = 0

    for field, payload_value in group["payload"].items():
        if field == "date":
            continue

        actual = optional_float(record.get(field))
        if actual is None:
            return False

        expected_values: list[float] = []

        payload_number = optional_float(payload_value)
        if payload_number is not None:
            expected_values.append(payload_number)

        source_row = group["selected_source_fields"].get(field, {})
        source_number = optional_float(source_row.get("value"))
        if source_number is not None:
            expected_values.append(source_number)

        if not expected_values:
            return False

        if not any(values_close(actual, value) for value in expected_values):
            return False

        compared += 1

    return compared > 0 and existing_vital_id(record) not in (None, "")


def find_existing_vital(
    group: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    matches = [
        record
        for record in records
        if existing_vital_matches(group, record)
    ]

    if len(matches) > 1:
        ids = [existing_vital_id(record) for record in matches]
        raise RuntimeError(
            "Multiple existing OpenEMR vital forms match this source "
            f"group: {ids}"
        )

    return matches[0] if matches else None


def update_vital(
    session: requests.Session,
    api_base_url: str,
    token: str,
    pid: int,
    encounter_id: int,
    vital_id: Any,
    payload: dict[str, Any],
) -> dict[str, Any]:
    path = (
        f"patient/{pid}/encounter/{encounter_id}/vital/{vital_id}"
    )

    try:
        response = session.put(
            f"{api_base_url}/{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=payload,
            verify=False,
            timeout=30,
        )
    except requests.Timeout as exc:
        raise RuntimeError(
            f"PUT {path} timed out. The result is ambiguous; stop and "
            "inspect OpenEMR before retrying."
        ) from exc

    body = response_json(response, f"PUT {path}")

    return {
        "vital_id": vital_id,
        "form_id": None,
        "uuid": "",
        "http_status": response.status_code,
        "response": body,
    }


def resolve_numeric_pid(
    session: requests.Session,
    api_base_url: str,
    token: str,
    patient_uuid: str,
    pid_cache: dict[str, int],
) -> int:
    if patient_uuid in pid_cache:
        return pid_cache[patient_uuid]

    records = api_get_records(
        session,
        api_base_url,
        token,
        f"patient/{patient_uuid}",
    )

    if not records:
        raise RuntimeError(
            f"Patient lookup returned no record for UUID {patient_uuid}."
        )

    raw_pid = records[0].get("pid") or records[0].get("id")

    try:
        pid = int(raw_pid)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Patient lookup returned no numeric PID for UUID {patient_uuid}."
        ) from exc

    pid_cache[patient_uuid] = pid
    return pid


def candidate_records(body: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            records.append(value)
            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(body)
    return records


def create_vital(
    session: requests.Session,
    api_base_url: str,
    token: str,
    pid: int,
    encounter_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    path = f"patient/{pid}/encounter/{encounter_id}/vital"

    try:
        response = session.post(
            f"{api_base_url}/{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=payload,
            verify=False,
            timeout=30,
        )
    except requests.Timeout as exc:
        raise RuntimeError(
            f"POST {path} timed out. The result is ambiguous; stop and "
            "inspect OpenEMR before retrying."
        ) from exc

    body = response_json(response, f"POST {path}")
    records = candidate_records(body)

    vital_id: Any = None
    form_id: Any = None
    vital_uuid = ""

    for record in records:
        if vital_id in (None, ""):
            vital_id = (
                record.get("vital_id")
                or record.get("vitalId")
                or record.get("vid")
                or record.get("id")
            )

        if form_id in (None, ""):
            form_id = (
                record.get("form_id")
                or record.get("formId")
                or record.get("fid")
            )

        if not vital_uuid:
            vital_uuid = clean(
                record.get("uuid")
                or record.get("vital_uuid")
                or record.get("vitalUuid")
            )

    if vital_id in (None, "") and form_id in (None, ""):
        raise RuntimeError(
            f"POST {path} returned HTTP {response.status_code} without "
            "a vital or form ID: "
            + json.dumps(body, ensure_ascii=False)
        )

    return {
        "vital_id": vital_id,
        "form_id": form_id,
        "uuid": vital_uuid,
        "http_status": response.status_code,
        "response": body,
    }


def required_vital_scope_present(scope: str) -> bool:
    tokens = set(clean(scope).split())
    return any(token.startswith("user/vital.") for token in tokens)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Group supported Synthea vital-sign rows and import them as "
            "OpenEMR encounter vital forms."
        )
    )
    parser.add_argument(
        "--observations-csv",
        type=Path,
        default=DEFAULT_OBSERVATIONS_CSV,
    )
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--commit", action="store_true")
    parser.add_argument(
        "--patient-name",
        help="Filter using the patient name stored in the patient map.",
    )
    parser.add_argument(
        "--synthea-encounter-id",
        help="Filter to one Synthea encounter UUID.",
    )
    parser.add_argument(
        "--openemr-encounter-id",
        type=int,
        help="Filter to one mapped numeric OpenEMR encounter ID.",
    )
    parser.add_argument(
        "--source-date",
        help="Filter to one exact Synthea DATE value.",
    )
    parser.add_argument(
        "--require-field",
        action="append",
        choices=sorted({
            clean(details["field"])
            for details in SUPPORTED_CODES.values()
        }),
        help=(
            "Require an OpenEMR vital payload field. Repeat the option to "
            "require multiple fields in the same grouped form."
        ),
    )
    parser.add_argument(
        "--unmapped-only",
        action="store_true",
        help=(
            "Select only grouped forms not already recorded in the local "
            "vital import map. Useful for sequential coverage pilots."
        ),
    )
    parser.add_argument(
        "--selection-file",
        type=Path,
        help=(
            "Select the exact ordered source keys stored in a prior JSON "
            "selection manifest."
        ),
    )
    parser.add_argument(
        "--write-selection",
        type=Path,
        help=(
            "Write the selected source keys to a JSON manifest so the exact "
            "same batch can be rerun."
        ),
    )
    parser.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help="Target OpenEMR timezone used for historical dates.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=25,
        help="Print progress every N grouped forms; use 0 to disable.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-record CREATED and SKIP messages.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        if args.limit < 1:
            raise RuntimeError("--limit must be at least 1.")
        if args.offset < 0:
            raise RuntimeError("--offset cannot be negative.")
        if args.progress_every < 0:
            raise RuntimeError("--progress-every cannot be negative.")
        if args.selection_file and args.unmapped_only:
            raise RuntimeError(
                "--selection-file and --unmapped-only cannot be combined."
            )
        if args.selection_file and args.write_selection:
            raise RuntimeError(
                "--selection-file and --write-selection cannot be combined."
            )

        for required_file, label in (
            (PATIENT_MAP_FILE, "patient mapping"),
            (ENCOUNTER_MAP_FILE, "encounter mapping"),
        ):
            if not required_file.is_file():
                raise RuntimeError(f"Missing {label}: {required_file}")

        patient_map = load_json(PATIENT_MAP_FILE, {})
        encounter_map = load_json(ENCOUNTER_MAP_FILE, {})
        existing_vital_map = load_json(VITAL_MAP_FILE, {})
        groups, stats = read_grouped_vitals(
            args.observations_csv.resolve(),
            args.timezone,
        )

        candidate_groups = [
            group for group in groups if group["importable"]
        ]

        if args.patient_name:
            wanted_name = args.patient_name.casefold()
            candidate_groups = [
                group
                for group in candidate_groups
                if clean(
                    patient_map.get(
                        clean(group["synthea_patient_id"]),
                        {},
                    ).get("name")
                ).casefold()
                == wanted_name
            ]

        if args.synthea_encounter_id:
            candidate_groups = [
                group
                for group in candidate_groups
                if clean(group["synthea_encounter_id"])
                == args.synthea_encounter_id
            ]

        if args.openemr_encounter_id is not None:
            candidate_groups = [
                group
                for group in candidate_groups
                if int(
                    encounter_map.get(
                        clean(group["synthea_encounter_id"]),
                        {},
                    ).get("openemr_encounter_id", -1)
                )
                == args.openemr_encounter_id
            ]

        if args.source_date:
            candidate_groups = [
                group
                for group in candidate_groups
                if clean(group["source_date"]) == args.source_date
            ]

        required_fields = list(dict.fromkeys(args.require_field or []))
        if required_fields:
            candidate_groups = [
                group
                for group in candidate_groups
                if all(
                    field in group["payload"]
                    for field in required_fields
                )
            ]

        if args.unmapped_only:
            candidate_groups = [
                group
                for group in candidate_groups
                if clean(group["source_key"]) not in existing_vital_map
            ]

        manifest_keys: list[str] | None = None
        if args.selection_file:
            manifest = load_json(args.selection_file.resolve(), {})
            raw_keys = manifest.get("source_keys")
            if not isinstance(raw_keys, list) or not raw_keys:
                raise RuntimeError(
                    "Selection manifest must contain a non-empty "
                    "source_keys list."
                )

            manifest_keys = [clean(key) for key in raw_keys]
            if any(not key for key in manifest_keys):
                raise RuntimeError(
                    "Selection manifest contains an empty source key."
                )
            if len(set(manifest_keys)) != len(manifest_keys):
                raise RuntimeError(
                    "Selection manifest contains duplicate source keys."
                )

            by_key = {
                clean(group["source_key"]): group
                for group in candidate_groups
            }
            missing_manifest_keys = [
                key for key in manifest_keys if key not in by_key
            ]
            if missing_manifest_keys:
                raise RuntimeError(
                    f"{len(missing_manifest_keys)} selection manifest key(s) "
                    "did not match the current filtered dataset. First "
                    f"missing key: {missing_manifest_keys[0]}"
                )

            selected_groups = [by_key[key] for key in manifest_keys]
            candidate_groups = selected_groups
        elif args.all:
            selected_groups = candidate_groups[args.offset :]
        else:
            selected_groups = candidate_groups[
                args.offset : args.offset + args.limit
            ]

        if not selected_groups:
            raise RuntimeError("No grouped vital forms matched the selection.")

        if args.write_selection:
            selection_path = args.write_selection.resolve()
            selected_keys = [
                clean(group["source_key"]) for group in selected_groups
            ]
            selection_payload = {
                "resource": "vitals",
                "observations_csv": str(args.observations_csv.resolve()),
                "timezone": args.timezone,
                "source_keys": selected_keys,
            }

            if selection_path.exists():
                existing_selection = load_json(selection_path, {})
                if existing_selection != selection_payload:
                    raise RuntimeError(
                        "Selection manifest already exists with different "
                        f"content: {selection_path}"
                    )
            else:
                selection_path.parent.mkdir(parents=True, exist_ok=True)
                save_json(selection_path, selection_payload)

        missing_patients = sorted(
            {
                clean(group["synthea_patient_id"])
                for group in selected_groups
                if clean(group["synthea_patient_id"]) not in patient_map
            }
        )
        if missing_patients:
            raise RuntimeError(
                f"{len(missing_patients)} selected vital patient(s) have "
                f"no mapping. First missing ID: {missing_patients[0]}"
            )

        missing_encounters = sorted(
            {
                clean(group["synthea_encounter_id"])
                for group in selected_groups
                if clean(group["synthea_encounter_id"]) not in encounter_map
            }
        )
        if missing_encounters:
            raise RuntimeError(
                f"{len(missing_encounters)} selected vital encounter(s) "
                f"have no mapping. First missing ID: "
                f"{missing_encounters[0]}"
            )

        first = selected_groups[0]
        first_patient_id = clean(first["synthea_patient_id"])
        first_encounter_id = clean(first["synthea_encounter_id"])
        first_patient = patient_map[first_patient_id]
        first_encounter = encounter_map[first_encounter_id]

        print(f"Observations CSV: {args.observations_csv.resolve()}")
        print(f"Strict vital-sign rows: {stats['strict_vital_rows']}")
        print(f"Supported source rows: {stats['supported_rows']}")
        print(f"Grouped vital forms: {stats['grouped_vital_forms']}")
        print(f"Importable grouped forms: {stats['importable_groups']}")
        print(
            "Conflict-only/conversion-error groups: "
            f"{stats['conflict_only_or_conversion_error_groups']}"
        )
        print(
            "Exact duplicate extra rows collapsed: "
            f"{stats['exact_duplicate_extra_rows']}"
        )
        print(
            "Conflicting field groups omitted: "
            f"{stats['conflicting_field_groups']}"
        )
        print(
            "Required payload fields: "
            + (", ".join(required_fields) if required_fields else "none")
        )
        print(f"Unmapped-only selection: {args.unmapped_only}")
        print(
            "Selection manifest input: "
            + (
                str(args.selection_file.resolve())
                if args.selection_file
                else "none"
            )
        )
        print(
            "Selection manifest output: "
            + (
                str(args.write_selection.resolve())
                if args.write_selection
                else "none"
            )
        )
        print(f"Grouped forms matching filters: {len(candidate_groups)}")
        print(f"Selection offset: {args.offset}")
        print(f"Grouped forms selected: {len(selected_groups)}")
        print(f"Mode: {'COMMIT' if args.commit else 'DRY RUN'}")
        print()
        print("First mapped vital form:")
        print(
            json.dumps(
                {
                    "source_key": first["source_key"],
                    "synthea_patient_id": first_patient_id,
                    "patient_name": first_patient.get("name"),
                    "openemr_patient_uuid": first_patient.get(
                        "openemr_identifier"
                    ),
                    "synthea_encounter_id": first_encounter_id,
                    "openemr_encounter_id": first_encounter.get(
                        "openemr_encounter_id"
                    ),
                    "source_date": first["source_date"],
                    "payload": first["payload"],
                    "selected_source_fields": (
                        first["selected_source_fields"]
                    ),
                    "exact_duplicates": first["exact_duplicates"],
                    "conflicts_omitted": first["conflicts"],
                    "conversion_errors_omitted": (
                        first["conversion_errors"]
                    ),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        print()
        print(
            "Policy: exact duplicates are collapsed. Differing values for "
            "the same vital field and timestamp are preserved locally but "
            "omitted rather than resolved arbitrarily. Existing mapped forms "
            "are skipped. An unmapped matching form stops the import for "
            "explicit review rather than being overwritten or duplicated."
        )

        if not args.commit:
            print()
            print("No OpenEMR vital forms were created.")
            print("Review the payload, then rerun with --commit.")
            return 0

        if not CLIENT_FILE.is_file():
            raise RuntimeError(
                f"Missing OAuth client credentials: {CLIENT_FILE}"
            )

        urllib3.disable_warnings(
            urllib3.exceptions.InsecureRequestWarning
        )
        openemr = detect()
        client = load_json(CLIENT_FILE, {})

        if client.get("base_url") != openemr["base_url"]:
            raise RuntimeError(
                "The saved OAuth client belongs to a different OpenEMR URL."
            )

        if not required_vital_scope_present(clean(client.get("scope"))):
            raise RuntimeError(
                "The saved OAuth client has no user/vital scope. Update "
                "the registration scopes, register a newly timestamped "
                "client, enable it in OpenEMR, and retry."
            )

        token = get_access_token(client)
        session = requests.Session()
        vital_map = existing_vital_map
        pid_cache: dict[str, int] = {}

        created = 0
        skipped = 0
        failed = 0
        conflict_fields_omitted = 0
        duplicate_rows_collapsed = 0
        existing_cache: dict[tuple[int, int], list[dict[str, Any]]] = {}

        def print_progress() -> None:
            processed = created + skipped + failed
            if args.progress_every == 0:
                return
            if (
                processed % args.progress_every == 0
                or processed == len(selected_groups)
            ):
                print(
                    f"PROGRESS {processed}/{len(selected_groups)} "
                    f"(created={created}, skipped={skipped}, "
                    f"failed={failed})",
                    flush=True,
                )

        for group in selected_groups:
            key = clean(group["source_key"])
            patient_source_id = clean(group["synthea_patient_id"])
            encounter_source_id = clean(
                group["synthea_encounter_id"]
            )
            patient_details = patient_map[patient_source_id]
            patient_uuid = clean(
                patient_details.get("openemr_identifier")
            )
            patient_name = (
                clean(patient_details.get("name"))
                or patient_source_id
            )
            encounter_details = encounter_map[encounter_source_id]
            raw_encounter_id = encounter_details.get(
                "openemr_encounter_id"
            )
            label = (
                f"{group['source_date']} for {patient_name} "
                f"[{key[:12]}]"
            )

            if not patient_uuid or patient_uuid == "created":
                print(
                    f"FAILED {label}: patient mapping has no UUID",
                    file=sys.stderr,
                    flush=True,
                )
                failed += 1
                print_progress()
                continue

            try:
                openemr_encounter_id = int(raw_encounter_id)
            except (TypeError, ValueError):
                print(
                    f"FAILED {label}: encounter mapping has no numeric ID",
                    file=sys.stderr,
                    flush=True,
                )
                failed += 1
                print_progress()
                continue

            if key in vital_map:
                if not args.quiet:
                    print(
                        f"SKIP already imported: {label}",
                        flush=True,
                    )
                skipped += 1
                print_progress()
                continue

            try:
                pid = resolve_numeric_pid(
                    session,
                    openemr["api_base_url"],
                    token,
                    patient_uuid,
                    pid_cache,
                )

                cache_key = (pid, openemr_encounter_id)
                if cache_key not in existing_cache:
                    existing_cache[cache_key] = api_get_records(
                        session,
                        openemr["api_base_url"],
                        token,
                        (
                            f"patient/{pid}/encounter/"
                            f"{openemr_encounter_id}/vital"
                        ),
                        empty_on_404=True,
                    )

                existing_vital = find_existing_vital(
                    group,
                    existing_cache[cache_key],
                )

                if existing_vital is not None:
                    existing_id = existing_vital_id(existing_vital)
                    raise RuntimeError(
                        "An unmapped matching OpenEMR vital already exists "
                        f"(vital ID {existing_id}). No POST or PUT was "
                        "performed. Inspect and explicitly resolve or seed "
                        "the existing record before retrying."
                    )

                created_vital = create_vital(
                    session,
                    openemr["api_base_url"],
                    token,
                    pid,
                    openemr_encounter_id,
                    group["payload"],
                )
                import_status = "created"

            except RuntimeError as error:
                message = str(error)
                print(
                    f"FAILED {label}: {message}",
                    file=sys.stderr,
                    flush=True,
                )
                failed += 1
                print_progress()

                if "result is ambiguous" in message:
                    print(
                        "Stopping immediately because retrying an "
                        "ambiguous POST could create a duplicate.",
                        file=sys.stderr,
                        flush=True,
                    )
                    break

                continue
            except (ValueError, requests.RequestException) as error:
                print(
                    f"FAILED {label}: {error}",
                    file=sys.stderr,
                    flush=True,
                )
                failed += 1
                print_progress()
                continue

            vital_map[key] = {
                "openemr_vital_id": created_vital["vital_id"],
                "openemr_form_id": created_vital["form_id"],
                "openemr_vital_uuid": created_vital["uuid"],
                "openemr_patient_pid": pid,
                "openemr_patient_uuid": patient_uuid,
                "openemr_encounter_id": openemr_encounter_id,
                "synthea_patient_id": patient_source_id,
                "synthea_encounter_id": encounter_source_id,
                "source_date": group["source_date"],
                "payload": group["payload"],
                "selected_source_fields": (
                    group["selected_source_fields"]
                ),
                "exact_duplicates": group["exact_duplicates"],
                "conflicts_omitted": group["conflicts"],
                "conversion_errors_omitted": (
                    group["conversion_errors"]
                ),
                "http_status": created_vital["http_status"],
                "status": import_status,
            }

            save_json(VITAL_MAP_FILE, vital_map)

            conflict_fields_omitted += len(group["conflicts"])
            duplicate_rows_collapsed += sum(
                len(rows) - 1
                for rows in group["exact_duplicates"].values()
            )

            returned_id = (
                created_vital["vital_id"]
                or created_vital["form_id"]
            )

            created += 1
            if not args.quiet:
                print(
                    f"CREATED {label}: {returned_id}",
                    flush=True,
                )

            print_progress()

        print()
        print("Vital import summary")
        print(f"  Created: {created}")
        print(f"  Skipped: {skipped}")
        print(f"  Failed: {failed}")
        print(
            "  Conflicting fields omitted from created forms: "
            f"{conflict_fields_omitted}"
        )
        print(
            "  Exact duplicate rows collapsed in created forms: "
            f"{duplicate_rows_collapsed}"
        )
        print("  Access token was not printed or saved.")
        return 1 if failed else 0

    except (
        RuntimeError,
        ValueError,
        OSError,
        csv.Error,
        json.JSONDecodeError,
        requests.RequestException,
    ) as error:
        print(f"Vital import failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
