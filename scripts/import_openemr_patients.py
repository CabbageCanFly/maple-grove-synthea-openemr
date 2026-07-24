#!/usr/bin/env python3
"""Import Synthea patient demographics through the OpenEMR Standard API."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests
import urllib3

from detect_openemr import detect


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATIENTS_CSV = ROOT / "output/gta-100-v2/csv/patients.csv"
CLIENT_FILE = ROOT / ".local/openemr-client.json"
IMPORT_MAP_FILE = ROOT / ".local/patient-import-map.json"


def clean(value: str | None) -> str:
    return (value or "").strip()


def map_patient(row: dict[str, str]) -> dict[str, str]:
    sex = {
        "M": "Male",
        "F": "Female",
    }.get(clean(row.get("GENDER")).upper(), clean(row.get("GENDER")))

    patient = {
        "fname": clean(row.get("FIRST")),
        "lname": clean(row.get("LAST")),
        "DOB": clean(row.get("BIRTHDATE")),
        "sex": sex,
        "street": clean(row.get("ADDRESS")),
        "city": clean(row.get("CITY")),
        "state": clean(row.get("STATE")),
        "postal_code": clean(row.get("ZIP")),
    }

    optional_fields = {
        "title": clean(row.get("PREFIX")),
        "mname": clean(row.get("MIDDLE")),
    }

    patient.update(
        {
            field: value
            for field, value in optional_fields.items()
            if value
        }
    )

    missing = [
        field
        for field in ("fname", "lname", "DOB", "sex")
        if not patient.get(field)
    ]

    if missing:
        raise ValueError(
            "Patient is missing required field(s): " + ", ".join(missing)
        )

    return patient


def duplicate_key(patient: dict[str, Any]) -> tuple[str, ...]:
    return (
        clean(str(patient.get("fname"))).casefold(),
        clean(str(patient.get("lname"))).casefold(),
        clean(str(patient.get("DOB"))),
        clean(str(patient.get("postal_code"))).replace(" ", "").casefold(),
    )


def load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default

    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def get_access_token(client: dict[str, Any]) -> str:
    username = os.getenv("OPENEMR_USERNAME", "admin")
    password = os.getenv("OPENEMR_PASSWORD", "pass")

    response = requests.post(
        client["token_endpoint"],
        data={
            "grant_type": "password",
            "client_id": client["client_id"],
            "scope": client["scope"],
            "user_role": "users",
            "username": username,
            "password": password,
        },
        verify=False,
        timeout=30,
    )

    if not response.ok:
        raise RuntimeError(
            f"Token request returned HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    token = response.json().get("access_token")

    if not token:
        raise RuntimeError("OpenEMR did not return an access token.")

    return token


def get_existing_patients(
    api_base_url: str,
    token: str,
) -> list[dict[str, Any]]:
    response = requests.get(
        f"{api_base_url}/patient",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        verify=False,
        timeout=30,
    )

    if not response.ok:
        raise RuntimeError(
            f"Patient lookup returned HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    data = response.json().get("data", [])
    return data if isinstance(data, list) else []


def create_patient(
    api_base_url: str,
    token: str,
    patient: dict[str, str],
) -> dict[str, Any]:
    response = requests.post(
        f"{api_base_url}/patient",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        json=patient,
        verify=False,
        timeout=30,
    )

    try:
        body = response.json()
    except requests.JSONDecodeError:
        body = {}

    if not response.ok:
        raise RuntimeError(
            f"Patient creation returned HTTP {response.status_code}: "
            f"{response.text[:1000]}"
        )

    validation_errors = body.get("validationErrors") or []
    internal_errors = body.get("internalErrors") or []

    if validation_errors or internal_errors:
        raise RuntimeError(
            "OpenEMR rejected the patient: "
            + json.dumps(
                {
                    "validationErrors": validation_errors,
                    "internalErrors": internal_errors,
                }
            )
        )

    data = body.get("data", {})
    return data if isinstance(data, dict) else {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import Synthea patient demographics into OpenEMR."
    )
    parser.add_argument(
        "--patients-csv",
        type=Path,
        default=DEFAULT_PATIENTS_CSV,
        help="Path to Synthea patients.csv.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Number of patients to process. Default: 1.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process every patient in the CSV.",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually create patients. Without this, perform a dry run.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    patients_csv = args.patients_csv.resolve()

    try:
        if not patients_csv.is_file():
            raise RuntimeError(f"CSV was not found: {patients_csv}")

        if args.limit < 1:
            raise RuntimeError("--limit must be at least 1.")

        with patients_csv.open(
            newline="",
            encoding="utf-8-sig",
        ) as file:
            rows = list(csv.DictReader(file))

        selected_rows = rows if args.all else rows[: args.limit]

        print(f"Patients CSV: {patients_csv}")
        print(f"Patients available: {len(rows)}")
        print(f"Patients selected: {len(selected_rows)}")
        print(f"Mode: {'COMMIT' if args.commit else 'DRY RUN'}")

        if not selected_rows:
            raise RuntimeError("The patient CSV contains no patient rows.")

        mapped = [
            (clean(row.get("Id")), map_patient(row))
            for row in selected_rows
        ]

        if not args.commit:
            print()
            print("First mapped patient:")
            print(json.dumps(mapped[0][1], indent=2))
            print()
            print("No OpenEMR records were created.")
            print("Add --commit only after reviewing this mapping.")
            return 0

        if not CLIENT_FILE.is_file():
            raise RuntimeError(
                "OAuth client credentials are missing. Run "
                "register_openemr_client.py first."
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

        token = get_access_token(client)
        existing_patients = get_existing_patients(
            openemr["api_base_url"],
            token,
        )
        existing_keys = {
            duplicate_key(patient)
            for patient in existing_patients
        }

        import_map = load_json(IMPORT_MAP_FILE, {})
        created = 0
        skipped = 0
        failed = 0

        for synthea_id, patient in mapped:
            label = f"{patient['fname']} {patient['lname']}"

            if synthea_id and synthea_id in import_map:
                print(f"SKIP already imported: {label}")
                skipped += 1
                continue

            if duplicate_key(patient) in existing_keys:
                print(f"SKIP likely duplicate: {label}")
                skipped += 1
                continue

            try:
                created_patient = create_patient(
                    openemr["api_base_url"],
                    token,
                    patient,
                )
            except RuntimeError as error:
                print(f"FAILED {label}: {error}", file=sys.stderr)
                failed += 1
                continue

            openemr_identifier = (
                created_patient.get("uuid")
                or created_patient.get("id")
                or "created"
            )

            print(f"CREATED {label}: {openemr_identifier}")
            created += 1
            existing_keys.add(duplicate_key(patient))

            if synthea_id:
                import_map[synthea_id] = {
                    "openemr_identifier": openemr_identifier,
                    "name": label,
                    "DOB": patient["DOB"],
                }
                save_json(IMPORT_MAP_FILE, import_map)

        print()
        print("Import summary")
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
        print(f"Patient import failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
