# Implementation Plan

- [ ] 1. Create the `sshserverconfiguration` module file
  - Create `source/packages/automojo/sysman/linux/redhat/sshserverconfiguration.py`
  - Add module-level docstring describing purpose, coordinator-driven design,
    and relationship to `security-compliance` and `user-group-management`
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11_

- [ ] 2. Define status enum and core result dataclasses
  - Implement `SshdActionStatus` enum with values: `SUCCESS`, `FAILED`,
    `SKIPPED`, `DRY_RUN`
  - Implement `SshdActionResult` dataclass with fields: `status`, `message`,
    `exit_code`, `stdout`, `stderr`, `command`, `path`
  - _Requirements: 3, 5, 7, 8, 10_

- [ ] 3. Define domain dataclasses
  - Implement `SshdServiceState` with fields: `active`, `enabled`, `present`,
    `main_pid`
  - Implement `SshdDropIn` with fields: `name`, `path`,
    `directive_value_pairs`, `valid`, `error`
  - Implement `SshdValidateResult` with fields: `ok`, `error_output`
  - Implement `HostKeyFingerprint` with fields: `type`, `bits`,
    `sha256_fingerprint`, `comment`, `valid`, `error`
  - _Requirements: 1, 4, 6, 9_

- [ ] 4. Define typed exceptions
  - Implement `SshdQueryError` with `exit_code` and `stderr` attributes
  - Implement `SshdNotInstalledError` with `message` attribute
  - _Requirements: 1.4, 2.3, 6.4_

- [ ] 5. Implement `SshServerManagerConfig` dataclass
  - Add `dry_run: bool = False` field
  - Add `elevated: bool = False` field
  - _Requirements: 3.6, 5.5, 7.5, 8.7, 10.4, 11_

- [ ] 6. Implement `SshServerManager.__init__`, `get_service_state()` and `get_effective_directive()`
  - Accept `ICommandRunner`, `IFileOperator`, and `SshServerManagerConfig` as
    constructor arguments; assign to instance variables in `__init__`
  - Implement `get_service_state()` using `systemctl is-active sshd` and
    `systemctl is-enabled sshd`; map well-known "not-found" outputs to
    `present=False`; raise `SshdQueryError` on other non-zero exit codes
  - Implement `get_effective_directive(name)` by invoking `sshd -T` with
    elevation and parsing the `<directive> <value>` line for the requested
    name; return `None` when the directive is absent
  - _Requirements: 1, 2.1_

- [ ] 7. Implement `get_full_effective_config()`
  - Invoke `sshd -T` with elevation via the command runner
  - Parse every `<directive> <value>` line into an ordered dict preserving
    insertion order
  - Raise `SshdQueryError` with captured stderr on non-zero exit code
  - Execute even when `dry_run=True` since the operation is read-only
  - _Requirements: 2.2, 2.3, 2.4_

- [ ] 8. Implement `set_directive_dropin()` and `delete_directive_dropin()`
  - For `set_directive_dropin()`: read current drop-in content via file
    operator; compute merged content preserving unrelated directives; if
    byte-identical to current return `SKIPPED`; otherwise write with mode
    `0o600`, owner/group `root`, and elevation
  - Invoke `sshd -t` after write; on failure restore previous content via
    file operator and return `SshdActionResult` with status `FAILED`
  - On success, return `SshdActionResult` with status `SUCCESS` and message
    indicating `systemctl reload sshd` is required
  - For `delete_directive_dropin()`: read current content; if directive is
    absent return `SKIPPED`; otherwise write file with the matching line
    removed; if the resulting file would be empty, delete the file instead;
    invoke `sshd -t` and roll back on failure
  - Honour `dry_run` on both methods; return `DRY_RUN` without writing
  - _Requirements: 3_

- [ ] 9. Implement `list_dropins()`
  - Use file operator `list_dir()` on `/etc/ssh/sshd_config.d/`
  - Return an empty list when the directory does not exist
  - For each `*.conf` file, read and parse directive-value pairs in file
    order; populate an `SshdDropIn` record
  - On parse failure, include the record with `valid=False` and the captured
    error
  - Do not use elevation (read-only query)
  - _Requirements: 4_

- [ ] 10. Implement `write_match_block()` and `remove_match_block()`
  - For `write_match_block()`: construct file content as `Match <spec>`
    followed by `<directive> <value>` lines for each pair; read existing
    drop-in; if byte-identical return `SKIPPED`; else write with mode
    `0o600`, owner/group `root`, and elevation
  - Invoke `sshd -t` after write; on failure restore previous content and
    return status `FAILED`; on success return status `SUCCESS`
  - For `remove_match_block()`: if the drop-in file does not exist return
    `SKIPPED`; else delete the file with elevation
  - Honour `dry_run` on both methods
  - _Requirements: 5_

