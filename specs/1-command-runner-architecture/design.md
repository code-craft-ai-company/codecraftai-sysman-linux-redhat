# Design - Command Runner Architecture

## Overview

The command runner architecture provides a pluggable, aspect-oriented framework for executing commands on Red Hat Linux systems. It supports both local subprocess execution and remote SSH execution through a unified `ICommandRunner` interface, enabling system management features to operate transparently across execution modes.

The architecture consists of three layers:

1. **Runner Layer**: `LocalCommandRunner` and `SSHCommandRunner` implementations that handle transport-specific execution
2. **Aspect Layer**: Composable behaviors (persistence, retry, timeout) that wrap command execution
3. **Metrics Layer**: Reliability tracking for SSH operations with detailed failure classification

All system management features (package management, service management, etc.) depend only on the `ICommandRunner` interface, making them testable with mock runners and deployable across local and remote systems without code changes.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner (Local or SSH)                      │
│  - Configures aspects (Persistence, Retry, Timeout)        │
│  - Injects runner into features                            │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              System Management Features                     │
│  PackageManager, ServiceManager, EnvironmentConfigurator    │
│  - Depend on ICommandRunner interface                       │
│  - Agnostic to transport (local vs SSH)                    │
└────────────────────────┬────────────────────────────────────┘
                         │
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
│ - subprocess.run()   │      │ - wraps SshAgent     │
│ - sudo -n            │      │   (automojo-interop) │
│ - direct env/cwd     │      │ - env/cwd via shell  │
│                      │      │ - sudo -n            │
└──────────┬───────────┘      └──────────┬───────────┘
           │                             │
           │                             ▼
           │                   ┌──────────────────────┐
           │                   │ SSHReliabilityMetrics│
           │                   │ - success_count      │
           │                   │ - failure_count      │
           │                   │ - retry_count        │
           │                   │ - avg_duration_ms    │
           │                   └──────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────┐
│                    Execution Aspects                        │
│  - PersistenceAspect: logs commands to storage              │
│  - RetryAspect: retries transient failures                  │
│  - TimeoutAspect: enforces global timeout policy            │
│  - Applied in order: before_execute → run → after_execute   │
└─────────────────────────────────────────────────────────────┘
```

---

## Components

### `ICommandRunner` (Protocol)

**Location:** `automojo.sysman.linux.redhat.runners.base`

A `@runtime_checkable` Protocol that every runner implements.  Features depend on this protocol and therefore compose with any runner — local, SSH, or mock.

```python
@runtime_checkable
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
        stdin: str | None = None,
    ) -> RunResult: ...

    def add_aspect(self, aspect: IExecutionAspect) -> None: ...
    def remove_aspect(self, aspect: IExecutionAspect) -> None: ...
    def get_metrics(self) -> SSHReliabilityMetrics | None: ...
```

The `stdin` parameter lets callers pipe arbitrary bytes into the remote command (see Requirement 13).  `get_metrics` returns `None` for runners that do not track reliability metrics (e.g., `LocalCommandRunner`).

### `LocalCommandRunner`

**Location:** `automojo.sysman.linux.redhat.runners.local`

Executes commands via local subprocess.

| Method | Description |
|---|---|
| `__init__(config)` | Initialize with `LocalRunnerConfig` |
| `run(...)` | Execute command via `subprocess.run()` |
| `add_aspect(aspect)` | Register an execution aspect |
| `get_metrics()` | Returns `None` (no reliability tracking for local) |

**Configuration:**

```python
@dataclass
class LocalRunnerConfig:
    dry_run: bool = False
    sudo_path: str = "/usr/bin/sudo"
```

### `SSHCommandRunner`

**Location:** `automojo.sysman.linux.redhat.runners.ssh`

Executes commands over SSH with connection management and reliability tracking.

| Method | Description |
|---|---|
| `__init__(config)` | Initialize with `SSHRunnerConfig` and establish connection |
| `run(...)` | Execute command over SSH channel |
| `connect()` | Establish SSH connection with retry logic |
| `disconnect()` | Close SSH connection |
| `add_aspect(aspect)` | Register an execution aspect |
| `get_metrics()` | Returns `SSHReliabilityMetrics` |
| `reset_metrics()` | Clear all reliability counters |

**Configuration:**

```python
@dataclass
class SSHRunnerConfig:
    host: str
    port: int = 22
    username: str
    password: str | None = None
    key_filename: str | None = None
    key_data: str | None = None
    timeout: float = 30.0
    keepalive_interval: float = 60.0
    max_connection_retries: int = 3
    retry_backoff_base: float = 2.0
    dry_run: bool = False
    sudo_path: str = "/usr/bin/sudo"
