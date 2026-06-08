# Design

## Overview

The boot management feature is implemented as a single module,
`ccai.sysman.linux.redhat.bootmanagement`, that exposes a `BootManager` class.
The coordinator instantiates `BootManager` and calls its methods to perform discrete
boot-configuration actions: querying and setting the default systemd target, listing
kernels known to GRUB2, querying and setting the default kernel, adding or removing
kernel command-line arguments, regenerating the GRUB2 configuration, removing kernel
entries, editing `/etc/default/grub` keys, and detecting BIOS vs UEFI firmware.

All process execution is delegated to a command runner implementing the
`ICommandRunner` interface. All filesystem reads and writes (e.g. `/proc/cmdline`,
`/etc/default/grub`, `/sys/firmware/efi` probe) are delegated to a file operator
implementing the `IFileOperator` interface. The `BootManager` never calls
`subprocess`, `os.path`, or `open()` directly; it only constructs argument lists and
file-operation requests and passes them to the injected collaborators. This keeps
business logic separate from execution and makes the feature fully testable via mock
collaborators without root access or a live system.

The feature works identically whether commands execute locally (via
`LocalCommandRunner` + `LocalFileOperator`) or remotely over SSH (via
`SSHCommandRunner` + `SSHFileOperator`). When using SSH, the coordinator can track
reliability metrics and apply execution aspects (persistence, retry, timeout) without
any changes to the `BootManager` code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md) for
details on local/SSH execution, reliability tracking, and aspect configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner + file operator (Local or SSH)       │
│  - Configures aspects (optional)                            │
│  - Injects runner and file operator into BootManager        │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    BootManager                              │
│  ccai.sysman.linux.redhat.bootmanagement                │
│                                                             │
│  get_default_target()    set_default_target()               │
│  list_kernels()          get_default_kernel()               │
│  set_default_kernel()    add_kernel_args()                  │
│  remove_kernel_args()    get_runtime_cmdline()              │
│  get_kernel_cmdline()    regenerate_grub_config()           │
│  remove_kernel_entry()   get_grub_setting()                 │
│  set_grub_setting()      delete_grub_setting()              │
│  get_boot_firmware()                                        │
└──────────────┬──────────────────────────┬───────────────────┘
               │ delegates commands to    │ delegates file I/O to
               ▼                          ▼
┌──────────────────────────┐    ┌──────────────────────────┐
│      ICommandRunner      │    │      IFileOperator       │
│ run(args, elevated, ...) │    │ read / write / exists    │
│         → RunResult      │    │         → bytes / bool   │
└──────────────┬───────────┘    └──────────────┬───────────┘
               │                               │
   ┌───────────┴───────────┐       ┌───────────┴───────────┐
   ▼                       ▼       ▼                       ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│ LocalCommand     │  │ SSHCommand       │  │ Local / SSH      │
│ Runner           │  │ Runner           │  │ FileOperator     │
│ - subprocess.run │  │ - SSH connection │  │ - read/write via │
│ - sudo elevation │  │ - reliability    │  │   local or SFTP  │
└──────────────────┘  └──────────────────┘  └──────────────────┘
           │                    │
           └──────────┬─────────┘
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

### `BootManager`

**Location:** `source/packages/ccai/sysman/linux/redhat/bootmanagement.py`

The primary public class.  Constructed with an `ICommandRunner`, an `IFileOperator`,
and a `BootManagerConfig`.

| Method | Description |
|---|---|
| `get_default_target()` → `BootTarget` | Return the current default systemd target unit |
| `set_default_target(target)` → `BootActionResult` | Set the default systemd target unit |
| `list_kernels()` → `list[KernelEntry]` | Return all kernel entries known to the bootloader |
| `get_default_kernel()` → `KernelEntry` | Return the kernel currently marked as default |
| `set_default_kernel(path=None, index=None)` → `BootActionResult` | Set the default kernel by path or index |
| `add_kernel_args(args, kernel_path=None)` → `BootActionResult` | Add kernel cmdline args to ALL kernels or a specific one |
| `remove_kernel_args(args, kernel_path=None)` → `BootActionResult` | Remove kernel cmdline args from ALL kernels or a specific one |
| `get_runtime_cmdline()` → `KernelCmdLine` | Read `/proc/cmdline` and return parsed cmdline |
| `get_kernel_cmdline(kernel_path)` → `KernelCmdLine` | Return cmdline for a specific kernel via `grubby --info` |
| `regenerate_grub_config(output_path=None)` → `BootActionResult` | Run `grub2-mkconfig` against the firmware-appropriate path |
| `remove_kernel_entry(kernel_path)` → `BootActionResult` | Remove a kernel entry from the bootloader |
| `get_grub_setting(key)` → `str \| None` | Read a key from `/etc/default/grub`, preserving quoting in the source |
| `set_grub_setting(key, value)` → `BootActionResult` | Set or replace a key in `/etc/default/grub`, preserving quoting style |
| `delete_grub_setting(key)` → `BootActionResult` | Remove a key line from `/etc/default/grub` |
| `get_boot_firmware()` → `BootFirmware` | Return `BootFirmware.UEFI` or `BootFirmware.BIOS` |

