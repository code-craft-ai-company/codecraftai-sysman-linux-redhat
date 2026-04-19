# Implementation Plan

- [ ] 1. Create the `subscriptionmanagement` module file
  - Create `source/packages/automojo/sysman/linux/redhat/subscriptionmanagement.py`
  - Add module-level docstring describing purpose and coordinator-driven design
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12_

- [ ] 2. Define status enum and result dataclasses
  - Implement `SubscriptionActionStatus` enum with values: `SUCCESS`, `FAILED`,
    `SKIPPED`, `DRY_RUN`
  - Implement `SubscriptionOverallStatus` enum with values: `CURRENT`, `INSUFFICIENT`,
    `INVALID`, `DISABLED`, `UNKNOWN`
  - Implement `SubscriptionActionResult` dataclass with fields: `status`, `operation`,
    `system_uuid`, `attached_pool_ids`, `exit_code`, `stdout`, `stderr`, `command`
  - Implement `SubscriptionIdentity` dataclass with fields: `uuid`, `name`,
    `organization`
  - Implement `SubscriptionStatus` dataclass with fields: `overall`, `raw_output`
  - Implement `SubscriptionRepo` dataclass with fields: `repo_id`, `name`, `url`,
    `enabled`
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10_

- [ ] 3. Define typed exceptions
  - Implement `SubscriptionQueryError` with `exit_code` and `stderr` attributes
  - Implement `SubscriptionNotRegisteredError` (optional) for mutating operations that
    require registration
  - _Requirements: 3.4, 4.4, 7.5_

- [ ] 4. Implement `SubscriptionManagerConfig` dataclass
  - Add `dry_run: bool = False` field
  - Add `elevated: bool = False` field
  - _Requirements: 1.7, 2.5, 5.6, 6.6, 8.6, 9.6, 10.4, 11_

- [ ] 5. Implement `SubscriptionManager.__init__`, `identity()`, and `status()`
  - Accept `ICommandRunner` and `SubscriptionManagerConfig` as constructor arguments
  - Implement `identity()` using `subscription-manager identity`; return `None` when the
    runner reports the well-known not-registered exit code
  - Raise `SubscriptionQueryError` for any other non-zero exit code
  - Implement `status()` using `subscription-manager status`; parse the `Overall Status`
    line and map it to `SubscriptionOverallStatus` (with `UNKNOWN` as the fallback)
  - _Requirements: 3, 4_

- [ ] 6. Implement `register()`
  - Call `identity()` to check current state; return `SKIPPED` when already registered
    and `force=False`
  - Build `subscription-manager register` command with either
    `--username/--password` or `--org/--activationkey`, appending `--force` when
    requested
  - Honour `dry_run` flag; return `DRY_RUN` result without calling runner (with
    credentials redacted from the returned command)
  - Pass `elevated` flag to runner; parse result into `SubscriptionActionResult` and
    populate `system_uuid` when available
  - _Requirements: 1_

- [ ] 7. Implement `unregister()`
  - Call `identity()` to check current state; return `SKIPPED` if not registered
  - Build `subscription-manager unregister` command
  - Honour `dry_run` flag
  - Pass `elevated` flag to runner; parse result into `SubscriptionActionResult`
  - _Requirements: 2_

- [ ] 8. Implement `attach()`
  - Support `auto=True` (issue one `attach --auto`) and `auto=False` with one command
    per supplied pool id
  - Append `--quantity <n>` when a quantity is provided
  - Raise `SemanticError` when `auto=False` and `pool_ids` is empty/`None`
  - Honour `dry_run` flag
  - Pass `elevated` flag to runner; parse result into `SubscriptionActionResult` and
    populate `attached_pool_ids`
  - _Requirements: 5_

- [ ] 9. Implement `remove()`
  - Query current attached subscriptions; return `SKIPPED` when none are attached
  - Support `all=True` (issue `remove --all`) and a list of serial numbers (one
    `remove --serial` per entry)
  - Honour `dry_run` flag
  - Pass `elevated` flag to runner; parse result into `SubscriptionActionResult`
  - _Requirements: 6_

