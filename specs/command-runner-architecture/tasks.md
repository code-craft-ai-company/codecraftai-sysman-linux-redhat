# Implementation Plan - Command Runner Architecture

- [ ] 1. Create base module structure
  - Create `source/packages/automojo/sysman/linux/redhat/runners/` directory
  - Create `source/packages/automojo/sysman/linux/redhat/aspects/` directory
  - Create `source/packages/automojo/sysman/linux/redhat/storage/` directory
  - Add `__init__.py` files for each directory
  - _Requirements: All_

- [ ] 2. Define core data models
  - Create `runners/models.py`
  - Implement `RunResult` dataclass with all fields
  - Implement `SSHReliabilityMetrics` dataclass
  - Implement `SSHErrorType` enum
  - Implement `ExecutionContext` dataclass
  - _Requirements: 1, 3, 12_

- [ ] 3. Define aspect protocol
  - Create `aspects/base.py`
  - Define `IExecutionAspect` protocol with `before_execute`, `after_execute`, `enabled`
  - _Requirements: 4_

- [ ] 4. Define storage protocol
  - Create `storage/base.py`
  - Define `ICommandStorage` protocol
  - Define `CommandLogEntry` dataclass
  - _Requirements: 5_

- [ ] 5. Implement LocalCommandRunner
  - Create `runners/local.py`
  - Implement `LocalRunnerConfig` dataclass
  - Implement `LocalCommandRunner.__init__` accepting config
  - Implement aspect registration: `add_aspect`, `remove_aspect`
  - _Requirements: 1, 8_

- [ ] 6. Implement LocalCommandRunner.run() - core logic
  - Implement dry-run check returning synthetic result
  - Implement aspect before_execute phase
  - Implement elevation logic (prepend sudo)
  - Implement environment merging
  - Implement subprocess invocation with timeout handling
  - Implement result mapping from CompletedProcess to RunResult
  - Implement aspect after_execute phase
  - _Requirements: 1, 8, 11_

- [ ] 7. Implement SSHCommandRunner - connection management
  - Create `runners/ssh.py`
  - Implement `SSHRunnerConfig` dataclass with all connection parameters
  - Implement `SSHCommandRunner.__init__` accepting config
  - Implement `connect()` with retry logic and exponential backoff
  - Implement `disconnect()` to close SSH connection
  - Implement `_start_keepalive()` for connection keepalive
  - Implement `_check_connection()` to verify connection health
  - _Requirements: 2, 9_

- [ ] 8. Implement SSHCommandRunner.run() - command execution
  - Implement dry-run check returning synthetic result
  - Implement aspect before_execute phase
  - Implement connection health check and reconnect logic
  - Implement `_build_command()` to construct SSH command with env, cwd, elevation
  - Implement SSH channel execution with output capture
  - Implement timeout handling for SSH channels
  - Implement result mapping to RunResult with transport="ssh"
  - Implement aspect after_execute phase
  - _Requirements: 9, 11_

- [ ] 9. Implement SSH reliability metrics tracking
  - Implement `_update_metrics()` to track success/failure/timeout counts
  - Implement duration tracking (min, max, avg)
  - Implement `_classify_error()` to map exceptions to SSHErrorType
  - Implement `get_metrics()` to return SSHReliabilityMetrics
  - Implement `reset_metrics()` to clear all counters
  - _Requirements: 3, 12_

- [ ] 10. Implement SSH error handling
  - Create `SSHConnectionError` exception class
  - Implement error classification logic for auth, network, host key, timeout errors
  - Implement connection retry with error tracking
  - Update metrics on each error type
  - _Requirements: 2, 12_

- [ ] 11. Implement PersistenceAspect
  - Create `aspects/persistence.py`
  - Implement `PersistenceAspectConfig` dataclass
  - Implement `PersistenceAspect.__init__` accepting config and storage backend
  - Implement `before_execute()` to create log entry with timestamp and command details
  - Implement `after_execute()` to update log entry with result and duration
  - Implement error handling for storage backend failures
  - _Requirements: 5_

- [ ] 12. Implement RetryAspect
  - Create `aspects/retry.py`
  - Implement `RetryAspectConfig` dataclass with retry policy parameters
  - Implement `RetryAspect.__init__` accepting config
  - Implement retry wrapper logic that re-executes on retryable failures
  - Implement retryable error detection (exit codes and stderr patterns)
  - Implement exponential backoff between retries
  - Update `RunResult.retry_count` on successful retry
  - _Requirements: 6_

- [ ] 13. Implement TimeoutAspect
  - Create `aspects/timeout.py`
  - Implement `TimeoutAspectConfig` dataclass
  - Implement `TimeoutAspect.__init__` accepting config
  - Implement `before_execute()` to set default timeout if not specified
  - Implement timeout override logic based on `allow_override` config
  - _Requirements: 7_

- [ ] 14. Implement SQLite storage backend
  - Create `storage/sqlite.py`
  - Implement `SQLiteStorage` class implementing `ICommandStorage`
  - Implement database schema for command log entries
  - Implement `log_command()` to insert new entry
  - Implement `update_command()` to update entry with result
  - Implement connection pooling and error handling
  - _Requirements: 5_

- [ ] 15. Implement JSON file storage backend
  - Create `storage/json.py`
  - Implement `JSONStorage` class implementing `ICommandStorage`
  - Implement `log_command()` to append to JSON file
  - Implement `update_command()` to update entry in JSON file
  - Implement file locking for concurrent access
  - _Requirements: 5_

