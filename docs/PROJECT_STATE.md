# Maple Grove Synthea–OpenEMR Project State

Last updated: 2026-07-23

## Goal

Provide students with a low-friction, repeatable workflow that:

1. clones the repository;
2. configures the supported OpenEMR target;
3. generates synthetic Greater Toronto Area patient data with Synthea;
4. imports as much clinically useful historical data as OpenEMR can safely support;
5. allows students to inspect the resulting longitudinal records in OpenEMR.

The long-term goal is broader than patient demographics. The importer should process
the supported Synthea CSV files in dependency order and preserve relationships between
patients, encounters, providers, organizations, and clinical records.

Students should not rebuild Synthea, discover Docker container names manually, edit
credential files, register OAuth clients manually, or assemble Java commands.

All imported records are synthetic. Never use real patient information.

## Supported OpenEMR environments

### Local student environment

- OpenEMR 8 in Docker Desktop
- Default URL: https://localhost:9300
- Default site: default
- Local synthetic/demo credentials: admin / pass
- No real patient information
- Docker container names, versions, and ports must be detected automatically

### Shared class environment

- OpenEMR 7 on AWS
- URL and site are non-secret target configuration
- Do not store AWS usernames, passwords, client secrets, or access tokens in Git
- Credentials must come from environment variables or a secure prompt
- Docker access may not be available to students
- Importer must support both OpenEMR 7 and OpenEMR 8 where their APIs overlap

## Synthea status

- GTA Synthea release: v0.1.1
- JAR: synthea-gta-maple-grove-v0.1.1.jar
- JAR is distributed through GitHub Releases, not committed to Git
- GTA configuration: config/synthea-gta.properties
- Verified with Toronto and other GTA municipalities
- Complete Canadian postal codes are generated
- Numeric suffixes on generated names are disabled
- CSV export is the source format for the OpenEMR import workflow

## Repository decisions

- GitHub is the source of truth for code and documentation
- Generated CSV files are not committed
- JAR files are not committed
- Local Docker/build folders are ignored
- Real credentials and tokens are never committed
- Import operations must support dry runs, limits, local tracking, and safe retries
- Synthea source identifiers must be retained in local mapping/tracking data
- Direct SQL is not the normal student workflow
- API-based import is preferred
- Version-specific exceptions must be isolated behind compatibility code

## OpenEMR discovery and authentication status

Completed locally against OpenEMR 8:

- Docker container names and published ports are detected automatically
- The actual OpenEMR version is read from version.php
- Images tagged `latest` are supported
- Local HTTPS is available at https://localhost:9300
- Standard REST API is enabled
- OAuth2 Password Grant is enabled only for the local synthetic/demo environment
- OAuth client registration is automated
- The API client is manually enabled once in Administration → System → API Clients
- Authentication and a read-only patient request were verified
- Access tokens are not printed or saved

The AWS OpenEMR 7 target must use the same importer where the Standard API is
compatible, but it must not depend on Docker access.

## Patient importer status

Completed functionality:

- A Synthea patient can be created through the OpenEMR Standard REST API
- Re-importing the same Synthea patient safely skips it
- Basic patient demographics are mapped
- Dry runs and limits are supported
- Local import tracking and duplicate protection exist

The patient CSV is the only Synthea clinical dataset implemented so far.
The exact most recent bulk-test count should be recorded after the next run.

<!-- BEGIN COMPLETED-MILESTONE-HISTORY -->

## Completed milestone history

1. Published the Maple Grove repository publicly on GitHub.
2. Cleaned and verified the customized GTA and Canadian Synthea resources.
3. Created `config/synthea-gta.properties` for GTA CSV generation.
4. Built and tested `synthea-gta-maple-grove-v0.1.1.jar`.
5. Distributed the JAR through GitHub Releases rather than committing it to Git.
6. Added a beginner-facing student setup guide.
7. Added `PROJECT_STATE.md` as the permanent project and AI handoff.
8. Added the Python importer dependency and configuration scaffold.
9. Removed obsolete Synthea build-recording helpers.
10. Built `detect_openemr.py` to detect Docker containers, published ports, and
    the actual OpenEMR version, including images tagged `latest`.
