# Implementation Plan

- [ ] 1. Create the `certificatetrustmanagement` module file
  - Create `source/packages/automojo/sysman/linux/redhat/certificatetrustmanagement.py`
  - Add module-level docstring describing purpose and coordinator-driven design
  - Document dependency on `ICommandRunner` and `IFileOperator` injections
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9_

- [ ] 2. Define status enum and result dataclasses
  - Implement `CertTrustActionStatus` enum with values: `SUCCESS`, `FAILED`, `SKIPPED`, `DRY_RUN`
  - Implement `CertTrustActionResult` dataclass with fields: `status`, `name`,
    `path`, `exit_code`, `stdout`, `stderr`, `command`, `note`, `subject`,
    `sha256_fingerprint`, `error`
  - Implement `TrustAnchor` dataclass with fields: `name`, `path`, `subject`,
    `issuer`, `not_before`, `not_after`, `sha256_fingerprint`, `valid`, `error`
  - Implement `CertMetadata` dataclass with fields: `subject`, `issuer`,
    `serial`, `not_before`, `not_after`, `sha256_fingerprint`, `sans`
  - Implement `CertVerifyResult` dataclass with fields: `ok`, `output`
  - Implement `TrustStoreState` dataclass with fields: `enabled`, `raw_status`
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8_

- [ ] 3. Define typed exceptions
  - Implement `CertTrustQueryError` with `exit_code` and `stderr` attributes
  - Implement `CertTrustPathNotFoundError` with `path` attribute
  - _Requirements: 3, 6, 7_

- [ ] 4. Implement `CertificateTrustManagerConfig` dataclass
  - Add `dry_run: bool = False` field
  - Add `elevated: bool = False` field
  - _Requirements: 1.6, 2.4, 4.5, 5.5, 8.5, 9_

- [ ] 5. Implement `CertificateTrustManager.__init__`, `list_anchors()`, and `query_trust_state()`
  - Accept `ICommandRunner`, `IFileOperator`, and `CertificateTrustManagerConfig` as constructor arguments
  - Implement `list_anchors()` by listing `/etc/pki/ca-trust/source/anchors/`
    via the file operator, then running
    `openssl x509 -noout -subject -issuer -dates -fingerprint -sha256 -in <path>`
    for each entry
  - Return an empty list when the directory does not exist
  - Mark each entry `valid=False` with captured error text when parsing fails
  - Implement `query_trust_state()` by running `update-ca-trust check` and
    parsing the output into `TrustStoreState`; raise `CertTrustQueryError` on
    unexpected exit codes
  - _Requirements: 3, 5.3_

- [ ] 6. Implement `install_anchor()`
  - Verify the source path exists via the file operator; if not, return
    `FAILED` with a descriptive error
  - Validate the PEM with `openssl x509 -noout -in <path>`; if non-zero,
    return `FAILED` with captured stderr and do not write
  - Compute the destination `/etc/pki/ca-trust/source/anchors/<name>.pem`
  - If the destination exists and bytes match the source, return `SKIPPED`
  - Honour `dry_run` flag; return `DRY_RUN` without writing
  - Write with `mode=0o644`, `owner="root"`, `group="root"`, and the configured
    `elevated` flag through the file operator
  - Capture subject and SHA-256 fingerprint from the source via
    `get_certificate_metadata()` for inclusion in the result
  - Return `CertTrustActionResult` with a note that `update-ca-trust` must be
    invoked
  - _Requirements: 1, 9.1_

- [ ] 7. Implement `remove_anchor()`
  - Compute the destination under `/etc/pki/ca-trust/source/anchors/<name>`
  - If the file does not exist via the file operator, return `SKIPPED`
  - Honour `dry_run` flag; return `DRY_RUN` without removing
  - Remove with the configured `elevated` flag through the file operator
  - Return `CertTrustActionResult` with a note that `update-ca-trust` must be
    invoked
  - _Requirements: 2, 9.1_

- [ ] 8. Implement `refresh_trust_store()`
  - Accept an `extract: bool = True` parameter
  - Build `update-ca-trust extract` when `extract=True`, otherwise bare
    `update-ca-trust`
  - Honour `dry_run` flag; return `DRY_RUN` without calling the runner
  - Pass the configured `elevated` flag to the runner
  - Map zero exit to `SUCCESS`, non-zero to `FAILED` with captured stderr
  - _Requirements: 4, 9.1_

- [ ] 9. Implement `enable_trust_store()` and `disable_trust_store()`
  - For each, call `query_trust_state()` first; return `SKIPPED` when already
    in the desired state
  - Build `update-ca-trust enable` or `update-ca-trust disable` as appropriate
  - Honour `dry_run` flag on both methods
  - Pass the configured `elevated` flag to the runner on both methods
  - Map runner result to `CertTrustActionResult`
  - _Requirements: 5.1, 5.2, 5.4, 5.5, 9.1_

