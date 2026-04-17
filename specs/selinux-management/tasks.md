# Implementation Plan

- [ ] 1. Create the `selinuxmanagement` module file
  - Create `source/packages/automojo/sysman/linux/redhat/selinuxmanagement.py`
  - Add module-level docstring describing purpose, coordinator-driven design, and the
    relationship to the `security-compliance` feature (runtime mode vs. persistent mode)
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18_

- [ ] 2. Define status enum and data-model dataclasses
  - Implement `SELinuxActionStatus` enum with values: `SUCCESS`, `FAILED`, `SKIPPED`,
    `DRY_RUN`
  - Implement `SELinuxActionResult` dataclass with fields: `status`, `target`, `changed`,
    `exit_code`, `stdout`, `stderr`, `command`, `warning`
  - Implement `SELinuxContext` dataclass with fields: `user`, `role`, `type_`, `level`
  - Implement `SELinuxFileContextRule` dataclass with fields: `path_regex`, `file_type`,
    `selinux_type`
  - Implement `SELinuxPortRule` dataclass with fields: `selinux_type`, `protocol`,
    `port_range`
  - Implement `SELinuxBoolean` dataclass with fields: `name`, `current_value`,
    `default_value`, `description`
  - Implement `SELinuxModule` dataclass with fields: `name`, `priority`
  - Implement `SELinuxGeneratedModule` dataclass with fields: `module_name`, `pp_path`,
    `te_path`, `raw_rules`
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17_

- [ ] 3. Define typed exceptions
  - Implement `SELinuxQueryError` with `exit_code` and `stderr` attributes
  - Implement `SELinuxPathNotFoundError` with `path` attribute
  - Implement `SELinuxBooleanNotFoundError` with `name` attribute
  - _Requirements: 1.3, 6.4, 7.3, 9.3, 12.4, 13.1, 15.3, 17.4_

- [ ] 4. Implement `SELinuxManagerConfig` dataclass
  - Add `dry_run: bool = False` field
  - Add `elevated: bool = False` field
  - _Requirements: 2.7, 3.6, 4.5, 5.4, 8.6, 10.5, 11.4, 13.6, 14.4, 16.6, 17.6, 18_

- [ ] 5. Implement `SELinuxManager.__init__` and context query methods
  - Accept `ICommandRunner`, `IFileOperator`, and `SELinuxManagerConfig` as constructor
    arguments and assign each to `self`
  - Implement `query_context(path)` using `stat --printf='%C' <path>`
  - Parse `user:role:type:level` output into a `SELinuxContext`
  - Return `None` when SELinux is disabled (context string is empty or `?`)
  - Raise `SELinuxPathNotFoundError` when `stat` reports no such file
  - _Requirements: 1_

- [ ] 6. Implement `set_context()` for chcon-based type/user/role changes
  - Call `query_context()` to compare to requested values; return `SKIPPED` if already
    matching
  - Build `chcon` command: add `-R` when `recursive=True`, `-t <type>`, optional
    `-u <user>` and `-r <role>`
  - Honour `dry_run` flag; return `DRY_RUN` without calling runner
  - Pass `elevated` flag to runner; map result into `SELinuxActionResult`
  - _Requirements: 2_

- [ ] 7. Implement `restore_context()` for restorecon-driven relabel
  - Build `restorecon -v` command: add `-R` when `recursive=True`, `-F` when `force=True`
  - Detect "no change needed" case from `restorecon` output and set `changed=False` on
    success
  - Honour `dry_run` flag
  - Pass `elevated` flag to runner; map result into `SELinuxActionResult`
  - _Requirements: 3_

- [ ] 8. Implement persistent fcontext rule management
  - Implement `add_fcontext_rule(path_regex, type_, file_type=None)`:
    - Use `list_fcontext_rules(local_only=True)` to decide between `-a` and `-m`
    - Build `semanage fcontext -a|-m -t <type> "<path-regex>"`
    - Honour `dry_run`; pass `elevated` flag to runner
  - Implement `remove_fcontext_rule(path_regex)`:
    - Use `list_fcontext_rules(local_only=True)` to short-circuit with `SKIPPED` when
      absent
    - Build `semanage fcontext -d "<path-regex>"`
    - Honour `dry_run`; pass `elevated` flag to runner
  - Implement `list_fcontext_rules(local_only=True)`:
    - Build `semanage fcontext -l` or `semanage fcontext -l -C`
    - Parse output lines into `SELinuxFileContextRule` records
    - Raise `SELinuxQueryError` on unexpected non-zero exit code
  - _Requirements: 4, 5, 6_

