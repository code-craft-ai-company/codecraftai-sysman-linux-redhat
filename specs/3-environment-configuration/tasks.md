# Implementation Plan

- [ ] 1. Create the `environmentconfiguration` module file
  - Create `source/packages/automojo/sysman/linux/redhat/environmentconfiguration.py`
  - Add module-level docstring describing purpose, the three file targets, and the
    coordinator-driven, file-operator-abstracted design
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9_

- [ ] 2. Define status enum and action result dataclass
  - Implement `EnvActionStatus` enum: `SUCCESS`, `FAILED`, `SKIPPED`, `DRY_RUN`
  - Implement `EnvActionResult` dataclass with fields: `status`, `path`, `name`, `value`,
    `message`
  - _Requirements: 1, 2, 3, 4, 5, 6_

- [ ] 3. Define `EnvConfigError` exception
  - Subclass `Exception`; used for unknown-user errors in query operations
  - _Requirements: 8.3_

- [ ] 4. Implement `EnvironmentConfiguratorConfig` dataclass
  - Fields: `dry_run: bool = False`, `elevated: bool = False`
  - _Requirements: 1.5, 2.5, 3.5, 4.4, 5.6, 6.5, 9_

- [ ] 5. Implement `_parse_export_lines(text)` private helper
  - Split on lines; skip blank and `#`-prefixed lines
  - Match `^export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$`
  - Strip surrounding double quotes from value
  - Return `dict[str, str]`
  - _Requirements: 7, 8_

- [ ] 6. Implement `_parse_env_lines(text)` private helper
  - Split on lines; skip blank and `#`-prefixed lines
  - Match `^([A-Za-z_][A-Za-z0-9_]*)=(.*)$`
  - Strip surrounding double quotes from value
  - Return `dict[str, str]`
  - _Requirements: 7_

- [ ] 7. Implement `_resolve_home(username)` private helper
  - Call `pwd.getpwnam(username).pw_dir`
  - Raise `EnvConfigError` on `KeyError`
  - _Requirements: 5.5, 6.4, 8.3_

- [ ] 8. Implement `_apply_set(lines, name, value, export)` private helper
  - Accept current file lines, variable name, new value, and bool indicating whether
    `export` keyword is used
  - Return `(new_lines, changed: bool, was_same_value: bool)` tuple
  - Scan for matching variable name; replace if different, return unchanged if same;
    append if absent
  - _Requirements: 1.2, 1.3, 3.2, 3.3, 5.2, 5.3_

- [ ] 9. Implement `_apply_unset(lines, name, export)` private helper
  - Return `(new_lines, changed: bool)` tuple
  - Scan for matching variable name; remove if found; return unchanged if absent
  - _Requirements: 2.1, 4.1, 6.1_

- [ ] 10. Implement `EnvironmentConfigurator.__init__`
  - Accept `IFileOperator` and `EnvironmentConfiguratorConfig`
  - Define class-level constants: `PROFILE_D = "/etc/profile.d"`,
    `ETC_ENV = "/etc/environment"`, `SHEBANG = "#!/usr/bin/env bash\n"`
  - _Requirements: 1, 2, 3, 4_

- [ ] 11. Implement `set_system_var(name, value, filename)`
  - Return `DRY_RUN` early when `dry_run=True` (still read file to check current state
    for accurate SKIPPED detection if desired, or skip read — keep simple: skip read
    in dry-run)
  - Build path `/etc/profile.d/<filename>`
  - Read file if it exists; create with shebang if it does not
  - Apply `_apply_set(lines, name, value, export=True)`
  - Return `SKIPPED` if `was_same_value`; write file and return `SUCCESS` otherwise
  - Pass `elevated=config.elevated` to `write_text`
  - _Requirements: 1_

- [ ] 12. Implement `unset_system_var(name, filename)`
  - Return `DRY_RUN` early when `dry_run=True`
  - Build path `/etc/profile.d/<filename>`
  - Return `SKIPPED` if file does not exist
  - Read file; apply `_apply_unset(lines, name, export=True)`
  - Return `SKIPPED` if not changed
  - If remaining non-shebang non-blank lines are empty: delete file
  - Otherwise write updated content; return `SUCCESS`
  - _Requirements: 2_

- [ ] 13. Implement `set_etc_env_var(name, value)`
  - Return `DRY_RUN` early when `dry_run=True`
  - Read `/etc/environment` if it exists; treat as empty if it does not
  - Apply `_apply_set(lines, name, value, export=False)`
  - Return `SKIPPED` if `was_same_value`; write and return `SUCCESS` otherwise
  - Pass `elevated=config.elevated` to `write_text`
  - _Requirements: 3_

