# Requirements

## Introduction

The certificate trust management feature provides the `automojo-sysman-linux-redhat`
package with the ability to manage the system-wide CA trust store on Red Hat Linux
systems: installing and removing trust anchors under
`/etc/pki/ca-trust/source/anchors/`, refreshing the consolidated trust bundle via
`update-ca-trust`, verifying certificates against the store, and inspecting
certificate metadata.

A maintenance coordinator uses this feature to apply a declared CA posture to the
target system. All system commands (`update-ca-trust`, `openssl`, `trust`) are
executed through the `automojo-interfaces` command-runner abstraction. All reads and
writes under `/etc/pki/ca-trust/source/` use the `automojo-interfaces` file-operator
abstraction. All mutating operations respect dry-run mode.

---

## Requirements

### Requirement 1: Install a CA Certificate as Trust Anchor

**User Story:** As a maintenance coordinator, I want to install a PEM-encoded
certificate as a trust anchor, so that the system trusts certificates issued under
that CA.

#### Acceptance Criteria

1. WHEN the coordinator supplies a certificate by path THE SYSTEM SHALL verify the
   source path exists and that the file is a syntactically valid PEM certificate (via
   `openssl x509 -noout -in <path>`) before proceeding.
2. WHEN the source is invalid THE SYSTEM SHALL return a `CertTrustActionResult` with
   status `FAILED` and an error describing the validation failure, and SHALL NOT copy
   the file.
3. WHEN the source is valid THE SYSTEM SHALL copy it to
   `/etc/pki/ca-trust/source/anchors/<name>.pem` through the file operator with mode
   `0644`, owner `root`, group `root`, and elevated privileges.
4. WHEN a file with the same destination name and identical content already exists
   THE SYSTEM SHALL skip the copy and return a `CertTrustActionResult` with status
   `SKIPPED`.
5. WHEN the copy succeeds THE SYSTEM SHALL return a `CertTrustActionResult` with
   status `SUCCESS`, a note indicating that `update-ca-trust` must be invoked for the
   change to take effect, and the captured certificate subject and SHA-256
   fingerprint.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT copy or overwrite any file.

---

### Requirement 2: Remove a CA Trust Anchor

**User Story:** As a maintenance coordinator, I want to remove a previously-installed
trust anchor, so that a CA no longer participates in system trust decisions.

#### Acceptance Criteria

1. WHEN the coordinator requests removal by anchor name THE SYSTEM SHALL delete
   `/etc/pki/ca-trust/source/anchors/<name>` through the file operator with elevated
   privileges.
2. WHEN the file does not exist THE SYSTEM SHALL skip execution and return a
   `CertTrustActionResult` with status `SKIPPED`.
3. WHEN the delete succeeds THE SYSTEM SHALL return a `CertTrustActionResult` with
   status `SUCCESS` and a note indicating that `update-ca-trust` must be invoked.
4. WHEN dry-run mode is active THE SYSTEM SHALL NOT delete any file.

---

### Requirement 3: List Installed Trust Anchors

**User Story:** As a maintenance coordinator, I want to list the trust anchors
currently installed under `/etc/pki/ca-trust/source/anchors/`, so that I can audit the
additions made to the system.

#### Acceptance Criteria

1. WHEN the coordinator requests the list THE SYSTEM SHALL enumerate
   `/etc/pki/ca-trust/source/anchors/` via the file operator.
2. THE SYSTEM SHALL return a list of `TrustAnchor` records containing the file name,
   full path, captured subject, issuer, not-before / not-after timestamps, and
   SHA-256 fingerprint. Subject, issuer, and timestamps SHALL be obtained via
   `openssl x509 -noout -subject -issuer -dates -in <path>`.
3. WHEN a file is not a valid PEM certificate THE SYSTEM SHALL include it in the
   returned list with `valid=False` and the captured error text.
4. WHEN the directory does not exist THE SYSTEM SHALL return an empty list.

---

### Requirement 4: Refresh the Consolidated Trust Store

**User Story:** As a maintenance coordinator, I want to trigger a refresh of the
consolidated trust store, so that installed anchors and blacklist entries take effect
for all TLS clients on the system.

#### Acceptance Criteria

1. WHEN the coordinator requests a refresh THE SYSTEM SHALL invoke
   `update-ca-trust extract` with elevated privileges.
2. WHEN the coordinator explicitly requests `update-ca-trust` without `extract` THE
   SYSTEM SHALL invoke the equivalent form.
3. WHEN the refresh succeeds THE SYSTEM SHALL return a `CertTrustActionResult` with
   status `SUCCESS`.
4. WHEN the refresh fails THE SYSTEM SHALL return a `CertTrustActionResult` with
   status `FAILED`, exit code, and captured stderr.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 5: Enable or Disable the Trust Store

