# Implementation Plan — Command Runner Architecture

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Package Layout

- [x] 1. Create runner/aspect/storage sub-packages with `__init__.py` re-exports
  - `runners/`, `aspects/`, `storage/` under
    `source/packages/automojo/sysman/linux/redhat/`
  - Each `__init__.py` re-exports the public API
  - _Requirements: All_

## Data Models and Protocols

- [x] 2. Define `runners/models.py`
  - `RunResult` dataclass: `exit_code`, `stdout`, `stderr`, `command`,
    `timed_out`, `dry_run`, `duration_ms`, `retry_count`, `transport`,
    plus a `success` property
  - `SSHReliabilityMetrics` dataclass: success/failure/timeout/connection-error
    counts, retry count, total/min/max/avg duration, last error + type
  - `SSHErrorType` enum: NETWORK / AUTH / HOST_KEY / TIMEOUT / CHANNEL /
    UNKNOWN
  - `ExecutionContext` dataclass for aspect hook I/O
  - _Requirements: 1, 3, 12, 14_

- [x] 3. Define `runners/base.py` with `ICommandRunner` Protocol
  - `@runtime_checkable` protocol
  - `run(...)` signature includes `stdin`
  - `add_aspect`, `remove_aspect`, `get_metrics` methods
  - _Requirements: 1, 4, 13_

- [x] 4. Define `aspects/base.py` with `IExecutionAspect` protocol and
  `ExecutionAspect` base class
  - `before_execute(context)`, `after_execute(context, result) -> RunResult`,
    `enabled` property
  - _Requirements: 4, 10_

- [x] 5. Define `storage/base.py` storage protocol and log-entry model
  - `CommandLogEntry` dataclass
  - `ICommandStorage` protocol covering append / query / delete
  - _Requirements: 5_

## LocalCommandRunner

- [x] 6. Implement `LocalRunnerConfig` dataclass
  - Fields: `dry_run: bool = False`, `sudo_path: str = "/usr/bin/sudo"`
  - _Requirements: 8, 11, 15_

- [x] 7. Implement `LocalCommandRunner` with aspect chain and protocol methods
  - `__init__`, config/aspects properties
  - `add_aspect` / `remove_aspect` (idempotent removal)
  - `get_metrics()` returns `None`
  - _Requirements: 1, 8, 10_

- [x] 8. Implement `LocalCommandRunner.run()`
  - Build `ExecutionContext` with `transport="local"`
  - Apply enabled aspects' `before_execute` in order
  - Short-circuit on `dry_run`: return synthetic `RunResult(dry_run=True,
    transport="local")` after running after-aspects
  - Prepend `[sudo_path, "-n"]` when `elevated=True`
  - Merge `env` with `os.environ` when provided
  - Measure duration via `time.monotonic()`
  - Invoke `subprocess.run(...)` with timeout and `text=True`
  - On `subprocess.TimeoutExpired`: return `RunResult(exit_code=-1,
    timed_out=True)` with partial output decoded to text
  - Populate `duration_ms`; apply after-aspects in reverse order
  - _Requirements: 1, 8, 11, 14, 15_

- [x] 9. Stream captured stdout/stderr through `on_output`/`on_error`
  callbacks after subprocess completion
  - Called once per stream with the full captured text when non-empty
  - _Requirements: 8_

## SSHCommandRunner

- [x] 10. Implement `SSHRunnerConfig` dataclass
  - Fields: `agent`, `host`, `username`, `port=22`, `password`,
    `key_filename`, `credential`, `credential_identifier="sshrunner"`,
    `aspects`, `dry_run=False`, `sudo_path="/usr/bin/sudo"`,
    `default_timeout=60.0`
  - _Requirements: 2, 9, 11, 15_

- [x] 11. Lazy-import SSH dependencies via `_import_ssh_deps()`
  - Imports `SshAgent`, `AspectsCmd`, `SshCredential`, `paramiko`
  - Raises `ImportError` with install hint when missing
  - _Requirements: 2.6_

- [x] 12. Implement `SSHCommandRunner.__init__`
  - Store config and empty aspect list
  - Initialise `SSHReliabilityMetrics()`
  - Call `_ensure_agent()` when not in dry-run mode
  - _Requirements: 2.1, 2.7, 11.4_

- [x] 13. Implement `_ensure_agent()` construction logic
  - Use pre-built `config.agent` when provided
  - Else require `host`; raise `ValueError` if missing
  - Else build `SshCredential` from username/password/key_filename;
    raise `ValueError` if username missing without credential
  - Build `SshAgent(host, primary_credential, port)`
  - _Requirements: 2.2, 2.3, 2.4, 2.5_