- [ ] 9. Implement boolean query, set, and metadata listing
  - Implement `get_boolean(name)`:
    - Invoke `getsebool <name>`; parse `on`/`off` to `True`/`False`
    - Raise `SELinuxBooleanNotFoundError` when getsebool reports unknown name
  - Implement `set_boolean(name, value, persistent=False)`:
    - Call `get_boolean()` first; return `SKIPPED` if current value already matches
    - Build `setsebool <name> on|off` (append `-P` when `persistent=True`)
    - Honour `dry_run`; pass `elevated` flag to runner when persistent
  - Implement `list_booleans()`:
    - Invoke `semanage boolean -l`; parse into `SELinuxBoolean` records
    - Raise `SELinuxQueryError` on unexpected non-zero exit code
  - _Requirements: 7, 8, 9_

- [ ] 10. Implement port-context management
  - Implement `add_port_context(port, protocol, type_)`:
    - Consult `list_port_contexts(local_only=True)` to decide between `-a` and `-m`
    - Build `semanage port -a|-m -t <type> -p <proto> <port>`
    - Honour `dry_run`; pass `elevated` flag to runner
  - Implement `remove_port_context(port, protocol)`:
    - Consult `list_port_contexts(local_only=True)`; return `SKIPPED` when absent
    - Build `semanage port -d -p <proto> <port>`
    - Honour `dry_run`; pass `elevated` flag to runner
  - Implement `list_port_contexts(local_only=False)`:
    - Build `semanage port -l` or `semanage port -l -C`
    - Parse output lines into `SELinuxPortRule` records
    - Raise `SELinuxQueryError` on unexpected non-zero exit code
  - _Requirements: 10, 11, 12_

- [ ] 11. Implement policy-module install, remove, and listing
  - Implement `install_module(filepath)`:
    - Verify the path exists via `self._fileop.exists()`; raise
      `SELinuxPathNotFoundError` when absent
    - Consult `list_modules()`; return `SKIPPED` when module is already installed at the
      same priority
    - Build `semodule -i <path>`; honour `dry_run`; pass `elevated` flag
  - Implement `remove_module(name)`:
    - Consult `list_modules()`; return `SKIPPED` when module not installed
    - Build `semodule -r <name>`; honour `dry_run`; pass `elevated` flag
  - Implement `list_modules()`:
    - Invoke `semodule -l`; parse lines into `SELinuxModule` records
    - Raise `SELinuxQueryError` on unexpected non-zero exit code
  - _Requirements: 13, 14, 15_

- [ ] 12. Implement persistent-mode management via `/etc/selinux/config`
  - Implement `set_persistent_mode(mode)`:
    - Validate `mode` is one of `enforcing`, `permissive`, `disabled`
    - Use `IFileOperator.read_text("/etc/selinux/config")` to read current config
    - Parse the `SELINUX=` line; return `SKIPPED` if value already matches
    - Build new config contents with the updated `SELINUX=` line
    - Honour `dry_run`; otherwise call `IFileOperator.write_text(..., elevated=...)`
    - When `mode == "disabled"`, set `warning="reboot required"` on the returned
      `SELinuxActionResult`
  - _Requirements: 16_

- [ ] 13. Implement audit2allow-driven module generation
  - Implement `generate_allow_module(module_name, working_dir)`:
    - Always invoke `audit2allow -a` (preview) via the command runner
    - When not in `dry_run`, additionally invoke `audit2allow -a -M <module-name>` with
      `cwd=working_dir` and populate `pp_path` / `te_path` based on the working directory
      and module name
    - When in `dry_run`, return `SELinuxGeneratedModule` with `pp_path=None` and
      `te_path=None` and populate only `raw_rules`
    - Raise `SELinuxQueryError` when the preview command fails
    - Do NOT install the generated module; installation remains the caller's
      responsibility via `install_module()`
  - _Requirements: 17_

