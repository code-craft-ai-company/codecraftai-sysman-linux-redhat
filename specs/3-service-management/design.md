# Design

## Overview

The service management feature is implemented as a single module,
`automojo.sysman.linux.redhat.servicemanagement`, that exposes a `ServiceManager` class.
The coordinator instantiates `ServiceManager` and calls its methods to perform discrete
maintenance actions against the `systemd` service manager via `systemctl` and `journalctl`.

All system interaction is delegated to a command runner implementing the `ICommandRunner`
interface. The `ServiceManager` never directly calls `subprocess` or any shell; it only
constructs command argument lists and passes them to the runner. This keeps business logic
separate from execution and makes the feature fully testable via a mock runner without root
access or a live `systemd` instance.

The feature works identically whether commands execute locally (via `LocalCommandRunner`) or
remotely over SSH (via `SSHCommandRunner`). When using SSH, the coordinator can track
reliability metrics and apply execution aspects (persistence, retry, timeout) without any
changes to the `ServiceManager` code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md) for
details on local/SSH execution, reliability tracking, and aspect configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner (Local or SSH)                      │
│  - Configures aspects (optional)                           │
│  - Injects runner into ServiceManager                      │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            ServiceManager                                   │
│  automojo.sysman.linux.redhat.servicemanagement             │
│                                                             │
│  start()        stop()                                      │
│  restart()      reload()                                    │
│  enable()       disable()                                   │
│  mask()         unmask()                                    │
│  query()        daemon_reload()                             │
│  get_journal()                                              │
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

### `ServiceManager`

**Location:** `source/packages/automojo/sysman/linux/redhat/servicemanagement.py`

The primary public class.  Constructed with an `ICommandRunner` and a
`ServiceManagerConfig`.

| Method | Signature | Description |
|---|---|---|
| `start` | `(name) → ServiceActionResult` | Start a service |
| `stop` | `(name) → ServiceActionResult` | Stop a service |
| `restart` | `(name) → ServiceActionResult` | Restart a service |
| `reload` | `(name) → ServiceActionResult` | Send reload signal to a service |
| `enable` | `(name) → ServiceActionResult` | Enable a service at boot |
| `disable` | `(name) → ServiceActionResult` | Disable a service at boot |
| `mask` | `(name) → ServiceActionResult` | Mask a service |
| `unmask` | `(name) → ServiceActionResult` | Unmask a service |
| `query` | `(name) → ServiceInfo` | Query active and enabled state |
| `daemon_reload` | `() → ServiceActionResult` | Reload the systemd daemon |
| `get_journal` | `(name, lines=50) → str` | Retrieve recent journal output |

### `ServiceManagerConfig`

**Location:** `source/packages/automojo/sysman/linux/redhat/servicemanagement.py`

A dataclass holding runtime configuration for `ServiceManager`.

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no commands are executed |
| `elevated` | `bool` | `False` | When `True`, write commands are passed with `elevated=True` |

---

## Data Models

### `ServiceActionResult`

Returned by all write operations (start, stop, restart, reload, enable, disable, mask,
unmask, daemon-reload).

```python
@dataclass
class ServiceActionResult:
    status: ServiceActionStatus   # SUCCESS | FAILED | SKIPPED | DRY_RUN
    service_name: str
    exit_code: int | None
    stdout: str | None
    stderr: str | None
    command: list[str] | None     # the command that was (or would have been) run
```

### `ServiceActionStatus`

```python
class ServiceActionStatus(Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"   # already in desired state
    DRY_RUN  = "dry_run"   # would have acted, but dry_run=True
```

### `ServiceInfo`

Returned by `query()`.