- [ ] 10. Implement `verify_certificate()`
  - Accept `cert_path: str`, `ca_file: str | None = None`, `untrusted: str | None = None`
  - Raise `CertTrustPathNotFoundError` when the certificate file does not exist
  - Build `openssl verify` with optional `-CAfile <ca-path>` and
    `-untrusted <chain-path>` flags
  - Run without elevation
  - Return `CertVerifyResult(ok=True, output=...)` on exit code zero,
    `CertVerifyResult(ok=False, output=...)` otherwise (do not raise)
  - _Requirements: 6, 9.3_

- [ ] 11. Implement `get_certificate_metadata()`
  - Raise `CertTrustPathNotFoundError` when the file does not exist
  - Invoke
    `openssl x509 -noout -subject -issuer -serial -startdate -enddate -fingerprint -sha256 -ext subjectAltName -in <path>`
  - Parse stdout into a `CertMetadata` record including the SAN list
  - Raise `CertTrustQueryError` with the captured stderr when parsing fails
  - Run without elevation
  - _Requirements: 7, 9.3_

- [ ] 12. Implement `install_blocklist_anchor()` and `remove_blocklist_anchor()`
  - Mirror `install_anchor()` and `remove_anchor()` but against
    `/etc/pki/ca-trust/source/blacklist/<name>.pem`
  - Validate PEM before copy; apply byte-identical-skip; honour `dry_run`
  - Apply elevated writes and removes through the file operator
  - Return `CertTrustActionResult` with a note that `update-ca-trust` must be
    invoked
  - _Requirements: 8, 9.1_

- [ ] 13. Create test module and mock infrastructure
  - Create `source/testroots/testbase/automojo/tests/sysman/linux/redhat/test_certificatetrustmanagement.py`
  - Implement `MockCommandRunner` that returns configurable `RunResult` values
    and records each call
  - Implement `MockFileOperator` that simulates an in-memory filesystem and
    records each call with its `elevated` flag
  - Set up hypothesis testing framework configuration
  - Create test fixtures for common PEM payloads and anchor names
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9_

- [ ] 14. Write property-based test for PEM validation before copy
  - **Property 1: PEM Validation Before Copy**
  - Generate random non-PEM byte payloads
  - Verify the file operator `write_bytes` is never invoked when validation fails
  - **Validates: Requirements 1.1, 1.2, 8.1**
  - _Requirements: 1, 8_

- [ ] 15. Write property-based test for byte-identical install skip
  - **Property 2: Byte-Identical Install Skip**
  - Generate random PEM-shaped payloads and pre-populate the destination with
    identical bytes
  - Verify `install_anchor()` and `install_blocklist_anchor()` return `SKIPPED`
    without calling `write_bytes`
  - **Validates: Requirements 1.4, 8.3**
  - _Requirements: 1, 8_

- [ ] 16. Write property-based test for remove-absent skip
  - **Property 3: Remove-Absent Skip**
  - Generate random anchor names that are not present in the mock filesystem
  - Verify `remove_anchor()` and `remove_blocklist_anchor()` return `SKIPPED`
    without calling `remove`
  - **Validates: Requirements 2.2, 8.2**
  - _Requirements: 2, 8_

- [ ] 17. Write property-based test for refresh determinism
  - **Property 4: Refresh Determinism**
  - Generate random exit codes from the runner
  - Verify `refresh_trust_store()` always invokes the runner and maps status
    deterministically (zero → `SUCCESS`, non-zero → `FAILED`)
  - **Validates: Requirements 4.1, 4.2, 4.3, 4.4**
  - _Requirements: 4_

- [ ] 18. Write property-based test for trust state query freshness
  - **Property 5: Trust State Query Freshness**
  - Invoke `query_trust_state()` multiple times in sequence
  - Verify the runner is called on every invocation (no caching)
  - **Validates: Requirements 5.3**
  - _Requirements: 5_

- [ ] 19. Write property-based test for verify returning result on mismatch
  - **Property 6: Verify Returns Result, Does Not Throw on Mismatch**
  - Generate random non-zero exit codes for `openssl verify`
  - Verify `CertVerifyResult(ok=False, output=...)` is returned without raising
  - **Validates: Requirements 6.1, 6.4, 6.5**
  - _Requirements: 6_

- [ ] 20. Write property-based test for dry-run safety
  - **Property 7: Dry-Run Safety**
  - Generate random mutating operations with `dry_run=True`
  - Verify the runner is never invoked for mutating commands and the file
    operator is never invoked for writes or removes
  - Verify all results have `DRY_RUN` status
  - **Validates: Requirements 1.6, 2.4, 4.5, 5.5, 8.5**
  - _Requirements: 1, 2, 4, 5, 8_

- [ ] 21. Write property-based test for elevation consistency
  - **Property 8: Elevation Consistency**
  - Generate random operations with `elevated=True` / `elevated=False`
  - Verify mutating operations propagate the `elevated` flag to the runner and
    file operator
  - Verify read-only operations never request elevation
  - **Validates: Requirements 9.1, 9.2, 9.3**
  - _Requirements: 9_