- [ ] 14. Create test module and mock infrastructure
  - Create `source/testroots/testbase/automojo/tests/sysman/linux/redhat/test_selinuxmanagement.py`
  - Implement `MockCommandRunner` that returns configurable `RunResult` values and
    records every invocation (args, elevated flag)
  - Implement `MockFileOperator` that simulates `/etc/selinux/config` contents in-memory
    and records every `write_text()` invocation (path, data, elevated flag)
  - Set up hypothesis testing framework configuration
  - Create test fixtures for common test scenarios (default config, enforcing config,
    disabled config, known booleans, known modules)
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18_

- [ ] 15. Write property-based test for context read-write round-trip
  - **Property 1: Context Read-Write Round-Trip**
  - Generate random valid paths and SELinux types
  - Verify `query_context()` after `set_context()` returns the requested type
  - **Validates: Requirements 1.2, 2.5**
  - _Requirements: 1, 2_

- [ ] 16. Write property-based test for set-context idempotency
  - **Property 2: Set-Context Idempotency**
  - Generate random path/type pairs already in target state
  - Verify `set_context()` returns `SKIPPED` and the runner is not invoked
  - **Validates: Requirements 2.4**
  - _Requirements: 2_

- [ ] 17. Write property-based test for restorecon no-op semantics
  - **Property 3: Restorecon No-Op When Context Matches Default**
  - Generate paths whose current context matches default
  - Verify `restore_context()` returns `SUCCESS` with `changed=False`
  - **Validates: Requirements 3.4**
  - _Requirements: 3_

- [ ] 18. Write property-based test for boolean set idempotency
  - **Property 4: Boolean Set Idempotency**
  - Generate random boolean names with pre-set values
  - Verify `set_boolean()` returns `SKIPPED` when the target matches current
  - **Validates: Requirements 8.3**
  - _Requirements: 8_

- [ ] 19. Write property-based test for persistent-mode idempotency
  - **Property 5: Persistent-Mode Idempotency**
  - Generate random modes already recorded in `/etc/selinux/config`
  - Verify `set_persistent_mode()` returns `SKIPPED` and the file operator is not invoked
    for writes
  - **Validates: Requirements 16.2**
  - _Requirements: 16_

- [ ] 20. Write property-based test for policy-module install idempotency
  - **Property 6: Policy-Module Install Idempotency**
  - Generate random module names already present in `list_modules()` output
  - Verify `install_module()` returns `SKIPPED` and `semodule -i` is not invoked
  - Verify `remove_module()` returns `SKIPPED` when module is absent
  - **Validates: Requirements 13.3, 14.2**
  - _Requirements: 13, 14_

- [ ] 21. Write property-based test for fcontext add-then-remove symmetry
  - **Property 7: Fcontext Add-Then-Remove Symmetry**
  - Generate random path regexes and types
  - Verify `add_fcontext_rule()` followed by `remove_fcontext_rule()` leaves
    `list_fcontext_rules(local_only=True)` without an entry for that regex
  - **Validates: Requirements 4.3, 5.3, 6.3**
  - _Requirements: 4, 5, 6_

- [ ] 22. Write property-based test for port-context add-then-remove symmetry
  - **Property 8: Port-Context Add-Then-Remove Symmetry**
  - Generate random `(port, protocol, type)` triples
  - Verify add followed by remove leaves `list_port_contexts(local_only=True)` without
    an entry
  - **Validates: Requirements 10.3, 11.3, 12.3**
  - _Requirements: 10, 11, 12_

- [ ] 23. Write property-based test for dry-run safety
  - **Property 9: Dry-Run Safety**
  - Generate random mutating operations with `dry_run=True`
  - Verify neither the command runner nor the file operator is invoked for writes
  - Verify all results have `DRY_RUN` status
  - **Validates: Requirements 2.7, 3.6, 4.5, 5.4, 8.6, 10.5, 11.4, 13.6, 14.4, 16.6, 17.6**
  - _Requirements: 2, 3, 4, 5, 8, 10, 11, 13, 14, 16, 17_

