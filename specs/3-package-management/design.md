# Design

## Overview

The package management feature is implemented as a single module,
`ccai.sysman.linux.redhat.packagemanagement`, that exposes a `PackageManager` class.
The coordinator instantiates `PackageManager` and calls its methods to perform discrete
maintenance actions.

All system interaction is delegated to a command runner implementing the `ICommandRunner`
interface. The `PackageManager` never directly calls `subprocess` or shell commands; it only
constructs command argument lists and passes them to the runner. This keeps business logic
separate from execution and makes the feature fully testable via a mock runner without root
access or a live system.

The feature works identically whether commands execute locally (via `LocalCommandRunner`) or
remotely over SSH (via `SSHCommandRunner`). When using SSH, the coordinator can track
reliability metrics and apply execution aspects (persistence, retry, timeout) without any
changes to the `PackageManager` code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md) for
details on local/SSH execution, reliability tracking, and aspect configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner (Local or SSH)                      │
│  - Configures aspects (optional)                           │
│  - Injects runner into PackageManager                      │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            PackageManager                                   │
│  ccai.sysman.linux.redhat.packagemanagement             │
│                                                             │
│  install()   remove()   update()                            │
│  query()     list_updates()                                 │
│  install_local()                                            │
│  hold()      release_hold()                                 │
│  group_install()  group_remove()                            │
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

### `PackageManager`

**Location:** `source/packages/ccai/sysman/linux/redhat/packagemanagement.py`

The primary public class.  Constructed with an `ICommandRunner` and a `PackageManagerConfig`.

| Method | Description |
|---|---|
| `install(name, version=None)` | Install a package, optionally at a specific version |
| `remove(name)` | Remove an installed package |
| `update(name, version=None)` | Update a package, optionally to a specific version |
| `query(name)` → `PackageInfo \| None` | Return installed package info or None |
| `list_updates()` → `list[PackageUpdateInfo]` | List all packages with available updates |
| `install_local(filepath)` | Install from a local `.rpm` file path |
| `hold(name)` | Pin a package version using dnf versionlock |
| `release_hold(name)` | Remove a dnf versionlock pin |
| `group_install(group_name)` | Install a dnf package group |
| `group_remove(group_name)` | Remove a dnf package group |

### `PackageManagerConfig`

**Location:** `source/packages/ccai/sysman/linux/redhat/packagemanagement.py`

A dataclass holding runtime configuration for `PackageManager`.

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no commands are executed |
| `elevated` | `bool` | `False` | When `True`, write commands are prefixed with sudo |

---

## Data Models

### `PackageActionResult`

Returned by all write operations (install, remove, update, hold, group install/remove).

```python
@dataclass
class PackageActionResult:
    status: PackageActionStatus   # SUCCESS | FAILED | SKIPPED | DRY_RUN
    package_name: str
    version: str | None           # resolved version after action, if known
    exit_code: int | None         # raw exit code from command runner
    stdout: str | None
    stderr: str | None
    command: list[str] | None     # the command that was (or would have been) run
```

### `PackageActionStatus`

```python
class PackageActionStatus(Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"   # already in desired state, no action needed
    DRY_RUN  = "dry_run"   # would have acted, but dry_run=True
```

### `PackageInfo`

Returned by `query()`.

```python
@dataclass
class PackageInfo:
    name: str
    version: str
    release: str
    arch: str
```

### `PackageUpdateInfo`

Returned as elements of `list_updates()`.