- [ ] 10. Implement `list_repos()`, `enable_repo()`, and `disable_repo()`
  - Implement `list_repos(filter=None)` using `repos --list`, `repos --list-enabled`, or
    `repos --list-disabled` based on the filter
  - Parse output into `list[SubscriptionRepo]`; raise `SubscriptionQueryError` on failure
  - For `enable_repo()`: call `list_repos()` to detect already-enabled state; return
    `SKIPPED` if already enabled
  - For `disable_repo()`: call `list_repos()` to detect already-disabled state; return
    `SKIPPED` if already disabled
  - Build `subscription-manager repos --enable <repo-id>` / `--disable <repo-id>`
  - Honour `dry_run` flag on both mutating methods
  - Pass `elevated` flag to runner on both mutating methods
  - _Requirements: 7, 8_

- [ ] 11. Implement `set_release()`, `unset_release()`, `show_release()`, and
  `list_releases()`
  - For `show_release()`: invoke `release --show`, parse the release string, return
    `None` when none is pinned
  - For `list_releases()`: invoke `release --list`, parse output into `list[str]`
  - For `set_release(version)`: call `show_release()` first; return `SKIPPED` if already
    pinned to `version`; otherwise invoke `release --set <version>`
  - For `unset_release()`: call `show_release()` first; return `SKIPPED` if no release
    is currently pinned; otherwise invoke `release --unset`
  - Honour `dry_run` flag on both mutating methods
  - Pass `elevated` flag to runner on both mutating methods
  - _Requirements: 9_

- [ ] 12. Implement `refresh()`
  - Build `subscription-manager refresh` command
  - Honour `dry_run` flag
  - Pass `elevated` flag to runner; parse result into `SubscriptionActionResult`
  - _Requirements: 10_

- [ ] 13. Implement credential-redaction helper
  - Implement a private `_redact_credentials(args)` helper that returns a copy of the
    argument list with the token following `--password` and `--activationkey` replaced
    by a fixed masking token
  - Implement a private `_redact_text(text, secrets)` helper that replaces each secret
    string in `stdout`/`stderr` with the same masking token
  - Apply both helpers to `SubscriptionActionResult.command`, `stdout`, and `stderr`
    prior to returning the result, including in dry-run mode
  - Ensure the redacted command is also what the runner receives for auditing (build the
    argument list once, then pass the redacted variant to audit consumers through the
    `SubscriptionActionResult`)
  - _Requirements: 12_

- [ ] 14. Create test module and mock infrastructure
  - Create
    `source/testroots/testbase/automojo/tests/sysman/linux/redhat/test_subscriptionmanagement.py`
  - Implement `MockCommandRunner` that returns configurable `RunResult` values and
    records every call
  - Set up hypothesis testing framework configuration
  - Create test fixtures for common scenarios (registered/unregistered identity,
    enabled/disabled repo sets, pinned/unpinned release, attached/unattached pool state)
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12_

- [ ] 15. Write property-based test for idempotent registration
  - **Property 1: Idempotent Registration**
  - Generate random credentials/activation keys with a prior-registered identity
  - Verify `register()` returns `SKIPPED` without invoking the runner when already
    registered and `force=False`
  - **Validates: Requirements 1.4**
  - _Requirements: 1_

- [ ] 16. Write property-based test for idempotent unregistration
  - **Property 2: Idempotent Unregistration**
  - Generate random prior-unregistered states
  - Verify `unregister()` returns `SKIPPED` without invoking the runner
  - **Validates: Requirements 2.2**
  - _Requirements: 2_

- [ ] 17. Write property-based test for dry-run safety
  - **Property 3: Dry-Run Safety**
  - Generate random mutating operations with `dry_run=True`
  - Verify command runner is never invoked for write operations
  - Verify all results have `DRY_RUN` status
  - **Validates: Requirements 1.7, 2.5, 5.6, 6.6, 8.6, 9.6, 10.4**
  - _Requirements: 1, 2, 5, 6, 8, 9, 10_

