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

## Next development step

Build automatic local Docker/OpenEMR discovery, followed by a harmless
API connection test. Do not import patients until authentication works.
