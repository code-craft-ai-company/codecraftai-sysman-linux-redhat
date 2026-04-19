# Design

## Overview

The kdump management feature is implemented as a single module,
`automojo.sysman.linux.redhat.kdumpmanagement`, that exposes a `KdumpManager` class.
The coordinator instantiates `KdumpManager` and calls its methods to manage the kernel
crash-dump capture subsystem: the `kdump.service` lifecycle, `/etc/kdump.conf` keys, the
`crashkernel=` kernel command-line reservation, the kdump initramfs, and
`/etc/sysconfig/kdump` environment settings.

All system command execution is delegated to a command runner implementing the
`ICommandRunner` interface. All reads and writes against `/etc/kdump.conf`,
`/etc/sysconfig/kdump`, `/proc/cmdline`, `/sys/kernel/kexec_crash_loaded`, and
`/sys/kernel/kexec_crash_size` are delegated to a file operator implementing the
`IFileOperator` interface. `KdumpManager` never directly calls `subprocess`, opens
files, or issues shell commands; it constructs command argument lists and file paths and
passes them to the injected abstractions. This keeps business logic separate from
execution and makes the feature fully testable via mock runners and mock file operators
without root access or a live kdump installation.

**Cross-Feature Dependency: `boot-management`.** `KdumpManager` does not duplicate
kernel-command-line mutation logic. When the coordinator asks to set or remove the
`crashkernel=` reservation on installed kernels, `KdumpManager` delegates to an injected
`BootManager` instance, using its "Add Kernel Command-Line Arguments" and "Remove
Kernel Command-Line Arguments" capabilities. This keeps `grubby` and bootloader-specific
handling in a single place and lets `KdumpManager` focus on kdump semantics.

The feature works identically whether commands execute locally (via `LocalCommandRunner`)
or remotely over SSH (via `SSHCommandRunner`). When using SSH, the coordinator can track
reliability metrics and apply execution aspects (persistence, retry, timeout) without any
changes to the `KdumpManager` code.

**See Also:**

- [Command Runner Architecture](../command-runner-architecture/design.md) for details on
  local/SSH execution, reliability tracking, and aspect configuration.
- [boot-management](../boot-management/design.md) for the delegated kernel
  command-line mutation capabilities that `KdumpManager` uses for `crashkernel=`
  operations.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner (Local or SSH)                       │
│  - Instantiates file operator                               │
│  - Instantiates BootManager (for crashkernel= delegation)   │
│  - Configures aspects (optional)                            │
│  - Injects runner, file operator, BootManager into          │
│    KdumpManager                                             │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐         ┌──────────────────────┐
│            KdumpManager                                     │ ──────▶ │   BootManager        │
│  automojo.sysman.linux.redhat.kdumpmanagement               │ crash-  │  (boot-management)   │
│                                                             │ kernel  │                      │
│  get_service_state()   lifecycle_action()                   │ ops     │ add_kernel_arg()     │
│  get_config()          set_config()    delete_config()      │         │ remove_kernel_arg()  │
│  get_crashkernel_runtime()                                  │         └──────────────────────┘
│  get_crashkernel_persistent()                               │
│  set_crashkernel()     remove_crashkernel()                 │
│  rebuild_initramfs()   get_armed_state()                    │
│  get_sysconfig()       set_sysconfig()                      │
└────────────────────────┬────────────────────────────────────┘
                         │ delegates to
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            ICommandRunner        +        IFileOperator     │
│  run(args, elevated, ...)        read_text(path, elevated)  │
│    → RunResult                   write_text(path, content,  │
│                                     mode, owner, group,     │
│                                     elevated)               │
│                                  exists(path)               │
│                                  delete(path, elevated)     │
└────────────────────────┬────────────────────────────────────┘
                         │
          ┌──────────────┴──────────────┐
          ▼                             ▼
┌──────────────────────┐      ┌──────────────────────┐
│ LocalCommandRunner   │      │ SSHCommandRunner     │
│ LocalFileOperator    │      │ SSHFileOperator      │
│ - subprocess.run()   │      │ - SSH connection     │
│ - direct file IO     │      │ - SFTP / remote cmd  │
│ - sudo for elevation │      │ - reliability metrics│
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

### `KdumpManager`

