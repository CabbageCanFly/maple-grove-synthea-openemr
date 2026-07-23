#!/usr/bin/env python3
"""Authenticate to local OpenEMR and perform a read-only patient request."""

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


def main() -> int:
    try:
        if not CLIENT_FILE.is_file():
            raise RuntimeError(
                "OAuth client credentials were not found. "
                "Run register_openemr_client.py first."
            )

        client = json.loads(CLIENT_FILE.read_text(encoding="utf-8"))
        openemr = detect()

        if client.get("base_url") != openemr["base_url"]:
            raise RuntimeError(
                "The saved OAuth client belongs to a different OpenEMR URL."
            )

        username = os.getenv("OPENEMR_USERNAME", "admin")
        password = os.getenv("OPENEMR_PASSWORD", "pass")

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        token_response = requests.post(
            client["token_endpoint"],
            data={
                "grant_type": "password",
                "client_id": client["client_id"],
                "scope": client["scope"],
                "user_role": "users",
                "username": username,
                "password": password,
            },
            verify=False,
            timeout=30,
        )

        if not token_response.ok:
            raise RuntimeError(
                f"Token request returned HTTP {token_response.status_code}:\n"
                f"{token_response.text[:1000]}"
            )

        token_data = token_response.json()
        access_token = token_data.get("access_token")

        if not access_token:
            raise RuntimeError("OpenEMR did not return an access token.")

        patient_response = requests.get(
            f"{openemr['api_base_url']}/patient",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            verify=False,
            timeout=30,
        )

        if not patient_response.ok:
            raise RuntimeError(
                f"Patient request returned HTTP {patient_response.status_code}:\n"
                f"{patient_response.text[:1000]}"
            )

        body = patient_response.json()
        patients = body.get("data", [])

        print("OpenEMR connection test passed")
        print(f"  OpenEMR version: {openemr['version']}")
        print(f"  Base URL: {openemr['base_url']}")
        print(f"  Authenticated user: {username}")
        print(f"  Patient API status: {patient_response.status_code}")
        print(f"  Existing patients returned: {len(patients)}")
        print("  Access token was not printed or saved.")

        return 0

    except (
        RuntimeError,
        requests.RequestException,
        json.JSONDecodeError,
    ) as error:
        print(f"Connection test failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
