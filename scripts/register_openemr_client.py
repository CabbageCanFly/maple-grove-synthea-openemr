#!/usr/bin/env python3
"""Register and safely store a local Maple Grove OpenEMR OAuth client."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
import urllib3

from detect_openemr import detect


ROOT = Path(__file__).resolve().parents[1]
CLIENT_FILE = ROOT / ".local" / "openemr-client.json"
CLIENT_NAME = "Maple Grove Synthea Importer"
def scopes_for_version(major_version: int) -> str:
    """Return equivalent minimum scopes for OpenEMR 7 or 8."""

    if major_version >= 8:
        return (
            "openid api:oemr "
            "user/patient.crs "
            "user/encounter.crs "
            "user/facility.crs "
            "user/practitioner.rs"
        )

    if major_version == 7:
        return (
            "openid api:oemr "
            "user/patient.read user/patient.write "
            "user/encounter.read user/encounter.write "
            "user/facility.read user/facility.write "
            "user/practitioner.read"
        )

    raise RuntimeError(
        f"Unsupported OpenEMR major version: {major_version}"
    )



def main() -> int:
    try:
        openemr = detect()
        base_url = openemr["base_url"]
        major_version = openemr.get("major_version")

        if not isinstance(major_version, int):
            raise RuntimeError(
                "The OpenEMR major version could not be detected."
            )

        scopes = scopes_for_version(major_version)

        if openemr["scheme"] != "https":
            raise RuntimeError(
                "Local HTTPS is not ready. Run ensure_local_https.py first."
            )

        if CLIENT_FILE.exists():
            saved = json.loads(CLIENT_FILE.read_text(encoding="utf-8"))

            if saved.get("base_url") != base_url:
                raise RuntimeError(
                    "The saved client belongs to a different OpenEMR URL. "
                    "Remove .local/openemr-client.json and run again."
                )

            print("Existing OpenEMR OAuth client is ready")
            print(f"  Client name: {saved['client_name']}")
            print(f"  Client ID: {saved['client_id']}")
            print(f"  Credentials: {CLIENT_FILE}")
            return 0

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        discovery_url = (
            f"{base_url}/oauth2/default/"
            ".well-known/openid-configuration"
        )

        discovery_response = requests.get(
            discovery_url,
            verify=False,
            timeout=20,
        )
        discovery_response.raise_for_status()
        discovery = discovery_response.json()

        registration_endpoint = discovery.get("registration_endpoint")
        token_endpoint = discovery.get("token_endpoint")

        if not registration_endpoint or not token_endpoint:
            raise RuntimeError(
                "OAuth discovery did not return registration/token endpoints."
            )

        registration = {
            "application_type": "private",
            "client_name": CLIENT_NAME,
            "redirect_uris": [
                f"{base_url}/maple-grove/oauth/callback"
            ],
            "token_endpoint_auth_method": "client_secret_post",
            "scope": scopes,
        }

        response = requests.post(
            registration_endpoint,
            json=registration,
            verify=False,
            timeout=30,
        )

        if not response.ok:
            raise RuntimeError(
                f"Client registration returned HTTP {response.status_code}:\n"
                f"{response.text[:1000]}"
            )

        registered = response.json()
        client_id = registered.get("client_id")
        client_secret = registered.get("client_secret")

        if not client_id or not client_secret:
            raise RuntimeError(
                "Registration succeeded but did not return both "
                "client_id and client_secret."
            )

        saved = {
            "client_name": CLIENT_NAME,
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scopes,
            "base_url": base_url,
            "issuer": discovery.get("issuer"),
            "registration_endpoint": registration_endpoint,
            "token_endpoint": token_endpoint,
        }

        CLIENT_FILE.parent.mkdir(parents=True, exist_ok=True)
        CLIENT_FILE.write_text(
            json.dumps(saved, indent=2) + "\n",
            encoding="utf-8",
        )

        try:
            os.chmod(CLIENT_FILE, 0o600)
        except OSError:
            pass

        print("OpenEMR OAuth client registered")
        print(f"  Client name: {CLIENT_NAME}")
        print(f"  Client ID: {client_id}")
        print(f"  Requested scope: {scopes}")
        print(f"  Credentials saved privately: {CLIENT_FILE}")
        print("  Client secret was not printed.")

        return 0

    except (
        RuntimeError,
        requests.RequestException,
        json.JSONDecodeError,
    ) as error:
        print(f"Client registration failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