- [ ] 11. Implement `validate_config()`
  - Invoke `sshd -t` via the command runner with elevation
  - On exit code 0 return `SshdValidateResult(ok=True, error_output=None)`
  - On non-zero exit code return `SshdValidateResult(ok=False,
    error_output=<stderr>)`
  - Raise `SshdNotInstalledError` when the runner reports that the `sshd`
    binary is not installed
  - Execute even when `dry_run=True` since validation is read-only
  - _Requirements: 6_

- [ ] 12. Implement `reload_config()`
  - Call `validate_config()` first; if it returns `ok=False` return
    `SshdActionResult` with status `FAILED` and the validation error without
    invoking `systemctl`
  - On validation success invoke `systemctl reload sshd` with elevation
  - Map exit code 0 to `SUCCESS`, non-zero to `FAILED` with exit code and
    stderr captured on the result
  - Honour `dry_run`; return `DRY_RUN` without executing any command
  - _Requirements: 7_

- [ ] 13. Implement `generate_host_key()` and `remove_host_key()`
  - For `generate_host_key(key_type, force=False)`: check whether
    `/etc/ssh/ssh_host_<type>_key` exists; if so and `force=False` return
    `SKIPPED`; if `force=True` delete the existing private and public key
    files via file operator with elevation
  - Invoke `ssh-keygen -t <type> -f /etc/ssh/ssh_host_<type>_key -N ''`
    with elevation
  - After generation, ensure the private key has mode `0o600` and the public
    key has mode `0o644`, owner and group `root`, via file operator
  - On success, invoke `ssh-keygen -l -f <path>.pub` and include the captured
    fingerprint in the result message
  - For `remove_host_key(key_type)`: delete both
    `/etc/ssh/ssh_host_<type>_key` and `/etc/ssh/ssh_host_<type>_key.pub`
    with elevation; return `SKIPPED` if neither exists
  - Honour `dry_run` on both methods
  - _Requirements: 8_

- [ ] 14. Implement `list_host_key_fingerprints()`
  - List `/etc/ssh/ssh_host_*_key.pub` via the file operator
  - For each file invoke `ssh-keygen -l -f <path>` and parse the output
    into a `HostKeyFingerprint` record (`type`, `bits`, `sha256_fingerprint`,
    `comment`)
  - On `ssh-keygen` failure include the record with `valid=False` and the
    captured error
  - Return an empty list when no files are found
  - _Requirements: 9_

- [ ] 15. Implement `ensure_banner()`
  - Read the existing file at the supplied path via the file operator; if the
    bytes match the supplied text byte-for-byte return `SKIPPED`
  - Otherwise write the file with mode `0o644`, owner/group `root`, and
    elevation
  - When `set_directive=True`, call `set_directive_dropin()` with
    `directive="Banner"` and `value=<path>`
  - Honour `dry_run`; return `DRY_RUN` without writing
  - _Requirements: 10_

- [ ] 16. Create test module and mock infrastructure
  - Create
    `source/testroots/testbase/automojo/tests/sysman/linux/redhat/test_sshserverconfiguration.py`
  - Implement `MockCommandRunner` that returns configurable `RunResult`
    values and records call history
  - Implement `MockFileOperator` that simulates an in-memory filesystem,
    records reads/writes/deletes, and exposes a rollback assertion helper
  - Set up hypothesis testing framework configuration
  - Create test fixtures for common test scenarios
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11_

- [ ] 17. Write property-based test for drop-in set byte-identical skip
  - **Property 1: Drop-In Set Byte-Identical Skip**
  - Generate random drop-in names, directives, and values
  - Pre-populate the mock file operator with the identical content
  - Verify `set_directive_dropin()` returns `SKIPPED` and no write is recorded
  - **Validates: Requirements 3.2**
  - _Requirements: 3_

- [ ] 18. Write property-based test for set-then-validate rollback
  - **Property 2: Set-then-Validate Rollback**
  - Generate random prior content and new content
  - Configure the mock runner to make `sshd -t` fail after the write
  - Verify the previous content is restored via the file operator and the
    result status is `FAILED`
  - **Validates: Requirements 3.4, 5.4**
  - _Requirements: 3, 5_

- [ ] 19. Write property-based test for delete absent directive skip
  - **Property 3: Delete Absent Directive Skip**
  - Generate random drop-in contents missing the target directive
  - Verify `delete_directive_dropin()` returns `SKIPPED` and no write or
    delete is recorded
  - **Validates: Requirements 3.3**
  - _Requirements: 3_

