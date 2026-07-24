#!/usr/bin/env python3
"""Import curated Synthea allergies into OpenEMR."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import requests
import urllib3

from detect_openemr import detect
from import_openemr import get_access_token, load_json, save_json


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ALLERGIES_CSV = ROOT / "output/gta-100-v2/csv/allergies.csv"
CLIENT_FILE = ROOT / ".local/openemr-client.json"
PATIENT_MAP_FILE = ROOT / ".local/patient-import-map.json"
ENCOUNTER_MAP_FILE = ROOT / ".local/encounter-import-map.json"
ALLERGY_MAP_FILE = ROOT / ".local/allergy-import-map.json"

EXCLUDED_DESCRIPTION = "Allergic disposition (finding)"

SOURCE_FIELDS = (
    "START",
    "STOP",
    "PATIENT",
    "ENCOUNTER",
    "CODE",
    "SYSTEM",
    "DESCRIPTION",
    "TYPE",
    "CATEGORY",
    "REACTION1",
    "DESCRIPTION1",
    "SEVERITY1",
    "REACTION2",
    "DESCRIPTION2",
    "SEVERITY2",
)

SEVERITY_RANK = {
    "": 0,
    "mild": 1,
    "moderate": 2,
    "severe": 3,
}


def clean(value: Any) -> str:
    return str(value or "").strip()


def source_key(row: dict[str, str]) -> str:
    canonical = "\x1f".join(clean(row.get(field)) for field in SOURCE_FIELDS)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_allergies(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise RuntimeError(f"CSV was not found: {path}")

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        missing = [field for field in SOURCE_FIELDS if field not in fieldnames]
        if missing:
            raise RuntimeError(
                "Allergies CSV is missing columns: " + ", ".join(missing)
            )

        rows = list(reader)

    for row in rows:
        row["_source_key"] = source_key(row)

    return rows


REACTION_OPTION_BY_CODE = {
    "402387002": "allergic_angioedema",
    "39579001": "anaphylaxis",
    "49727002": "cough",
    "21626009": "cutaneous_hypersensitivity",
    "62315008": "diarrhea",
    "247472004": "hives",
    "418290006": "itching",
    "267101005": "nasal_discharge",
    "422587007": "nausea",
    "878820003": "rhinoconjunctivitis",
    "267036007": "shortness_of_breath",
    "271807003": "skin_eruption",
    "76067001": "sneezing",
    "300359004": "vomiting",
    "56018004": "wheezing",
}


def primary_reaction(row: dict[str, str]) -> tuple[str, str]:
    candidates: list[tuple[int, int, str, str]] = []

    for index in (1, 2):
        code = clean(row.get(f"REACTION{index}"))
        description = clean(row.get(f"DESCRIPTION{index}"))
        severity = clean(row.get(f"SEVERITY{index}")).lower()

        if not code and not description:
            continue

        if not code:
            raise ValueError(
                f"Reaction {index} has a description but no SNOMED code: "
                f"{description}"
            )

        option_id = REACTION_OPTION_BY_CODE.get(code)
        if option_id is None:
            raise ValueError(
                f"Unsupported Synthea reaction code {code}: {description}"
            )

        if severity and severity not in SEVERITY_RANK:
            raise ValueError(
                f"Unsupported reaction severity: {severity}"
            )

        # Higher severity wins. Negating the index makes reaction 1 win ties.
        candidates.append(
            (
                SEVERITY_RANK.get(severity, 0),
                -index,
                option_id,
                severity or "unassigned",
            )
        )

    if not candidates:
        return "unassigned", "unassigned"

    _, _, option_id, severity = max(candidates)
    return option_id, severity


def build_payload(row: dict[str, str]) -> dict[str, Any]:
    title = clean(row.get("DESCRIPTION"))
    start = clean(row.get("START"))
    stop = clean(row.get("STOP"))
    code = clean(row.get("CODE"))
    system = clean(row.get("SYSTEM")) or "Unknown"

    if not title:
        raise ValueError("Allergy DESCRIPTION is empty.")
    if not start:
        raise ValueError("Allergy START is empty.")
    if not code:
        raise ValueError("Allergy CODE is empty.")

    reaction, severity = primary_reaction(row)

    payload: dict[str, Any] = {
        "title": title,
        "begdate": start,
        "reaction": reaction,
        "severity_al": severity,
    }

    if stop:
        payload["enddate"] = stop

    # Do not pretend that "Unknown" is a real OpenEMR coding namespace.
    if system.casefold() != "unknown":
        payload["diagnosis"] = f"{system}:{code}"

    return payload


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


def normalize_date(value: Any) -> str:
    return clean(value)[:10]


def find_existing(
    existing: list[dict[str, Any]],
    row: dict[str, str],
) -> dict[str, Any] | None:
    title = clean(row.get("DESCRIPTION"))
    start = clean(row.get("START"))
    code = clean(row.get("CODE"))

    for item in existing:
        if clean(item.get("title")).casefold() != title.casefold():
            continue
        if normalize_date(item.get("begdate")) != start:
            continue

        diagnosis = clean(item.get("diagnosis"))
        if diagnosis and code not in diagnosis:
            continue

        return item

    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import curated Synthea allergies into OpenEMR."
    )
    parser.add_argument(
        "--allergies-csv",
        type=Path,
        default=DEFAULT_ALLERGIES_CSV,
    )
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--commit", action="store_true")
    parser.add_argument(
        "--include-allergic-disposition",
        action="store_true",
        help=(
            "Include generic 'Allergic disposition (finding)' rows. "
            "They are excluded by default."
        ),
    )
    parser.add_argument(
        "--category",
        choices=("environment", "food", "medication"),
        help="Import only one Synthea allergy category.",
    )
    parser.add_argument(
        "--description-contains",
        help="Filter descriptions using a case-insensitive substring.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=10,
        help="Print progress every N processed rows; use 0 to disable.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-record messages while keeping progress summaries.",
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

        rows = read_allergies(args.allergies_csv.resolve())
        candidate_rows = rows

        if not args.include_allergic_disposition:
            candidate_rows = [
                row
                for row in candidate_rows
                if clean(row.get("DESCRIPTION")) != EXCLUDED_DESCRIPTION
            ]

        if args.category:
            candidate_rows = [
                row
                for row in candidate_rows
                if clean(row.get("CATEGORY")).casefold() == args.category
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
            raise RuntimeError("No allergy rows matched the selection.")

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

        print(f"Allergies CSV: {args.allergies_csv.resolve()}")
        print(f"Allergy rows available: {len(rows)}")
        print(f"Rows excluded as generic disposition: {0 if args.include_allergic_disposition else len(rows) - len([row for row in rows if clean(row.get('DESCRIPTION')) != EXCLUDED_DESCRIPTION])}")
        print(f"Allergies matching filters: {len(candidate_rows)}")
        print(f"Selection offset: {args.offset}")
        print(f"Allergies selected: {len(selected_rows)}")
        print(f"Mode: {'COMMIT' if args.commit else 'DRY RUN'}")
        print()
        print("First mapped allergy:")
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
                    "type": clean(first.get("TYPE")),
                    "category": clean(first.get("CATEGORY")),
                    "payload": first_payload,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        print()
        print(
            "Note: The payload uses one SNOMED-mapped primary reaction and "
            "severity. Both source reactions, their codes, and their individual "
            "severities remain preserved in the local map."
        )

        if not args.commit:
            print()
            print("No OpenEMR allergies were created.")
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
        allergy_map = load_json(ALLERGY_MAP_FILE, {})
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
                    f"(created={created}, skipped={skipped}, failed={failed})",
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
                clean(patient_details.get("name"))
                or patient_source_id
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

            if key in allergy_map:
                if not args.quiet:
                    print(f"SKIP already imported: {label}", flush=True)
                skipped += 1
                print_progress()
                continue

            try:
                if patient_uuid not in existing_by_patient:
                    existing_by_patient[patient_uuid] = api_get_records(
                        session,
                        openemr["api_base_url"],
                        token,
                        f"patient/{patient_uuid}/allergy",
                    )

                existing = existing_by_patient[patient_uuid]
                matched = find_existing(existing, row)

                if matched is not None:
                    allergy_map[key] = {
                        "openemr_allergy_id": matched.get("id"),
                        "openemr_allergy_uuid": (
                            matched.get("uuid")
                            or matched.get("allergy_uuid")
                        ),
                        "openemr_patient_uuid": patient_uuid,
                        "synthea_encounter_id": encounter_source_id,
                        "status": "discovered-existing",
                    }
                    save_json(ALLERGY_MAP_FILE, allergy_map)
                    if not args.quiet:
                        print(f"SKIP found existing: {label}", flush=True)
                    skipped += 1
                    print_progress()
                    continue

                payload = build_payload(row)
                created_allergy = api_post_record(
                    session,
                    openemr["api_base_url"],
                    token,
                    f"patient/{patient_uuid}/allergy",
                    payload,
                )
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

            allergy_id = created_allergy.get("id") or "created"
            allergy_uuid = (
                created_allergy.get("uuid")
                or created_allergy.get("allergy_uuid")
                or ""
            )

            allergy_map[key] = {
                "openemr_allergy_id": allergy_id,
                "openemr_allergy_uuid": allergy_uuid,
                "openemr_patient_uuid": patient_uuid,
                "synthea_patient_id": patient_source_id,
                "synthea_encounter_id": encounter_source_id,
                "openemr_encounter": encounter_map.get(encounter_source_id),
                "source_system": clean(row.get("SYSTEM")),
                "source_code": clean(row.get("CODE")),
                "title": title,
                "type": clean(row.get("TYPE")),
                "category": clean(row.get("CATEGORY")),
                "begdate": clean(row.get("START")),
                "enddate": clean(row.get("STOP")),
                "reaction1_code": clean(row.get("REACTION1")),
                "reaction1_description": clean(row.get("DESCRIPTION1")),
                "reaction1_severity": clean(row.get("SEVERITY1")),
                "reaction2_code": clean(row.get("REACTION2")),
                "reaction2_description": clean(row.get("DESCRIPTION2")),
                "reaction2_severity": clean(row.get("SEVERITY2")),
                "status": "created",
            }
            save_json(ALLERGY_MAP_FILE, allergy_map)

            existing_by_patient[patient_uuid].append(
                {
                    **build_payload(row),
                    "id": allergy_id,
                    "uuid": allergy_uuid,
                }
            )

            if not args.quiet:
                print(
                    f"CREATED {label}: {allergy_uuid or allergy_id}",
                    flush=True,
                )
            created += 1
            print_progress()

        print()
        print("Allergy import summary")
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
        print(f"Allergy import failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