- [ ] 24. Write property-based test for elevation consistency
  - **Property 10: Elevation Consistency**
  - Generate random operations with `elevated=True/False`
  - Verify mutating commands (`chcon`, `restorecon`, `setsebool -P`, `semanage -a|-m|-d`,
    `semodule -i|-r`) pass `elevated=True`
  - Verify read-only commands (`stat -Z`, `getsebool`, `semanage -l`, `semodule -l`) never
    pass `elevated=True`
  - **Validates: Requirements 18.1, 18.2, 18.3**
  - _Requirements: 18_

- [ ] 25. Write property-based test for path-not-found validation
  - **Property 11: Path-Not-Found Validation**
  - Generate random non-existent paths
  - Verify `install_module()` raises `SELinuxPathNotFoundError` before the runner is
    invoked
  - **Validates: Requirements 13.1**
  - _Requirements: 13_

- [ ] 26. Write property-based test for status mapping correctness
  - **Property 12: Status Mapping Correctness**
  - Generate random exit codes from the command runner
  - Verify `0` maps to `SUCCESS` and non-zero maps to `FAILED` on all mutating operations
  - **Validates: Requirements 2.5, 2.6, 3.5, 4.3, 4.4, 8.4, 8.5, 10.4, 13.4, 13.5, 16.4, 16.5**
  - _Requirements: 2, 3, 4, 8, 10, 13, 16_

- [ ] 27. Write property-based test for boolean-not-found propagation
  - **Property 13: Boolean Not-Found Propagation**
  - Generate random unknown boolean names
  - Verify `get_boolean()` raises `SELinuxBooleanNotFoundError` (never returns `False`)
  - **Validates: Requirements 7.3**
  - _Requirements: 7_

- [ ] 28. Write property-based test for audit2allow read-only dry-run
  - **Property 14: audit2allow Read-Only When Dry-Run**
  - Generate random module names and working directories with `dry_run=True`
  - Verify only `audit2allow -a` is invoked and `audit2allow -M` is not
  - Verify the returned `SELinuxGeneratedModule` has `pp_path=None` and `te_path=None`
  - **Validates: Requirements 17.1, 17.5, 17.6**
  - _Requirements: 17_

- [ ] 29. Write unit tests for `query_context()`
  - Test: SELinux-enabled path with a populated context — returns populated
    `SELinuxContext`
  - Test: path does not exist — raises `SELinuxPathNotFoundError`
  - Test: SELinux disabled — returns `None`
  - _Requirements: 1_

- [ ] 30. Write unit tests for `set_context()`
  - Test: type mismatch — runner called with `chcon -t <type> <path>`, returns `SUCCESS`
  - Test: recursive mode — runner called with `chcon -R -t <type> <path>`
  - Test: user/role supplied — runner called with `-u` / `-r` flags appended
  - Test: path already has requested context — returns `SKIPPED`, runner not called
  - Test: chcon failure — returns `FAILED` with exit code and stderr
  - Test: dry-run mode — returns `DRY_RUN`, runner not called
  - Test: elevated mode — runner called with `elevated=True`
  - _Requirements: 2_

- [ ] 31. Write unit tests for `restore_context()`
  - Test: path with drift — runner called with `restorecon -v <path>`, returns `SUCCESS`
    with `changed=True`
  - Test: recursive mode — runner called with `-R`
  - Test: force mode — runner called with `-F`
  - Test: no drift — returns `SUCCESS` with `changed=False`
  - Test: restorecon failure — returns `FAILED` with exit code and stderr
  - Test: dry-run mode — returns `DRY_RUN`, runner not called
  - _Requirements: 3_

- [ ] 32. Write unit tests for fcontext rule methods
  - Test: add new rule — runner called with `semanage fcontext -a`, returns `SUCCESS`
  - Test: add when regex already exists — runner called with `semanage fcontext -m`
  - Test: remove existing rule — runner called with `semanage fcontext -d`, returns
    `SUCCESS`
  - Test: remove when rule absent — returns `SKIPPED`, runner not called
  - Test: list local rules — runner called with `semanage fcontext -l -C`, returns parsed
    list
  - Test: list all rules — runner called with `semanage fcontext -l`
  - Test: list failure — raises `SELinuxQueryError`
  - Test: dry-run mode on add and remove — returns `DRY_RUN`, runner not called
  - _Requirements: 4, 5, 6_

