# Implementation Plan

- [ ] 1. Create the `tunedmanagement` module file
  - Create `source/packages/automojo/sysman/linux/redhat/tunedmanagement.py`
  - Add module-level docstring describing purpose and coordinator-driven design
  - Declare imports for `dataclasses`, `enum`, `os`, `typing`, the
    `ICommandRunner`, `IFileOperator`, and `RunResult` interface types
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10_

- [ ] 2. Define status enum and result dataclasses
  - Implement `TunedActionStatus` enum with values: `SUCCESS`, `FAILED`,
    `SKIPPED`, `DRY_RUN`
  - Implement `TunedActionResult` dataclass with fields: `status`, `action`,
    `target`, `exit_code`, `stdout`, `stderr`, `command`, `error_message`
  - Implement `TunedServiceState` dataclass with fields: `present`, `active`,
    `enabled`
  - Implement `TunedProfile` dataclass with fields: `name`, `description`,
    `active`
  - Implement `TunedVerifyResult` dataclass with fields: `success`,
    `mismatches`, `stdout`
  - Use explicit type hints on all fields; no mutable default values
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9_

- [ ] 3. Define typed exceptions
  - Implement `TunedQueryError` with `exit_code`, `stderr`, and `command`
    attributes
  - Docstring each exception with Sphinx-style `:raises:` documentation
  - _Requirements: 1, 3, 4, 7, 8_

- [ ] 4. Implement `TunedManagerConfig` dataclass
  - Add `dry_run: bool = False` field
  - Add `elevated: bool = False` field
  - _Requirements: 2.5, 5.6, 6.4, 9.6, 10_

- [ ] 5. Implement `TunedManager.__init__` and `get_service_state()`
  - Accept `ICommandRunner`, `IFileOperator`, and `TunedManagerConfig` as
    constructor arguments; initialize all instance variables in `__init__`
  - Implement `get_service_state()` using `systemctl is-active tuned` and
    `systemctl is-enabled tuned` without elevation
  - Map `is-active` exit code `0` to `active=True`, exit code `3` to
    `active=False`
  - Map `is-enabled` exit code `0` to `enabled=True`, exit code `1` to
    `enabled=False`
  - Detect unit-not-present via stderr pattern and return
    `TunedServiceState(present=False, active=None, enabled=None)`
  - Raise `TunedQueryError` for any other non-zero exit/stderr combination
  - _Requirements: 1_

- [ ] 6. Implement `lifecycle_action()` for the tuned service
  - Accept `action` parameter restricted to `start`, `stop`, `restart`,
    `enable`, `disable`
  - Call `get_service_state()` first; return `SKIPPED` when the service is
    already in the requested state (e.g. `start` while `active=True`)
  - Build the `systemctl <action> tuned` command
  - Honour `dry_run` flag; return `DRY_RUN` result without calling runner
  - Pass `elevated` flag to runner; parse result into `TunedActionResult`
  - Return `FAILED` with exit code and stderr on runner non-zero
  - _Requirements: 2, 10_

- [ ] 7. Implement `list_profiles()` and `get_active_profile()`
  - `list_profiles()`: invoke `tuned-adm list` without elevation; parse each
    profile line into `TunedProfile(name, description, active)`; populate
    `active=True` on the entry matching the "Current active profile" line
  - `get_active_profile()`: invoke `tuned-adm active` without elevation; parse
    the output; return the profile name on success, `None` when no profile is
    active
  - Raise `TunedQueryError` when either command exits non-zero
  - _Requirements: 3, 4_

- [ ] 8. Implement `recommend_profile()`
  - Invoke `tuned-adm recommend` without elevation
  - Return the first non-empty line of stdout as the recommended profile name
  - Return `None` when output is empty
  - Raise `TunedQueryError` on non-zero exit
  - _Requirements: 7_

