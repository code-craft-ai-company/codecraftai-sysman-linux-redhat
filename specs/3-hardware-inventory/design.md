# Design

## Overview

The hardware inventory feature is implemented as a single module,
`ccai.sysman.linux.redhat.hardwareinventory`, that exposes a
`HardwareInventoryManager` class. The coordinator instantiates
`HardwareInventoryManager` and calls its methods to query discrete hardware and
platform identity capabilities, or to produce a combined hardware snapshot.

All system interaction is delegated to a command runner implementing the
`ICommandRunner` interface and a file operator implementing the `IFileOperator`
interface. The `HardwareInventoryManager` never directly calls `subprocess`, the
shell, or `open()`; it only constructs command argument lists and file paths and
passes them to the injected abstractions. This keeps business logic separate from
execution and makes the feature fully testable via mock implementations without
root access, real DMI hardware, or a live system.

This feature is strictly **read-only**. It does not mutate system state and
therefore has **no dry-run mode distinction from its normal behavior** — every
call is inherently safe to repeat and no commands issued by this module change
the underlying host. As a result, no `dry_run` field exists on
`HardwareInventoryConfig` and no `DRY_RUN` status appears in any result type.

The feature works identically whether commands execute locally (via
`LocalCommandRunner`) or remotely over SSH (via `SSHCommandRunner`). When using
SSH, the coordinator can track reliability metrics and apply execution aspects
(persistence, retry, timeout) without any changes to the
`HardwareInventoryManager` code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md) for
details on local/SSH execution, reliability tracking, and aspect configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner (Local or SSH)                       │
│  - Instantiates file operator (Local or SSH)                │
│  - Configures aspects (optional)                            │
│  - Injects runner + file operator into HardwareInventory    │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            HardwareInventoryManager                         │
│  ccai.sysman.linux.redhat.hardwareinventory             │
│                                                             │
│  get_dmi_identity()       get_cpu_info()                    │
│  get_memory_info()        list_block_devices()              │
│  list_pci_devices()       list_usb_devices()                │
│  list_net_hardware()      get_os_release()                  │
│  get_firmware_info()      collect_snapshot()                │
└────────────────────────┬────────────────────────────────────┘
                         │ delegates to
          ┌──────────────┴──────────────┐
          ▼                             ▼
┌──────────────────────┐      ┌──────────────────────┐
│   ICommandRunner     │      │    IFileOperator     │
│ run(args, elevated,  │      │ read_text(path)      │
│     timeout)         │      │ exists(path)         │
│     → RunResult      │      │ list_dir(path)       │
└──────────┬───────────┘      └──────────┬───────────┘
           │                             │
           ▼                             ▼
┌─────────────────────────────────────────────────────────────┐
│        Local / SSH transport implementations                │
│  - LocalCommandRunner / LocalFileOperator                   │
│  - SSHCommandRunner   / SSHFileOperator                     │
│  - sudo for elevation (dmidecode only)                      │
│  - reliability metrics (SSH)                                │
└────────────────────────┬────────────────────────────────────┘
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

### `HardwareInventoryManager`

**Location:** `source/packages/ccai/sysman/linux/redhat/hardwareinventory.py`

The primary public class. Constructed with an `ICommandRunner`, an
`IFileOperator`, and a `HardwareInventoryConfig`.

| Method | Description |
|---|---|
| `get_dmi_identity()` → `DmiIdentity` | Query DMI/SMBIOS identity via `dmidecode`; fall back to `/sys/class/dmi/id/` when unavailable |
| `get_cpu_info()` → `CpuInfo` | Query CPU topology and features via `lscpu -J`; fall back to `/proc/cpuinfo` |
| `get_memory_info()` → `MemoryInfo` | Query aggregate memory via `lsmem --output-all -J`; fall back to `/proc/meminfo`; populate DIMM list via `dmidecode -t memory` if available |
| `list_block_devices()` → `list[BlockDeviceInventory]` | Enumerate block devices via `lsblk -J -b -o ...` |
| `list_pci_devices()` → `list[PciDevice]` | Enumerate PCI devices via `lspci -vmm -nnk`; return empty list when absent |
| `list_usb_devices()` → `list[UsbDevice]` | Enumerate USB devices via `lsusb`; return empty list when absent |
| `list_net_hardware()` → `list[NetInterfaceHardware]` | Enumerate link-layer NIC info via `ip -j -d link show` merged with `/sys/class/net/<iface>/...` |
| `get_os_release()` → `OsRelease` | Kernel (`uname -srvmo`), `/etc/os-release`, and machine identity (`hostnamectl --json=short` or `/etc/machine-id` + `/etc/hostname`) |
| `get_firmware_info()` → `FirmwareInfo` | BIOS/UEFI vendor, version, release date from `/sys/class/dmi/id/`; fall back to `dmidecode -s`; detect UEFI via `/sys/firmware/efi` |
| `collect_snapshot()` → `HardwareSnapshot` | Aggregate all sub-queries into a single `HardwareSnapshot`; permission errors demote sub-fields to `None` with a `PartialInventoryWarning` |

