# Maple Grove Synthea and OpenEMR

Generate synthetic Greater Toronto Area patient data with Synthea, then import
supported clinical records into OpenEMR 8.

> **Synthetic data only:** Never represent generated or imported records as real
> patient information.

## Student quick start

Run every command below from the repository root.

### 1. Get the project and check prerequisites

Clone the repository or download it through GitHub Desktop:

```bash
git clone https://github.com/CabbageCanFly/maple-grove-synthea-openemr.git
cd maple-grove-synthea-openemr
```

Required:

- Python 3.10 or newer
- Java 17
- Docker Desktop with a local OpenEMR 8 environment

Check Python and Java:

```bash
python3 --version
java -version
```

For Windows, run the project through WSL. Install missing prerequisites with:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip openjdk-17-jdk
```

For macOS with Homebrew:

```bash
brew install python
brew install --cask temurin@17
```

Java should report version 17.

### 2. Download the GTA Synthea JAR

```bash
mkdir -p dist

curl -L \
  https://github.com/CabbageCanFly/maple-grove-synthea-openemr/releases/download/v0.1.1/synthea-gta-maple-grove-v0.1.1.jar \
  -o dist/synthea-gta-maple-grove-v0.1.1.jar
```

Verify the file exists:

```bash
ls -lh dist/synthea-gta-maple-grove-v0.1.1.jar
```

### 3. Prepare Python

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements_openemr_import.txt
```

When returning to the project later, reactivate the environment with:

```bash
source .venv/bin/activate
```

### 4. Generate a small test dataset

```bash
python3 scripts/generate_gta_patients.py --population 5
```

Each generation is stored in a unique directory under `output/runs/`. The
selected dataset is recorded in `output/current-dataset.json` automatically.

### 5. Prepare OpenEMR

Start Docker Desktop and the local OpenEMR 8 containers.

In OpenEMR, open:

```text
Administration -> Config -> Connectors
```

Enable:

```text
Standard REST API
OAuth2 Password Grant
```

For the standard local project setup, set the OAuth site address to:

```text
https://localhost:9300
```

Create or reuse one facility. Also create at least one active, authorized
provider assigned to that facility. Use a clearly synthetic unique NPI, such as:

```text
0000000001
```

### 6. Detect OpenEMR and register the importer

```bash
python3 scripts/detect_openemr.py
python3 scripts/ensure_local_https.py
python3 scripts/register_openemr_client.py
```

In OpenEMR, open:

```text
Administration -> System -> API Clients
```

Enable the newest **Maple Grove Synthea Importer** client.

Test the connection:

```bash
python3 scripts/test_openemr_connection.py
```

Do not continue until the connection test succeeds.

### 7. Preflight and import

First run the safe preflight. It creates no OpenEMR records:

```bash
python3 scripts/import_openemr.py
```

Then import the selected dataset:

```bash
python3 scripts/import_openemr.py \
  --commit \
  --quiet \
  --progress-every 100
```

Run the same import command again to test duplicate protection. Previously
tracked records should be skipped rather than duplicated.

### 8. Check the imported records

Inspect several OpenEMR patients for:

- demographics
- encounters
- medical problems
- allergies
- medications
- vital signs

Missing OpenEMR allergy reaction options automatically fall back to
`unassigned`.

## Supported import workflow

The normal import runs these resources in dependency order:

1. patients
2. encounters, including facility and provider mappings
3. curated conditions
4. curated allergies
5. medications
6. supported vital signs

Re-running the same dataset safely skips records already tracked as imported.
Local credentials, dataset and target binding, and resumable maps stay under
`.local/` and must not be committed.

## Common commands

Generate a larger dataset:

```bash
python3 scripts/generate_gta_patients.py --population 100
```

Import only one supported resource:

```bash
python3 scripts/import_openemr.py \
  --resource vitals \
  --commit \
  --quiet
```

List supported and intentionally unsupported resources:

```bash
python3 scripts/import_openemr.py --list-resources
```

View all importer options:

```bash
python3 scripts/import_openemr.py --help
```

## Dataset selection and local state

Normally, `scripts/import_openemr.py` uses the dataset selected by
`output/current-dataset.json`.

To select an existing dataset explicitly:

```bash
python3 scripts/import_openemr.py \
  --csv-dir output/gta-100-v2/csv
```

If multiple datasets exist and no current manifest selects one, the importer
stops instead of guessing.

Development maps created before dataset and target binding can be adopted once,
after verifying they belong to the selected CSV files and OpenEMR target:

```bash
python3 scripts/import_openemr.py \
  --csv-dir output/gta-100-v2/csv \
  --adopt-existing-state
```

Do not reuse `.local/` import maps with a different generated dataset or a
different OpenEMR installation.

## Unsupported or deferred resources

The installed OpenEMR API does not provide a suitable writable endpoint for
complete Synthea procedure, immunization, care-plan, device, imaging-study, or
supply resources. A narrow surgery endpoint exists, but a curated surgery
subset is deferred and is not treated as complete procedure coverage.
Financial and insurance CSV files are outside the current clinical import
scope.

Unsupported records are not redirected into unrelated OpenEMR features merely
to claim import coverage.

## More documentation

- [`docs/STUDENT_SETUP.md`](docs/STUDENT_SETUP.md) - detailed setup help
- [`docs/IMPORT_QUICK_REFERENCE.md`](docs/IMPORT_QUICK_REFERENCE.md) - importer command reference
- [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md) - current implementation state
- [`docs/OPENEMR_API_NOTES.md`](docs/OPENEMR_API_NOTES.md) - OpenEMR API findings
- [`docs/PROJECT_HISTORY.md`](docs/PROJECT_HISTORY.md) - project background and decisions
- [`docs/SYNTHEA_GTA_BUILD.md`](docs/SYNTHEA_GTA_BUILD.md) - GTA Synthea build details
- [`docs/openemr-vitals-api-compatibility.md`](docs/openemr-vitals-api-compatibility.md) - local vitals compatibility notes

## Repository hygiene

Do not commit:

- generated datasets under `output/`
- local credentials and import state under `.local/`
- Python virtual environments and cache directories
- downloaded or built JAR files under `dist/`
- `.env` files, API credentials, access tokens, or private certificates
