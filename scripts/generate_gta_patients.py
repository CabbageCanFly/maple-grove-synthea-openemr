#!/usr/bin/env python3
"""Generate a versioned GTA Synthea CSV dataset and select it for import."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/synthea-gta.properties"
DEFAULT_OUTPUT_ROOT = ROOT / "output/runs"
CURRENT_DATASET_FILE = ROOT / "output/current-dataset.json"
RUN_MANIFEST_NAME = "dataset-manifest.json"
CORE_REQUIRED_CSV_FILES = (
    "patients.csv",
    "organizations.csv",
    "providers.csv",
    "encounters.csv",
)

# Synthea may omit a resource CSV entirely when a small generated population
# contains no rows for that resource. Create a header-only file so the dataset
# remains valid and downstream importers can process it as zero records.
OPTIONAL_EMPTY_CSV_HEADERS: dict[str, tuple[str, ...]] = {
    "conditions.csv": (
        "START",
        "STOP",
        "PATIENT",
        "ENCOUNTER",
        "SYSTEM",
        "CODE",
        "DESCRIPTION",
    ),
    "allergies.csv": (
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
    ),
    "medications.csv": (
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
    ),
    "observations.csv": (
        "DATE",
        "PATIENT",
        "ENCOUNTER",
        "CATEGORY",
        "CODE",
        "DESCRIPTION",
        "VALUE",
        "UNITS",
        "TYPE",
    ),
}

REQUIRED_CSV_FILES = CORE_REQUIRED_CSV_FILES + tuple(OPTIONAL_EMPTY_CSV_HEADERS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate GTA Synthea CSV files in a unique output run and write "
            "output/current-dataset.json for the OpenEMR orchestrator."
        )
    )
    parser.add_argument(
        "-p",
        "--population",
        type=int,
        default=100,
        help="Requested Synthea population. Default: 100.",
    )
    parser.add_argument(
        "--jar",
        type=Path,
        help=(
            "Path to the GTA Synthea JAR. When omitted, the highest "
            "versioned JAR under dist/ is selected."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="GTA Synthea properties file.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Parent directory for generated runs. Default: output/runs.",
    )
    parser.add_argument(
        "--run-name",
        help=(
            "Optional output run directory name. By default a timestamped "
            "name such as 20260724-094500-p100 is used."
        ),
    )
    parser.add_argument("--seed", type=int, help="Optional Synthea patient seed.")
    parser.add_argument(
        "--clinician-seed",
        type=int,
        help="Optional Synthea clinician seed.",
    )
    parser.add_argument(
        "--reference-date",
        help="Optional Synthea reference date in YYYYMMDD format.",
    )
    parser.add_argument(
        "--state",
        default="Ontario",
        help="Synthea state/province argument. Default: Ontario.",
    )
    parser.add_argument(
        "--city",
        help=(
            "Optional Synthea city argument. Omit it to generate across "
            "the configured GTA municipalities."
        ),
    )
    parser.add_argument(
        "--java",
        default="java",
        help="Java executable or path. Default: java.",
    )
    parser.add_argument(
        "--extra-arg",
        action="append",
        default=[],
        help="Additional raw Synthea argument. Repeat as needed.",
    )
    parser.add_argument(
        "--no-select",
        action="store_true",
        help=(
            "Write the run manifest but do not replace output/current-dataset.json. "
            "Useful for a maintainer smoke test."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print the Java command without generating data.",
    )
    return parser.parse_args()


def root_path(path: Path) -> Path:
    return path.expanduser().resolve() if path.is_absolute() else (ROOT / path).resolve()


def relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def version_key(path: Path) -> tuple[int, ...]:
    match = re.search(r"-v(\d+(?:\.\d+)*)\.jar$", path.name, re.IGNORECASE)
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def locate_jar(explicit: Path | None) -> Path:
    if explicit is not None:
        jar = root_path(explicit)
        if not jar.is_file():
            raise RuntimeError(f"GTA Synthea JAR was not found: {jar}")
        return jar

    preferred = ROOT / "dist/synthea-gta-maple-grove-v0.1.1.jar"
    if preferred.is_file():
        return preferred.resolve()

    patterns = (
        ROOT / "dist/synthea-gta-maple-grove-v*.jar",
        ROOT / "dist/synthea-gta-maple-grove.jar",
    )
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(path.resolve() for path in pattern.parent.glob(pattern.name) if path.is_file())

    unique = sorted(set(candidates))
    if not unique:
        raise RuntimeError(
            "No GTA Synthea JAR was found under dist/. "
            "Add a versioned JAR or pass --jar."
        )

    versioned = [path for path in unique if version_key(path)]
    if versioned:
        highest = max(version_key(path) for path in versioned)
        best = [path for path in versioned if version_key(path) == highest]
    else:
        best = unique

    if len(best) > 1:
        rendered = "\n".join(f"  - {path}" for path in best)
        raise RuntimeError(
            "Multiple equally preferred GTA Synthea JARs were found. "
            "Choose one with --jar:\n" + rendered
        )

    return best[0]


def check_java(java: str) -> None:
    executable = shutil.which(java) if not Path(java).is_file() else str(Path(java).resolve())
    if not executable:
        raise RuntimeError(f"Java executable was not found: {java}")

    result = subprocess.run(
        [java, "-version"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    output = (result.stderr or result.stdout).strip()
    if result.returncode != 0:
        raise RuntimeError(f"Java version check failed:\n{output}")

    match = re.search(r'version\s+"(\d+)', output)
    if match and int(match.group(1)) < 17:
        raise RuntimeError(f"Java 17 or newer is required. Detected:\n{output}")


def safe_run_name(value: str | None, population: int) -> str:
    if value is None:
        return datetime.now().astimezone().strftime(f"%Y%m%d-%H%M%S-p{population}")

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise RuntimeError(
            "--run-name may contain only letters, numbers, periods, underscores, and hyphens."
        )
    return value


def unique_run_dir(output_root: Path, run_name: str, explicit: bool) -> Path:
    candidate = output_root / run_name
    if not candidate.exists():
        return candidate
    if explicit:
        raise RuntimeError(f"The requested output run already exists: {candidate}")

    index = 2
    while True:
        alternative = output_root / f"{run_name}-{index}"
        if not alternative.exists():
            return alternative
        index += 1


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_csv_rows(path: Path) -> int:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        return sum(1 for _ in reader)


def ensure_optional_empty_csvs(csv_dir: Path) -> list[str]:
    """Create header-only CSVs for optional resources with zero generated rows."""

    created: list[str] = []
    for filename, header in OPTIONAL_EMPTY_CSV_HEADERS.items():
        path = csv_dir / filename
        if path.is_file():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(header)
        created.append(filename)
    return created


def build_csv_inventory(csv_dir: Path) -> list[dict[str, Any]]:
    missing_core = [
        name for name in CORE_REQUIRED_CSV_FILES if not (csv_dir / name).is_file()
    ]
    if missing_core:
        rendered = "\n".join(f"  - {name}" for name in missing_core)
        raise RuntimeError(f"Synthea did not produce required CSV files:\n{rendered}")

    created_empty = ensure_optional_empty_csvs(csv_dir)
    for filename in created_empty:
        print(f"  Created empty optional CSV: {filename}")

    entries: list[dict[str, Any]] = []
    for path in sorted(csv_dir.glob("*.csv")):
        entries.append(
            {
                "name": path.name,
                "rows": count_csv_rows(path),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    if not entries:
        raise RuntimeError(f"No CSV files were produced under {csv_dir}")
    return entries


def dataset_fingerprint(entries: list[dict[str, Any]]) -> str:
    identity = [
        {
            "name": entry["name"],
            "size_bytes": entry["size_bytes"],
            "sha256": entry["sha256"],
        }
        for entry in entries
    ]
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def build_command(
    args: argparse.Namespace,
    jar: Path,
    config: Path,
    run_dir: Path,
) -> list[str]:
    command = [
        args.java,
        "-jar",
        str(jar),
        "-c",
        str(config),
        "-p",
        str(args.population),
        f"--exporter.baseDirectory={run_dir}",
    ]
    if args.seed is not None:
        command.extend(["-s", str(args.seed)])
    if args.clinician_seed is not None:
        command.extend(["-cs", str(args.clinician_seed)])
    if args.reference_date:
        command.extend(["-r", args.reference_date])
    command.extend(args.extra_arg)
    command.append(args.state)
    if args.city:
        command.append(args.city)
    return command


def main() -> int:
    try:
        args = parse_args()
        if args.population < 1:
            raise RuntimeError("--population must be at least 1.")
        if args.reference_date and not re.fullmatch(r"\d{8}", args.reference_date):
            raise RuntimeError("--reference-date must use YYYYMMDD format.")

        config = root_path(args.config)
        output_root = root_path(args.output_root)
        if not config.is_file():
            raise RuntimeError(f"GTA Synthea configuration was not found: {config}")

        jar = locate_jar(args.jar)
        check_java(args.java)
        run_name = safe_run_name(args.run_name, args.population)
        run_dir = unique_run_dir(output_root, run_name, args.run_name is not None)
        command = build_command(args, jar, config, run_dir)

        print("Maple Grove GTA Synthea generation")
        print(f"  JAR: {jar}")
        print(f"  Configuration: {config}")
        print(f"  Population requested: {args.population}")
        print(f"  Output run: {run_dir}")
        print("  Command:")
        print("   ", shlex.join(command))

        if args.dry_run:
            print("\nDry run complete. Synthea was not started.")
            return 0

        output_root.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"Synthea exited with status {result.returncode}. "
                f"No current-dataset manifest was changed. Partial output may remain at {run_dir}."
            )

        csv_dir = run_dir / "csv"
        entries = build_csv_inventory(csv_dir)
        patients_entry = next(item for item in entries if item["name"] == "patients.csv")
        fingerprint = dataset_fingerprint(entries)
        created_at = datetime.now().astimezone().isoformat(timespec="seconds")

        manifest = {
            "schema_version": 1,
            "created_at": created_at,
            "generator": "scripts/generate_gta_patients.py",
            "jar": relative_to_root(jar),
            "jar_sha256": sha256_file(jar),
            "config": relative_to_root(config),
            "config_sha256": sha256_file(config),
            "population_requested": args.population,
            "population_generated": patients_entry["rows"],
            "seed": args.seed,
            "clinician_seed": args.clinician_seed,
            "reference_date": args.reference_date,
            "output_directory": relative_to_root(run_dir),
            "csv_directory": relative_to_root(csv_dir),
            "dataset_fingerprint": fingerprint,
            "csv_files": entries,
        }

        write_json_atomic(run_dir / RUN_MANIFEST_NAME, manifest)
        if not args.no_select:
            write_json_atomic(CURRENT_DATASET_FILE, manifest)

        print("\nGeneration complete")
        print(f"  Patients generated: {patients_entry['rows']}")
        print(f"  CSV files: {len(entries)}")
        print(f"  CSV directory: {csv_dir}")
        print(f"  Dataset fingerprint: {fingerprint}")
        if args.no_select:
            print("  Current dataset selection: unchanged (--no-select)")
        else:
            print(f"  Current dataset manifest: {CURRENT_DATASET_FILE}")
        if patients_entry["rows"] != args.population:
            print(
                "  Note: generated patient rows differ from the requested population; "
                "the manifest records both values."
            )
        if not args.no_select:
            print("\nNext command:")
            print("  python3 scripts/import_openemr.py")
        return 0

    except (RuntimeError, OSError, csv.Error, json.JSONDecodeError) as error:
        print(f"GTA Synthea generation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
