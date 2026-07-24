# OpenEMR Remote Target Branch Notes

**Branch:** `feat/openemr-target-config`  
**Project:** Maple Grove Synthea → OpenEMR importer  
**Started:** 2026-07-24  
**Current target under test:** OpenEMR 7.0.2 on a cloned AWS instance

---

## Purpose of This Branch

Add one adaptable OpenEMR target workflow that supports:

- Local Docker OpenEMR 8
- Remote OpenEMR 7 servers
- Official HTTPS hostnames with valid certificates
- Disposable AWS test servers accessed through a raw IP address
- Explicit insecure TLS mode for known test servers

The intended student workflow should remain simple:

```bash
python3 scripts/configure_openemr_target.py
python3 scripts/register_openemr_client.py
python3 scripts/test_openemr_connection.py
python3 scripts/import_openemr.py
```

Students should not need AWS Console, SSH, Docker, database, or server filesystem access.

---

## AWS Test Environment

### Clone

```text
https://18.223.33.251
```

This is a disposable clone of the OpenEMR AWS instance.

### OpenEMR version

Confirmed inside the OpenEMR container:

```text
OpenEMR 7.0.2 patch 0
```

The version file reported:

```php
$v_major = '7';
$v_minor = '0';
$v_patch = '2';
$v_realpatch = '0';
```

### TLS behavior

The raw IP does not match the certificate hostname, so normal certificate verification fails.

For this known disposable test server, the project target configuration uses:

```json
{
  "base_url": "https://18.223.33.251",
  "site": "default",
  "version": "7.0.2",
  "major_version": 7,
  "verify_tls": false,
  "target_mode": "remote"
}
```

This configuration is stored privately under:

```text
.local/openemr-target.json
```

It must remain excluded from Git.

The official shared server should later use:

```text
https://mgfhc-demo.hopto.org
```

with:

```json
{
  "verify_tls": true
}
```

---

## Server Checks Completed

OAuth discovery succeeded:

```bash
BASE_URL="https://18.223.33.251"

curl -kfsS   "${BASE_URL}/oauth2/default/.well-known/openid-configuration" |
  python3 -m json.tool
```

Important results:

- Issuer points to the clone IP
- Registration endpoint points to the clone IP
- Token endpoint points to the clone IP
- `password` appears under supported grant types
- OpenEMR Standard REST API scopes are advertised

Unauthenticated API access returned the expected response:

```bash
curl -ki   "${BASE_URL}/apis/default/api/patient"
```

Result:

```text
HTTP/1.1 401 Unauthorized
Missing "Authorization" header
```

This confirms the Standard REST API endpoint is active.

---

## Changes Implemented

### New file

```text
scripts/configure_openemr_target.py
```

Responsibilities:

- Ask whether the target is local Docker or remote
- Accept a remote server base URL
- Accept the OpenEMR site name
- Accept the OpenEMR version
- Test OAuth discovery
- Detect certificate verification failure
- Offer explicit insecure certificate mode
- Save the target under `.local/openemr-target.json`
- Remove the remote target configuration when local mode is selected
- Warn when an existing OAuth client belongs to another server

### Modified file

```text
scripts/detect_openemr.py
```

New behavior:

1. Check for `.local/openemr-target.json`
2. If a remote target is configured, return that target
3. Otherwise fall back to the existing local Docker detection

Remote detection now returns information including:

```text
base_url
site
version
major_version
api_base_url
verify_tls
target_mode
```

Local Docker detection continues to work as the fallback.

### Modified files

```text
scripts/import_openemr_patients.py
scripts/test_openemr_connection.py
```

The password-grant token request now includes:

```python
"client_secret": client["client_secret"],
```

This matches the OAuth client registration method:

```text
client_secret_post
```

### Existing scope logic retained

The existing registration script already selects the correct scope format:

- OpenEMR 8 uses compact scopes
- OpenEMR 7 uses expanded `.read` and `.write` scopes

No manual scope list should be required from students.

---

## Configuration Test Completed

The target configurator was run successfully:

```bash
python3 scripts/configure_openemr_target.py
```

Inputs:

```text
Selection: 2
OpenEMR server URL: https://18.223.33.251
OpenEMR site: default
OpenEMR version: 7.0.2
Allow insecure certificate mode: y
```

Detection now reports:

```text
Remote OpenEMR configured
  OpenEMR version: 7.0.2
  Base URL: https://18.223.33.251
  Standard API: https://18.223.33.251/apis/default/api
  Verify TLS certificate: False
```

Verified using:

```bash
python3 scripts/detect_openemr.py
```

---

## Git State

A new branch was created:

```text
feat/openemr-target-config
```

Initial commit:

```text
feat: support configurable OpenEMR targets
```

The branch was pushed with upstream tracking:

```bash
git push -u origin feat/openemr-target-config
```

Future pushes on this branch only require:

```bash
git push
```

---

## Current Next Step

The existing OAuth client belongs to another OpenEMR server.

Remove only the saved local client:

```bash
rm -f .local/openemr-client.json
```

Then register a new OAuth client against the configured AWS clone:

```bash
python3 scripts/register_openemr_client.py
```

After registration:

1. Open the clone in a browser
2. Continue through the certificate warning because this is the known disposable clone
3. Go to:

```text
Administration → System → API Clients
```

4. Find the newly registered Maple Grove client
5. Enable or approve it

Then test authentication:

```bash
export OPENEMR_USERNAME="admin"
read -rsp "OpenEMR password: " OPENEMR_PASSWORD
echo
export OPENEMR_PASSWORD

python3 scripts/test_openemr_connection.py

unset OPENEMR_PASSWORD
```

Expected checkpoint:

```text
OpenEMR connection test passed
```

---

## After Authentication Works

Do not immediately run the full import.

First run the dry-run:

```bash
python3 scripts/import_openemr.py
```

Then perform the smallest safe write test:

```bash
python3 scripts/import_openemr.py   --resource patients   --commit
```

Prefer a dataset containing only one patient for the first AWS write test.

After import:

- Confirm the patient appears in OpenEMR
- Confirm demographics are correct
- Confirm rerunning does not create an unintended duplicate
- Inspect the local patient mapping file
- Only then proceed to encounters and clinical resources

Suggested validation order:

1. Patients
2. Encounters
3. Conditions
4. Allergies
5. Medications
6. Vitals

Vitals should be tested last because prior OpenEMR 8 work required version-specific compatibility handling. Do not apply the OpenEMR 8 server patch to OpenEMR 7.

---

## Known Caveats and Follow-Up Work

### 1. TLS verification is not yet consistently respected

Some existing requests currently use:

```python
verify=False
```

globally.

The intended behavior is:

```python
verify=openemr["verify_tls"]
```

Policy:

- Official hostname: verification required
- Known disposable raw-IP server: verification may be disabled explicitly
- Never silently downgrade from verified HTTPS
- Never automatically switch to plain HTTP

Likely future commit:

```text
fix: respect configured TLS verification
```

### 2. Verify every importer uses the shared target

The repository contains multiple resource importers.

Before considering the branch complete, confirm all of them obtain their base URL through the same shared detection/configuration path rather than assuming local Docker.

### 3. Local OpenEMR 8 regression test required

Before merging:

```bash
python3 scripts/configure_openemr_target.py
```

Choose local Docker mode, then verify:

```bash
python3 scripts/detect_openemr.py
python3 scripts/test_openemr_connection.py
```

Local OpenEMR 8 must continue working.

### 4. Remote OpenEMR version entry is currently manual

The setup command currently asks the user for the OpenEMR version.

A later improvement could attempt version detection through the API or another safe endpoint, with manual entry as fallback.

### 5. Shared classroom imports require a policy

Thirty students should not all import the same dataset independently into one shared OpenEMR server.

Possible models:

- Instructor performs one canonical import
- Each student receives a unique Synthea seed and small patient count
- Each student receives a separate server or OpenEMR site

Start with very small imports on the shared instance.

---

## Useful Health Checks

Check current branch:

```bash
git branch --show-current
```

Check repository state:

```bash
git status --short
```

Check Python syntax:

```bash
python3 -m py_compile   scripts/configure_openemr_target.py   scripts/detect_openemr.py   scripts/register_openemr_client.py   scripts/import_openemr_patients.py   scripts/test_openemr_connection.py
```

Check whitespace errors:

```bash
git diff --check
```

Review uncommitted changes:

```bash
git diff
```

Review staged changes:

```bash
git diff --cached
```

---

## Suggested Commit Sequence

Already completed:

```text
feat: support configurable OpenEMR targets
```

Likely future commits:

```text
fix: respect configured TLS verification
```

```text
fix: support remote targets across importers
```

```text
test: validate OpenEMR 7 remote import flow
```

```text
docs: document local and remote OpenEMR setup
```

Keep experimental progress commits on:

```text
feat/openemr-target-config
```

Merge into `main` only after:

- Local OpenEMR 8 still works
- Remote OpenEMR 7 registration works
- Remote authentication works
- One-patient AWS import works
- Duplicate behavior is verified
- TLS settings are respected
- Student documentation covers both environments

---

## Resume Prompt for a New Chat

Use this when restarting with another ChatGPT session:

> I am working on the `feat/openemr-target-config` branch of my Maple Grove Synthea-to-OpenEMR project. We added `scripts/configure_openemr_target.py` and modified `scripts/detect_openemr.py` so the existing scripts can use either local Docker OpenEMR or a saved remote target under `.local/openemr-target.json`. The current disposable AWS clone is OpenEMR 7.0.2 at `https://18.223.33.251`, using explicit `verify_tls: false` because the certificate does not match the raw IP. OAuth discovery and the unauthenticated Standard API endpoint both work. The next step is to remove the old `.local/openemr-client.json`, register a new client using the existing `scripts/register_openemr_client.py`, approve it in OpenEMR, and run `scripts/test_openemr_connection.py`. Please review this progress document and continue with small explicit file or terminal changes rather than editing a ZIP copy of the repository.

---

## Security Reminder

Never commit:

```text
.local/openemr-target.json
.local/openemr-client.json
.env
passwords
client secrets
access tokens
AWS credentials
```

Before every commit:

```bash
git status --short
git diff --cached
```
