#!/usr/bin/env python3
"""Publish an existing local OpenEMR container's HTTPS port automatically."""

from __future__ import annotations

import json
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from detect_openemr import detect


ROOT = Path(__file__).resolve().parents[1]
LOCAL_DIRECTORY = ROOT / ".local"
OVERRIDE_FILE = LOCAL_DIRECTORY / "docker-compose.maple-grove.override.yml"
HTTPS_HOST_PORT = 9300


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )

    if check and result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(message or f"Command failed: {' '.join(command)}")

    return result


def inspect_container(name: str) -> dict[str, Any]:
    result = run(["docker", "inspect", name])
    records = json.loads(result.stdout)

    if not records:
        raise RuntimeError(f"Could not inspect container: {name}")

    return records[0]


def local_path(raw_path: str) -> Path:
    """Convert Docker Desktop Windows paths for use inside WSL."""

    if re.match(r"^[A-Za-z]:[\\/]", raw_path):
        wslpath = shutil.which("wslpath")

        if not wslpath:
            raise RuntimeError(
                f"Windows path detected but wslpath is unavailable: {raw_path}"
            )

        result = run([wslpath, "-u", raw_path])
        return Path(result.stdout.strip())

    return Path(raw_path)


def compose_command() -> list[str]:
    result = run(["docker", "compose", "version"], check=False)

    if result.returncode == 0:
        return ["docker", "compose"]

    legacy = shutil.which("docker-compose")

    if legacy:
        return [legacy]

    raise RuntimeError("Docker Compose was not found.")


def wait_for_https(url: str, attempts: int = 180) -> int:
    context = ssl._create_unverified_context()

    for _ in range(attempts):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "Maple-Grove-Setup"},
            )

            with urllib.request.urlopen(
                request,
                context=context,
                timeout=5,
            ) as response:
                return response.status

        except urllib.error.HTTPError as error:
            if error.code < 500:
                return error.code

        except (OSError, urllib.error.URLError):
            pass

        time.sleep(2)

    raise RuntimeError(f"HTTPS did not become available at {url}")


def main() -> int:
    try:
        current = detect()

        if current["scheme"] == "https":
            print(f"Local HTTPS is already available: {current['base_url']}")
            return 0

        details = inspect_container(current["openemr_container"])
        labels = details.get("Config", {}).get("Labels") or {}

        working_directory_raw = labels.get(
            "com.docker.compose.project.working_dir"
        )
        compose_files_raw = labels.get(
            "com.docker.compose.project.config_files"
        )
        project = labels.get("com.docker.compose.project")
        service = labels.get("com.docker.compose.service")

        if not all(
            (working_directory_raw, compose_files_raw, project, service)
        ):
            raise RuntimeError(
                "The OpenEMR container does not contain complete "
                "Docker Compose labels."
            )

        working_directory = local_path(working_directory_raw)
        compose_files = [
            local_path(item.strip())
            for item in compose_files_raw.split(",")
            if item.strip()
        ]

        for compose_file in compose_files:
            if not compose_file.is_file():
                raise RuntimeError(
                    f"Compose file was not found: {compose_file}"
                )

        LOCAL_DIRECTORY.mkdir(parents=True, exist_ok=True)

        OVERRIDE_FILE.write_text(
            "services:\n"
            f"  {json.dumps(service)}:\n"
            "    ports:\n"
            f'      - "{HTTPS_HOST_PORT}:443"\n',
            encoding="utf-8",
        )

        command = compose_command() + ["--project-name", project]

        for compose_file in compose_files:
            command.extend(["-f", str(compose_file)])

        command.extend(["-f", str(OVERRIDE_FILE)])

        run(command + ["config", "--quiet"], cwd=working_directory)

        print(
            f"Publishing HTTPS as "
            f"https://localhost:{HTTPS_HOST_PORT} ..."
        )

        run(command + ["up", "-d", service], cwd=working_directory)

        url = f"https://localhost:{HTTPS_HOST_PORT}"
        status = wait_for_https(url)

        updated = detect()

        print("Local OpenEMR HTTPS is ready")
        print(f"  HTTP status: {status}")
        print(f"  Base URL: {updated['base_url']}")
        print(f"  Standard API: {updated['api_base_url']}")
        print(f"  OpenEMR version: {updated['version']}")

        return 0

    except RuntimeError as error:
        print(f"HTTPS setup failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
