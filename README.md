# Maple Grove Synthea and OpenEMR

This repository supports the Maple Grove student project. It contains tools
for generating synthetic Greater Toronto Area patient data and importing
selected records into OpenEMR.

## Start here

### Students

A short student setup guide will be added here before distribution.

Students will normally:

1. Download this repository.
2. Download the prebuilt GTA Synthea JAR from the GitHub Releases page.
3. Install Java 17.
4. Generate the CSV dataset.
5. Run the OpenEMR import scripts.

### Project maintainers

See:

- `docs/PROJECT_HISTORY.md`
- `docs/SYNTHEA_GTA_BUILD.md`

## Current status

Completed:

- Canadian Synthea configuration;
- GTA municipality data;
- diverse complete Canadian postal codes;
- custom GTA Synthea JAR;
- reproducible CSV generation.

In progress:

- CSV validation and transformation;
- OpenEMR Standard REST API importer;
- beginner student setup instructions.

## Important data warning

All patient records produced by this project are synthetic. They must not be
represented as real patient information.

## Large files

The built JAR and generated CSV datasets are not stored in normal Git history.

- The built JAR will be distributed through GitHub Releases.
- Generated CSV files remain in the local `output` folder.