### `HardwareInventoryConfig`

**Location:** `source/packages/ccai/sysman/linux/redhat/hardwareinventory.py`

A dataclass holding runtime configuration for `HardwareInventoryManager`.

| Field | Type | Default | Description |
|---|---|---|---|
| `elevated` | `bool` | `False` | When `True`, DMI/DIMM queries (`dmidecode`) are issued with elevation; all other commands run unelevated regardless of this flag |

Note: there is intentionally **no** `dry_run` field — every capability in this
feature is read-only.

---

## Data Models

### `DmiIdentity`

Returned by `get_dmi_identity()`.

```python
@dataclass
class DmiIdentity:
    system_manufacturer: str | None
    system_product: str | None
    system_version: str | None
    system_serial: str | None
    system_uuid: str | None
    baseboard_manufacturer: str | None
    baseboard_product: str | None
    baseboard_serial: str | None
    chassis_type: str | None
    bios_vendor: str | None
    bios_version: str | None
    bios_release_date: str | None
    source: str                 # "dmidecode" | "sysfs" | "partial"
```

### `CpuInfo`

Returned by `get_cpu_info()`.

```python
@dataclass
class CpuInfo:
    arch: str | None
    byte_order: str | None
    cpus: int | None
    threads_per_core: int | None
    cores_per_socket: int | None
    sockets: int | None
    numa_nodes: int | None
    vendor: str | None
    model_name: str | None
    model: str | None
    family: str | None
    stepping: str | None
    mhz: float | None
    virtualization: str | None
    hypervisor: str | None
    flags: list[str]
    l1d_cache: str | None
    l1i_cache: str | None
    l2_cache: str | None
    l3_cache: str | None
    source: str                 # "lscpu" | "proc_cpuinfo"
```

### `MemoryInfo`

Returned by `get_memory_info()`.

```python
@dataclass
class MemoryInfo:
    total_online_bytes: int | None
    total_offline_bytes: int | None
    memory_block_size: int | None
    available_bytes: int | None
    free_bytes: int | None
    swap_total_bytes: int | None
    swap_free_bytes: int | None
    dimms: list[Dimm]
    source: str                 # "lsmem" | "proc_meminfo"
```

### `Dimm`

Element of `MemoryInfo.dimms`.

```python
@dataclass
class Dimm:
    locator: str | None
    bank_locator: str | None
    size_bytes: int | None
    type: str | None
    speed_mts: int | None
    manufacturer: str | None
    part_number: str | None
    serial: str | None
```

### `BlockDeviceInventory`

Element of `list_block_devices()`.

```python
@dataclass
class BlockDeviceInventory:
    name: str
    kname: str | None
    type: str | None
    size_bytes: int | None
    rota: bool | None
    model: str | None
    serial: str | None
    vendor: str | None
    wwn: str | None
    tran: str | None
    fstype: str | None
    uuid: str | None
    label: str | None
    mountpoint: str | None
    children: list["BlockDeviceInventory"]
```

### `PciDevice`

Element of `list_pci_devices()`.

```python
@dataclass
class PciDevice:
    slot: str
    class_: str | None
    vendor: str | None
    device: str | None
    vendor_id: str | None
    device_id: str | None
    subsystem_vendor: str | None
    subsystem_device: str | None
    driver: str | None
    modules_available: list[str]
```

