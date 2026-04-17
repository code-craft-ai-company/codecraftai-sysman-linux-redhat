# Implementation Plan

- [ ] 1. Create the `kdumpmanagement` module file
  - Create `source/packages/automojo/sysman/linux/redhat/kdumpmanagement.py`
  - Add module-level docstring describing purpose, coordinator-driven design, and the
    cross-feature dependency on `boot-management` for `crashkernel=` mutations
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10_

- [ ] 2. Define status enum and result dataclasses
  - Implement `KdumpActionStatus` enum with values: `SUCCESS`, `FAILED`, `SKIPPED`,
    `DRY_RUN`
  - Implement `KdumpActionResult` dataclass with fields: `status`, `action`,
    `exit_code`, `stdout`, `stderr`, `command`, `note`
  - Implement `KdumpServiceState` dataclass with fields: `present`, `active`, `enabled`,
    `armed`, `reserved_memory_bytes`
  - Implement `CrashkernelReservation` dataclass with fields: `raw`, `parsed_ranges`
  - Implement `KdumpArmedState` dataclass with fields: `loaded`, `reserved_bytes`
  - Use full type hints on every field; no mutable defaults
  - _Requirements: 1, 2, 4, 5, 6, 7, 8, 9_

- [ ] 3. Define typed exceptions
  - Implement `KdumpQueryError` with `exit_code`, `stderr`, and `detail` attributes
  - Implement `KdumpConfigNotFoundError` with a `path` attribute
  - _Requirements: 1.5, 3.3, 3.4_

- [ ] 4. Implement `KdumpManagerConfig` dataclass
  - Add `dry_run: bool = False` field
  - Add `elevated: bool = False` field
  - Add `boot_manager: BootManager` field (no default; caller must inject a
    `boot-management` instance for `crashkernel=` delegation)
  - _Requirements: 6.1, 6.2, 10.1, 10.2, 10.3_

- [ ] 5. Implement `KdumpManager.__init__` and `get_service_state()`
  - Accept `ICommandRunner`, `IFileOperator`, and `KdumpManagerConfig` as constructor
    arguments; store references on `self` in `__init__` only
  - Implement `get_service_state()` using `systemctl is-active kdump`,
    `systemctl is-enabled kdump`, and `kdumpctl status`
  - Return a `KdumpServiceState` with `present=False` when the unit is not present
  - Parse reserved memory from `kdumpctl status` when available
  - Raise `KdumpQueryError` on unexpected failures
  - _Requirements: 1_

- [ ] 6. Implement `lifecycle_action()`
  - Accept an `action` parameter constrained to
    `"start" | "stop" | "restart" | "enable" | "disable"`
  - Call `get_service_state()` to decide whether the action is a no-op; return `SKIPPED`
    when already in the requested state
  - When enabling and runtime `crashkernel=` is missing, populate the result `note` with
    a reservation-missing warning but still attempt the enable
  - Build `systemctl <action> kdump` command
  - Honour `dry_run`; return `DRY_RUN` without calling the runner
  - Pass `elevated` flag to runner; map result to `KdumpActionResult`
  - _Requirements: 2_

- [ ] 7. Implement `get_config()` and `delete_config()`
  - `get_config(key=None)`: read `/etc/kdump.conf` via the file operator; parse
    whitespace-separated `<key> <value>` lines; return the first matching value, `None`
    if the key is absent, or the full `OrderedDict` when `key is None`
  - Raise `KdumpConfigNotFoundError` when the file does not exist
  - Raise `KdumpQueryError` for any other read failure
  - `delete_config(key)`: remove the matching line, preserve comments and ordering,
    write with mode `0600`, owner `root`, group `root`, elevated; return
    `KdumpActionResult`
  - Honour `dry_run` on `delete_config`
  - _Requirements: 3, 4.2_

- [ ] 8. Implement `set_config()`
  - Read current value via `get_config(key)`; return `SKIPPED` when current value equals
    the requested value
  - Update the matching `<key> <value>` line in place, preserving comments and ordering,
    or append the line when absent
  - Write back with mode `0600`, owner `root`, group `root`, elevated
  - Populate the result `note` with advisory text indicating
    `kdumpctl rebuild` and a kdump service restart may be required
  - Honour `dry_run`; return `DRY_RUN` without calling the file operator
  - _Requirements: 4_