- [ ] 33. Write unit tests for boolean methods
  - Test: `get_boolean()` returns `True` for `on` output
  - Test: `get_boolean()` returns `False` for `off` output
  - Test: `get_boolean()` on unknown name — raises `SELinuxBooleanNotFoundError`
  - Test: `set_boolean()` runtime-only — runner called with `setsebool <name> on|off`
  - Test: `set_boolean()` persistent — runner called with `setsebool -P`
  - Test: `set_boolean()` when already at target value — returns `SKIPPED`, runner not
    called
  - Test: `set_boolean()` failure — returns `FAILED` with exit code and stderr
  - Test: `set_boolean()` dry-run — returns `DRY_RUN`, runner not called
  - Test: `list_booleans()` — runner called with `semanage boolean -l`, returns parsed
    list
  - Test: `list_booleans()` failure — raises `SELinuxQueryError`
  - _Requirements: 7, 8, 9_

- [ ] 34. Write unit tests for port-context methods
  - Test: `add_port_context()` new — runner called with `semanage port -a`
  - Test: `add_port_context()` existing — runner called with `semanage port -m`
  - Test: `remove_port_context()` existing — runner called with `semanage port -d`,
    returns `SUCCESS`
  - Test: `remove_port_context()` absent — returns `SKIPPED`, runner not called
  - Test: `list_port_contexts()` all — runner called with `semanage port -l`
  - Test: `list_port_contexts()` local only — runner called with `semanage port -l -C`
  - Test: list failure — raises `SELinuxQueryError`
  - Test: dry-run on add and remove — returns `DRY_RUN`, runner not called
  - _Requirements: 10, 11, 12_

- [ ] 35. Write unit tests for policy-module methods
  - Test: `install_module()` with valid path — runner called with `semodule -i <path>`,
    returns `SUCCESS`
  - Test: `install_module()` with missing path — raises `SELinuxPathNotFoundError` before
    runner is called
  - Test: `install_module()` when already installed at same priority — returns `SKIPPED`
  - Test: `install_module()` failure — returns `FAILED` with exit code and stderr
  - Test: `install_module()` dry-run — returns `DRY_RUN`, runner not called
  - Test: `remove_module()` existing — runner called with `semodule -r <name>`, returns
    `SUCCESS`
  - Test: `remove_module()` absent — returns `SKIPPED`, runner not called
  - Test: `remove_module()` dry-run — returns `DRY_RUN`, runner not called
  - Test: `list_modules()` — runner called with `semodule -l`, returns parsed list
  - Test: `list_modules()` failure — raises `SELinuxQueryError`
  - _Requirements: 13, 14, 15_

- [ ] 36. Write unit tests for `set_persistent_mode()`
  - Test: mode mismatch enforcing→permissive — file operator `write_text()` called with
    updated `SELINUX=permissive` line, returns `SUCCESS`
  - Test: mode already matches — returns `SKIPPED`, file operator `write_text()` not
    called
  - Test: mode change to `disabled` — returns `SUCCESS` with `warning="reboot required"`
  - Test: write failure — returns `FAILED` with captured error
  - Test: dry-run mode — returns `DRY_RUN`, file operator `write_text()` not called
  - Test: elevated mode — file operator `write_text()` called with `elevated=True`
  - _Requirements: 16_

- [ ] 37. Write unit tests for `generate_allow_module()`
  - Test: preview succeeds — runner called with `audit2allow -a`, returns populated
    `raw_rules`
  - Test: compile succeeds — runner additionally called with `audit2allow -a -M <name>`
    in `working_dir`, returns populated `pp_path` and `te_path`
  - Test: preview failure — raises `SELinuxQueryError`
  - Test: dry-run — only `audit2allow -a` invoked; `pp_path` and `te_path` are `None`
  - Test: generated module is NOT auto-installed (no `semodule -i` call is made)
  - _Requirements: 17_

- [ ] 38. Checkpoint - Ensure all tests pass
  - Run all unit tests and property-based tests
  - Verify all properties pass with 100+ iterations
  - Ensure all tests pass, ask the user if questions arise