- [ ] 9. Implement `apply_profile()`
  - Call `get_service_state()`; if `active` is not `True` return `FAILED`
    with `error_message` stating tuned service is not active, and do not call
    the runner
  - Call `get_active_profile()`; if it equals `name` return `SKIPPED` without
    calling the runner
  - Call `list_profiles()`; if `name` is not present return `FAILED` with an
    error message listing the missing name, and do not call the runner
  - Build `tuned-adm profile <name>` command
  - Honour `dry_run` flag; return `DRY_RUN` result without calling runner
  - Pass `elevated` flag to runner; parse result into `TunedActionResult`
  - _Requirements: 5, 10_

- [ ] 10. Implement `profile_off()`
  - Call `get_active_profile()`; return `SKIPPED` when no profile is active
  - Build `tuned-adm off` command
  - Honour `dry_run` flag; return `DRY_RUN` result without calling runner
  - Pass `elevated` flag to runner; parse result into `TunedActionResult`
  - _Requirements: 6, 10_

- [ ] 11. Implement `verify_profile()`
  - Invoke `tuned-adm verify` without elevation
  - Map exit code `0` to `TunedVerifyResult(success=True, mismatches=[], stdout=...)`
  - Map exit code `1` to `TunedVerifyResult(success=False, mismatches=<parsed drift lines>, stdout=...)`
  - Raise `TunedQueryError` for any other exit code
  - _Requirements: 8_

- [ ] 12. Implement `install_custom_profile()`
  - Accept `name`, `body: bytes`, optional `payload: dict[str, tuple[bytes, int]] | None`
    (path relative to the profile dir → `(data, mode)`); default to `None` and
    materialize an empty mapping inside the method
  - Compute the profile directory path `/etc/tuned/<name>/`
  - When the directory exists and `tuned.conf` content equals `body` byte-for-byte
    AND no additional payload is requested, return `SKIPPED`
  - Honour `dry_run` flag; return `DRY_RUN` without calling the file operator
  - Via `IFileOperator`: create the directory with mode `0755`, write
    `tuned.conf` with mode `0644`, owner `root`, group `root`, and write each
    payload file with its supplied mode, all with `elevated` as configured
  - Return `SUCCESS` on completion; surface file operator errors as `FAILED`
    with `error_message`
  - _Requirements: 9, 10_

- [ ] 13. Implement `remove_custom_profile()`
  - Compute the profile directory path `/etc/tuned/<name>/`
  - When the directory does not exist return `SKIPPED`
  - Call `get_active_profile()`; when it equals `name` return `FAILED` with
    `error_message` stating the profile must be deactivated first
  - Honour `dry_run` flag; return `DRY_RUN` without calling the file operator
  - Via `IFileOperator.remove_tree`, recursively delete the directory with
    `elevated` as configured
  - Return `SUCCESS` on completion; surface file operator errors as `FAILED`
  - _Requirements: 9, 10_

- [ ] 14. Create test module and mock infrastructure
  - Create `source/testroots/testbase/automojo/tests/sysman/linux/redhat/test_tunedmanagement.py`
  - Implement `MockCommandRunner` that returns configurable `RunResult` values
    keyed on command args, and records invocations with their `elevated` flag
  - Implement `MockFileOperator` that maintains an in-memory filesystem and
    records invocations with their `elevated` flag
  - Set up hypothesis testing framework configuration
  - Create test fixtures for common test scenarios (service active/inactive,
    known profiles, active profile set/unset)
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10_

- [ ] 15. Write property-based test for apply-profile idempotency
  - **Property 1: Apply-Profile Idempotency**
  - Generate random profile names and configure the mocks so `get_active_profile`
    returns the same name
  - Verify `apply_profile(name)` returns `SKIPPED` and the runner was not
    invoked with `tuned-adm profile`
  - **Validates: Requirements 5.3**
  - _Requirements: 5_

- [ ] 16. Write property-based test for apply-when-service-not-active failure
  - **Property 2: Apply-When-Service-Not-Active Fails Cleanly**
  - Generate random profile names with `get_service_state` returning
    `active=False`
  - Verify `apply_profile` returns `FAILED`, error message mentions service
    not active, and `tuned-adm profile` was not invoked
  - **Validates: Requirements 5.2**
  - _Requirements: 5_

