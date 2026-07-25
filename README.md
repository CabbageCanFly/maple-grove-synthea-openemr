# Maple Grove Synthea and OpenEMR

Generate **synthetic Greater Toronto Area patient records** with Synthea, then
import the supported records into either:

- a local OpenEMR 8 installation running in Docker; or
- a shared/remote OpenEMR 7 server, such as the class AWS server.

> **Synthetic data only:** Never represent generated or imported records as real
> patient information.

## Student walkthrough

Follow the numbered steps in order. Run every command from the project folder
(the folder that contains this `README.md` and the `scripts/` directory).

### 1. Choose your OpenEMR setup

Before starting, know which setup you are using:

#### Option A — Local Docker

Use this when OpenEMR is running on your own computer through Docker Desktop.
You will need:

- Docker Desktop;
- permission to configure your local OpenEMR installation; and
- a local OpenEMR username and password.

#### Option B — Shared AWS server

Use this when an instructor gives you an OpenEMR website address and an assigned
username/password.

You **do not** need Docker, AWS Console, SSH, EC2 access, database access, or an
OpenEMR version number for this option.

### 2. Open a terminal in the folder where you want the project

#### Windows using WSL

In File Explorer, open the folder where you want to store the project. Click the
address bar, type:

```text
wsl
```

Press **Enter**. A WSL terminal should open in that folder.

#### macOS

Open Terminal. Type `cd ` with a space after it, drag the desired folder into
the Terminal window, then press **Enter**.

### 3. Download the project

Run:

```bash
git clone https://github.com/CabbageCanFly/maple-grove-synthea-openemr.git
cd maple-grove-synthea-openemr
```

Confirm that you are in the correct folder:

```bash
test -f README.md && test -d scripts && echo "Project folder detected."
```

Expected result:

```text
Project folder detected.
```

### 4. Install/check Python and Java

This project requires:

- Python 3.10 or newer;
- Java 17; and
- Docker Desktop only when using local OpenEMR.

Check Python and Java:

```bash
python3 --version
java -version
```

#### Windows/WSL installation

If Python or Java is missing:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip openjdk-17-jdk
```

#### macOS installation with Homebrew

```bash
brew install python
brew install --cask temurin@17
```

Run the version checks again. Java should report version 17.

### 5. Download the GTA Synthea JAR

The large Synthea JAR is distributed through GitHub Releases rather than stored
inside the Git repository.

Run:

```bash
mkdir -p dist

curl -L \
  https://github.com/CabbageCanFly/maple-grove-synthea-openemr/releases/download/v0.1.1/synthea-gta-maple-grove-v0.1.1.jar \
  -o dist/synthea-gta-maple-grove-v0.1.1.jar
```

Confirm that it downloaded:

```bash
ls -lh dist/synthea-gta-maple-grove-v0.1.1.jar
```

Do not commit the JAR to Git.

### 6. Prepare Python

Create a project-only Python environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements_openemr_import.txt
```

Your terminal should now show `(.venv)` near the prompt.

Whenever you open a new terminal later, return to the project folder and run:

```bash
source .venv/bin/activate
```

### 7. Generate a small test dataset

Start with a small dataset so mistakes are quick to correct:

```bash
python3 scripts/generate_gta_patients.py \
  --population 30 \
  --min-allergies 10
```

The generated CSV files are saved in a new directory below:

```text
output/runs/
```

The newest generated dataset is selected automatically in:

```text
output/current-dataset.json
```

You normally do not need to open or edit that file.

### 8. Select the OpenEMR target

Run:

```bash
python3 scripts/configure_openemr_target.py
```

You will see:

```text
Where is OpenEMR running?
  1. On this computer using Docker
  2. On another server, such as AWS
```

#### For local Docker

Choose:

```text
1
```

The project will detect the local OpenEMR container automatically.

#### For AWS/shared OpenEMR

Choose:

```text
2
```

When asked for the OpenEMR address, paste the main address you use in your
browser, for example:

```text
https://example-openemr.school.ca
```

