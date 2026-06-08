# Design

## Overview

The command execution feature is implemented as a single module,
`ccai.sysman.linux.redhat.commandexecution`, that exposes a `CommandExecutor` class.
The coordinator instantiates `CommandExecutor` and calls its methods to run commands,
sequences, and shell scripts through the `ICommandRunner` abstraction.

No `subprocess` calls, file system access, or environment inspection occurs directly
inside this module, except for:
- Creating and deleting a temporary script file for `run_script()` (via `tempfile` and
  `pathlib`).
- Checking that a `working_dir` path exists (via `pathlib.Path.is_dir()`).
- Merging caller-supplied `env` with `os.environ` for `run()` and `run_script()`.

Everything else goes through `ICommandRunner`.

The feature works identically whether the underlying runner executes locally (via 
`LocalCommandRunner`) or remotely over SSH (via `SSHCommandRunner`). When using SSH, the
coordinator can track reliability metrics and apply execution aspects (persistence, retry,
timeout) without any changes to the `CommandExecutor` code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md) for
details on local/SSH execution, reliability tracking, and aspect configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner (Local or SSH)                      │
│  - Configures aspects (optional)                           │
│  - Injects runner into CommandExecutor                     │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            CommandExecutor                                  │
│  ccai.sysman.linux.redhat.commandexecution             │
│                                                             │
│  run()          run_sequence()                              │
│  run_script()                                               │
└────────────────────────┬────────────────────────────────────┘
                         │ delegates to
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                   ICommandRunner                            │
│  run(args, elevated, cwd, env, timeout) → RunResult         │
└────────────────────────┬────────────────────────────────────┘
                         │
          ┌──────────────┴──────────────┐
          ▼                             ▼
┌──────────────────────┐      ┌──────────────────────┐
│ LocalCommandRunner   │      │ SSHCommandRunner     │
│ - subprocess.run()   │      │ - SSH connection     │
│ - sudo for elevation │      │ - reliability metrics│
│ - local execution    │      │ - connection retry   │
└──────────────────────┘      └──────────────────────┘
          │                             │
          └──────────────┬──────────────┘
                         │ optional
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    Execution Aspects                        │
│  - PersistenceAspect: audit logging                         │
│  - RetryAspect: transient failure retry                     │
│  - TimeoutAspect: global timeout policy                     │
└─────────────────────────────────────────────────────────────┘
```

---

## Components

### `CommandExecutor`

**Location:** `source/packages/ccai/sysman/linux/redhat/commandexecution.py`

| Method | Signature | Description |
|---|---|---|
| `run` | `(args, *, elevated, working_dir, env, timeout_seconds, on_output, on_error) → CommandResult` | Execute a single command |
| `run_sequence` | `(commands, *, stop_on_failure) → SequenceResult` | Execute an ordered list of `CommandSpec` objects |
| `run_script` | `(script, *, elevated, working_dir, env, timeout_seconds, on_output, on_error) → CommandResult` | Write script to temp file and run via `bash` |

All keyword-only arguments are optional with documented defaults.

### `CommandExecutorConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no commands are executed |

---

## Data Models

### `CommandSpec`

Represents a single command within a sequence.

```python
@dataclass
class CommandSpec:
    args: list[str]
    elevated: bool = False
    working_dir: str | None = None
    env: dict[str, str] | None = None
    timeout_seconds: float | None = None
    on_output: Callable[[str], None] | None = None
    on_error: Callable[[str], None] | None = None
    label: str | None = None         # optional human-readable name for reporting
```

### `CommandResult`

```python
@dataclass
class CommandResult:
    success: bool
    exit_code: int | None
    stdout: str
    stderr: str
    args: list[str]
    timed_out: bool = False
    dry_run: bool = False
```

### `SequenceResult`

```python
@dataclass
class SequenceResult:
    all_succeeded: bool
    results: list[CommandResult]
    failed_at: int | None     # index of first failed command, or None
    dry_run: bool = False
```

