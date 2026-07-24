#!/usr/bin/env python3
"""Apply or restore the local OpenEMR 8.0.0.3 vitals REST compatibility patch."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from detect_openemr import detect


ROOT = Path(__file__).resolve().parents[1]
BACKUP_DIR = ROOT / ".local/openemr-vitals-api-compat"
BACKUP_FILE = BACKUP_DIR / "EncounterRestController.php.original"

OPENEMR_ROOT = "/var/www/localhost/htdocs/openemr"
TARGET_FILE = (
    OPENEMR_ROOT
    + "/src/RestControllers/EncounterRestController.php"
)
CALCULATED_SERVICE_FILE = (
    OPENEMR_ROOT + "/src/Services/VitalsCalculatedService.php"
)

EXPECTED_VERSION = "8.0.0.3"
PATCH_MARKER = "MAPLE_GROVE_VITALS_API_COMPAT"

CONSTRUCTOR = """    public function __construct(private readonly SessionInterface $session)
    {
        $this->encounterService = new EncounterService();
    }
"""

HELPER = """    public function __construct(private readonly SessionInterface $session)
    {
        $this->encounterService = new EncounterService();
    }

    // MAPLE_GROVE_VITALS_API_COMPAT
    // OpenEMR 8.0.0.3 keeps OAuth values in the Symfony session used by
    // HttpRestRequest, while VitalsCalculatedService still reads the legacy
    // PHP session superglobal. Synchronize only the values needed by the
    // encounter-vitals service before POST or PUT processing.
    private function prepareVitalSessionData(array $data): array
    {
        $authUserId = $this->session->get('authUserID');
        if ($authUserId !== null && $authUserId !== '') {
            $_SESSION['authUserID'] = (int) $authUserId;
        }

        $authUser = $this->session->get('authUser');
        if ($authUser !== null && $authUser !== '') {
            $_SESSION['authUser'] = $authUser;
            if (!isset($data['user']) || $data['user'] === '') {
                $data['user'] = $authUser;
            }
        }

        $authProvider = $this->session->get('authProvider');
        if ($authProvider !== null && $authProvider !== '') {
            $_SESSION['authProvider'] = $authProvider;
            if (!isset($data['groupname']) || $data['groupname'] === '') {
                $data['groupname'] = $authProvider;
            }
        }

        return $data;
    }