- [ ] 9. Implement `get_crashkernel_runtime()` and `get_crashkernel_persistent()`
  - `get_crashkernel_runtime()`: read `/proc/cmdline` via the file operator; locate the
    `crashkernel=` token; parse the raw specifier and return a
    `CrashkernelReservation`, or `None` when absent
  - `get_crashkernel_persistent(kernel_path)`: use the `BootManager` read surface (or
    `grubby --info=<kernel_path>` when exposed) to extract `crashkernel=...` from the
    kernel `args` field; return a `CrashkernelReservation` or `None`
  - Parse the raw specifier into a list of `(range, size)` tuples when possible
  - _Requirements: 5_

- [ ] 10. Implement `set_crashkernel()` and `remove_crashkernel()` (delegate to `boot-management`)
  - `set_crashkernel(value)`: compare requested value against persistent value on every
    targeted kernel; return `SKIPPED` when all kernels already match
  - Delegate the mutation to `self._config.boot_manager.add_kernel_arg(
    argument=f"crashkernel={value}")`
  - `remove_crashkernel()`: delegate to
    `self._config.boot_manager.remove_kernel_arg(argument_key="crashkernel")`
  - Translate `BootActionResult` into `KdumpActionResult`, preserving status, exit code,
    and stderr; propagate delegate `FAILED` and `SKIPPED` outcomes faithfully
  - Populate the `note` field on `SUCCESS` with a reboot-required advisory
  - Honour `dry_run`; return `DRY_RUN` without invoking the delegate
  - _Requirements: 6_

- [ ] 11. Implement `rebuild_initramfs()`
  - Build `kdumpctl rebuild` command
  - Honour `dry_run`; return `DRY_RUN` without calling the runner
  - Pass `elevated` flag to runner
  - Map runner result to `KdumpActionResult`; on `SUCCESS`, include a `note` stating the
    initramfs has been rebuilt
  - _Requirements: 7_

- [ ] 12. Implement `get_armed_state()`
  - Read `/sys/kernel/kexec_crash_loaded` via the file operator; interpret `"1"` as
    `loaded=True` and `"0"` as `loaded=False`
  - Read `/sys/kernel/kexec_crash_size` via the file operator; parse as integer bytes
  - When either file is absent, return `KdumpArmedState(loaded=False, reserved_bytes=0)`
  - Never invoke the command runner from this method
  - _Requirements: 8_

- [ ] 13. Implement `get_sysconfig()` and `set_sysconfig()`
  - `get_sysconfig(key)`: read `/etc/sysconfig/kdump` via the file operator; parse
    `KEY=value` lines; return the value (with surrounding quotes stripped) or `None`
  - `set_sysconfig(key, value)`: read current value; return `SKIPPED` when the value
    already matches
  - Preserve the existing quoting style (single-quoted, double-quoted, or unquoted) when
    rewriting the line; append a new line when absent
  - Write back with mode `0644`, owner `root`, group `root`, elevated
  - Populate the result `note` with a kdump service-restart advisory on `SUCCESS`
  - Honour `dry_run`; return `DRY_RUN` without calling the file operator
  - _Requirements: 9_

- [ ] 14. Create test module and mock infrastructure
  - Create `source/testroots/testbase/automojo/tests/sysman/linux/redhat/test_kdumpmanagement.py`
  - Implement `MockCommandRunner` that returns configurable `RunResult` values and
    records every `run()` call
  - Implement `MockFileOperator` with an in-memory filesystem, honouring `read_text`,
    `write_text`, `exists`, and `delete`, and recording every mutation
  - Implement `MockBootManager` exposing `add_kernel_arg` and `remove_kernel_arg` with
    configurable `BootActionResult` values and call recording
  - Set up hypothesis testing framework configuration with a minimum of 100 iterations
    per property test
  - Create shared test fixtures: default configs, present-service state, absent-service
    state, populated `/etc/kdump.conf`, and sample `/sys/kernel/kexec_crash_*` contents
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10_

- [ ] 15. Write property-based test for lifecycle idempotency
  - **Property 1: Lifecycle Idempotency**
  - Generate random combinations of requested action and current service state
  - Verify `lifecycle_action()` returns `SKIPPED` without invoking the mutating
    `systemctl` call when the service is already in the requested state
  - **Validates: Requirements 2.2**
  - _Requirements: 2_

- [ ] 16. Write property-based test for kdump.conf set idempotency
  - **Property 2: kdump.conf Set Idempotency**
  - Generate random `(key, value)` pairs and pre-seed `/etc/kdump.conf` with a matching
    line in the mock file operator
  - Verify `set_config()` returns `SKIPPED` and no `write_text` call is recorded
  - **Validates: Requirements 4.3**
  - _Requirements: 4_