- [ ] 20. Write property-based test for list stability
  - **Property 4: List Stability**
  - Generate random sets of drop-in files in the mock file operator
  - Verify two consecutive `list_dropins()` calls return equivalent records
    in the same order
  - **Validates: Requirements 4.1, 4.2**
  - _Requirements: 4_

- [ ] 21. Write property-based test for match block byte-identical skip
  - **Property 5: Match Block Byte-Identical Skip**
  - Generate random match specs and directive-value pairs
  - Pre-populate the mock file operator with byte-identical content
  - Verify `write_match_block()` returns `SKIPPED` and no write is recorded
  - **Validates: Requirements 5.3**
  - _Requirements: 5_

- [ ] 22. Write property-based test for reload validates first
  - **Property 6: Reload Validates First**
  - Generate random validation outcomes (pass/fail)
  - Verify that `sshd -t` is always invoked before `systemctl reload sshd`
  - Verify that when validate fails, reload is not invoked and status is
    `FAILED`
  - **Validates: Requirements 7.1, 7.2**
  - _Requirements: 7_

- [ ] 23. Write property-based test for host-key generation force flag
  - **Property 7: Host-Key Generation Respects force=False**
  - Generate random key types and existence states
  - Verify `generate_host_key(force=False)` returns `SKIPPED` when the
    private key exists
  - Verify `generate_host_key(force=True)` deletes the existing pair before
    invoking `ssh-keygen`
  - **Validates: Requirements 8.2, 8.3**
  - _Requirements: 8_

- [ ] 24. Write property-based test for host-key file modes
  - **Property 8: Host-Key File Modes**
  - Generate random key types
  - Verify file operator writes record mode `0o600` on the private key and
    mode `0o644` on the public key, with owner and group `root`
  - **Validates: Requirements 8.5**
  - _Requirements: 8_

- [ ] 25. Write property-based test for banner byte-identical skip
  - **Property 9: Banner Byte-Identical Skip**
  - Generate random paths and texts
  - Pre-populate the mock file operator with byte-identical content
  - Verify `ensure_banner()` returns `SKIPPED` and no write is recorded
  - **Validates: Requirements 10.2**
  - _Requirements: 10_

- [ ] 26. Write property-based test for dry-run safety
  - **Property 10: Dry-Run Safety**
  - Generate random mutating operations (drop-in set/delete, match
    write/remove, reload, host-key generate/remove, banner ensure) with
    `dry_run=True`
  - Verify no mutating runner command and no file operator write or delete
    is recorded
  - Verify all results have status `DRY_RUN`
  - **Validates: Requirements 3.6, 5.5, 7.5, 8.7, 10.4**
  - _Requirements: 3, 5, 7, 8, 10_

- [ ] 27. Write property-based test for elevation consistency
  - **Property 11: Elevation Consistency**
  - Generate random operations with `elevated=True`/`False`
  - Verify mutating operations and privileged reads (`sshd -T`, `sshd -t`,
    `systemctl reload sshd`, `ssh-keygen` on `/etc/ssh/`, writes/deletes
    under `/etc/ssh/`) pass `elevated=True` when configured
  - Verify read-only file operator queries (list drop-ins, read banner) do
    not use elevation
  - **Validates: Requirements 11.1, 11.2, 11.3, 11.4**
  - _Requirements: 11_

- [ ] 28. Write property-based test for service state mapping
  - **Property 12: Service State Mapping**
  - Generate random combinations of `systemctl is-active sshd` and
    `systemctl is-enabled sshd` outputs
  - Verify the returned `SshdServiceState` correctly reflects `active`,
    `enabled`, and `present`
  - **Validates: Requirements 1.1, 1.2, 1.3**
  - _Requirements: 1_

- [ ] 29. Write unit tests for `get_service_state()`
  - Test: active and enabled — returns populated state with `present=True`
  - Test: inactive but enabled — returns `active=False`, `enabled=True`
  - Test: unit not present — returns record with `present=False`, other
    fields `None`
  - Test: unexpected systemctl exit code — raises `SshdQueryError`
  - _Requirements: 1_

- [ ] 30. Write unit tests for `get_effective_directive()` and `get_full_effective_config()`
  - Test: directive present — returns value string
  - Test: directive absent — returns `None`
  - Test: full config — returns ordered dict with expected keys and values
  - Test: `sshd -T` non-zero exit code — raises `SshdQueryError` with stderr
  - Test: dry-run mode — `sshd -T` still executes (read-only)
  - _Requirements: 2_

