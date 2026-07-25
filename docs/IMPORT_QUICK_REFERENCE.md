# OpenEMR import quick reference

This page is for students or maintainers who already completed the full
[`README.md`](../README.md) setup.

Run commands from the repository root.

## Start a terminal session

```bash
source .venv/bin/activate
source scripts/openemr_session.sh
python3 scripts/test_openemr_connection.py
```

If the password is wrong, rerun:

```bash
source scripts/openemr_session.sh
```

## Generate a dataset

```bash
python3 scripts/generate_gta_patients.py --population 100
```

The generator creates a unique run under `output/runs/` and selects it in:

```text
output/current-dataset.json
```

## Select local Docker or AWS OpenEMR

```bash
python3 scripts/configure_openemr_target.py
```

For local Docker only:

```bash
python3 scripts/ensure_local_https.py
```

Register a client on the selected OpenEMR target:

```bash
python3 scripts/register_openemr_client.py
```

Enable the newest timestamped client in:

```text
Administration -> System -> API Clients
```

Then log in and test:

```bash
source scripts/openemr_session.sh
python3 scripts/test_openemr_connection.py
```

## Run the complete workflow

Preflight without creating records:

```bash
python3 scripts/import_openemr.py
```

Create records:

```bash
python3 scripts/import_openemr.py \
  --commit \
  --quiet \
  --progress-every 100
```

Supported dependency order:

1. patients;
2. encounters;
3. curated conditions;
4. curated allergies;
5. medications;
6. supported vital signs.

Rerun the same command to verify that tracked records are skipped.

## Run selected resources

```bash
python3 scripts/import_openemr.py \
  --resource patients \
  --resource encounters \
  --commit
```

## Inspect target resources

```bash
python3 scripts/inspect_openemr_resources.py
```

This is useful when diagnosing facilities, providers, authorization, or NPI
setup.

## Use an existing CSV directory

```bash
python3 scripts/import_openemr.py \
  --csv-dir output/example-run/csv
```

If multiple datasets exist and none is selected, the orchestrator stops instead
of guessing.

## Start a deliberately new dataset mapping state

Use the orchestrator's supported state-management option rather than manually
mixing maps from different datasets:

```bash
python3 scripts/import_openemr.py \
  --start-new-dataset \
  --commit \
  --quiet \
  --progress-every 100
```

Review the preflight behavior before using this with an important target.

## Complete local reset

Only for a clean test or when instructed. This removes the selected target,
OAuth client details, username memory, and local import maps:

```bash
openemr_logout 2>/dev/null || true; rm -rf .local
python3 scripts/configure_openemr_target.py
```

It does not delete records already in OpenEMR. Do not clear maps and then assume
that every existing record can still be safely identified as imported.

## Resource-specific developer commands

These are for focused validation. The orchestrator is preferred for normal use.

### Patients

```bash
python3 scripts/import_openemr_patients.py \
  --patients-csv output/example-run/csv/patients.csv \
  --limit 1
```

### Encounters

```bash
python3 -u scripts/import_openemr_encounters.py \
  --encounters-csv output/example-run/csv/encounters.csv \
  --organizations-csv output/example-run/csv/organizations.csv \
  --providers-csv output/example-run/csv/providers.csv \
  --all \
  --commit \
  --progress-every 250
```

### Conditions

```bash
python3 -u scripts/import_openemr_conditions.py \
  --conditions-csv output/example-run/csv/conditions.csv \
  --all \
  --commit \
  --quiet \
  --progress-every 100
```

### Allergies

```bash
python3 -u scripts/import_openemr_allergies.py \
  --allergies-csv output/example-run/csv/allergies.csv \
  --all \
  --commit \
  --quiet \
  --progress-every 10
```

### Medications

```bash
python3 -u scripts/import_openemr_medications.py \
  --medications-csv output/example-run/csv/medications.csv \
  --all \
  --commit \
  --quiet \
  --progress-every 100
```

### Vital signs

```bash
python3 -u scripts/import_openemr_vitals.py \
  --observations-csv output/example-run/csv/observations.csv \
  --all \
  --commit \
  --quiet \
  --progress-every 100
```

## Important behavior

- Without `--commit`, the orchestrator performs preflight only.
- Re-running the same selected dataset should skip tracked records.
- `.local/import-context.json` binds import maps to one dataset and target.
- Do not reuse `.local` maps with a different OpenEMR installation.
- Never commit `.local/`, generated `output/`, secrets, or access tokens.
- Use `python3 scripts/import_openemr.py --help` for current options.
