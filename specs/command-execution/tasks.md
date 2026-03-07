# Implementation Plan

- [ ] 1. Create the `commandexecution` module file
  - Create `source/packages/automojo/sysman/linux/redhat/commandexecution.py`
  - Add module-level docstring describing purpose, the coordinator-driven design, and the
    three execution modes: single command, sequence, and shell script
  - _Requirements: 1, 2, 3_

- [ ] 2. Define `CommandSpec` dataclass
  - Fields: `args`, `elevated`, `working_dir`, `env`, `timeout_seconds`, `on_output`,
    `on_error`, `label`
  - All fields except `args` are optional with appropriate defaults
  - _Requirements: 2, 4, 5, 6, 7, 8_

- [ ] 3. Define `CommandResult` dataclass
  - Fields: `success`, `exit_code`, `stdout`, `stderr`, `args`, `timed_out`, `dry_run`
  - _Requirements: 1, 3, 6_

- [ ] 4. Define `SequenceResult` dataclass
  - Fields: `all_succeeded`, `results`, `failed_at`, `dry_run`
  - _Requirements: 2_

- [ ] 5. Implement `CommandExecutorConfig` dataclass
  - Field: `dry_run: bool = False`
  - _Requirements: 1.5, 2.6, 3.6_

- [ ] 6. Implement `CommandExecutor.__init__`
  - Accept `ICommandRunner` and `CommandExecutorConfig`
  - Store runner and config as private attributes
  - _Requirements: 1_

- [ ] 7. Implement `run()` method
  - Return dry-run result immediately when `config.dry_run=True`
  - Validate `working_dir` exists when provided; return failure result if not
  - Merge `env` with `os.environ` when provided; pass `None` otherwise
  - Delegate to runner with all forwarded kwargs
  - Map `RunResult` to `CommandResult`; handle `timed_out` field
  - _Requirements: 1, 4, 5, 6, 7, 8_

- [ ] 8. Implement `run_sequence()` method
  - Return empty-success result immediately for empty `commands` list
  - Return dry-run sequence result immediately when `config.dry_run=True`
  - Iterate specs, calling `run()` for each
  - Stop and return on first failure when `stop_on_failure=True` (default)
  - Continue through all specs when `stop_on_failure=False`
  - Set `failed_at` to index of first failed result; `None` when all succeed
  - _Requirements: 2_

- [ ] 9. Implement `run_script()` method
  - Return dry-run result immediately when `config.dry_run=True`; no file created
  - Write script string to `tempfile.NamedTemporaryFile(suffix=".sh", delete=False)`
  - `chmod` temp file to `0o755` via `os.chmod`
  - Call `run(["bash", tmpfile_path], ...)` forwarding all kwargs
  - Delete temp file in `finally` block using `Path.unlink(missing_ok=True)`
  - Return `CommandResult` from `run()`
  - _Requirements: 3, 4, 5, 6, 7, 8_

- [ ] 10. Create test module and mock infrastructure
  - Create `source/testroots/testbase/automojo/tests/sysman/linux/redhat/test_commandexecution.py`
  - Implement `MockCommandRunner` with configurable return value and call recorder
  - Helper `make_run_result(exit_code, stdout, stderr, timed_out)` for test fixtures
  - Set up hypothesis testing framework configuration
  - Create test fixtures for common test scenarios
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8_

- [ ] 11. Write property-based test for success mapping consistency
  - **Property 1: Success Mapping Consistency**
  - Generate random exit codes
  - Verify exit code 0 maps to success=True, non-zero to success=False
  - **Validates: Requirements 1.2, 1.3**
  - _Requirements: 1_

- [ ] 12. Write property-based test for dry-run safety
  - **Property 2: Dry-Run Safety**
  - Generate random commands, sequences, and scripts with dry_run=True
  - Verify runner never invoked and results have dry_run=True
  - **Validates: Requirements 1.5, 2.6, 3.6**
  - _Requirements: 1, 2, 3_

- [ ] 13. Write property-based test for working directory validation
  - **Property 3: Working Directory Validation**
  - Generate valid and invalid directory paths
  - Verify invalid paths return failure without runner invocation
  - **Validates: Requirements 4.3**
  - _Requirements: 4_

