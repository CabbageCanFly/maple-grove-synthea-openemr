#!/usr/bin/env python3
"""Collect OpenEMR login credentials without exposing passwords."""

from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import urllib3

from detect_openemr import detect


ROOT = Path(__file__).resolve().parents[1]
LOGIN_FILE = ROOT / ".local" / "openemr-login.json"


def yes_no(prompt: str, *, default: bool = False) -> bool:
    """Ask a simple yes/no question."""
    suffix = " [Y/n]: " if default else " [y/N]: "
    answer = input(prompt + suffix).strip().casefold()

    if not answer:
        return default

    return answer in {"y", "yes"}


def is_local_target(openemr: dict[str, Any]) -> bool:
    """Return True when using the local Docker OpenEMR installation."""
    if openemr.get("target_mode") == "local":
        return True

    hostname = (
        urlparse(str(openemr.get("base_url") or "")).hostname
        or ""
    ).casefold()

    return hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }


def load_saved_username(base_url: str) -> str:
    """Return the username remembered for this OpenEMR server."""
    if not LOGIN_FILE.is_file():
        return ""

    try:
        saved = json.loads(
            LOGIN_FILE.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return ""

    if saved.get("base_url") != base_url:
        return ""

    return str(saved.get("username") or "").strip()


def save_username(base_url: str, username: str) -> None:
    """Remember a non-secret username for the selected server."""
    LOGIN_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    LOGIN_FILE.write_text(
        json.dumps(
            {
                "base_url": base_url,
                "username": username,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    try:
        os.chmod(LOGIN_FILE, 0o600)
    except OSError:
        pass


def prompt_for_credentials(
    base_url: str,
    saved_username: str = "",
) -> tuple[str, str]:
    """Prompt for a username and hidden password."""
    if saved_username:
        entered = input(
            f"OpenEMR username [{saved_username}]: "
        ).strip()
        username = entered or saved_username
    else:
        username = input("OpenEMR username: ").strip()

    if not username:
        raise RuntimeError(
            "The OpenEMR username cannot be empty."
        )

    password = getpass.getpass(
        f"OpenEMR password for {username}: "
    )

    if not password:
        raise RuntimeError(
            "The OpenEMR password cannot be empty."
        )

    save_username(base_url, username)

    return username, password


def get_credentials(
    openemr: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Return credentials for the selected OpenEMR installation."""
    openemr = openemr or detect()
    base_url = str(openemr["base_url"])

    environment_username = os.getenv(
        "OPENEMR_USERNAME",
        "",
    ).strip()
    environment_password = os.getenv(
        "OPENEMR_PASSWORD",
        "",
    )

    # Environment variables remain available as an advanced option.
    if environment_username or environment_password:
        if is_local_target(openemr):
            username = environment_username or "admin"
            password = environment_password or "pass"
        else:
            username = environment_username
            password = environment_password

        if not username or not password:
            raise RuntimeError(
                "Both OPENEMR_USERNAME and OPENEMR_PASSWORD "
                "must be provided for a remote server."
            )

        return username, password

    saved_username = load_saved_username(base_url)

    if is_local_target(openemr):
        # A remembered username means the student previously chose
        # custom local credentials.
        if saved_username:
            return prompt_for_credentials(
                base_url,
                saved_username,
            )

        print()
        print("Local Docker OpenEMR detected.")

        if yes_no(
            "Use the default local login (admin / pass)?",
            default=True,
        ):
            return "admin", "pass"

        return prompt_for_credentials(base_url)

    # Remote OpenEMR always uses the student's assigned login.
    return prompt_for_credentials(
        base_url,
        saved_username,
    )


def authenticated_subprocess_environment(
    openemr: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Build an environment for child importer scripts."""
    openemr = openemr or detect()
    username, password = get_credentials(openemr)

    environment = os.environ.copy()
    environment["OPENEMR_USERNAME"] = username
    environment["OPENEMR_PASSWORD"] = password

    return environment


def request_access_token(
    client: dict[str, Any],
    openemr: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Authenticate and return the access token and username."""
    openemr = openemr or detect()
    username, password = get_credentials(openemr)

    verify_tls = bool(
        openemr.get("verify_tls", True)
    )

    if not verify_tls:
        urllib3.disable_warnings(
            urllib3.exceptions.InsecureRequestWarning
        )

    token_data = {
        "grant_type": "password",
        "client_id": client["client_id"],
        "scope": client["scope"],
        "user_role": "users",
        "username": username,
        "password": password,
    }

    client_secret = client.get("client_secret")

    if client_secret:
        token_data["client_secret"] = client_secret

    response = requests.post(
        client["token_endpoint"],
        data=token_data,
        verify=verify_tls,
        timeout=30,
    )

    if not response.ok:
        raise RuntimeError(
            f"Token request returned HTTP "
            f"{response.status_code}:\n"
            f"{response.text[:1000]}"
        )

    try:
        body = response.json()
    except requests.JSONDecodeError as exc:
        raise RuntimeError(
            "OpenEMR did not return a valid token response."
        ) from exc

    access_token = body.get("access_token")

    if not access_token:
        raise RuntimeError(
            "OpenEMR did not return an access token."
        )

    return str(access_token), username
