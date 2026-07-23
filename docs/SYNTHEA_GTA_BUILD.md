# Maple Grove GTA Synthea build record

This file records the local build that successfully generated synthetic
Greater Toronto Area patient data.

## Recorded

`2026-07-23T08:30:05-04:00`

## Upstream source revisions

- Synthea commit: `7e08387c68a7f0e21d13076609a159fd473fc902`
- Synthea International commit: `4d406f4d3b06adfb12d57c365651e41eb11d1302`

## Local changes applied to the Synthea source

These are expected because the Canada configuration and GTA-specific files
were copied or created inside the upstream Synthea source folder.

**Existing files modified locally:**
- `README.md`
- `src/main/resources/payers/insurance_eligibilities.csv`
- `src/main/resources/payers/insurance_plans.csv`
- `src/main/resources/synthea.properties`
- `src/test/resources/test.properties`

**New files added locally:**
- `src/main/resources/geography/demographics_ca.csv`
- `src/main/resources/geography/demographics_gta.csv`
- `src/main/resources/geography/timezones_ca.csv`
- `src/main/resources/geography/zipcodes_ca.csv`
- `src/main/resources/geography/zipcodes_gta.csv`
- `src/main/resources/payers/insurance_companies_ca.csv`
- `src/main/resources/providers/hospitals_ca.csv`
- `src/main/resources/providers/longterm_ca.csv`
- `src/main/resources/providers/nursing_ca.csv`
- `src/main/resources/providers/primary_care_facilities_ca.csv`
- `src/main/resources/providers/urgent_care_facilities_ca.csv`
- `src/main/resources/providers/va_facilities_ca.csv`

## Local changes in Synthea International

No local changes.

## Java

```text
openjdk version "17.0.19" 2026-04-21
OpenJDK Runtime Environment (build 17.0.19+10-1-24.04.2-Ubuntu)
OpenJDK 64-Bit Server VM (build 17.0.19+10-1-24.04.2-Ubuntu, mixed mode, sharing)
```

## GTA configuration

Cities included:

- Ajax
- Brampton
- Burlington
- Markham
- Milton
- Mississauga
- Oakville
- Oshawa
- Pickering
- Richmond Hill
- Toronto
- Vaughan
- Whitby

- Demographic rows: 14
- Postal-code rows: 1300
- Person-name number suffixes are disabled during generation.
- CSV export is used for the OpenEMR import workflow.

## Input data hashes

- GeoNames archive: `CA_full.csv.zip`
- GeoNames SHA-256: `e908a41b9e646e248c75b778bbe506486f68170f876f2ad8d8bd772d72426feb`

## Built JAR

- File: `synthea-gta-maple-grove.jar`
- SHA-256: `8750f500d12e9e91c441365123ed4714607ce4f7239cf1b4b6f6c92c6d1cc2f8`

## Known limitations

- All generated patient records are synthetic.
- Postal codes are selected from the patient's municipality.
- A generated street address may not correspond to its exact postal code.
- Synthea's clinical modules are not an exact simulation of Ontario clinical,
  billing, or provincial health-insurance practices.
