#!/usr/bin/env python3
"""Import Synthea conditions as OpenEMR medical problems."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import requests
import urllib3

from detect_openemr import detect
from import_openemr import get_access_token, load_json, save_json


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONDITIONS_CSV = ROOT / "output/gta-100-v2/csv/conditions.csv"
CLIENT_FILE = ROOT / ".local/openemr-client.json"
PATIENT_MAP_FILE = ROOT / ".local/patient-import-map.json"
ENCOUNTER_MAP_FILE = ROOT / ".local/encounter-import-map.json"
CONDITION_MAP_FILE = ROOT / ".local/condition-import-map.json"

SOURCE_FIELDS = (
    "START",
    "STOP",
    "PATIENT",
    "ENCOUNTER",
    "SYSTEM",
    "CODE",
    "DESCRIPTION",
)


def clean(value: Any) -> str:
    return str(value or "").strip()


def read_conditions(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise RuntimeError(f"CSV was not found: {path}")

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        missing = [field for field in SOURCE_FIELDS if field not in fieldnames]
        if missing:
            raise RuntimeError(
                "Conditions CSV is missing columns: " + ", ".join(missing)
            )
        rows = list(reader)

    for row in rows:
        row["_source_key"] = source_key(row)

    return rows


def source_key(row: dict[str, str]) -> str:
    canonical = "\x1f".join(clean(row.get(field)) for field in SOURCE_FIELDS)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def semantic_tag(description: str) -> str:
    match = re.search(r"\(([^()]*)\)\s*$", clean(description))
    return match.group(1).casefold() if match else "untagged"


def response_records(
    response: requests.Response,
    label: str,
) -> list[dict[str, Any]]:
    if not response.ok:
        raise RuntimeError(
            f"{label} returned HTTP {response.status_code}: "
            f"{response.text[:1000]}"
        )

    try:
        body = response.json()
    except requests.JSONDecodeError as exc:
        raise RuntimeError(f"{label} returned invalid JSON.") from exc

    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]

    if not isinstance(body, dict):
        raise RuntimeError(
            f"{label} returned unexpected JSON type: {type(body).__name__}"
        )

    validation_errors = body.get("validationErrors") or []
    internal_errors = body.get("internalErrors") or []
    if validation_errors or internal_errors:
        raise RuntimeError(
            f"{label} returned API errors: "
            + json.dumps(
                {
                    "validationErrors": validation_errors,
                    "internalErrors": internal_errors,
                }
            )
        )

    data = body.get("data", [])
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def api_get_records(
    session: requests.Session,
    api_base_url: str,
    token: str,
    path: str,
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
    return response_records(response, f"GET {path}")


def api_post_record(
    session: requests.Session,
    api_base_url: str,
    token: str,
    path: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = session.post(
        f"{api_base_url}/{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        json=payload,
        verify=False,
        timeout=30,
    )
    records = response_records(response, f"POST {path}")
    return records[0] if records else {}


def build_payload(row: dict[str, str]) -> dict[str, Any]:
    system = clean(row.get("SYSTEM")).casefold()
    if system not in {"snomed-ct", "snomed"}:
        raise ValueError(
            f"Unsupported condition code system: {clean(row.get('SYSTEM'))}"
        )

    start = clean(row.get("START"))
    if not start:
        raise ValueError("Condition START is empty.")

    code = clean(row.get("CODE"))
    title = clean(row.get("DESCRIPTION"))
    if not code or not title:
        raise ValueError("Condition CODE or DESCRIPTION is empty.")

    return {
        "title": title,
        "begdate": start,
        "enddate": clean(row.get("STOP")) or None,
        "diagnosis": f"SNOMED:{code}",
    }


def normalize_date(value: Any) -> str:
    return clean(value)[:10]


def diagnosis_contains_code(value: Any, code: str) -> bool:
    expected = clean(code)

    if isinstance(value, dict):
        if expected in {clean(key) for key in value}:
            return True
        for item in value.values():
            if isinstance(item, dict) and clean(item.get("code")) == expected:
                return True

    if isinstance(value, list):
        return any(diagnosis_contains_code(item, expected) for item in value)

    text = clean(value)
    return text == expected or text.endswith(f":{expected}")


def find_existing(
    existing: list[dict[str, Any]],
    row: dict[str, str],
) -> dict[str, Any] | None:
    title = clean(row.get("DESCRIPTION"))
    start = clean(row.get("START"))
    stop = clean(row.get("STOP"))
    code = clean(row.get("CODE"))

    for item in existing:
        if clean(item.get("title")).casefold() != title.casefold():
            continue
        if normalize_date(item.get("begdate")) != start:
            continue
        if normalize_date(item.get("enddate")) != stop:
            continue
        if not diagnosis_contains_code(item.get("diagnosis"), code):
            continue
        return item

    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import Synthea conditions as OpenEMR medical problems."
    )
    parser.add_argument(
        "--conditions-csv",
        type=Path,
        default=DEFAULT_CONDITIONS_CSV,
    )
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--commit", action="store_true")
    parser.add_argument(
        "--status",
        choices=("active", "resolved"),
        help="Import only active or resolved conditions.",
    )
    parser.add_argument(
        "--semantic-tag",
        action="append",
        help=(
            "Filter by the final SNOMED semantic tag, such as disorder or finding. "
            "May be repeated."
        ),
    )
    parser.add_argument(
        "--description-contains",
        help="Filter descriptions using a case-insensitive substring.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100,
        help="Print progress every N processed rows; use 0 to disable.",
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

        rows = read_conditions(args.conditions_csv.resolve())
        candidate_rows = rows

        if args.status:
            candidate_rows = [
                row
                for row in candidate_rows
                if (
                    args.status == "active"
                    and not clean(row.get("STOP"))
                )
                or (
                    args.status == "resolved"
                    and clean(row.get("STOP"))
                )
            ]

        if args.semantic_tag:
            wanted_tags = {
                clean(tag).casefold()
                for tag in args.semantic_tag
                if clean(tag)
            }
            candidate_rows = [
                row
                for row in candidate_rows
                if semantic_tag(clean(row.get("DESCRIPTION"))) in wanted_tags
            ]

        if args.description_contains:
            needle = args.description_contains.casefold()
            candidate_rows = [
                row
                for row in candidate_rows
                if needle in clean(row.get("DESCRIPTION")).casefold()
            ]

        if args.all:
            selected_rows = candidate_rows[args.offset :]
        else:
            selected_rows = candidate_rows[
                args.offset : args.offset + args.limit
            ]

        if not selected_rows:
            raise RuntimeError("No condition rows matched the selection.")

        for required_file, label in (
            (CLIENT_FILE, "OAuth client credentials"),
            (PATIENT_MAP_FILE, "patient mapping"),
            (ENCOUNTER_MAP_FILE, "encounter mapping"),
        ):
            if not required_file.is_file():
                raise RuntimeError(f"Missing {label}: {required_file}")

        patient_map = load_json(PATIENT_MAP_FILE, {})
        encounter_map = load_json(ENCOUNTER_MAP_FILE, {})

        missing_patients = sorted(
            {
                clean(row.get("PATIENT"))
                for row in selected_rows
                if clean(row.get("PATIENT")) not in patient_map
            }
        )
        if missing_patients:
            raise RuntimeError(
                f"{len(missing_patients)} selected patient(s) have no mapping. "
                f"First missing ID: {missing_patients[0]}"
            )

        missing_encounters = sorted(
            {
                clean(row.get("ENCOUNTER"))
                for row in selected_rows
                if clean(row.get("ENCOUNTER")) not in encounter_map
            }
        )
        if missing_encounters:
            raise RuntimeError(
                f"{len(missing_encounters)} selected encounter(s) have no mapping. "
                f"First missing ID: {missing_encounters[0]}"
            )

        first = selected_rows[0]
        first_patient_id = clean(first.get("PATIENT"))
        first_patient = patient_map[first_patient_id]
        first_payload = build_payload(first)

        print(f"Conditions CSV: {args.conditions_csv.resolve()}")
        print(f"Conditions available: {len(rows)}")
        print(f"Conditions matching filters: {len(candidate_rows)}")
        print(f"Selection offset: {args.offset}")
        print(f"Conditions selected: {len(selected_rows)}")
        print(f"Mode: {'COMMIT' if args.commit else 'DRY RUN'}")
        print()
        print("First mapped condition:")
        print(
            json.dumps(
                {
                    "source_key": clean(first.get("_source_key")),
                    "synthea_patient_id": first_patient_id,
                    "patient_name": first_patient.get("name"),
                    "openemr_patient_uuid": first_patient.get(
                        "openemr_identifier"
                    ),
                    "synthea_encounter_id": clean(first.get("ENCOUNTER")),
                    "openemr_encounter": encounter_map.get(
                        clean(first.get("ENCOUNTER"))
                    ),
                    "semantic_tag": semantic_tag(
                        clean(first.get("DESCRIPTION"))
                    ),
                    "payload": first_payload,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        print()
        print(
            "Note: the Standard medical-problem API creates a patient problem-list "
            "entry. The source encounter is retained in the local mapping, but the "
            "API does not create an encounter issue link."
        )

        if not args.commit:
            print()
            print("No OpenEMR medical problems were created.")
            print("Review the payload, then rerun with --commit.")
            return 0

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        openemr = detect()
        client = load_json(CLIENT_FILE, {})
        if client.get("base_url") != openemr["base_url"]:
            raise RuntimeError(
                "The saved OAuth client belongs to a different OpenEMR URL."
            )

        token = get_access_token(client)
        session = requests.Session()
        condition_map = load_json(CONDITION_MAP_FILE, {})
        existing_by_patient: dict[str, list[dict[str, Any]]] = {}

        created = 0
        skipped = 0
        failed = 0

        def print_progress() -> None:
            processed = created + skipped + failed
            if args.progress_every == 0:
                return
            if (
                processed % args.progress_every == 0
                or processed == len(selected_rows)
            ):
                print(
                    f"PROGRESS {processed}/{len(selected_rows)} "
                    f"(created={created}, skipped={skipped}, failed={failed})"
                )

        for row in selected_rows:
            key = clean(row.get("_source_key"))
            patient_source_id = clean(row.get("PATIENT"))
            encounter_source_id = clean(row.get("ENCOUNTER"))
            patient_details = patient_map[patient_source_id]
            patient_uuid = clean(
                patient_details.get("openemr_identifier")
            )
            patient_name = (
                clean(patient_details.get("name"))
                or patient_source_id
            )
            title = clean(row.get("DESCRIPTION"))
            label = f"{title} for {patient_name} [{key[:12]}]"

            if not patient_uuid or patient_uuid == "created":
                print(
                    f"FAILED {label}: patient mapping has no UUID",
                    file=sys.stderr,
                )
                failed += 1
                print_progress()
                continue

            if key in condition_map:
                if not args.quiet:
                    print(f"SKIP already imported: {label}")
                skipped += 1
                print_progress()
                continue

            try:
                if patient_uuid not in existing_by_patient:
                    existing_by_patient[patient_uuid] = api_get_records(
                        session,
                        openemr["api_base_url"],
                        token,
                        f"patient/{patient_uuid}/medical_problem",
                    )

                existing = existing_by_patient[patient_uuid]
                matched = find_existing(existing, row)

                if matched is not None:
                    condition_map[key] = {
                        "openemr_condition_id": matched.get("id"),
                        "openemr_condition_uuid": (
                            matched.get("condition_uuid")
                            or matched.get("uuid")
                        ),
                        "openemr_patient_uuid": patient_uuid,
                        "synthea_encounter_id": encounter_source_id,
                        "status": "discovered-existing",
                    }
                    save_json(CONDITION_MAP_FILE, condition_map)
                    if not args.quiet:
                        print(f"SKIP found existing: {label}")
                    skipped += 1
                    print_progress()
                    continue

                payload = build_payload(row)
                created_condition = api_post_record(
                    session,
                    openemr["api_base_url"],
                    token,
                    f"patient/{patient_uuid}/medical_problem",
                    payload,
                )
            except (
                RuntimeError,
                ValueError,
                requests.RequestException,
            ) as error:
                print(f"FAILED {label}: {error}", file=sys.stderr)
                failed += 1
                print_progress()
                continue

            condition_id = created_condition.get("id") or "created"
            condition_uuid = (
                created_condition.get("condition_uuid")
                or created_condition.get("uuid")
                or ""
            )

            condition_map[key] = {
                "openemr_condition_id": condition_id,
                "openemr_condition_uuid": condition_uuid,
                "openemr_patient_uuid": patient_uuid,
                "synthea_patient_id": patient_source_id,
                "synthea_encounter_id": encounter_source_id,
                "openemr_encounter": encounter_map.get(encounter_source_id),
                "code_system": clean(row.get("SYSTEM")),
                "code": clean(row.get("CODE")),
                "title": title,
                "begdate": clean(row.get("START")),
                "enddate": clean(row.get("STOP")),
                "status": "created",
            }
            save_json(CONDITION_MAP_FILE, condition_map)

            existing_by_patient[patient_uuid].append(
                {
                    **payload,
                    "id": condition_id,
                    "uuid": condition_uuid,
                }
            )

            if not args.quiet:
                print(
                    f"CREATED {label}: "
                    f"{condition_uuid or condition_id}"
                )
            created += 1
            print_progress()

        print()
        print("Condition import summary")
        print(f"  Created: {created}")
        print(f"  Skipped: {skipped}")
        print(f"  Failed: {failed}")
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
        print(f"Condition import failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
