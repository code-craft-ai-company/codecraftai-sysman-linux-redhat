# Implementation Plan — Samba Server Management

All tasks are unchecked — new feature.

## Module and Models

- [ ] 1. Create `source/packages/automojo/sysman/linux/redhat/sambaservermanagement.py`
- [ ] 2. Define `SambaActionStatus`, `SambaServerManagerConfig`,
  `SambaActionResult`, `ShareSpec`, `ShareInfo`,
  `SambaValidateResult`, `SambaUserInfo` dataclasses
- [ ] 3. Define `SambaCredentialError(SemanticError)`

## Helpers

- [ ] 4. Implement `__init__(runner, config=None, service_manager=None)`
- [ ] 5. Implement `_read_smb_conf()` → ordered list of (header,
  directives dict); helper for block-boundary edits
- [ ] 6. Implement `_write_smb_conf(sections)` via `tee`
- [ ] 7. Implement `_dispatch(command, detail=None, stdin=None,
  share=None)` with dry-run short-circuit + sanitised command
  recording when stdin carries credential bytes

## Global Directives

- [ ] 8. Implement `get_globals()` / `set_global(key, value)` /
  `remove_global(key)` with idempotency and preservation of other
  sections
  - _Requirements: 1_

## Shares

- [ ] 9. Implement `list_shares()` — every `[<name>]` block except
  reserved names
  - _Requirements: 2.4_

- [ ] 10. Implement `query_share(name)` — parsed block or `None`
  - _Requirements: 2.3_

- [ ] 11. Implement `define_share(spec)` — idempotent upsert;
  inserts new block at end when absent
  - _Requirements: 2.1, 2.5_

- [ ] 12. Implement `update_share(spec)` — replace directives of
  an existing block atomically
  - _Requirements: 2.5_

- [ ] 13. Implement `remove_share(name)` — strip the entire block
  (header + directives until next section header); SKIPPED when
  absent
  - _Requirements: 2.2_

## Validation, Reload, Restart

- [ ] 14. Implement `validate_config()` — `testparm -s
  --suppress-prompt` parsed into `SambaValidateResult`
  - _Requirements: 3_

- [ ] 15. Implement `reload()` — validate-before-delegate;
  FAILED without touching service if validation fails; delegates
  `smb` + optionally `nmb`
  - _Requirements: 4_

- [ ] 16. Implement `restart(services=("smb", "nmb"))` — delegates
  each to `service_manager.restart(<name>)`

## Samba Users

- [ ] 17. Implement `list_samba_users()` — `pdbedit -L` parsed
  into `list[SambaUserInfo]`
  - _Requirements: 5.4_

- [ ] 18. Implement `add_samba_user(username, credential)` —
  `smbpasswd -a -s <username>` with two-line stdin; raises
  `SambaCredentialError` when credential missing
  - _Requirements: 5.1, 5.5, 7.4_

- [ ] 19. Implement `delete_samba_user(username)` — `smbpasswd -x`
  - _Requirements: 5.2_

- [ ] 20. Implement `enable_samba_user(username)` /
  `disable_samba_user(username)` — `smbpasswd -e|-d`
  - _Requirements: 5.3_

## AD

- [ ] 21. Implement `join_ad(domain, admin_credential)` —
  `net ads join -U <user>` with password on stdin; sanitised
  command recording
  - _Requirements: 6.1, 7.4_

- [ ] 22. Implement `leave_ad(admin_credential)` — `net ads leave
  -U <user>` with password on stdin
  - _Requirements: 6.3_

- [ ] 23. Implement `test_join()` — `net ads testjoin` → `bool`
  - _Requirements: 6.2_

- [ ] 24. Implement `list_trusted_domains()` — `wbinfo
  --trusted-domains` → `list[str]`
  - _Requirements: 6.4_

## Tests

- [ ] 25. Create `test_sambaservermanagement.py` with
  `_MockRunner`, `_MockServiceManager`, `_MockCredential`
- [ ] 26. Parser tests for `testparm`, `pdbedit -L`,
  `wbinfo --trusted-domains`
- [ ] 27. Tests for global-directive upsert / remove idempotency
- [ ] 28. Tests for share CRUD including block-boundary strip in
  `remove_share`
- [ ] 29. **Assert-no-password-on-argv** sweep for
  `add_samba_user`, `join_ad`, `leave_ad`
- [ ] 30. Tests that `reload()` never delegates when validation
  fails
- [ ] 31. Test that `restart()` delegates to `ServiceManager` only
- [ ] 32. Dry-run sweep
- [ ] 33. Failure-mapping sweep

## Follow-ups

- [ ] 34. **automojo-* reuse check**: route `admin_credential` /
  Samba-user passwords through
  `automojo.credentials.sshcredential.SshCredential` (or an
  equivalent credential abstraction).  Do NOT accept plaintext
  passphrase strings on the public API.