"""

POST_OLD = """    public function postVital($pid, $eid, $data)
    {
        $validationResult = $this->encounterService->validateVital($data);
"""

POST_NEW = """    public function postVital($pid, $eid, $data)
    {
        $data = $this->prepareVitalSessionData($data);
        $validationResult = $this->encounterService->validateVital($data);
"""

PUT_OLD = """    public function putVital($pid, $eid, $vid, $data)
    {
        $validationResult = $this->encounterService->validateVital($data);
"""

PUT_NEW = """    public function putVital($pid, $eid, $vid, $data)
    {
        $data = $this->prepareVitalSessionData($data);
        $validationResult = $this->encounterService->validateVital($data);
"""


class PatchError(RuntimeError):
    pass


def run(
    args: list[str],
    *,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    if check and result.returncode != 0:
        command = " ".join(args)
        raise PatchError(
            f"Command failed ({result.returncode}): {command}\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )

    return result


def require_repo_root() -> None:
    if not (ROOT / "README.md").is_file() or not (ROOT / "scripts").is_dir():
        raise PatchError(
            "Repository root could not be verified. Run this script from "
            "the cloned project."
        )


def detect_container() -> str:
    result = run(["docker", "ps", "--format", "{{.Names}}"])
    names = [line.strip() for line in result.stdout.splitlines() if line.strip()]

    for name in names:
        probe = run(
            [
                "docker",
                "exec",
                name,
                "test",
                "-f",
                TARGET_FILE,
            ],
            check=False,
        )
        if probe.returncode == 0:
            return name

    raise PatchError("The local OpenEMR application container was not found.")


def read_container_file(container: str, path: str) -> str:
    return run(["docker", "exec", container, "cat", path]).stdout


def detect_version(container: str) -> str:
    detected = detect()

    detected_container = str(
        detected.get("openemr_container") or ""
    ).strip()
    if detected_container and detected_container != container:
        raise PatchError(
            "OpenEMR detector and compatibility script selected different "
            f"containers: {detected_container!r} versus {container!r}."
        )

    version = str(detected.get("version") or "").strip()
    if not version:
        raise PatchError(
            "The existing OpenEMR detector did not return a version."
        )

    return version


def verify_known_bug_source(container: str) -> None:
    source = read_container_file(container, CALCULATED_SERVICE_FILE)

    required = (
        "private int $authUserId;",
        "$this->authUserId = $_SESSION['authUserID'];",
        "public function setCurrentUserId(int $user): void",
    )

    missing = [item for item in required if item not in source]
    if missing:
        raise PatchError(
            "VitalsCalculatedService.php does not match the known "
            "OpenEMR 8.0.0.3 failure shape. Missing marker: "
            + missing[0]
        )


def validate_original(source: str) -> None:
    required = (CONSTRUCTOR, POST_OLD, PUT_OLD)
    missing = [item for item in required if item not in source]

    if missing:
        raise PatchError(
            "EncounterRestController.php did not match the expected "
            "OpenEMR 8.0.0.3 source. No changes were made."
        )


def build_patched(source: str) -> str:
    if PATCH_MARKER in source:
        return source

    validate_original(source)

    patched = source.replace(CONSTRUCTOR, HELPER, 1)
    patched = patched.replace(POST_OLD, POST_NEW, 1)
    patched = patched.replace(PUT_OLD, PUT_NEW, 1)

    if PATCH_MARKER not in patched:
        raise PatchError("Internal error: patch marker was not inserted.")

    return patched


def container_file_metadata(container: str) -> tuple[str, str]:
    result = run(
        [
            "docker",
            "exec",
            container,
            "stat",
            "-c",
            "%u:%g %a",
            TARGET_FILE,
        ]
    )
    fields = result.stdout.strip().split()

    if len(fields) != 2:
        raise PatchError("Could not read target file ownership and mode.")

    return fields[0], fields[1]


def install_source(container: str, source: str) -> None:
    owner, mode = container_file_metadata(container)

    with tempfile.TemporaryDirectory() as temp_dir:
        local_file = Path(temp_dir) / "EncounterRestController.php"
        local_file.write_text(source, encoding="utf-8")

        remote_temp = "/tmp/EncounterRestController.php.maple-grove"
        run(["docker", "cp", str(local_file), f"{container}:{remote_temp}"])

        validation = run(
            ["docker", "exec", container, "php", "-l", remote_temp]
        )
        if "No syntax errors detected" not in validation.stdout:
            raise PatchError(
                "PHP syntax validation did not report success."
            )

        run(
            [
                "docker",
                "exec",
                container,
                "sh",
                "-lc",
                (
                    f"cp {remote_temp} {TARGET_FILE} && "
                    f"chown {owner} {TARGET_FILE} && "
                    f"chmod {mode} {TARGET_FILE} && "
                    f"rm -f {remote_temp}"
                ),
            ]
        )

    final_validation = run(
        ["docker", "exec", container, "php", "-l", TARGET_FILE]
    )
    if "No syntax errors detected" not in final_validation.stdout:
        raise PatchError("Installed PHP file failed syntax validation.")


def restart_container(container: str) -> None:
    run(["docker", "restart", container])
    time.sleep(3)


def apply_patch(container: str) -> None:
    version = detect_version(container)

    if version != EXPECTED_VERSION:
        raise PatchError(
            f"This compatibility patch only supports OpenEMR "
            f"{EXPECTED_VERSION}; detected {version}."
        )

    verify_known_bug_source(container)
    source = read_container_file(container, TARGET_FILE)

    if PATCH_MARKER in source:
        print(
            "Vitals REST compatibility patch is already installed."
        )
        return

    patched = build_patched(source)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if not BACKUP_FILE.exists():
        BACKUP_FILE.write_text(source, encoding="utf-8")
        print(f"Saved original source: {BACKUP_FILE}")
    else:
        print(f"Existing original backup retained: {BACKUP_FILE}")

    install_source(container, patched)
    restart_container(container)

    installed = read_container_file(container, TARGET_FILE)
    if PATCH_MARKER not in installed:
        raise PatchError(
            "Patch marker was not found after container restart."
        )

    print("Installed local OpenEMR vitals REST compatibility patch.")
    print(f"OpenEMR version: {version}")
    print(f"Container restarted: {container}")


def restore_patch(container: str) -> None:
    if not BACKUP_FILE.is_file():
        raise PatchError(
            f"Original backup does not exist: {BACKUP_FILE}"
        )

    original = BACKUP_FILE.read_text(encoding="utf-8")
    validate_original(original)
    install_source(container, original)
    restart_container(container)

    restored = read_container_file(container, TARGET_FILE)
    if PATCH_MARKER in restored:
        raise PatchError(
            "Patch marker is still present after restore."
        )

    print("Restored the original EncounterRestController.php.")
    print(f"Container restarted: {container}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Apply the local OpenEMR 8.0.0.3 encounter-vitals REST "
            "session compatibility patch."
        )
    )
    parser.add_argument(
        "--restore",
        action="store_true",
        help="Restore the original controller from the local backup.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        require_repo_root()

        if shutil.which("docker") is None:
            raise PatchError("Docker was not found on PATH.")

        container = detect_container()
        print(f"OpenEMR container: {container}")

        if args.restore:
            restore_patch(container)
        else:
            apply_patch(container)

        return 0

    except (PatchError, OSError) as error:
        print(f"Vitals compatibility setup failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
