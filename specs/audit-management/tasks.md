# Implementation Plan

- [ ] 1. Create the `auditmanagement` module file
  - Create `source/packages/automojo/sysman/linux/redhat/auditmanagement.py`
  - Add module-level docstring describing purpose and coordinator-driven design
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12_

- [ ] 2. Define status enum and result/model dataclasses
  - Implement `AuditActionStatus` enum with values: `SUCCESS`, `FAILED`, `SKIPPED`, `DRY_RUN`
  - Implement `AuditActionResult` dataclass with fields: `status`, `target`,
    `exit_code`, `stdout`, `stderr`, `command`, `note`
  - Implement `AuditRule` dataclass with fields: `raw`, `filter_list`, `action`, `key`
  - Implement `AuditRuleFile` dataclass with fields: `name`, `path`, `size_bytes`
  - Implement `AuditEvent` dataclass with fields: `event_id`, `timestamp`,
    `event_type`, `serial`, `raw`
  - Implement `AuditReport` dataclass with fields: `category`, `raw`, `parsed`
  - Implement `AuditServiceState` dataclass with fields: `present`, `active`,
    `enabled`, `pid`
  - Implement `AuditKernelState` dataclass with fields: `enabled`, `failure`, `pid`,
    `rate_limit`, `backlog_limit`, `backlog`, `lost`
  - _Requirements: 1, 2, 3, 4, 5, 6, 9, 10, 11_

- [ ] 3. Define typed exceptions
  - Implement `AuditQueryError` with `exit_code` and `stderr` attributes
  - Implement `AuditImmutableError` with a descriptive message and an `enabled`
    attribute carrying the kernel-reported value
  - _Requirements: 1.4, 3.4, 4.2, 7.2, 9.4, 10.5, 11.3_

- [ ] 4. Implement `AuditManagerConfig` dataclass
  - Add `dry_run: bool = False` field
  - Add `elevated: bool = False` field
  - _Requirements: 2.5, 4.6, 5.5, 6.5, 7.5, 8.6, 12_

- [ ] 5. Implement `AuditManager.__init__` and `get_service_state()`
  - Accept `ICommandRunner`, `IFileOperator`, and `AuditManagerConfig` as constructor
    arguments
  - Implement `get_service_state()` by invoking `systemctl is-active auditd` and
    `systemctl is-enabled auditd`
  - Parse output into `AuditServiceState`; handle the "unit not present" case by
    returning `present=False` with other fields unset
  - Raise `AuditQueryError` for any other non-zero exit code
  - _Requirements: 1_

- [ ] 6. Implement `set_service_state()` lifecycle action
  - Accept an action: `start`, `stop`, `enable`, `disable`
  - Call `get_service_state()` first; return `SKIPPED` if already in desired state
  - Build `systemctl <action> auditd` command
  - Honour `dry_run` flag; return `DRY_RUN` result without calling runner
  - Pass `elevated` flag to runner; parse result into `AuditActionResult`
  - _Requirements: 2_

- [ ] 7. Implement `restart_service()`
  - Build `service auditd restart` command (NOT `systemctl restart`)
  - Honour `dry_run` flag
  - Pass `elevated` flag to runner; parse result into `AuditActionResult`
  - _Requirements: 2.1_

- [ ] 8. Implement `list_active_rules()`
  - Build `auditctl -l` command
  - Treat the "No rules" output as an empty list return
  - Parse each rule into an `AuditRule` record with `raw`, `filter_list`, `action`,
    and `key` populated where parseable
  - Raise `AuditQueryError` for non-zero exit codes
  - _Requirements: 3_

- [ ] 9. Implement `add_runtime_rule()`
  - Call `get_kernel_state()`; if `enabled == 2`, return `FAILED` with an
    `AuditImmutableError` note and do not invoke the runner
  - Call `list_active_rules()`; return `SKIPPED` if the same rule is already active
  - Build `auditctl -a <rule-args>` command (or the appropriate `auditctl` syntax
    for the supplied argument list)
  - Honour `dry_run` flag
  - Pass `elevated` flag to runner; parse result into `AuditActionResult`
  - _Requirements: 4_

- [ ] 10. Implement `remove_runtime_rule()` and `flush_runtime_rules()`
  - For `remove_runtime_rule(rule_args)`: if kernel is immutable return `FAILED`
    with `AuditImmutableError` note; if the rule is not currently active return
    `SKIPPED`; otherwise invoke `auditctl -d <rule-args>`
  - For `flush_runtime_rules()`: if kernel is immutable return `FAILED` with
    `AuditImmutableError` note; otherwise invoke `auditctl -D`
  - Honour `dry_run` flag on both methods
  - Pass `elevated` flag to runner on both methods; parse result into
    `AuditActionResult`
  - _Requirements: 5_