### `UsbDevice`

Element of `list_usb_devices()`.

```python
@dataclass
class UsbDevice:
    bus: str
    device: str
    vendor_id: str | None
    product_id: str | None
    description: str | None
```

### `NetInterfaceHardware`

Element of `list_net_hardware()`.

```python
@dataclass
class NetInterfaceHardware:
    name: str
    mac: str | None
    link_state: str | None
    mtu: int | None
    driver: str | None
    speed_mbps: int | None
    duplex: str | None
    pci_slot: str | None
```

### `OsRelease`

Returned by `get_os_release()`.

```python
@dataclass
class OsRelease:
    kernel_name: str | None
    kernel_release: str | None
    kernel_version: str | None
    machine: str | None
    os: str | None
    id: str | None
    id_like: str | None
    version: str | None
    version_id: str | None
    pretty_name: str | None
    cpe_name: str | None
    machine_id: str | None
    hostname: str | None
```

### `FirmwareInfo`

Returned by `get_firmware_info()`.

```python
@dataclass
class FirmwareInfo:
    vendor: str | None
    version: str | None
    release_date: str | None
    mode: str | None            # "BIOS" | "UEFI"
    available: bool
```

### `HardwareSnapshot`

Returned by `collect_snapshot()`.

```python
@dataclass
class HardwareSnapshot:
    collected_at: str           # ISO-8601 UTC timestamp
    dmi: DmiIdentity | None
    cpu: CpuInfo | None
    memory: MemoryInfo | None
    block_devices: list[BlockDeviceInventory] | None
    pci: list[PciDevice] | None
    usb: list[UsbDevice] | None
    net_hardware: list[NetInterfaceHardware] | None
    os_release: OsRelease | None
    firmware: FirmwareInfo | None
    warnings: list["PartialInventoryWarning"]
```

### `PartialInventoryWarning`

Element of `HardwareSnapshot.warnings`.

```python
@dataclass
class PartialInventoryWarning:
    field: str                  # e.g. "dmi", "memory.dimms"
    reason: str                 # e.g. "permission_denied", "tool_unavailable"
    detail: str | None          # optional human-readable detail
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Snapshot Aggregation Completeness

*For any* successful `collect_snapshot()` call on a fully privileged system, every
sub-query defined in Requirements 1–9 is invoked exactly once and its result is
placed on the corresponding `HardwareSnapshot` field.

**Validates: Requirements 10.1, 10.2**

### Property 2: Partial Snapshot Tolerance

*For any* `collect_snapshot()` call during which a sub-query raises
`HardwareInventoryPermissionError`, the returned `HardwareSnapshot` leaves the
corresponding field as `None`, appends a `PartialInventoryWarning`, and never
aborts the remaining sub-queries.

**Validates: Requirements 10.3**

### Property 3: DMI Fallback to sysfs

*For any* `get_dmi_identity()` call where `dmidecode` is not installed, the
command runner is invoked at most once (the failing probe), and the result is
populated from `/sys/class/dmi/id/` via the file operator with `source="sysfs"`.

**Validates: Requirements 1.3**

### Property 4: CPU Fallback to /proc/cpuinfo

*For any* `get_cpu_info()` call where `lscpu` is absent, the returned `CpuInfo`
record is populated from `/proc/cpuinfo` via the file operator with
`source="proc_cpuinfo"`, and missing fields are left as `None` without raising.

**Validates: Requirements 2.3**

### Property 5: Memory Fallback to /proc/meminfo

*For any* `get_memory_info()` call where `lsmem` is absent, the aggregate memory
fields are populated from `/proc/meminfo` and `dimms` is an empty list when
`dmidecode` is also absent.

**Validates: Requirements 3.1, 3.4**

### Property 6: Empty List Stability

*For any* call to `list_pci_devices()` or `list_usb_devices()` where the
underlying command is not installed, the method returns an empty `list`,
does not raise, and does not leak a `None` to the caller.

**Validates: Requirements 5.3, 6.3**

### Property 7: Elevation Only for DMI

*For any* method invocation, the `elevated` flag is passed to
`ICommandRunner.run()` as `True` only when the command being run is `dmidecode`
(any form) AND `HardwareInventoryConfig.elevated` is `True`. All other commands
invoke the runner with `elevated=False`.

**Validates: Requirements 11.1, 11.2, 11.3**

### Property 8: Query Error Status Mapping

*For any* sub-query other than those that fall back on unavailability, a non-zero
exit code from the runner raises `HardwareInventoryQueryError` carrying the
command, exit code, and stderr. Permission-denied conditions on `dmidecode`
raise `HardwareInventoryPermissionError` instead.

**Validates: Requirements 1.4, 2.4, 3.5, 4.3, 5.4, 6.4, 7.5**

### Property 9: Network Hardware Excludes IP Addressing

*For any* returned `NetInterfaceHardware` record from `list_net_hardware()`, the
record contains only link-layer fields (`name`, `mac`, `link_state`, `mtu`,
`driver`, `speed_mbps`, `duplex`, `pci_slot`) and never includes IP addresses,
routes, or DNS data.

**Validates: Requirements 7.3, 7.4**

### Property 10: JSON Parse Stability

*For any* command whose documented invocation produces JSON (`lscpu -J`,
`lsmem -J`, `lsblk -J`, `ip -j`, `hostnamectl --json=short`), malformed JSON on
stdout raises `HardwareInventoryQueryError` rather than surfacing a raw
`json.JSONDecodeError` to the caller.

**Validates: Requirements 2.4, 3.1, 4.3, 7.5, 8.1**

### Property 11: Firmware Mode Detection

*For any* `get_firmware_info()` call, `FirmwareInfo.mode` is `"UEFI"` when
`/sys/firmware/efi` exists on the target filesystem and `"BIOS"` otherwise; when
no firmware data is readable, `available` is `False`.

**Validates: Requirements 9.3, 9.4**

---

## Interfaces

### `ICommandRunner` (from `codecraftai-interfaces`)

`HardwareInventoryManager` depends on this interface, injected at construction
time.

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

`HardwareInventoryManager` depends on this interface for reading `/proc`, `/sys`,
and `/etc` content without bypassing the transport boundary.

```python
class IFileOperator(Protocol):
    def read_text(self, path: str) -> str: ...
    def exists(self, path: str) -> bool: ...
    def list_dir(self, path: str) -> list[str]: ...
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

