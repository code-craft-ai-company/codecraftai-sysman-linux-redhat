# Implementation Plan — ACL Management

All tasks are unchecked — new feature.

## Module and Models

- [ ] 1. Create `source/packages/automojo/sysman/linux/redhat/aclmanagement.py`
- [ ] 2. Define `AclActionStatus`, `AclManagerConfig`,
  `AclActionResult`, `AclEntry`, `AclSet`, `FileAttributes`,
  `FileCapabilities` dataclasses

## Helpers

- [ ] 3. Implement `__init__` accepting runner + optional config
- [ ] 4. Implement `_dispatch(command, detail, path)` with dry-run
  short-circuit + result mapping
- [ ] 5. Implement `_is_directory(path)` — `stat -c %F <path>` via
  runner; used by default-ACL and recursive-attr guards
- [ ] 6. Implement `_entry_to_spec(entry)` helper producing the
  canonical `setfacl -m` spec string

## POSIX ACLs

- [ ] 7. Implement `query_acl(path)` — `getfacl --absolute-names
  --omit-header <path>` parsed into `AclSet`; `None` when path
  absent
  - _Requirements: 1.1, 1.2_

- [ ] 8. Implement `set_acl(path, entries, recursive=False)` with
  idempotency via `query_acl` comparison
  - _Requirements: 2.1, 2.4, 2.5_

- [ ] 9. Implement `remove_acl_entries(path, entries,
  recursive=False)` — `setfacl -x`
  - _Requirements: 2.2_

- [ ] 10. Implement `clear_acl(path, recursive=False)` —
  `setfacl -b`
  - _Requirements: 2.3_

- [ ] 11. Implement `clear_default_acl(path)` — `setfacl -k`;
  requires path to be a directory (`_is_directory` guard)
  - _Requirements: 3.1, 3.3_

## Extended Attributes

- [ ] 12. Implement `query_attrs(path)` — `lsattr -d <path>`
  parsed into `FileAttributes`
  - _Requirements: 4.1_

- [ ] 13. Implement `add_attrs(path, flags, recursive=False)` —
  `chattr [-R] +<flags>`; recursive requires directory
  - _Requirements: 4.2, 4.6_

- [ ] 14. Implement `remove_attrs(path, flags, recursive=False)` —
  `chattr [-R] -<flags>`
  - _Requirements: 4.3_

- [ ] 15. Implement `set_attrs(path, flags, recursive=False)` —
  `chattr [-R] =<flags>`
  - _Requirements: 4.4, 4.5_

## File Capabilities

- [ ] 16. Implement `query_caps(path)` — `getcap <path>` parsed
  into `FileCapabilities`
  - _Requirements: 5.1_

- [ ] 17. Implement `set_caps(path, caps)` — `setcap '<spec>'
  <path>`; SKIPPED when already matching
  - _Requirements: 5.2, 5.4_

- [ ] 18. Implement `clear_caps(path)` — `setcap -r <path>`
  - _Requirements: 5.3_

## Bulk Enumeration

- [ ] 19. Implement `enumerate_acls(root)` — `getfacl -R
  --absolute-names --omit-header <root>` parsed into `dict[str,
  AclSet]`
  - _Requirements: 6.1_

- [ ] 20. Implement `enumerate_attrs(root)` — `lsattr -R <root>`
  parsed into `dict[str, FileAttributes]`
  - _Requirements: 6.2_

- [ ] 21. Implement `enumerate_caps(root)` — `getcap -r <root>`
  parsed into `list[FileCapabilities]`
  - _Requirements: 6.3_

## Tests

- [ ] 22. Create `test_aclmanagement.py` with `_MockRunner`
- [ ] 23. Parser tests for `getfacl`, `lsattr -d`, `getcap`
- [ ] 24. Tests for `set_acl` idempotency (compare entries to
  current)
- [ ] 25. Tests for default-ACL directory guard (non-dir path →
  FAILED)
- [ ] 26. Tests for `add_attrs(recursive=True)` on non-directory
  returning FAILED
- [ ] 27. Tests for `set_caps` SKIPPED when canonical form matches
- [ ] 28. Tests for `enumerate_*` batch queries
- [ ] 29. Dry-run sweep
- [ ] 30. Failure-mapping sweep

## Follow-ups

- [ ] 31. **automojo-* reuse check**: no obvious sibling helpers
  for POSIX ACLs / capabilities; verify before extending.