- [ ] 18. Write property-based test for elevation consistency
  - **Property 4: Elevation Consistency**
  - Generate random operations with `elevated=True/False`
  - Verify mutating operations pass the elevated flag correctly to the runner
  - Verify read-only operations (`identity`, `status`, `list_repos`, `show_release`,
    `list_releases`) never use elevation
  - **Validates: Requirements 11.1, 11.2, 11.3**
  - _Requirements: 11_

- [ ] 19. Write property-based test for overall status mapping
  - **Property 5: Overall Status Mapping Correctness**
  - Generate random `subscription-manager status` outputs with varied overall values
  - Verify known values map to the correct enum member and unknown values map to
    `UNKNOWN`
  - **Validates: Requirements 4.2, 4.3**
  - _Requirements: 4_

- [ ] 20. Write property-based test for command construction correctness
  - **Property 6: Command Construction Correctness**
  - Generate random inputs across every operation type
  - Verify constructed commands match the expected format from Command Mappings
  - **Validates: Requirements 1.1, 1.2, 1.3, 2.1, 3.1, 4.1, 5.1, 5.2, 5.3, 6.1, 6.2, 7.1, 7.2, 7.3, 8.1, 8.2, 9.1, 9.2, 9.3, 9.4, 10.1**
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10_

- [ ] 21. Write property-based test for repository enable/disable idempotency
  - **Property 7: Repository Enable Idempotency**
  - Generate random repo lists with varied enabled/disabled state
  - Verify `enable_repo()` returns `SKIPPED` when already enabled
  - Verify `disable_repo()` returns `SKIPPED` when already disabled
  - **Validates: Requirements 8.3**
  - _Requirements: 8_

- [ ] 22. Write property-based test for release set/unset idempotency
  - **Property 8: Release Set/Unset Idempotency**
  - Generate random release strings with varied currently-pinned state
  - Verify `set_release()` returns `SKIPPED` when already pinned to the same version
  - Verify `unset_release()` returns `SKIPPED` when no release is currently pinned
  - **Validates: Requirements 9.1, 9.2**
  - _Requirements: 9_

- [ ] 23. Write property-based test for attach empty-pool handling
  - **Property 9: Attach Empty-Pool Handling**
  - Generate random pool-id lists (empty and non-empty) with both `auto=True` and
    `auto=False`
  - Verify `SemanticError` is raised when `auto=False` and `pool_ids` is empty
  - Verify exactly one `attach --auto` command is issued when `auto=True`
  - **Validates: Requirements 5.1, 5.2**
  - _Requirements: 5_

- [ ] 24. Write property-based test for remove empty-state handling
  - **Property 10: Remove Empty-State Handling**
  - Generate random attached-subscription states (empty and non-empty)
  - Verify `remove()` returns `SKIPPED` without invoking the runner when nothing is
    attached
  - **Validates: Requirements 6.3**
  - _Requirements: 6_

- [ ] 25. Write property-based test for query error propagation
  - **Property 11: Query Error Propagation**
  - Generate random non-zero exit codes from the mock runner for read-only queries
  - Verify `SubscriptionQueryError` is raised carrying the exit code and stderr
  - **Validates: Requirements 3.4, 4.4, 7.5**
  - _Requirements: 3, 4, 7_

- [ ] 26. Write property-based test for credential redaction
  - **Property 12: Credential Redaction**
  - Generate random credential strings and random command layouts including
    `--password` and `--activationkey`
  - Verify the recorded `command`, `stdout`, and `stderr` on the returned
    `SubscriptionActionResult` contain no raw credential values and retain the flag
    tokens
  - **Validates: Requirements 12.1, 12.2, 12.3, 12.4**
  - _Requirements: 12_

- [ ] 27. Write unit tests for `identity()` and `status()`
  - Test: system registered — returns populated `SubscriptionIdentity`
  - Test: system not registered — returns `None`
  - Test: unexpected exit code — raises `SubscriptionQueryError`
  - Test: `status()` maps `Current`, `Insufficient`, `Invalid`, `Disabled`, and unknown
    values correctly
  - Test: `status()` on command failure raises `SubscriptionQueryError`
  - _Requirements: 3, 4_

