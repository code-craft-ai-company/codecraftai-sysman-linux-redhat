# Requirements

## Introduction

The certificate issuance feature provides the
`automojo-sysman-linux-redhat` package with the ability to *issue*
X.509 certificates — as opposed to the *trust* of externally-issued
certificates covered by `3-certificate-trust-management`.  It
covers OpenSSL-based CSR generation and self-signing, certmonger
enrolment and renewal tracking, and ACME-based issuance via
`certbot` or `acme.sh`.

All system commands (`openssl`, `getcert`, `certmonger`, `certbot`,
`acme.sh`) are executed through the `automojo-interfaces`
command-runner abstraction.  Private-key and certificate files are
created at declared paths; reads and writes route through the
runner (`cat`/`tee`).  All mutating operations respect dry-run
mode.

Private-key passphrases and ACME account credentials SHALL flow
through `automojo-credentials` (or an equivalent credential
object) and be delivered via runner `stdin`, never argv.

This feature complements — and does not duplicate — the trust-store
surface owned by `3-certificate-trust-management` (installing /
removing / refreshing CA anchors).

---

## Requirements

### Requirement 1: Private-Key Generation

**User Story:** As a maintenance coordinator, I want to generate
private keys with a declared algorithm, bit/curve strength, and
optional passphrase encryption.

#### Acceptance Criteria

1. WHEN the coordinator requests an RSA key THE SYSTEM SHALL
   invoke `openssl genpkey -algorithm RSA -pkeyopt
   rsa_keygen_bits:<bits> -out <path>`.
2. WHEN the coordinator requests an EC key THE SYSTEM SHALL
   invoke `openssl genpkey -algorithm EC -pkeyopt
   ec_paramgen_curve:<curve> -out <path>`.
3. WHEN the coordinator supplies a passphrase credential THE
   SYSTEM SHALL append `-aes256 -pass stdin` and pass the
   passphrase bytes via runner `stdin`.
4. WHEN a key already exists at the target path AND `overwrite=False`
   THE SYSTEM SHALL return `SKIPPED` without dispatching.
5. WHEN the key file is created THE SYSTEM SHALL chmod it to
   `0600` post-write.
6. WHEN dry-run is active THE SYSTEM SHALL NOT dispatch key
   generation.
7. THE SYSTEM SHALL NEVER put the passphrase on argv.

---

### Requirement 2: Certificate Signing Request (CSR)

**User Story:** As a maintenance coordinator, I want to generate
a CSR from an existing key with declared subject and SAN fields,
so that a CA can issue a certificate.

#### Acceptance Criteria

1. WHEN the coordinator requests a CSR THE SYSTEM SHALL write a
   temporary OpenSSL config (with `req`, `req_distinguished_name`,
   `v3_req`, `alt_names` sections) to a temp file, invoke
   `openssl req -new -key <key_path> -out <csr_path> -config
   <temp_config>`, and remove the temp config in a `finally`
   block.
2. WHEN the key is passphrase-encrypted THE SYSTEM SHALL supply
   the passphrase via stdin using `-passin stdin`.
3. WHEN a CSR already exists at the target path AND
   `overwrite=False` THE SYSTEM SHALL return `SKIPPED`.
4. WHEN the coordinator queries a CSR THE SYSTEM SHALL invoke
   `openssl req -in <path> -noout -text` and return a parsed
   `CsrInfo` record (subject, public-key algorithm, SANs).

---

### Requirement 3: Self-Signed Certificate

**User Story:** As a maintenance coordinator, I want to issue a
self-signed certificate for internal services or short-lived test
workflows.

#### Acceptance Criteria

1. WHEN the coordinator requests a self-signed cert THE SYSTEM
   SHALL invoke `openssl req -x509 -new -key <key> -out <cert>
   -days <days> -config <temp_config>` with the same passphrase-
   on-stdin semantics as CSR generation.
2. WHEN a cert already exists at the target path AND
   `overwrite=False` THE SYSTEM SHALL return `SKIPPED`.
3. WHEN the cert is written THE SYSTEM SHALL chmod it to `0644`
   post-write (unlike the key, which is `0600`).

---