- [ ] 16. Create test module structure
  - Create `source/testroots/testbase/automojo/tests/sysman/linux/redhat/runners/` directory
  - Create `source/testroots/testbase/automojo/tests/sysman/linux/redhat/aspects/` directory
  - Create `source/testroots/testbase/automojo/tests/sysman/linux/redhat/storage/` directory
  - Add `__init__.py` files for each test directory
  - _Requirements: All_

- [ ] 17. Write unit tests for LocalCommandRunner
  - Test: command execution with mocked subprocess
  - Test: elevation prepends sudo correctly
  - Test: environment merging with os.environ
  - Test: working directory passed to subprocess
  - Test: timeout handling with TimeoutExpired exception
  - Test: dry-run mode returns synthetic result without subprocess call
  - Test: aspect before_execute and after_execute invocation order
  - _Requirements: 1, 8, 11_

- [ ] 18. Write unit tests for SSHCommandRunner
  - Test: connection establishment with mocked paramiko
  - Test: connection retry logic with simulated failures
  - Test: command building with env, cwd, elevation
  - Test: SSH channel execution with mocked output
  - Test: timeout handling for SSH channels
  - Test: dry-run mode returns synthetic result without connection
  - Test: keepalive packet sending
  - Test: connection health check and reconnect
  - _Requirements: 2, 9, 11_

- [ ] 19. Write unit tests for SSH reliability metrics
  - Test: success counter increments on successful execution
  - Test: failure counter increments on failed execution
  - Test: timeout counter increments on timeout
  - Test: connection error counter increments on connection failure
  - Test: duration tracking (min, max, avg) calculations
  - Test: error classification for different exception types
  - Test: metrics reset clears all counters
  - _Requirements: 3, 12_

- [ ] 20. Write unit tests for PersistenceAspect
  - Test: log entry created in before_execute
  - Test: log entry updated in after_execute with result
  - Test: storage backend failure does not abort execution (when fail_on_storage_error=False)
  - Test: storage backend failure aborts execution (when fail_on_storage_error=True)
  - Test: environment variables logged when log_environment=True
  - Test: output logged when log_output=True
  - _Requirements: 5_

- [ ] 21. Write unit tests for RetryAspect
  - Test: command retried on retryable exit code
  - Test: command retried on retryable stderr pattern
  - Test: command not retried on non-retryable error
  - Test: exponential backoff between retries
  - Test: max retries limit enforced
  - Test: retry_count updated in result after successful retry
  - Test: last failure returned when all retries exhausted
  - _Requirements: 6_

- [ ] 22. Write unit tests for TimeoutAspect
  - Test: default timeout set when command has no timeout
  - Test: command timeout preserved when allow_override=True
  - Test: command timeout overridden when allow_override=False
  - _Requirements: 7_

- [ ] 23. Write unit tests for aspect composition
  - Test: multiple aspects applied in registration order
  - Test: aspect before_execute called in order
  - Test: aspect after_execute called in reverse order
  - Test: aspect exception in before_execute aborts execution
  - Test: disabled aspect skipped during execution
  - Test: context modifications propagate through aspect chain
  - _Requirements: 4, 10_

- [ ] 24. Write unit tests for storage backends
  - Test SQLite: log entry insertion and retrieval
  - Test SQLite: log entry update with result
  - Test SQLite: concurrent access handling
  - Test JSON: log entry appended to file
  - Test JSON: log entry updated in file
  - Test JSON: file locking for concurrent writes
  - _Requirements: 5_

- [ ]* 25. Write property test for transport equivalence
  - **Property: Transport Equivalence**
  - For any command, local and SSH execution should produce equivalent exit codes and output (modulo timing)
  - **Validates: Requirements 1, 9**

- [ ]* 26. Write property test for aspect idempotence
  - **Property: Aspect Idempotence**
  - Applying the same aspect configuration twice should not change execution behavior
  - **Validates: Requirements 4, 10**

- [ ]* 27. Write property test for retry convergence
  - **Property: Retry Convergence**
  - For any retryable command, retry aspect should eventually succeed or exhaust max retries
  - **Validates: Requirements 6**

- [ ]* 28. Write property test for metrics accuracy
  - **Property: Metrics Accuracy**
  - SSH metrics counters (success + failure + timeout) should equal total executions
  - **Validates: Requirements 3**

- [ ]* 29. Write property test for connection resilience
  - **Property: Connection Resilience**
  - SSH runner should successfully execute commands after simulated connection failures within retry limit
  - **Validates: Requirements 2**

- [ ]* 30. Write property test for elevation consistency
  - **Property: Elevation Consistency**
  - For any command with elevated=True, both local and SSH runners should prepend sudo
  - **Validates: Requirements 1, 8, 9**

- [ ]* 31. Write property test for dry-run safety
  - **Property: Dry-Run Safety**
  - For any command with dry_run=True, no subprocess or SSH connection should be created
  - **Validates: Requirements 11**

- [ ]* 32. Write property test for timeout enforcement
  - **Property: Timeout Enforcement**
  - For any command with timeout T, execution should not exceed T + epsilon (small margin)
  - **Validates: Requirements 7, 8, 9**

- [ ]* 33. Write integration test for SSH connection lifecycle
  - Test: establish connection, execute multiple commands, disconnect
  - Test: connection recovery after network interruption
  - Test: keepalive prevents connection timeout
  - Requires: Docker container with SSH server
  - _Requirements: 2, 9_

- [ ]* 34. Write integration test for persistence with real storage
  - Test: commands logged to SQLite database
  - Test: commands logged to JSON file
  - Test: log entries queryable after execution
  - _Requirements: 5_

- [ ] 35. Final Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.