```python
@dataclass
class PackageUpdateInfo:
    name: str
    installed_version: str
    available_version: str
    arch: str
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Idempotent Install Operations

*For any* package name and version, calling `install()` multiple times with the same arguments should result in the same final system state, with subsequent calls returning `SKIPPED` status.

**Validates: Requirements 1.3**

### Property 2: Idempotent Remove Operations

*For any* package name, calling `remove()` on a non-installed package should return `SKIPPED` status without invoking the command runner.

**Validates: Requirements 2.2**

### Property 3: Query Consistency

*For any* package name, if `query()` returns a `PackageInfo` object, then calling `query()` again immediately should return an equivalent `PackageInfo` object with the same name, version, release, and arch.

**Validates: Requirements 4.2**

### Property 4: Dry-Run Safety

*For any* write operation (install, remove, update, hold, release_hold, group_install, group_remove) with `dry_run=True`, the command runner should never be invoked with a write command, and the result status should be `DRY_RUN`.

**Validates: Requirements 1.6, 2.5, 3.6, 7.5, 8.5, 9.5**

### Property 5: Command Construction Correctness

*For any* package operation, the constructed command arguments passed to the runner should match the expected command format specified in the Command Mappings table.

**Validates: Requirements 1.1, 1.2, 2.1, 3.1, 3.2, 4.1, 5.1, 6.1, 7.1, 8.1, 9.1, 9.2**

### Property 6: Elevation Consistency

*For any* write operation with `elevated=True` in the config, the command runner should be invoked with `elevated=True`; for read operations, `elevated` should never be `True`.

**Validates: Requirements 10.1, 10.2**

### Property 7: File Existence Validation

*For any* file path provided to `install_local()`, if the file does not exist on disk, a `PackageFileNotFoundError` should be raised before the command runner is invoked.

**Validates: Requirements 6.2**

### Property 8: Status Mapping Correctness

*For any* operation result from the command runner, the `PackageActionResult` status should correctly reflect the runner's exit code: 0 maps to `SUCCESS`, non-zero maps to `FAILED` (unless the operation was skipped or in dry-run mode).

**Validates: Requirements 1.4, 1.5, 2.3, 2.4, 3.4, 3.5**

### Property 9: Update List Exit Code Handling

*For any* call to `list_updates()`, exit codes 0 and 100 from `dnf check-update` should be treated as success, while any other exit code should raise `PackageQueryError`.

**Validates: Requirements 5.4, 5.5**

### Property 10: Hold Idempotency

*For any* package name, calling `hold()` when a hold already exists should return `SKIPPED` status; calling `release_hold()` when no hold exists should return `SKIPPED` status.

**Validates: Requirements 7.2, 8.2**

---

## Interfaces

### `ICommandRunner` (from `codecraftai-interfaces`)

`PackageManager` depends on this interface, injected at construction time.

```python
class ICommandRunner(Protocol):
    def run(
        self,
        args: list[str],
        elevated: bool = False,
        capture_output: bool = True,
    ) -> RunResult: ...
```

### `RunResult` (from `codecraftai-interfaces`)

```python
@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    command: list[str]
```

### Elevation Strategy

When `PackageManagerConfig.elevated` is `True`, the `PackageManager` passes
`elevated=True` to `ICommandRunner.run()`.  The runner is responsible for prepending
`sudo` or using whatever privilege escalation mechanism is appropriate for the environment.
`PackageManager` has no knowledge of how elevation is implemented.

---

## Command Mappings

| Operation | Command |
|---|---|
| Install by name | `dnf install -y <name>` |
| Install at version | `dnf install -y <name>-<version>` |
| Remove | `dnf remove -y <name>` |
| Update latest | `dnf update -y <name>` |
| Update to version | `dnf install -y <name>-<version>` |
| Query installed | `rpm -q --queryformat '%{NAME} %{VERSION} %{RELEASE} %{ARCH}\n' <name>` |
| List updates | `dnf check-update` |
| Install local file | `dnf install -y <filepath>` |
| Hold version | `dnf versionlock add <name>` |
| Release hold | `dnf versionlock delete <name>` |
| Group install | `dnf groupinstall -y "<group>"` |
| Group remove | `dnf groupremove -y "<group>"` |

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `PackageQueryError` | `rpm -q` or `dnf check-update` returns an unexpected non-zero exit code |
| `PackageFileNotFoundError` | `install_local()` is called with a path that does not exist on disk |

All other failures are surfaced as a `PackageActionResult` with `status=FAILED` so the
coordinator can decide how to respond without catching exceptions for normal operational
failures.

### `dnf check-update` Exit Code 100

`dnf check-update` exits with code `100` when updates are available (not an error) and
code `0` when the system is fully up to date.  `PackageManager` treats both as success.
Any other exit code raises `PackageQueryError`.

### Idempotency Guards

Before executing write commands, `PackageManager` calls `query()` to determine current
state.  If the system is already in the desired state the method returns `SKIPPED` without
invoking `dnf`.  This makes all write operations safe to call multiple times.

---

## Module Layout

```
source/packages/ccai/sysman/linux/redhat/
├── __init__.py
└── packagemanagement.py          ← PackageManager, config, models, exceptions

