# Requirements - Command Runner Architecture

## Introduction

The command runner architecture provides the foundational abstraction for executing commands on Red Hat Linux systems, supporting both local and remote (SSH) execution modes. This architecture enables all system management features in the `automojo-sysman-linux-redhat` package to operate transparently across local and remote systems while maintaining reliability, auditability, and testability.

The architecture introduces three key concepts:

1. **Command Runner Abstraction**: A unified interface (`ICommandRunner`) with pluggable implementations for local and SSH execution
2. **Measured Reliability**: Automatic tracking and reporting of reliability metrics for SSH operations, including connection stability, retry behavior, and failure modes
3. **Execution Aspects**: Cross-cutting concerns that control command execution behavior, including persistence (command history logging), retry policies, and timeout strategies

A maintenance coordinator instantiates a command runner (local or SSH) and injects it into system management features. The features remain agnostic to the execution mode, while the runner handles transport-specific concerns and applies configured aspects.

---

## Glossary

- **Command Runner**: An implementation of `ICommandRunner` that executes commands either locally or over SSH
- **Execution Aspect**: A reusable behavior that wraps command execution to provide cross-cutting functionality (logging, retries, metrics)
- **Reliability Metric**: A measured value tracking the success, failure, or performance of command execution over SSH
- **Persistence Aspect**: An aspect that logs command execution details to a persistent store for audit and replay purposes
- **Transport**: The mechanism used to execute commands (local subprocess or SSH connection)

---

## Requirements

### Requirement 1: Unified Command Runner Interface

**User Story:** As a system management feature developer, I want a single interface for command execution, so that my feature works identically whether commands run locally or over SSH.

#### Acceptance Criteria

1. WHEN a feature receives an `ICommandRunner` instance THE SYSTEM SHALL execute commands through the runner without knowledge of the underlying transport.
2. WHEN the runner is a `LocalCommandRunner` THE SYSTEM SHALL execute commands via local subprocess invocation.
3. WHEN the runner is an `SSHCommandRunner` THE SYSTEM SHALL execute commands via SSH connection to a remote host.
4. WHEN a command is executed THE SYSTEM SHALL return a `RunResult` with the same structure regardless of transport.
5. WHEN a feature requests elevated execution THE SYSTEM SHALL apply the appropriate elevation mechanism for the transport (sudo for local, sudo over SSH for remote).

---

### Requirement 2: SSH Connection Management

**User Story:** As a maintenance coordinator, I want SSH connections to be managed reliably with automatic reconnection, so that transient network issues do not cause maintenance workflows to fail unnecessarily.

#### Acceptance Criteria

1. WHEN an `SSHCommandRunner` is instantiated with connection parameters THE SYSTEM SHALL establish an SSH connection to the target host.
2. WHEN a command is executed and the connection is closed THE SYSTEM SHALL attempt to reconnect before failing.
3. WHEN a connection attempt fails THE SYSTEM SHALL retry up to a configured maximum number of times with exponential backoff.
4. WHEN all connection attempts are exhausted THE SYSTEM SHALL raise an `SSHConnectionError` with details of all attempts.
5. WHEN a connection is idle for longer than a configured keepalive interval THE SYSTEM SHALL send a keepalive packet to prevent connection timeout.

---

### Requirement 3: SSH Reliability Measurement

**User Story:** As a maintenance coordinator, I want detailed metrics on SSH command execution reliability, so that I can identify and diagnose network or host issues affecting maintenance operations.

#### Acceptance Criteria

1. WHEN commands are executed over SSH THE SYSTEM SHALL track the number of successful executions, failed executions, connection errors, and timeouts.
2. WHEN a command execution completes THE SYSTEM SHALL record the execution duration in milliseconds.
3. WHEN a connection retry occurs THE SYSTEM SHALL increment a retry counter and record the retry reason.
4. WHEN reliability metrics are requested THE SYSTEM SHALL return an `SSHReliabilityMetrics` object with cumulative counts and timing statistics.
5. WHEN metrics are reset THE SYSTEM SHALL clear all counters and timing data.

---

### Requirement 4: Execution Aspect Framework

**User Story:** As a maintenance coordinator, I want to apply cross-cutting behaviors to command execution without modifying individual features, so that I can consistently enforce policies like logging, retries, and timeouts across all operations.

#### Acceptance Criteria

1. WHEN a command runner is configured with aspects THE SYSTEM SHALL apply each aspect in order before and after command execution.
2. WHEN an aspect's `before_execute` method is called THE SYSTEM SHALL pass the command arguments and execution context.
3. WHEN an aspect's `after_execute` method is called THE SYSTEM SHALL pass the command arguments, execution context, and `RunResult`.
4. WHEN an aspect raises an exception in `before_execute` THE SYSTEM SHALL abort command execution and propagate the exception.
5. WHEN multiple aspects are configured THE SYSTEM SHALL apply them in the order they were registered.

---

### Requirement 5: Persistence Aspect for Command History

**User Story:** As a maintenance coordinator, I want all executed commands to be logged to a persistent store, so that I can audit what operations were performed and replay them if needed.

#### Acceptance Criteria

1. WHEN a `PersistenceAspect` is configured with a storage backend THE SYSTEM SHALL log every command execution to the backend.
2. WHEN a command is about to execute THE SYSTEM SHALL record the command arguments, working directory, environment variables, timestamp, and execution mode (local/SSH).
3. WHEN a command execution completes THE SYSTEM SHALL update the log entry with exit code, stdout, stderr, duration, and success status.
4. WHEN a command execution fails or times out THE SYSTEM SHALL record the failure reason in the log entry.
5. WHEN the storage backend is unavailable THE SYSTEM SHALL log a warning but SHALL NOT fail the command execution.

---

### Requirement 6: Retry Aspect for Transient Failures