Enter only the main address. Do not add `/interface`, `/apis`, `/oauth2`, or any
other path.

The script automatically uses the normal OpenEMR site named `default` and
automatically detects OpenEMR 7 versus OpenEMR 8. You do not need to know either
of those details.

A temporary test server opened by raw IP may have an untrusted certificate. Only
accept that warning when an instructor or project maintainer confirms that the
server is a known test instance.

### 9A. Prepare local Docker OpenEMR

Skip this section when using the shared AWS server.

Start Docker Desktop and your OpenEMR containers, then run:

```bash
python3 scripts/ensure_local_https.py
```

The normal project address is:

```text
https://localhost:9300
```

Your browser may warn about the local development certificate. That is expected
for this local synthetic-data environment.

In OpenEMR, open:

```text
Administration -> Config -> Connectors
```

Enable:

```text
Standard REST API
OAuth2 Password Grant
```

Set the local OAuth site address to:

```text
https://localhost:9300
```

#### Prepare at least one provider

In OpenEMR, open:

```text
Administration -> Users
```

Create or edit at least one **non-admin provider** and make sure that:

- the account is Active;
- the account is Authorized;
- the provider is assigned to a facility; and
- the NPI field contains a unique synthetic 10-digit placeholder.

Examples:

```text
0000000001
0000000002
0000000003
```

These are fictional compatibility placeholders. Never use a real provider's
NPI. OpenEMR may hide a manually created provider from the Practitioner API until
its NPI field is filled in.

### 9B. Prepare shared AWS OpenEMR

Skip this section when using local Docker.

Your instructor or OpenEMR administrator should already have prepared:

- the Standard REST API;
- OAuth2 Password Grant;
- at least one facility;
- active, authorized provider accounts with synthetic NPIs; and
- your assigned OpenEMR username and password.

Students do not need to edit the AWS server, use Docker, or enter provider data
unless specifically instructed.

### 10. Register the importer

Run:

```bash
python3 scripts/register_openemr_client.py
```

The output includes a timestamped client name similar to:

```text
Maple Grove Synthea Importer 2026-07-24 21:15 EDT
```

In the **same OpenEMR installation selected in Step 8**, open:

```text
Administration -> System -> API Clients
```

Find the newest matching timestamped client, enable it, and save.

Do not continue until that newest client is enabled. A `401 invalid_client`
message usually means the newly registered client has not been enabled yet.

### 11. Log in for this terminal session

Run:

```bash
source scripts/openemr_session.sh
```

The word `source` is required. It allows later commands in this terminal to use
the login without repeatedly asking for the password.

#### Local Docker login

The script offers the usual local demo login:

```text
admin / pass
```

Choose the custom-login option instead if you changed those credentials.

#### AWS/shared-server login

Enter the username and password assigned to you. The password remains invisible
while you type; that is normal.

The password is not saved in the project folder. It stays only in the current
terminal session and disappears when the terminal closes or when you run:

```bash
openemr_logout
```

Only the non-secret username is remembered locally.

If you typed the wrong password, simply run this again to replace the terminal
login:

```bash
source scripts/openemr_session.sh
```

### 12. Test the connection

Run:

```bash
python3 scripts/test_openemr_connection.py
```

Expected ending:

```text
OpenEMR connection test passed.
```

Do not continue until the test passes.

Common causes of failure:

- `invalid_client`: enable the newest timestamped API client in OpenEMR;
- `invalid_grant` or login failure: rerun `source scripts/openemr_session.sh`
  and carefully re-enter the username/password;
- wrong server: rerun `python3 scripts/configure_openemr_target.py`, then
  register and enable a client on that server;
- no eligible provider pool: check that a non-admin provider is Active,
  Authorized, assigned to a facility, and has a unique synthetic 10-digit NPI.

### 13. Preview the import safely

Run the preflight first:

```bash
python3 scripts/import_openemr.py
```

This checks the selected dataset and prints the planned import steps. It does
**not** create OpenEMR records.

### 14. Import the dataset

Run:

```bash
python3 scripts/import_openemr.py \
  --commit \
  --quiet \
  --progress-every 100
```

The normal workflow imports, in dependency order:

1. patients;
2. encounters and provider/facility mappings;
3. curated conditions;
4. curated allergies;
5. medications; and
6. supported vital signs.

Keep the terminal open until the command finishes.

### 15. Confirm duplicate protection

Run the exact same import command again:

```bash
python3 scripts/import_openemr.py \
  --commit \
  --quiet \
  --progress-every 100
```

Previously imported records should be skipped rather than created again.

### 16. Inspect the imported records

Open several patient charts in OpenEMR and check:

- demographics;
- encounters;
- medical problems;
- allergies;
- medications; and
- vital signs.

Missing OpenEMR allergy-reaction options may be stored as `unassigned`.

## Returning to the project later

For the same dataset and same OpenEMR server, the normal returning workflow is:

```bash
cd maple-grove-synthea-openemr
source .venv/bin/activate
source scripts/openemr_session.sh
python3 scripts/test_openemr_connection.py
```

Then run whichever generation or import command you need.

## Switching servers or starting over

### Re-enter only the password

This is safe and does not reset import tracking:

```bash
source scripts/openemr_session.sh
```

### Completely reset this repo's saved local state

Only do this before a clean test or when an instructor/maintainer tells you to.
It forgets the selected server, OAuth client, remembered username, and all local
import maps:

```bash
openemr_logout 2>/dev/null || true; rm -rf .local
python3 scripts/configure_openemr_target.py
```

This does **not** delete records already inside OpenEMR. Deleting the local maps
and then re-importing into the same populated server may weaken duplicate
protection. Use a fresh/reset OpenEMR instance for a true clean test.

## Common commands

Generate a larger dataset:

```bash
python3 scripts/generate_gta_patients.py --population 100
```

List supported and intentionally unsupported resources:

```bash
python3 scripts/import_openemr.py --list-resources
```

Import only one resource:

```bash
python3 scripts/import_openemr.py \
  --resource vitals \
  --commit \
  --quiet
```

Inspect facilities and providers visible through the API:

```bash
python3 scripts/inspect_openemr_resources.py
```

View all importer options:

```bash
python3 scripts/import_openemr.py --help
```

## Local files and privacy

The project stores private working state under `.local/`, including:

- the selected OpenEMR target;
- OAuth client registration details;
- the remembered username; and
- resumable import maps.

The OpenEMR user password is not written there. It stays in the terminal session
only.

Never commit:

- `.local/`;
- generated datasets under `output/`;
- `.venv/`;
- downloaded JARs under `dist/`;
- `.env` files;
- passwords, access tokens, client secrets, or private certificates.

## Supported and deferred records

The supported workflow imports patients, encounters, curated conditions,
curated allergies, medications, and selected vital signs.

The installed OpenEMR APIs do not provide a suitable writable destination for
complete Synthea procedure, immunization, care-plan, device, imaging-study, or
supply resources. Financial and insurance CSV files are also outside the current
clinical import scope. Unsupported records are not forced into unrelated
OpenEMR features merely to claim import coverage.

## Additional documentation

- [`docs/STUDENT_SETUP.md`](docs/STUDENT_SETUP.md) — extra help installing tools and opening the project
- [`docs/IMPORT_QUICK_REFERENCE.md`](docs/IMPORT_QUICK_REFERENCE.md) — compact command reference after setup
- [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md) — current implementation and validation state
- [`docs/OPENEMR_API_NOTES.md`](docs/OPENEMR_API_NOTES.md) — API findings and limitations
- [`docs/PROJECT_HISTORY.md`](docs/PROJECT_HISTORY.md) — project background and decisions
- [`docs/SYNTHEA_GTA_BUILD.md`](docs/SYNTHEA_GTA_BUILD.md) — GTA Synthea build details
- [`docs/openemr-vitals-api-compatibility.md`](docs/openemr-vitals-api-compatibility.md) — exact local OpenEMR 8 vitals compatibility notes