```

---

## Data Models

### `RunResult`

Returned by all command executions.

```python
@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    command: list[str]
    timed_out: bool = False
    dry_run: bool = False
    duration_ms: float | None = None
    retry_count: int = 0
    transport: str = "local"  # "local" or "ssh"
```

### `SSHReliabilityMetrics`

Tracks reliability statistics for SSH operations.

```python
@dataclass
class SSHReliabilityMetrics:
    success_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    connection_error_count: int = 0
    retry_count: int = 0
    total_duration_ms: float = 0.0
    min_duration_ms: float | None = None
    max_duration_ms: float | None = None
    avg_duration_ms: float | None = None
    last_error: str | None = None
    last_error_type: SSHErrorType | None = None
```

### `SSHErrorType`

Classification of SSH errors.

```python
class SSHErrorType(Enum):
    NETWORK_ERROR = "network_error"
    AUTH_ERROR = "auth_error"
    HOST_KEY_ERROR = "host_key_error"
    TIMEOUT_ERROR = "timeout_error"
    CHANNEL_ERROR = "channel_error"
    UNKNOWN_ERROR = "unknown_error"
```

### `ExecutionContext`

Passed to aspects before and after execution.

```python
@dataclass
class ExecutionContext:
    args: list[str]
    elevated: bool
    cwd: str | None
    env: dict[str, str] | None
    timeout: float | None
    transport: str
    dry_run: bool
    metadata: dict[str, Any]  # for aspects to store data
```

---

## Execution Aspects

### `IExecutionAspect` (Protocol)

```python
class IExecutionAspect(Protocol):
    def before_execute(self, context: ExecutionContext) -> None:
        """Called before command execution. May modify context or raise exception to abort."""
        ...
    
    def after_execute(self, context: ExecutionContext, result: RunResult) -> RunResult:
        """Called after command execution. May modify result."""
        ...
    
    @property
    def enabled(self) -> bool:
        """Whether this aspect is currently active."""
        ...
```

### `PersistenceAspect`

**Location:** `automojo.sysman.linux.redhat.aspects.persistence`

Logs all command executions to a persistent store.

```python
@dataclass
class PersistenceAspectConfig:
    storage_backend: ICommandStorage
    log_environment: bool = False  # whether to log env vars
    log_output: bool = True         # whether to log stdout/stderr
    fail_on_storage_error: bool = False
```

**Storage Interface:**

```python
class ICommandStorage(Protocol):
    def log_command(self, entry: CommandLogEntry) -> str:
        """Log a command execution. Returns entry ID."""
        ...
    
    def update_command(self, entry_id: str, result: RunResult) -> None:
        """Update a log entry with execution result."""
        ...
```

**Behavior:**
- `before_execute`: Creates log entry with timestamp, command, context
- `after_execute`: Updates log entry with result, duration, exit code

### `RetryAspect`

**Location:** `automojo.sysman.linux.redhat.aspects.retry`

Automatically retries commands on transient failures.

```python
@dataclass
class RetryAspectConfig:
    max_retries: int = 3
    backoff_base: float = 2.0
    retryable_exit_codes: list[int] = field(default_factory=lambda: [])
    retryable_error_patterns: list[str] = field(default_factory=list)
```

**Behavior:**
- Wraps the `run()` call in retry logic
- Checks exit code and stderr against retryable patterns
- Applies exponential backoff between retries
- Updates `RunResult.retry_count` on success after retries

### `TimeoutAspect`

**Location:** `automojo.sysman.linux.redhat.aspects.timeout`

Enforces a global timeout policy.

```python
@dataclass
class TimeoutAspectConfig:
    default_timeout: float
    allow_override: bool = True
```

**Behavior:**
- `before_execute`: Sets `context.timeout` if not already set
- `after_execute`: No-op (timeout enforcement happens in runner)

---

## LocalCommandRunner Implementation

### Command Execution Flow

1. **Aspect Before Phase**: Call `aspect.before_execute(context)` for each aspect
2. **Dry-Run Check**: If `dry_run=True`, return synthetic success result
3. **Elevation**: If `elevated=True`, prepend `["sudo", "-n"]` to args
4. **Environment Merge**: Merge `env` with `os.environ` if provided
5. **Subprocess Invocation**: Call `subprocess.run(args, cwd=cwd, env=merged_env, timeout=timeout, capture_output=True)`
6. **Result Mapping**: Map `CompletedProcess` to `RunResult`
7. **Aspect After Phase**: Call `aspect.after_execute(context, result)` for each aspect in reverse order
8. **Return**: Return final `RunResult`

### Elevation Strategy

```python
if elevated:
    args = [self.config.sudo_path, "-n"] + args