- [x] 14. Implement `SSHCommandRunner.run()`
  - Build `ExecutionContext` with `transport="ssh"`
  - Apply before-aspects
  - Dry-run short-circuits with `RunResult(dry_run=True, transport="ssh")`
  - Build shell command via `_build_shell_command` (env exports, cwd,
    elevation, optional stdin base64 pipeline)
  - Build `AspectsCmd` via `_aspects_for_run` with permissive expected
    statuses and caller/default timeout
  - Call `agent.run_cmd(command_line, aspects)` and map outcome to
    `RunResult`
  - On `socket.timeout` / `TimeoutError`: `exit_code=-1`, `timed_out=True`
  - On other exceptions: `exit_code=-1`, `stderr=str(ex)`
  - Measure duration; update metrics via `_update_metrics`
  - Apply after-aspects in reverse order
  - _Requirements: 1, 9, 11, 13, 14, 15_

- [x] 15. Implement `_build_shell_command` helper
  - `export KEY=shlex.quote(value)` for each env entry
  - `cd shlex.quote(cwd)` when `cwd` set
  - Prepend `[sudo_path, "-n"]` on elevation
  - shlex-quote all command args
  - When `stdin_payload` non-None: wrap with
    `printf '%s' <b64> | base64 -d | <cmd>`
  - Join parts with `&&`
  - _Requirements: 9.3, 9.4, 9.5, 13.1, 13.2, 15_

- [x] 16. Implement `_aspects_for_run` builder
  - Build `AspectsCmd` with permissive expected statuses (0–255) so every
    exit code returns rather than raising
  - Honour `SSHRunnerConfig.aspects` by copying relevant fields and
    overriding `completion_timeout` from the caller
  - _Requirements: 7_

- [x] 17. Implement `_classify_error` / `_update_metrics`
  - Classify paramiko exceptions (auth, host key, channel, generic SSH)
  - Classify `socket.timeout`, `TimeoutError`, `socket.error`
  - String-based fallback when paramiko is unavailable
  - Update success/failure/timeout counts, duration stats, last error
  - _Requirements: 3, 12_

- [x] 18. Expose `reset_metrics()` to clear the `SSHReliabilityMetrics`
  - _Requirements: 3.5_

## Aspects Layer

- [x] 19. Implement `ExecutionAspect` base + `IExecutionAspect` protocol
  - `enabled` flag, `before_execute` default no-op, `after_execute` returns
    result unchanged
  - _Requirements: 4, 10_

- [x] 20. Implement `PersistenceAspect` writing to an `ICommandStorage`
  backend
  - `before_execute` stores a pending entry (args/cwd/env/timestamp)
  - `after_execute` completes the entry with outcome fields
  - Storage failures must not abort command execution
  - _Requirements: 5_

- [x] 21. Implement `RetryAspect` with configurable retry policy
  - Retryable exit codes + error-pattern matching
  - Exponential backoff between attempts
  - Stops at `max_retries`; records `retry_count`
  - _Requirements: 6_

- [x] 22. Implement `TimeoutAspect` applying a default timeout
  - Applies `config.default_timeout` when `context.timeout is None`
  - Leaves caller-supplied timeout untouched
  - _Requirements: 7_

## Storage Layer

- [x] 23. Implement `JsonFileStorage` backend
  - Append-only JSON Lines layout
  - Log entry serialisation and tail query
  - _Requirements: 5_

- [x] 24. Implement `SQLiteStorage` backend
  - SQLite schema, connection management, append + query, retention prune
  - _Requirements: 5_

## Unit Tests — Implemented

- [x] 25. `runners/test_local.py` — core paths
  - Covers dry-run short-circuit, aspect before/after ordering, elevation
    (sudo -n prefix), env merging, timeout handling, duration measurement,
    metrics=None
- [x] 26. `runners/test_ssh.py` — core paths
  - Covers dry-run short-circuit (no agent import), aspect invocation in
    dry-run, shell-command building (env, cwd, elevation, stdin), error
    classification into metrics, timeout handling
- [x] 27. `aspects/test_persistence.py` — happy path + storage-failure
  degradation
- [x] 28. `aspects/test_retry.py` — retryable/non-retryable, max retries,
  backoff (mocked sleep), success after retry
- [x] 29. `aspects/test_timeout.py` — default timeout injection, honours
  caller timeout
- [x] 30. `storage/test_storage.py` — JSON and SQLite backends round-trip

## Unit Tests — Gaps

- [ ] 31. Additional `runners/test_ssh.py` cases for each
  `SSHErrorType` classification branch
  - Inject each paramiko exception type through a stub agent and assert
    `SSHReliabilityMetrics.last_error_type` — currently only a subset
    of branches is covered
  - _Requirements: 12.1–12.4, 12.6, 12.7, 12.8_

- [ ] 32. Add test confirming SSH runner skips `_ensure_agent` entirely
  when `dry_run=True` (no import of `automojo.interop` is attempted)
  - _Requirements: 2.7, 11.4_

- [ ] 33. Add test for `reset_metrics()` clearing all counters
  - _Requirements: 3.5_

## Documentation

- [ ] 34. Cross-link this spec from the aspect-consumer specs (retry,
  timeout, persistence) and from `1-command-execution/design.md` once the
  spec tree settles
  - Mechanical follow-up; the architecture doc is already referenced from
    `1-command-execution` but reverse links would help discovery
