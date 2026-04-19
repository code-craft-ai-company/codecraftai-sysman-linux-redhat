# Implementation Plan

- [ ] 1. Create the `insightsmanagement` module file
  - Create `source/packages/automojo/sysman/linux/redhat/insightsmanagement.py`
  - Add module-level docstring describing purpose and coordinator-driven design
  - Note dependency on the `package-management` feature (installs `insights-client`
    RPM) and on the `subscription-management` feature (RHSM registration)
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10_

- [ ] 2. Define status enum and result dataclasses
  - Implement `InsightsActionStatus` enum with values: `SUCCESS`, `FAILED`,
    `SKIPPED`, `DRY_RUN`
  - Implement `InsightsActionResult` dataclass with fields: `status`, `exit_code`,
    `stdout`, `stderr`, `command`, `machine_id`, `payload_path`, `detail`
  - Implement `InsightsState` dataclass with fields: `registered`, `machine_id`
  - Implement `InsightsVersion` dataclass with fields: `client_version`,
    `core_version`
  - Implement `InsightsConnectionResult` dataclass with fields: `ok`, `output`
  - _Requirements: 1, 2, 3, 4, 5, 6, 7_

- [ ] 3. Define typed exceptions
  - Implement `InsightsQueryError` with `exit_code` and `stderr` attributes
  - Implement `InsightsNotInstalledError` with no required attributes
  - Implement `InsightsPermissionError` with `path` or `command` context attribute
  - _Requirements: 1.4, 1.5, 6.3, 7.4_

- [ ] 4. Implement `InsightsManagerConfig` dataclass
  - Add `dry_run: bool = False` field
  - Add `elevated: bool = False` field
  - _Requirements: 2.8, 3.5, 4.6, 5.5, 8.6, 9.4, 10_

- [ ] 5. Implement `InsightsManager.__init__`, `get_registration_state()`, and `get_version()`
  - Accept `ICommandRunner`, `IFileOperator`, and `InsightsManagerConfig` as
    constructor arguments; assign all instance variables in `__init__`
  - Implement `get_registration_state()` by invoking `insights-client --status`
    with elevation; parse stdout into `InsightsState` (registered + machine-id)
  - Detect "command not found" style stderr and raise
    `InsightsNotInstalledError`; raise `InsightsQueryError` for other non-zero
    exit codes
  - Implement `get_version()` by invoking `insights-client --version`; parse
    output into `InsightsVersion` (client + optional core); raise
    `InsightsQueryError` on failure
  - _Requirements: 1, 6_

- [ ] 6. Implement `register()`
  - Call `get_registration_state()` to check current state; return `SKIPPED` when
    already registered and `force` is not `True`
  - Build `insights-client --register` command; append `--display-name=<value>`
    when provided, `--group=<value>` when provided, `--force-reregister` when
    `force=True`
  - Honour `dry_run` flag; return `DRY_RUN` result without calling runner
  - Pass `elevated` flag to runner; parse result into `InsightsActionResult` and
    capture `machine_id` on success when available
  - _Requirements: 2_

- [ ] 7. Implement `unregister()`
  - Call `get_registration_state()` to check current state; return `SKIPPED` when
    not registered
  - Build `insights-client --unregister` command
  - Honour `dry_run` flag
  - Pass `elevated` flag to runner; parse result into `InsightsActionResult`
  - _Requirements: 3_

- [ ] 8. Implement `upload()` and `checkin()`
  - For both methods, call `get_registration_state()`; if not registered return
    `FAILED` with a detail string and do not invoke the runner
  - For `upload()`: build `insights-client` (no flags) command
  - For `checkin()`: build `insights-client --checkin` command
  - Honour `dry_run` flag on both methods
  - Pass `elevated` flag to runner on both methods; parse result into
    `InsightsActionResult`
  - _Requirements: 4_

