#!/usr/bin/env python3
"""Create HTTP sessions that respect the selected OpenEMR target."""

from __future__ import annotations

from typing import Any

import requests
import urllib3


def create_openemr_session(
    openemr: dict[str, Any],
) -> requests.Session:
    """Return a session configured for the target's TLS policy."""
    session = requests.Session()
    session.verify = bool(openemr.get("verify_tls", True))

    if not session.verify:
        urllib3.disable_warnings(
            urllib3.exceptions.InsecureRequestWarning
        )

    return session
