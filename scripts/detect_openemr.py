#!/usr/bin/env python3
"""Detect a local Docker OpenEMR installation without fixed container names."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
TARGET_FILE = ROOT / ".local" / "openemr-target.json"


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Docker was not found. Install and start Docker Desktop."
        ) from exc

    if check and result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(message or "Docker command failed.")

    return result


def running_containers() -> list[dict[str, Any]]:
    result = run(["docker", "ps", "--format", "{{json .}}"])

    return [
        json.loads(line)
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def is_openemr_container(container: dict[str, Any]) -> bool:
    image = container.get("Image", "").lower()
    name = container.get("Names", "").lower()

    if "openemr/openemr" in image:
        return True

    database_words = ("mysql", "mariadb", "xtrabackup", "database", "db")

    return (
        "openemr" in name
        and not any(word in name for word in database_words)
    )


def is_database_container(container: dict[str, Any]) -> bool:
    image = container.get("Image", "").lower()
    name = container.get("Names", "").lower()

    database_words = ("mysql", "mariadb", "xtrabackup")

    return any(word in image or word in name for word in database_words)


def inspect_container(name: str) -> dict[str, Any]:
    result = run(["docker", "inspect", name])
    records = json.loads(result.stdout)

    if not records:
        raise RuntimeError(f"Could not inspect container {name}.")

    return records[0]


def find_containers(
    containers: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    openemr = next(
        (container for container in containers if is_openemr_container(container)),
        None,
    )

    if openemr is None:
        raise RuntimeError("No running OpenEMR container was detected.")

    database = next(
        (
            container
            for container in containers
            if container is not openemr and is_database_container(container)
        ),
        None,
    )

    return openemr, database


def read_application_version(container_name: str) -> tuple[str | None, str | None]:
    command = r"""
for candidate in \
    /var/www/localhost/htdocs/openemr/version.php \
    /var/www/html/openemr/version.php \
    /var/www/html/version.php
do
    if [ -f "$candidate" ]; then
        printf 'VERSION_FILE:%s\n' "$candidate"
        cat "$candidate"
        exit 0
    fi
done

candidate="$(find /var/www -type f -path '*/openemr/version.php' \
    -print -quit 2>/dev/null)"

if [ -n "$candidate" ]; then
    printf 'VERSION_FILE:%s\n' "$candidate"
    cat "$candidate"
    exit 0
fi