### `BootManagerConfig`

**Location:** `source/packages/ccai/sysman/linux/redhat/bootmanagement.py`

A dataclass holding runtime configuration for `BootManager`.

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no mutating commands or file writes are executed |
| `elevated` | `bool` | `False` | When `True`, mutating commands and file writes are requested with elevation |

---

## Data Models

### `BootActionStatus`

```python
class BootActionStatus(Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"   # already in desired state, no action needed
    DRY_RUN  = "dry_run"   # would have acted, but dry_run=True
```

### `BootActionResult`

Returned by all mutating operations (set target, set default kernel, add/remove args,
regenerate config, remove kernel entry, set/delete grub setting).

```python
@dataclass
class BootActionResult:
    status: BootActionStatus
    action: str                    # short identifier of the action performed
    exit_code: int | None          # raw exit code from command runner
    stdout: str | None
    stderr: str | None
    command: list[str] | None      # the command that was (or would have been) run
    target_path: str | None        # file path involved, if any (e.g. grub.cfg)
    note: str | None               # e.g. "grub2-mkconfig required to take effect"
```

### `BootTarget`

Returned by `get_default_target()`.

```python
@dataclass
class BootTarget:
    unit: str                      # e.g. "multi-user.target", "graphical.target"
```

### `KernelEntry`

Returned as elements of `list_kernels()` and by `get_default_kernel()`.

```python
@dataclass
class KernelEntry:
    index: int
    kernel: str                    # path to the kernel image, e.g. /boot/vmlinuz-...
    args: str                      # raw args string as reported by grubby
    root: str                      # root device string
    initrd: str                    # path to the initramfs image
    title: str                     # menu title
```

### `KernelCmdLine`

Returned by `get_runtime_cmdline()` and `get_kernel_cmdline()`.

```python
@dataclass
class KernelCmdLine:
    raw: str                       # raw, unparsed cmdline string
    parsed: dict[str, str | None]  # parsed name → value (None for bare flags)
```

### `BootFirmware`

Returned by `get_boot_firmware()`.