---

## Interfaces

### `ICommandRunner` (from `codecraftai-interfaces`)

```python
class ICommandRunner(Protocol):
    def run(
        self,
        args: list[str],
        *,
        elevated: bool = False,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        on_output: Callable[[str], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        capture_output: bool = True,
    ) -> RunResult: ...
```

---

## Method Designs

### `run(args, *, elevated, working_dir, env, timeout_seconds, on_output, on_error)`

1. If `dry_run=True`: return `CommandResult(success=True, exit_code=0, stdout="", stderr="", args=args, dry_run=True)`.
2. If `working_dir` is not `None` and `Path(working_dir).is_dir()` is `False`:
   return `CommandResult(success=False, exit_code=1, stdout="", stderr=f"working_dir does not exist: {working_dir}", args=args)`.
3. Build merged env: `{**os.environ, **env}` when `env` is provided; else `None`.
4. Call `self._runner.run(args, elevated=elevated, cwd=working_dir, env=merged_env, timeout=timeout_seconds, on_output=on_output, on_error=on_error)`.
5. Map `RunResult` to `CommandResult`:
   - `success = (run_result.exit_code == 0)`
   - `timed_out = run_result.timed_out`
   - If `timed_out`, set `stderr` to a standard timeout message if runner did not populate it.

### `run_sequence(commands, *, stop_on_failure=True)`

1. If `commands` is empty: return `SequenceResult(all_succeeded=True, results=[], failed_at=None)`.
2. If `dry_run=True`: build one dry-run `CommandResult` per spec and return `SequenceResult(all_succeeded=True, results=[...], dry_run=True)`.
3. Iterate `commands`:
   - Call `self.run(spec.args, elevated=spec.elevated, working_dir=spec.working_dir, env=spec.env, timeout_seconds=spec.timeout_seconds, on_output=spec.on_output, on_error=spec.on_error)`.
   - Append `CommandResult` to `results`.
   - If `not result.success` and `stop_on_failure=True`: return `SequenceResult(all_succeeded=False, results=results, failed_at=i)`.
4. `all_succeeded = all(r.success for r in results)`
5. `failed_at = next((i for i, r in enumerate(results) if not r.success), None)`
6. Return `SequenceResult(all_succeeded=all_succeeded, results=results, failed_at=failed_at)`.

### `run_script(script, *, elevated, working_dir, env, timeout_seconds, on_output, on_error)`

1. If `dry_run=True`: return `CommandResult(success=True, exit_code=0, stdout="", stderr="", args=["bash", "<dry-run>"], dry_run=True)`.
2. Write `script` to a `tempfile.NamedTemporaryFile` with `suffix=".sh"`, `delete=False`.
3. `chmod` the temp file to `0o755` via `os.chmod`.
4. Call `self.run(["bash", tmpfile_path], elevated=elevated, working_dir=working_dir, env=env, timeout_seconds=timeout_seconds, on_output=on_output, on_error=on_error)`.
5. Delete the temp file via `Path(tmpfile_path).unlink(missing_ok=True)` in a `finally` block.
6. Return the `CommandResult` from step 4.

---

## Environment Merging

The merged environment is computed as `{**os.environ, **caller_env}` so that caller
values take precedence over inherited process environment.  When `env=None` the runner
receives `None` and uses its own default (typically the unmodified process environment).

---

## Dry-Run Behaviour

When `CommandExecutorConfig.dry_run=True`:
- `run()` returns immediately with a synthetic success result and `dry_run=True`.
- `run_sequence()` builds one synthetic result per spec and returns immediately.
- `run_script()` returns immediately — the temp file is never created.

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Success Mapping Consistency

*For any* command execution, if the exit code is 0, then `success` should be `True`; if the exit code is non-zero, then `success` should be `False`.

**Validates: Requirements 1.2, 1.3**

