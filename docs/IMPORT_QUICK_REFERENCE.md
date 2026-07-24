# OpenEMR Import Quick Reference

Run all commands from the repository root.

## 1. Prepare Python

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements_openemr_import.txt
```

Confirm the repository root:

```bash
test -f README.md && test -d scripts && echo "Project folder detected."
```

## 2. Generate or select a dataset

Generate a fresh 100-patient GTA dataset:

```bash
python3 scripts/generate_gta_patients.py --population 100
```

The generator writes a unique run below `output/runs/` and updates:

```text
output/current-dataset.json
```

To use an existing dataset explicitly, add this to an orchestrator command:

```bash
--csv-dir output/gta-100-v2/csv
```

## 3. Prepare the OpenEMR connection

```bash
python3 scripts/detect_openemr.py
python3 scripts/ensure_local_https.py
python3 scripts/register_openemr_client.py
```

Enable the newest client in:

```text
Administration -> System -> API Clients
```

Then test it:

```bash
python3 scripts/test_openemr_connection.py
```

## 4. Run the supported workflow

Preflight only:

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

For legacy development maps created before dataset/target binding, verify the
selected dataset and OpenEMR target, then adopt them once:

```bash
python3 scripts/import_openemr.py \
  --csv-dir output/gta-100-v2/csv \
  --adopt-existing-state
```

## 5. Run selected resources

```bash
python3 scripts/import_openemr.py \
  --resource patients \
  --resource encounters \
  --commit
```

## 6. Resource-specific developer commands

These are for pilots and focused validation. The orchestrator is preferred for
a normal complete run.

### Patients

```bash
python3 scripts/import_openemr_patients.py \
  --patients-csv output/gta-100-v2/csv/patients.csv \
  --limit 1
```

### Encounters

```bash
python3 -u scripts/import_openemr_encounters.py \
  --encounters-csv output/gta-100-v2/csv/encounters.csv \
  --organizations-csv output/gta-100-v2/csv/organizations.csv \
  --providers-csv output/gta-100-v2/csv/providers.csv \
  --all \
  --commit \
  --progress-every 250
```

### Conditions

```bash
python3 -u scripts/import_openemr_conditions.py \
  --conditions-csv output/gta-100-v2/csv/conditions.csv \
  --semantic-tag disorder \
  --semantic-tag "morphologic abnormality" \
  --semantic-tag untagged \
  --all \
  --commit \
  --quiet \
  --progress-every 100
```

### Allergies

```bash
python3 -u scripts/import_openemr_allergies.py \
  --allergies-csv output/gta-100-v2/csv/allergies.csv \
  --all \
  --commit \
  --quiet \
  --progress-every 10
```

### Medications

```bash
python3 -u scripts/import_openemr_medications.py \
  --medications-csv output/gta-100-v2/csv/medications.csv \
  --all \
  --commit \
  --quiet \
  --progress-every 100
```

### Vital signs

```bash
python3 -u scripts/import_openemr_vitals.py \
  --observations-csv output/gta-100-v2/csv/observations.csv \
  --all \
  --commit \
  --quiet \
  --progress-every 100
```

## Important behavior

- Without `--commit`, the orchestrator performs preflight only.
- Re-running the same selected dataset should skip tracked records.
- `output/current-dataset.json` selects the normal default dataset.
- `.local/import-context.json` binds maps to one dataset and OpenEMR target.
- Do not reuse `.local` maps for a different generation or OpenEMR installation.
- Never commit `.local/`, generated `output/`, secrets, or access tokens.
- Use `--help` to inspect current options.