- [ ] 14. Implement `unset_etc_env_var(name)`
  - Return `DRY_RUN` early when `dry_run=True`
  - Read `/etc/environment` if it exists; return `SKIPPED` if file does not exist
  - Apply `_apply_unset(lines, name, export=False)`
  - Return `SKIPPED` if not changed; write updated content and return `SUCCESS` otherwise
  - _Requirements: 4_

- [ ] 15. Implement `set_user_var(username, name, value, rc_file)`
  - Return `DRY_RUN` early when `dry_run=True`
  - Call `_resolve_home(username)`; return `FAILED` result on `EnvConfigError`
  - Build path `<home>/<rc_file>`
  - Read file if it exists; treat as empty if it does not
  - Apply `_apply_set(lines, name, value, export=True)`
  - Return `SKIPPED` if `was_same_value`; write and return `SUCCESS` otherwise
  - No elevation — user files are owned by the user
  - _Requirements: 5_

- [ ] 16. Implement `unset_user_var(username, name, rc_file)`
  - Return `DRY_RUN` early when `dry_run=True`
  - Call `_resolve_home(username)`; return `FAILED` result on `EnvConfigError`
  - Build path `<home>/<rc_file>`; return `SKIPPED` if file does not exist
  - Apply `_apply_unset(lines, name, export=True)`
  - Return `SKIPPED` if not changed; write updated content and return `SUCCESS` otherwise
  - _Requirements: 6_

- [ ] 17. Implement `query_system(filename)`
  - Build path `/etc/profile.d/<filename>`
  - Return empty dict if file does not exist
  - Read file; pass to `_parse_export_lines()`; return result
  - _Requirements: 7.1, 7.3, 7.4_

- [ ] 18. Implement `query_etc_env()`
  - Return empty dict if `/etc/environment` does not exist
  - Read file; pass to `_parse_env_lines()`; return result
  - _Requirements: 7.2, 7.3, 7.4_

- [ ] 19. Implement `query_user(username, rc_file)`
  - Call `_resolve_home(username)`; raise `EnvConfigError` on unknown user
  - Return empty dict if file does not exist
  - Read file; pass to `_parse_export_lines()`; return result
  - _Requirements: 8_

- [ ] 20. Create test module
  - Create `source/testroots/testbase/automojo/tests/sysman/linux/redhat/test_environmentconfiguration.py`
  - Implement `MockFileOperator` backed by `dict[str, str]` in-memory store
  - Implement `MockFileOperator.exists()`, `.read_text()`, `.write_text()`, `.delete()`
  - Record all write and delete calls for assertion
  - _Requirements: 1–9_

- [ ] 21. Write tests for `_parse_export_lines()` and `_parse_env_lines()`
  - Test: normal export line parsed correctly
  - Test: value with embedded double quotes parsed correctly
  - Test: blank lines and comments skipped
  - Test: line with no `=` skipped without error
  - Test: `_parse_env_lines` ignores `export` prefix
  - _Requirements: 7.4, 8.4_

- [ ] 22. Write tests for `set_system_var()` and `unset_system_var()`
  - Test set: variable absent — file written with export line, `SUCCESS`
  - Test set: variable present with same value — no write, `SKIPPED`
  - Test set: variable present with different value — line updated in place, `SUCCESS`
  - Test set: file does not exist — created with shebang, `SUCCESS`
  - Test set: dry-run — no write, `DRY_RUN`
  - Test set: elevated — `write_text` called with `elevated=True`
  - Test unset: variable present — line removed, `SUCCESS`
  - Test unset: variable absent — `SKIPPED`, no write
  - Test unset: file does not exist — `SKIPPED`
  - Test unset: only shebang remains after removal — file deleted, not written
  - Test unset: dry-run — no write or delete, `DRY_RUN`
  - _Requirements: 1, 2_

- [ ] 23. Write tests for `set_etc_env_var()` and `unset_etc_env_var()`
  - Test set: variable absent — appended, `SUCCESS`
  - Test set: variable present with same value — `SKIPPED`, no write
  - Test set: variable present with different value — updated in place, `SUCCESS`
  - Test set: dry-run — `DRY_RUN`, no write
  - Test unset: variable present — removed, `SUCCESS`
  - Test unset: variable absent — `SKIPPED`
  - Test unset: file does not exist — `SKIPPED`
  - Test unset: dry-run — `DRY_RUN`
  - _Requirements: 3, 4_