```python
class BootFirmware(Enum):
    BIOS = "bios"
    UEFI = "uefi"
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Idempotent `set_default_target`

*For any* target unit name, calling `set_default_target()` when the supplied target is
already the current default should return a `BootActionResult` with status `SKIPPED`
and must not invoke the command runner.

**Validates: Requirements 2.2**

### Property 2: Idempotent `set_default_kernel`

*For any* kernel path or index, calling `set_default_kernel()` when the supplied
kernel is already the default should return a `BootActionResult` with status `SKIPPED`
and must not invoke the command runner.

**Validates: Requirements 4.5**

### Property 3: Add-Args No-Op When Present

*For any* set of kernel arguments, calling `add_kernel_args()` when every targeted
kernel already contains the supplied arguments with matching values should return a
`BootActionResult` with status `SKIPPED` and must not invoke the command runner.

**Validates: Requirements 5.3**

### Property 4: Remove-Args No-Op When Absent

*For any* set of kernel arguments, calling `remove_kernel_args()` when no targeted
kernel contains any of the supplied arguments should return a `BootActionResult` with
status `SKIPPED` and must not invoke the command runner.

**Validates: Requirements 6.3**

### Property 5: Refusal to Remove Running or Default Kernel

*For any* kernel path that matches either the currently running kernel (inferred from
`/proc/cmdline` BOOT_IMAGE) or the current default kernel, calling
`remove_kernel_entry()` should return a `BootActionResult` with status `FAILED` and an
explanatory error message, and must not invoke the removal command.

**Validates: Requirements 9.2, 9.3**

### Property 6: Firmware-Driven `grub2-mkconfig` Path Selection

*For any* invocation of `regenerate_grub_config()` without an explicit output path,
the selected output path must be `/boot/grub2/grub.cfg` when
`get_boot_firmware()` returns `BIOS` and `/boot/efi/EFI/redhat/grub.cfg` when it
returns `UEFI`. When an explicit `output_path` is supplied, that path must be used
regardless of firmware mode.

**Validates: Requirements 8.1, 8.2, 8.3**

### Property 7: Dry-Run Safety

*For any* mutating operation (`set_default_target`, `set_default_kernel`,
`add_kernel_args`, `remove_kernel_args`, `regenerate_grub_config`,
`remove_kernel_entry`, `set_grub_setting`, `delete_grub_setting`) with `dry_run=True`,
the command runner must never be invoked with a mutating command, the file operator
must never be invoked to write, and the returned `BootActionResult.status` must be
`DRY_RUN`.

**Validates: Requirements 2.6, 4.7, 5.6, 6.5, 8.6, 9.6, 10.6**

### Property 8: Elevation Consistency

*For any* mutating operation with `elevated=True` in the config, the command runner
must be invoked with `elevated=True` and the file operator's write must be requested
with elevation; for any read-only query, elevation must never be requested.

**Validates: Requirements 12.1, 12.2, 12.3**

### Property 9: Status Mapping Correctness

*For any* operation result from the command runner, the `BootActionResult.status` must
correctly reflect the runner's exit code: `0` maps to `SUCCESS`, non-zero maps to
`FAILED` (unless the operation was skipped or in dry-run mode), with the exit code and
captured stderr preserved on `FAILED` results.

**Validates: Requirements 1.3, 2.4, 2.5, 3.4, 7.4, 8.4, 8.5, 11.4**

### Property 10: Idempotent `set_grub_setting` with Quoting Preservation

*For any* key and value, calling `set_grub_setting()` when the current file content
already assigns that key to the requested value must return a `BootActionResult` with
status `SKIPPED` and must not request a file write. When a write is required, the
existing quoting style of the replaced line (double-quoted, single-quoted, or
unquoted) must be preserved in the rewritten line.

**Validates: Requirements 10.2, 10.4**

### Property 11: Firmware Probe Classification

*For any* invocation of `get_boot_firmware()`, when the file operator reports that
`/sys/firmware/efi` exists the result must be `BootFirmware.UEFI`; when the probe
reports not-found the result must be `BootFirmware.BIOS`; for any other probe error
the method must raise `BootQueryError`.

**Validates: Requirements 11.1, 11.2, 11.3, 11.4**

### Property 12: Command Construction Correctness

*For any* boot operation, the constructed command arguments passed to the runner must
match the expected command format specified in the Command Mappings table below.

**Validates: Requirements 1.1, 2.1, 3.1, 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 6.1, 6.2, 7.1, 7.2, 8.1, 8.2, 9.1, 10.1, 10.2, 10.3, 11.1**

---

## Interfaces

### `ICommandRunner` (from `codecraftai-interfaces`)

`BootManager` depends on this interface, injected at construction time.

```python
class ICommandRunner(Protocol):
    def run(
        self,
        args: list[str],
        elevated: bool = False,
        capture_output: bool = True,
    ) -> RunResult: ...
```

### `IFileOperator` (from `codecraftai-interfaces`)

`BootManager` depends on this interface, injected at construction time. Used for
reads of `/proc/cmdline`, `/etc/default/grub`, and the `/sys/firmware/efi` existence
probe, and for writes against `/etc/default/grub`.

```python
class IFileOperator(Protocol):
    def exists(self, path: str) -> bool: ...
    def read_text(self, path: str) -> str: ...
    def write_text(
        self,
        path: str,
        content: str,
        mode: int = 0o644,
        owner: str | None = None,
        group: str | None = None,
        elevated: bool = False,
    ) -> None: ...
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

When `BootManagerConfig.elevated` is `True`, the `BootManager` passes
`elevated=True` to `ICommandRunner.run()` and to `IFileOperator.write_text()` for
mutating operations. The runner is responsible for prepending `sudo` or using
whatever privilege-escalation mechanism is appropriate for the environment.
`BootManager` has no knowledge of how elevation is implemented. Read-only queries
never request elevation.

---

## Command Mappings