```python
@dataclass
class ServiceInfo:
    service_name: str
    active_state: str    # "active" | "inactive" | "failed" | "not-found" | ...
    enabled_state: str   # "enabled" | "disabled" | "masked" | "not-found" | ...
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Idempotent Start Operations

*For any* service name, calling `start()` on an already active service should return `SKIPPED` status without invoking the command runner.

**Validates: Requirements 1.2**

### Property 2: Idempotent Stop Operations

*For any* service name, calling `stop()` on an already inactive service should return `SKIPPED` status without invoking the command runner.

**Validates: Requirements 2.2**

### Property 3: Idempotent Enable Operations

*For any* service name, calling `enable()` on an already enabled service should return `SKIPPED` status without invoking the command runner.

**Validates: Requirements 5.2**

### Property 4: Idempotent Disable Operations

*For any* service name, calling `disable()` on an already disabled service should return `SKIPPED` status without invoking the command runner.

**Validates: Requirements 6.2**

### Property 5: Idempotent Mask Operations

*For any* service name, calling `mask()` on an already masked service should return `SKIPPED` status without invoking the command runner.

**Validates: Requirements 7.2**

### Property 6: Idempotent Unmask Operations

*For any* service name, calling `unmask()` on a non-masked service should return `SKIPPED` status without invoking the command runner.

**Validates: Requirements 8.2**

### Property 7: Dry-Run Safety

*For any* write operation (start, stop, restart, reload, enable, disable, mask, unmask, daemon_reload) with `dry_run=True`, the command runner should never be invoked, and the result status should be `DRY_RUN`.

**Validates: Requirements 1.5, 2.5, 3.4, 4.5, 5.5, 6.5, 7.5, 8.5, 10.4**

### Property 8: Command Construction Correctness

*For any* service operation, the constructed command arguments passed to the runner should match the expected command format specified in the Command Mappings table.

**Validates: Requirements 1.1, 2.1, 3.1, 4.1, 5.1, 6.1, 7.1, 8.1, 9.1, 10.1, 11.1**

### Property 9: Elevation Consistency

*For any* write operation with `elevated=True` in the config, the command runner should be invoked with `elevated=True`; for read operations (query, get_journal), `elevated` should never be `True`.

**Validates: Requirements 12.1, 12.2, 12.3**

### Property 10: Query State Consistency

*For any* service name, if `query()` returns a `ServiceInfo` object, then calling `query()` again immediately should return an equivalent `ServiceInfo` object with the same active_state and enabled_state.

**Validates: Requirements 9.2**

### Property 11: Reload Precondition

*For any* service name, calling `reload()` when the service is not in active state should return `FAILED` status without invoking the reload command.

**Validates: Requirements 4.2**

### Property 12: Status Mapping Correctness

*For any* operation result from the command runner, the `ServiceActionResult` status should correctly reflect the runner's exit code: 0 maps to `SUCCESS`, non-zero maps to `FAILED` (unless the operation was skipped or in dry-run mode).

**Validates: Requirements 1.3, 1.4, 2.3, 2.4, 3.2, 3.3, 4.3, 4.4, 5.3, 5.4, 6.3, 6.4, 7.3, 7.4, 8.3, 8.4, 10.2, 10.3**

---

## Interfaces

### `ICommandRunner` (from `automojo-interfaces`)

`ServiceManager` depends on this interface, injected at construction time.

```python
class ICommandRunner(Protocol):
    def run(
        self,
        args: list[str],
        elevated: bool = False,
        capture_output: bool = True,
    ) -> RunResult: ...
```

### `RunResult` (from `automojo-interfaces`)

```python
@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    command: list[str]
```

---

## Command Mappings

| Operation | Command |
|---|---|
| Start service | `systemctl start <name>` |
| Stop service | `systemctl stop <name>` |
| Restart service | `systemctl restart <name>` |
| Reload service | `systemctl reload <name>` |
| Enable service | `systemctl enable <name>` |
| Disable service | `systemctl disable <name>` |
| Mask service | `systemctl mask <name>` |
| Unmask service | `systemctl unmask <name>` |
| Reload daemon | `systemctl daemon-reload` |
| Query active state | `systemctl is-active <name>` |
| Query enabled state | `systemctl is-enabled <name>` |
| Query journal | `journalctl -u <name> --no-pager -n <lines>` |

---

## Idempotency Guards

State is determined by calling `query()` before issuing write commands.
`query()` runs both `is-active` and `is-enabled` and stores the results in a `ServiceInfo`.

| Method | Guard condition | Expected state to skip |
|---|---|---|
| `start()` | `active_state == "active"` | Already running |
| `stop()` | `active_state == "inactive"` | Already stopped |
| `enable()` | `enabled_state == "enabled"` | Already enabled |
| `disable()` | `enabled_state == "disabled"` | Already disabled |
| `mask()` | `enabled_state == "masked"` | Already masked |
| `unmask()` | `enabled_state != "masked"` | Not masked, nothing to undo |
| `reload()` | `active_state != "active"` | Must be running; returns `FAILED` |

`restart()` and `daemon_reload()` are unconditional — they never skip.

---

## `systemctl is-active` and `is-enabled` Exit Codes

`systemctl is-active` and `is-enabled` return non-zero exit codes for non-active or
non-enabled states respectively.  These are not errors; they are informational.
`ServiceManager` always treats the stdout string as the state value and never treats a
non-zero exit code from these two commands as a failure.

| `is-active` stdout | Meaning |
|---|---|
| `active` | Service is running |
| `inactive` | Service is stopped normally |
| `failed` | Service has failed |
| `activating` | Service is starting |
| `not-found` | Unit does not exist |

| `is-enabled` stdout | Meaning |
|---|---|
| `enabled` | Starts at boot |
| `disabled` | Does not start at boot |
| `masked` | Completely blocked |
| `static` | Enabled by dependency only |
| `not-found` | Unit does not exist |

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `ServiceQueryError` | `is-active`, `is-enabled`, or `journalctl` returns an unexpected error (not a state-informational non-zero) |

All other failures from write operations are surfaced as a `ServiceActionResult` with
`status=FAILED` so the coordinator can decide how to respond without catching exceptions.

---

## Module Layout

```
source/packages/automojo/sysman/linux/redhat/
├── __init__.py
└── servicemanagement.py          ← ServiceManager, config, models, exceptions