- [ ] 22. Write property-based test for metadata round trip
  - **Property 9: Metadata Round Trip**
  - Install a generated PEM via the mock file operator and run
    `get_certificate_metadata()` and `list_anchors()`
  - Verify the `subject`, `issuer`, and `sha256_fingerprint` fields match
  - **Validates: Requirements 3.2, 7.1, 7.2**
  - _Requirements: 3, 7_

- [ ] 23. Write property-based test for blocklist symmetry
  - **Property 10: Blocklist Symmetry**
  - Generate random anchor names
  - Run `install_blocklist_anchor()` followed by `remove_blocklist_anchor()`
    and verify the blocklist directory state matches its starting state
  - Verify `update-ca-trust` was not invoked between the two calls
  - **Validates: Requirements 8.1, 8.2, 8.4**
  - _Requirements: 8_

- [ ] 24. Write unit tests for `install_anchor()`
  - Test: source path missing — returns `FAILED`, file operator not asked to write
  - Test: invalid PEM — returns `FAILED` with captured stderr, file operator not asked to write
  - Test: destination absent — writes with `mode=0o644`, `owner="root"`,
    `group="root"`, and `elevated=True`; returns `SUCCESS`
  - Test: destination has identical bytes — returns `SKIPPED`, no write
  - Test: dry-run mode — returns `DRY_RUN`, no write
  - Test: result includes captured subject and SHA-256 fingerprint
  - _Requirements: 1_

- [ ] 25. Write unit tests for `remove_anchor()`
  - Test: anchor present — removes with `elevated=True`, returns `SUCCESS`
  - Test: anchor absent — returns `SKIPPED`, remove not called
  - Test: dry-run mode — returns `DRY_RUN`, remove not called
  - Test: result note mentions `update-ca-trust`
  - _Requirements: 2_

- [ ] 26. Write unit tests for `list_anchors()`
  - Test: directory absent — returns empty list
  - Test: valid anchors — returns populated `TrustAnchor` records
  - Test: invalid anchor file — returns entry with `valid=False` and error text
  - Test: runner called without elevation for metadata parsing
  - _Requirements: 3, 9.3_

- [ ] 27. Write unit tests for `refresh_trust_store()`
  - Test: default call invokes `update-ca-trust extract` with elevation,
    returns `SUCCESS`
  - Test: `extract=False` invokes bare `update-ca-trust`
  - Test: non-zero exit — returns `FAILED` with exit code and stderr
  - Test: dry-run mode — returns `DRY_RUN`, runner not called
  - _Requirements: 4_

- [ ] 28. Write unit tests for `enable_trust_store()`, `disable_trust_store()`, and `query_trust_state()`
  - Test: enable when disabled — invokes `update-ca-trust enable`, returns `SUCCESS`
  - Test: enable when already enabled — returns `SKIPPED`, no mutating command
  - Test: disable when enabled — invokes `update-ca-trust disable`, returns `SUCCESS`
  - Test: disable when already disabled — returns `SKIPPED`, no mutating command
  - Test: `query_trust_state()` parses enabled state from
    `update-ca-trust check` output
  - Test: `query_trust_state()` runs without elevation
  - Test: dry-run mode on both mutators — returns `DRY_RUN`
  - _Requirements: 5, 9.1, 9.3_

- [ ] 29. Write unit tests for `verify_certificate()`
  - Test: missing certificate file — raises `CertTrustPathNotFoundError`
  - Test: exit code zero — returns `CertVerifyResult(ok=True, output=...)`
  - Test: non-zero exit — returns `CertVerifyResult(ok=False, output=...)` without raising
  - Test: `-CAfile` flag propagated when `ca_file` supplied
  - Test: `-untrusted` flag propagated when `untrusted` supplied
  - Test: runner called without elevation
  - _Requirements: 6, 9.3_

- [ ] 30. Write unit tests for `get_certificate_metadata()`
  - Test: valid certificate — returns populated `CertMetadata` including SANs
  - Test: missing file — raises `CertTrustPathNotFoundError`
  - Test: invalid certificate — raises `CertTrustQueryError` with captured stderr
  - Test: runner called without elevation
  - _Requirements: 7, 9.3_

- [ ] 31. Write unit tests for `install_blocklist_anchor()` and `remove_blocklist_anchor()`
  - Test: install to `/etc/pki/ca-trust/source/blacklist/<name>.pem` with
    `mode=0o644`, `owner="root"`, `group="root"`, `elevated=True`; returns `SUCCESS`
  - Test: identical bytes at destination — returns `SKIPPED`, no write
  - Test: invalid PEM — returns `FAILED`, no write
  - Test: remove present blocklist file — returns `SUCCESS`
  - Test: remove absent blocklist file — returns `SKIPPED`, no remove
  - Test: dry-run mode on both — returns `DRY_RUN`
  - _Requirements: 8, 9.1_

- [ ] 32. Checkpoint - Ensure all tests pass
  - Run all unit tests and property-based tests
  - Verify all properties pass with 100+ iterations
  - Ensure all tests pass, ask the user if questions arise

