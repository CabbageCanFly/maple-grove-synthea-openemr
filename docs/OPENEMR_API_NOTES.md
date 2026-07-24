# OpenEMR API Notes

These notes record behavior confirmed against the project's local OpenEMR 8.0.0.3 environment.

## Medication-list API

Routes:

```text
GET    /apis/default/api/patient/{pid}/medication
POST   /apis/default/api/patient/{pid}/medication
GET    /apis/default/api/patient/{pid}/medication/{mid}
PUT    /apis/default/api/patient/{pid}/medication/{mid}
DELETE /apis/default/api/patient/{pid}/medication/{mid}
```

The medication routes use the numeric OpenEMR patient ID (`pid`), not the patient UUID.

### Confirmed runtime behavior

1. A patient with no medication-list entries may return a blank HTTP 404 from the collection GET instead of HTTP 200 with an empty list.
2. A POST with a date-only `begdate`, such as `2012-06-22`, returned HTTP 200 with a JSON validation error:
   `{"begdate":{"DateTime::INVALID_VALUE":"begdate must be a valid date"}}`
3. HTTP success status alone is therefore not enough. Importers must inspect the JSON response for validation or internal errors.
4. A POST using `2012-06-22 22:52:50` returned HTTP 201 with `{"id":1244}`.
5. The following collection GET returned HTTP 500 because OpenEMR attempted to convert a null UUID:
   `Ramsey\Uuid\Uuid::fromBytes(): Argument #1 ($bytes) must be of type string, null given`
6. The POST result ID and a local import map must be treated as the primary resumability evidence until the GET behavior is resolved.
7. Save the local import map immediately after each successful POST.
8. Do not retry a POST merely because the verification GET fails after a successful 201 response.

## Allergy coding

The Synthea allergy export currently reports `SYSTEM=Unknown` for every allergy code.

Do not send values such as:

```text
Unknown:84489001
```

in OpenEMR's `diagnosis` field. OpenEMR interprets the prefix as a coding system and renders an unresolved educational-material link such as:

```text
Educational materials for Unknown code "84489001"
```

For these allergy rows:

- send the allergen title, start date, severity, and reactions to OpenEMR;
- omit `diagnosis` while the source coding system is unknown;
- preserve the original `SYSTEM` and `CODE` in `.local/allergy-import-map.json`;
- only send a diagnosis code when its coding system is known and maps to a recognized OpenEMR code type.

<!-- BEGIN ALLERGY-REACTION-LIST -->

### Recommended OpenEMR Reaction list

The allergy importer maps supported Synthea reaction SNOMED CT codes to
OpenEMR Reaction-list option IDs. These options must be configured by an
OpenEMR administrator for friendly reaction labels to persist consistently.

Keep `Unassigned` first as the special empty or fallback reaction, then
alphabetize the real reactions.

In OpenEMR, configure the Reaction list with the following values:

| Order | ID | Title | Code(s) | Local setup status |
|---:|---|---|---|---|
| 10 | `unassigned` | Unassigned | Leave blank | Existing |
| 20 | `allergic_angioedema` | Allergic Angioedema | `SNOMED-CT:402387002` | Add |
| 30 | `anaphylaxis` | Anaphylaxis | `SNOMED-CT:39579001` | Add |
| 40 | `cough` | Cough | `SNOMED-CT:49727002` | Add |
| 50 | `cutaneous_hypersensitivity` | Cutaneous Hypersensitivity | `SNOMED-CT:21626009` | Add |
| 60 | `diarrhea` | Diarrhea | `SNOMED-CT:62315008` | Add |
| 70 | `hives` | Hives | `SNOMED-CT:247472004` | Existing; change order |
| 80 | `itching` | Itching | `SNOMED-CT:418290006` | Add |
| 90 | `nasal_discharge` | Nasal Discharge | `SNOMED-CT:267101005` | Add |
| 100 | `nausea` | Nausea | `SNOMED-CT:422587007` | Existing; change order |
| 110 | `rhinoconjunctivitis` | Rhinoconjunctivitis | `SNOMED-CT:878820003` | Add |
| 120 | `shortness_of_breath` | Shortness of Breath | `SNOMED-CT:267036007` | Existing; change order |
| 130 | `skin_eruption` | Skin Eruption | `SNOMED-CT:271807003` | Add |
| 140 | `sneezing` | Sneezing | `SNOMED-CT:76067001` | Add |
| 150 | `vomiting` | Vomiting | `SNOMED-CT:300359004` | Add |
| 160 | `wheezing` | Wheezing | `SNOMED-CT:56018004` | Add |

The option IDs must remain exactly as shown because
`scripts/import_openemr_allergies.py` uses them as API values.

When an option is unavailable or rejected by the target OpenEMR installation,
the importer falls back to `unassigned` rather than failing the allergy record.
The source reaction code, description, and severity remain preserved in
`.local/allergy-import-map.json`.

This list was validated against the current local OpenEMR 8.0.0.3 setup.
Other OpenEMR versions or shared installations require equivalent
administrator-side configuration and independent validation.

<!-- END ALLERGY-REACTION-LIST -->

## Encounter-vitals API

OpenEMR 8.0.0.3 encounter-vitals behavior, the authenticated-session defect,
partial-write risk, exact-version local compatibility patch, and empty-collection
HTTP 404 behavior are documented in:

```text
docs/openemr-vitals-api-compatibility.md
```

A failed POST must not be retried automatically when the server may already have
saved clinical rows. Reconcile the response, target records, and local import map first.

## General importer rule

An OpenEMR response should count as successful only after checking:

1. the expected HTTP status;
2. the JSON body for validation and internal errors;
3. the returned record identifier when creating a resource;
4. the local map checkpoint written immediately after creation.

Runtime behavior takes precedence over assumptions based solely on Swagger examples.

<!-- BEGIN READ-ONLY-RESOURCE-NOTES -->

## Read-only or unavailable clinical resource endpoints

Local OpenEMR 8.0.0.3 capability checks found:

- generic procedures: Standard and FHIR `GET` only; a separate patient surgery `POST` route exists but is not a complete Procedure-import API;
- immunizations: Standard and FHIR `GET` only;
- care plans: FHIR `GET` only;
- devices: FHIR `GET` only;
- diagnostic reports: FHIR `GET` only;
- supplies: no matching route.

These findings apply to the installed local version and must not be assumed for
another OpenEMR release without checking its installed Swagger specification.

No importer should create substitute procedure orders, billing records, notes, or
documents for these source resources. Such substitutions would change the clinical
meaning of the Synthea data.

<!-- END READ-ONLY-RESOURCE-NOTES -->
