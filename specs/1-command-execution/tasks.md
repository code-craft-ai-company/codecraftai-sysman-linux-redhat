# Implementation Plan

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `commandexecution` module
  - `source/packages/automojo/sysman/linux/redhat/commandexecution.py` exists
  - Module-level comment describes purpose
  - _Requirements: 1, 2, 3_

- [x] 2. Define `CommandExecutorConfig` dataclass
  - Field: `dry_run: bool = False`
  - _Requirements: 1.5, 2.6, 3.6_

- [x] 3. Define `CommandSpec` dataclass
  - Fields: `args`, `elevated`, `working_dir`, `env`, `timeout_seconds`,
    `on_output`, `on_error`, `label`
  - All fields except `args` have appropriate defaults
  - _Requirements: 2, 4, 5, 6, 7, 8_

- [x] 4. Define `CommandResult` dataclass
  - Fields: `success`, `exit_code`, `stdout`, `stderr`, `args`, `timed_out`,
    `dry_run`
  - _Requirements: 1, 3, 6_

- [x] 5. Define `SequenceResult` dataclass
  - Fields: `all_succeeded`, `results`, `failed_at`, `dry_run`
  - _Requirements: 2_

## CommandExecutor Methods

- [x] 6. Implement `CommandExecutor.__init__`
  - Accepts `ICommandRunner` and optional `CommandExecutorConfig`
  - Defaults config to `CommandExecutorConfig()` when `None`
  - Stores runner and config as private attributes with read-only properties
  - _Requirements: 1_

- [x] 7. Implement `run()` method
  - Returns dry-run result immediately when `config.dry_run=True`
  - Validates `working_dir` exists when provided; returns failure without
    invoking the runner if it does not
  - Merges `env` with `os.environ` (caller-wins) when provided, else `None`
  - Delegates to runner with all forwarded kwargs
  - Maps `RunResult` to `CommandResult`, injecting the default timeout
    message when the runner supplied empty `stderr` alongside `timed_out=True`
  - _Requirements: 1, 4, 5, 6, 7, 8_

- [x] 8. Implement `run_sequence()` method
  - Returns empty-success result immediately for empty `commands`
  - Returns dry-run sequence result immediately when `config.dry_run=True`
  - Iterates specs, delegating each to `run()`
  - Stops and returns on first failure when `stop_on_failure=True`
  - Continues through all specs when `stop_on_failure=False`
  - `failed_at` is the index of the first failed result; `None` when all succeed
  - _Requirements: 2_

- [x] 9. Implement `run_script()` method
  - Returns dry-run result immediately when `config.dry_run=True`; no file
    created
  - Writes script to `tempfile.NamedTemporaryFile(prefix="automojo_script_",
    suffix=".sh", delete=False)`
  - `chmod` temp file to `0o755`
  - Invokes `run(["bash", tmpfile_path], ...)` forwarding all kwargs
  - Deletes temp file in a `finally` block via `Path.unlink(missing_ok=True)`
  - Returns the `CommandResult` from the inner `run()` call
  - _Requirements: 3, 4, 5, 6, 7, 8_

## Unit Tests — Implemented

- [x] 10. Create test module with mock runner
  - `test_commandexecution.py` exists
  - Local `_MockRunner` dataclass with `queued_results` and call recorder
  - Implements `add_aspect`, `remove_aspect`, `get_metrics` no-ops to satisfy
    the runner protocol
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8_

- [x] 11. Test `run()` — dry-run short-circuits the runner
  - _Requirements: 1.5_ (`test_dry_run_does_not_invoke_runner`)

- [x] 12. Test `run()` — successful RunResult maps to success with elevation
  - _Requirements: 1.1, 1.2, 8.1_ (`test_successful_run_maps_result`)

- [x] 13. Test `run()` — failing RunResult maps to failure
  - _Requirements: 1.3_ (`test_failure_run_maps_result`)

- [x] 14. Test `run()` — timeout RunResult with empty stderr receives default
  message
  - _Requirements: 6.2_ (`test_timeout_populates_default_message`)

- [x] 15. Test `run()` — missing `working_dir` short-circuits before runner
  - _Requirements: 4.3_ (`test_working_dir_missing_short_circuits`)

- [x] 16. Test `run()` — caller `env` merged with `os.environ`
  - _Requirements: 5.1, 5.3_ (`test_env_merged_with_os_environ`)

- [x] 17. Test `run_sequence()` — empty list returns success with no runner
  invocations
  - _Requirements: 2.5_ (`test_empty_sequence_returns_success`)

- [x] 18. Test `run_sequence()` — dry-run emits synthetic results for every
  spec
  - _Requirements: 2.6_ (`test_dry_run_sequence_emits_synthetic_results`)

- [x] 19. Test `run_sequence()` — `stop_on_failure=True` halts at first
  failure and records `failed_at`
  - _Requirements: 2.3_ (`test_stop_on_failure_halts_sequence`)

- [x] 20. Test `run_sequence()` — `stop_on_failure=False` runs all and
  records `failed_at`
  - _Requirements: 2.4_ (`test_continue_on_failure_runs_all`)

- [x] 21. Test `run_script()` — dry-run writes no temp file and does not
  invoke runner
  - _Requirements: 3.6_ (`test_dry_run_script_no_tempfile`)

- [x] 22. Test `run_script()` — bash invocation on success with temp-file
  cleanup
  - _Requirements: 3.1, 3.2, 3.4_ (`test_script_executed_via_bash_and_cleaned_up`)

- [x] 23. Test `run_script()` — temp-file cleanup on script failure
  - _Requirements: 3.3, 3.4_ (`test_script_tempfile_cleanup_on_failure`)

## Unit Tests — Gaps

- [ ] 24. Test `run()` — `timeout_seconds` propagated to runner `timeout`
  kwarg
  - Verify runner receives the caller-supplied timeout value; verify `None`
    is passed when no timeout specified
  - _Requirements: 6.1, 6.4_

- [ ] 25. Test `run()` — valid `working_dir` reaches runner as `cwd`
  - Verify runner's `cwd` kwarg equals the caller-supplied path when valid;
    verify `cwd=None` when `working_dir` omitted
  - _Requirements: 4.1, 4.2_

- [ ] 26. Test `run()` — `on_output` and `on_error` callbacks propagated to
  the runner
  - Verify runner receives the callables; verify `None` is passed when
    callbacks are omitted
  - _Requirements: 7.1, 7.2, 7.4_

- [ ] 27. Test `run()` — no `env` passes `env=None` to the runner
  - Verify runner receives `env=None` when caller omits the parameter
  - _Requirements: 5.2_

- [ ] 28. Test `run_script()` — `elevated=True` propagated through to runner
  - _Requirements: 3.5, 8.1_

- [ ] 29. Test `run_sequence()` — per-command `elevated` honoured across
  mixed-elevation specs
  - Build a sequence with some specs `elevated=True` and others `False`;
    verify the runner call for each spec used the per-spec elevation flag
  - _Requirements: 8.3_

- [ ] 30. Test `run()` — preserves runner-supplied `stderr` when runner
  already populates it on timeout
  - _Requirements: 6.3_
