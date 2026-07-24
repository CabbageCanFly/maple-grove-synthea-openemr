# Maple Grove Synthea and OpenEMR

This repository supports the Maple Grove student project. It generates synthetic
Greater Toronto Area patient data with Synthea and imports the clinical resources
that are writable through the supported OpenEMR API workflow.

## Start here

### Students

Follow `docs/STUDENT_SETUP.md` from the repository root. The normal workflow is:

1. Download or clone this repository.
2. Install the prerequisites described in the setup guide.
3. Download the versioned GTA Synthea JAR from GitHub Releases into `tools/`.
4. Generate a CSV dataset under `output/`.
5. Configure the OpenEMR API client.
6. Preflight and run the supported import workflow.

Preflight the complete supported dataset without creating records:

```bash
python3 scripts/import_openemr.py
```

Run the complete supported import:

```bash
python3 scripts/import_openemr.py \
  --commit \
  --quiet \
  --progress-every 100
```

The importers keep local resumable mapping state under `.local/`. Re-running the
same workflow safely skips records already tracked as imported.

To run one supported resource, repeat `--resource` as needed:

```bash
python3 scripts/import_openemr.py \
  --resource vitals \
  --commit \
  --quiet
```

## Supported OpenEMR import coverage

The orchestrated workflow runs these resources in dependency order:

1. patients;
2. encounters, with facility and provider mappings;
3. conditions;
4. allergies;
5. medications;
6. supported vital signs.

The current local validation target is OpenEMR 8.0.0.3. Shared AWS OpenEMR 7
validation remains a separate compatibility step and must not assume Docker or
server-file access.

## Audited but unsupported or deferred resources

The installed OpenEMR API does not provide a suitable writable endpoint for the
complete Synthea resources below:

- generic procedures;
- immunizations;
- care plans;
- devices;
- imaging studies;
- supplies.

A narrow patient-surgery endpoint exists, but a curated surgery subset is deferred
and is not treated as a substitute for the complete procedure dataset. Claims,
payer, insurance, and patient-expense CSVs are also outside the current clinical
import scope.

The project does not redirect unsupported records into unrelated OpenEMR features
merely to claim import coverage.

## Project maintainers

See:

- `docs/PROJECT_STATE.md`
- `docs/OPENEMR_API_NOTES.md`
- `docs/IMPORT_QUICK_REFERENCE.md`
- `docs/PROJECT_HISTORY.md`
- `docs/SYNTHEA_GTA_BUILD.md`
- `docs/openemr-vitals-api-compatibility.md`

Resource-specific importers remain available in `scripts/` for development,
pilots, filters, and targeted reruns. The patient-only importer is
`scripts/import_openemr_patients.py`; `scripts/import_openemr.py` is the top-level
orchestrator.

## Important data warning

All patient records produced by this project are synthetic. They must not be
represented as real patient information.

## Repository hygiene

The following stay outside normal Git history:

- generated CSV datasets under `output/`;
- local credentials and import maps under `.local/`;
- Python `__pycache__` directories;
- the built Synthea JAR, which is distributed through GitHub Releases.
