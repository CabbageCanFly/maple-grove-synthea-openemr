#!/usr/bin/env python3
"""Test authentication and API access to the selected OpenEMR."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import requests
import urllib3

from detect_openemr import detect
from openemr_auth import request_access_token


ROOT = Path(__file__).resolve().parents[1]
CLIENT_FILE = ROOT / ".local" / "openemr-client.json"


def main() -> int:
    try:
        if not CLIENT_FILE.is_file():
            raise RuntimeError(
                "No registered OAuth client was found.\n"
                "Run:\n"
                "  python3 scripts/register_openemr_client.py"
            )

        client = json.loads(
            CLIENT_FILE.read_text(encoding="utf-8")
        )

        openemr = detect()

        client_base_url = str(
            client.get("base_url") or ""
        ).rstrip("/")

        selected_base_url = str(
            openemr["base_url"]
        ).rstrip("/")

        if (
            client_base_url
            and client_base_url != selected_base_url
        ):
            raise RuntimeError(
                "The saved OAuth client belongs to another "
                "OpenEMR server.\n"
                "Remove it and register a new client:\n"
                "  rm -f .local/openemr-client.json\n"
                "  python3 scripts/register_openemr_client.py"
            )

        access_token, username = request_access_token(
            client,
            openemr,
        )

        verify_tls = bool(
            openemr.get("verify_tls", True)
        )

        if not verify_tls:
            urllib3.disable_warnings(
                urllib3.exceptions.InsecureRequestWarning
            )

        response = requests.get(
            f"{openemr['api_base_url']}/patient",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            verify=verify_tls,
            timeout=30,
        )

        if not response.ok:
            raise RuntimeError(
                f"The patient API returned HTTP "
                f"{response.status_code}:\n"
                f"{response.text[:1000]}"
            )

        body = response.json()
        patients = body.get("data", [])

        if not isinstance(patients, list):
            patients = []

        print()
        print("OpenEMR connection test passed.")
        print(f"  Server: {openemr['base_url']}")
        print(f"  Logged in as: {username}")
        print(f"  Patient records returned: {len(patients)}")
        print("  Password was not saved.")

        return 0

    except (
        RuntimeError,
        OSError,
        ValueError,
        requests.RequestException,
    ) as error:
        print(
            f"Connection test failed:\n{error}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
