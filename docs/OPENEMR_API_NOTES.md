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

## General importer rule

An OpenEMR response should count as successful only after checking:

1. the expected HTTP status;
2. the JSON body for validation and internal errors;
3. the returned record identifier when creating a resource;
4. the local map checkpoint written immediately after creation.

Runtime behavior takes precedence over assumptions based solely on Swagger examples.
