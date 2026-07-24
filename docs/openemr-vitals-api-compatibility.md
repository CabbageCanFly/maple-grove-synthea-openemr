# OpenEMR encounter-vitals API compatibility notes

## Scope

These notes document the encounter-vitals behavior observed while importing
Synthea observations into a local OpenEMR 8.0.0.3 Docker environment.

The general importer remains API-based and is intended to support other OpenEMR
versions where the Standard REST API overlaps. The compatibility patch described
below is deliberately limited to the exact affected local version and source
shape.

## Observed OpenEMR 8.0.0.3 failure

The Standard REST request was:

```text
POST /apis/default/api/patient/:pid/encounter/:eid/vital
```

The first Neville Schuster pilot returned HTTP 500 with:

```text
Cannot assign null to property
OpenEMR\Services\VitalsCalculatedService::$authUserId of type int
```

The request was not rejected before saving. OpenEMR had already created:

- one `form_vitals` row;
- one linked `forms` row for the encounter;
- the supplied historical vital date and measurements.

The response failed afterward while OpenEMR was processing calculated-vital
metadata. This is an ambiguous partial-write condition: the client receives an
error even though the main clinical record exists.

## Cause

The Standard REST route constructs `EncounterRestController` with the REST
request's Symfony session. Normal encounter creation reads authenticated values
from that session.

The calculated-vitals service instead falls back to the legacy PHP session value:

```php
$_SESSION['authUserID']
```

During the REST request that value was not populated, so OpenEMR attempted to
assign `null` to an integer-only property.

## Local compatibility patch

The project script is:

```text
scripts/ensure_local_vitals_api_compat.py
```

It synchronizes the authenticated REST session values needed by the vitals
service before POST or PUT processing.

Safety properties:

- detects the local OpenEMR application container;
- reuses the project's existing OpenEMR version detector;
- applies only to OpenEMR 8.0.0.3;
- verifies the expected affected source code before editing;
- saves the original controller under `.local/`;
- validates the patched PHP syntax;
- is idempotent;
- supports restoration with `--restore`;
- does not perform the clinical import through SQL.

Apply:

```bash
python3 scripts/ensure_local_vitals_api_compat.py
```

Restore:

```bash
python3 scripts/ensure_local_vitals_api_compat.py --restore
```

The `.local/` backup must not be committed.

## Empty collection behavior

For an encounter with no vital forms, this OpenEMR installation returns HTTP 404
from the collection request:

```text
GET /apis/default/api/patient/:pid/encounter/:eid/vital
```

For this exact collection lookup, the importer treats 404 as an empty collection.
Other unexpected 404 responses remain errors.

## Importer safety rules

The vital importer must:

- group supported Synthea `vital-signs` observations by patient, encounter, and
  exact source timestamp;
- preserve the historical date;
- checkpoint the returned vital and form identifiers immediately after a
  successful POST;
- collapse exact duplicate source rows;
- preserve conflicting source values locally and omit only the conflicting
  field rather than choosing arbitrarily;
- stop after an ambiguous timeout or partial-write condition;
- never blindly retry an ambiguous POST;
- stop for explicit review when an unmapped matching OpenEMR vital already
  exists;
- use the local vital map for normal duplicate protection.

## Verified Neville pilot

The clean post-patch pilot imported one vital form for Neville Schuster and a
repeat run skipped it from the local import map.

Verified values:

- historical date: 2018-02-03;
- height: approximately 64.29 in;
- weight: approximately 176.59 lb;
- blood pressure: 137/91;
- pulse: 87;
- respiration: 14;
- OpenEMR user: populated.

## AWS and OpenEMR 7

The 8.0.0.3 compatibility patch must not be applied automatically to the shared
AWS OpenEMR 7 target.

Before importing vitals on AWS:

1. detect and record the exact OpenEMR 7 release;
2. verify the encounter-vitals route and OAuth scopes;
3. run one dry pilot;
4. run one live pilot;
5. inspect the API response and OpenEMR UI;
6. repeat the same pilot and confirm duplicate protection.

If OpenEMR 7 has a different defect, add a separate exact-version compatibility
rule only after inspecting its installed source. Do not fall back to direct SQL
as the normal remote import workflow.

## Completed validation

For the current 105-patient validation dataset:

- Neville Schuster's historical vital form imported successfully;
- the same Neville form was skipped on repeat import;
- temperature imported successfully in Fahrenheit;
- oxygen saturation imported successfully as a percentage;
- pediatric head circumference imported successfully in inches;
- a mixed 25-form batch imported successfully;
- the exact same 25-form selection was skipped on repeat import;
- all 1,503 dynamically discovered supported grouped vital forms were processed;
- a complete repeat run created zero forms, skipped all 1,503 forms, and failed zero forms.

These counts are validation evidence for the current dataset, not hard-coded
import targets. Future datasets are discovered and grouped dynamically.
