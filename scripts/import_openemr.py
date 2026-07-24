#!/usr/bin/env python3
"""Run supported Synthea-to-OpenEMR importers in dependency order."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

# Backward-compatible exports for the existing resource importers. They
# historically imported these helpers from import_openemr.py when that file
# was the patient-only importer.
from import_openemr_patients import get_access_token, load_json, save_json


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
DEFAULT_CSV_DIR = ROOT / "output/gta-100-v2/csv"
CLIENT_FILE = ROOT / ".local/openemr-client.json"
PATIENT_MAP_FILE = ROOT / ".local/patient-import-map.json"
ENCOUNTER_MAP_FILE = ROOT / ".local/encounter-import-map.json"


@dataclass(frozen=True)
class Resource:
    name: str
    label: str
    script: str
    csv_arguments: tuple[tuple[str, str], ...]
    supports_quiet: bool = False
    supports_progress: bool = False
    requires_patient_map: bool = False
    requires_encounter_map: bool = False


RESOURCE_ORDER: tuple[Resource, ...] = (
    Resource(
        name="patients",
        label="Patients",
        script="import_openemr_patients.py",
        csv_arguments=(("--patients-csv", "patients.csv"),),
    ),
    Resource(
        name="encounters",
        label="Encounters",
        script="import_openemr_encounters.py",
        csv_arguments=(
            ("--encounters-csv", "encounters.csv"),
            ("--organizations-csv", "organizations.csv"),
            ("--providers-csv", "providers.csv"),
        ),
        supports_progress=True,
        requires_patient_map=True,
    ),
    Resource(
        name="conditions",
        label="Conditions",
        script="import_openemr_conditions.py",
        csv_arguments=(("--conditions-csv", "conditions.csv"),),
        supports_quiet=True,
        supports_progress=True,
        requires_patient_map=True,
        requires_encounter_map=True,
    ),
    Resource(
        name="allergies",
        label="Allergies",
        script="import_openemr_allergies.py",
        csv_arguments=(("--allergies-csv", "allergies.csv"),),
        supports_quiet=True,
        supports_progress=True,
        requires_patient_map=True,
        requires_encounter_map=True,
    ),
    Resource(
        name="medications",
        label="Medications",
        script="import_openemr_medications.py",
        csv_arguments=(("--medications-csv", "medications.csv"),),
        supports_quiet=True,
        supports_progress=True,
        requires_patient_map=True,
        requires_encounter_map=True,
    ),
    Resource(
        name="vitals",
        label="Vital signs",
        script="import_openemr_vitals.py",
        csv_arguments=(("--observations-csv", "observations.csv"),),
        supports_quiet=True,
        supports_progress=True,
        requires_patient_map=True,
        requires_encounter_map=True,
    ),
)

RESOURCE_BY_NAME = {resource.name: resource for resource in RESOURCE_ORDER}

UNSUPPORTED_RESOURCES: tuple[tuple[str, str], ...] = (
    ("procedures.csv", "generic Procedure creation is unavailable; optional surgery subset deferred"),
    ("immunizations.csv", "installed Standard/FHIR routes are read-only"),
    ("careplans.csv", "installed FHIR routes are read-only"),
    ("devices.csv", "installed FHIR routes are read-only"),
    ("imaging_studies.csv", "no writable matching imaging resource"),
    ("supplies.csv", "no matching API route"),
    ("claims*.csv and payer files", "financial/insurance mapping is outside the current clinical import scope"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Preflight or run all supported Synthea CSV imports in safe "
            "dependency order. Without --commit, only validate and print "
            "the execution plan."
        )
    )
    parser.add_argument(
        "--csv-dir",
        type=Path,
        default=DEFAULT_CSV_DIR,
        help=(
            "Directory containing the selected Synthea CSV files. "
            f"Default: {DEFAULT_CSV_DIR}"
        ),
    )
    parser.add_argument(
        "--resource",
        action="append",
        choices=tuple(RESOURCE_BY_NAME),
        help=(
            "Run only this supported resource. Repeat to select multiple "
            "resources. Default: all supported resources."
        ),
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually create records. Without this flag, perform preflight only.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-record messages where the underlying importer supports it.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100,
        help="Progress interval passed to supporting importers. Default: 100.",
    )
    parser.add_argument(
        "--skip-local-vitals-compat",
        action="store_true",
        help=(
            "Do not automatically verify/apply the exact-version local "
            "OpenEMR 8.0.0.3 vitals compatibility patch."
        ),
    )
    parser.add_argument(
        "--list-resources",
        action="store_true",
        help="List supported and intentionally unsupported resources, then exit.",
    )
    return parser.parse_args()


def selected_resources(names: list[str] | None) -> list[Resource]:
    if not names:
        return list(RESOURCE_ORDER)

    requested = set(names)
    return [resource for resource in RESOURCE_ORDER if resource.name in requested]


def print_resource_inventory() -> None:
    print("Supported import resources")
    for resource in RESOURCE_ORDER:
        print(f"  {resource.name}: {resource.label}")

    print("\nIntentionally unsupported or deferred")
    for filename, reason in UNSUPPORTED_RESOURCES:
        print(f"  {filename}: {reason}")


def expected_csv_paths(resource: Resource, csv_dir: Path) -> list[Path]:
    return [csv_dir / filename for _, filename in resource.csv_arguments]


def require_files(paths: Iterable[Path], description: str) -> None:
    missing = [path for path in paths if not path.is_file()]
    if missing:
        rendered = "\n".join(f"  - {path}" for path in missing)
        raise RuntimeError(f"Missing {description}:\n{rendered}")


def validate_map_dependencies(resources: list[Resource]) -> None:
    names = {resource.name for resource in resources}

    for resource in resources:
        if (
            resource.requires_patient_map
            and "patients" not in names
            and not PATIENT_MAP_FILE.is_file()
        ):
            raise RuntimeError(
                f"{resource.label} requires {PATIENT_MAP_FILE}. Include "
                "--resource patients or run the patient import first."
            )

        if (
            resource.requires_encounter_map
            and "encounters" not in names
            and not ENCOUNTER_MAP_FILE.is_file()
        ):
            raise RuntimeError(
                f"{resource.label} requires {ENCOUNTER_MAP_FILE}. Include "
                "--resource encounters or run the encounter import first."
            )


def build_command(
    resource: Resource,
    csv_dir: Path,
    *,
    commit: bool,
    quiet: bool,
    progress_every: int,
) -> list[str]:
    command = [sys.executable, str(SCRIPTS_DIR / resource.script)]

    for flag, filename in resource.csv_arguments:
        command.extend([flag, str(csv_dir / filename)])

    command.append("--all")

    if commit:
        command.append("--commit")

    if quiet and resource.supports_quiet:
        command.append("--quiet")

    if resource.supports_progress:
        command.extend(["--progress-every", str(progress_every)])

    return command


def safe_command_text(command: list[str]) -> str:
    return shlex.join(command)


def read_client_base_url() -> str:
    if not CLIENT_FILE.is_file():
        return ""

    try:
        data = json.loads(CLIENT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not read {CLIENT_FILE}: {error}") from error

    return str(data.get("base_url") or "").strip()


def maybe_prepare_local_vitals(
    resources: list[Resource],
    *,
    skip: bool,
) -> None:
    if skip or not any(resource.name == "vitals" for resource in resources):
        return

    base_url = read_client_base_url()
    hostname = (urlparse(base_url).hostname or "").casefold()

    if hostname not in {"localhost", "127.0.0.1", "::1"}:
        print("Vitals compatibility: remote/non-local target; no local patch attempted.")
        return

    try:
        from detect_openemr import detect

        information = detect()
    except RuntimeError as error:
        raise RuntimeError(
            "Could not inspect the local OpenEMR version before the vitals step: "
            f"{error}"
        ) from error

    version = str(information.get("version") or "").strip()

    if version != "8.0.0.3":
        print(
            "Vitals compatibility: no automatic patch for local OpenEMR "
            f"{version or 'unknown'}."
        )
        return

    command = [
        sys.executable,
        str(SCRIPTS_DIR / "ensure_local_vitals_api_compat.py"),
    ]
    print("\nPreparing local OpenEMR 8.0.0.3 vitals compatibility")
    print(f"  {safe_command_text(command)}")
    result = subprocess.run(command, cwd=ROOT, check=False)

    if result.returncode != 0:
        raise RuntimeError(
            "Local vitals compatibility preparation failed; import stopped."
        )


def run_resources(
    resources: list[Resource],
    csv_dir: Path,
    *,
    quiet: bool,
    progress_every: int,
    skip_local_vitals_compat: bool,
) -> int:
    completed: list[tuple[str, float]] = []
    started_all = time.monotonic()

    for position, resource in enumerate(resources, start=1):
        if resource.name == "vitals":
            maybe_prepare_local_vitals(
                resources,
                skip=skip_local_vitals_compat,
            )

        command = build_command(
            resource,
            csv_dir,
            commit=True,
            quiet=quiet,
            progress_every=progress_every,
        )

        print("\n" + "=" * 72)
        print(f"STEP {position}/{len(resources)}: {resource.label}")
        print("=" * 72)
        print(f"Command: {safe_command_text(command)}")
        sys.stdout.flush()

        started = time.monotonic()
        result = subprocess.run(command, cwd=ROOT, check=False)
        elapsed = time.monotonic() - started

        if result.returncode != 0:
            print(
                f"\nImport stopped: {resource.label} exited with status "
                f"{result.returncode} after {elapsed:.1f} seconds.",
                file=sys.stderr,
            )
            return result.returncode or 1

        completed.append((resource.label, elapsed))

    elapsed_all = time.monotonic() - started_all
    print("\n" + "=" * 72)
    print("SUPPORTED IMPORT WORKFLOW COMPLETE")
    print("=" * 72)
    for label, elapsed in completed:
        print(f"  {label}: completed in {elapsed:.1f} seconds")
    print(f"  Total elapsed: {elapsed_all:.1f} seconds")
    print("  Access tokens were not printed or saved by the orchestrator.")
    return 0


def main() -> int:
    args = parse_args()

    try:
        if args.progress_every < 0:
            raise RuntimeError("--progress-every cannot be negative.")

        if args.list_resources:
            print_resource_inventory()
            return 0

        csv_dir = args.csv_dir.resolve()
        resources = selected_resources(args.resource)

        if not resources:
            raise RuntimeError("No supported resources were selected.")

        require_files(
            [SCRIPTS_DIR / resource.script for resource in resources],
            "importer scripts",
        )

        csv_paths: list[Path] = []
        for resource in resources:
            csv_paths.extend(expected_csv_paths(resource, csv_dir))
        require_files(csv_paths, "Synthea CSV files")

        validate_map_dependencies(resources)

        if args.commit and not CLIENT_FILE.is_file():
            raise RuntimeError(
                f"OAuth client credentials are missing: {CLIENT_FILE}. Run "
                "scripts/register_openemr_client.py first."
            )

        print("Maple Grove supported OpenEMR import")
        print(f"CSV directory: {csv_dir}")
        print(f"Mode: {'COMMIT' if args.commit else 'PREFLIGHT ONLY'}")
        print("Selected resources:")

        for position, resource in enumerate(resources, start=1):
            print(f"  {position}. {resource.label} ({resource.name})")

        print("\nExecution plan:")
        for resource in resources:
            command = build_command(
                resource,
                csv_dir,
                commit=args.commit,
                quiet=args.quiet,
                progress_every=args.progress_every,
            )
            print(f"  {safe_command_text(command)}")

        print("\nNot included in this workflow:")
        for filename, reason in UNSUPPORTED_RESOURCES:
            print(f"  {filename}: {reason}")

        if not args.commit:
            print("\nPreflight passed. No OpenEMR records were created.")
            print("Run the same command with --commit to execute the plan.")
            return 0

        return run_resources(
            resources,
            csv_dir,
            quiet=args.quiet,
            progress_every=args.progress_every,
            skip_local_vitals_compat=args.skip_local_vitals_compat,
        )

    except (RuntimeError, OSError) as error:
        print(f"OpenEMR import workflow failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
