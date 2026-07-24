#!/usr/bin/env python3
"""Audit a Synthea observations.csv before OpenEMR mapping decisions."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OBSERVATIONS_CSV = ROOT / "output/gta-100-v2/csv/observations.csv"
DEFAULT_REPORT = ROOT / "output/observations-audit.json"
PATIENT_MAP_FILE = ROOT / ".local/patient-import-map.json"
ENCOUNTER_MAP_FILE = ROOT / ".local/encounter-import-map.json"

COMMON_VITAL_CODES = {
    "8302-2",   # Body height
    "29463-7",  # Body weight
    "39156-5",  # Body mass index
    "85354-9",  # Blood pressure panel
    "8480-6",   # Systolic blood pressure
    "8462-4",   # Diastolic blood pressure
    "8867-4",   # Heart rate
    "9279-1",   # Respiratory rate
    "8310-5",   # Body temperature
    "2708-6",   # Oxygen saturation
    "59408-5",  # Oxygen saturation by pulse oximetry
    "9843-4",   # Head circumference
    "72514-3",  # Pain severity
}

VITAL_DESCRIPTION_TERMS = (
    "blood pressure",
    "systolic",
    "diastolic",
    "heart rate",
    "pulse",
    "respiratory rate",
    "body temperature",
    "temperature",
    "oxygen saturation",
    "body height",
    "height",
    "body weight",
    "weight",
    "body mass index",
    "bmi",
    "head circumference",
    "pain severity",
)


def clean(value: Any) -> str:
    return str(value or "").strip()


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}

    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)

    if not isinstance(data, dict):
        raise RuntimeError(f"Expected a JSON object in {path}")

    return data


def numeric_value(value: str) -> bool:
    text = clean(value)
    if not text:
        return False

    try:
        number = float(text)
    except ValueError:
        return False

    return math.isfinite(number)


def normalize_header_map(fieldnames: list[str]) -> dict[str, str]:
    return {clean(name).upper(): name for name in fieldnames}


def field(row: dict[str, str], headers: dict[str, str], name: str) -> str:
    original = headers.get(name.upper())
    if original is None:
        return ""
    return clean(row.get(original))


def is_vital_candidate(
    category: str,
    code: str,
    description: str,
) -> bool:
    normalized_category = category.casefold().replace("_", "-").replace(" ", "-")

    if normalized_category in {
        "vital-signs",
        "vitals",
        "vital-sign",
    }:
        return True

    if code in COMMON_VITAL_CODES:
        return True

    description_lower = description.casefold()
    return any(term in description_lower for term in VITAL_DESCRIPTION_TERMS)


def exact_row_hash(row: dict[str, str], fieldnames: list[str]) -> str:
    canonical = "\x1f".join(clean(row.get(name)) for name in fieldnames)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def top_counter(counter: Counter[str], limit: int) -> list[dict[str, Any]]:
    return [
        {"value": value, "count": count}
        for value, count in counter.most_common(limit)
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit a Synthea observations.csv without assuming a fixed "
            "dataset size."
        )
    )
    parser.add_argument(
        "--observations-csv",
        type=Path,
        default=DEFAULT_OBSERVATIONS_CSV,
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Number of top values to retain in each frequency summary.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        if args.top < 1:
            raise RuntimeError("--top must be at least 1.")

        path = args.observations_csv.resolve()

        if not path.is_file():
            raise RuntimeError(f"Observations CSV was not found: {path}")

        patient_map = load_json(PATIENT_MAP_FILE)
        encounter_map = load_json(ENCOUNTER_MAP_FILE)

        row_count = 0
        patients: set[str] = set()
        encounters: set[str] = set()
        dates: list[str] = []

        missing_by_column: Counter[str] = Counter()
        category_counts: Counter[str] = Counter()
        type_counts: Counter[str] = Counter()
        unit_counts: Counter[str] = Counter()
        value_kind_counts: Counter[str] = Counter()

        code_counts: Counter[str] = Counter()
        description_counts: Counter[str] = Counter()
        code_description_counts: Counter[tuple[str, str]] = Counter()
        code_categories: dict[str, Counter[str]] = defaultdict(Counter)
        code_units: dict[str, Counter[str]] = defaultdict(Counter)
        code_types: dict[str, Counter[str]] = defaultdict(Counter)

        vital_row_count = 0
        vital_code_counts: Counter[tuple[str, str]] = Counter()
        vital_unit_counts: Counter[str] = Counter()
        vital_type_counts: Counter[str] = Counter()

        row_hash_counts: Counter[str] = Counter()

        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])

            if not fieldnames:
                raise RuntimeError("Observations CSV has no header.")

            headers = normalize_header_map(fieldnames)
            required = ("DATE", "PATIENT", "CODE", "DESCRIPTION", "VALUE")
            missing_required = [
                name for name in required if name not in headers
            ]

            if missing_required:
                raise RuntimeError(
                    "Observations CSV is missing required columns: "
                    + ", ".join(missing_required)
                )

            for row in reader:
                row_count += 1

                for name in fieldnames:
                    if not clean(row.get(name)):
                        missing_by_column[name] += 1

                date = field(row, headers, "DATE")
                patient = field(row, headers, "PATIENT")
                encounter = field(row, headers, "ENCOUNTER")
                category = field(row, headers, "CATEGORY") or "(blank)"
                code = field(row, headers, "CODE") or "(blank)"
                description = field(row, headers, "DESCRIPTION") or "(blank)"
                value = field(row, headers, "VALUE")
                units = field(row, headers, "UNITS") or "(blank)"
                value_type = field(row, headers, "TYPE") or "(blank)"

                if date:
                    dates.append(date)
                if patient:
                    patients.add(patient)
                if encounter:
                    encounters.add(encounter)

                category_counts[category] += 1
                type_counts[value_type] += 1
                unit_counts[units] += 1
                code_counts[code] += 1
                description_counts[description] += 1
                code_description_counts[(code, description)] += 1
                code_categories[code][category] += 1
                code_units[code][units] += 1
                code_types[code][value_type] += 1

                if not value:
                    value_kind_counts["empty"] += 1
                elif numeric_value(value):
                    value_kind_counts["numeric"] += 1
                else:
                    value_kind_counts["text"] += 1

                if is_vital_candidate(category, code, description):
                    vital_row_count += 1
                    vital_code_counts[(code, description)] += 1
                    vital_unit_counts[units] += 1
                    vital_type_counts[value_type] += 1

                row_hash_counts[exact_row_hash(row, fieldnames)] += 1

        duplicate_groups = sum(
            1 for count in row_hash_counts.values() if count > 1
        )
        duplicate_extra_rows = sum(
            count - 1 for count in row_hash_counts.values() if count > 1
        )

        missing_patient_mappings = sorted(
            patient for patient in patients if patient_map and patient not in patient_map
        )
        missing_encounter_mappings = sorted(
            encounter
            for encounter in encounters
            if encounter_map and encounter not in encounter_map
        )

        top_codes: list[dict[str, Any]] = []
        for (code, description), count in code_description_counts.most_common(args.top):
            top_codes.append(
                {
                    "code": code,
                    "description": description,
                    "count": count,
                    "categories": top_counter(
                        code_categories[code],
                        5,
                    ),
                    "units": top_counter(
                        code_units[code],
                        5,
                    ),
                    "types": top_counter(
                        code_types[code],
                        5,
                    ),
                }
            )

        top_vital_codes = [
            {
                "code": code,
                "description": description,
                "count": count,
            }
            for (code, description), count
            in vital_code_counts.most_common(args.top)
        ]

        report = {
            "observations_csv": str(path),
            "columns": fieldnames,
            "row_count": row_count,
            "unique_patients": len(patients),
            "unique_encounters": len(encounters),
            "date_min": min(dates) if dates else None,
            "date_max": max(dates) if dates else None,
            "missing_by_column": dict(
                sorted(missing_by_column.items())
            ),
            "exact_duplicate_groups": duplicate_groups,
            "exact_duplicate_extra_rows": duplicate_extra_rows,
            "value_kind_counts": dict(value_kind_counts),
            "category_counts": top_counter(category_counts, args.top),
            "type_counts": top_counter(type_counts, args.top),
            "unit_counts": top_counter(unit_counts, args.top),
            "top_codes": top_codes,
            "top_descriptions": top_counter(
                description_counts,
                args.top,
            ),
            "candidate_vital_signs": {
                "row_count": vital_row_count,
                "top_codes": top_vital_codes,
                "unit_counts": top_counter(
                    vital_unit_counts,
                    args.top,
                ),
                "type_counts": top_counter(
                    vital_type_counts,
                    args.top,
                ),
            },
            "mapping_checks": {
                "patient_map_present": PATIENT_MAP_FILE.is_file(),
                "encounter_map_present": ENCOUNTER_MAP_FILE.is_file(),
                "missing_patient_mapping_count": len(
                    missing_patient_mappings
                ),
                "first_missing_patient_ids": missing_patient_mappings[:20],
                "missing_encounter_mapping_count": len(
                    missing_encounter_mappings
                ),
                "first_missing_encounter_ids": missing_encounter_mappings[:20],
            },
        }

        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        print(f"Observations CSV: {path}")
        print(f"Rows: {row_count}")
        print(f"Unique patients: {len(patients)}")
        print(f"Unique encounters: {len(encounters)}")
        print(
            "Date range: "
            f"{report['date_min'] or '(none)'} to "
            f"{report['date_max'] or '(none)'}"
        )
        print(
            "Value kinds: "
            + ", ".join(
                f"{name}={count}"
                for name, count in sorted(value_kind_counts.items())
            )
        )
        print(
            "Exact duplicate rows beyond first occurrence: "
            f"{duplicate_extra_rows}"
        )
        print(
            "Missing patient mappings: "
            f"{len(missing_patient_mappings)}"
        )
        print(
            "Missing encounter mappings: "
            f"{len(missing_encounter_mappings)}"
        )
        print(f"Candidate vital-sign rows: {vital_row_count}")

        print("\nTop categories:")
        for item in top_counter(category_counts, min(args.top, 15)):
            print(f"  {item['value']}: {item['count']}")

        print("\nTop candidate vital-sign concepts:")
        for item in top_vital_codes[:15]:
            print(
                f"  {item['code']} | {item['description']}: "
                f"{item['count']}"
            )

        print(f"\nSaved report: {args.report}")
        return 0

    except (
        RuntimeError,
        OSError,
        csv.Error,
        json.JSONDecodeError,
    ) as error:
        print(f"Observation audit failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