source/testroots/testbase/
└── ccai/tests/sysman/linux/redhat/
    └── test_packagemanagement.py ← unit tests using MockCommandRunner
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` injected into `PackageManager` at construction time.
- Tests verify the *arguments* passed to the runner, not system state.
- Each public method has at minimum: a success path test, a skip/idempotency test, a
  failure path test, and a dry-run test.
- `list_updates()` has an additional test for exit code `100` being treated as success.
- `install_local()` has a test for `PackageFileNotFoundError` before the runner is called.
- No test requires root access or a live `dnf` installation.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in Python. Each correctness property defined above will be implemented as a property-based test that:

- Generates random but valid inputs (package names, versions, configurations)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific correctness property they validate, using the format: `# Feature: package-management, Property N: <property description>`

**Property Test Coverage:**

- Property 1-2: Idempotency tests generate random package names and verify repeated operations
- Property 3: Query consistency tests verify stable results across multiple calls
- Property 4: Dry-run safety tests verify no runner invocation across all operations
- Property 5: Command construction tests verify correct argument formatting
- Property 6: Elevation tests verify correct elevation flag usage
- Property 7: File validation tests generate random file paths
- Property 8-9: Status mapping tests verify correct result interpretation
- Property 10: Hold idempotency tests verify skip behavior

Both unit tests and property-based tests are complementary: unit tests verify specific examples and edge cases, while property tests verify universal correctness across many inputs.

---

## Usage Examples

### Local Execution (Default)

```python
from ccai.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from ccai.sysman.linux.redhat.packagemanagement import PackageManager, PackageManagerConfig

# Create local runner
runner = LocalCommandRunner(LocalRunnerConfig())

# Inject into package manager
config = PackageManagerConfig(elevated=True, dry_run=False)
pkg_manager = PackageManager(runner, config)

# Use package manager
result = pkg_manager.install("nginx")
print(f"Installation: {result.status}")
```

### SSH Execution with Reliability Tracking

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.packagemanagement import PackageManager, PackageManagerConfig

# Create SSH runner for remote system
ssh_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0
)
runner = SSHCommandRunner(ssh_config)

# Inject into package manager (same code as local!)
config = PackageManagerConfig(elevated=True)
pkg_manager = PackageManager(runner, config)

# Use package manager - it doesn't know it's SSH
result = pkg_manager.install("nginx")

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
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.aspects import PersistenceAspect, RetryAspect, TimeoutAspect
from ccai.sysman.linux.redhat.aspects import PersistenceAspectConfig, RetryAspectConfig, TimeoutAspectConfig
from ccai.sysman.linux.redhat.storage import SQLiteStorage
from ccai.sysman.linux.redhat.packagemanagement import PackageManager, PackageManagerConfig

# Create SSH runner
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))

# Add persistence aspect for audit logging
storage = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

# Add retry aspect for transient failures (e.g., network glitches)
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[1, 255],
    retryable_error_patterns=["Connection reset", "Temporary failure"]
))
runner.add_aspect(retry)

# Add timeout aspect for global policy
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=300.0))
runner.add_aspect(timeout)

# Inject into package manager
pkg_manager = PackageManager(runner, PackageManagerConfig(elevated=True))

# All package operations now have:
# - Audit logging to SQLite
# - Automatic retry on transient failures
# - 5-minute timeout enforcement
result = pkg_manager.install("nginx")

# Query command history from storage
# (storage backend provides query methods)
```

### Dry-Run Mode Across Transports

```python
# Dry-run works identically for local and SSH
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin", dry_run=True))
pkg_manager = PackageManager(runner, PackageManagerConfig(elevated=True))

# No SSH connection is made, no commands executed
result = pkg_manager.install("nginx")
assert result.status == PackageActionStatus.DRY_RUN
```