When `HardwareInventoryConfig.elevated` is `True`, the `HardwareInventoryManager`
passes `elevated=True` to `ICommandRunner.run()` **only** for `dmidecode`
invocations (DMI identity, DIMM inventory, and the `dmidecode -s` firmware
fallbacks). All other commands in this feature (`lscpu`, `lsmem`, `lsblk`,
`lspci`, `lsusb`, `ip`, `uname`, `hostnamectl`) are issued with `elevated=False`
regardless of the config flag, because they do not require root privilege on a
standard RHEL system. The runner is responsible for prepending `sudo` or using
whatever privilege-escalation mechanism is appropriate for the environment.
`HardwareInventoryManager` has no knowledge of how elevation is implemented.

---

## Command Mappings

| Operation | Command / Path |
|---|---|
| DMI identity (primary) | `dmidecode -t system -t baseboard -t chassis -t bios` |
| DIMM inventory (primary) | `dmidecode -t memory` |
| Firmware (dmidecode fallback) | `dmidecode -s bios-vendor` / `-s bios-version` / `-s bios-release-date` |
| CPU info (primary) | `lscpu -J` |
| Memory aggregate (primary) | `lsmem --output-all -J` |
| Block devices | `lsblk -J -b -o NAME,KNAME,TYPE,SIZE,ROTA,MODEL,SERIAL,VENDOR,WWN,TRAN,FSTYPE,UUID,LABEL,MOUNTPOINT` |
| PCI devices | `lspci -vmm -nnk` |
| USB devices | `lsusb` |
| Network link hardware | `ip -j -d link show` |
| Kernel identity | `uname -srvmo` |
| OS release file | read `/etc/os-release` via file operator |
| Machine identity (primary) | `hostnamectl --json=short` |
| Machine-id fallback | read `/etc/machine-id` via file operator |
| Hostname fallback | read `/etc/hostname` via file operator |
| DMI sysfs fallback | read `/sys/class/dmi/id/<field>` via file operator |
| UEFI detection | file operator `exists("/sys/firmware/efi")` |
| NIC per-interface sysfs | read `/sys/class/net/<iface>/speed`, `.../duplex`, `.../device/driver` |
| CPU fallback | read `/proc/cpuinfo` via file operator |
| Memory fallback | read `/proc/meminfo` via file operator |

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `HardwareInventoryQueryError` | A required command returns an unexpected non-zero exit code, produces malformed JSON, or cannot be parsed |
| `HardwareInventoryPermissionError` | `dmidecode` fails with a permission-denied indication and elevation was not in effect |