11. Established support requirements for local OpenEMR 8 and the shared AWS
    OpenEMR 7 environment.
12. Built `ensure_local_https.py` to expose local OpenEMR through
    `https://localhost:9300` without requiring students to edit Docker Compose.
13. Verified local OpenEMR 8.0.0.3 and local HTTPS successfully.
14. Chose a one-time manual OpenEMR API configuration step instead of directly
    changing OpenEMR database settings.
15. Enabled the Standard REST API, local OAuth2 Password Grant, and the local
    site address.
16. Left FHIR, FHIR system scopes, and Patient Portal APIs disabled during the
    initial Standard API implementation.
17. Automated OAuth client registration.
18. Obtained an OAuth access token and verified an authenticated read-only
    patient API request.
19. Imported a Synthea patient through the OpenEMR Standard REST API.
20. Verified duplicate protection by safely skipping the same patient during
    re-import.
21. Implemented basic patient-demographic transformation, dry runs, import
    limits, local tracking, and duplicate protection.
22. Moved into the encounter phase, including organization/facility mapping and
    deterministic mapping of many Synthea providers onto a small manually
    created OpenEMR provider pool.

The earlier milestone of registering an OAuth client, obtaining a token, and
performing an authenticated read-only call is complete and is no longer the
current next step.

<!-- END COMPLETED-MILESTONE-HISTORY -->

## Current development phase: encounters

The next implemented resource is `encounters.csv`.

The uploaded working sample contains:

- 5,384 encounter rows
- 105 referenced patients
- 176 referenced Synthea organizations
- 176 referenced Synthea providers
- date range from 1942-11-05 through 2026-07-22
- no duplicate encounter IDs
- no missing patient, organization, provider, payer, class, code, or description fields
- 3,433 rows with a reason code/description
- encounter classes:
  - ambulatory: 2,802
  - wellness: 1,328
  - outpatient: 764
  - emergency: 235
  - urgentcare: 96
  - home: 66
  - inpatient: 61
  - snf: 20
  - virtual: 7
  - hospice: 5

## Encounter dependency strategy

Encounters cannot be imported correctly using only `encounters.csv`.

Required mappings:

1. Synthea patient UUID → OpenEMR patient UUID
2. Synthea organization UUID → OpenEMR facility ID and facility UUID
3. Synthea provider UUID → OpenEMR provider numeric ID and provider UUID
4. Synthea encounter UUID → OpenEMR encounter UUID and legacy encounter ID

The mappings must persist between runs so later CSV files can reference the same
OpenEMR records.

### Organizations

`organizations.csv` should be processed before encounters.

OpenEMR facilities do not require login accounts, so importing Synthea organizations
as facilities is technically practical. Before importing all organizations, test the
effect on the OpenEMR facility dropdown and demo usability.

Supported strategies:

- full fidelity: create every referenced Synthea organization as an OpenEMR facility;
- curated pool: create a smaller set of representative facilities and deterministically
  map the remaining organizations onto them;
- minimal pilot: map every organization to the existing Maple Grove facility.

The preferred starting point is one pilot facility mapping, followed by a small
organization import test. Do not create hundreds of records before validating the UI.

### Providers

Do not create hundreds of OpenEMR login accounts from `providers.csv`.

Create a small manual pool of realistic provider accounts, such as:

- physician
- nurse practitioner
- registered nurse or other appropriate clinician

Then deterministically map every Synthea provider UUID to one account. When
`providers.csv` is available, prefer specialty-aware mapping over random mapping.

For the Nurse Practitioner account:

- provider type: Nurse Practitioner
- NUCC taxonomy: 363L00000X
- SNOMED CT occupation: 224571005

### Encounter fields

Proposed Standard API mapping:

- `PATIENT` → patient UUID lookup
- `START` → `date`, converted from UTC to the target OpenEMR timezone
- `DESCRIPTION` and optional `REASONDESCRIPTION` → `reason`
- `ORGANIZATION` → `facility_id`, `billing_facility`, and facility name
- `PROVIDER` → `provider_id`
- `ENCOUNTERCLASS` → `class_code`
- Synthea encounter `Id` → `external_id`
- configurable OpenEMR visit category → `pc_catid`
- default encounter sensitivity → `normal`

Important limitations:

- Synthea `STOP` has no direct field in the Standard encounter payload
- encounter costs and payer coverage do not belong in the basic encounter record
- the Synthea clinical code is not the same thing as OpenEMR `pc_catid`
- `pc_catid` is an OpenEMR visit/category ID and must be validated on each target
- class codes must be discovered from the target where possible, with safe fallbacks

Initial class-code fallbacks:

- ambulatory, wellness, outpatient, urgentcare → AMB
- emergency → EMER
- inpatient → IMP
- home → HH
- virtual → VR
- snf and hospice → target-supported non-acute/inpatient fallback

Do not assume every fallback exists. Query the target encounter-type list and validate
the pilot response.

## Proposed encounter import sequence

1. Audit all CSV files and cross-file references.
2. Confirm the patient mapping generated by the existing patient importer.
3. Read and validate `organizations.csv`.
4. Create or select the OpenEMR facility mapping strategy.
5. Read `providers.csv`.
6. Fetch the manually created OpenEMR practitioner accounts.
7. Generate and persist deterministic organization and provider maps.
8. Query or configure the OpenEMR encounter category and class codes.
9. Dry-run one encounter and print the proposed payload without credentials.
10. Import one encounter.
11. Verify the encounter manually in the OpenEMR UI.
12. Re-run the same encounter and confirm it is skipped.
13. Test 10 encounters across multiple classes.
14. Import the remaining encounters in resumable batches.
15. Persist Synthea encounter UUID → OpenEMR encounter UUID/ID mappings.

## Wider Synthea CSV import order

Dependency-oriented target order:

1. patients
2. organizations/facilities
3. provider mapping
4. encounters
5. conditions
6. allergies
7. medications
8. selected observations/vitals
9. procedures
10. immunizations
11. care plans
12. devices
13. selected imaging information
14. optional insurance/billing data

Not every CSV maps cleanly to the OpenEMR Standard API.

Current OpenEMR Standard API coverage is suitable for patients, facilities,
encounters, medical problems, allergies, and some medication/list workflows.
Some resources, including generic observations, procedures, immunizations,
care plans, devices, and imaging data, may require FHIR endpoints, a custom
OpenEMR module/API extension, or a deliberately reduced mapping.

Claims, claims transactions, patient expenses, payer transitions, and payer data
must be treated separately. Synthea billing data is not an exact simulation of
Ontario billing or provincial insurance and should not be imported blindly.

## Compatibility strategy

The importer will:

1. detect OpenEMR version and target profile;
2. use the overlapping Standard API for OpenEMR 7 and 8;
3. query `/api/version` where available;
4. inspect target capabilities before resource imports;
5. use version-appropriate OAuth scopes;
6. validate required encounter fields with a one-record pilot;
7. keep resource transformations independent from HTTP/API transport;
8. store mapping files in a generated, ignored state directory;
9. continue safely after interruption;
10. record unsupported fields instead of silently pretending they were imported.

<!-- BEGIN STUDENT-USABILITY-AND-PATH-RULES -->

## Student usability and path rules

The intended users include students with limited experience using terminals, Git,
Python, Docker, WSL, Linux, or macOS command-line tools. Student-facing instructions
must minimize assumptions and avoid requiring students to understand operating-system
path translation.

### Repository-root convention

All documented project commands assume the terminal is already open at the root of
the cloned repository.

The repository may be cloned anywhere. Never hard-code a location such as:

