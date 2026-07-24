#!/usr/bin/env python3
"""Audit a Synthea procedures.csv before choosing an OpenEMR mapping."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROCEDURES_CSV = ROOT / "output/gta-100-v2/csv/procedures.csv"
DEFAULT_REPORT = ROOT / "output/procedures-audit.json"
PATIENT_MAP_FILE = ROOT / ".local/patient-import-map.json"
ENCOUNTER_MAP_FILE = ROOT / ".local/encounter-import-map.json"

DESCRIPTION_BUCKET_TERMS = {
    "imaging": (
        "x-ray",
        "xray",
        "radiograph",
        "computed tomography",
        " ct ",
        "mri",
        "magnetic resonance",
        "ultrasound",
        "sonogram",
        "mammogram",
        "mammography",
        "pet scan",
    ),
    "surgery_or_invasive": (
        "surgery",
        "surgical",
        "resection",
        "excision",
        "incision",
        "biopsy",
        "repair",
        "replacement",
        "transplant",
        "implant",
        "catheter",
        "endoscopy",
        "colonoscopy",
        "bronchoscopy",
        "arthroscopy",
        "ectomy",
        "otomy",
    ),
    "therapy_or_rehabilitation": (
        "therapy",
        "therapeutic",
        "rehabilitation",
        "physical therapy",
        "occupational therapy",
        "speech therapy",
        "dialysis",
        "chemotherapy",
        "radiation treatment",
    ),
    "screening_or_diagnostic": (
        "screening",
        "diagnostic",
        "examination",
        "assessment",
        "test",
        "electrocardiogram",
        "echocardiogram",
        "spirometry",
        "monitoring",
    ),
}


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


def normalize_header_map(fieldnames: list[str]) -> dict[str, str]:
    return {clean(name).upper(): name for name in fieldnames}


def field(
    row: dict[str, str],
    headers: dict[str, str],
    name: str,
) -> str:
    original = headers.get(name.upper())
    if original is None:
        return ""
    return clean(row.get(original))


def exact_row_hash(
    row: dict[str, str],
    fieldnames: list[str],
) -> str:
    canonical = "\x1f".join(clean(row.get(name)) for name in fieldnames)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def top_counter(
    counter: Counter[Any],
    limit: int,
) -> list[dict[str, Any]]:
    return [
        {"value": value, "count": count}
        for value, count in counter.most_common(limit)
    ]


def parse_cost(value: str) -> float | None:
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


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None

    ordered = sorted(values)

    if len(ordered) == 1:
        return ordered[0]

    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)

    if lower == upper:
        return ordered[lower]

    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def summarize_costs(
    values: list[float],
    blank_count: int,
    invalid_count: int,
) -> dict[str, Any]:
    negative_count = sum(1 for value in values if value < 0)
    zero_count = sum(1 for value in values if value == 0)

    if not values:
        return {
            "valid_count": 0,
            "blank_count": blank_count,
            "invalid_count": invalid_count,
            "negative_count": negative_count,
            "zero_count": zero_count,
            "min": None,
            "median": None,
            "mean": None,
            "p95": None,
            "max": None,
            "total": None,
        }

    return {
        "valid_count": len(values),
        "blank_count": blank_count,
        "invalid_count": invalid_count,
        "negative_count": negative_count,
        "zero_count": zero_count,
        "min": round(min(values), 2),
        "median": round(statistics.median(values), 2),
        "mean": round(statistics.fmean(values), 2),
        "p95": round(percentile(values, 0.95) or 0.0, 2),
        "max": round(max(values), 2),
        "total": round(sum(values), 2),
    }


def description_buckets(description: str) -> list[str]:
    normalized = f" {description.casefold()} "
    matches: list[str] = []

    for bucket, terms in DESCRIPTION_BUCKET_TERMS.items():
        if any(term in normalized for term in terms):
            matches.append(bucket)

    return matches or ["other"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit a Synthea procedures.csv without assuming a fixed "
            "dataset size or OpenEMR destination."
        )
    )
    parser.add_argument(
        "--procedures-csv",
        type=Path,
        default=DEFAULT_PROCEDURES_CSV,
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
    )
    parser.add_argument(
        "--top",
        type=int,
        default=25,
        help="Number of top frequency values retained in the report.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        if args.top < 1:
            raise RuntimeError("--top must be at least 1.")

        path = args.procedures_csv.resolve()

        if not path.is_file():
            raise RuntimeError(f"Procedures CSV was not found: {path}")

        patient_map = load_json(PATIENT_MAP_FILE)
        encounter_map = load_json(ENCOUNTER_MAP_FILE)

        row_count = 0
        patients: set[str] = set()
        encounters: set[str] = set()
        starts: list[str] = []
        stops: list[str] = []

        missing_by_column: Counter[str] = Counter()
        code_description_counts: Counter[tuple[str, str]] = Counter()
        description_counts: Counter[str] = Counter()
        reason_counts: Counter[tuple[str, str]] = Counter()
        bucket_counts: Counter[str] = Counter()
        patient_row_counts: Counter[str] = Counter()
        encounter_row_counts: Counter[str] = Counter()

        exact_hash_counts: Counter[str] = Counter()
        same_event_rows: dict[
            tuple[str, str, str, str],
            list[tuple[str, ...]],
        ] = defaultdict(list)

        costs: list[float] = []
        blank_cost_count = 0
        invalid_cost_count = 0
        rows_without_encounter = 0
        rows_without_code = 0
        rows_without_description = 0
        rows_without_reason = 0

        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])

            if not fieldnames:
                raise RuntimeError("Procedures CSV has no header.")

            headers = normalize_header_map(fieldnames)
            required = ("START", "PATIENT", "CODE", "DESCRIPTION")
            missing_required = [
                name for name in required if name not in headers
            ]

            if missing_required:
                raise RuntimeError(
                    "Procedures CSV is missing required columns: "
                    + ", ".join(missing_required)
                )

            for row in reader:
                row_count += 1

                for name in fieldnames:
                    if not clean(row.get(name)):
                        missing_by_column[name] += 1

                start = field(row, headers, "START")
                stop = field(row, headers, "STOP")
                patient = field(row, headers, "PATIENT")
                encounter = field(row, headers, "ENCOUNTER")
                code = field(row, headers, "CODE")
                description = field(row, headers, "DESCRIPTION")
                base_cost = field(row, headers, "BASE_COST")
                reason_code = field(row, headers, "REASONCODE")
                reason_description = field(
                    row,
                    headers,
                    "REASONDESCRIPTION",
                )

                if start:
                    starts.append(start)

                if stop:
                    stops.append(stop)

                if patient:
                    patients.add(patient)
                    patient_row_counts[patient] += 1

                if encounter:
                    encounters.add(encounter)
                    encounter_row_counts[encounter] += 1
                else:
                    rows_without_encounter += 1

                if not code:
                    rows_without_code += 1

                if not description:
                    rows_without_description += 1

                if not reason_code and not reason_description:
                    rows_without_reason += 1

                display_code = code or "(blank)"
                display_description = description or "(blank)"
                display_reason_code = reason_code or "(blank)"
                display_reason_description = (
                    reason_description or "(blank)"
                )

                code_description_counts[
                    (display_code, display_description)
                ] += 1
                description_counts[display_description] += 1
                reason_counts[
                    (
                        display_reason_code,
                        display_reason_description,
                    )
                ] += 1

                for bucket in description_buckets(description):
                    bucket_counts[bucket] += 1

                if not base_cost:
                    blank_cost_count += 1
                else:
                    parsed_cost = parse_cost(base_cost)
                    if parsed_cost is None:
                        invalid_cost_count += 1
                    else:
                        costs.append(parsed_cost)

                exact_hash_counts[
                    exact_row_hash(row, fieldnames)
                ] += 1

                event_key = (
                    patient,
                    encounter,
                    start,
                    code,
                )
                event_variant = (
                    description,
                    base_cost,
                    reason_code,
                    reason_description,
                )
                same_event_rows[event_key].append(event_variant)

        exact_duplicate_groups = sum(
            1 for count in exact_hash_counts.values() if count > 1
        )
        exact_duplicate_extra_rows = sum(
            count - 1
            for count in exact_hash_counts.values()
            if count > 1
        )

        repeated_event_groups = 0
        identical_repeated_event_groups = 0
        conflicting_repeated_event_groups = 0
        repeated_event_extra_rows = 0
        conflicting_examples: list[dict[str, Any]] = []

        for key, variants in same_event_rows.items():
            if len(variants) <= 1:
                continue

            repeated_event_groups += 1
            repeated_event_extra_rows += len(variants) - 1
            unique_variants = sorted(set(variants))

            if len(unique_variants) == 1:
                identical_repeated_event_groups += 1
            else:
                conflicting_repeated_event_groups += 1

                if len(conflicting_examples) < 20:
                    patient, encounter, start, code = key
                    conflicting_examples.append(
                        {
                            "patient": patient,
                            "encounter": encounter,
                            "start": start,
                            "code": code,
                            "variants": [
                                {
                                    "description": variant[0],
                                    "base_cost": variant[1],
                                    "reason_code": variant[2],
                                    "reason_description": variant[3],
                                }
                                for variant in unique_variants
                            ],
                        }
                    )

        missing_patient_mappings = sorted(
            patient
            for patient in patients
            if patient_map and patient not in patient_map
        )
        missing_encounter_mappings = sorted(
            encounter
            for encounter in encounters
            if encounter_map and encounter not in encounter_map
        )

        top_codes = [
            {
                "code": code,
                "description": description,
                "count": count,
            }
            for (code, description), count
            in code_description_counts.most_common(args.top)
        ]

        top_reasons = [
            {
                "reason_code": code,
                "reason_description": description,
                "count": count,
            }
            for (code, description), count
            in reason_counts.most_common(args.top)
        ]

        report = {
            "procedures_csv": str(path),
            "columns": fieldnames,
            "row_count": row_count,
            "unique_patients": len(patients),
            "unique_encounters": len(encounters),
            "start_min": min(starts) if starts else None,
            "start_max": max(starts) if starts else None,
            "stop_min": min(stops) if stops else None,
            "stop_max": max(stops) if stops else None,
            "rows_without_encounter": rows_without_encounter,
            "rows_without_code": rows_without_code,
            "rows_without_description": rows_without_description,
            "rows_without_reason": rows_without_reason,
            "missing_by_column": dict(
                sorted(missing_by_column.items())
            ),
            "exact_duplicates": {
                "groups": exact_duplicate_groups,
                "extra_rows": exact_duplicate_extra_rows,
            },
            "same_patient_encounter_date_code": {
                "repeated_groups": repeated_event_groups,
                "extra_rows": repeated_event_extra_rows,
                "identical_groups": identical_repeated_event_groups,
                "conflicting_groups": conflicting_repeated_event_groups,
                "first_conflicting_examples": conflicting_examples,
            },
            "base_cost": summarize_costs(
                costs,
                blank_cost_count,
                invalid_cost_count,
            ),
            "description_keyword_buckets_non_authoritative": (
                top_counter(bucket_counts, len(bucket_counts))
            ),
            "top_codes": top_codes,
            "top_descriptions": top_counter(
                description_counts,
                args.top,
            ),
            "top_reasons": top_reasons,
            "row_distribution": {
                "top_patients": top_counter(
                    patient_row_counts,
                    min(args.top, 20),
                ),
                "top_encounters": top_counter(
                    encounter_row_counts,
                    min(args.top, 20),
                ),
            },
            "mapping_checks": {
                "patient_map_present": PATIENT_MAP_FILE.is_file(),
                "encounter_map_present": ENCOUNTER_MAP_FILE.is_file(),
                "missing_patient_mapping_count": len(
                    missing_patient_mappings
                ),
                "first_missing_patient_ids": (
                    missing_patient_mappings[:20]
                ),
                "missing_encounter_mapping_count": len(
                    missing_encounter_mappings
                ),
                "first_missing_encounter_ids": (
                    missing_encounter_mappings[:20]
                ),
            },
        }

        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        print(f"Procedures CSV: {path}")
        print(f"Rows: {row_count}")
        print(f"Unique patients: {len(patients)}")
        print(f"Unique encounters: {len(encounters)}")
        print(
            "Start range: "
            f"{report['start_min'] or '(none)'} to "
            f"{report['start_max'] or '(none)'}"
        )
        print(
            "Stop range: "
            f"{report['stop_min'] or '(none)'} to "
            f"{report['stop_max'] or '(none)'}"
        )
        print(
            "Exact duplicate rows beyond first occurrence: "
            f"{exact_duplicate_extra_rows}"
        )
        print(
            "Repeated patient/encounter/date/code groups: "
            f"{repeated_event_groups}"
        )
        print(
            "Conflicting repeated groups: "
            f"{conflicting_repeated_event_groups}"
        )
        print(
            "Missing patient mappings: "
            f"{len(missing_patient_mappings)}"
        )
        print(
            "Missing encounter mappings: "
            f"{len(missing_encounter_mappings)}"
        )

        cost_summary = report["base_cost"]
        print(
            "Base cost: "
            f"valid={cost_summary['valid_count']}, "
            f"blank={cost_summary['blank_count']}, "
            f"invalid={cost_summary['invalid_count']}, "
            f"median={cost_summary['median']}, "
            f"p95={cost_summary['p95']}"
        )

        print("\nTop procedure concepts:")
        for item in top_codes[:15]:
            print(
                f"  {item['code']} | {item['description']}: "
                f"{item['count']}"
            )

        print("\nDescription keyword buckets (non-authoritative):")
        for item in top_counter(bucket_counts, len(bucket_counts)):
            print(f"  {item['value']}: {item['count']}")

        print(f"\nSaved report: {args.report}")
        return 0

    except (
        RuntimeError,
        OSError,
        csv.Error,
        json.JSONDecodeError,
    ) as error:
        print(f"Procedure audit failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