source/testroots/testbase/
└── automojo/tests/sysman/linux/redhat/
    └── test_servicemanagement.py ← unit tests using MockCommandRunner
```

---

---

## Usage Examples

### Local Execution (Default)

```python
from automojo.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from automojo.sysman.linux.redhat.servicemanagement import ServiceManager, ServiceManagerConfig

# Create local runner
runner = LocalCommandRunner(LocalRunnerConfig())

# Inject into service manager
config = ServiceManagerConfig(elevated=True, dry_run=False)
svc_manager = ServiceManager(runner, config)

# Use service manager
result = svc_manager.restart("nginx")
print(f"Restart: {result.status}")

# Query service status
info = svc_manager.query("nginx")
print(f"Active: {info.active}, Enabled: {info.enabled}")
```

### SSH Execution with Reliability Tracking

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.servicemanagement import ServiceManager, ServiceManagerConfig

# Create SSH runner for remote system
ssh_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0
)
runner = SSHCommandRunner(ssh_config)

# Inject into service manager (same code as local!)
config = ServiceManagerConfig(elevated=True)
svc_manager = ServiceManager(runner, config)

# Use service manager - it doesn't know it's SSH
result = svc_manager.restart("nginx")

# Check SSH reliability metrics
metrics = runner.get_metrics()
print(f"Success rate: {metrics.success_count / (metrics.success_count + metrics.failure_count):.2%}")
print(f"Average duration: {metrics.avg_duration_ms:.2f}ms")
print(f"Connection retries: {metrics.retry_count}")
if metrics.last_error:
    print(f"Last error: {metrics.last_error_type.value} - {metrics.last_error}")
```

### SSH Execution with Aspects

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.aspects import PersistenceAspect, RetryAspect, TimeoutAspect
from automojo.sysman.linux.redhat.aspects import PersistenceAspectConfig, RetryAspectConfig, TimeoutAspectConfig
from automojo.sysman.linux.redhat.storage import SQLiteStorage
from automojo.sysman.linux.redhat.servicemanagement import ServiceManager, ServiceManagerConfig

# Create SSH runner
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))

# Add persistence aspect for audit logging
storage = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

# Add retry aspect for transient failures (e.g., systemd busy)
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[1, 5],  # systemd temporary failures
    retryable_error_patterns=["System is booting up", "Failed to connect to bus"]
))
runner.add_aspect(retry)

# Add timeout aspect for global policy
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=120.0))
runner.add_aspect(timeout)

# Inject into service manager
svc_manager = ServiceManager(runner, ServiceManagerConfig(elevated=True))

# All service operations now have:
# - Audit logging to SQLite
# - Automatic retry on systemd busy states
# - 2-minute timeout enforcement
result = svc_manager.restart("nginx")

# Query service journal with SSH
journal_output = svc_manager.get_journal("nginx", lines=100)
print("Recent nginx logs:", journal_output)
```

### Dry-Run Mode Across Transports

```python
# Dry-run works identically for local and SSH
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin", dry_run=True))
svc_manager = ServiceManager(runner, ServiceManagerConfig(elevated=True))

# No SSH connection is made, no commands executed
result = svc_manager.restart("nginx")
assert result.status == ServiceActionStatus.DRY_RUN
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` injected into `ServiceManager` at construction time.
- Tests verify the *arguments* passed to the runner, not live systemd state.
- `MockCommandRunner` is configured per-test to return specific `RunResult` values for
  `is-active` and `is-enabled` so that idempotency guard logic is exercised in isolation.
- Each public write method has: success path, skip/idempotency path, failure path, dry-run.
- `query()` is tested for each meaningful `active_state` and `enabled_state` value.
- `get_journal()` is tested for default line count and a custom line count.
- No test requires root access, a live `systemctl`, or `journald`.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in Python. Each correctness property defined above will be implemented as a property-based test that:

- Generates random but valid inputs (service names, states, configurations)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific correctness property they validate, using the format: `# Feature: service-management, Property N: <property description>`

**Property Test Coverage:**

- Properties 1-6: Idempotency tests generate random service names and verify repeated operations
- Property 7: Dry-run safety tests verify no runner invocation across all operations
- Property 8: Command construction tests verify correct argument formatting
- Property 9: Elevation tests verify correct elevation flag usage
- Property 10: Query consistency tests verify stable results across multiple calls
- Property 11: Reload precondition tests verify failure when service not active
- Property 12: Status mapping tests verify correct result interpretation

Both unit tests and property-based tests are complementary: unit tests verify specific examples and edge cases, while property tests verify universal correctness across many inputs.