- [ ] 14. Write property-based test for environment merging correctness
  - **Property 4: Environment Merging Correctness**
  - Generate random environment dictionaries
  - Verify merged env has correct precedence
  - **Validates: Requirements 5.1, 5.3**
  - _Requirements: 5_

- [ ] 15. Write property-based test for sequence stop-on-failure behavior
  - **Property 5: Sequence Stop-on-Failure Behavior**
  - Generate random command sequences with failures
  - Verify execution stops at first failure
  - **Validates: Requirements 2.3**
  - _Requirements: 2_

- [ ] 16. Write property-based test for sequence continue-on-failure behavior
  - **Property 6: Sequence Continue-on-Failure Behavior**
  - Generate random command sequences with stop_on_failure=False
  - Verify all commands execute regardless of failures
  - **Validates: Requirements 2.4**
  - _Requirements: 2_

- [ ] 17. Write property-based test for empty sequence handling
  - **Property 7: Empty Sequence Handling**
  - Verify empty list returns success without runner invocation
  - **Validates: Requirements 2.5**
  - _Requirements: 2_

- [ ] 18. Write property-based test for script temp file cleanup
  - **Property 8: Script Temp File Cleanup**
  - Generate random scripts
  - Verify temp file deleted after execution (success or failure)
  - **Validates: Requirements 3.4**
  - _Requirements: 3_

- [ ] 19. Write property-based test for timeout propagation
  - **Property 9: Timeout Propagation**
  - Generate random timeout values
  - Verify timeout passed to runner and timed_out flag set correctly
  - **Validates: Requirements 6.1, 6.2**
  - _Requirements: 6_

- [ ] 20. Write property-based test for elevation propagation
  - **Property 10: Elevation Propagation**
  - Generate random commands with elevated=True/False
  - Verify elevated flag passed correctly to runner
  - **Validates: Requirements 8.1, 8.2**
  - _Requirements: 8_

- [ ] 21. Write property-based test for output capture completeness
  - **Property 11: Output Capture Completeness**
  - Generate random commands with/without callbacks
  - Verify complete stdout/stderr in result regardless of callbacks
  - **Validates: Requirements 7.3**
  - _Requirements: 7_

- [ ] 22. Write unit tests for `run()` — happy path and failure
  - Test success: exit 0 — `success=True`, stdout/stderr captured
  - Test failure: non-zero exit — `success=False`, exit code preserved
  - Test elevated: runner called with `elevated=True`
  - Test not elevated: runner called with `elevated=False`
  - _Requirements: 1, 8_

- [ ] 12. Write tests for `run()` — working_dir
  - Test valid `working_dir`: runner called with `cwd=working_dir`
  - Test missing `working_dir`: returns failure result, runner NOT called
  - Test no `working_dir`: runner called with `cwd=None`
  - _Requirements: 4_

- [ ] 13. Write tests for `run()` — env merging
  - Test `env` provided: runner called with merged env (caller key overrides os.environ)
  - Test no `env`: runner called with `env=None`
  - _Requirements: 5_

- [ ] 14. Write tests for `run()` — timeout
  - Test `timeout_seconds` provided: runner called with `timeout=timeout_seconds`
  - Test timed-out runner response: `CommandResult.timed_out=True`, `success=False`
  - Test no `timeout_seconds`: runner called with `timeout=None`
  - _Requirements: 6_

- [ ] 15. Write tests for `run()` — streaming callbacks
  - Test `on_output` provided: runner called with `on_output` callable
  - Test `on_error` provided: runner called with `on_error` callable
  - Test no callbacks: runner called with `on_output=None`, `on_error=None`
  - _Requirements: 7_

- [ ] 16. Write tests for `run()` — dry-run
  - Test dry-run: runner NOT called, `CommandResult.dry_run=True`, `success=True`,
    exit code 0, empty stdout/stderr
  - _Requirements: 1.5_

- [ ] 17. Write tests for `run_sequence()` — stop on failure
  - Test all succeed: `all_succeeded=True`, all results populated, `failed_at=None`
  - Test first command fails, `stop_on_failure=True` (default): only one runner call,
    `all_succeeded=False`, `failed_at=0`
  - Test second command fails, `stop_on_failure=True`: two runner calls, `failed_at=1`
  - _Requirements: 2.2, 2.3_

- [ ] 18. Write tests for `run_sequence()` — continue on failure
  - Test second command fails, `stop_on_failure=False`: all three runner calls made,
    `all_succeeded=False`, `failed_at=1`
  - Test all fail, `stop_on_failure=False`: all runner calls made, `all_succeeded=False`
  - _Requirements: 2.4_