- [ ] 17. Write property-based test for persistent-mode edit note
  - **Property 3: Persistent-Mode Edit Note**
  - Generate random `(key, value)` pairs that force a write
  - Verify the returned `KdumpActionResult.note` is non-empty and references rebuild /
    restart guidance
  - **Validates: Requirements 4.4**
  - _Requirements: 4_

- [ ] 18. Write property-based test for crashkernel delegate SKIPPED propagation
  - **Property 4: Crashkernel Delegate Skipped Propagation**
  - Generate random crashkernel specifiers and configure the mock `BootManager` to
    report the same persistent value on every targeted kernel
  - Verify `set_crashkernel()` returns `SKIPPED` and no `add_kernel_arg` call is made
  - **Validates: Requirements 6.3**
  - _Requirements: 6_

- [ ] 19. Write property-based test for crashkernel delegate FAILED propagation
  - **Property 5: Crashkernel Delegate Failure Propagation**
  - Generate random failure results from the mock `BootManager`
  - Verify `set_crashkernel()` and `remove_crashkernel()` return a `KdumpActionResult`
    with `status=FAILED`, preserving exit code and stderr from the delegate
  - **Validates: Requirements 6.4_
  - _Requirements: 6_

- [ ] 20. Write property-based test for armed state file-only reads
  - **Property 6: Armed State Reads Only /sys Files**
  - Generate random file contents (present, absent, malformed) for
    `/sys/kernel/kexec_crash_loaded` and `/sys/kernel/kexec_crash_size`
  - Verify `get_armed_state()` never invokes the command runner and returns a
    well-defined `KdumpArmedState` in every case
  - **Validates: Requirements 8.1, 8.2, 8.3, 8.4**
  - _Requirements: 8_

- [ ] 21. Write property-based test for dry-run safety
  - **Property 7: Dry-Run Safety**
  - Generate random mutating operations with `dry_run=True`
  - Verify no mutating runner call, no `write_text` call, and no `BootManager` mutation
    is recorded
  - Verify every returned `KdumpActionResult` has `status=DRY_RUN`
  - **Validates: Requirements 2.6, 4.5, 6.5, 7.4, 9.5**
  - _Requirements: 2, 4, 6, 7, 9_

- [ ] 22. Write property-based test for elevation consistency
  - **Property 8: Elevation Consistency**
  - Generate random operations and config elevation flags
  - Verify mutating operations pass `elevated=True` to the runner / file operator /
    `BootManager` when configured, and read-only queries never use elevation
  - **Validates: Requirements 10.1, 10.2, 10.3**
  - _Requirements: 10_

- [ ] 23. Write property-based test for status mapping correctness
  - **Property 9: Status Mapping Correctness**
  - Generate random runner exit codes for mutating operations
  - Verify `exit_code == 0` maps to `SUCCESS` and non-zero maps to `FAILED`, except when
    the call was skipped or in dry-run mode
  - **Validates: Requirements 2.4, 2.5, 7.2, 7.3**
  - _Requirements: 2, 7_

- [ ] 24. Write property-based test for sysconfig quote preservation
  - **Property 10: Sysconfig Edit Quote Preservation**
  - Generate random existing `KEY=value` entries using single-quote, double-quote, and
    unquoted styles
  - Verify `set_sysconfig()` rewrites the entry using the same quoting style that was
    used on the existing line
  - **Validates: Requirements 9.2**
  - _Requirements: 9_

- [ ] 25. Write property-based test for kdump config-not-found error
  - **Property 11: Kdump Config Not Found**
  - Generate random `(key, value)` inputs with the mock file operator configured so that
    `/etc/kdump.conf` does not exist
  - Verify `get_config()` raises `KdumpConfigNotFoundError`
  - Verify `set_config()` raises `KdumpConfigNotFoundError` before any `write_text` call
    is recorded
  - **Validates: Requirements 3.3**
  - _Requirements: 3_

- [ ] 26. Write unit tests for `get_service_state()`
  - Test: unit present, active, enabled, armed — returns populated record
  - Test: unit present, inactive, disabled, not armed — returns record with
    corresponding booleans
  - Test: unit not present — returns record with `present=False` and other fields unset
  - Test: unexpected query failure — raises `KdumpQueryError`
  - _Requirements: 1_

- [ ] 27. Write unit tests for `lifecycle_action()`
  - Test: service inactive, action `start` — runner called, returns `SUCCESS`
  - Test: service already active, action `start` — returns `SKIPPED`, runner not called
    for the mutating command
  - Test: action `enable` with missing runtime `crashkernel=` — still attempts enable
    and populates `note` with a reservation-missing warning
  - Test: runner failure — returns `FAILED` with exit code and stderr
  - Test: dry-run mode — returns `DRY_RUN`, runner not called for mutations
  - Test: elevated mode — runner called with `elevated=True`
  - _Requirements: 2, 10_

