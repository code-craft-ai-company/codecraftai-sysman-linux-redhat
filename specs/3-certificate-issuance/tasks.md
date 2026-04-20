# Implementation Plan — Certificate Issuance

All tasks are unchecked — new feature.

## Module and Models

- [ ] 1. Create `source/packages/automojo/sysman/linux/redhat/certificateissuance.py`
- [ ] 2. Define `CertificateActionStatus`,
  `CertificateIssuanceConfig`, `CertificateActionResult`,
  `SubjectSpec`, `CsrSpec`, `CsrInfo`, `CertificateInfo`,
  `CertmongerTracking`, `AcmeIssuanceSpec` dataclasses
- [ ] 3. Define `CertificateCredentialError` and
  `AcmeRequiredArgumentError` as `SemanticError` subtypes

## Helpers

- [ ] 4. Implement `__init__` accepting runner + optional config
- [ ] 5. Implement `_exists(path)` — `test -e <path>` via runner
- [ ] 6. Implement `_dispatch(command, detail=None, stdin=None,
  path=None)` with dry-run short-circuit + SANITISED command
  recording when `stdin` carries credential bytes
- [ ] 7. Implement `_write_temp_openssl_config(spec)` — `tee` to a
  temp path, `try/finally rm -f` pattern consumers apply around it
- [ ] 8. Implement `_chmod(path, mode)` — `chmod <octal> <path>`

## Keys

- [ ] 9. Implement `generate_key(path, algorithm, bits_or_curve,
  credential=None, overwrite=False)` — RSA / EC branches;
  passphrase via `-aes256 -pass stdin`; chmod 0600 post-write;
  SKIPPED when exists and not overwriting
  - _Requirements: 1_

## CSR

- [ ] 10. Implement `generate_csr(spec, credential=None,
  overwrite=False)` — temp-config + guaranteed cleanup;
  `-passin stdin` when key is encrypted
  - _Requirements: 2.1, 2.2, 2.3_

- [ ] 11. Implement `inspect_csr(path)` — `openssl req -noout
  -text` parsed into `CsrInfo`
  - _Requirements: 2.4_

## Self-signed

- [ ] 12. Implement `generate_self_signed(spec, credential=None,
  overwrite=False, days=365)` — `openssl req -x509 -new -key`;
  chmod 0644 post-write
  - _Requirements: 3_

## Inspection

- [ ] 13. Implement `inspect_certificate(path)` — returns
  `CertificateInfo` or `None`
  - _Requirements: 4.1, 4.2_

- [ ] 14. Implement `expires_in(path)` — returns `timedelta`
  (negative when expired); raises `FileNotFoundError` when absent
  - _Requirements: 4.3_

## certmonger

- [ ] 15. Implement `list_certmonger()` — `getcert list` parsed
  into `list[CertmongerTracking]`
  - _Requirements: 5.2_

- [ ] 16. Implement `request_certmonger(spec, cert_path,
  key_path, ca=None, profile=None)` — SKIPPED when existing
  tracking matches
  - _Requirements: 5.1, 5.4_

- [ ] 17. Implement `stop_certmonger(cert_path)` — SKIPPED when
  no tracking matches
  - _Requirements: 5.3_

## ACME

- [ ] 18. Implement `request_acme(spec)` — method dispatch
  (webroot / standalone / dns-<plugin>) with renew-after-days
  SKIPPED branch
  - _Requirements: 6.1, 6.2, 6.3, 6.4_

- [ ] 19. Implement `renew_acme(cert_name, force=False)` —
  `certbot renew --cert-name <name> [--force-renewal]`
  - _Requirements: 6.5_

## Tests

- [ ] 20. Create `test_certificateissuance.py` with `_MockRunner`
  + `_MockCredential`
- [ ] 21. Parser tests for CSR text, X.509 text,
  `openssl x509 -fingerprint`, `getcert list`
- [ ] 22. Tests for `generate_key` RSA / EC / passphrase variants;
  **assert no passphrase on argv**
- [ ] 23. Tests for `generate_csr` temp-file cleanup on success
  AND on failure
- [ ] 24. Tests for `generate_self_signed` overwrite semantics +
  chmod 0644
- [ ] 25. Tests for `inspect_certificate` returning `None` on
  missing file
- [ ] 26. Tests for `expires_in` positive / negative deltas
- [ ] 27. Tests for certmonger idempotency (existing tracking →
  SKIPPED)
- [ ] 28. Tests for ACME renewal-threshold SKIPPED path
- [ ] 29. Dry-run sweep across every mutating method
- [ ] 30. Failure-mapping sweep

## Follow-ups

- [ ] 31. **automojo-* reuse check**: route every `credential`
  parameter through
  `automojo.credentials.sshcredential.SshCredential` (or an
  equivalent credential abstraction).
