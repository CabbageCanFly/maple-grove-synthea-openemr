# Maple Grove Synthea–OpenEMR Project State

Last updated: 2026-07-23

## Goal

Provide students with a low-friction workflow:

1. clone the repository;
2. run one setup command;
3. generate GTA synthetic patients;
4. import them into OpenEMR;
5. view the patients in OpenEMR.

Students should not rebuild Synthea, find Docker container names, edit
credential files, register OAuth clients manually, or assemble Java commands.

## Supported OpenEMR environments

### Local student environment

- OpenEMR 8 in Docker Desktop
- Default URL: https://localhost:9300
- Default site: default
- Local synthetic/demo credentials: admin / pass
- No real patient information
- Docker container names and ports must be detected automatically

### Shared class environment

- OpenEMR 7 on AWS
- URL and site will be stored as non-secret target configuration
- Do not store AWS usernames or passwords in Git
- AWS credentials must come from environment variables or a secure prompt
- Docker access may not be available to students
- Importer must support both OpenEMR 7 and OpenEMR 8

## Synthea status

- GTA Synthea release: v0.1.1
- JAR: synthea-gta-maple-grove-v0.1.1.jar
- JAR is distributed through GitHub Releases, not committed to Git
- GTA configuration: config/synthea-gta.properties
- Verified with Toronto, Ontario patients and Canadian postal codes
- Numeric suffixes on generated names are disabled

## Repository decisions

- GitHub repository is public
- GitHub is the source of truth for code
- Generated CSV files are not committed
- JAR files are not committed
- Local Docker/build folders are ignored
- Real credentials are never committed

## Current development milestone

The importer scaffold exists:

- requirements_openemr_import.txt
- config/openemr.example.env

No completed OpenEMR importer exists yet.

Do not require students to edit openemr.env manually. The setup/import
scripts should create or obtain configuration automatically.

## Compatibility strategy

The scripts will:

1. detect local Docker containers, image versions, and published ports;
2. detect whether OpenEMR is version 7 or 8;
3. use version-appropriate OAuth scopes;
4. use the same Standard REST API importer logic where compatible;
5. support local and AWS target profiles;
6. begin with patient demographics before encounters and clinical data.

## Planned student commands

python3 scripts/setup_project.py
python3 scripts/generate_gta_patients.py
python3 scripts/import_openemr.py

## Latest completed progress

- Local Docker container names and ports are detected automatically.
- The actual OpenEMR version is read from version.php, so `latest` images work.
- Local HTTPS is available at https://localhost:9300.
- The Standard REST API and local test-only password grant were enabled manually.
- FHIR, FHIR system scopes, and Patient Portal APIs remain disabled.
- Direct SQL configuration was rejected as the normal student workflow.

## Next development step

Register a local OAuth client, obtain an access token, and perform an
authenticated read-only patient request. Do not import patients until
authentication works.

## OpenEMR discovery rules

- Never assume the Docker image tag contains the OpenEMR version.
- Images tagged `latest` must be supported.
- Docker image tags are hints only.
- For local Docker, read the actual version from OpenEMR's version.php.
- Automatically detect container names and published ports.
- Prefer published HTTPS port 443 over HTTP port 80.
- Recognize MariaDB, MySQL, and mysql-xtrabackup database containers.
- Local students use automatically detected OpenEMR 8 Docker environments.
- The shared AWS target uses OpenEMR 7 and does not require student Docker access.

## OAuth and API status

- Standard REST API is enabled manually in Administration → Config → Connectors.
- OAuth2 Password Grant is enabled for the local synthetic/demo environment.
- Local OAuth site address is https://localhost:9300.
- OAuth client registration is automated.
- After registration, the student must open Administration → System → API Clients
  and enable the Maple Grove Synthea Importer once.
- Authentication and the read-only patient API request have been verified.
- Access tokens are not printed or saved.

## Patient importer status

- OAuth authentication was successfully verified against local OpenEMR 8.
- A Synthea patient was created through the Standard REST API.
- The patient count increased from 3 to 4.
- Re-importing the same Synthea patient safely skipped it.
- Current importer supports basic patient demographics, dry runs, limits,
  local import tracking, and duplicate protection.
- Bulk import has not yet been approved or tested.