- [ ] 28. Write unit tests for `get_config()`, `set_config()`, and `delete_config()`
  - Test: key present — `get_config(key)` returns the value
  - Test: key absent — `get_config(key)` returns `None`
  - Test: `get_config(None)` — returns full `OrderedDict`
  - Test: file missing — `get_config()` raises `KdumpConfigNotFoundError`
  - Test: unexpected read failure — `get_config()` raises `KdumpQueryError`
  - Test: `set_config` same value — returns `SKIPPED`, no `write_text` call
  - Test: `set_config` new value — writes file with mode `0600`, owner/group `root`,
    elevated; returns `SUCCESS` with rebuild/restart advisory in `note`
  - Test: `set_config` append when key absent — writes a new line appended to the file
  - Test: `delete_config` key present — removes the line, returns `SUCCESS`
  - Test: `delete_config` key absent — returns `SKIPPED`
  - Test: dry-run mode on set/delete — returns `DRY_RUN`, file operator not called for
    writes
  - _Requirements: 3, 4_

- [ ] 29. Write unit tests for `get_crashkernel_runtime()` and `get_crashkernel_persistent()`
  - Test: `/proc/cmdline` contains `crashkernel=...` — returns populated
    `CrashkernelReservation` with parsed ranges
  - Test: `/proc/cmdline` lacks the parameter — returns `None`
  - Test: persistent read returns a value via `BootManager` — returns populated
    `CrashkernelReservation`
  - Test: persistent read returns no value — returns `None`
  - _Requirements: 5_

- [ ] 30. Write unit tests for `set_crashkernel()` and `remove_crashkernel()`
  - Test: requested value matches persistent value on every targeted kernel — returns
    `SKIPPED`, no `BootManager.add_kernel_arg` call
  - Test: `BootManager` delegate returns `SUCCESS` — returns `KdumpActionResult` with
    `status=SUCCESS` and reboot-required `note`
  - Test: `BootManager` delegate returns `FAILED` — returns `KdumpActionResult` with
    `status=FAILED`, preserving exit code and stderr
  - Test: dry-run mode — returns `DRY_RUN`, delegate not invoked
  - Test: `remove_crashkernel` when no reservation is present — returns `SKIPPED`
  - _Requirements: 6_

- [ ] 31. Write unit tests for `rebuild_initramfs()`
  - Test: rebuild success — runner called with `kdumpctl rebuild`, returns `SUCCESS`
  - Test: rebuild failure — returns `FAILED` with exit code and stderr
  - Test: dry-run mode — returns `DRY_RUN`, runner not called
  - Test: elevated mode — runner called with `elevated=True`
  - _Requirements: 7, 10_

- [ ] 32. Write unit tests for `get_armed_state()`
  - Test: `/sys/kernel/kexec_crash_loaded` == `"1"` and `kexec_crash_size` is a valid
    integer — returns record with `loaded=True` and parsed `reserved_bytes`
  - Test: loaded file == `"0"` — returns record with `loaded=False`
  - Test: either `/sys` file absent — returns record with `loaded=False` and
    `reserved_bytes=0`
  - Test: runner is never invoked in any scenario
  - _Requirements: 8_

- [ ] 33. Write unit tests for `get_sysconfig()` and `set_sysconfig()`
  - Test: `get_sysconfig(key)` — returns the unquoted value
  - Test: `get_sysconfig(key)` on absent key — returns `None`
  - Test: `set_sysconfig` same value — returns `SKIPPED`, no `write_text` call
  - Test: `set_sysconfig` with existing double-quoted entry — writes back using double
    quotes
  - Test: `set_sysconfig` with existing single-quoted entry — writes back using single
    quotes
  - Test: `set_sysconfig` with existing unquoted entry — writes back unquoted
  - Test: `set_sysconfig` new key — appends unquoted entry
  - Test: `set_sysconfig` writes with mode `0644`, owner/group `root`, elevated, and
    populates `note` with a restart advisory
  - Test: dry-run mode — returns `DRY_RUN`, file operator not called for writes
  - _Requirements: 9, 10_

- [ ] 34. Checkpoint — Ensure all tests pass
  - Run all unit tests and property-based tests
  - Verify all properties pass with 100+ iterations
  - Ensure all tests pass; ask the user if questions arise