| Operation | Command / File Action |
|---|---|
| Get default target | `systemctl get-default` |
| Set default target | `systemctl set-default <target>` |
| List all kernel entries | `grubby --info=ALL` |
| Query a single kernel entry | `grubby --info=<kernel_path>` |
| Get default kernel path | `grubby --default-kernel` |
| Get default kernel index | `grubby --default-index` |
| Set default kernel by path | `grubby --set-default=<kernel_path>` |
| Set default kernel by index | `grubby --set-default-index=<n>` |
| Add args to all kernels | `grubby --update-kernel=ALL --args="<args>"` |
| Add args to one kernel | `grubby --update-kernel=<kernel_path> --args="<args>"` |
| Remove args from all kernels | `grubby --update-kernel=ALL --remove-args="<args>"` |
| Remove args from one kernel | `grubby --update-kernel=<kernel_path> --remove-args="<args>"` |
| Remove kernel entry | `grubby --remove-kernel=<kernel_path>` |
| Regenerate GRUB2 config | `grub2-mkconfig -o <output_path>` |
| Read runtime cmdline | File read: `/proc/cmdline` |
| Read `/etc/default/grub` | File read: `/etc/default/grub` |
| Write `/etc/default/grub` | File write: `/etc/default/grub` (mode `0644`, owner `root`, group `root`, elevated) |
| Detect firmware | File probe: `/sys/firmware/efi` |

### `grub2-mkconfig` Output Path by Firmware Mode

When `regenerate_grub_config()` is called without an explicit `output_path`, the
firmware detected by `get_boot_firmware()` dictates the target file:

| Firmware | Output Path |
|---|---|
| `BootFirmware.BIOS` | `/boot/grub2/grub.cfg` |
| `BootFirmware.UEFI` | `/boot/efi/EFI/redhat/grub.cfg` |

When the caller supplies an explicit `output_path`, that path is used verbatim and the
firmware mode is not consulted.

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `BootQueryError` | `systemctl get-default`, `grubby --info=*`, `grubby --default-*`, `/proc/cmdline` read, or `/sys/firmware/efi` probe fails with an unexpected error |

All other failures are surfaced as a `BootActionResult` with `status=FAILED` so the
coordinator can decide how to respond without catching exceptions for normal
operational failures.

### Refusal to Remove Running or Default Kernel

`remove_kernel_entry()` does not raise when asked to remove a kernel that is
dangerous to remove. Instead it returns `BootActionResult(status=FAILED, ...)` with a
descriptive `stderr`/`note` so the coordinator can surface the refusal without an
exception handler. The running kernel is identified by extracting the `BOOT_IMAGE`
value from `/proc/cmdline`; the default kernel is identified by calling
`get_default_kernel()`. Neither fallback involves elevation.

### Idempotency Guards

Before executing mutating commands and file writes, `BootManager` reads current
state (via `get_default_target`, `get_default_kernel`, `list_kernels`,
`get_grub_setting`) to determine whether action is needed. When the system is already
in the desired state the method returns `SKIPPED` without invoking the command runner
or the file operator's write path. This makes all mutating operations safe to call
multiple times.

---

## Module Layout

```
source/packages/ccai/sysman/linux/redhat/
├── __init__.py
└── bootmanagement.py             ← BootManager, config, models, exceptions

source/testroots/testbase/
└── ccai/tests/sysman/linux/redhat/
    └── test_bootmanagement.py    ← unit tests using MockCommandRunner + MockFileOperator
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` and a `MockFileOperator` injected into
  `BootManager` at construction time.
- Tests verify the *arguments* passed to the runner and the *paths and content*
  passed to the file operator, not real system state.
- Each public method has at minimum: a success path test, a skip/idempotency test
  where applicable, a failure path test, and a dry-run test.
- `remove_kernel_entry()` has additional tests for the running-kernel and
  default-kernel refusal paths.
- `regenerate_grub_config()` has tests for BIOS, UEFI, and explicit-path selection.
- `get_boot_firmware()` has tests for present, absent, and error probe outcomes.
- `set_grub_setting()` has tests verifying preservation of double-quoted,
  single-quoted, and unquoted source lines.
- No test requires root access, a live bootloader, or a real `/sys/firmware/efi`.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in
Python. Each correctness property defined above will be implemented as a
property-based test that:

- Generates random but valid inputs (target names, kernel paths, arg strings,
  cmdline strings, grub setting key/value pairs, firmware-probe outcomes)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific correctness
property they validate, using the format:
`# Feature: boot-management, Property N: <property description>`

**Property Test Coverage:**