```

The `-n` flag prevents sudo from prompting for a password, causing immediate failure if passwordless sudo is not configured.

### Timeout Handling

```python
try:
    proc = subprocess.run(..., timeout=timeout)
except subprocess.TimeoutExpired as e:
    return RunResult(
        exit_code=-1,
        stdout=e.stdout.decode() if e.stdout else "",
        stderr=e.stderr.decode() if e.stderr else "Command timed out",
        command=args,
        timed_out=True,
        transport="local"
    )
```

---

## SSHCommandRunner Implementation

### Connection Management

**Connection Pool**: Maintains a single persistent SSH connection per runner instance.

**Connection Lifecycle:**
1. `__init__`: Calls `connect()` to establish initial connection
2. `run()`: Checks connection health before each command
3. `run()`: Reconnects if connection is closed
4. `disconnect()`: Explicitly closes connection (called by coordinator or context manager)

**Retry Logic:**

```python
def connect(self) -> None:
    for attempt in range(self.config.max_connection_retries):
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.client.connect(
                hostname=self.config.host,
                port=self.config.port,
                username=self.config.username,
                password=self.config.password,
                key_filename=self.config.key_filename,
                timeout=self.config.timeout
            )
            self._start_keepalive()
            return
        except Exception as e:
            if attempt == self.config.max_connection_retries - 1:
                raise SSHConnectionError(...)
            time.sleep(self.config.retry_backoff_base ** attempt)
```

### Command Execution Flow

1. **Aspect Before Phase**: Call `aspect.before_execute(context)` for each aspect
2. **Dry-Run Check**: If `dry_run=True`, return synthetic success result without connecting
3. **Connection Check**: Verify connection is alive, reconnect if needed
4. **Command Building**:
   - If `cwd`: prepend `cd <cwd> &&`
   - If `env`: prepend `export VAR=value &&` for each var
   - If `elevated`: prepend `sudo -n`
5. **Channel Execution**: Open SSH channel, execute command, capture output
6. **Metrics Update**: Increment success/failure counters, record duration
7. **Aspect After Phase**: Call `aspect.after_execute(context, result)` for each aspect in reverse order
8. **Return**: Return final `RunResult`

### Command Building Example

```python
def _build_command(self, args: list[str], context: ExecutionContext) -> str:
    parts = []
    
    # Environment variables
    if context.env:
        for key, value in context.env.items():
            parts.append(f"export {key}={shlex.quote(value)}")
    
    # Working directory
    if context.cwd:
        parts.append(f"cd {shlex.quote(context.cwd)}")
    
    # Elevation
    if context.elevated:
        args = [self.config.sudo_path, "-n"] + args
    
    # Command
    parts.append(" ".join(shlex.quote(arg) for arg in args))
    
    return " && ".join(parts)
```

### Reliability Metrics Tracking

```python
def _update_metrics(self, result: RunResult, error: Exception | None = None) -> None:
    if result.exit_code == 0:
        self.metrics.success_count += 1
    else:
        self.metrics.failure_count += 1
    
    if result.timed_out:
        self.metrics.timeout_count += 1
    
    if error:
        self.metrics.connection_error_count += 1
        self.metrics.last_error = str(error)
        self.metrics.last_error_type = self._classify_error(error)
    
    if result.duration_ms:
        self.metrics.total_duration_ms += result.duration_ms
        self.metrics.min_duration_ms = min(
            self.metrics.min_duration_ms or float('inf'),
            result.duration_ms
        )
        self.metrics.max_duration_ms = max(
            self.metrics.max_duration_ms or 0,
            result.duration_ms
        )
        count = self.metrics.success_count + self.metrics.failure_count
        self.metrics.avg_duration_ms = self.metrics.total_duration_ms / count
```

---

## Aspect Composition

Aspects are applied in a nested fashion:

```
PersistenceAspect.before_execute()
  RetryAspect.before_execute()
    TimeoutAspect.before_execute()
      [COMMAND EXECUTION]
    TimeoutAspect.after_execute()
  RetryAspect.after_execute()
PersistenceAspect.after_execute()
```

The `RetryAspect` is special: it wraps the entire execution including other aspects' after phase, so retries re-execute the full aspect chain.

---

## Error Handling

### SSH Error Classification

```python
def _classify_error(self, error: Exception) -> SSHErrorType:
    if isinstance(error, paramiko.AuthenticationException):
        return SSHErrorType.AUTH_ERROR
    elif isinstance(error, paramiko.SSHException) and "host key" in str(error).lower():
        return SSHErrorType.HOST_KEY_ERROR
    elif isinstance(error, socket.timeout):
        return SSHErrorType.TIMEOUT_ERROR
    elif isinstance(error, (socket.error, paramiko.SSHException)):
        return SSHErrorType.NETWORK_ERROR
    elif isinstance(error, paramiko.ChannelException):
        return SSHErrorType.CHANNEL_ERROR
    else:
        return SSHErrorType.UNKNOWN_ERROR
