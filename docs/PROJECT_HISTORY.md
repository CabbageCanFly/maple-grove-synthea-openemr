# Project history

## Objective

Generate realistic synthetic Canadian Greater Toronto Area patient records
for import into the Maple Grove OpenEMR student project.

## Initial Synthea test

The standard prebuilt Synthea JAR was tested first. Its default geography was
not suitable for the final Canadian dataset.

## Canadian configuration

The official Synthea source repository and Synthea International repository
were downloaded. The Canada configuration files were copied into the Synthea
source tree, and a custom JAR was built.

## Name formatting

Synthea normally adds number suffixes to generated names. These suffixes were
disabled so names appear naturally in the OpenEMR demonstration.

## Initial Toronto test

Generating with `Ontario Toronto` restricted all patients to Toronto. The
original Canadian geography file contained only one Toronto postal prefix,
so every generated Toronto patient received the same three-character value.

## GTA expansion

A GTA-specific demographics file was created with selected municipalities
from across the Greater Toronto Area.

## Complete postal codes

The original Canadian patient geography contained only partial postal-code
prefixes. Complete Canadian postal-code records were added from the GeoNames
Canada postal-code dataset.

The resulting GTA geography file includes many complete postal codes per
municipality, along with latitude and longitude values.

## Current working result

The customized GTA JAR now generates:

- synthetic Canadian patients;
- multiple GTA municipalities;
- diverse complete Canadian postal codes;
- CSV output;
- names without numeric suffixes;
- reproducible output when fixed seeds and dates are used.

## Current direction

The Python API workflow is implemented and validated through patients, encounters,
curated conditions, allergies, medications, and historical vital signs, including the
complete all-skipped vital acceptance rerun. The current work is to verify exact OpenEMR
procedure write capabilities and select only procedure mappings that preserve the
source's clinical meaning.

Validation marker: complete acceptance rerun: 0 created, 1,503 skipped, and 0 failed.