- Property 1-4: Idempotency and no-op tests generate random inputs and verify skip behavior
- Property 5: Removal refusal tests generate running/default kernel paths
- Property 6: `grub2-mkconfig` path selection tests across firmware modes
- Property 7: Dry-run safety tests verify no runner or file-operator write invocation
- Property 8: Elevation tests verify correct elevation flag propagation
- Property 9: Status mapping tests verify correct result interpretation
- Property 10: Grub setting idempotency and quoting preservation
- Property 11: Firmware probe classification
- Property 12: Command construction correctness across all operations

Both unit tests and property-based tests are complementary: unit tests verify specific
examples and edge cases, while property tests verify universal correctness across many
inputs.

---

## Usage Examples

### Local Execution (Add IOMMU Kernel Argument and Regenerate Config)

```python
from ccai.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from ccai.sysman.linux.redhat.fileops import LocalFileOperator, LocalFileOperatorConfig
from ccai.sysman.linux.redhat.bootmanagement import BootManager, BootManagerConfig

# Create local runner and file operator
runner = LocalCommandRunner(LocalRunnerConfig())
fileops = LocalFileOperator(LocalFileOperatorConfig())

# Inject into boot manager
config = BootManagerConfig(elevated=True, dry_run=False)
boot_manager = BootManager(runner, fileops, config)

# Add IOMMU argument to all kernels
add_result = boot_manager.add_kernel_args(args="intel_iommu=on iommu=pt")
print(f"Add args: {add_result.status}")

# Regenerate GRUB config for the currently detected firmware mode
regen_result = boot_manager.regenerate_grub_config()
print(f"Regenerate: {regen_result.status} at {regen_result.target_path}")
```

### SSH Execution with Reliability Tracking (Set Default Target to multi-user)

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.fileops import SSHFileOperator, SSHFileOperatorConfig
from ccai.sysman.linux.redhat.bootmanagement import BootManager, BootManagerConfig

# Create SSH runner and file operator for remote system
ssh_runner_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0,
)
runner = SSHCommandRunner(ssh_runner_config)

ssh_fileops_config = SSHFileOperatorConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
)
fileops = SSHFileOperator(ssh_fileops_config)

# Inject into boot manager (same code as local)
config = BootManagerConfig(elevated=True)
boot_manager = BootManager(runner, fileops, config)

# Set default target to multi-user
result = boot_manager.set_default_target(target="multi-user.target")
print(f"Set default target: {result.status}")

# Check SSH reliability metrics
metrics = runner.get_metrics()
print(f"Success rate: {metrics.success_count / (metrics.success_count + metrics.failure_count):.2%}")
print(f"Average duration: {metrics.avg_duration_ms:.2f}ms")
```

### SSH Execution with Aspects (Persistence + Retry + Timeout)

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.fileops import SSHFileOperator, SSHFileOperatorConfig
from ccai.sysman.linux.redhat.aspects import PersistenceAspect, RetryAspect, TimeoutAspect
from ccai.sysman.linux.redhat.aspects import PersistenceAspectConfig, RetryAspectConfig, TimeoutAspectConfig
from ccai.sysman.linux.redhat.storage import SQLiteStorage
from ccai.sysman.linux.redhat.bootmanagement import BootManager, BootManagerConfig

# Create SSH runner + file operator
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))
fileops = SSHFileOperator(SSHFileOperatorConfig(host="remote-server", username="admin"))

# Add persistence aspect for audit logging
storage = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

# Add retry aspect for transient failures (e.g., network glitches)
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[1, 255],
    retryable_error_patterns=["Connection reset", "Temporary failure"],
))
runner.add_aspect(retry)

# Add timeout aspect for global policy
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=300.0))
runner.add_aspect(timeout)

# Inject into boot manager
boot_manager = BootManager(runner, fileops, BootManagerConfig(elevated=True))

# All boot operations now have:
# - Audit logging to SQLite
# - Automatic retry on transient failures
# - 5-minute timeout enforcement
result = boot_manager.set_grub_setting(key="GRUB_TIMEOUT", value="5")
print(f"Set GRUB_TIMEOUT: {result.status} ({result.note})")
```

### Dry-Run Mode Across Transports

```python
# Dry-run works identically for local and SSH
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))
fileops = SSHFileOperator(SSHFileOperatorConfig(host="remote-server", username="admin"))
boot_manager = BootManager(runner, fileops, BootManagerConfig(elevated=True, dry_run=True))

# No mutating command is run, no file is written
result = boot_manager.set_default_kernel(path="/boot/vmlinuz-5.14.0-362.el9.x86_64")
assert result.status == BootActionStatus.DRY_RUN
```