- [ ] 11. Implement `list_rule_files()`, `write_rule_file()`, and `remove_rule_file()`
  - `list_rule_files()`: enumerate `/etc/audit/rules.d/*.rules` via the file operator
    and return each as an `AuditRuleFile` with `name`, `path`, and `size_bytes`
  - `write_rule_file(name, content)`: compare against existing content; return
    `SKIPPED` if identical; otherwise write to `/etc/audit/rules.d/<name>.rules`
    with mode `0o640`, owner `root`, group `root`, and elevated privileges
  - `remove_rule_file(name)`: return `SKIPPED` if the file does not exist; otherwise
    delete `/etc/audit/rules.d/<name>.rules`
  - Honour `dry_run` flag on the two mutating methods
  - _Requirements: 6_

- [ ] 12. Implement `reload_rules()`
  - Call `get_kernel_state()`; if `enabled == 2`, return `FAILED` with an
    `AuditImmutableError` note and do not invoke the runner
  - Build `augenrules --load` command
  - Honour `dry_run` flag
  - Pass `elevated` flag to runner; parse result into `AuditActionResult`
  - _Requirements: 7_

- [ ] 13. Implement `get_config_value()`, `set_config_value()`, and `delete_config_key()`
  - `get_config_value(key)`: read `/etc/audit/auditd.conf` via file operator, parse
    `key = value` lines, and return the trimmed value or `None` if absent
  - `set_config_value(key, value)`: call `get_config_value(key)` first; return
    `SKIPPED` if already equal; otherwise update the matching line in place or
    append a new `key = value` line and write the file back with mode `0o640`,
    owner `root`, group `root`, and elevated privileges. Include a `note`
    indicating that a service restart may be required
  - `delete_config_key(key)`: remove the matching line and write back; return
    `SKIPPED` if the key is absent
  - Honour `dry_run` flag on both mutating methods
  - _Requirements: 8_

- [ ] 14. Implement `search_events()`
  - Accept optional filter parameters: `key`, `message_type`, `user_id`, `time_start`,
    `time_end`, `executable`
  - Build `ausearch` command with the corresponding flags (`-k`, `-m`, `-ui`,
    `-ts`, `-te`, `-x`) for each supplied filter
  - Treat the documented "no results" exit code as an empty list return
  - Parse each event block into an `AuditEvent` with `event_id`, `timestamp`,
    `event_type`, `serial`, and `raw` populated
  - Raise `AuditQueryError` for other non-zero exit codes
  - _Requirements: 9_

- [ ] 15. Implement `get_summary_report()`
  - Accept an optional `category` parameter: `summary` (default), `users`,
    `executables`, `failed-auth`
  - Build `aureport` command with the corresponding flag (`--summary`, `-u`, `-x`,
    `-au --failed`)
  - Append `-ts <time_start>` and `-te <time_end>` when supplied
  - Parse the output into an `AuditReport` with `category`, `raw`, and (where
    feasible) a `parsed` dict of key/value pairs
  - Raise `AuditQueryError` on non-zero exit codes
  - _Requirements: 10_

- [ ] 16. Implement `get_kernel_state()`
  - Build `auditctl -s` command
  - Parse output fields into `AuditKernelState`: `enabled`, `failure`, `pid`,
    `rate_limit`, `backlog_limit`, `backlog`, `lost`
  - Raise `AuditQueryError` on non-zero exit codes
  - _Requirements: 11_

- [ ] 17. Create test module and mock infrastructure
  - Create `source/testroots/testbase/automojo/tests/sysman/linux/redhat/test_auditmanagement.py`
  - Implement `MockCommandRunner` that returns configurable `RunResult` values
  - Implement `MockFileOperator` backed by an in-memory dict for paths, content,
    and metadata
  - Set up hypothesis testing framework configuration
  - Create test fixtures for common test scenarios (service present/absent, kernel
    immutable/mutable, rule lists, config files, rule drop-ins)
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12_

- [ ] 18. Write property-based test for lifecycle idempotency
  - **Property 1: Lifecycle Idempotency**
  - Generate random current/target service states
  - Verify `set_service_state()` returns `SKIPPED` when already in desired state
  - **Validates: Requirements 2.2**
  - _Requirements: 2_

- [ ] 19. Write property-based test for runtime rule add idempotency
  - **Property 2: Runtime Rule Add Idempotency**
  - Generate random rule argument lists
  - Verify `add_runtime_rule()` returns `SKIPPED` when the rule is already active
  - **Validates: Requirements 4.3**
  - _Requirements: 4_