- [ ] 28. Write unit tests for `register()`
  - Test: not registered + credentials — runner called with correct args, returns
    `SUCCESS` with `system_uuid`
  - Test: not registered + activation key — runner called with `--org/--activationkey`,
    returns `SUCCESS`
  - Test: already registered without `force` — returns `SKIPPED`, runner not called
  - Test: already registered with `force=True` — runner called with `--force`
  - Test: registration failure — returns `FAILED` with exit code and stderr
  - Test: dry-run mode — returns `DRY_RUN`, runner not called
  - Test: elevated mode — runner called with `elevated=True`
  - Test: credential redaction — `--password` and `--activationkey` values masked in
    `command`, `stdout`, and `stderr`
  - _Requirements: 1, 11, 12_

- [ ] 29. Write unit tests for `unregister()`
  - Test: system registered — runner called, returns `SUCCESS`
  - Test: system not registered — returns `SKIPPED`, runner not called
  - Test: unregister failure — returns `FAILED`
  - Test: dry-run mode — returns `DRY_RUN`, runner not called
  - _Requirements: 2_

- [ ] 30. Write unit tests for `attach()` and `remove()`
  - Test: `attach(auto=True)` — runner called once with `attach --auto`, returns
    `SUCCESS`
  - Test: `attach` with pool ids — runner called once per pool
  - Test: `attach` with quantity — runner call includes `--quantity <n>`
  - Test: `attach(auto=False, pool_ids=[])` — raises `SemanticError`
  - Test: `attach` failure — returns `FAILED`
  - Test: `attach` dry-run — returns `DRY_RUN`, runner not called
  - Test: `remove(all=True)` — runner called with `--all`, returns `SUCCESS`
  - Test: `remove` with serials — runner called once per serial
  - Test: `remove` with no attached subscriptions — returns `SKIPPED`, runner not called
  - Test: `remove` dry-run — returns `DRY_RUN`, runner not called
  - _Requirements: 5, 6_

- [ ] 31. Write unit tests for `list_repos()`, `enable_repo()`, and `disable_repo()`
  - Test: `list_repos()` with no filter — runner called with `repos --list`, returns
    populated list
  - Test: `list_repos(filter="enabled")` — runner called with `repos --list-enabled`
  - Test: `list_repos(filter="disabled")` — runner called with `repos --list-disabled`
  - Test: `list_repos` on command failure — raises `SubscriptionQueryError`
  - Test: `enable_repo` on already-enabled repo — returns `SKIPPED`, runner not called
  - Test: `enable_repo` on disabled repo — runner called, returns `SUCCESS`
  - Test: `disable_repo` on already-disabled repo — returns `SKIPPED`, runner not called
  - Test: `disable_repo` on enabled repo — runner called, returns `SUCCESS`
  - Test: enable/disable failures — return `FAILED`
  - Test: enable/disable dry-run — returns `DRY_RUN`, runner not called
  - _Requirements: 7, 8_

- [ ] 32. Write unit tests for `set_release()`, `unset_release()`, `show_release()`, and
  `list_releases()`
  - Test: `show_release()` pinned — returns the release string
  - Test: `show_release()` not pinned — returns `None`
  - Test: `list_releases()` — returns parsed list of strings
  - Test: `set_release()` when already pinned to same version — returns `SKIPPED`,
    runner not called
  - Test: `set_release()` when unpinned or pinned to different version — runner called,
    returns `SUCCESS`
  - Test: `unset_release()` when no release pinned — returns `SKIPPED`, runner not
    called
  - Test: `unset_release()` when release pinned — runner called, returns `SUCCESS`
  - Test: set/unset dry-run — returns `DRY_RUN`, runner not called
  - _Requirements: 9_

- [ ] 33. Write unit tests for `refresh()`
  - Test: refresh success — runner called with `refresh`, returns `SUCCESS`
  - Test: refresh failure — returns `FAILED` with exit code and stderr
  - Test: refresh dry-run — returns `DRY_RUN`, runner not called
  - Test: refresh elevated — runner called with `elevated=True`
  - _Requirements: 10, 11_

- [ ] 34. Checkpoint - Ensure all tests pass
  - Run all unit tests and property-based tests
  - Verify all properties pass with 100+ iterations
  - Ensure all tests pass, ask the user if questions arise

