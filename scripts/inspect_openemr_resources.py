#!/usr/bin/env python3
"""List existing OpenEMR facilities and practitioners."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

from detect_openemr import detect
from openemr_auth import request_access_token
from openemr_http import create_openemr_session


ROOT = Path(__file__).resolve().parents[1]
CLIENT_FILE = ROOT / ".local/openemr-client.json"


def main() -> int:
    try:
        client = json.loads(CLIENT_FILE.read_text(encoding="utf-8"))
        openemr = detect()

        if client.get("base_url") != openemr["base_url"]:
            raise RuntimeError(
                "The saved OAuth client belongs to a different "
                "OpenEMR server."
            )

        token, _ = request_access_token(client, openemr)
        session = create_openemr_session(openemr)

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

        for resource in ("facility", "practitioner"):
            response = session.get(
                f"{openemr['api_base_url']}/{resource}",
                headers=headers,
                params={"_count": 100, "_offset": 0},
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
        RuntimeError,
        OSError,
        KeyError,
        json.JSONDecodeError,
        requests.RequestException,
    ) as error:
        print(f"Resource inspection failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