- [ ] 20. Write property-based test for runtime rule remove idempotency
  - **Property 3: Runtime Rule Remove Idempotency**
  - Generate random rule argument lists
  - Verify `remove_runtime_rule()` returns `SKIPPED` when the rule is not active
  - **Validates: Requirements 5.3**
  - _Requirements: 5_

- [ ] 21. Write property-based test for rules.d drop-in round trip
  - **Property 4: Rules.d Drop-In Round Trip**
  - Generate random drop-in names and content
  - Verify first write returns `SUCCESS`, second write of same content returns
    `SKIPPED`, and `list_rule_files()` reports matching name and size
  - **Validates: Requirements 6.2, 6.4**
  - _Requirements: 6_

- [ ] 22. Write property-based test for immutable-mode refusal
  - **Property 5: Immutable-Mode Refusal**
  - Generate random mutating rule invocations with kernel state `enabled == 2`
  - Verify each returns `FAILED` with an `AuditImmutableError` note and the runner
    is never invoked with the mutating command
  - **Validates: Requirements 4.2, 7.2**
  - _Requirements: 4, 7_

- [ ] 23. Write property-based test for reload skip independence
  - **Property 6: Reload Skip When Nothing Changed**
  - Generate random sequences of `write_rule_file()` calls that return `SKIPPED`
  - Verify `augenrules --load` is never invoked until `reload_rules()` is explicitly
    called
  - **Validates: Requirements 6.2, 7.1**
  - _Requirements: 6, 7_

- [ ] 24. Write property-based test for dry-run safety
  - **Property 7: Dry-Run Safety**
  - Generate random mutating operations with `dry_run=True`
  - Verify neither the command runner nor the file operator receives a mutating call
  - Verify all results have `DRY_RUN` status
  - **Validates: Requirements 2.5, 4.6, 5.5, 6.5, 7.5, 8.6**
  - _Requirements: 2, 4, 5, 6, 7, 8_

- [ ] 25. Write property-based test for elevation consistency
  - **Property 8: Elevation Consistency**
  - Generate random operations with `elevated=True`/`False`
  - Verify mutating operations pass `elevated` flag correctly to runner and file
    operator
  - Verify read-only operations never request elevation unless falling back on
    unreadable files
  - **Validates: Requirements 12.1, 12.2, 12.3**
  - _Requirements: 12_

- [ ] 26. Write property-based test for search empty-result contract
  - **Property 9: Search Empty-Result Contract**
  - Generate random filter inputs where `ausearch` exits with the "no results"
    exit code
  - Verify the returned list is empty and no exception is raised
  - **Validates: Requirements 9.3**
  - _Requirements: 9_

- [ ] 27. Write property-based test for status mapping correctness
  - **Property 10: Status Mapping Correctness**
  - Generate random exit codes and outcomes from runner and file operator
  - Verify correct `AuditActionResult` status mapping across mutating operations
  - **Validates: Requirements 2.3, 2.4, 4.4, 4.5, 5.4, 6.3, 7.3, 7.4, 8.4, 8.5**
  - _Requirements: 2, 4, 5, 6, 7, 8_

- [ ] 28. Write property-based test for config set idempotency
  - **Property 11: Config Set Idempotency**
  - Generate random key/value pairs
  - Verify `set_config_value()` returns `SKIPPED` and performs no write when the
    current value already matches
  - **Validates: Requirements 8.4**
  - _Requirements: 8_

- [ ] 29. Write property-based test for restart uses `service` not `systemctl`
  - **Property 12: Restart Uses `service` Not `systemctl`**
  - Invoke `restart_service()` under many runner configurations
  - Verify the command passed to the runner starts with `service auditd restart`
    and never with `systemctl restart auditd`
  - **Validates: Requirements 2.1**
  - _Requirements: 2_

- [ ] 30. Write unit tests for `get_service_state()`
  - Test: unit active and enabled — returns populated `AuditServiceState`
  - Test: unit not present — returns `present=False` with other fields unset
  - Test: unexpected exit code — raises `AuditQueryError`
  - _Requirements: 1_

- [ ] 31. Write unit tests for `set_service_state()` and `restart_service()`
  - Test: start when already active — returns `SKIPPED`, runner not called
  - Test: start when inactive — runner called with correct args, returns `SUCCESS`
  - Test: lifecycle failure — returns `FAILED` with exit code and stderr
  - Test: dry-run mode — returns `DRY_RUN`, runner not called
  - Test: elevated mode — runner called with `elevated=True`
  - Test: `restart_service()` — runner called with `service auditd restart`
    and never with `systemctl restart auditd`
  - _Requirements: 2_

