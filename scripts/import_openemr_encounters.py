#!/usr/bin/env python3
"""Import Synthea encounters through the OpenEMR Standard API.

Target strategy:
- select an existing OpenEMR facility safely instead of assuming a numeric ID;
- map Synthea providers deterministically to active authorized providers at that facility;
- mappings and imported encounter identifiers are stored under .local/.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
import urllib3

from detect_openemr import detect
from import_openemr import get_access_token, load_json, save_json


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENCOUNTERS_CSV = ROOT / "output/gta-100-v2/csv/encounters.csv"
DEFAULT_ORGANIZATIONS_CSV = ROOT / "output/gta-100-v2/csv/organizations.csv"
DEFAULT_PROVIDERS_CSV = ROOT / "output/gta-100-v2/csv/providers.csv"
CLIENT_FILE = ROOT / ".local/openemr-client.json"
PATIENT_MAP_FILE = ROOT / ".local/patient-import-map.json"
ORGANIZATION_MAP_FILE = ROOT / ".local/organization-import-map.json"
PROVIDER_MAP_FILE = ROOT / ".local/provider-import-map.json"
ENCOUNTER_MAP_FILE = ROOT / ".local/encounter-import-map.json"

PREFERRED_FACILITY_NAME = "Maple Grove Family Health Centre"
DEFAULT_PC_CATID = 9
DEFAULT_TIMEZONE = "America/Toronto"

CLASS_CODES = {
    "ambulatory": "AMB",
    "wellness": "AMB",
    "outpatient": "AMB",
    "urgentcare": "AMB",
    "emergency": "EMER",
    "inpatient": "IMP",
    "home": "HH",
    "virtual": "VR",
    "snf": "IMP",
    "hospice": "IMP",
}


def clean(value: str | None) -> str:
    return (value or "").strip()


def read_csv_by_id(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        raise RuntimeError(f"CSV was not found: {path}")

    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    result: dict[str, dict[str, str]] = {}
    for row in rows:
        source_id = clean(row.get("Id"))
        if not source_id:
            raise RuntimeError(f"A row in {path.name} is missing Id.")
        if source_id in result:
            raise RuntimeError(f"Duplicate Id in {path.name}: {source_id}")
        result[source_id] = row
    return result


def response_records(response: requests.Response, label: str) -> list[dict[str, Any]]:
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
    api_base_url: str,
    token: str,
    path: str,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    response = requests.get(
        f"{api_base_url}/{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        params=params,
        verify=False,
        timeout=30,
    )
    # OpenEMR 7 may return HTTP 404 with an empty response when a
    # patient has no encounters. Treat only that specific response as
    # an empty encounter collection so creation can continue.
    if (
        response.status_code == 404
        and path.startswith("patient/")
        and path.endswith("/encounter")
    ):
        try:
            body = response.json()
        except requests.JSONDecodeError:
            body = None

        if (
            isinstance(body, dict)
            and not (body.get("validationErrors") or [])
            and not (body.get("internalErrors") or [])
            and body.get("data") in (None, [], {})
        ):
            return []

    return response_records(response, f"GET {path}")


def api_post_record(
    api_base_url: str,
    token: str,
    path: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = requests.post(
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


def parse_synthea_datetime(value: str, timezone_name: str) -> str:
    source = clean(value)
    if not source:
        raise ValueError("Encounter START is empty.")

    try:
        parsed = datetime.fromisoformat(source.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid Synthea datetime: {source}") from exc

    if parsed.tzinfo is None:
        raise ValueError(f"Synthea datetime has no timezone: {source}")

    local = parsed.astimezone(ZoneInfo(timezone_name))
    return local.strftime("%Y-%m-%d %H:%M:%S")


def build_reason(row: dict[str, str]) -> str:
    description = clean(row.get("DESCRIPTION"))
    reason_description = clean(row.get("REASONDESCRIPTION"))

    if reason_description and reason_description.casefold() != description.casefold():
        return f"{description}; reason: {reason_description}" if description else reason_description
    return description or "Synthea encounter"


def choose_provider(
    synthea_provider_id: str,
    provider_pool: list[dict[str, Any]],
) -> dict[str, Any]:
    digest = hashlib.sha256(synthea_provider_id.encode("utf-8")).digest()
    index = int.from_bytes(digest[:8], "big") % len(provider_pool)
    return provider_pool[index]


def as_enabled(value: Any) -> bool:
    """Interpret common OpenEMR boolean representations."""

    if isinstance(value, bool):
        return value

    normalized = clean(str(value)).casefold()
    return normalized in {"1", "true", "yes", "on"}


def practitioner_pool_for_facility(
    practitioners: list[dict[str, Any]],
    facility_id: Any,
    provider_usernames: set[str],
    provider_username_prefix: str | None,
) -> list[dict[str, Any]]:
    """Return active authorized non-admin providers for one facility."""

    unique: dict[str, dict[str, Any]] = {}

    for practitioner in practitioners:
        key = practitioner.get("uuid") or practitioner.get("id")
        if key is not None:
            unique[str(key)] = practitioner

    requested = {value.casefold() for value in provider_usernames}
    prefix = clean(provider_username_prefix).casefold()

    result: list[dict[str, Any]] = []

    for practitioner in unique.values():
        username = clean(str(practitioner.get("username")))
        username_folded = username.casefold()

        if not as_enabled(practitioner.get("active")):
            continue
        if not as_enabled(practitioner.get("authorized")):
            continue
        if str(practitioner.get("facility_id")) != str(facility_id):
            continue
        if not username:
            continue

        if requested:
            if username_folded not in requested:
                continue
        else:
            if username_folded == "admin":
                continue
            if prefix and not username_folded.startswith(prefix):
                continue

        result.append(practitioner)

    return sorted(
        result,
        key=lambda item: (
            int(item.get("id") or 0),
            clean(str(item.get("username"))).casefold(),
        ),
    )


def facility_label(facility: dict[str, Any]) -> str:
    return (
        f"{clean(str(facility.get('name'))) or '(unnamed)'} "
        f"(ID {facility.get('id')})"
    )


def get_target_resources(
    api_base_url: str,
    token: str,
    facility_id: int | None,
    facility_name: str | None,
    provider_usernames: set[str],
    provider_username_prefix: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """Select a safe facility and provider pool from the target OpenEMR."""

    facilities = api_get_records(
        api_base_url,
        token,
        "facility",
        {"_count": 1000, "_offset": 0},
    )
    practitioners = api_get_records(
        api_base_url,
        token,
        "practitioner",
        {"_count": 1000, "_offset": 0},
    )

    unique_facilities: dict[str, dict[str, Any]] = {}
    for facility in facilities:
        key = facility.get("id")
        if key is not None:
            unique_facilities[str(key)] = facility

    available = sorted(
        unique_facilities.values(),
        key=lambda item: (
            int(item.get("id") or 0),
            clean(str(item.get("name"))).casefold(),
        ),
    )

    if not available:
        raise RuntimeError("OpenEMR returned no facilities.")

    selected: dict[str, Any] | None = None
    selection_strategy = ""

    if facility_id is not None:
        selected = next(
            (
                item
                for item in available
                if str(item.get("id")) == str(facility_id)
            ),
            None,
        )
        if selected is None:
            choices = ", ".join(facility_label(item) for item in available)
            raise RuntimeError(
                f"OpenEMR facility ID {facility_id} was not found. "
                f"Available facilities: {choices}"
            )
        selection_strategy = "explicit-facility-id"

    elif clean(facility_name):
        wanted = clean(facility_name).casefold()
        matches = [
            item
            for item in available
            if clean(str(item.get("name"))).casefold() == wanted
        ]
        if len(matches) != 1:
            choices = ", ".join(facility_label(item) for item in available)
            raise RuntimeError(
                f"Expected exactly one facility named {facility_name!r}; "
                f"found {len(matches)}. Available facilities: {choices}"
            )
        selected = matches[0]
        selection_strategy = "explicit-facility-name"

    else:
        preferred = [
            item
            for item in available
            if clean(str(item.get("name"))).casefold()
            == PREFERRED_FACILITY_NAME.casefold()
        ]

        preferred_with_providers = [
            item
            for item in preferred
            if practitioner_pool_for_facility(
                practitioners,
                item.get("id"),
                provider_usernames,
                provider_username_prefix,
            )
        ]

        if len(preferred_with_providers) == 1:
            selected = preferred_with_providers[0]
            selection_strategy = "preferred-maple-grove-facility"
        else:
            eligible = [
                item
                for item in available
                if practitioner_pool_for_facility(
                    practitioners,
                    item.get("id"),
                    provider_usernames,
                    provider_username_prefix,
                )
            ]

            if len(eligible) == 1:
                selected = eligible[0]
                selection_strategy = "only-facility-with-eligible-providers"
            elif not eligible:
                choices = ", ".join(
                    facility_label(item) for item in available
                )
                raise RuntimeError(
                    "No facility has an eligible provider pool. Create at least "
                    "one active, authorized non-admin provider assigned to a "
                    "facility. The provider must be visible through the "
                    f"Practitioner API. Available facilities: {choices}"
                )
            else:
                choices = ", ".join(
                    facility_label(item) for item in eligible
                )
                raise RuntimeError(
                    "Multiple facilities have eligible providers and no safe "
                    "automatic choice is possible. Name one facility "
                    f"{PREFERRED_FACILITY_NAME!r}, or run the encounter importer "
                    "with --facility-id/--facility-name. Eligible facilities: "
                    + choices
                )

    assert selected is not None

    provider_pool = practitioner_pool_for_facility(
        practitioners,
        selected.get("id"),
        provider_usernames,
        provider_username_prefix,
    )

    if not provider_pool:
        filter_note = ""
        if provider_usernames:
            filter_note = (
                " Requested usernames: "
                + ", ".join(sorted(provider_usernames))
                + "."
            )
        elif clean(provider_username_prefix):
            filter_note = (
                " Requested username prefix: "
                + clean(provider_username_prefix)
                + "."
            )

        raise RuntimeError(
            "No active authorized provider accounts were found at "
            f"{facility_label(selected)}.{filter_note} "
            "Create or enable at least one provider assigned to this facility."
        )

    return selected, provider_pool, selection_strategy


def ensure_source_mappings(
    selected_rows: list[dict[str, str]],
    organizations: dict[str, dict[str, str]],
    providers: dict[str, dict[str, str]],
    facility: dict[str, Any],
    provider_pool: list[dict[str, Any]],
    persist: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    organization_map = load_json(ORGANIZATION_MAP_FILE, {})
    provider_map = load_json(PROVIDER_MAP_FILE, {})

    for row in selected_rows:
        organization_id = clean(row.get("ORGANIZATION"))
        provider_id = clean(row.get("PROVIDER"))

        source_organization = organizations.get(organization_id)
        if source_organization is None:
            raise RuntimeError(
                f"Encounter references missing organization: {organization_id}"
            )

        source_provider = providers.get(provider_id)
        if source_provider is None:
            raise RuntimeError(f"Encounter references missing provider: {provider_id}")

        existing_organization = organization_map.get(organization_id)
        if existing_organization is not None:
            mapped_facility_id = existing_organization.get(
                "openemr_facility_id"
            )
            if str(mapped_facility_id) != str(facility.get("id")):
                raise RuntimeError(
                    "Existing organization mapping targets OpenEMR facility "
                    f"{mapped_facility_id}, but the selected facility is "
                    f"{facility.get('id')}. Use the original facility or start "
                    "with fresh target-specific .local mapping state."
                )
        else:
            organization_map[organization_id] = {
                "source_name": clean(source_organization.get("NAME")),
                "source_city": clean(source_organization.get("CITY")),
                "openemr_facility_id": facility.get("id"),
                "openemr_facility_uuid": facility.get("uuid"),
                "openemr_facility_name": facility.get("name"),
                "strategy": "map-to-selected-existing-facility",
            }

        existing_provider = provider_map.get(provider_id)
        provider_ids = {
            str(item.get("id"))
            for item in provider_pool
            if item.get("id") is not None
        }

        if existing_provider is not None:
            mapped_provider_id = existing_provider.get("openemr_provider_id")
            if str(mapped_provider_id) not in provider_ids:
                raise RuntimeError(
                    "Existing provider mapping targets OpenEMR provider "
                    f"{mapped_provider_id}, which is not in the selected "
                    "provider pool. Use the original provider setup or start "
                    "with fresh target-specific .local mapping state."
                )
        else:
            target = choose_provider(provider_id, provider_pool)
            provider_map[provider_id] = {
                "source_name": clean(source_provider.get("NAME")),
                "source_speciality": clean(source_provider.get("SPECIALITY")),
                "source_organization": clean(source_provider.get("ORGANIZATION")),
                "openemr_provider_id": target.get("id"),
                "openemr_provider_uuid": target.get("uuid"),
                "openemr_username": target.get("username"),
                "openemr_name": " ".join(
                    part
                    for part in (
                        clean(str(target.get("fname"))),
                        clean(str(target.get("lname"))),
                    )
                    if part
                ),
                "strategy": "stable-sha256-selected-provider-pool",
            }

    if persist:
        save_json(ORGANIZATION_MAP_FILE, organization_map)
        save_json(PROVIDER_MAP_FILE, provider_map)
    return organization_map, provider_map


def build_payload(
    row: dict[str, str],
    organization_map: dict[str, Any],
    provider_map: dict[str, Any],
    pc_catid: int,
    timezone_name: str,
) -> dict[str, Any]:
    encounter_class = clean(row.get("ENCOUNTERCLASS")).casefold()
    class_code = CLASS_CODES.get(encounter_class)
    if class_code is None:
        raise ValueError(f"Unsupported Synthea encounter class: {encounter_class}")

    organization = organization_map[clean(row.get("ORGANIZATION"))]
    provider = provider_map[clean(row.get("PROVIDER"))]

    return {
        "date": parse_synthea_datetime(clean(row.get("START")), timezone_name),
        "reason": build_reason(row),
        "facility_id": int(organization["openemr_facility_id"]),
        "billing_facility": int(organization["openemr_facility_id"]),
        "provider_id": int(provider["openemr_provider_id"]),
        "pc_catid": pc_catid,
        "class_code": class_code,
        "sensitivity": "normal",
        "external_id": clean(row.get("Id")),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import Synthea encounters into OpenEMR."
    )
    parser.add_argument("--encounters-csv", type=Path, default=DEFAULT_ENCOUNTERS_CSV)
    parser.add_argument("--organizations-csv", type=Path, default=DEFAULT_ORGANIZATIONS_CSV)
    parser.add_argument("--providers-csv", type=Path, default=DEFAULT_PROVIDERS_CSV)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip this many matching encounter rows before importing.",
    )
    parser.add_argument(
        "--encounter-class",
        choices=sorted(CLASS_CODES),
        help="Process only one Synthea encounter class.",
    )
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--commit", action="store_true")
    facility_group = parser.add_mutually_exclusive_group()
    facility_group.add_argument(
        "--facility-id",
        type=int,
        help=(
            "Use this OpenEMR facility ID. When omitted, prefer "
            f"{PREFERRED_FACILITY_NAME!r}, otherwise require one unambiguous "
            "facility with eligible providers."
        ),
    )
    facility_group.add_argument(
        "--facility-name",
        help="Use the OpenEMR facility with this exact name.",
    )

    provider_group = parser.add_mutually_exclusive_group()
    provider_group.add_argument(
        "--provider-username",
        action="append",
        default=[],
        help=(
            "Use only this OpenEMR provider username. Repeat to select "
            "multiple providers."
        ),
    )
    provider_group.add_argument(
        "--provider-username-prefix",
        help=(
            "Use only provider usernames beginning with this prefix. "
            "By default, all active authorized non-admin providers at the "
            "selected facility are eligible."
        ),
    )

    parser.add_argument("--pc-catid", type=int, default=DEFAULT_PC_CATID)
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100,
        help="Print progress every N processed encounters; use 0 to disable.",
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

        encounters = read_csv_by_id(args.encounters_csv.resolve())
        organizations = read_csv_by_id(args.organizations_csv.resolve())
        providers = read_csv_by_id(args.providers_csv.resolve())
        rows = list(encounters.values())

        candidate_rows = rows

        if args.encounter_class:
            candidate_rows = [
                row
                for row in candidate_rows
                if clean(row.get("ENCOUNTERCLASS")).casefold()
                == args.encounter_class.casefold()
            ]

        if args.all:
            selected_rows = candidate_rows[args.offset :]
        else:
            selected_rows = candidate_rows[
                args.offset : args.offset + args.limit
            ]

        if not selected_rows:
            raise RuntimeError("The encounter CSV contains no rows.")
        if not CLIENT_FILE.is_file():
            raise RuntimeError(
                "OAuth client credentials are missing. Run "
                "scripts/register_openemr_client.py first."
            )
        if not PATIENT_MAP_FILE.is_file():
            raise RuntimeError(
                "Patient mapping is missing. Import patients before encounters."
            )

        patient_map = load_json(PATIENT_MAP_FILE, {})
        missing_patients = sorted(
            {
                clean(row.get("PATIENT"))
                for row in selected_rows
                if clean(row.get("PATIENT")) not in patient_map
            }
        )
        if missing_patients:
            raise RuntimeError(
                f"{len(missing_patients)} selected encounter patient(s) have no "
                "OpenEMR mapping. First missing ID: " + missing_patients[0]
            )

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        openemr = detect()
        client = load_json(CLIENT_FILE, {})
        if client.get("base_url") != openemr["base_url"]:
            raise RuntimeError(
                "The saved OAuth client belongs to a different OpenEMR URL."
            )

        token = get_access_token(client)
        facility, provider_pool, facility_selection = get_target_resources(
            openemr["api_base_url"],
            token,
            args.facility_id,
            args.facility_name,
            set(args.provider_username),
            args.provider_username_prefix,
        )
        organization_map, provider_map = ensure_source_mappings(
            selected_rows,
            organizations,
            providers,
            facility,
            provider_pool,
            persist=args.commit,
        )

        first_payload = build_payload(
            selected_rows[0],
            organization_map,
            provider_map,
            args.pc_catid,
            args.timezone,
        )

        first_patient_source_id = clean(
            selected_rows[0].get("PATIENT")
        )
        first_patient = patient_map[first_patient_source_id]

        print(f"Encounters CSV: {args.encounters_csv.resolve()}")
        print(f"Encounters available: {len(rows)}")
        print(f"Encounters matching filter: {len(candidate_rows)}")
        print(f"Selection offset: {args.offset}")
        print(f"Encounters selected: {len(selected_rows)}")
        if args.encounter_class:
            print(f"Encounter class filter: {args.encounter_class}")
        print(f"Mode: {'COMMIT' if args.commit else 'DRY RUN'}")
        print(
            "Target facility: "
            f"{facility.get('name')} (ID {facility.get('id')})"
        )
        print(f"Facility selection: {facility_selection}")
        print(
            "Provider pool: "
            + ", ".join(
                f"{item.get('username')} (ID {item.get('id')})"
                for item in provider_pool
            )
        )
        print()
        print("First mapped encounter:")
        print(
            json.dumps(
                {
                    "synthea_encounter_id": clean(selected_rows[0].get("Id")),
                    "synthea_patient_id": first_patient_source_id,
                    "patient_name": first_patient.get("name"),
                    "patient_dob": first_patient.get("DOB"),
                    "openemr_patient_uuid": first_patient.get(
                        "openemr_identifier"
                    ),
                    "synthea_organization_id": clean(
                        selected_rows[0].get("ORGANIZATION")
                    ),
                    "synthea_provider_id": clean(
                        selected_rows[0].get("PROVIDER")
                    ),
                    "payload": first_payload,
                },
                indent=2,
                ensure_ascii=False,
            )
        )

        if not args.commit:
            print()
            print("No OpenEMR encounters were created.")
            print("Review the payload, then rerun with --commit.")
            return 0

        encounter_map = load_json(ENCOUNTER_MAP_FILE, {})
        created = 0
        skipped = 0
        failed = 0
        existing_encounters_by_patient: dict[str, list[dict[str, Any]]] = {}

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
            source_encounter_id = clean(row.get("Id"))
            patient_source_id = clean(row.get("PATIENT"))
            patient_uuid = clean(
                str(patient_map[patient_source_id].get("openemr_identifier"))
            )
            patient_details = patient_map[patient_source_id]
            patient_name = (
                clean(str(patient_details.get("name")))
                or patient_source_id
            )
            label = (
                f"{source_encounter_id} for "
                f"{patient_name} ({patient_source_id})"
            )

            if not patient_uuid or patient_uuid == "created":
                print(f"FAILED {label}: patient mapping has no UUID", file=sys.stderr)
                failed += 1
                print_progress()
                continue

            if source_encounter_id in encounter_map:
                print(f"SKIP already imported: {label}")
                skipped += 1
                print_progress()
                continue

            try:
                if patient_uuid not in existing_encounters_by_patient:
                    existing_encounters_by_patient[patient_uuid] = api_get_records(
                        openemr["api_base_url"],
                        token,
                        f"patient/{patient_uuid}/encounter",
                        {"_count": 1000, "_offset": 0},
                    )
                existing = existing_encounters_by_patient[patient_uuid]
                matched = next(
                    (
                        item
                        for item in existing
                        if clean(str(item.get("external_id"))) == source_encounter_id
                    ),
                    None,
                )
                if matched is not None:
                    encounter_map[source_encounter_id] = {
                        "openemr_encounter_id": matched.get("eid") or matched.get("id"),
                        "openemr_encounter_uuid": matched.get("euuid") or matched.get("uuid"),
                        "openemr_patient_uuid": patient_uuid,
                        "status": "discovered-existing",
                    }
                    save_json(ENCOUNTER_MAP_FILE, encounter_map)
                    print(f"SKIP found existing encounter: {label}")
                    skipped += 1
                    print_progress()
                    continue

                payload = build_payload(
                    row,
                    organization_map,
                    provider_map,
                    args.pc_catid,
                    args.timezone,
                )
                created_encounter = api_post_record(
                    openemr["api_base_url"],
                    token,
                    f"patient/{patient_uuid}/encounter",
                    payload,
                )
            except (RuntimeError, ValueError, requests.RequestException) as error:
                print(f"FAILED {label}: {error}", file=sys.stderr)
                failed += 1
                print_progress()
                continue

            encounter_id = (
                created_encounter.get("eid")
                or created_encounter.get("id")
                or "created"
            )
            encounter_uuid = (
                created_encounter.get("euuid")
                or created_encounter.get("uuid")
                or ""
            )

            encounter_map[source_encounter_id] = {
                "openemr_encounter_id": encounter_id,
                "openemr_encounter_uuid": encounter_uuid,
                "openemr_patient_uuid": patient_uuid,
                "openemr_provider_id": payload["provider_id"],
                "openemr_facility_id": payload["facility_id"],
                "date": payload["date"],
                "status": "created",
            }
            save_json(ENCOUNTER_MAP_FILE, encounter_map)
            print(f"CREATED {label}: {encounter_uuid or encounter_id}")
            created += 1
            print_progress()

        print()
        print("Encounter import summary")
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
        print(f"Encounter import failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