**Location:** `source/packages/automojo/sysman/linux/redhat/kdumpmanagement.py`

The primary public class. Constructed with an `ICommandRunner`, an `IFileOperator`, and a
`KdumpManagerConfig` (which carries an injected `BootManager` used for `crashkernel=`
delegation).

| Method | Description |
|---|---|
| `get_service_state()` → `KdumpServiceState` | Query `kdump.service` active/enabled/armed state and reserved memory |
| `lifecycle_action(action)` → `KdumpActionResult` | Perform a lifecycle action: `start`, `stop`, `restart`, `enable`, `disable` |
| `get_config(key=None)` → `str \| None \| OrderedDict[str, str]` | Read one `/etc/kdump.conf` key or the full parsed configuration |
| `set_config(key, value)` → `KdumpActionResult` | Set or replace a key in `/etc/kdump.conf`; skip when the value already matches |
| `delete_config(key)` → `KdumpActionResult` | Remove a key line from `/etc/kdump.conf` |
| `get_crashkernel_runtime()` → `CrashkernelReservation \| None` | Read `crashkernel=` from `/proc/cmdline` |
| `get_crashkernel_persistent(kernel_path)` → `CrashkernelReservation \| None` | Read persistent `crashkernel=` via `grubby --info=<path>` |
| `set_crashkernel(value)` → `KdumpActionResult` | Delegate `crashkernel=<value>` set to `BootManager.add_kernel_arg` |
| `remove_crashkernel()` → `KdumpActionResult` | Delegate `crashkernel` removal to `BootManager.remove_kernel_arg` |
| `rebuild_initramfs()` → `KdumpActionResult` | Invoke `kdumpctl rebuild` |
| `get_armed_state()` → `KdumpArmedState` | Read `/sys/kernel/kexec_crash_loaded` and `/sys/kernel/kexec_crash_size` |
| `get_sysconfig(key)` → `str \| None` | Read one `KEY=value` entry from `/etc/sysconfig/kdump` |
| `set_sysconfig(key, value)` → `KdumpActionResult` | Set or replace `KEY=value` in `/etc/sysconfig/kdump`, preserving quoting |

### `KdumpManagerConfig`

**Location:** `source/packages/automojo/sysman/linux/redhat/kdumpmanagement.py`

A dataclass holding runtime configuration for `KdumpManager`.

| Field              | Type          | Default | Description                                                                       |
|--------------------|---------------|---------|-----------------------------------------------------------------------------------|
| `dry_run`          | `bool`        | `False` | When `True`, no commands are executed and no files are written                    |
| `elevated`         | `bool`        | `False` | When `True`, mutating commands and writes are issued with elevated privileges     |
| `boot_manager`     | `BootManager` | —       | The injected `boot-management` instance used for `crashkernel=` mutations         |

---

## Data Models

### `KdumpActionStatus`

```python
class KdumpActionStatus(Enum):
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"   # already in desired state, no action needed
    DRY_RUN = "dry_run"   # would have acted, but dry_run=True
```

### `KdumpActionResult`

Returned by all mutating operations (`lifecycle_action`, `set_config`, `delete_config`,
`set_crashkernel`, `remove_crashkernel`, `rebuild_initramfs`, `set_sysconfig`).

```python
@dataclass
class KdumpActionResult:
    status: KdumpActionStatus
    action: str                       # e.g. "lifecycle:enable", "set_config:path"
    exit_code: int | None             # raw exit code from the command runner, if any
    stdout: str | None
    stderr: str | None
    command: list[str] | None         # the command that was (or would have been) run
    note: str | None                  # advisory text (e.g. "reboot required")
```

### `KdumpServiceState`

Returned by `get_service_state()`.

```python
@dataclass
class KdumpServiceState:
    present: bool                     # True when the kdump.service unit exists
    active: bool | None               # None when present is False
    enabled: bool | None              # None when present is False
    armed: bool | None                # derived from kdumpctl status
    reserved_memory_bytes: int | None # parsed from kdumpctl status when available
```

### `CrashkernelReservation`

Returned by `get_crashkernel_runtime()` and `get_crashkernel_persistent()`.

