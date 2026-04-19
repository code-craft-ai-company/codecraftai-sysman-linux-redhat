# Implementation Plan

- [ ] 1. Create the `servicemanagement` module file
  - Create `source/packages/automojo/sysman/linux/redhat/servicemanagement.py`
  - Add module-level docstring describing purpose and coordinator-driven design
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12_

- [ ] 2. Define status enum and result dataclasses
  - Implement `ServiceActionStatus` enum with values: `SUCCESS`, `FAILED`, `SKIPPED`,
    `DRY_RUN`
  - Implement `ServiceActionResult` dataclass with fields: `status`, `service_name`,
    `exit_code`, `stdout`, `stderr`, `command`
  - Implement `ServiceInfo` dataclass with fields: `service_name`, `active_state`,
    `enabled_state`
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10_

- [ ] 3. Define typed exceptions
  - Implement `ServiceQueryError` with `exit_code` and `stderr` attributes
  - _Requirements: 9, 11_

- [ ] 4. Implement `ServiceManagerConfig` dataclass
  - Add `dry_run: bool = False` field
  - Add `elevated: bool = False` field
  - _Requirements: 1.5, 2.5, 3.4, 4.5, 5.5, 6.5, 7.5, 8.5, 10.4, 12_

- [ ] 5. Implement `ServiceManager.__init__` and `query()`
  - Accept `ICommandRunner` and `ServiceManagerConfig` as constructor arguments
  - Implement `query(name)` by running `systemctl is-active` and `systemctl is-enabled`
  - Treat any stdout string as the state value regardless of exit code for these two commands
  - Raise `ServiceQueryError` only on unexpected runner errors (e.g. runner itself fails)
  - Populate and return a `ServiceInfo` record
  - _Requirements: 9_

- [ ] 6. Implement shared private helper `_run_write_command()`
  - Accept service name, command args, and a `skip_if` callable over `ServiceInfo`
  - Call `query()` first and return `SKIPPED` if `skip_if` condition is met
  - Return `DRY_RUN` without calling runner when `dry_run=True`
  - Call runner with `elevated` flag and map result to `ServiceActionResult`
  - _Requirements: 1, 2, 5, 6, 7, 8, 12_

- [ ] 7. Implement `start()`
  - Use helper: skip when `active_state == "active"`
  - Build `systemctl start <name>` command
  - _Requirements: 1_

- [ ] 8. Implement `stop()`
  - Use helper: skip when `active_state == "inactive"`
  - Build `systemctl stop <name>` command
  - _Requirements: 2_

- [ ] 9. Implement `restart()`
  - No skip condition — always execute
  - Return `DRY_RUN` without calling runner when `dry_run=True`
  - Build `systemctl restart <name>` command; pass `elevated` flag
  - _Requirements: 3_

- [ ] 10. Implement `reload()`
  - Call `query()` first; return `FAILED` immediately if `active_state != "active"`
  - Return `DRY_RUN` without calling runner when `dry_run=True`
  - Build `systemctl reload <name>` command; pass `elevated` flag
  - _Requirements: 4_

- [ ] 11. Implement `enable()`
  - Use helper: skip when `enabled_state == "enabled"`
  - Build `systemctl enable <name>` command
  - _Requirements: 5_

- [ ] 12. Implement `disable()`
  - Use helper: skip when `enabled_state == "disabled"`
  - Build `systemctl disable <name>` command
  - _Requirements: 6_

- [ ] 13. Implement `mask()`
  - Use helper: skip when `enabled_state == "masked"`
  - Build `systemctl mask <name>` command
  - _Requirements: 7_

- [ ] 14. Implement `unmask()`
  - Use helper: skip when `enabled_state != "masked"`
  - Build `systemctl unmask <name>` command
  - _Requirements: 8_

- [ ] 15. Implement `daemon_reload()`
  - No skip condition and no service name argument
  - Return `DRY_RUN` without calling runner when `dry_run=True`
  - Build `systemctl daemon-reload` command; pass `elevated` flag
  - Return `ServiceActionResult` with `service_name=""` (not applicable)
  - _Requirements: 10_

- [ ] 16. Implement `get_journal()`
  - Accept `name` and optional `lines: int = 50`
  - Build `journalctl -u <name> --no-pager -n <lines>` command
  - Do NOT pass `elevated` flag (read-only operation)
  - Return captured stdout as string on success
  - Raise `ServiceQueryError` on non-zero exit code
  - _Requirements: 11_

- [ ] 17. Create test module and mock infrastructure
  - Create `source/testroots/testbase/automojo/tests/sysman/linux/redhat/test_servicemanagement.py`
  - Implement `MockCommandRunner` that returns configurable `RunResult` per command args
  - Helper factory `make_is_active_result(state)` and `make_is_enabled_result(state)` for setting up query responses
  - Set up hypothesis testing framework configuration
  - Create test fixtures for common test scenarios
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12_