### Requirement 4: Inspect a Certificate

**User Story:** As a maintenance coordinator, I want to read
subject, issuer, validity, and SAN fields from an existing
certificate so that compliance reports can summarise the
installed PKI posture.

#### Acceptance Criteria

1. WHEN the coordinator queries a cert THE SYSTEM SHALL invoke
   `openssl x509 -in <path> -noout -text` and return a
   `CertificateInfo` record with `subject`, `issuer`,
   `serial_hex`, `not_before`, `not_after`, `signature_algorithm`,
   `public_key_algorithm`, `public_key_size`,
   `sans: list[str]`, `key_usages: list[str]`,
   `extended_key_usages: list[str]`, `fingerprint_sha256`.
2. WHEN the certificate is absent THE SYSTEM SHALL return `None`.
3. WHEN the coordinator queries expiry THE SYSTEM SHALL return a
   `timedelta` representing time-until-expiry, negative when
   expired.

---

### Requirement 5: certmonger (IPA and ad-hoc auto-renewal)

**User Story:** As a maintenance coordinator, I want to enrol a
certificate with certmonger so that renewals happen
automatically.

#### Acceptance Criteria

1. WHEN the coordinator requests a certmonger enrolment THE
   SYSTEM SHALL invoke `getcert request -f <cert_path> -k
   <key_path> -r -w [-N <subject>] [-D <san> ...] [-c <ca>] [-T
   <profile>]` — `-r` enables renewal, `-w` waits for first
   issuance.
2. WHEN the coordinator lists tracking THE SYSTEM SHALL invoke
   `getcert list` and return `list[CertmongerTracking]`.
3. WHEN the coordinator removes tracking THE SYSTEM SHALL invoke
   `getcert stop-tracking -f <cert_path>`; SKIPPED when no
   tracking matches.
4. WHEN a tracking entry for the target `cert_path` already
   exists with the same subject + SANs + CA THE SYSTEM SHALL
   return `SKIPPED`.

---

### Requirement 6: ACME Issuance (certbot / acme.sh)

**User Story:** As a maintenance coordinator, I want to request
ACME certificates (e.g. Let's Encrypt) via the declared client,
so that the system can obtain publicly-trusted TLS certificates.

#### Acceptance Criteria

1. WHEN the coordinator requests an ACME certificate via certbot
   with the webroot method THE SYSTEM SHALL invoke `certbot
   certonly --webroot --webroot-path <path> -d <domain> [-d ...]
   --agree-tos -m <email> --non-interactive [--server <acme_url>]`.
2. WHEN the coordinator requests with the standalone method THE
   SYSTEM SHALL invoke `certbot certonly --standalone` with the
   same flags.
3. WHEN the coordinator requests with the DNS-01 method the plugin
   SHALL be declared in `AcmeIssuanceSpec.plugin` and passed as
   `--<plugin>` on the certbot command line; plugin credentials
   SHALL be routed via `--<plugin>-credentials <path>` with the
   file content written ahead of time via `tee` and `chmod 0600`.
4. WHEN the cert already exists AND is more than `renew_after_days`
   from expiry THE SYSTEM SHALL return `SKIPPED`.
5. WHEN the coordinator renews a cert THE SYSTEM SHALL invoke
   `certbot renew --cert-name <name> [--force-renewal]`.

---

### Requirement 7: Dry-Run, Elevation, Credential Handling, Error
Reporting

Standard dry-run / elevation / failure semantics.

#### Acceptance Criteria

1. WHEN `config.dry_run=True` THE SYSTEM SHALL NOT dispatch any
   mutating `openssl` / `getcert request|stop-tracking` / `certbot
   certonly|renew` command, and SHALL NOT write any key,
   certificate, CSR, or temp-config file.
2. WHEN `config.elevated=True` THE SYSTEM SHALL dispatch mutating
   commands elevated.
3. THE SYSTEM SHALL NEVER include passphrase material in the
   recorded `command` field on any returned result, and SHALL
   NEVER place passphrase material on argv.
4. WHEN a required credential is missing THE SYSTEM SHALL raise
   `CertificateCredentialError` (a `SemanticError` subtype).