**User Story:** As a maintenance coordinator, I want commands to automatically retry on transient failures, so that temporary issues like network glitches do not cause maintenance workflows to fail.

#### Acceptance Criteria

1. WHEN a `RetryAspect` is configured with a retry policy THE SYSTEM SHALL retry failed commands according to the policy.
2. WHEN a command fails with a retryable error THE SYSTEM SHALL wait for the configured backoff duration and retry the command.
3. WHEN a command fails with a non-retryable error THE SYSTEM SHALL NOT retry and SHALL return the failure immediately.
4. WHEN the maximum retry count is reached THE SYSTEM SHALL return the last failure result.
5. WHEN a retry succeeds THE SYSTEM SHALL return the successful result and record the number of retries in the result metadata.

---

### Requirement 7: Timeout Aspect for Hung Commands

**User Story:** As a maintenance coordinator, I want a global timeout policy applied to all commands, so that hung processes do not block maintenance workflows indefinitely.

#### Acceptance Criteria

1. WHEN a `TimeoutAspect` is configured with a default timeout THE SYSTEM SHALL apply the timeout to all commands that do not specify their own timeout.
2. WHEN a command specifies its own timeout THE SYSTEM SHALL use the command-specific timeout instead of the aspect's default.
3. WHEN a command exceeds the timeout THE SYSTEM SHALL terminate the command and return a `RunResult` with `timed_out=True`.
4. WHEN a timeout occurs over SSH THE SYSTEM SHALL close the SSH channel and mark the connection as potentially unstable.

---

### Requirement 8: Local Command Runner Implementation

**User Story:** As a maintenance coordinator, I want to execute commands on the local system using subprocess, so that I can manage the system where the maintenance process is running.

#### Acceptance Criteria

1. WHEN a `LocalCommandRunner` executes a command THE SYSTEM SHALL invoke the command via `subprocess.run()` or equivalent.
2. WHEN `elevated=True` is requested THE SYSTEM SHALL prepend `sudo` to the command arguments.
3. WHEN a working directory is specified THE SYSTEM SHALL pass it as the `cwd` parameter to subprocess.
4. WHEN environment variables are specified THE SYSTEM SHALL merge them with the process environment and pass to subprocess.
5. WHEN a timeout is specified THE SYSTEM SHALL pass it to subprocess and handle `TimeoutExpired` exceptions.

---

### Requirement 9: SSH Command Runner Implementation

**User Story:** As a maintenance coordinator, I want to execute commands on a remote system over SSH, so that I can manage systems without requiring local access.

#### Acceptance Criteria

1. WHEN an `SSHCommandRunner` is instantiated THE SYSTEM SHALL accept host, port, username, and authentication parameters (password or key).
2. WHEN a command is executed THE SYSTEM SHALL open an SSH channel, execute the command, capture output, and close the channel.
3. WHEN `elevated=True` is requested THE SYSTEM SHALL prepend `sudo` to the command executed over SSH.
4. WHEN a working directory is specified THE SYSTEM SHALL prepend `cd <dir> &&` to the command.
5. WHEN environment variables are specified THE SYSTEM SHALL prepend `export VAR=value &&` statements to the command.
6. WHEN a timeout is specified THE SYSTEM SHALL set a channel timeout and handle timeout exceptions.

---

### Requirement 10: Aspect Configuration and Composition

**User Story:** As a maintenance coordinator, I want to configure multiple aspects on a command runner, so that I can compose behaviors like logging + retries + timeouts.

#### Acceptance Criteria

1. WHEN aspects are added to a runner THE SYSTEM SHALL store them in registration order.
2. WHEN a command is executed THE SYSTEM SHALL apply `before_execute` for each aspect in order.
3. WHEN a command completes THE SYSTEM SHALL apply `after_execute` for each aspect in reverse order.
4. WHEN an aspect modifies the execution context THE SYSTEM SHALL pass the modified context to subsequent aspects.
5. WHEN an aspect is disabled THE SYSTEM SHALL skip its `before_execute` and `after_execute` methods.

---

### Requirement 11: Dry-Run Mode Across Transports

**User Story:** As a maintenance coordinator, I want dry-run mode to work identically for local and SSH execution, so that I can safely test maintenance workflows without making changes.

#### Acceptance Criteria

1. WHEN a command runner is configured with `dry_run=True` THE SYSTEM SHALL NOT execute any commands.
2. WHEN dry-run mode is active THE SYSTEM SHALL return a synthetic `RunResult` with `exit_code=0`, empty output, and `dry_run=True`.
3. WHEN dry-run mode is active and aspects are configured THE SYSTEM SHALL still invoke aspect `before_execute` and `after_execute` methods to test aspect logic.
4. WHEN dry-run mode is active over SSH THE SYSTEM SHALL NOT open SSH connections or channels.

---

### Requirement 12: Error Classification for SSH Operations

**User Story:** As a maintenance coordinator, I want SSH errors to be classified by type, so that I can distinguish between network issues, authentication failures, and command execution failures.

#### Acceptance Criteria

1. WHEN an SSH connection fails due to network issues THE SYSTEM SHALL raise an `SSHConnectionError` with error type `NETWORK_ERROR`.
2. WHEN an SSH connection fails due to authentication THE SYSTEM SHALL raise an `SSHConnectionError` with error type `AUTH_ERROR`.
3. WHEN an SSH connection fails due to host key verification THE SYSTEM SHALL raise an `SSHConnectionError` with error type `HOST_KEY_ERROR`.
4. WHEN a command execution fails over SSH THE SYSTEM SHALL return a `RunResult` with `success=False` and the actual exit code.
5. WHEN an SSH channel times out THE SYSTEM SHALL return a `RunResult` with `timed_out=True` and error type `TIMEOUT_ERROR`.

