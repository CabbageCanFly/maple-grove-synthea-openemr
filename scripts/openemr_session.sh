#!/usr/bin/env bash

# This script must be sourced so credentials remain available
# in the current terminal session.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    echo "Run this script with:"
    echo
    echo "  source scripts/openemr_session.sh"
    echo
    echo "Do not run it as ./scripts/openemr_session.sh"
    exit 1
fi

_openemr_repo_root="$(
    cd "$(dirname "${BASH_SOURCE[0]}")/.." &&
    pwd
)"

_openemr_info="$(
    cd "$_openemr_repo_root" &&
    python3 - <<'PY'
import json
import sys
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root / "scripts"))

from detect_openemr import detect

openemr = detect()
base_url = str(openemr["base_url"])
target_mode = str(openemr.get("target_mode") or "")

login_file = root / ".local" / "openemr-login.json"
saved_username = ""

if login_file.is_file():
    try:
        saved = json.loads(login_file.read_text(encoding="utf-8"))

        if saved.get("base_url") == base_url:
            saved_username = str(
                saved.get("username") or ""
            ).strip()
    except (OSError, json.JSONDecodeError):
        pass

print(
    "|".join(
        (
            base_url,
            target_mode,
            saved_username,
        )
    )
)
PY
)" || {
    echo "Could not detect the selected OpenEMR server."
    return 1
}

IFS='|' read -r \
    _openemr_base_url \
    _openemr_target_mode \
    _openemr_saved_username \
    <<< "$_openemr_info"

echo
echo "OpenEMR terminal login"
echo "  Server: $_openemr_base_url"
echo

if [[ -n "$_openemr_saved_username" ]]; then
    read -r -p \
        "OpenEMR username [$_openemr_saved_username]: " \
        _openemr_username

    _openemr_username="${_openemr_username:-$_openemr_saved_username}"
elif [[ "$_openemr_target_mode" == "local" ]]; then
    read -r -p "OpenEMR username [admin]: " \
        _openemr_username

    _openemr_username="${_openemr_username:-admin}"
else
    read -r -p "OpenEMR username: " \
        _openemr_username
fi

if [[ -z "$_openemr_username" ]]; then
    echo "The OpenEMR username cannot be empty."
    return 1
fi

_openemr_password=""

if (
    [[ "$_openemr_target_mode" == "local" ]] &&
    [[ "$_openemr_username" == "admin" ]]
); then
    read -r -p \
        "Use the default local password (pass)? [Y/n]: " \
        _openemr_default_password

    case "${_openemr_default_password,,}" in
        ""|y|yes)
            _openemr_password="pass"
            ;;
    esac
fi

if [[ -z "$_openemr_password" ]]; then
    read -r -s -p \
        "OpenEMR password for $_openemr_username: " \
        _openemr_password
    echo
fi

if [[ -z "$_openemr_password" ]]; then
    echo "The OpenEMR password cannot be empty."
    return 1
fi

export OPENEMR_USERNAME="$_openemr_username"
export OPENEMR_PASSWORD="$_openemr_password"

OPENEMR_SESSION_REPO_ROOT="$_openemr_repo_root" \
OPENEMR_SESSION_BASE_URL="$_openemr_base_url" \
OPENEMR_SESSION_USERNAME="$_openemr_username" \
python3 - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["OPENEMR_SESSION_REPO_ROOT"])
path = root / ".local" / "openemr-login.json"

path.parent.mkdir(parents=True, exist_ok=True)

path.write_text(
    json.dumps(
        {
            "base_url": os.environ[
                "OPENEMR_SESSION_BASE_URL"
            ],
            "username": os.environ[
                "OPENEMR_SESSION_USERNAME"
            ],
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)

try:
    path.chmod(0o600)
except OSError:
    pass
PY

openemr_logout() {
    unset OPENEMR_USERNAME
    unset OPENEMR_PASSWORD
    echo "OpenEMR credentials removed from this terminal session."
}

unset _openemr_password
unset _openemr_username
unset _openemr_saved_username
unset _openemr_target_mode
unset _openemr_base_url
unset _openemr_info
unset _openemr_repo_root
unset _openemr_default_password

echo
echo "OpenEMR login is ready for this terminal session."
echo "You can now run connection tests and imports without"
echo "entering the password again."
echo
echo "To remove the login before closing the terminal, run:"
echo "  openemr_logout"
