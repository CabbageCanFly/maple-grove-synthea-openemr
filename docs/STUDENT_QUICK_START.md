# Student Quick Start — macOS and OpenEMR 8

All commands below run from the repository root.

## 1. Clone the repository

```bash
git clone https://github.com/CabbageCanFly/maple-grove-synthea-openemr.git
cd maple-grove-synthea-openemr
```

## 2. Download the Synthea JAR

Run this from the repository root:

```bash
mkdir -p dist

curl -L \
  https://github.com/CabbageCanFly/maple-grove-synthea-openemr/releases/download/v0.1.1/synthea-gta-maple-grove-v0.1.1.jar \
  -o dist/synthea-gta-maple-grove-v0.1.1.jar
```

Verify:

```bash
ls -lh dist/synthea-gta-maple-grove-v0.1.1.jar
```

## 3. Create the Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements_openemr_import.txt
```

## 4. Generate a small test dataset

```bash
python3 scripts/generate_gta_patients.py --population 5
```

## 5. Start OpenEMR 8

Start Docker Desktop and the local OpenEMR 8 containers.

## 6. Detect and prepare OpenEMR

```bash
python3 scripts/detect_openemr.py
python3 scripts/ensure_local_https.py
```

## 7. Enable the OpenEMR API

In OpenEMR, open:

```text
Administration → Config → Connectors
```

Enable:

```text
Standard REST API
OAuth2 Password Grant
```

Set the OAuth site address to:

```text
https://localhost:9300
```

## 8. Prepare one facility and provider

Create or reuse one facility.

Create at least one active, authorized provider assigned to that facility. Use a clearly synthetic unique NPI such as:

```text
0000000001
```

## 9. Register the API client

```bash
python3 scripts/register_openemr_client.py
```

In OpenEMR, open:

```text
Administration → System → API Clients
```

Enable the newest **Maple Grove Synthea Importer** client.

## 10. Test the connection

```bash
python3 scripts/test_openemr_connection.py
```

Do not continue until this succeeds.

## 11. Run import preflight

```bash
python3 scripts/import_openemr.py
```

This creates no clinical records.

## 12. Import the dataset

```bash
python3 scripts/import_openemr.py   --commit   --quiet   --progress-every 100
```

## 13. Test duplicate protection

Run the same import command again. Existing records should be skipped instead of duplicated.

## 14. Inspect OpenEMR

Check several patients for:

- demographics
- encounters
- medical problems
- allergies
- medications
- vital signs

Missing OpenEMR allergy reaction options automatically fall back to `unassigned`.