### Property 2: Dry-Run Safety

*For any* command execution (run, run_sequence, run_script) with `dry_run=True`, the command runner should never be invoked, and the result should have `dry_run=True` with `success=True`.

**Validates: Requirements 1.5, 2.6, 3.6**

### Property 3: Working Directory Validation

*For any* command execution with a `working_dir` parameter, if the directory does not exist, the command should return a failure result without invoking the command runner.

**Validates: Requirements 4.3**

### Property 4: Environment Merging Correctness

*For any* command execution with an `env` parameter, the merged environment passed to the runner should contain all keys from `os.environ` plus all keys from the provided `env`, with `env` values taking precedence on conflicts.

**Validates: Requirements 5.1, 5.3**

### Property 5: Sequence Stop-on-Failure Behavior

*For any* command sequence with `stop_on_failure=True`, if a command fails, no subsequent commands should be executed, and `failed_at` should equal the index of the first failed command.

**Validates: Requirements 2.3**

### Property 6: Sequence Continue-on-Failure Behavior

*For any* command sequence with `stop_on_failure=False`, all commands should be executed regardless of failures, and `failed_at` should equal the index of the first failed command (or `None` if all succeed).

**Validates: Requirements 2.4**

### Property 7: Empty Sequence Handling

*For any* empty command list, `run_sequence()` should return `all_succeeded=True` with an empty results list without invoking the command runner.

**Validates: Requirements 2.5**

### Property 8: Script Temp File Cleanup

*For any* script execution, the temporary script file should be deleted after execution completes, regardless of whether the script succeeded or failed.

**Validates: Requirements 3.4**

### Property 9: Timeout Propagation

*For any* command execution with a `timeout_seconds` parameter, the timeout value should be passed to the command runner, and if the runner reports a timeout, the result should have `timed_out=True` and `success=False`.

**Validates: Requirements 6.1, 6.2**

### Property 10: Elevation Propagation

*For any* command execution with `elevated=True`, the command runner should be invoked with `elevated=True`; when `elevated=False` or not specified, the runner should receive `elevated=False`.

**Validates: Requirements 8.1, 8.2**

### Property 11: Output Capture Completeness

*For any* command execution, the returned `CommandResult` should contain the complete stdout and stderr from the command, regardless of whether streaming callbacks were provided.

**Validates: Requirements 7.3**

---

## Module Layout

```
source/packages/ccai/sysman/linux/redhat/
├── __init__.py
└── commandexecution.py         ← CommandExecutor, config, models

source/testroots/testbase/
└── ccai/tests/sysman/linux/redhat/
    └── test_commandexecution.py
```

---

---

## Usage Examples

### Local Execution (Default)

```python
from ccai.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from ccai.sysman.linux.redhat.commandexecution import CommandExecutor, CommandExecutorConfig, CommandSpec

# Create local runner
runner = LocalCommandRunner(LocalRunnerConfig())

# Inject into command executor
config = CommandExecutorConfig(dry_run=False)
cmd_executor = CommandExecutor(runner, config)

# Execute single command
result = cmd_executor.run(["ls", "-la"], elevated=False, working_dir="/tmp")
print(f"Exit code: {result.exit_code}")
print(f"Output: {result.stdout}")

# Execute command sequence
commands = [
    CommandSpec(["systemctl", "stop", "nginx"], elevated=True),
    CommandSpec(["systemctl", "start", "nginx"], elevated=True),
    CommandSpec(["systemctl", "status", "nginx"], elevated=False)
]
seq_result = cmd_executor.run_sequence(commands, stop_on_failure=True)
print(f"All succeeded: {seq_result.all_succeeded}")
```

### SSH Execution with Reliability Tracking

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.commandexecution import CommandExecutor, CommandExecutorConfig

# Create SSH runner for remote system
ssh_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0
)
runner = SSHCommandRunner(ssh_config)