```python
@dataclass
class CrashkernelReservation:
    raw: str                                 # e.g. "1G-4G:192M,4G-64G:256M,64G-:512M"
    parsed_ranges: list[tuple[str, str]]     # [("1G-4G", "192M"), ("4G-64G", "256M"), ...]
```

### `KdumpArmedState`

Returned by `get_armed_state()`.

```python
@dataclass
class KdumpArmedState:
    loaded: bool                      # True when /sys/kernel/kexec_crash_loaded == "1"
    reserved_bytes: int               # value of /sys/kernel/kexec_crash_size, 0 if absent
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Lifecycle Idempotency

*For any* lifecycle action (`start`, `stop`, `restart`, `enable`, `disable`), when the
service is already in the requested state, `lifecycle_action()` returns `SKIPPED` without
invoking the command runner for the mutating `systemctl` call.

**Validates: Requirements 2.2**

### Property 2: kdump.conf Set Idempotency

*For any* `(key, value)` pair, when the current value of `key` in `/etc/kdump.conf`
already equals the requested value, `set_config()` returns `SKIPPED` and does not write
the file.

**Validates: Requirements 4.3**

### Property 3: Persistent-Mode Edit Note

*For any* successful `set_config()` or `delete_config()` call, the returned
`KdumpActionResult` includes a `note` indicating that `kdumpctl rebuild` and a kdump
service restart are required for some keys to take effect.

**Validates: Requirements 4.4**

### Property 4: Crashkernel Delegate Skipped Propagation

*For any* `set_crashkernel(value)` call where the requested value already matches the
persistent value on every targeted kernel (as reported by `BootManager`), the method
returns `SKIPPED` and the `BootManager.add_kernel_arg` mutation is not invoked.

**Validates: Requirements 6.3**

### Property 5: Crashkernel Delegate Failure Propagation

*For any* `set_crashkernel()` or `remove_crashkernel()` call where the `BootManager`
delegate returns a `FAILED` result, `KdumpManager` propagates that failure as a
`KdumpActionResult` with `status=FAILED`, preserving the delegate's exit code and
stderr.

**Validates: Requirements 6.4**

### Property 6: Armed State Reads Only /sys Files

*For any* `get_armed_state()` call, only the files `/sys/kernel/kexec_crash_loaded` and
`/sys/kernel/kexec_crash_size` are read; no command is executed through the command
runner.

**Validates: Requirements 8.1, 8.2, 8.3, 8.4**

### Property 7: Dry-Run Safety

*For any* mutating operation (`lifecycle_action`, `set_config`, `delete_config`,
`set_crashkernel`, `remove_crashkernel`, `rebuild_initramfs`, `set_sysconfig`) with
`dry_run=True`, neither the command runner nor the file operator is invoked with a
mutating call, and the returned `KdumpActionResult` has `status=DRY_RUN`.

**Validates: Requirements 2.6, 4.5, 6.5, 7.4, 9.5**

### Property 8: Elevation Consistency

*For any* mutating operation with `elevated=True` in the config, the command runner and
file operator are invoked with `elevated=True`; for read-only query operations,
`elevated` is never `True`.

**Validates: Requirements 10.1, 10.2, 10.3**

### Property 9: Status Mapping Correctness

*For any* result returned by the command runner for a mutating kdump operation, the
`KdumpActionResult.status` reflects the runner's exit code: `0` maps to `SUCCESS`,
non-zero maps to `FAILED`, except when the operation was skipped or running in dry-run
mode.

**Validates: Requirements 2.4, 2.5, 7.2, 7.3**

### Property 10: Sysconfig Edit Quote Preservation

*For any* existing quoted value in `/etc/sysconfig/kdump` (single-quoted, double-quoted,
or unquoted), a successful `set_sysconfig(key, value)` call writes the new value using
the same quoting style that the existing entry used.

**Validates: Requirements 9.2**

### Property 11: Kdump Config Not Found

*For any* `get_config()` or `set_config()` call when `/etc/kdump.conf` does not exist,
`get_config()` raises `KdumpConfigNotFoundError` and `set_config()` raises
`KdumpConfigNotFoundError` before any write is attempted.

**Validates: Requirements 3.3**

---

## Interfaces

### `ICommandRunner` (from `automojo-interfaces`)

`KdumpManager` depends on this interface, injected at construction time.

```python
class ICommandRunner(Protocol):
    def run(
        self,
        args: list[str],
        elevated: bool = False,
        capture_output: bool = True,
    ) -> RunResult: ...
