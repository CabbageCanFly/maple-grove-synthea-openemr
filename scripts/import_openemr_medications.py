#!/usr/bin/env python3
"""Import Synthea medication episodes into the OpenEMR medication list."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import urllib3

from detect_openemr import detect
from import_openemr import get_access_token, load_json, save_json


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MEDICATIONS_CSV = ROOT / "output/gta-100-v2/csv/medications.csv"
CLIENT_FILE = ROOT / ".local/openemr-client.json"
PATIENT_MAP_FILE = ROOT / ".local/patient-import-map.json"
ENCOUNTER_MAP_FILE = ROOT / ".local/encounter-import-map.json"
MEDICATION_MAP_FILE = ROOT / ".local/medication-import-map.json"

SOURCE_FIELDS = (
    "START",
    "STOP",
    "PATIENT",
    "PAYER",
    "ENCOUNTER",
    "CODE",
    "DESCRIPTION",
    "BASE_COST",
    "PAYER_COVERAGE",
    "DISPENSES",
    "TOTALCOST",
    "REASONCODE",
    "REASONDESCRIPTION",
)


def clean(value: Any) -> str:
    return str(value or "").strip()


def source_key(row: dict[str, str]) -> str:
    canonical = "\x1f".join(clean(row.get(field)) for field in SOURCE_FIELDS)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_medications(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise RuntimeError(f"Medications CSV was not found: {path}")

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        missing = [field for field in SOURCE_FIELDS if field not in fieldnames]
        if missing:
            raise RuntimeError(
                "Medications CSV is missing columns: " + ", ".join(missing)
            )
        rows = list(reader)

    for row in rows:
        row["_source_key"] = source_key(row)

    return rows


def openemr_datetime(value: Any) -> str:
    text = clean(value)
    if not text:
        return ""

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return f"{text} 00:00:00"

    normalized = text.replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Unsupported Synthea date/time: {text}") from exc

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)

    return parsed.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


MAX_OPENEMR_TITLE_LENGTH = 255


def medication_display_title(row: dict[str, str]) -> str:
    source_title = clean(row.get("DESCRIPTION"))

    if len(source_title) <= MAX_OPENEMR_TITLE_LENGTH:
        return source_title

    # Long RxNorm pack names commonly end with:
    # Pack [Brand Name]
    match = re.search(
        r"\bPack\s*\[([^\[\]]+)\]\s*$",
        source_title,
        flags=re.IGNORECASE,
    )

    if match:
        brand = " ".join(match.group(1).split())
        concise = f"{brand} Pack"

        if len(concise) <= MAX_OPENEMR_TITLE_LENGTH:
            return concise

    # Deterministic fallback that retains the source medication code.
    code = clean(row.get("CODE"))
    suffix = f" [{code}]" if code else ""
    available = MAX_OPENEMR_TITLE_LENGTH - len(suffix) - 3

    return source_title[:available].rstrip(" ,;/") + "..." + suffix


def build_payload(row: dict[str, str]) -> dict[str, Any]:
    title = medication_display_title(row)
    begdate = openemr_datetime(row.get("START"))
    enddate = openemr_datetime(row.get("STOP"))

    if not title:
        raise ValueError("Medication DESCRIPTION is empty.")
    if not begdate:
        raise ValueError("Medication START is empty.")

    payload: dict[str, Any] = {
        "title": title,
        "begdate": begdate,
    }

    if enddate:
        payload["enddate"] = enddate

    # The Standard medication-list API has no medication-code, dosage,
    # frequency, route, dispense, cost, or source-encounter fields.
    # Preserve those source values in the local map instead of inventing data.
    return payload


def response_json(response: requests.Response, label: str) -> Any:
    if not response.ok:
        raise RuntimeError(
            f"{label} returned HTTP {response.status_code}: "
            f"{response.text[:1500]}"
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
                    }
                )
            )

        # OpenEMR can return HTTP 200 while placing field validation failures
        # directly under the field name, for example:
        # {"begdate":{"DateTime::INVALID_VALUE":"..."}}
        serialized = json.dumps(body, ensure_ascii=False)
        if "INVALID_VALUE" in serialized or "validation error" in serialized.lower():
            raise RuntimeError(f"{label} returned validation errors: {serialized}")

    return body


def response_records(response: requests.Response, label: str) -> list[dict[str, Any]]:
    body = response_json(response, label)

    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]

    if not isinstance(body, dict):
        raise RuntimeError(
            f"{label} returned unexpected JSON type: {type(body).__name__}"
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


def create_medication(
    session: requests.Session,
    api_base_url: str,
    token: str,
    pid: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    path = f"patient/{pid}/medication"
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

    body = response_json(response, f"POST {path}")

    record: dict[str, Any]
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        record = body["data"]
    elif isinstance(body, dict):
        record = body
    else:
        raise RuntimeError(
            f"POST {path} returned unexpected JSON type: "
            f"{type(body).__name__}"
        )

    medication_id = record.get("id")
    if medication_id in (None, ""):
        raise RuntimeError(
            f"POST {path} returned HTTP {response.status_code} "
            "without a medication ID: "
            + json.dumps(body, ensure_ascii=False)
        )

    return {
        "id": medication_id,
        "uuid": record.get("uuid") or "",
        "http_status": response.status_code,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import Synthea medication episodes into OpenEMR."
    )
    parser.add_argument(
        "--medications-csv",
        type=Path,
        default=DEFAULT_MEDICATIONS_CSV,
    )
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--commit", action="store_true")
    parser.add_argument(
        "--status",
        choices=("active", "stopped"),
        help="Select active rows without STOP or stopped rows with STOP.",
    )
    parser.add_argument(
        "--description-contains",
        help="Filter medication descriptions case-insensitively.",
    )
    parser.add_argument(
        "--patient-name",
        help="Filter using the patient name stored in the patient map.",
    )
    parser.add_argument(
        "--seed-existing-id",
        type=int,
        help=(
            "Record one already-created OpenEMR medication ID in the local "
            "map without issuing a POST. Requires exactly one selected row "
            "and --commit."
        ),
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=25,
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
        if args.seed_existing_id is not None and not args.commit:
            raise RuntimeError("--seed-existing-id requires --commit.")

        for required_file, label in (
            (CLIENT_FILE, "OAuth client credentials"),
            (PATIENT_MAP_FILE, "patient mapping"),
            (ENCOUNTER_MAP_FILE, "encounter mapping"),
        ):
            if not required_file.is_file():
                raise RuntimeError(f"Missing {label}: {required_file}")

        rows = read_medications(args.medications_csv.resolve())
        patient_map = load_json(PATIENT_MAP_FILE, {})
        encounter_map = load_json(ENCOUNTER_MAP_FILE, {})

        candidate_rows = rows

        if args.status == "active":
            candidate_rows = [
                row for row in candidate_rows if not clean(row.get("STOP"))
            ]
        elif args.status == "stopped":
            candidate_rows = [
                row for row in candidate_rows if clean(row.get("STOP"))
            ]

        if args.description_contains:
            needle = args.description_contains.casefold()
            candidate_rows = [
                row
                for row in candidate_rows
                if needle in clean(row.get("DESCRIPTION")).casefold()
            ]

        if args.patient_name:
            wanted_name = args.patient_name.casefold()
            candidate_rows = [
                row
                for row in candidate_rows
                if clean(
                    patient_map.get(clean(row.get("PATIENT")), {}).get("name")
                ).casefold()
                == wanted_name
            ]

        if args.all:
            selected_rows = candidate_rows[args.offset :]
        else:
            selected_rows = candidate_rows[
                args.offset : args.offset + args.limit
            ]

        if not selected_rows:
            raise RuntimeError("No medication rows matched the selection.")

        if args.seed_existing_id is not None and len(selected_rows) != 1:
            raise RuntimeError(
                "--seed-existing-id requires exactly one selected row."
            )

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
                f"{len(missing_encounters)} selected encounter(s) have no "
                f"mapping. First missing ID: {missing_encounters[0]}"
            )

        first = selected_rows[0]
        first_patient_source_id = clean(first.get("PATIENT"))
        first_patient = patient_map[first_patient_source_id]
        first_payload = build_payload(first)

        print(f"Medications CSV: {args.medications_csv.resolve()}")
        print(f"Medication rows available: {len(rows)}")
        print(f"Medications matching filters: {len(candidate_rows)}")
        print(f"Selection offset: {args.offset}")
        print(f"Medications selected: {len(selected_rows)}")
        print(f"Mode: {'COMMIT' if args.commit else 'DRY RUN'}")
        if args.seed_existing_id is not None:
            print(f"Seed existing OpenEMR medication ID: {args.seed_existing_id}")
        print()
        print("First mapped medication:")
        print(
            json.dumps(
                {
                    "source_key": clean(first.get("_source_key")),
                    "synthea_patient_id": first_patient_source_id,
                    "patient_name": first_patient.get("name"),
                    "openemr_patient_uuid": first_patient.get(
                        "openemr_identifier"
                    ),
                    "synthea_encounter_id": clean(first.get("ENCOUNTER")),
                    "openemr_encounter": encounter_map.get(
                        clean(first.get("ENCOUNTER"))
                    ),
                    "source_code": clean(first.get("CODE")),
                    "reason_code": clean(first.get("REASONCODE")),
                    "reason_description": clean(
                        first.get("REASONDESCRIPTION")
                    ),
                    "payload": first_payload,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        print()
        print(
            "Note: OpenEMR receives the medication title and start/end "
            "date-times. Source code, payer, costs, dispenses, reason, and "
            "source encounter remain in the local map."
        )

        if not args.commit:
            print()
            print("No OpenEMR medications were created.")
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
        medication_map = load_json(MEDICATION_MAP_FILE, {})
        pid_cache: dict[str, int] = {}

        created = 0
        skipped = 0
        failed = 0
        seeded = 0

        def print_progress() -> None:
            processed = created + skipped + failed + seeded
            if args.progress_every == 0:
                return
            if (
                processed % args.progress_every == 0
                or processed == len(selected_rows)
            ):
                print(
                    f"PROGRESS {processed}/{len(selected_rows)} "
                    f"(created={created}, seeded={seeded}, "
                    f"skipped={skipped}, failed={failed})",
                    flush=True,
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
                clean(patient_details.get("name")) or patient_source_id
            )
            title = clean(row.get("DESCRIPTION"))
            label = f"{title} for {patient_name} [{key[:12]}]"

            if not patient_uuid or patient_uuid == "created":
                print(
                    f"FAILED {label}: patient mapping has no UUID",
                    file=sys.stderr,
                    flush=True,
                )
                failed += 1
                print_progress()
                continue

            if key in medication_map:
                if not args.quiet:
                    print(f"SKIP already imported: {label}", flush=True)
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

                payload = build_payload(row)

                if args.seed_existing_id is not None:
                    created_medication = {
                        "id": args.seed_existing_id,
                        "uuid": "",
                        "http_status": None,
                    }
                    status = "seeded-existing"
                else:
                    created_medication = create_medication(
                        session,
                        openemr["api_base_url"],
                        token,
                        pid,
                        payload,
                    )
                    status = "created"

            except (
                RuntimeError,
                ValueError,
                requests.RequestException,
            ) as error:
                print(
                    f"FAILED {label}: {error}",
                    file=sys.stderr,
                    flush=True,
                )
                failed += 1
                print_progress()
                continue

            medication_map[key] = {
                "openemr_medication_id": created_medication["id"],
                "openemr_medication_uuid": created_medication["uuid"],
                "openemr_patient_pid": pid,
                "openemr_patient_uuid": patient_uuid,
                "synthea_patient_id": patient_source_id,
                "synthea_encounter_id": encounter_source_id,
                "openemr_encounter": encounter_map.get(encounter_source_id),
                "source_code": clean(row.get("CODE")),
                "title": title,
                "openemr_title": payload["title"],
                "begdate": clean(row.get("START")),
                "enddate": clean(row.get("STOP")),
                "payer": clean(row.get("PAYER")),
                "base_cost": clean(row.get("BASE_COST")),
                "payer_coverage": clean(row.get("PAYER_COVERAGE")),
                "dispenses": clean(row.get("DISPENSES")),
                "total_cost": clean(row.get("TOTALCOST")),
                "reason_code": clean(row.get("REASONCODE")),
                "reason_description": clean(
                    row.get("REASONDESCRIPTION")
                ),
                "http_status": created_medication["http_status"],
                "status": status,
            }

            # Save immediately after every successful creation/seed so an
            # interrupted run can resume without repeating the POST.
            save_json(MEDICATION_MAP_FILE, medication_map)

            if args.seed_existing_id is not None:
                if not args.quiet:
                    print(
                        f"SEEDED {label}: {created_medication['id']}",
                        flush=True,
                    )
                seeded += 1
            else:
                if not args.quiet:
                    print(
                        f"CREATED {label}: {created_medication['id']}",
                        flush=True,
                    )
                created += 1

            print_progress()

        print()
        print("Medication import summary")
        print(f"  Created: {created}")
        print(f"  Seeded: {seeded}")
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
        print(f"Medication import failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