**User Story:** As a maintenance coordinator, I want to enable or disable the
consolidated trust store, so that I can control whether the system honors its
configured trust anchors.

#### Acceptance Criteria

1. WHEN the coordinator requests enable THE SYSTEM SHALL invoke
   `update-ca-trust enable` with elevated privileges.
2. WHEN the coordinator requests disable THE SYSTEM SHALL invoke
   `update-ca-trust disable` with elevated privileges.
3. WHEN the coordinator queries the state THE SYSTEM SHALL invoke
   `update-ca-trust check` and parse the output to return a `TrustStoreState` record
   containing `enabled` and the captured raw status.
4. WHEN the state is already the desired state THE SYSTEM SHALL skip the command and
   return a `CertTrustActionResult` with status `SKIPPED`.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any mutating command.

---

### Requirement 6: Verify a Certificate Against the Trust Store

**User Story:** As a maintenance coordinator, I want to verify that a certificate
chains to a trusted CA in the system trust store, so that I can confirm trust posture
before deploying services that depend on it.

#### Acceptance Criteria

1. WHEN the coordinator supplies a certificate path THE SYSTEM SHALL invoke
   `openssl verify <path>` and capture the exit code and output.
2. WHEN the coordinator supplies an explicit untrusted chain THE SYSTEM SHALL pass
   `-untrusted <chain-path>`.
3. WHEN the coordinator supplies an explicit CA file THE SYSTEM SHALL pass
   `-CAfile <ca-path>`; otherwise the system trust store SHALL be used.
4. THE SYSTEM SHALL return a `CertVerifyResult` record with `ok=True` when exit code
   is zero and `ok=False` otherwise, along with the captured output for diagnostic
   purposes.
5. WHEN the source certificate file does not exist THE SYSTEM SHALL raise a
   `CertTrustPathNotFoundError`.

---

### Requirement 7: Query Certificate Metadata

**User Story:** As a maintenance coordinator, I want to query subject, issuer,
validity dates, serial, SANs, and fingerprints of a certificate, so that I can make
decisions about replacement or expiration.

#### Acceptance Criteria

1. WHEN the coordinator requests metadata for a path THE SYSTEM SHALL invoke
   `openssl x509 -noout -subject -issuer -serial -startdate -enddate -fingerprint -sha256 -ext subjectAltName -in <path>`.
2. THE SYSTEM SHALL return a `CertMetadata` record containing `subject`, `issuer`,
   `serial`, `not_before`, `not_after`, `sha256_fingerprint`, and a list of
   subject-alt-name entries.
3. WHEN the file does not exist THE SYSTEM SHALL raise a
   `CertTrustPathNotFoundError`.
4. WHEN the file is not a valid certificate THE SYSTEM SHALL raise a
   `CertTrustQueryError` with the captured error text.

---

### Requirement 8: Manage Blocklist (Untrusted) Anchors

**User Story:** As a maintenance coordinator, I want to install a certificate under
`/etc/pki/ca-trust/source/blacklist/` (or the current named directory), so that a CA
can be explicitly distrusted even if present elsewhere.

#### Acceptance Criteria

1. WHEN the coordinator requests a distrust install THE SYSTEM SHALL copy the
   supplied PEM to `/etc/pki/ca-trust/source/blacklist/<name>.pem` through the file
   operator with mode `0644`, owner `root`, group `root`, and elevated privileges.
2. WHEN the coordinator requests removal THE SYSTEM SHALL delete the named file.
3. WHEN identical content already exists at the destination THE SYSTEM SHALL skip
   the copy and return a `CertTrustActionResult` with status `SKIPPED`.
4. WHEN the operation succeeds THE SYSTEM SHALL return a `CertTrustActionResult`
   with status `SUCCESS` and a note indicating that `update-ca-trust` must be
   invoked.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT copy or delete any file.

---

### Requirement 9: Elevated Privilege Execution

**User Story:** As a maintenance coordinator, I want all mutating trust-store
operations to run with the privileges required by the tool, so that operations
succeed on standard systems where the process does not run as root.

#### Acceptance Criteria

1. WHEN the system is configured to use sudo elevation THE SYSTEM SHALL prefix all
   mutating commands (writes under `/etc/pki/ca-trust/source/`,
   `update-ca-trust extract|enable|disable`) with the command runner's elevation
   mechanism.
2. WHEN the system is running as root THE SYSTEM SHALL NOT prefix commands with
   `sudo`.
3. WHEN read-only operations (`openssl x509 -noout -...`, `openssl verify`,
   `update-ca-trust check`, file enumeration under `/etc/pki/ca-trust/source/`) are
   invoked THE SYSTEM SHALL NOT require elevation.
