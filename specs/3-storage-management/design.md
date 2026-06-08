# Design

## Overview

The storage management feature is implemented as a single module,
`ccai.sysman.linux.redhat.storagemanagement`, that exposes a `StorageManager` class.
The coordinator instantiates `StorageManager` and calls its methods to perform discrete
block-level storage actions: block-device discovery, partition-table creation,
partitioning, filesystem creation, LVM (physical volumes, volume groups, logical
volumes), and software RAID (`mdadm`) management.

This feature **complements — and does not duplicate** — the mount, unmount, and
`/etc/fstab` management provided by the `filesystem-management` feature. The
`StorageManager` brings block devices, partitions, filesystems, LVM stacks, and RAID
arrays into the desired shape; the `FilesystemManager` is then responsible for
mounting those filesystems and persisting their entries in `/etc/fstab`.

All command execution is delegated to a command runner implementing the
`ICommandRunner` interface. All writes to configuration files (notably
`/etc/mdadm.conf`) are delegated to a file operator implementing the `IFileOperator`
interface. The `StorageManager` never directly calls `subprocess`, shell commands, or
`open()`; it only constructs argument lists and file payloads and hands them to its
injected dependencies. This keeps business logic separate from execution and makes
the feature fully testable via mock dependencies without root access or a live system.

The feature works identically whether commands execute locally (via
`LocalCommandRunner`) or remotely over SSH (via `SSHCommandRunner`). When using SSH,
the coordinator can track reliability metrics and apply execution aspects
(persistence, retry, timeout) without any changes to the `StorageManager` code.

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
│  - Injects runner + file operator into StorageManager       │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            StorageManager                                   │
│  ccai.sysman.linux.redhat.storagemanagement             │
│                                                             │
│  list_block_devices()   query_partition_table()             │
│  create_partition_table()  create_partition()               │
│  remove_partition()     wipe_signatures()                   │
│  create_filesystem()                                        │
│  create_pv()  create_vg()  extend_vg()  remove_vg()         │
│  create_lv()  extend_lv()  reduce_lv()  remove_lv()         │
│  list_pvs()   list_vgs()   list_lvs()                       │
│  create_raid_array()  query_raid_array()                    │
│  list_raid_arrays()   refresh_mdadm_conf()                  │
└───────────┬─────────────────────────────────────┬───────────┘
            │ delegates commands to               │ delegates file writes to
            ▼                                     ▼
┌──────────────────────────────┐      ┌──────────────────────────────┐
│       ICommandRunner         │      │        IFileOperator         │
│ run(args, elevated, ...)     │      │ read/write/stat/chmod/chown  │
│   → RunResult                │      │                              │
└──────────────┬───────────────┘      └──────────────┬───────────────┘
               │                                     │
     ┌─────────┴─────────┐                 ┌─────────┴─────────┐
     ▼                   ▼                 ▼                   ▼
┌──────────┐      ┌──────────────┐  ┌──────────┐      ┌──────────────┐
│ Local    │      │ SSH          │  │ Local    │      │ SSH          │
│ Command  │      │ Command      │  │ File     │      │ File         │
│ Runner   │      │ Runner       │  │ Operator │      │ Operator     │
└────┬─────┘      └──────┬───────┘  └────┬─────┘      └──────┬───────┘
     │                   │               │                   │
     └─────────┬─────────┘               └─────────┬─────────┘
               │ optional                          │ optional
               ▼                                   ▼