- [ ] 9. Implement `collect_no_upload()`
  - Call `get_registration_state()`; if not registered return `FAILED` with a
    detail string and do not invoke the runner
  - Build `insights-client --no-upload` command
  - Honour `dry_run` flag
  - Pass `elevated` flag to runner; parse stdout for the payload path and record
    it on the `InsightsActionResult`
  - _Requirements: 5_

- [ ] 10. Implement `test_connection()`
  - Build `insights-client --test-connection` command
  - Honour `dry_run` flag by returning `InsightsConnectionResult(ok=False,
    output="dry-run")` without invoking the runner
  - Pass `elevated` flag to runner; map exit code `0` to `ok=True`, non-zero to
    `ok=False`; capture combined stdout/stderr as `output`
  - Reclassify missing-binary and permission-denied failures into
    `InsightsNotInstalledError` / `InsightsPermissionError`
  - _Requirements: 7_

- [ ] 11. Implement `get_config()`, `set_config()`, and `delete_config()`
  - `get_config(key)`: read `/etc/insights-client/insights-client.conf` via the
    file operator (transparently elevating on permission errors); parse INI,
    return the value from the `[insights-client]` section or `None`
  - `set_config(key, value)`: read current file contents, parse INI, compare
    current value to `value`; return `SKIPPED` when equal
  - Otherwise update the matching line in the `[insights-client]` section
    (creating the section when absent), preserve surrounding comments, and write
    back through the file operator with mode `0640`, owner `root`, group `root`,
    and `elevated=True` when configured
  - `delete_config(key)`: remove the matching line; return `SKIPPED` when the
    key is absent; write back under the same mode/owner/group/elevation rules
  - Honour `dry_run` on both write methods; return `DRY_RUN` without calling
    `IFileOperator.write`
  - _Requirements: 8_

- [ ] 12. Implement `read_tags()` and `write_tags()`
  - `read_tags()`: call `IFileOperator.exists()`; return `{}` when absent;
    otherwise read via file operator (transparently elevating on permission
    errors) and parse YAML into a dict
  - `write_tags(tags)`: read current file contents when present and parse YAML;
    if the parsed structure equals `tags`, return `SKIPPED` without writing
    (equivalence is parsed-YAML based, not byte-level)
  - Otherwise serialize `tags` as YAML and write via the file operator with
    mode `0640`, owner `root`, group `root`, and `elevated=True` when configured
  - Honour `dry_run`; return `DRY_RUN` without calling `IFileOperator.write`
  - _Requirements: 9_

- [ ] 13. Create test module and mock infrastructure
  - Create
    `source/testroots/testbase/automojo/tests/sysman/linux/redhat/test_insightsmanagement.py`
  - Implement `MockCommandRunner` that returns configurable `RunResult` values
    and records every call (args, elevated)
  - Implement `MockFileOperator` that backs `read`/`write`/`exists` with an
    in-memory dict and records every call (path, data, mode, owner, group,
    elevated)
  - Set up hypothesis testing framework configuration and shared strategies
    for display names, group names, config keys/values, and tag dicts
  - Create test fixtures for common test scenarios (registered/unregistered)
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10_

- [ ] 14. Write property-based test for register idempotency
  - **Property 1: Register Idempotency**
  - Generate random display names, groups, and `force` flags
  - Verify repeated `register()` calls without `force=True` return `SKIPPED` when
    state is registered; verify `force=True` always invokes the runner with
    `--force-reregister`
  - **Validates: Requirements 2.4, 2.5**
  - _Requirements: 2_

- [ ] 15. Write property-based test for unregister idempotency
  - **Property 2: Unregister Idempotency**
  - Generate random initial registration states
  - Verify `unregister()` returns `SKIPPED` when not registered and does not
    invoke the command runner for the unregister call
  - **Validates: Requirements 3.2**
  - _Requirements: 3_

- [ ] 16. Write property-based test for upload requiring registration
  - **Property 3: Upload Requires Registration**
  - Generate random unregistered-state outputs
  - Verify `upload()`, `checkin()`, and `collect_no_upload()` all return
    `FAILED` with a registration-required detail and do not invoke the runner
    for the upload command
  - **Validates: Requirements 4.3**
  - _Requirements: 4, 5_