- `H:\Desktop\maple-grove-synthea`
- `C:\Users\<name>\Desktop\...`
- `/mnt/c/Users/<name>/...`
- `/mnt/h/Desktop/...`
- `/Users/<name>/Desktop/...`
- `~/Desktop/...`
- a Downloads folder

After entering the repository root, commands should use relative paths:

```text
scripts/...
docs/...
config/...
output/...
tools/...
```

Typical commands should therefore look like:

```bash
python3 scripts/import_openemr.py
python3 scripts/audit_synthea_csvs.py --csv-dir output/gta-100-v2/csv
```

Scripts should resolve repository resources relative to the repository root or the
script location. They must not depend on the repository having a particular absolute
path.

### Opening the repository on Windows with WSL

The preferred beginner workflow is:

1. Open the cloned project folder in Windows File Explorer.
2. Click the File Explorer address bar.
3. Type `wsl`.
4. Press Enter.

This opens WSL in the selected project folder. Students should not need to manually
translate a Windows path such as `H:\...` into a WSL path such as `/mnt/h/...`.

### Opening the repository on macOS

The preferred beginner workflow is:

1. Open Terminal.
2. Type `cd` followed by one space.
3. Drag the cloned project folder from Finder into Terminal.
4. Press Enter.

Dragging the folder lets Terminal insert and escape the path correctly.

### Verifying the repository root

Student instructions may use:

```bash
pwd
test -f README.md && test -d scripts && echo "Project folder detected."
```

If the confirmation is not displayed, the student is probably in the wrong folder
and should not continue with setup or import commands.

### Cross-platform command style

After students enter the repository root:

- prefer one common Linux/macOS-style command sequence;
- use `python3`, not `python`;
- use forward-slash relative paths;
- avoid unnecessary `cd` commands;
- do not require students to understand WSL mount points;
- explain one action at a time and state the expected result;
- isolate Windows and macOS differences primarily to opening the terminal and
  installing prerequisites.

### Documentation-update convention

For small generated documentation changes, prefer an idempotent command that can be
pasted and run from the repository root, commonly using a Python heredoc:

```text
python3 - <<'PY'
# Read, update, and rewrite a repository file.
PY
```

Avoid requiring students or maintainers to download a replacement text file and copy
it from an operating-system-specific Downloads path.

Update commands should:

- verify that they are running from the repository root;
- fail clearly rather than modifying the wrong file;
- be safe to run more than once;
- use repository-relative paths;
- print which file was updated.

For unusually large replacements or when chat formatting makes a heredoc unreliable,
providing a complete downloadable file is acceptable.

<!-- END STUDENT-USABILITY-AND-PATH-RULES -->

## Planned student commands

```bash
python3 scripts/setup_project.py
python3 scripts/generate_gta_patients.py
python3 scripts/audit_synthea_csvs.py --csv-dir output/gta-100-v2/csv
python3 scripts/import_openemr.py --resource patients
python3 scripts/import_openemr.py --resource organizations
python3 scripts/import_openemr.py --resource encounters --dry-run --limit 1
python3 scripts/import_openemr.py --resource encounters --limit 10
```

The exact final command interface may change as the importer is refactored.

## Files required for the next exact coding step

To continue implementation without guessing, preserve or provide:

- `scripts/import_openemr.py`
- all files under `scripts/openemr_import/`
- the current patient import tracking/mapping file format
- `organizations.csv`
- `providers.csv`
- one successful patient-import console transcript with secrets removed
- OpenEMR 7 exact version from the AWS instance
- OpenEMR 8 exact local version
- the IDs/names of the manually created OpenEMR providers
- the selected local facility ID/name

## Immediate next development step

Integrate encounter support into the existing importer, not as a disconnected second
authentication implementation.

First inspect the current importer code and its tracking model. Then add:

- organization/facility mapping;
- provider-pool mapping;
- encounter transformation;
- encounter API client method;
- one-record dry run;
- one-record live pilot;
- idempotent encounter tracking.