`HardwareInventoryPermissionError` carries enough context (`field`, `command`,
`stderr`) for the coordinator to surface actionable guidance to retry with
elevation.

### Partial-Snapshot Warnings

`collect_snapshot()` does not abort on `HardwareInventoryPermissionError` from a
sub-query. Instead, the corresponding field on `HardwareSnapshot` is left as
`None` and a `PartialInventoryWarning` entry is appended to
`HardwareSnapshot.warnings`. Any exception other than a permission error
(including `HardwareInventoryQueryError`) is re-raised and aborts the snapshot.

### Tool-Unavailable Handling

When a command is not installed, the feature distinguishes two classes:

| Capability | Behavior When Tool Missing |
|---|---|
| `get_dmi_identity()` | Fall back to sysfs; `source="sysfs"` or `"partial"` |
| `get_cpu_info()` | Fall back to `/proc/cpuinfo`; `source="proc_cpuinfo"` |
| `get_memory_info()` aggregate | Fall back to `/proc/meminfo` |
| `get_memory_info()` DIMMs | Return empty DIMM list |
| `list_pci_devices()` | Return empty list |
| `list_usb_devices()` | Return empty list |
| `get_os_release()` machine identity | Fall back to `/etc/machine-id` + `/etc/hostname` |
| `get_firmware_info()` | Fall back to `dmidecode -s` then `available=False` |

Command absence is detected by the runner (non-zero exit with
`command not found`-style stderr, or exit code 127) and does not propagate as a
`HardwareInventoryQueryError` for the capabilities listed above.

---

## Module Layout

```
source/packages/ccai/sysman/linux/redhat/
├── __init__.py
└── hardwareinventory.py             ← HardwareInventoryManager, config, models, exceptions

source/testroots/testbase/
└── ccai/tests/sysman/linux/redhat/
    └── test_hardwareinventory.py    ← unit + property-based tests using MockCommandRunner + MockFileOperator
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` and a `MockFileOperator` injected into
  `HardwareInventoryManager` at construction time.
- Mocks are seeded with canned stdout from real `dmidecode`, `lscpu -J`,
  `lsmem -J`, `lsblk -J`, `lspci -vmm -nnk`, `lsusb`, `ip -j -d link show`,
  `uname`, and `hostnamectl --json=short` captures.
- Tests verify the *arguments* passed to the runner and the *paths* passed to
  the file operator, not system state.
- Each public method has at minimum: a success path test, a fallback test
  (where applicable), a tool-unavailable test (where applicable), and a failure
  path test.
- `collect_snapshot()` has dedicated tests for full-success aggregation, for
  partial-snapshot behavior on permission errors, and for re-raising on other
  errors.
- `get_firmware_info()` has a dedicated test for UEFI vs. BIOS detection via
  `/sys/firmware/efi`.
- No test requires root access, real DMI hardware, or a live RHEL system.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in
Python. Each correctness property defined above is implemented as a
property-based test that:

- Generates random but valid inputs (stub command outputs, file contents,
  configurations)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific
correctness property they validate, using the format:
`# Feature: hardware-inventory, Property N: <property description>`

**Property Test Coverage:**

- Property 1: Snapshot aggregates every sub-query exactly once on a fully
  privileged stubbed system
- Property 2: Permission errors produce `None` fields and warnings without
  aborting
- Property 3: DMI fallback populates from sysfs when `dmidecode` is missing
- Property 4: CPU fallback populates from `/proc/cpuinfo` when `lscpu` is
  missing
- Property 5: Memory fallback populates from `/proc/meminfo` with empty DIMM
  list
