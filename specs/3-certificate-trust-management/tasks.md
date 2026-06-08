# Implementation Plan — Certificate Trust Management

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

Note: the source module is named `certificatetrust.py` (not
`certificatetrustmanagement.py`); spec module-path references should
match.

## Module and Models

- [x] 1. Create the `certificatetrust` module
  - `source/packages/ccai/sysman/linux/redhat/certificatetrust.py`
    exists

- [x] 2. Define `TrustActionStatus` enum (SUCCESS / FAILED / SKIPPED /
  DRY_RUN)

- [x] 3. Define `CertificateTrustConfig` dataclass
  - Fields: `dry_run`, `elevated`,
    `anchors_dir="/etc/pki/ca-trust/source/anchors"`

- [x] 4. Define `TrustActionResult` dataclass
  - Fields: `status`, `detail`, `exit_code`, `stdout`, `stderr`, `command`

## CertificateTrustManager — Implemented

- [x] 5. Implement `__init__` accepting runner + optional config

- [x] 6. Implement `add_anchor(name, pem_content)`
  - Writes `<anchors_dir>/<name>.pem` via `tee` with stdin
  - Idempotent: reads existing via `cat`, returns SKIPPED when unchanged
  - Chains `update-ca-trust extract` on successful write
  - Dry-run short-circuits before `tee`

- [x] 7. Implement `remove_anchor(name)`
  - Reads existing via `cat`; returns SKIPPED when file already absent
  - Deletes via `rm -f` then runs `update-ca-trust extract`
  - Dry-run short-circuits

- [x] 8. Implement `refresh()` — `update-ca-trust extract` direct

## CertificateTrustManager — Gaps (specified but not implemented)

- [ ] 9. Implement `verify_certificate(path, ca_bundle=None)` — `openssl
  verify` or `trust ...` wrapper for certificate validation

- [ ] 10. Implement `inspect_certificate(path)` — returns subject,
  issuer, fingerprint, validity dates, SANs

- [ ] 11. Implement `list_anchors()` — enumerate installed anchors by
  name

- [ ] 12. Support the `blocklist` directory
  (`/etc/pki/ca-trust/source/blocklist/`) for negative-trust entries

- [ ] 13. Support non-PEM formats in `source/anchors/` (extracted
  `.p11-kit` trust files)

## Tests — Implemented

- [x] 14. `test_add_anchor_writes_and_refreshes` — verifies `tee` write
  followed by `update-ca-trust extract`

- [x] 15. `test_add_anchor_skipped_when_content_matches` — idempotency

- [x] 16. `test_refresh_dispatches_update_ca_trust`

## Tests — Gaps

- [ ] 17. Test `remove_anchor` — delete + refresh chain; SKIPPED when
  file absent

- [ ] 18. Test dry-run short-circuits for `add_anchor`, `remove_anchor`,
  `refresh`

- [ ] 19. Test `add_anchor` returns FAILED with exit_code/stderr when
  `tee` fails before attempting refresh

- [ ] 20. Test `add_anchor` returns FAILED with exit_code/stderr when
  refresh fails after a successful `tee`

## Follow-ups

- [ ] 21. **codecraftai-* reuse check**: `codecraftai-credentials` may already
  have certificate inspection helpers; evaluate before implementing
  tasks 10 / 11.
