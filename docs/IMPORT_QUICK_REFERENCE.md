# OpenEMR Import Quick Reference

This is the current developer/student-at-a-glance workflow for the implemented import scripts.

Run all commands from the repository root.

> The final release should replace most resource-specific commands with one orchestrated import command.

## 1. Enter the project and activate Python

```bash
source .venv/bin/activate
python3 -m pip install -r requirements_openemr_import.txt
```

Confirm you are in the repository root:

```bash
test -f README.md && test -d scripts && echo "Project folder detected."
```

## 2. Prepare the OpenEMR connection

Detect the target:

```bash
python3 scripts/detect_openemr.py
```

For local Docker OpenEMR, ensure HTTPS is available:

```bash
python3 scripts/ensure_local_https.py
```

Register an OAuth client:

```bash
python3 scripts/register_openemr_client.py
```

Then enable the newly registered client in OpenEMR:

```text
Administration -> System -> API Clients
```

Test the connection:

```bash
python3 scripts/test_openemr_connection.py
```

## 3. Import patients

Dry-run one patient:

```bash
python3 scripts/import_openemr.py --limit 1
```

Import all patients:

```bash
python3 scripts/import_openemr.py --all --commit
```

## 4. Import encounters

Dry-run one encounter:

```bash
python3 scripts/import_openemr_encounters.py --limit 1
```

Import all encounters with live progress:

```bash
python3 -u scripts/import_openemr_encounters.py \
  --all \
  --commit \
  --progress-every 250
```

## 5. Import curated medical problems

Dry-run one disorder:

```bash
python3 scripts/import_openemr_conditions.py \
  --semantic-tag disorder \
  --limit 1
```

Import the approved clinical-problem categories:

```bash
python3 -u scripts/import_openemr_conditions.py \
  --semantic-tag disorder \
  --semantic-tag "morphologic abnormality" \
  --semantic-tag untagged \
  --all \
  --commit \
  --quiet \
  --progress-every 100
```

The importer intentionally excludes findings, situations, and person-context records from Medical Problems.

## 6. Import curated allergies

Dry-run one allergy:

```bash
python3 scripts/import_openemr_allergies.py --limit 1
```

Import all approved allergies with live progress:

```bash
python3 -u scripts/import_openemr_allergies.py \
  --all \
  --commit \
  --quiet \
  --progress-every 10
```

Generic `Allergic disposition (finding)` rows are excluded automatically.

## 7. Import medications

Dry-run one medication episode:

```bash
python3 scripts/import_openemr_medications.py --limit 1
```

Import all medication episodes:

```bash
python3 -u scripts/import_openemr_medications.py \
  --all \
  --commit \
  --quiet \
  --progress-every 100
```

## 8. Import historical vital signs

For the exact affected local OpenEMR 8.0.0.3 target, verify the local
compatibility patch first:

```bash
python3 scripts/ensure_local_vitals_api_compat.py
```

Import every supported grouped vital form:

```bash
python3 -u scripts/import_openemr_vitals.py \
  --all \
  --commit \
  --quiet \
  --progress-every 100
```

A completed rerun should create zero forms and skip every supported grouped form.
Do not apply the OpenEMR 8.0.0.3 compatibility patch to OpenEMR 7 or another
OpenEMR version.

## Important behavior

- Without `--commit`, import scripts perform a dry run.
- Re-running a completed command should skip records already imported.
- Generated mappings and OAuth credentials are stored under `.local/`.
- Never commit `.local/`, passwords, access tokens, client secrets, or generated output logs.
- Use `--help` to inspect a script's current options.

```bash
python3 scripts/import_openemr_encounters.py --help
python3 scripts/import_openemr_conditions.py --help
python3 scripts/import_openemr_allergies.py --help
python3 scripts/import_openemr_medications.py --help
python3 scripts/import_openemr_vitals.py --help
```

## Current import order

1. Patients
2. Encounters
3. Curated medical problems
4. Curated allergies
5. Medications
6. Selected observations and vitals
7. Selected procedures
8. Immunizations
9. Optional care plans and other selected resources
