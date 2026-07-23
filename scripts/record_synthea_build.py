#!/usr/bin/env python3
"""
Create docs/SYNTHEA_GTA_BUILD.md with a readable record of the working
Maple Grove GTA Synthea build.

Run from the project root:

    python3 scripts/record_synthea_build.py
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path.cwd()
SYNTHEA_SOURCE = ROOT / "tools" / "synthea-canada-source"
INTERNATIONAL_SOURCE = ROOT / "tools" / "synthea-international"
POSTAL_ARCHIVE = ROOT / "tools" / "postal-data" / "CA_full.csv.zip"
GEOGRAPHY_DIR = SYNTHEA_SOURCE / "src" / "main" / "resources" / "geography"
DEMOGRAPHICS_FILE = GEOGRAPHY_DIR / "demographics_gta.csv"
POSTAL_FILE = GEOGRAPHY_DIR / "zipcodes_gta.csv"
JAR_CANDIDATES = [
    ROOT / "tools" / "synthea-gta-maple-grove.jar",
    ROOT / "tools" / "synthea-canada-maple-grove.jar",
]
OUTPUT_FILE = ROOT / "docs" / "SYNTHEA_GTA_BUILD.md"


def run_command(command: list[str], cwd: Path | None = None) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return 127, f"Command not found: {command[0]}"

    output = (result.stdout or result.stderr).strip()
    return result.returncode, output


def git_revision(repository: Path) -> str:
    if not repository.is_dir():
        return "Repository not found"

    code, output = run_command(["git", "rev-parse", "HEAD"], cwd=repository)
    return output if code == 0 and output else "Unable to read commit"


def readable_git_changes(repository: Path) -> str:
    """Convert Git's compact status codes into plain-language groups."""
    if not repository.is_dir():
        return "Repository not found."

    code, output = run_command(["git", "status", "--porcelain"], cwd=repository)
    if code != 0:
        return output or "Unable to read repository status."
    if not output:
        return "No local changes."

    groups: dict[str, list[str]] = defaultdict(list)

    for raw_line in output.splitlines():
        if len(raw_line) < 4:
            continue

        status = raw_line[:2]
        path = raw_line[3:].strip()

        if status == "??":
            label = "New files added locally"
        elif "D" in status:
            label = "Files deleted locally"
        elif "R" in status:
            label = "Files renamed locally"
        elif "C" in status:
            label = "Files copied locally"
        elif "M" in status:
            label = "Existing files modified locally"
        elif "A" in status:
            label = "New files already staged in Git"
        else:
            label = "Other local changes"

        groups[label].append(path)

    sections: list[str] = []
    preferred_order = [
        "Existing files modified locally",
        "New files added locally",
        "New files already staged in Git",
        "Files renamed locally",
        "Files copied locally",
        "Files deleted locally",
        "Other local changes",
    ]

    for label in preferred_order:
        paths = groups.get(label)
        if not paths:
            continue

        sections.append(f"**{label}:**")
        sections.extend(f"- `{path}`" for path in sorted(paths))
        sections.append("")

    return "\n".join(sections).rstrip()


def sha256(path: Path) -> str:
    if not path.is_file():
        return "File not found"

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def count_csv_rows(path: Path) -> int:
    if not path.is_file():
        return 0

    with path.open(newline="", encoding="utf-8-sig") as file:
        return max(sum(1 for _ in csv.reader(file)) - 1, 0)


def read_cities(path: Path) -> list[str]:
    if not path.is_file():
        return []

    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        return sorted(
            {
                row.get("NAME", "").strip()
                for row in reader
                if row.get("NAME", "").strip()
            }
        )


def find_jar() -> Path:
    for candidate in JAR_CANDIDATES:
        if candidate.is_file():
            return candidate
    return JAR_CANDIDATES[0]


def java_version() -> str:
    code, output = run_command(["java", "-version"])
    if code == 127:
        return output
    return output or "Unable to read Java version"


def main() -> None:
    jar = find_jar()
    cities = read_cities(DEMOGRAPHICS_FILE)
    city_markdown = "\n".join(f"- {city}" for city in cities)
    if not city_markdown:
        city_markdown = "- Unable to read cities"

    recorded_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")

    document = f"""# Maple Grove GTA Synthea build record

This file records the local build that successfully generated synthetic
Greater Toronto Area patient data.

## Recorded

`{recorded_at}`

## Upstream source revisions

- Synthea commit: `{git_revision(SYNTHEA_SOURCE)}`
- Synthea International commit: `{git_revision(INTERNATIONAL_SOURCE)}`

## Local changes applied to the Synthea source

These are expected because the Canada configuration and GTA-specific files
were copied or created inside the upstream Synthea source folder.

{readable_git_changes(SYNTHEA_SOURCE)}

## Local changes in Synthea International

{readable_git_changes(INTERNATIONAL_SOURCE)}

## Java

```text
{java_version()}
```

## GTA configuration

Cities included:

{city_markdown}

- Demographic rows: {count_csv_rows(DEMOGRAPHICS_FILE)}
- Postal-code rows: {count_csv_rows(POSTAL_FILE)}
- Person-name number suffixes are disabled during generation.
- CSV export is used for the OpenEMR import workflow.

## Input data hashes

- GeoNames archive: `{POSTAL_ARCHIVE.name}`
- GeoNames SHA-256: `{sha256(POSTAL_ARCHIVE)}`

## Built JAR

- File: `{jar.name}`
- SHA-256: `{sha256(jar)}`

## Known limitations

- All generated patient records are synthetic.
- Postal codes are selected from the patient's municipality.
- A generated street address may not correspond to its exact postal code.
- Synthea's clinical modules are not an exact simulation of Ontario clinical,
  billing, or provincial health-insurance practices.
"""

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(document, encoding="utf-8")
    print(f"Created: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
