#!/usr/bin/env python3
"""Choose the OpenEMR installation used by the project scripts."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
import urllib3


ROOT = Path(__file__).resolve().parents[1]
TARGET_FILE = ROOT / ".local" / "openemr-target.json"
CLIENT_FILE = ROOT / ".local" / "openemr-client.json"

DEFAULT_SITE = "default"


def yes_no(prompt: str, *, default: bool = False) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    answer = input(prompt + suffix).strip().casefold()

    if not answer:
        return default

    return answer in {"y", "yes"}


def normalize_base_url(value: str) -> str:
    value = value.strip().rstrip("/")

    if not value:
        raise RuntimeError("The OpenEMR address cannot be empty.")

    if "://" not in value:
        value = "https://" + value

    parsed = urlparse(value)

    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError(
            "The address must start with http:// or https://."
        )

    if not parsed.netloc:
        raise RuntimeError(
            "Enter an address such as "
            "https://mgfhc-demo.hopto.org."
        )

    if (
        parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(
            "Enter only the main OpenEMR address, without "
            "anything after the hostname or IP address."
        )

    return value


def detect_major_version(
    discovery: dict[str, object],
) -> int:
    """Detect the OpenEMR API generation from advertised scopes."""
    scopes_value = discovery.get("scopes_supported", [])

    if not isinstance(scopes_value, list):
        scopes_value = []

    scopes = {
        str(scope).strip()
        for scope in scopes_value
        if str(scope).strip()
    }

    # OpenEMR 8 uses compact permission scopes.
    compact_scopes = {
        "user/patient.crs",
        "user/encounter.crs",
        "user/facility.crs",
    }

    if scopes.intersection(compact_scopes):
        return 8

    # OpenEMR 7 uses separate read and write scopes.
    legacy_patient_scopes = {
        "user/patient.read",
        "user/patient.write",
    }

    legacy_encounter_scopes = {
        "user/encounter.read",
        "user/encounter.write",
    }

    if (
        legacy_patient_scopes.issubset(scopes)
        or legacy_encounter_scopes.issubset(scopes)
    ):
        return 7

    raise RuntimeError(
        "The OpenEMR API was found, but its version could not "
        "be detected automatically."
    )


def load_discovery(
    base_url: str,
) -> tuple[dict[str, object], bool]:
    discovery_url = (
        f"{base_url}/oauth2/{DEFAULT_SITE}/"
        ".well-known/openid-configuration"
    )

    parsed = urlparse(base_url)

    if parsed.scheme == "http":
        print()
        print("WARNING: This server uses unencrypted HTTP.")
        print(
            "Your OpenEMR username and password could be visible "
            "to others on the network."
        )

        if not yes_no(
            "Continue with this known test server?"
        ):
            raise RuntimeError("HTTP server was not accepted.")

    try:
        response = requests.get(
            discovery_url,
            verify=True,
            timeout=20,
        )
        verify_tls = True

    except requests.exceptions.SSLError:
        print()
        print(
            "This server does not have a trusted HTTPS "
            "certificate for the address you entered."
        )
        print(
            "This is common for a temporary AWS server accessed "
            "through its IP address."
        )

        if not yes_no(
            "Continue with this known test server?"
        ):
            raise RuntimeError(
                "The untrusted certificate was not accepted."
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
    except ValueError as exc:
        raise RuntimeError(
            "OpenEMR responded, but OAuth discovery did not "
            "return valid JSON."
        ) from exc

    if not isinstance(discovery, dict):
        raise RuntimeError(
            "OpenEMR OAuth discovery returned an unexpected result."
        )

    required = (
        "registration_endpoint",
        "token_endpoint",
    )

    missing = [
        name
        for name in required
        if not discovery.get(name)
    ]

    if missing:
        raise RuntimeError(
            "OpenEMR OAuth discovery is missing: "
            + ", ".join(missing)
        )

    return discovery, verify_tls


def save_target(target: dict[str, object]) -> None:
    TARGET_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    TARGET_FILE.write_text(
        json.dumps(
            target,
            indent=2,
            sort_keys=True,
        )
        + "\n",
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
    print("Local Docker OpenEMR selected.")
    print("The project will find the local container automatically.")

    if CLIENT_FILE.exists():
        print()
        print(
            "A saved OAuth client already exists. If it belongs "
            "to another server, remove it and register again:"
        )
        print("  rm -f .local/openemr-client.json")

    return 0


def configure_remote() -> int:
    print()

    base_url = normalize_base_url(
        input(
            "OpenEMR address "
            "(the address used in your browser): "
        )
    )

    print()
    print("Connecting to OpenEMR...")

    discovery, verify_tls = load_discovery(base_url)
    major_version = detect_major_version(discovery)

    target = {
        "target_mode": "remote",
        "base_url": base_url,
        "site": DEFAULT_SITE,
        "major_version": major_version,
        "version": f"{major_version} compatibility",
        "verify_tls": verify_tls,
        "issuer": discovery.get("issuer"),
        "registration_endpoint": discovery.get(
            "registration_endpoint"
        ),
        "token_endpoint": discovery.get("token_endpoint"),
    }

    save_target(target)

    print()
    print("OpenEMR is ready.")
    print(f"  Address: {base_url}")
    print(
        f"  Detected: OpenEMR {major_version} "
        "API compatibility"
    )

    if verify_tls:
        print("  Secure certificate: verified")
    else:
        print(
            "  Secure certificate: verification disabled "
            "for this test server"
        )

    print(f"  Saved configuration: {TARGET_FILE}")

    if CLIENT_FILE.exists():
        try:
            client = json.loads(
                CLIENT_FILE.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            client = {}

        if client.get("base_url") != base_url:
            print()
            print(
                "Your saved OAuth client belongs to another "
                "OpenEMR installation."
            )
            print("Run:")
            print("  rm -f .local/openemr-client.json")
            print("  python3 scripts/register_openemr_client.py")

    return 0


def main() -> int:
    print("Where is OpenEMR running?")
    print("  1. On this computer using Docker")
    print("  2. On another server, such as AWS")

    selection = input("Selection [1]: ").strip() or "1"

    try:
        if selection == "1":
            return configure_local()

        if selection == "2":
            return configure_remote()

        raise RuntimeError("Enter 1 or 2.")

    except (
        RuntimeError,
        requests.RequestException,
    ) as error:
        print(
            f"OpenEMR setup failed: {error}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