- [ ] 19. Write tests for `run_sequence()` — edge cases
  - Test empty list: returns `all_succeeded=True`, empty results, no runner calls
  - Test dry-run: no runner calls, one dry-run result per spec, `all_succeeded=True`
  - _Requirements: 2.5, 2.6_

- [ ] 20. Write tests for `run_script()`
  - Test success: runner called with `["bash", <path ending in .sh>]`, `success=True`
  - Test failure: non-zero script exit — `success=False`, exit code preserved
  - Test temp file deleted on success: path no longer exists after call
  - Test temp file deleted on failure: path no longer exists after call
  - Test elevated: runner called with `elevated=True`
  - _Requirements: 3_

- [ ] 21. Write tests for `run_script()` — dry-run
  - Test dry-run: no file written, runner NOT called, `CommandResult.dry_run=True`,
    `success=True`, empty stdout/stderr
  - _Requirements: 3.6_

- [ ] 23. Write unit tests for `run()` — working_dir
  - Test valid `working_dir`: runner called with `cwd=working_dir`
  - Test missing `working_dir`: returns failure result, runner NOT called
  - Test no `working_dir`: runner called with `cwd=None`
  - _Requirements: 4_

- [ ] 24. Write unit tests for `run()` — env merging
  - Test `env` provided: runner called with merged env (caller key overrides os.environ)
  - Test no `env`: runner called with `env=None`
  - _Requirements: 5_

- [ ] 25. Write unit tests for `run()` — timeout
  - Test `timeout_seconds` provided: runner called with `timeout=timeout_seconds`
  - Test timed-out runner response: `CommandResult.timed_out=True`, `success=False`
  - Test no `timeout_seconds`: runner called with `timeout=None`
  - _Requirements: 6_

- [ ] 26. Write unit tests for `run()` — streaming callbacks
  - Test `on_output` provided: runner called with `on_output` callable
  - Test `on_error` provided: runner called with `on_error` callable
  - Test no callbacks: runner called with `on_output=None`, `on_error=None`
  - _Requirements: 7_

- [ ] 27. Write unit tests for `run()` — dry-run
  - Test dry-run: runner NOT called, `CommandResult.dry_run=True`, `success=True`,
    exit code 0, empty stdout/stderr
  - _Requirements: 1.5_

- [ ] 28. Write unit tests for `run_sequence()` — stop on failure
  - Test all succeed: `all_succeeded=True`, all results populated, `failed_at=None`
  - Test first command fails, `stop_on_failure=True` (default): only one runner call,
    `all_succeeded=False`, `failed_at=0`
  - Test second command fails, `stop_on_failure=True`: two runner calls, `failed_at=1`
  - _Requirements: 2.2, 2.3_

- [ ] 29. Write unit tests for `run_sequence()` — continue on failure
  - Test second command fails, `stop_on_failure=False`: all three runner calls made,
    `all_succeeded=False`, `failed_at=1`
  - Test all fail, `stop_on_failure=False`: all runner calls made, `all_succeeded=False`
  - _Requirements: 2.4_

- [ ] 30. Write unit tests for `run_sequence()` — edge cases
  - Test empty list: returns `all_succeeded=True`, empty results, no runner calls
  - Test dry-run: no runner calls, one dry-run result per spec, `all_succeeded=True`
  - _Requirements: 2.5, 2.6_

- [ ] 31. Write unit tests for `run_script()`
  - Test success: runner called with `["bash", <path ending in .sh>]`, `success=True`
  - Test failure: non-zero script exit — `success=False`, exit code preserved
  - Test temp file deleted on success: path no longer exists after call
  - Test temp file deleted on failure: path no longer exists after call
  - Test elevated: runner called with `elevated=True`
  - _Requirements: 3_

- [ ] 32. Write unit tests for `run_script()` — dry-run
  - Test dry-run: no file written, runner NOT called, `CommandResult.dry_run=True`,
    `success=True`, empty stdout/stderr
  - _Requirements: 3.6_

- [ ] 33. Checkpoint - Ensure all tests pass
  - Run all unit tests and property-based tests
  - Verify all properties pass with 100+ iterations
  - Ensure all tests pass, ask the user if questions arise