- [ ] 31. Write unit tests for `set_directive_dropin()` and `delete_directive_dropin()`
  - Test: new drop-in created — file operator called with correct path,
    mode, owner/group, and elevation; status `SUCCESS` with reload-required
    message
  - Test: directive already identical — returns `SKIPPED`; no write recorded
  - Test: `sshd -t` fails after write — previous content restored; status
    `FAILED` with stderr captured
  - Test: delete when directive present — file rewritten or deleted if empty
  - Test: delete when directive absent — returns `SKIPPED`; no write recorded
  - Test: dry-run mode — returns `DRY_RUN`, no write recorded
  - _Requirements: 3_

- [ ] 32. Write unit tests for `list_dropins()`
  - Test: multiple `*.conf` files — returns list of `SshdDropIn` records in
    file-order with correct directive-value pairs
  - Test: empty directory — returns empty list
  - Test: directory does not exist — returns empty list
  - Test: malformed file — record included with `valid=False` and error
    captured
  - _Requirements: 4_

- [ ] 33. Write unit tests for `write_match_block()` and `remove_match_block()`
  - Test: write new match block — file operator called with correct content,
    mode `0o600`, owner/group `root`, elevation; status `SUCCESS`
  - Test: write identical match block — returns `SKIPPED`; no write recorded
  - Test: validate fails after write — rollback restores previous content;
    status `FAILED`
  - Test: remove existing match block — file operator delete called;
    status `SUCCESS`
  - Test: remove absent match block — returns `SKIPPED`; no delete recorded
  - Test: dry-run mode on both — returns `DRY_RUN`; no write or delete
    recorded
  - _Requirements: 5_

- [ ] 34. Write unit tests for `validate_config()`
  - Test: `sshd -t` exit 0 — returns `ok=True, error_output=None`
  - Test: `sshd -t` non-zero — returns `ok=False` with stderr captured
  - Test: sshd not installed — raises `SshdNotInstalledError`
  - Test: dry-run mode — still executes (read-only)
  - _Requirements: 6_

- [ ] 35. Write unit tests for `reload_config()`
  - Test: validate passes, reload succeeds — returns `SUCCESS`
  - Test: validate fails — reload not invoked; returns `FAILED` with
    validation error
  - Test: validate passes, reload fails — returns `FAILED` with exit code and
    stderr
  - Test: dry-run mode — returns `DRY_RUN`; no command recorded
  - _Requirements: 7_

- [ ] 36. Write unit tests for `generate_host_key()` and `remove_host_key()`
  - Test: key does not exist — `ssh-keygen` called with correct args and
    elevation; mode `0o600` on private and `0o644` on public; status
    `SUCCESS` with fingerprint in message
  - Test: key exists with `force=False` — returns `SKIPPED`; `ssh-keygen`
    not called
  - Test: key exists with `force=True` — existing private and public files
    deleted via file operator; then `ssh-keygen` called
  - Test: remove existing host key — both `.key` and `.pub` deleted with
    elevation; status `SUCCESS`
  - Test: remove absent host key — returns `SKIPPED`; no delete recorded
  - Test: dry-run mode on both — returns `DRY_RUN`; no file or command side
    effect
  - _Requirements: 8_

- [ ] 37. Write unit tests for `list_host_key_fingerprints()`
  - Test: multiple host keys — returns list of `HostKeyFingerprint` records
    with type, bits, sha256_fingerprint, comment populated
  - Test: no host keys — returns empty list
  - Test: `ssh-keygen -l` fails on a file — record included with
    `valid=False` and error captured
  - _Requirements: 9_

- [ ] 38. Write unit tests for `ensure_banner()`
  - Test: file does not exist — written with mode `0o644`, owner/group
    `root`, elevation; status `SUCCESS`
  - Test: file content byte-identical — returns `SKIPPED`; no write recorded
  - Test: file content differs — written; status `SUCCESS`
  - Test: `set_directive=True` — delegates to `set_directive_dropin()` with
    `Banner <path>`
  - Test: dry-run mode — returns `DRY_RUN`; no write recorded
  - _Requirements: 10_

- [ ] 39. Write unit tests for elevation propagation
  - Test: `elevated=True` config — mutating commands (`systemctl reload
    sshd`, `ssh-keygen`) invoked with `elevated=True`
  - Test: `elevated=True` config — `sshd -T` and `sshd -t` invoked with
    `elevated=True`
  - Test: `elevated=True` config — mutating file operator writes and deletes
    invoked with `elevated=True`
  - Test: `elevated=True` config — read-only file operator listings invoked
    with `elevated=False`
  - _Requirements: 11_

- [ ] 40. Checkpoint - Ensure all tests pass
  - Run all unit tests and property-based tests
  - Verify all properties pass with 100+ iterations
  - Ensure all tests pass, ask the user if questions arise