```

### `IFileOperator` (from `automojo-interfaces`)

`KdumpManager` depends on this interface for all reads and writes against
`/etc/kdump.conf`, `/etc/sysconfig/kdump`, `/proc/cmdline`, and the
`/sys/kernel/kexec_crash_*` files.

```python
class IFileOperator(Protocol):
    def read_text(self, path: str, elevated: bool = False) -> str: ...
    def write_text(
        self,
        path: str,
        content: str,
        mode: int | None = None,
        owner: str | None = None,
        group: str | None = None,
        elevated: bool = False,
    ) -> None: ...
    def exists(self, path: str) -> bool: ...
    def delete(self, path: str, elevated: bool = False) -> None: ...
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

### `BootManager` Delegate Interface

`KdumpManager` does not redefine the `boot-management` API. It uses only two methods,
whose shapes it relies on:

```python
class BootManager:
    def add_kernel_arg(self, argument: str) -> BootActionResult: ...
    def remove_kernel_arg(self, argument_key: str) -> BootActionResult: ...
```

The `BootActionResult` is translated into a `KdumpActionResult` by `KdumpManager` so that
callers receive consistent result types from kdump-level calls.

### Elevation Strategy

When `KdumpManagerConfig.elevated` is `True`, `KdumpManager` passes `elevated=True` to
`ICommandRunner.run()` and `IFileOperator.write_text()` / `delete()` for mutating calls.
The runner and file operator are responsible for prepending `sudo` or using whatever
privilege escalation mechanism is appropriate for the environment. `KdumpManager` has no
knowledge of how elevation is implemented.

---

## Command Mappings

| Operation                       | Command or File                                                 |
|---------------------------------|-----------------------------------------------------------------|
| Query service active            | `systemctl is-active kdump`                                     |
| Query service enabled           | `systemctl is-enabled kdump`                                    |
| Start service                   | `systemctl start kdump`                                         |
| Stop service                    | `systemctl stop kdump`                                          |
| Restart service                 | `systemctl restart kdump`                                       |
| Enable service                  | `systemctl enable kdump`                                        |
| Disable service                 | `systemctl disable kdump`                                       |
| Query kdump status / armed      | `kdumpctl status`                                               |
| Rebuild kdump initramfs         | `kdumpctl rebuild`                                              |
| Query runtime crashkernel       | Read `/proc/cmdline` (file operator)                            |
| Query armed loaded              | Read `/sys/kernel/kexec_crash_loaded` (file operator)           |
| Query armed reserved size       | Read `/sys/kernel/kexec_crash_size` (file operator)             |
| Read kdump config               | Read `/etc/kdump.conf` (file operator)                          |
| Write kdump config              | Write `/etc/kdump.conf`, mode `0600`, `root:root`, elevated     |
| Read sysconfig kdump            | Read `/etc/sysconfig/kdump` (file operator)                     |
| Write sysconfig kdump           | Write `/etc/sysconfig/kdump`, mode `0644`, `root:root`, elevated|
| Set persistent crashkernel      | Delegate to `BootManager.add_kernel_arg("crashkernel=<value>")` |
| Remove persistent crashkernel   | Delegate to `BootManager.remove_kernel_arg("crashkernel")`      |
| Query persistent crashkernel    | `grubby --info=<kernel_path>` (via `BootManager` read APIs)     |

---

## Error Handling

### Typed Exceptions

| Exception                    | Raised When                                                                 |
|------------------------------|-----------------------------------------------------------------------------|
| `KdumpQueryError`            | A query (`systemctl is-*`, `kdumpctl status`, or file read) fails unexpectedly |
| `KdumpConfigNotFoundError`   | `/etc/kdump.conf` does not exist when a read or write is requested          |

All other failures are surfaced as a `KdumpActionResult` with `status=FAILED` so the
coordinator can decide how to respond without catching exceptions for normal operational
failures.

### Reboot-Required Warning