- [ ] 17. Write property-based test for state query freshness
  - **Property 4: State Query Freshness**
  - Generate pairs of differing `--status` outputs (registered/unregistered)
  - Verify two sequential `get_registration_state()` calls reflect each
    successive runner output without caching
  - **Validates: Requirements 1.1, 1.2, 1.3**
  - _Requirements: 1_

- [ ] 18. Write property-based test for version stability
  - **Property 5: Version Stability**
  - Generate random valid `--version` outputs
  - Verify repeated `get_version()` calls against the same mock output yield
    `InsightsVersion` records that compare equal
  - **Validates: Requirements 6.1, 6.2**
  - _Requirements: 6_

- [ ] 19. Write property-based test for config set idempotency
  - **Property 6: Config Set Idempotency**
  - Generate random `(key, value)` pairs and pre-populate the mock file
    operator so the key already holds `value`
  - Verify `set_config(key, value)` returns `SKIPPED` and
    `IFileOperator.write` was not invoked
  - **Validates: Requirements 8.4**
  - _Requirements: 8_

- [ ] 20. Write property-based test for tags YAML equivalence skip
  - **Property 7: Tags YAML Equivalence Skip**
  - Generate random tag dicts; serialize them under two different YAML
    renderings (different key order, whitespace, quoting)
  - Seed the mock file operator with one rendering; call `write_tags()` with the
    other; verify `SKIPPED` is returned based on parsed-YAML equality (not
    byte comparison) and that `IFileOperator.write` was not invoked
  - **Validates: Requirements 9.2**
  - _Requirements: 9_

- [ ] 21. Write property-based test for dry-run safety
  - **Property 8: Dry-Run Safety**
  - Generate random operations with `dry_run=True` across `register`,
    `unregister`, `upload`, `checkin`, `collect_no_upload`, `set_config`,
    `delete_config`, and `write_tags`
  - Verify neither the command runner nor the file operator's write is invoked
    and every result status is `DRY_RUN`
  - **Validates: Requirements 2.8, 3.5, 4.6, 5.5, 8.6, 9.4**
  - _Requirements: 2, 3, 4, 5, 8, 9_

- [ ] 22. Write property-based test for elevation consistency
  - **Property 9: Elevation Consistency**
  - Generate random operations with `elevated=True` and `elevated=False`
  - Verify every `insights-client` invocation forwards the elevated flag and
    every write under `/etc/insights-client/` forwards the elevated flag
  - **Validates: Requirements 10.1, 10.2, 10.3**
  - _Requirements: 10_

- [ ] 23. Write property-based test for status mapping correctness
  - **Property 10: Status Mapping Correctness**
  - Generate random exit codes, stdout, and stderr values from the mock runner
  - Verify `InsightsActionResult.status` is `SUCCESS` for exit code `0`,
    `FAILED` for non-zero, and that `exit_code`, `stdout`, and `stderr` are
    always populated
  - **Validates: Requirements 2.6, 2.7, 3.3, 3.4, 4.4, 4.5, 5.3, 5.4, 7.2, 7.3**
  - _Requirements: 2, 3, 4, 5, 7_

- [ ] 24. Write unit tests for `get_registration_state()`
  - Test: registered output — returns `InsightsState(registered=True,
    machine_id=<value>)`
  - Test: unregistered output — returns `InsightsState(registered=False,
    machine_id=None)`
  - Test: `command not found` stderr — raises `InsightsNotInstalledError`
  - Test: unexpected non-zero exit code — raises `InsightsQueryError`
  - _Requirements: 1_