- Property 6: PCI and USB return empty lists on tool unavailability
- Property 7: Elevation is applied only to `dmidecode` invocations
- Property 8: Non-zero exit codes raise the correct typed exception
- Property 9: Network hardware records contain no IP addressing fields
- Property 10: Malformed JSON surfaces as `HardwareInventoryQueryError`
- Property 11: UEFI/BIOS mode reflects `/sys/firmware/efi` existence

Both unit tests and property-based tests are complementary: unit tests verify
specific examples and edge cases, while property tests verify universal
correctness across many inputs.

---

## Usage Examples

### Local Execution (Default)

```python
from ccai.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from ccai.sysman.linux.redhat.fileops import LocalFileOperator, LocalFileOperatorConfig
from ccai.sysman.linux.redhat.hardwareinventory import (
    HardwareInventoryManager,
    HardwareInventoryConfig,
)

# Create local runner + file operator
runner = LocalCommandRunner(LocalRunnerConfig())
file_operator = LocalFileOperator(LocalFileOperatorConfig())

# Inject into hardware inventory manager
config = HardwareInventoryConfig(elevated=True)
inventory = HardwareInventoryManager(runner, file_operator, config)

# Produce a combined snapshot for a maintenance report
snapshot = inventory.collect_snapshot()
print(f"Host: {snapshot.os_release.pretty_name}")
print(f"CPU sockets: {snapshot.cpu.sockets} cores/socket: {snapshot.cpu.cores_per_socket}")
print(f"Block devices: {len(snapshot.block_devices)}")
if len(snapshot.warnings) > 0:
    for warning in snapshot.warnings:
        print(f"  partial: {warning.field} — {warning.reason}")
```

### SSH Execution with Reliability Tracking

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.fileops import SSHFileOperator, SSHFileOperatorConfig
from ccai.sysman.linux.redhat.hardwareinventory import (
    HardwareInventoryManager,
    HardwareInventoryConfig,
)

# Create SSH runner + file operator for a remote system
ssh_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0
)
runner = SSHCommandRunner(ssh_config)
file_operator = SSHFileOperator(SSHFileOperatorConfig.from_runner(runner))

# Inject into hardware inventory (same code as local!)
config = HardwareInventoryConfig(elevated=True)
inventory = HardwareInventoryManager(runner, file_operator, config)

# Query across a fleet — manager code is unchanged
snapshot = inventory.collect_snapshot()

# Check SSH reliability metrics for this host
metrics = runner.get_metrics()
print(f"Host: {ssh_config.host}")
print(f"Success rate: {metrics.success_count / (metrics.success_count + metrics.failure_count):.2%}")
print(f"Average duration: {metrics.avg_duration_ms:.2f}ms")
print(f"Connection retries: {metrics.retry_count}")
if metrics.last_error is not None:
    print(f"Last error: {metrics.last_error_type.value} - {metrics.last_error}")
```

### SSH Execution with Aspects

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.fileops import SSHFileOperator, SSHFileOperatorConfig
from ccai.sysman.linux.redhat.aspects import (
    PersistenceAspect, RetryAspect, TimeoutAspect,
    PersistenceAspectConfig, RetryAspectConfig, TimeoutAspectConfig,
)
from ccai.sysman.linux.redhat.storage import SQLiteStorage
from ccai.sysman.linux.redhat.hardwareinventory import (
    HardwareInventoryManager,
    HardwareInventoryConfig,
)

# Create SSH runner
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))
file_operator = SSHFileOperator(SSHFileOperatorConfig.from_runner(runner))

# Add persistence aspect for an audit trail of all inventory queries
storage = SQLiteStorage("/var/log/maintenance/inventory.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

# Retry on transient SSH failures
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[255],
    retryable_error_patterns=["Connection reset", "Temporary failure"]
))
runner.add_aspect(retry)

# Global timeout
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=120.0))
runner.add_aspect(timeout)

# Inject into inventory manager
inventory = HardwareInventoryManager(
    runner,
    file_operator,
    HardwareInventoryConfig(elevated=True),
)

# Every inventory command now has:
# - Audit persistence to SQLite (audit trail)
# - Automatic retry on transient failures
# - 2-minute per-command timeout
snapshot = inventory.collect_snapshot()
```