- [ ] 17. Write property-based test for profile-not-available failure
  - **Property 3: Profile-Not-Available Fails Cleanly**
  - Generate random missing names (not in the list_profiles fixture)
  - Verify `apply_profile` returns `FAILED`, error message mentions the
    missing name, and `tuned-adm profile` was not invoked
  - **Validates: Requirements 5.4**
  - _Requirements: 5_

- [ ] 18. Write property-based test for off-when-no-profile skip
  - **Property 4: Off-When-No-Profile Skip**
  - Configure `get_active_profile` to return `None`
  - Verify `profile_off()` returns `SKIPPED` and `tuned-adm off` was not
    invoked
  - **Validates: Requirements 6.2**
  - _Requirements: 6_

- [ ] 19. Write property-based test for custom profile byte-for-byte install skip
  - **Property 5: Custom Profile Install Byte-for-Byte Skip**
  - Generate random `name` and `body: bytes`; pre-populate the mock file
    operator with an identical `/etc/tuned/<name>/tuned.conf`
  - Verify `install_custom_profile(name, body)` returns `SKIPPED` and no
    write was recorded
  - **Validates: Requirements 9.3**
  - _Requirements: 9_

- [ ] 20. Write property-based test for custom profile remove-when-absent skip
  - **Property 6: Custom Profile Remove When Absent Skip**
  - Generate random names with no `/etc/tuned/<name>/` in the mock
  - Verify `remove_custom_profile(name)` returns `SKIPPED` and no delete was
    recorded
  - **Validates: Requirements 9.4**
  - _Requirements: 9_

- [ ] 21. Write property-based test for active profile removal refusal
  - **Property 7: Active Profile Removal Refusal**
  - Generate random `name`; configure `get_active_profile` to return the
    same name and pre-populate the directory
  - Verify `remove_custom_profile(name)` returns `FAILED`, error message
    mentions deactivation, and no delete was recorded
  - **Validates: Requirements 9.5**
  - _Requirements: 9_

- [ ] 22. Write property-based test for dry-run safety
  - **Property 8: Dry-Run Safety**
  - Generate random mutating operations (lifecycle_action, apply_profile,
    profile_off, install_custom_profile, remove_custom_profile) with
    `dry_run=True`
  - Verify no mutating command was sent to the runner, no write or delete
    was sent to the file operator, and every result has `DRY_RUN` status
  - **Validates: Requirements 2.5, 5.6, 6.4, 9.6**
  - _Requirements: 2, 5, 6, 9_

- [ ] 23. Write property-based test for elevation consistency
  - **Property 9: Elevation Consistency**
  - Generate random mutating operations with `elevated=True` / `elevated=False`
  - Verify mutating commands and file operator writes / deletes receive
    the elevated flag as configured
  - Verify read-only queries (`systemctl is-*`, `tuned-adm list`, `active`,
    `recommend`, `verify`) never use elevation
  - **Validates: Requirements 10.1, 10.2, 10.3**
  - _Requirements: 10_

- [ ] 24. Write property-based test for status mapping correctness
  - **Property 10: Status Mapping Correctness**
  - Generate random exit codes from the command runner
  - Verify `0` maps to `SUCCESS`, non-zero (when not a skip/dry-run) maps to
    `FAILED` with exit code and stderr populated
  - Verify `get_service_state` maps `is-active=0` → `active=True`, `=3` →
    `active=False`; `is-enabled=0` → `enabled=True`, `=1` → `enabled=False`
  - **Validates: Requirements 1.2, 2.3, 2.4, 5.5, 6.3, 8.2, 8.3**
  - _Requirements: 1, 2, 5, 6, 8_

- [ ] 25. Write unit tests for `get_service_state()`
  - Test: active & enabled — returns `TunedServiceState(present=True, active=True, enabled=True)`
  - Test: inactive (`is-active` exit 3) — returns `active=False`
  - Test: disabled (`is-enabled` exit 1) — returns `enabled=False`
  - Test: unit not present (stderr indicates not-found) — returns `present=False`
  - Test: unexpected exit code — raises `TunedQueryError`
  - _Requirements: 1_

