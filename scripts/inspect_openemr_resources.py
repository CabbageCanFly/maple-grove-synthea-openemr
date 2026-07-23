#!/usr/bin/env python3
"""List existing OpenEMR facilities and practitioners."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
import urllib3

from detect_openemr import detect


ROOT = Path(__file__).resolve().parents[1]
CLIENT_FILE = ROOT / ".local/openemr-client.json"


def main() -> int:
    try:
        client = json.loads(CLIENT_FILE.read_text(encoding="utf-8"))
        openemr = detect()

        urllib3.disable_warnings(
            urllib3.exceptions.InsecureRequestWarning
        )

        token_response = requests.post(
            client["token_endpoint"],
            data={
                "grant_type": "password",
                "client_id": client["client_id"],
                "scope": client["scope"],
                "user_role": "users",
                "username": os.getenv("OPENEMR_USERNAME", "admin"),
                "password": os.getenv("OPENEMR_PASSWORD", "pass"),
            },
            verify=False,
            timeout=30,
        )
        token_response.raise_for_status()
        token = token_response.json()["access_token"]

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

        for resource in ("facility", "practitioner"):
            response = requests.get(
                f"{openemr['api_base_url']}/{resource}",
                headers=headers,
                params={"_count": 100, "_offset": 0},
                verify=False,
                timeout=30,
            )
            response.raise_for_status()

            payload = response.json()
            records = payload.get("data", [])

            if resource == "practitioner":
                unique = {}
                for record in records:
                    key = record.get("uuid") or record.get("id")
                    unique[str(key)] = record
                records = list(unique.values())
            print(f"\n=== {resource.upper()} ({len(records)}) ===")

            for record in records:
                if resource == "facility":
                    print({
                        "id": record.get("id"),
                        "uuid": record.get("uuid"),
                        "name": record.get("name"),
                        "city": record.get("city"),
                    })
                else:
                    print({
                        "id": record.get("id"),
                        "uuid": record.get("uuid"),
                        "username": record.get("username"),
                        "first_name": (
                            record.get("fname")
                            or record.get("first_name")
                        ),
                        "last_name": (
                            record.get("lname")
                            or record.get("last_name")
                        ),
                        "active": record.get("active"),
                        "authorized": record.get("authorized"),
                    })

        return 0

    except (
        OSError,
        KeyError,
        json.JSONDecodeError,
        requests.RequestException,
    ) as error:
        print(f"Resource inspection failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