```

### Exception Types

```python
class SSHConnectionError(Exception):
    """Raised when SSH connection cannot be established after all retries."""
    def __init__(self, host: str, port: int, error_type: SSHErrorType, attempts: int, last_error: str):
        self.host = host
        self.port = port
        self.error_type = error_type
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(f"Failed to connect to {host}:{port} after {attempts} attempts: {last_error}")
```

---

## Module Layout

```
source/packages/automojo/sysman/linux/redhat/
├── __init__.py
├── runners/
│   ├── __init__.py
│   ├── local.py              ← LocalCommandRunner
│   ├── ssh.py                ← SSHCommandRunner
│   └── models.py             ← RunResult, SSHReliabilityMetrics, etc.
├── aspects/
│   ├── __init__.py
│   ├── base.py               ← IExecutionAspect protocol
│   ├── persistence.py        ← PersistenceAspect
│   ├── retry.py              ← RetryAspect
│   └── timeout.py            ← TimeoutAspect
└── storage/
    ├── __init__.py
    ├── base.py               ← ICommandStorage protocol
    ├── sqlite.py             ← SQLite storage backend
    └── json.py               ← JSON file storage backend

source/testroots/testbase/
└── automojo/tests/sysman/linux/redhat/
    ├── runners/
    │   ├── test_local.py
    │   └── test_ssh.py
    └── aspects/
        ├── test_persistence.py
        ├── test_retry.py
        └── test_timeout.py
```

---

## Testing Strategy

### Unit Testing

**LocalCommandRunner:**
- Mock `subprocess.run()` to verify args, cwd, env, timeout
- Test elevation by verifying sudo prepending
- Test dry-run mode without invoking subprocess
- Test timeout handling with `TimeoutExpired` exception

**SSHCommandRunner:**
- Mock `paramiko.SSHClient` to verify connection parameters
- Test connection retry logic with simulated failures
- Test command building with env, cwd, elevation
- Test metrics tracking with various success/failure scenarios
- Test error classification with different exception types

**Aspects:**
- Test each aspect in isolation with mock runner
- Test aspect composition with multiple aspects
- Test aspect exception handling (abort on before_execute error)
- Test persistence aspect with mock storage backend
- Test retry aspect with configurable retry policies

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing. Key properties:

- **Transport Equivalence**: For any command, local and SSH execution should produce equivalent results (modulo timing)
- **Aspect Idempotence**: Applying the same aspect twice should not change behavior
- **Retry Convergence**: For any retryable command, retry aspect should eventually succeed or exhaust retries
- **Metrics Accuracy**: SSH metrics counters should always sum correctly
- **Connection Resilience**: SSH runner should recover from transient connection failures

### Integration Testing

- Test actual SSH connections to a test container
- Test command execution over SSH with real output
- Test keepalive behavior with long-running commands
- Test connection recovery after network interruption
- Test persistence aspect with real storage backends

---

## Usage Examples

### Local Execution

```python
from automojo.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig

config = LocalRunnerConfig(dry_run=False)
runner = LocalCommandRunner(config)

result = runner.run(["ls", "-la"], cwd="/tmp")
print(f"Exit code: {result.exit_code}")
print(f"Output: {result.stdout}")
```

### SSH Execution with Aspects

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.aspects import PersistenceAspect, RetryAspect
from automojo.sysman.linux.redhat.storage import SQLiteStorage

# Configure SSH runner
ssh_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5
)
runner = SSHCommandRunner(ssh_config)

# Add persistence aspect
storage = SQLiteStorage("/var/log/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

# Add retry aspect
retry = RetryAspect(RetryAspectConfig(max_retries=3))
runner.add_aspect(retry)

# Execute command
result = runner.run(["systemctl", "restart", "nginx"], elevated=True)

# Check metrics
metrics = runner.get_metrics()
print(f"Success rate: {metrics.success_count / (metrics.success_count + metrics.failure_count)}")
print(f"Average duration: {metrics.avg_duration_ms}ms")
```

### Feature Integration

```python
from automojo.sysman.linux.redhat.packagemanagement import PackageManager, PackageManagerConfig
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig

# Create SSH runner for remote system
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))

# Inject into package manager
pkg_config = PackageManagerConfig(elevated=True)
pkg_manager = PackageManager(runner, pkg_config)

# Use package manager transparently (doesn't know it's SSH)
result = pkg_manager.install("nginx")
print(f"Installation {'succeeded' if result.status == 'SUCCESS' else 'failed'}")
```