- [ ] 24. Write tests for `set_user_var()` and `unset_user_var()`
  - Test set: variable absent — export line written, `SUCCESS`
  - Test set: same value — `SKIPPED`
  - Test set: different value — updated, `SUCCESS`
  - Test set: unknown user — `FAILED` result returned, no write
  - Test set: dry-run — `DRY_RUN`, no write
  - Test unset: variable present — removed, `SUCCESS`
  - Test unset: variable absent — `SKIPPED`
  - Test unset: unknown user — `FAILED` result
  - Test unset: dry-run — `DRY_RUN`
  - _Requirements: 5, 6_

- [ ] 25. Write unit tests for `query_system()`, `query_etc_env()`, and `query_user()`
  - Test query_system: file with multiple exports — dict returned correctly
  - Test query_system: file not found — empty dict
  - Test query_etc_env: file with multiple NAME=value lines — dict returned correctly
  - Test query_etc_env: file not found — empty dict
  - Test query_user: file with exports — dict returned correctly
  - Test query_user: file not found — empty dict
  - Test query_user: unknown user — raises `EnvConfigError`
  - _Requirements: 7, 8_

- [ ]* 26. Write property test for set operation idempotency
  - **Property 1: Set Operation Idempotency**
  - **Validates: Requirements 1.2, 3.2, 5.2**

- [ ]* 27. Write property test for update in-place correctness
  - **Property 2: Update In-Place Correctness**
  - **Validates: Requirements 1.3, 3.3, 5.3**

- [ ]* 28. Write property test for unset operation idempotency
  - **Property 3: Unset Operation Idempotency**
  - **Validates: Requirements 2.2, 4.2, 6.2**

- [ ]* 29. Write property test for dry-run safety
  - **Property 4: Dry-Run Safety**
  - **Validates: Requirements 1.5, 2.5, 3.5, 4.4, 5.6, 6.5**

- [ ]* 30. Write property test for system file shebang creation
  - **Property 5: System File Shebang Creation**
  - **Validates: Requirements 1.4**

- [ ]* 31. Write property test for user RC file creation
  - **Property 6: User RC File Creation**
  - **Validates: Requirements 5.4**

- [ ]* 32. Write property test for empty file cleanup
  - **Property 7: Empty File Cleanup**
  - **Validates: Requirements 2.3**

- [ ]* 33. Write property test for user validation in set operations
  - **Property 8: User Validation for Set Operations**
  - **Validates: Requirements 5.5**

- [ ]* 34. Write property test for user validation in unset operations
  - **Property 9: User Validation for Unset Operations**
  - **Validates: Requirements 6.4**

- [ ]* 35. Write property test for user validation in query operations
  - **Property 10: User Validation for Query Operations**
  - **Validates: Requirements 8.3**

- [ ]* 36. Write property test for system variable format correctness
  - **Property 11: System Variable Format Correctness**
  - **Validates: Requirements 1.1**

- [ ]* 37. Write property test for /etc/environment format correctness
  - **Property 12: /etc/environment Format Correctness**
  - **Validates: Requirements 3.1**

- [ ]* 38. Write property test for user variable format correctness
  - **Property 13: User Variable Format Correctness**
  - **Validates: Requirements 5.1**

- [ ]* 39. Write property test for variable removal correctness
  - **Property 14: Variable Removal Correctness**
  - **Validates: Requirements 2.1, 4.1, 6.1**

- [ ]* 40. Write property test for query parsing correctness
  - **Property 15: Query Parsing Correctness**
  - **Validates: Requirements 7.1, 7.2, 8.1**

- [ ]* 41. Write property test for query non-existent file handling
  - **Property 16: Query Non-Existent File Handling**
  - **Validates: Requirements 7.3, 8.2**

- [ ]* 42. Write property test for parser robustness
  - **Property 17: Parser Robustness**
  - **Validates: Requirements 7.4, 8.4**

- [ ]* 43. Write property test for elevation in system writes
  - **Property 18: Elevation for System Writes**
  - **Validates: Requirements 9.1**

- [ ]* 44. Write property test for no elevation in queries
  - **Property 19: No Elevation for Queries**
  - **Validates: Requirements 9.2**

- [ ]* 45. Write property test for set-query round trip
  - **Property 20: Set-Query Round Trip**
  - **Validates: Requirements 1.1, 3.1, 5.1, 7.1, 7.2, 8.1**

- [ ] 46. Final Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.
