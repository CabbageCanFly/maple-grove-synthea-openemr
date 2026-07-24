#!/usr/bin/env python3
"""Select the local or remote OpenEMR target used by project scripts."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
import urllib3


ROOT = Path(__file__).resolve().parents[1]
TARGET_FILE = ROOT / ".local" / "openemr-target.json"
CLIENT_FILE = ROOT / ".local" / "openemr-client.json"


def yes_no(prompt: str, *, default: bool = False) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    answer = input(prompt + suffix).strip().casefold()

    if not answer:
        return default

    return answer in {"y", "yes"}


def normalize_base_url(value: str) -> str:
    value = value.strip().rstrip("/")

    if "://" not in value:
        value = "https://" + value

    parsed = urlparse(value)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(
            "Enter a complete server URL such as "
            "https://mgfhc-demo.hopto.org or https://18.223.33.251."
        )

    if (
        parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(
            "Enter only the OpenEMR server base URL, without a path."
        )

    return value


def parse_version(value: str) -> tuple[str, int]:
    value = value.strip()
    match = re.fullmatch(r"(\d+)(?:\.\d+){0,3}", value)

    if not match:
        raise RuntimeError(
            "Enter a version such as 7, 7.0.2, or 8.0.0.3."
        )

    return value, int(match.group(1))


def test_discovery(
    base_url: str,
    site: str,
) -> tuple[bool, dict[str, object]]:
    discovery_url = (
        f"{base_url}/oauth2/{site}/"
        ".well-known/openid-configuration"
    )
    verify_tls = True

    try:
        response = requests.get(
            discovery_url,
            verify=True,
            timeout=20,
        )
    except requests.exceptions.SSLError:
        print()
        print(
            "The server responded, but its HTTPS certificate is not "
            "trusted for this address."
        )
        print(
            "This is common when using the raw IP address of a test server."
        )

        if not yes_no(
            "Allow insecure certificate mode for this server?"
        ):
            raise RuntimeError(
                "Certificate verification was not accepted."
            )

        verify_tls = False
        urllib3.disable_warnings(
            urllib3.exceptions.InsecureRequestWarning
        )

        response = requests.get(
            discovery_url,
            verify=False,
            timeout=20,
        )

    response.raise_for_status()

    try:
        discovery = response.json()
    except requests.JSONDecodeError as exc:
        raise RuntimeError(
            "OAuth discovery did not return JSON. Check the server URL "
            "and OpenEMR API settings."
        ) from exc

    if (
        not discovery.get("registration_endpoint")
        or not discovery.get("token_endpoint")
    ):
        raise RuntimeError(
            "OAuth discovery did not return registration and token "
            "endpoints."
        )

    return verify_tls, discovery


def save_target(target: dict[str, object]) -> None:
    TARGET_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    TARGET_FILE.write_text(
        json.dumps(target, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    try:
        os.chmod(TARGET_FILE, 0o600)
    except OSError:
        pass


def configure_local() -> int:
    if TARGET_FILE.exists():
        TARGET_FILE.unlink()

    print()
    print("Local Docker OpenEMR selected")
    print("  Docker detection will be used.")
    print(f"  Remote target configuration removed: {TARGET_FILE}")
    return 0


def configure_remote() -> int:
    print()

    base_url = normalize_base_url(
        input("OpenEMR server URL: ")
    )

    site = (
        input("OpenEMR site [default]: ").strip()
        or "default"
    )

    version, major_version = parse_version(
        input("OpenEMR version, for example 7.0.2: ")
    )

    verify_tls, discovery = test_discovery(
        base_url,
        site,
    )

    target = {
        "target_mode": "remote",
        "base_url": base_url,
        "site": site,
        "version": version,
        "major_version": major_version,
        "verify_tls": verify_tls,
        "issuer": discovery.get("issuer"),
        "registration_endpoint": discovery.get(
            "registration_endpoint"
        ),
        "token_endpoint": discovery.get("token_endpoint"),
    }

    save_target(target)

    print()
    print("Remote OpenEMR target saved")
    print(f"  Base URL: {base_url}")
    print(f"  Site: {site}")
    print(f"  OpenEMR version: {version}")
    print(f"  Verify TLS certificate: {verify_tls}")
    print(f"  Configuration: {TARGET_FILE}")

    if CLIENT_FILE.exists():
        try:
            client = json.loads(
                CLIENT_FILE.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            client = {}

        if client.get("base_url") != base_url:
            print()
            print(
                "An OAuth client for another OpenEMR server exists."
            )
            print("Before registration, remove it with:")
            print("  rm -f .local/openemr-client.json")

    return 0


def main() -> int:
    print("Choose the OpenEMR target")
    print("  1. Local Docker OpenEMR")
    print("  2. Remote OpenEMR server")

    selection = input("Selection [1]: ").strip() or "1"

    try:
        if selection == "1":
            return configure_local()

        if selection == "2":
            return configure_remote()

        raise RuntimeError("Choose 1 or 2.")

    except (
        RuntimeError,
        requests.RequestException,
    ) as error:
        print(
            f"OpenEMR target configuration failed: {error}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