- [ ] 18. Write property-based test for idempotent start operations
  - **Property 1: Idempotent Start Operations**
  - Generate random service names
  - Verify start on already active service returns SKIPPED
  - **Validates: Requirements 1.2**
  - _Requirements: 1_

- [ ] 19. Write property-based test for idempotent stop operations
  - **Property 2: Idempotent Stop Operations**
  - Generate random service names
  - Verify stop on already inactive service returns SKIPPED
  - **Validates: Requirements 2.2**
  - _Requirements: 2_

- [ ] 20. Write property-based test for idempotent enable operations
  - **Property 3: Idempotent Enable Operations**
  - Generate random service names
  - Verify enable on already enabled service returns SKIPPED
  - **Validates: Requirements 5.2**
  - _Requirements: 5_

- [ ] 21. Write property-based test for idempotent disable operations
  - **Property 4: Idempotent Disable Operations**
  - Generate random service names
  - Verify disable on already disabled service returns SKIPPED
  - **Validates: Requirements 6.2**
  - _Requirements: 6_

- [ ] 22. Write property-based test for idempotent mask operations
  - **Property 5: Idempotent Mask Operations**
  - Generate random service names
  - Verify mask on already masked service returns SKIPPED
  - **Validates: Requirements 7.2**
  - _Requirements: 7_

- [ ] 23. Write property-based test for idempotent unmask operations
  - **Property 6: Idempotent Unmask Operations**
  - Generate random service names
  - Verify unmask on non-masked service returns SKIPPED
  - **Validates: Requirements 8.2**
  - _Requirements: 8_

- [ ] 24. Write property-based test for dry-run safety
  - **Property 7: Dry-Run Safety**
  - Generate random operations with dry_run=True
  - Verify command runner is never invoked for write operations
  - Verify all results have DRY_RUN status
  - **Validates: Requirements 1.5, 2.5, 3.4, 4.5, 5.5, 6.5, 7.5, 8.5, 10.4**
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 10_

- [ ] 25. Write property-based test for command construction correctness
  - **Property 8: Command Construction Correctness**
  - Generate random service names and operation types
  - Verify constructed commands match expected format from Command Mappings
  - **Validates: Requirements 1.1, 2.1, 3.1, 4.1, 5.1, 6.1, 7.1, 8.1, 9.1, 10.1, 11.1**
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11_

- [ ] 26. Write property-based test for elevation consistency
  - **Property 9: Elevation Consistency**
  - Generate random operations with elevated=True/False
  - Verify write operations pass elevated flag correctly
  - Verify read operations never use elevation
  - **Validates: Requirements 12.1, 12.2, 12.3**
  - _Requirements: 12_

- [ ] 27. Write property-based test for query state consistency
  - **Property 10: Query State Consistency**
  - Generate random service info
  - Verify repeated query calls return equivalent results
  - **Validates: Requirements 9.2**
  - _Requirements: 9_

- [ ] 28. Write property-based test for reload precondition
  - **Property 11: Reload Precondition**
  - Generate random service names with non-active states
  - Verify reload returns FAILED without invoking command
  - **Validates: Requirements 4.2**
  - _Requirements: 4_

- [ ] 29. Write property-based test for status mapping correctness
  - **Property 12: Status Mapping Correctness**
  - Generate random exit codes from command runner
  - Verify correct ServiceActionResult status mapping
  - **Validates: Requirements 1.3, 1.4, 2.3, 2.4, 3.2, 3.3, 4.3, 4.4, 5.3, 5.4, 6.3, 6.4, 7.3, 7.4, 8.3, 8.4, 10.2, 10.3**
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 10_

- [ ] 30. Write unit tests for `query()`
  - Test: active + enabled service — returns correct `ServiceInfo`
  - Test: inactive + disabled service — returns correct `ServiceInfo`
  - Test: masked service — `enabled_state` is `"masked"`
  - Test: unit not found — both states return `"not-found"`
  - _Requirements: 9_

- [ ] 19. Write tests for `start()` and `stop()`
  - Test start: service inactive — command issued, returns `SUCCESS`
  - Test start: service already active — returns `SKIPPED`, no command issued
  - Test start: command fails — returns `FAILED` with stderr
  - Test start: dry-run — returns `DRY_RUN`, no command issued
  - Test stop: service active — command issued, returns `SUCCESS`
  - Test stop: service already inactive — returns `SKIPPED`
  - Test stop: dry-run — returns `DRY_RUN`
  - _Requirements: 1, 2_

- [ ] 20. Write tests for `restart()`
  - Test: command issued unconditionally, returns `SUCCESS`
  - Test: command fails — returns `FAILED`
  - Test: dry-run — returns `DRY_RUN`, no command issued
  - Test: elevated mode — runner called with `elevated=True`
  - _Requirements: 3_