`set_crashkernel()` and `remove_crashkernel()` always populate the `note` field of a
`SUCCESS` result with a reboot-required advisory, because kernel-command-line changes do
not take effect until the next boot. `set_config()` and `set_sysconfig()` populate the
`note` field with a "rebuild initramfs / restart service required" advisory so the
coordinator knows when to follow up with `rebuild_initramfs()` and
`lifecycle_action("restart")`.

### Idempotency Guards

Before executing mutating commands or writes, `KdumpManager` queries current state:

- `lifecycle_action()` calls `get_service_state()` before `systemctl` mutations.
- `set_config()` / `delete_config()` read `/etc/kdump.conf` before writing.
- `set_crashkernel()` / `remove_crashkernel()` read persistent crashkernel values before
  invoking `BootManager`.
- `set_sysconfig()` reads `/etc/sysconfig/kdump` before writing.

If the system is already in the desired state, the method returns `SKIPPED` without
invoking the runner, file operator write, or `BootManager` mutation.

---

## Module Layout

```
source/packages/automojo/sysman/linux/redhat/
├── __init__.py
└── kdumpmanagement.py           ← KdumpManager, config, models, exceptions

source/testroots/testbase/
└── automojo/tests/sysman/linux/redhat/
    └── test_kdumpmanagement.py  ← unit tests using MockCommandRunner,
                                    MockFileOperator, MockBootManager
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner`, a `MockFileOperator`, and a `MockBootManager`
  injected into `KdumpManager` at construction time.
- Tests verify the *arguments* passed to the runner, *paths and content* passed to the
  file operator, and *delegate calls* made to the `BootManager`, not system state.
- Each public method has at minimum: a success path test, a skip/idempotency test, a
  failure path test, and a dry-run test.
- `get_service_state()` has additional tests for the "unit not present" branch.
- `get_armed_state()` has additional tests for the "files absent" branch.
- `set_crashkernel()` / `remove_crashkernel()` verify `BootManager` delegation, including
  propagation of `SKIPPED` and `FAILED` delegate outcomes.
- No test requires root access, a live kdump service, or a running kernel with a
  `crashkernel=` reservation.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in Python.
Each correctness property defined above is implemented as a property-based test that:

- Generates random but valid inputs (service states, config keys/values, crashkernel
  specifiers, `/sys` file contents, elevation flags)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific correctness
property they validate, using the format:
`# Feature: kdump-management, Property N: <property description>`

**Property Test Coverage:**

- Property 1: Lifecycle idempotency across the five action types
- Property 2: `set_config` idempotency across random `(key, value)` pairs
- Property 3: Note inclusion on successful `set_config` / `delete_config`
- Property 4: `set_crashkernel` returns SKIPPED when delegate sees no change
- Property 5: Failure propagation from `BootManager` delegate
- Property 6: `get_armed_state` never touches the command runner
- Property 7: Dry-run safety across all mutating operations
- Property 8: Elevation consistency on mutating vs. read-only operations
- Property 9: Status mapping across random exit codes
- Property 10: Quote-style preservation in `set_sysconfig`
- Property 11: `KdumpConfigNotFoundError` raised when `/etc/kdump.conf` is missing

Both unit tests and property-based tests are complementary: unit tests verify specific
examples and edge cases, while property tests verify universal correctness across many
inputs.

---

## Usage Examples

### Local Execution (Default)

```python
from automojo.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from automojo.sysman.linux.redhat.fileoperators import LocalFileOperator
from automojo.sysman.linux.redhat.bootmanagement import BootManager, BootManagerConfig
from automojo.sysman.linux.redhat.kdumpmanagement import KdumpManager, KdumpManagerConfig

# Create local runner and file operator
runner = LocalCommandRunner(LocalRunnerConfig())
file_operator = LocalFileOperator()

# Boot manager instance (injected for crashkernel= delegation)
boot_manager = BootManager(runner, file_operator, BootManagerConfig(elevated=True))

# Inject into kdump manager
config = KdumpManagerConfig(elevated=True, dry_run=False, boot_manager=boot_manager)
kdump_manager = KdumpManager(runner, file_operator, config)

# Configure the dump target to /var/crash and rebuild the kdump initramfs
path_result = kdump_manager.set_config(key="path", value="/var/crash")
print(f"set_config status: {path_result.status}")

rebuild_result = kdump_manager.rebuild_initramfs()
print(f"rebuild status: {rebuild_result.status}")
```