┌─────────────────────────────────────────────────────────────┐
│                    Execution Aspects                        │
│  - PersistenceAspect: audit logging                         │
│  - RetryAspect: transient failure retry                     │
│  - TimeoutAspect: global timeout policy                     │
└─────────────────────────────────────────────────────────────┘
```

---

## Components

### `StorageManager`

**Location:** `source/packages/ccai/sysman/linux/redhat/storagemanagement.py`

The primary public class.  Constructed with an `ICommandRunner`, an `IFileOperator`,
and a `StorageManagerConfig`.

| Method | Description |
|---|---|
| `list_block_devices(type_filter=None)` → `list[BlockDevice]` | Enumerate block devices via `lsblk -J -b` |
| `query_partition_table(device)` → `PartitionTable` | Query the partition table of a disk via `parted ... print` |
| `create_partition_table(device, table_type, force=False)` → `StorageActionResult` | Create a GPT or MSDOS table via `parted mklabel` |
| `create_partition(device, part_type, fs_hint, start, end, name=None, flags=None)` → `StorageActionResult` | Create a partition via `parted mkpart` plus optional `name` and `set` |
| `remove_partition(device, number)` → `StorageActionResult` | Remove a partition via `parted rm` |
| `wipe_signatures(device)` → `StorageActionResult` | Wipe filesystem and table signatures via `wipefs -a` |
| `create_filesystem(device, fs_type, label=None, uuid=None, force=False)` → `StorageActionResult` | Create a filesystem via `mkfs.xfs / mkfs.ext4 / mkfs.ext3 / mkfs.vfat / mkswap` |
| `create_pv(device, force=False)` → `StorageActionResult` | Initialize an LVM PV via `pvcreate` |
| `create_vg(vg_name, pv_list)` → `StorageActionResult` | Create an LVM VG via `vgcreate` |
| `extend_vg(vg_name, pv_list)` → `StorageActionResult` | Extend an LVM VG via `vgextend` |
| `remove_vg(vg_name)` → `StorageActionResult` | Remove an LVM VG via `vgremove -f` |
| `create_lv(vg_name, lv_name, size=None, extents=None)` → `StorageActionResult` | Create an LV via `lvcreate -n -L \| -l` |
| `extend_lv(vg_name, lv_name, size=None, extents=None, resize_fs=False)` → `StorageActionResult` | Extend an LV via `lvextend` with optional `-r` |
| `reduce_lv(vg_name, lv_name, size=None, extents=None, confirm=False)` → `StorageActionResult` | Reduce an LV via `lvreduce`; requires `confirm=True` |
| `remove_lv(vg_name, lv_name)` → `StorageActionResult` | Remove an LV via `lvremove -f` |
| `list_pvs()` → `list[PhysicalVolume]` | Query PV state via `pvs --reportformat json` |
| `list_vgs()` → `list[VolumeGroup]` | Query VG state via `vgs --reportformat json` |
| `list_lvs()` → `list[LogicalVolume]` | Query LV state via `lvs --reportformat json` |
| `create_raid_array(device_name, level, members, metadata=None, chunk=None, bitmap=None, run=False, force=False)` → `StorageActionResult` | Create a software RAID array via `mdadm --create` |
| `query_raid_array(device)` → `RaidArray` | Query a single array via `mdadm --detail` |
| `list_raid_arrays()` → `list[RaidArray]` | Enumerate all arrays via `/proc/mdstat` |
| `refresh_mdadm_conf()` → `StorageActionResult` | Refresh `/etc/mdadm.conf` with scanned ARRAY lines |

### `StorageManagerConfig`

**Location:** `source/packages/ccai/sysman/linux/redhat/storagemanagement.py`

A dataclass holding runtime configuration for `StorageManager`.

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no commands are executed and no files are written |
| `elevated` | `bool` | `False` | When `True`, mutating commands are invoked with the runner's elevation mechanism |

---

## Data Models

### `StorageActionStatus`

```python
class StorageActionStatus(Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"   # already in desired state, no action needed
    DRY_RUN  = "dry_run"   # would have acted, but dry_run=True
```

### `StorageActionResult`

Returned by all mutating operations.

```python
@dataclass
class StorageActionResult:
    status: StorageActionStatus
    target: str                    # device path, vg/lv name, or array device
    changed: bool                  # True when the system state was modified
    exit_code: int | None          # raw exit code from command runner
    stdout: str | None
    stderr: str | None
    command: list[str] | None      # the command that was (or would have been) run
    detail: dict | None            # operation-specific extras (e.g. assigned part number, UUID)
```

### `BlockDevice`

Returned as elements of `list_block_devices()`.

```python
@dataclass
class BlockDevice:
    name: str
    kname: str
    pkname: str | None
    type: str                      # disk, part, lvm, raid1, raid5, ...
    size: int                      # bytes
    fstype: str | None
    fsuse_pct: int | None
    uuid: str | None
    mountpoint: str | None
    ro: bool
    rm: bool
    model: str | None
    serial: str | None
    parent: str | None             # derived from pkname for traversal
```

### `PartitionTable`

Returned by `query_partition_table()`.

```python
@dataclass
class PartitionTable:
    type: str                      # "msdos" | "gpt" | "loop" | "unknown"
    size_bytes: int
    partitions: list[Partition]
```

### `Partition`

```python
@dataclass
class Partition:
    number: int
    start: int                     # bytes
    end: int                       # bytes
    size: int                      # bytes
    fs_type: str | None
    flags: list[str]
```

### `PhysicalVolume`

Returned as elements of `list_pvs()`.

```python
@dataclass
class PhysicalVolume:
    pv_name: str
    vg_name: str | None
    pv_uuid: str
    pv_size: int                   # bytes
    pv_free: int                   # bytes
```

### `VolumeGroup`

Returned as elements of `list_vgs()`.

```python
@dataclass
class VolumeGroup:
    vg_name: str
    vg_uuid: str
    pv_count: int
    lv_count: int
    vg_size: int                   # bytes
    vg_free: int                   # bytes
```

### `LogicalVolume`

Returned as elements of `list_lvs()`.

```python
@dataclass
class LogicalVolume:
    lv_name: str
    vg_name: str
    lv_uuid: str
    lv_path: str
    lv_size: int                   # bytes
    seg_type: str
    origin: str | None
    pool_lv: str | None
```

### `RaidArray`

Returned by `query_raid_array()` and as elements of `list_raid_arrays()`.

```python
@dataclass
class RaidArray:
    device: str                    # /dev/mdX
    level: str                     # raid0, raid1, raid5, raid6, raid10
    state: str                     # clean, degraded, resyncing, ...
    members: list[str]
    active: int
    working: int
    failed: int
    spare: int
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Partition-Table Idempotency

*For any* device that already carries a partition table of the requested type, calling
`create_partition_table()` with `force=False` should skip execution and return
`SKIPPED` without invoking the command runner.

**Validates: Requirements 3.2**

### Property 2: Wipe Refusal When Mounted

*For any* device whose self or any child is mounted, `wipe_signatures()` must never
invoke the command runner and must return `FAILED` with an error naming the mounted
path.

**Validates: Requirements 6.2**

### Property 3: mkfs Skip When Same Filesystem

*For any* device that already carries a filesystem of the same type requested and
`force=False`, `create_filesystem()` should skip execution and return `SKIPPED`.

**Validates: Requirements 7.4**

### Property 4: LVM Create-then-Remove Symmetry

*For any* PV/VG/LV created by `create_pv()` / `create_vg()` / `create_lv()`, a
subsequent call to the matching remove path (`remove_vg()` after
`lvremove`, `pvremove` semantics) should succeed and leave the system in a state where
`list_pvs() / list_vgs() / list_lvs()` no longer reports the object.

**Validates: Requirements 8, 9, 10, 11**

### Property 5: lvreduce Confirmation Required

*For any* call to `reduce_lv()` with `confirm=False`, the command runner must not be
invoked and the result must be `FAILED` with an explanatory error; only
`confirm=True` permits the reduction command.

**Validates: Requirements 10.3**

### Property 6: RAID Create Refuses Busy Members

*For any* member device that is currently mounted or that appears in another RAID
array, `create_raid_array()` must never invoke the command runner and must return
`FAILED` with an error naming the conflicting use.

**Validates: Requirements 12.3**

### Property 7: LV/VG Remove Refusal on Active Mount

*For any* LV that is currently mounted, both `reduce_lv()` and `remove_lv()` must
return `FAILED` without invoking the command runner. *For any* VG that still has
active LVs, `remove_vg()` must return `FAILED` without invoking the command runner.

**Validates: Requirements 9.5, 10.5**

### Property 8: JSON Parsing Stability for LVM Queries

*For any* well-formed JSON output from `pvs / vgs / lvs --reportformat json`, parsing
into `PhysicalVolume / VolumeGroup / LogicalVolume` must be stable across multiple
invocations and must surface every record present in the report.

**Validates: Requirements 11.4**

### Property 9: Dry-Run Safety

*For any* mutating operation (`create_partition_table`, `create_partition`,
`remove_partition`, `wipe_signatures`, `create_filesystem`, `create_pv`, `create_vg`,
`extend_vg`, `remove_vg`, `create_lv`, `extend_lv`, `reduce_lv`, `remove_lv`,
`create_raid_array`, `refresh_mdadm_conf`) with `dry_run=True`, the command runner
must never be invoked with a mutating command and the file operator must never be
invoked with a write, and the result status must be `DRY_RUN`.

**Validates: Requirements 3.6, 4.6, 5.5, 6.4, 7.8, 8.5, 9.6, 10.7, 12.5, 14.4**

### Property 10: Elevation Consistency

*For any* mutating operation with `elevated=True` in the config, the command runner
must be invoked with `elevated=True`; for read-only queries (`lsblk`, `blkid`,
`parted print`, `pvs`, `vgs`, `lvs`, `mdadm --detail`, `cat /proc/mdstat`),
`elevated` must never be forced `True` by the `StorageManager`.

**Validates: Requirements 15.1, 15.3**

### Property 11: Status Mapping Correctness

*For any* mutating operation result from the command runner, the
`StorageActionResult` status must correctly reflect the runner's exit code: `0`
maps to `SUCCESS`, non-zero maps to `FAILED` (unless the operation was skipped or
in dry-run mode).

**Validates: Requirements 3.4, 3.5, 4.5, 5.4, 7.7, 8.4, 10.6, 12.4**

### Property 12: mdadm.conf Idempotent Refresh

*For any* `/etc/mdadm.conf` whose contents already contain the exact ARRAY lines
emitted by `mdadm --detail --scan`, `refresh_mdadm_conf()` must return `SKIPPED`
without invoking the file operator's write method.

**Validates: Requirements 14.3**

### Property 13: Query Error Propagation

*For any* query command (`lsblk`, `parted print`, `pvs`, `vgs`, `lvs`,
`mdadm --detail`) that exits non-zero, the corresponding method must raise a
`StorageQueryError` carrying the exit code and stderr.

**Validates: Requirements 1.4, 2.4, 11.5, 13.4**

### Property 14: Partition Boundary Idempotency

*For any* partition request whose start, end, and type match an existing partition on
the disk, `create_partition()` must return `SKIPPED` without invoking the command
runner.

**Validates: Requirements 4.4**

---

## Interfaces

### `ICommandRunner` (from `codecraftai-interfaces`)

`StorageManager` depends on this interface, injected at construction time.

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

`StorageManager` depends on this interface, injected at construction time, for all
writes under `/etc/` (notably `/etc/mdadm.conf`).

```python
class IFileOperator(Protocol):
    def read_text(self, path: str, elevated: bool = False) -> str: ...
    def write_text(
        self,
        path: str,
        content: str,
        mode: int = 0o644,
        owner: str | None = None,
        group: str | None = None,
        elevated: bool = False,
    ) -> None: ...
    def exists(self, path: str, elevated: bool = False) -> bool: ...
    def stat(self, path: str, elevated: bool = False) -> FileStat: ...
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

When `StorageManagerConfig.elevated` is `True`, the `StorageManager` passes
`elevated=True` to `ICommandRunner.run()` and `IFileOperator.write_text()` for all
**mutating** operations. Read-only query commands (`lsblk`, `blkid`, `parted print`,
`pvs`, `vgs`, `lvs`, `mdadm --detail`, `cat /proc/mdstat`) are invoked with
`elevated=False`; the runner itself is free to escalate if the invoking user lacks
permission to read a device node, but `StorageManager` never forces it. The runner is
responsible for prepending `sudo` or using whatever privilege escalation mechanism is
appropriate for the environment. `StorageManager` has no knowledge of how elevation is
implemented.

---

## Command Mappings

| Operation | Command |
|---|---|
| Enumerate block devices | `lsblk -J -b -o NAME,KNAME,PKNAME,TYPE,SIZE,FSTYPE,FSUSE%,UUID,MOUNTPOINT,RO,RM,MODEL,SERIAL` |
| Query partition table | `parted -s -m <device> unit B print` |
| Create partition table | `parted -s <device> mklabel <gpt\|msdos>` |
| Create partition | `parted -s <device> mkpart <type> <fs> <start> <end>` |
| Name GPT partition | `parted -s <device> name <n> <label>` |
| Set partition flag | `parted -s <device> set <n> <flag> on` |
| Remove partition | `parted -s <device> rm <n>` |
| Wipe signatures | `wipefs -a <device>` |
| Create xfs filesystem | `mkfs.xfs [-f] [-L <label>] <device>` |
| Create ext4 filesystem | `mkfs.ext4 [-F] [-L <label>] [-U <uuid>] <device>` |
| Create ext3 filesystem | `mkfs.ext3 [-F] [-L <label>] [-U <uuid>] <device>` |
| Create vfat filesystem | `mkfs.vfat [-I] [-n <label>] <device>` |
| Create swap | `mkswap [-L <label>] [-U <uuid>] <device>` |
| Set xfs UUID | `xfs_admin -U <uuid> <device>` |
| Query device UUID | `blkid -s UUID -o value <device>` |
| Create LVM PV | `pvcreate [-ff -y] <device>` |
| Create LVM VG | `vgcreate <vg-name> <pv-list>` |
| Extend LVM VG | `vgextend <vg-name> <pv-list>` |
| Remove LVM VG | `vgremove -f <vg-name>` |
| Create LV | `lvcreate -n <lv-name> -L <size>\|-l <extents> <vg-name>` |
| Extend LV | `lvextend [-r] -L <size>\|-l <extents> <vg>/<lv>` |
| Reduce LV | `lvreduce -L <size>\|-l <extents> <vg>/<lv>` |
| Remove LV | `lvremove -f <vg>/<lv>` |
| Query PVs | `pvs --reportformat json --units b -o +vg_name,pv_uuid,pv_size,pv_free` |
| Query VGs | `vgs --reportformat json --units b -o +vg_uuid,pv_count,lv_count,vg_size,vg_free` |
| Query LVs | `lvs --reportformat json --units b -o +lv_uuid,lv_path,lv_size,seg_type,origin,pool_lv` |
| Create RAID array | `mdadm --create /dev/<name> --level=<n> --raid-devices=<count> [--metadata=<v>] [--chunk=<k>] [--bitmap=<b>] [--run --force] <members>` |
| Query RAID array | `mdadm --detail /dev/<name>` |
| List RAID arrays | `cat /proc/mdstat` |
| Scan for mdadm.conf refresh | `mdadm --detail --scan` |

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `StorageQueryError` | `lsblk`, `parted print`, `blkid`, `pvs`, `vgs`, `lvs`, `mdadm --detail`, or `cat /proc/mdstat` returns an unexpected non-zero exit code, or the target is not a RAID array when queried |

All other failures are surfaced as a `StorageActionResult` with `status=FAILED` so
the coordinator can decide how to respond without catching exceptions for normal
operational failures.

### Mounted-Device Refusal

Before executing any destructive operation (`create_partition_table`,
`remove_partition`, `wipe_signatures`, `create_filesystem`, `reduce_lv`, `remove_lv`,
`remove_vg`, `create_raid_array`), `StorageManager` consults `list_block_devices()`
and LVM queries to determine whether the target (or any of its children) is mounted
or in use. When the target is in use, the method returns a `StorageActionResult`
with `status=FAILED` and an error message naming the mounted path(s) or conflicting
array membership. The command runner is never invoked in this case.

### Idempotency Guards

Before executing mutating commands, `StorageManager` queries current state:

- `create_partition_table`: consult `query_partition_table()` and compare table type
- `create_partition`: consult `query_partition_table()` and compare boundaries + type
- `create_filesystem`: consult `list_block_devices()` and compare `fstype`
- `create_pv`: consult `list_pvs()` and compare by device path
- `create_vg`: consult `list_vgs()` and compare name + PV set
- `create_lv`: consult `list_lvs()` and compare VG/LV name
- `refresh_mdadm_conf`: compare existing ARRAY lines against the scanned output

If the system is already in the desired state and `force=False`, the method returns
`SKIPPED` without invoking the runner or file operator.

---

## Module Layout

```
source/packages/ccai/sysman/linux/redhat/
├── __init__.py
└── storagemanagement.py          ← StorageManager, config, models, exceptions

source/testroots/testbase/
└── ccai/tests/sysman/linux/redhat/
    └── test_storagemanagement.py ← unit tests using MockCommandRunner + MockFileOperator
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` and a `MockFileOperator` injected into
  `StorageManager` at construction time.
- Tests verify the *arguments* passed to the runner and the *payload* passed to the
  file operator, not system state.
- Each public method has at minimum: a success path test, a skip/idempotency test, a
  failure path test, and (for mutating methods) a dry-run test.
- `list_block_devices()` and the LVM query methods have fixture-driven tests that
  parse canned JSON samples into the data classes.
- `reduce_lv()` has dedicated tests for the `confirm=True / confirm=False` branches.
- `create_raid_array()` has dedicated tests for the busy-member refusal path.
- `refresh_mdadm_conf()` has dedicated tests for the scan-matches-file skip path and
  the preserved-non-ARRAY-lines path.
- No test requires root access, a live `lsblk`, or real `mdadm`/`lvm2` tooling.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in Python.
Each correctness property defined above will be implemented as a property-based test
that:

- Generates random but valid inputs (device paths, sizes, PV/VG/LV names, RAID levels)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific correctness
property they validate, using the format:
`# Feature: storage-management, Property N: <property description>`

**Property Test Coverage:**

- Property 1: Partition-table idempotency with random table-type pairs
- Property 2: Wipe refusal driven by randomly-generated mounted-child trees
- Property 3: mkfs skip with randomly-generated matching fstype pairs
- Property 4: LVM create-then-remove symmetry for random PV/VG/LV names
- Property 5: `lvreduce` confirm gating with random size/extent pairs
- Property 6: RAID create refusal with random busy-member injection
- Property 7: LV/VG remove refusal with random mount injection
- Property 8: JSON parsing stability on randomly-shuffled valid LVM reports
- Property 9: Dry-run safety across all mutating operations
- Property 10: Elevation consistency for mutating versus read-only calls
- Property 11: Status mapping for random exit codes
- Property 12: mdadm.conf idempotent refresh with permuted ARRAY-line ordering
- Property 13: Query error propagation for random non-zero exit codes
- Property 14: Partition boundary idempotency for random start/end matches

Both unit tests and property-based tests are complementary: unit tests verify
specific examples and edge cases, while property tests verify universal correctness
across many inputs.

---

## Usage Examples

### Local Execution: Create PV + VG + LV on /dev/sdb

```python
from ccai.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from ccai.sysman.linux.redhat.fileops import LocalFileOperator, LocalFileOperatorConfig
from ccai.sysman.linux.redhat.storagemanagement import (
    StorageManager, StorageManagerConfig,
)

# Create local runner + file operator
runner = LocalCommandRunner(LocalRunnerConfig())
fileop = LocalFileOperator(LocalFileOperatorConfig())

# Inject into storage manager
config = StorageManagerConfig(elevated=True, dry_run=False)
storage = StorageManager(runner, fileop, config)

# Wipe any stale signatures, then build PV / VG / LV on /dev/sdb
wipe_result = storage.wipe_signatures(device="/dev/sdb")
pv_result   = storage.create_pv(device="/dev/sdb")
vg_result   = storage.create_vg(vg_name="data_vg", pv_list=["/dev/sdb"])
lv_result   = storage.create_lv(vg_name="data_vg", lv_name="data_lv", size="10G")

print(f"PV: {pv_result.status}, VG: {vg_result.status}, LV: {lv_result.status}")
```

### SSH Execution with Reliability: Extend LV with Filesystem Resize

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.fileops import SSHFileOperator, SSHFileOperatorConfig
from ccai.sysman.linux.redhat.storagemanagement import (
    StorageManager, StorageManagerConfig,
)

# Create SSH runner + file operator for remote system
ssh_runner_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0,
)
runner = SSHCommandRunner(ssh_runner_config)
fileop = SSHFileOperator(SSHFileOperatorConfig(host="192.168.1.100", username="admin"))