- [ ] 32. Write unit tests for `list_active_rules()`
  - Test: populated rule output — returns parsed `AuditRule` list with
    `filter_list`, `action`, `key` populated where present
  - Test: `No rules` output — returns empty list
  - Test: unexpected exit code — raises `AuditQueryError`
  - _Requirements: 3_

- [ ] 33. Write unit tests for `add_runtime_rule()`, `remove_runtime_rule()`, and `flush_runtime_rules()`
  - Test: add when rule not present — runner called with correct args, returns `SUCCESS`
  - Test: add when rule already present — returns `SKIPPED`, runner not called
  - Test: remove when rule not present — returns `SKIPPED`, runner not called
  - Test: remove when rule present — runner called with correct args, returns `SUCCESS`
  - Test: flush — runner called with `auditctl -D`, returns `SUCCESS`
  - Test: kernel immutable — all three return `FAILED` with `AuditImmutableError` note,
    runner not called with mutating command
  - Test: dry-run mode — all three return `DRY_RUN`, runner not called
  - _Requirements: 4, 5_

- [ ] 34. Write unit tests for `list_rule_files()`, `write_rule_file()`, and `remove_rule_file()`
  - Test: list returns one `AuditRuleFile` per `.rules` drop-in
  - Test: write with new content — file operator called with correct path,
    content, mode `0o640`, owner `root`, group `root`, `elevated=True`,
    returns `SUCCESS`
  - Test: write with identical content — returns `SKIPPED`, file operator not
    called for write
  - Test: remove existing file — file operator called with delete, returns `SUCCESS`
  - Test: remove non-existent file — returns `SKIPPED`, file operator not called
    for delete
  - Test: dry-run mode on write and remove — returns `DRY_RUN`, file operator not
    called
  - _Requirements: 6_

- [ ] 35. Write unit tests for `reload_rules()`
  - Test: kernel mutable, reload succeeds — runner called with `augenrules --load`,
    returns `SUCCESS`
  - Test: kernel immutable — returns `FAILED` with `AuditImmutableError` note,
    runner not called with mutating command
  - Test: reload failure — returns `FAILED` with exit code and stderr
  - Test: dry-run mode — returns `DRY_RUN`, runner not called
  - _Requirements: 7_

- [ ] 36. Write unit tests for `get_config_value()`, `set_config_value()`, and `delete_config_key()`
  - Test: get existing key — returns trimmed value
  - Test: get absent key — returns `None`
  - Test: set with new value — file operator called with correct path, content,
    mode `0o640`, owner `root`, group `root`, `elevated=True`; returns `SUCCESS`
    with a `note` indicating restart may be required
  - Test: set with identical current value — returns `SKIPPED`, no file write
  - Test: set when key absent — appends key/value line and writes file, returns `SUCCESS`
  - Test: delete existing key — writes file without that line, returns `SUCCESS`
  - Test: delete absent key — returns `SKIPPED`, no file write
  - Test: dry-run mode on set and delete — returns `DRY_RUN`, no file write
  - _Requirements: 8_

- [ ] 37. Write unit tests for `search_events()`
  - Test: no filters — runner called with bare `ausearch`; result parsed into events
  - Test: each filter individually — `-k`, `-m`, `-ui`, `-ts`, `-te`, `-x` appear
    in the command args
  - Test: multiple filters combined — all flags appear in correct order
  - Test: "no results" exit code — returns empty list, no exception
  - Test: unexpected exit code — raises `AuditQueryError`
  - _Requirements: 9_

- [ ] 38. Write unit tests for `get_summary_report()`
  - Test: default category — runner called with `aureport --summary`
  - Test: users category — runner called with `aureport -u`
  - Test: executables category — runner called with `aureport -x`
  - Test: failed-auth category — runner called with `aureport -au --failed`
  - Test: time range supplied — `-ts` and `-te` appended
  - Test: parsed structure populated where feasible
  - Test: unexpected exit code — raises `AuditQueryError`
  - _Requirements: 10_

- [ ] 39. Write unit tests for `get_kernel_state()`
  - Test: parseable output — returns `AuditKernelState` with all fields populated
  - Test: `enabled 2` — returns record with `enabled=2` (immutable)
  - Test: unexpected exit code — raises `AuditQueryError`
  - _Requirements: 11_

- [ ] 40. Checkpoint - Ensure all tests pass
  - Run all unit tests and property-based tests
  - Verify all properties pass with 100+ iterations
  - Ensure all tests pass, ask the user if questions arise