### SSH Execution with Reliability Tracking

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.fileoperators import SSHFileOperator
from automojo.sysman.linux.redhat.bootmanagement import BootManager, BootManagerConfig
from automojo.sysman.linux.redhat.kdumpmanagement import KdumpManager, KdumpManagerConfig

# Create SSH runner for remote system
ssh_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0
)
runner = SSHCommandRunner(ssh_config)
file_operator = SSHFileOperator(ssh_config)

# Boot manager uses the same transport
boot_manager = BootManager(runner, file_operator, BootManagerConfig(elevated=True))

# Inject into kdump manager (same code as local!)
config = KdumpManagerConfig(elevated=True, boot_manager=boot_manager)
kdump_manager = KdumpManager(runner, file_operator, config)

# Enable kdump on a remote fleet member - KdumpManager doesn't know it's SSH
enable_result = kdump_manager.lifecycle_action(action="enable")
print(f"enable status: {enable_result.status}")

# Check SSH reliability metrics
metrics = runner.get_metrics()
total = metrics.success_count + metrics.failure_count
rate = (metrics.success_count / total) if total > 0 else 0.0
print(f"Success rate: {rate:.2%}")
print(f"Average duration: {metrics.avg_duration_ms:.2f}ms")
print(f"Connection retries: {metrics.retry_count}")
if metrics.last_error is not None:
    print(f"Last error: {metrics.last_error_type.value} - {metrics.last_error}")
```

### SSH Execution with Aspects

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.fileoperators import SSHFileOperator
from automojo.sysman.linux.redhat.aspects import PersistenceAspect, RetryAspect, TimeoutAspect
from automojo.sysman.linux.redhat.aspects import PersistenceAspectConfig, RetryAspectConfig, TimeoutAspectConfig
from automojo.sysman.linux.redhat.storage import SQLiteStorage
from automojo.sysman.linux.redhat.bootmanagement import BootManager, BootManagerConfig
from automojo.sysman.linux.redhat.kdumpmanagement import KdumpManager, KdumpManagerConfig

# Create SSH transport
ssh_config = SSHRunnerConfig(host="remote-server", username="admin")
runner = SSHCommandRunner(ssh_config)
file_operator = SSHFileOperator(ssh_config)

# Add persistence aspect for audit logging
storage = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

# Add retry aspect for transient failures
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[1, 255],
    retryable_error_patterns=["Connection reset", "Temporary failure"]
))
runner.add_aspect(retry)

# Add timeout aspect for global policy
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=300.0))
runner.add_aspect(timeout)

# Boot manager and kdump manager share the same aspected transport
boot_manager = BootManager(runner, file_operator, BootManagerConfig(elevated=True))
kdump_manager = KdumpManager(
    runner,
    file_operator,
    KdumpManagerConfig(elevated=True, boot_manager=boot_manager),
)

# Bundle: reserve memory, point dump at /var/crash, enable the service.
# All three operations benefit from persistence, retry, and timeout aspects.
set_ck_result = kdump_manager.set_crashkernel(value="1G-4G:192M,4G-64G:256M,64G-:512M")
print(f"set_crashkernel status: {set_ck_result.status} note: {set_ck_result.note}")

set_path_result = kdump_manager.set_config(key="path", value="/var/crash")
print(f"set_config status: {set_path_result.status}")

enable_result = kdump_manager.lifecycle_action(action="enable")
print(f"enable status: {enable_result.status}")
```

### Dry-Run Mode Across Transports

```python
# Dry-run works identically for local and SSH
ssh_config = SSHRunnerConfig(host="remote-server", username="admin", dry_run=True)
runner = SSHCommandRunner(ssh_config)
file_operator = SSHFileOperator(ssh_config)
boot_manager = BootManager(runner, file_operator, BootManagerConfig(elevated=True, dry_run=True))
kdump_manager = KdumpManager(
    runner,
    file_operator,
    KdumpManagerConfig(elevated=True, dry_run=True, boot_manager=boot_manager),
)

# No SSH connection is made, no files are written, no delegate mutations occur
result = kdump_manager.lifecycle_action(action="enable")
assert result.status == KdumpActionStatus.DRY_RUN
```