# Inject into storage manager (same code as local)
config = StorageManagerConfig(elevated=True)
storage = StorageManager(runner, fileop, config)

# Grow /dev/data_vg/data_lv by 5G and resize the filesystem inline
result = storage.extend_lv(
    vg_name="data_vg",
    lv_name="data_lv",
    size="+5G",
    resize_fs=True,
)

# Check SSH reliability metrics
metrics = runner.get_metrics()
denom = metrics.success_count + metrics.failure_count
success_rate = metrics.success_count / denom if denom > 0 else 0.0
print(f"Success rate: {success_rate:.2%}")
print(f"Average duration: {metrics.avg_duration_ms:.2f}ms")
print(f"Connection retries: {metrics.retry_count}")
if metrics.last_error is not None:
    print(f"Last error: {metrics.last_error_type.value} - {metrics.last_error}")
```

### SSH Execution with Aspects: Bulk Provisioning

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.fileops import SSHFileOperator, SSHFileOperatorConfig
from ccai.sysman.linux.redhat.aspects import (
    PersistenceAspect, RetryAspect, TimeoutAspect,
    PersistenceAspectConfig, RetryAspectConfig, TimeoutAspectConfig,
)
from ccai.sysman.linux.redhat.storage import SQLiteStorage
from ccai.sysman.linux.redhat.storagemanagement import (
    StorageManager, StorageManagerConfig,
)

# Create SSH runner + file operator
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))
fileop = SSHFileOperator(SSHFileOperatorConfig(host="remote-server", username="admin"))

# Add persistence aspect for audit logging
storage_backend = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage_backend))
runner.add_aspect(persistence)

# Add retry aspect for transient failures (e.g., network glitches)
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[1, 255],
    retryable_error_patterns=["Connection reset", "Temporary failure"],
))
runner.add_aspect(retry)

# Add timeout aspect for global policy
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=600.0))
runner.add_aspect(timeout)

# Inject into storage manager
storage = StorageManager(runner, fileop, StorageManagerConfig(elevated=True))

# Bulk provisioning: initialize 4 disks into a RAID5 array + single LV
members = ["/dev/sdb", "/dev/sdc", "/dev/sdd", "/dev/sde"]
for device in members:
    storage.wipe_signatures(device=device)

raid_result = storage.create_raid_array(
    device_name="md0",
    level="raid5",
    members=members,
    metadata="1.2",
    chunk="512",
    bitmap="internal",
)
storage.create_pv(device="/dev/md0")
storage.create_vg(vg_name="bulk_vg", pv_list=["/dev/md0"])
storage.create_lv(vg_name="bulk_vg", lv_name="bulk_lv", extents="100%FREE")

# Persist the array in /etc/mdadm.conf so it assembles at boot
conf_result = storage.refresh_mdadm_conf()
print(f"RAID: {raid_result.status}, mdadm.conf: {conf_result.status}")
```
