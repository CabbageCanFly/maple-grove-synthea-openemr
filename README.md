# Maple Grove Synthea and OpenEMR

This repository generates synthetic Greater Toronto Area patient data with
Synthea and imports the clinical resources that are writable through the
supported OpenEMR API workflow.

## Fresh local workflow

Run commands from the repository root.

### 1. Prepare Python

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements_openemr_import.txt
```

### 2. Add the GTA Synthea JAR

Download the versioned GTA Synthea JAR from GitHub Releases and place it in:

```text
dist/synthea-gta-maple-grove-v0.1.1.jar
```

### 3. Generate a dataset

```bash
python3 scripts/generate_gta_patients.py --population 100
```

Each generation is written to a unique directory under `output/runs/`. The
generator records the selected run in `output/current-dataset.json`, so the
import command does not depend on a hard-coded folder name.

### 4. Prepare OpenEMR

```bash
python3 scripts/detect_openemr.py
python3 scripts/ensure_local_https.py
python3 scripts/register_openemr_client.py
```

Enable the newly registered client in:

```text
Administration -> System -> API Clients
```

Then test it:

```bash
python3 scripts/test_openemr_connection.py
```

### 5. Preflight and import

```bash
python3 scripts/import_openemr.py
```

The preflight creates no OpenEMR records. Run the displayed workflow with:

```bash
python3 scripts/import_openemr.py \
  --commit \
  --quiet \
  --progress-every 100
```

The supported workflow runs in dependency order:

1. patients;
2. encounters, including facility and provider mappings;
3. conditions;
4. allergies;
5. medications;
6. supported vital signs.

Re-running the same dataset safely skips records already tracked as imported.
Local credentials, dataset/target binding, and resumable maps stay under
`.local/` and must not be committed.

## Dataset selection

Normally, `scripts/import_openemr.py` reads `output/current-dataset.json`.
An explicit existing dataset can be selected with:

```bash
python3 scripts/import_openemr.py \
  --csv-dir output/gta-100-v2/csv
```

The old `gta-100-v2` name is validation history only; it is no longer the
default. If multiple datasets exist and no current manifest selects one, the
orchestrator stops instead of guessing.

Existing development maps created before dataset/target binding can be adopted
once, after verifying that they belong to the selected CSVs and OpenEMR target:

```bash
python3 scripts/import_openemr.py \
  --csv-dir output/gta-100-v2/csv \
  --adopt-existing-state
```

## Targeted development runs

One supported resource can be selected explicitly:

```bash
python3 scripts/import_openemr.py \
  --resource vitals \
  --commit \
  --quiet
```

Resource-specific importers remain available under `scripts/` for pilots,
filters, and targeted validation. The patient-only importer is
`scripts/import_openemr_patients.py`; `scripts/import_openemr.py` is the
orchestrator.

## Unsupported or deferred resources

The installed OpenEMR API does not provide a suitable writable endpoint for the
complete Synthea procedure, immunization, care-plan, device, imaging-study, or
supply resources. A narrow surgery endpoint exists, but a curated surgery
subset is deferred and is not treated as complete procedure coverage. Financial
and insurance CSVs are outside the current clinical import scope.

The project does not redirect unsupported records into unrelated OpenEMR
features merely to claim import coverage.

## Documentation

- `docs/STUDENT_SETUP.md`
- `docs/PROJECT_STATE.md`
- `docs/OPENEMR_API_NOTES.md`
- `docs/IMPORT_QUICK_REFERENCE.md`
- `docs/PROJECT_HISTORY.md`
- `docs/SYNTHEA_GTA_BUILD.md`
- `docs/openemr-vitals-api-compatibility.md`

## Important data warning

All generated and imported records are synthetic. They must never be
represented as real patient information.

## Repository hygiene

The following stay outside normal Git history:

- generated datasets under `output/`;
- local credentials and import state under `.local/`;
- Python virtual environments and cache directories;
- built/downloaded JAR files, which are distributed through GitHub Releases.