exit 1
"""

    result = run(
        ["docker", "exec", container_name, "sh", "-lc", command],
        check=False,
    )

    if result.returncode != 0:
        return None, None

    version_file_match = re.search(
        r"^VERSION_FILE:(.+)$",
        result.stdout,
        re.MULTILINE,
    )
    version_file = (
        version_file_match.group(1).strip()
        if version_file_match
        else None
    )

    values: dict[str, str] = {}

    for variable in ("v_major", "v_minor", "v_patch", "v_realpatch"):
        match = re.search(
            rf"\${variable}\s*=\s*['\"]([^'\"]*)['\"]",
            result.stdout,
        )
        if match:
            values[variable] = match.group(1)

    required = ("v_major", "v_minor", "v_patch")

    if not all(values.get(variable) for variable in required):
        return None, version_file

    version_parts = [
        values["v_major"],
        values["v_minor"],
        values["v_patch"],
    ]

    real_patch = values.get("v_realpatch", "")

    if real_patch and real_patch != "0":
        version_parts.append(real_patch)

    return ".".join(version_parts), version_file


def version_from_image_tag(image: str) -> str | None:
    match = re.search(
        r":(\d+\.\d+\.\d+(?:\.\d+)?)(?:-|$)",
        image,
    )
    return match.group(1) if match else None


def published_endpoint(
    details: dict[str, Any],
) -> tuple[str, int]:
    ports = details.get("NetworkSettings", {}).get("Ports") or {}

    # Prefer HTTPS whenever the container publishes both.
    for container_port, scheme in (
        ("443/tcp", "https"),
        ("80/tcp", "http"),
    ):
        bindings = ports.get(container_port) or []

        if bindings:
            return scheme, int(bindings[0]["HostPort"])

    raise RuntimeError(
        "The OpenEMR container does not publish port 80 or port 443."
    )


def make_base_url(scheme: str, port: int) -> str:
    default_port = 443 if scheme == "https" else 80

    if port == default_port:
        return f"{scheme}://localhost"

    return f"{scheme}://localhost:{port}"



def configured_target() -> dict[str, Any] | None:
    """Return a configured remote target, or None for local Docker."""
    if not TARGET_FILE.is_file():
        return None

    try:
        target = json.loads(
            TARGET_FILE.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "Could not read OpenEMR target configuration: "
            f"{TARGET_FILE}"
        ) from exc

    if target.get("target_mode") != "remote":
        return None

    base_url = str(
        target.get("base_url") or ""
    ).strip().rstrip("/")

    site = (
        str(target.get("site") or "default").strip()
        or "default"
    )

    version = str(
        target.get("version") or "unknown"
    ).strip()

    major_version = target.get("major_version")
    parsed = urlparse(base_url)

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
    ):
        raise RuntimeError(
            f"Invalid remote OpenEMR URL in {TARGET_FILE}: "
            f"{base_url!r}"
        )

    if not isinstance(major_version, int):
        match = re.match(r"(\d+)", version)
        major_version = (
            int(match.group(1))
            if match
            else None
        )

    default_port = (
        443 if parsed.scheme == "https" else 80
    )

    return {
        "openemr_container": None,
        "database_container": None,
        "image": "remote server",
        "version": version,
        "major_version": major_version,
        "version_source": str(TARGET_FILE),
        "version_file": None,
        "status": "configured",
        "scheme": parsed.scheme,
        "host_port": parsed.port or default_port,
        "base_url": base_url,
        "site": site,
        "api_base_url": (
            f"{base_url}/apis/{site}/api"
        ),
        "verify_tls": bool(
            target.get("verify_tls", True)
        ),
        "target_mode": "remote",
    }


def detect() -> dict[str, Any]:
    configured = configured_target()

    if configured is not None:
        return configured

    openemr, database = find_containers(running_containers())

    container_name = openemr["Names"]
    image = openemr.get("Image", "")
    details = inspect_container(container_name)

    actual_version, version_file = read_application_version(container_name)
    image_version = version_from_image_tag(image)

    if actual_version:
        version = actual_version
        version_source = "OpenEMR version.php"
    elif image_version:
        version = image_version
        version_source = "Docker image tag fallback"
    else:
        version = "unknown"
        version_source = "not detected"

    major_match = re.match(r"(\d+)", version)
    major_version = int(major_match.group(1)) if major_match else None

    scheme, host_port = published_endpoint(details)
    base_url = make_base_url(scheme, host_port)

    state = details.get("State", {})
    health = state.get("Health", {}).get("Status")
    status = health or state.get("Status", "unknown")

    return {
        "openemr_container": container_name,
        "database_container": database["Names"] if database else None,
        "image": image,
        "version": version,
        "major_version": major_version,
        "version_source": version_source,
        "version_file": version_file,
        "status": status,
        "scheme": scheme,
        "host_port": host_port,
        "base_url": base_url,
        "site": "default",
        "api_base_url": f"{base_url}/apis/default/api",
        "verify_tls": False,
        "target_mode": "local",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        information = detect()
    except RuntimeError as error:
        print(f"OpenEMR detection failed: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(information, indent=2))
        return 0


    if information.get("target_mode") == "remote":
        print("Remote OpenEMR configured")
        print(
            f"  OpenEMR version: "
            f"{information['version']}"
        )
        print(
            f"  Base URL: "
            f"{information['base_url']}"
        )
        print(
            f"  Standard API: "
            f"{information['api_base_url']}"
        )
        print(
            "  Verify TLS certificate: "
            f"{information['verify_tls']}"
        )
        return 0

    print("Local OpenEMR detected")
    print(f"  OpenEMR container: {information['openemr_container']}")
    print(
        "  Database container: "
        f"{information['database_container'] or 'not detected'}"
    )
    print(f"  Docker image: {information['image']}")
    print(f"  Actual OpenEMR version: {information['version']}")
    print(f"  Version source: {information['version_source']}")
    print(f"  Version file: {information['version_file'] or 'not found'}")
    print(f"  Status: {information['status']}")
    print(f"  Base URL: {information['base_url']}")
    print(f"  Standard API: {information['api_base_url']}")

    if information["scheme"] != "https":
        print("  Warning: this installation currently publishes HTTP only.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