# Inject into command executor (same code as local!)
config = CommandExecutorConfig()
cmd_executor = CommandExecutor(runner, config)

# Execute script over SSH
script = """
#!/bin/bash
echo "Checking disk space..."
df -h
echo "Checking memory..."
free -h
"""
result = cmd_executor.run_script(script, elevated=False)

# Check SSH reliability metrics
metrics = runner.get_metrics()
print(f"Success rate: {metrics.success_count / (metrics.success_count + metrics.failure_count):.2%}")
print(f"Average duration: {metrics.avg_duration_ms:.2f}ms")
print(f"Connection retries: {metrics.retry_count}")
```

### SSH Execution with Aspects

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.aspects import PersistenceAspect, RetryAspect, TimeoutAspect
from ccai.sysman.linux.redhat.aspects import PersistenceAspectConfig, RetryAspectConfig, TimeoutAspectConfig
from ccai.sysman.linux.redhat.storage import SQLiteStorage
from ccai.sysman.linux.redhat.commandexecution import CommandExecutor, CommandExecutorConfig

# Create SSH runner
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))

# Add persistence aspect for audit logging
storage = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

# Add retry aspect for transient failures
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[255],  # SSH connection errors
    retryable_error_patterns=["Connection reset", "Broken pipe"]
))
runner.add_aspect(retry)

# Add timeout aspect for global policy
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=300.0))
runner.add_aspect(timeout)

# Inject into command executor
cmd_executor = CommandExecutor(runner, CommandExecutorConfig())

# All commands now have:
# - Audit logging to SQLite
# - Automatic retry on connection failures
# - 5-minute timeout enforcement
result = cmd_executor.run(["systemctl", "restart", "nginx"], elevated=True)

# Execute sequence with aspects applied to each command
commands = [
    CommandSpec(["systemctl", "stop", "apache2"], elevated=True),
    CommandSpec(["systemctl", "start", "nginx"], elevated=True),
    CommandSpec(["systemctl", "reload", "nginx"], elevated=True)
]
seq_result = cmd_executor.run_sequence(commands)
```

### Dry-Run Mode Across Transports

```python
# Dry-run works identically for local and SSH
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin", dry_run=True))
cmd_executor = CommandExecutor(runner, CommandExecutorConfig())

# No SSH connection is made, no commands executed
result = cmd_executor.run(["rm", "-rf", "/important/data"], elevated=True)
assert result.dry_run == True
assert result.success == True  # Synthetic success in dry-run
```

---

## Testing Strategy

### Unit Testing (Implemented)

Tests live in
`source/testroots/testbase/ccai/tests/sysman/linux/redhat/test_commandexecution.py`
and use the `ccai.testbase` assertion helpers.

- A local `_MockRunner` dataclass stubs `ICommandRunner`, letting each test queue a
  sequence of `RunResult` instances and inspect the call history.
- `working_dir` existence checks are verified with a known-bad path for the not-found
  case; the mock runner is asserted not to have been called.
- Temp file creation and cleanup for `run_script()` is verified by inspecting the
  `MockRunner` call args (`args[0] == "bash"`, `args[1]` ends with `.sh`) and confirming
  the file no longer exists after the call (both success and failure paths).
- Sequence stop-on-failure is verified by counting runner invocations against the
  provided spec list.
- Dry-run for all three methods is verified by asserting the runner is never called.
- Timeout handling uses a `RunResult(timed_out=True)` and asserts the default timeout
  message is injected when the runner supplies an empty `stderr`.
- Environment merging is verified by patching `os.environ` and inspecting the `env`
  kwarg passed to the runner.
- No test requires a live system or root access.

### Property Validation

The Correctness Properties defined above are validated by the unit tests rather than
by generative (hypothesis-style) property testing.  Each property is covered by one or
more targeted unit tests that exercise the relevant edge cases with hand-crafted
inputs.  Generative property-based testing is a potential future enhancement but is
not currently in scope.