- [ ] 21. Write tests for `reload()`
  - Test: service active — reload command issued, returns `SUCCESS`
  - Test: service not active — returns `FAILED` without issuing reload command
  - Test: reload command fails — returns `FAILED` with stderr
  - Test: dry-run — returns `DRY_RUN`, no command issued
  - _Requirements: 4_

- [ ] 22. Write tests for `enable()` and `disable()`
  - Test enable: disabled service — command issued, returns `SUCCESS`
  - Test enable: already enabled — returns `SKIPPED`
  - Test enable: command fails — returns `FAILED`
  - Test enable: dry-run — returns `DRY_RUN`
  - Test disable: enabled service — command issued, returns `SUCCESS`
  - Test disable: already disabled — returns `SKIPPED`
  - Test disable: dry-run — returns `DRY_RUN`
  - _Requirements: 5, 6_

- [ ] 23. Write tests for `mask()` and `unmask()`
  - Test mask: unmasked service — command issued, returns `SUCCESS`
  - Test mask: already masked — returns `SKIPPED`
  - Test mask: dry-run — returns `DRY_RUN`
  - Test unmask: masked service — command issued, returns `SUCCESS`
  - Test unmask: not masked — returns `SKIPPED`
  - Test unmask: dry-run — returns `DRY_RUN`
  - _Requirements: 7, 8_

- [ ] 24. Write tests for `daemon_reload()`
  - Test: command issued, returns `SUCCESS`
  - Test: command fails — returns `FAILED`
  - Test: dry-run — returns `DRY_RUN`, no command issued
  - Test: elevated mode — runner called with `elevated=True`
  - _Requirements: 10_

- [ ] 25. Write tests for `get_journal()`
  - Test: default line count — command built with `-n 50`, stdout returned as string
  - Test: custom line count — command built with `-n <custom>`, stdout returned
  - Test: command fails — raises `ServiceQueryError`
  - Test: elevated NOT used — runner called without elevation flag
  - _Requirements: 11_

- [ ] 31. Write unit tests for `start()` and `stop()`
  - Test start: service inactive — command issued, returns `SUCCESS`
  - Test start: service already active — returns `SKIPPED`, no command issued
  - Test start: command fails — returns `FAILED` with stderr
  - Test start: dry-run — returns `DRY_RUN`, no command issued
  - Test stop: service active — command issued, returns `SUCCESS`
  - Test stop: service already inactive — returns `SKIPPED`
  - Test stop: dry-run — returns `DRY_RUN`
  - _Requirements: 1, 2_

- [ ] 32. Write unit tests for `restart()`
  - Test: command issued unconditionally, returns `SUCCESS`
  - Test: command fails — returns `FAILED`
  - Test: dry-run — returns `DRY_RUN`, no command issued
  - Test: elevated mode — runner called with `elevated=True`
  - _Requirements: 3_

- [ ] 33. Write unit tests for `reload()`
  - Test: service active — reload command issued, returns `SUCCESS`
  - Test: service not active — returns `FAILED` without issuing reload command
  - Test: reload command fails — returns `FAILED` with stderr
  - Test: dry-run — returns `DRY_RUN`, no command issued
  - _Requirements: 4_

- [ ] 34. Write unit tests for `enable()` and `disable()`
  - Test enable: disabled service — command issued, returns `SUCCESS`
  - Test enable: already enabled — returns `SKIPPED`
  - Test enable: command fails — returns `FAILED`
  - Test enable: dry-run — returns `DRY_RUN`
  - Test disable: enabled service — command issued, returns `SUCCESS`
  - Test disable: already disabled — returns `SKIPPED`
  - Test disable: dry-run — returns `DRY_RUN`
  - _Requirements: 5, 6_

- [ ] 35. Write unit tests for `mask()` and `unmask()`
  - Test mask: unmasked service — command issued, returns `SUCCESS`
  - Test mask: already masked — returns `SKIPPED`
  - Test mask: dry-run — returns `DRY_RUN`
  - Test unmask: masked service — command issued, returns `SUCCESS`
  - Test unmask: not masked — returns `SKIPPED`
  - Test unmask: dry-run — returns `DRY_RUN`
  - _Requirements: 7, 8_

- [ ] 36. Write unit tests for `daemon_reload()`
  - Test: command issued, returns `SUCCESS`
  - Test: command fails — returns `FAILED`
  - Test: dry-run — returns `DRY_RUN`, no command issued
  - Test: elevated mode — runner called with `elevated=True`
  - _Requirements: 10_

- [ ] 37. Write unit tests for `get_journal()`
  - Test: default line count — command built with `-n 50`, stdout returned as string
  - Test: custom line count — command built with `-n <custom>`, stdout returned
  - Test: command fails — raises `ServiceQueryError`
  - Test: elevated NOT used — runner called without elevation flag
  - _Requirements: 11_

- [ ] 38. Checkpoint - Ensure all tests pass
  - Run all unit tests and property-based tests
  - Verify all properties pass with 100+ iterations
  - Ensure all tests pass, ask the user if questions arise