- [ ] 26. Write unit tests for `lifecycle_action()`
  - Test: start when inactive — runner called with `systemctl start tuned`,
    returns `SUCCESS`
  - Test: start when already active — returns `SKIPPED`, runner not called
  - Test: stop failure — returns `FAILED` with exit code and stderr
  - Test: dry-run mode — returns `DRY_RUN`, runner not called
  - Test: elevated mode — runner called with `elevated=True`
  - _Requirements: 2, 10_

- [ ] 27. Write unit tests for `list_profiles()` and `get_active_profile()`
  - Test: list_profiles parses names, descriptions, and active marker
  - Test: list_profiles non-zero exit — raises `TunedQueryError`
  - Test: get_active_profile returns profile name when active
  - Test: get_active_profile returns `None` when no profile active
  - Test: get_active_profile non-zero exit — raises `TunedQueryError`
  - _Requirements: 3, 4_

- [ ] 28. Write unit tests for `recommend_profile()`
  - Test: recommend returns first non-empty line as name
  - Test: recommend with empty output returns `None`
  - Test: recommend non-zero exit — raises `TunedQueryError`
  - _Requirements: 7_

- [ ] 29. Write unit tests for `apply_profile()`
  - Test: service inactive — returns `FAILED` with service-not-active message,
    runner not invoked with `tuned-adm profile`
  - Test: profile already active — returns `SKIPPED`, runner not called
  - Test: profile not in list — returns `FAILED` with missing-name message
  - Test: happy path — runner called with `tuned-adm profile <name>` and
    `elevated=True`, returns `SUCCESS`
  - Test: apply failure — returns `FAILED` with exit code and stderr
  - Test: dry-run mode — returns `DRY_RUN`, runner not called
  - _Requirements: 5, 10_

- [ ] 30. Write unit tests for `profile_off()`
  - Test: no active profile — returns `SKIPPED`, runner not called
  - Test: happy path — runner called with `tuned-adm off` and `elevated=True`,
    returns `SUCCESS`
  - Test: off failure — returns `FAILED` with exit code and stderr
  - Test: dry-run mode — returns `DRY_RUN`, runner not called
  - _Requirements: 6, 10_

- [ ] 31. Write unit tests for `verify_profile()`
  - Test: exit 0 — returns `TunedVerifyResult(success=True, mismatches=[])`
  - Test: exit 1 with drift output — returns `success=False` and parsed
    mismatches list
  - Test: unexpected exit code — raises `TunedQueryError`
  - _Requirements: 8_

- [ ] 32. Write unit tests for `install_custom_profile()`
  - Test: happy path — directory created (mode `0755`), `tuned.conf` written
    (mode `0644`, owner `root`, group `root`), returns `SUCCESS`
  - Test: byte-for-byte identical content — returns `SKIPPED`, no write
    recorded
  - Test: payload files — each payload path written with its supplied mode
  - Test: dry-run mode — returns `DRY_RUN`, no write recorded
  - Test: elevated mode — file operator write invoked with `elevated=True`
  - Test: file operator raises — returns `FAILED` with populated
    `error_message`
  - _Requirements: 9, 10_

- [ ] 33. Write unit tests for `remove_custom_profile()`
  - Test: directory absent — returns `SKIPPED`, no delete recorded
  - Test: profile active — returns `FAILED` with deactivation message,
    no delete recorded
  - Test: happy path — `remove_tree` invoked on `/etc/tuned/<name>/` with
    `elevated=True`, returns `SUCCESS`
  - Test: dry-run mode — returns `DRY_RUN`, no delete recorded
  - Test: file operator raises — returns `FAILED` with populated
    `error_message`
  - _Requirements: 9, 10_

- [ ] 34. Checkpoint - Ensure all tests pass
  - Run all unit tests and property-based tests
  - Verify all properties pass with 100+ iterations
  - Ensure all tests pass, ask the user if questions arise