- [ ] 25. Write unit tests for `register()`
  - Test: not registered — runner called with `insights-client --register`,
    returns `SUCCESS` with `machine_id`
  - Test: already registered, no force — returns `SKIPPED`, runner not called
  - Test: already registered, `force=True` — runner called with
    `--force-reregister`, returns `SUCCESS`
  - Test: `display_name` provided — runner called with
    `--display-name=<value>`
  - Test: `group` provided — runner called with `--group=<value>`
  - Test: registration failure — returns `FAILED` with exit code and stderr
  - Test: dry-run mode — returns `DRY_RUN`, runner not called
  - Test: elevated mode — runner called with `elevated=True`
  - _Requirements: 2, 10_

- [ ] 26. Write unit tests for `unregister()`
  - Test: registered — runner called, returns `SUCCESS`
  - Test: not registered — returns `SKIPPED`, runner not called
  - Test: unregister failure — returns `FAILED`
  - Test: dry-run mode — returns `DRY_RUN`, runner not called
  - _Requirements: 3_

- [ ] 27. Write unit tests for `upload()` and `checkin()`
  - Test: registered — runner called with `insights-client` / `--checkin`,
    returns `SUCCESS`
  - Test: not registered — returns `FAILED` with registration-required detail,
    runner not called
  - Test: upload failure — returns `FAILED` with exit code and stderr
  - Test: dry-run mode — returns `DRY_RUN`, runner not called
  - _Requirements: 4_

- [ ] 28. Write unit tests for `collect_no_upload()`
  - Test: registered, runner reports payload path — returns `SUCCESS` with
    `payload_path` populated
  - Test: not registered — returns `FAILED` with detail, runner not called
  - Test: collect failure — returns `FAILED`
  - Test: dry-run mode — returns `DRY_RUN`, runner not called
  - _Requirements: 5_

- [ ] 29. Write unit tests for `get_version()`
  - Test: output with both client and core versions — returns populated
    `InsightsVersion`
  - Test: output with only client version — returns `InsightsVersion` with
    `core_version=None`
  - Test: command failure — raises `InsightsQueryError`
  - _Requirements: 6_

- [ ] 30. Write unit tests for `test_connection()`
  - Test: runner exit code `0` — returns `InsightsConnectionResult(ok=True, ...)`
  - Test: runner non-zero exit code — returns
    `InsightsConnectionResult(ok=False, output=<stderr>)`
  - Test: missing binary — raises `InsightsNotInstalledError`
  - Test: permission denied — raises `InsightsPermissionError`
  - Test: dry-run mode — returns `ok=False` with `output="dry-run"`, runner not
    called
  - _Requirements: 7_

- [ ] 31. Write unit tests for `get_config()`, `set_config()`, and `delete_config()`
  - Test: `get_config()` returns existing value from the `[insights-client]`
    section
  - Test: `get_config()` returns `None` when the key is absent
  - Test: `set_config()` writes updated INI with preserved comments and correct
    mode/owner/group/elevated
  - Test: `set_config()` creates the `[insights-client]` section when missing
  - Test: `set_config()` returns `SKIPPED` when the current value already matches
  - Test: `delete_config()` removes the matching line and writes back
  - Test: `delete_config()` returns `SKIPPED` when the key is absent
  - Test: dry-run mode on both writes — returns `DRY_RUN`, file operator's
    `write` not called
  - Test: read transparently elevates when the unprivileged read raises a
    permission error
  - _Requirements: 8, 10_

- [ ] 32. Write unit tests for `read_tags()` and `write_tags()`
  - Test: `read_tags()` returns `{}` when the file does not exist
  - Test: `read_tags()` parses and returns the tag dict
  - Test: `write_tags()` writes YAML with mode `0640`, owner `root`, group
    `root`, and `elevated=True` when configured
  - Test: `write_tags()` returns `SKIPPED` when the on-disk parsed YAML equals
    the requested dict, even when serialized with different whitespace/key order
  - Test: dry-run mode — returns `DRY_RUN`, file operator's `write` not called
  - _Requirements: 9, 10_

- [ ] 33. Checkpoint - Ensure all tests pass
  - Run all unit tests and property-based tests
  - Verify all properties pass with 100+ iterations
  - Ensure all tests pass, ask the user if questions arise

