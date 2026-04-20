# Design

## Overview

The filesystem management feature is implemented as a single module,
`automojo.sysman.linux.redhat.filesystemmanagement`, that exposes a `FilesystemManager`
class. The coordinator instantiates `FilesystemManager` and calls its methods to perform
discrete maintenance actions on the local filesystem.

All system interaction is delegated to a command runner implementing the `ICommandRunner`
interface. `FilesystemManager` never directly accesses the filesystem via Python's `os` or
`pathlib` modules; it only constructs command argument lists and passes them to the runner.
This keeps business logic separate from execution and makes the feature fully testable via a
mock runner without requiring a live filesystem or root access.

The feature works identically whether commands execute locally (via `LocalCommandRunner`) or
remotely over SSH (via `SSHCommandRunner`). When using SSH, the coordinator can track
reliability metrics and apply execution aspects (persistence, retry, timeout) without any
changes to the `FilesystemManager` code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md) for
details on local/SSH execution, reliability tracking, and aspect configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner (Local or SSH)                      │
│  - Configures aspects (optional)                           │
│  - Injects runner into FilesystemManager                   │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│          FilesystemManager                                  │
│  automojo.sysman.linux.redhat.filesystemmanagement         │
│                                                             │
│  create_directory()  remove_directory()                     │
│  create_symlink()    remove_symlink()                       │
│  query_path()        query_disk_usage()                     │
│  mount()             unmount()                              │
│  list_mounts()                                              │
│  add_fstab_entry()   remove_fstab_entry()                   │
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

### `FilesystemManager`

**Location:** `source/packages/automojo/sysman/linux/redhat/filesystemmanagement.py`

| Method | Signature | Description |
|---|---|---|
| `create_directory` | `(path, mode=None, owner=None, group=None) → FilesystemActionResult` | Create directory with optional permissions |
| `remove_directory` | `(path, recursive=False) → FilesystemActionResult` | Remove a directory |
| `create_symlink` | `(link, target) → FilesystemActionResult` | Create a symbolic link |
| `remove_symlink` | `(link) → FilesystemActionResult` | Remove a symbolic link |
| `query_path` | `(path) → PathInfo \| None` | Stat a path |
| `query_disk_usage` | `(path) → DiskUsageInfo` | Get disk usage for a mount point |
| `mount` | `(spec: MountSpec) → FilesystemActionResult` | Mount a filesystem |
| `unmount` | `(mountpoint, lazy=False) → FilesystemActionResult` | Unmount a filesystem |
| `list_mounts` | `() → list[MountInfo]` | List all currently mounted filesystems |
| `add_fstab_entry` | `(entry: FstabEntry) → FilesystemActionResult` | Add entry to fstab |
| `remove_fstab_entry` | `(device_or_mountpoint) → FilesystemActionResult` | Remove entry from fstab |

### `FilesystemManagerConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no commands are executed |
| `elevated` | `bool` | `False` | When `True`, write commands pass `elevated=True` |
| `fstab_path` | `str` | `"/etc/fstab"` | Path to fstab file; overridable for testing |

---

## Data Models

### Input Types

```python
@dataclass
class MountSpec:
    device: str
    mountpoint: str
    fstype: str | None = None
    options: str | None = None

@dataclass
class FstabEntry:
    device: str
    mountpoint: str
    fstype: str
    options: str = "defaults"
    dump: int = 0
    passno: int = 0
```

### Result Types

```python
class FilesystemActionStatus(Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"
    DRY_RUN  = "dry_run"

@dataclass
class FilesystemActionResult:
    status: FilesystemActionStatus
    path: str
    exit_code: int | None
    stdout: str | None
    stderr: str | None
    command: list[str] | None
    detail: str | None            # human-readable detail for SKIPPED or conflict cases
```

### Query Result Types

```python
@dataclass
class PathInfo:
    path: str
    path_type: str      # "regular file" | "directory" | "symbolic link" | ...
    mode: str           # octal string e.g. "755"
    owner: str
    group: str
    size_bytes: int

@dataclass
class DiskUsageInfo:
    path: str
    total_bytes: int
    used_bytes: int
    available_bytes: int
    use_percent: int

@dataclass
class MountInfo:
    device: str
    mountpoint: str
    fstype: str
    options: str
```

---

## Interfaces

### `ICommandRunner` (from `automojo-interfaces`)

```python
class ICommandRunner(Protocol):
    def run(
        self,
        args: list[str],
        elevated: bool = False,
        capture_output: bool = True,
        stdin: str | None = None,
    ) -> RunResult: ...
```

---

## Command Mappings

### Directory Operations

| Operation | Command |
|---|---|
| Create directory | `mkdir -p <path>` |
| Set permissions | `chmod <mode> <path>` |
| Set ownership | `chown <owner>:<group> <path>` |
| Remove empty directory | `rmdir <path>` |
| Remove directory tree | `rm -rf <path>` |
| Check path exists | `stat -c "%F %a %U %G %s" <path>` |

### Symlink Operations

| Operation | Command |
|---|---|
| Create symlink | `ln -s <target> <link>` |
| Read symlink target | `readlink <link>` |
| Remove symlink | `unlink <link>` |
| Check path type | `stat -c "%F" <path>` |

### Mount Operations

| Operation | Command |
|---|---|
| Mount filesystem | `mount [-t <fstype>] [-o <options>] <device> <mountpoint>` |
| Unmount filesystem | `umount [-l] <mountpoint>` |
| List mounts | `findmnt --json` |

### Disk and fstab Operations

| Operation | Command |
|---|---|
| Query disk usage | `df -B1 --output=size,used,avail,pcent <path>` |
| Read fstab | `cat <fstab_path>` |
| Write fstab | `tee <fstab_path>` with content on stdin |

---

## Idempotency Guards

| Method | Guard | How determined |
|---|---|---|
| `create_directory()` | Directory already exists | `stat` exits 0 and path type is `directory` |
| `remove_directory()` | Directory absent | `stat` exits non-zero |
| `create_symlink()` | Symlink exists with same target | `readlink` output matches target |
| `create_symlink()` — retarget | Symlink exists with different target | `readlink` output differs; unlink then re-create |
| `create_symlink()` — conflict | Non-symlink exists at link path | `stat -c "%F"` returns non-symlink type; return `FAILED` |
| `remove_symlink()` | Path absent | `stat` exits non-zero |
| `remove_symlink()` — conflict | Path is not a symlink | `stat -c "%F"` is not `symbolic link`; return `FAILED` |
| `mount()` | Device already mounted at mountpoint | `list_mounts()` contains matching entry |
| `unmount()` | Mountpoint not currently mounted | `list_mounts()` has no matching entry |
| `add_fstab_entry()` | Entry already in fstab | Parse `cat fstab` — match on device or mountpoint field |
| `remove_fstab_entry()` | No matching entry | Parse `cat fstab` — no match found |

---

## fstab Management

fstab entries are read via `cat <fstab_path>` through the runner.  The content is parsed
in pure Python line by line (skipping comments and blank lines).  Each non-comment line is
split on whitespace into the six standard fields: device, mountpoint, fstype, options,
dump, passno.

For `add_fstab_entry()`: if no existing line matches on device or mountpoint, the rendered
entry line is appended to the file content and written back via `tee`.

For `remove_fstab_entry()`: lines matching the supplied device-or-mountpoint string in
field 1 or field 2 are removed.  The updated content is written back via `tee`.

A rendered `FstabEntry` follows the standard six-field format:

```
<device>  <mountpoint>  <fstype>  <options>  <dump>  <passno>
```

Example:
```
/dev/sdb1  /data  ext4  defaults,noatime  0  2
```

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `FilesystemQueryError` | `query_path()`, `query_disk_usage()`, or `list_mounts()` returns an unexpected non-zero exit code |

All other failures from write operations are surfaced as a `FilesystemActionResult` with
`status=FAILED`.  Path conflict cases (non-symlink at symlink target; non-symlink passed
to `remove_symlink`) also return `FAILED` with a `detail` message rather than raising.

---

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Directory Creation Idempotency

*For any* directory path, calling `create_directory()` multiple times should result in the first call returning `SUCCESS` and subsequent calls returning `SKIPPED` with no additional mkdir commands.

**Validates: Requirements 1.2**

### Property 2: Directory Creation Command Construction

*For any* directory path, calling `create_directory()` should construct the command `mkdir -p <path>` and pass it to the command runner.

**Validates: Requirements 1.1**

### Property 3: Directory Permission Application

*For any* directory path and valid octal permission mode, calling `create_directory()` with a mode parameter should result in a `chmod <mode> <path>` command after successful mkdir.

**Validates: Requirements 1.3**

### Property 4: Directory Ownership Application

*For any* directory path and valid user/group combination, calling `create_directory()` with ownership parameters should result in a `chown <owner>:<group> <path>` command after successful mkdir.

**Validates: Requirements 1.4**

### Property 5: Directory Removal Command Selection

*For any* directory path, calling `remove_directory()` with `recursive=False` should use `rmdir <path>`, and with `recursive=True` should use `rm -rf <path>`.

**Validates: Requirements 2.1, 2.2**

### Property 6: Directory Removal Idempotency

*For any* non-existent directory path, calling `remove_directory()` should return `SKIPPED` without executing any commands.

**Validates: Requirements 2.3**

### Property 7: Symlink Creation Command Construction

*For any* link path and target path, calling `create_symlink()` should construct the command `ln -s <target> <link>` and pass it to the command runner.

**Validates: Requirements 3.1**

### Property 8: Symlink Creation Idempotency

*For any* existing symlink that points to the correct target, calling `create_symlink()` with the same link and target should return `SKIPPED` without executing commands.

**Validates: Requirements 3.2**

### Property 9: Symlink Target Update

*For any* existing symlink that points to a different target, calling `create_symlink()` should remove the old link and create a new one, returning `SUCCESS`.

**Validates: Requirements 3.3**

### Property 10: Symlink Path Conflict Detection

*For any* existing non-symlink file or directory, calling `create_symlink()` at that path should return `FAILED` with an appropriate error message.

**Validates: Requirements 3.4**

### Property 11: Symlink Removal Command Construction

*For any* link path, calling `remove_symlink()` should construct the command `unlink <path>` and pass it to the command runner.

**Validates: Requirements 4.1**

### Property 12: Symlink Removal Idempotency

*For any* non-existent path, calling `remove_symlink()` should return `SKIPPED` without executing any commands.

**Validates: Requirements 4.2**

### Property 13: Non-Symlink Removal Protection

*For any* existing non-symlink file or directory, calling `remove_symlink()` should return `FAILED` with an appropriate error message.

**Validates: Requirements 4.3**

### Property 14: Path Query Command Construction

*For any* path, calling `query_path()` should construct the command `stat -c "%F %a %U %G %s" <path>` and pass it to the command runner.

**Validates: Requirements 5.1**

### Property 15: Path Query Result Parsing

*For any* successful stat command output, `query_path()` should return a `PathInfo` object with correctly parsed path type, permissions, owner, group, and size.

**Validates: Requirements 5.2**

### Property 16: Non-Existent Path Query Handling

*For any* non-existent path, `query_path()` should return `None` when the stat command fails with "No such file or directory".

**Validates: Requirements 5.3**

### Property 17: Disk Usage Query Command Construction

*For any* path, calling `query_disk_usage()` should construct the command `df -B1 --output=size,used,avail,pcent <path>` and pass it to the command runner.

**Validates: Requirements 6.1**

### Property 18: Disk Usage Result Parsing

*For any* successful df command output, `query_disk_usage()` should return a `DiskUsageInfo` object with correctly parsed total, used, available bytes and use percentage.

**Validates: Requirements 6.2**

### Property 19: Mount Command Construction

*For any* device and mount point, calling `mount()` should construct the appropriate mount command with optional filesystem type and options.

**Validates: Requirements 7.1, 7.2, 7.3**

### Property 20: Mount Operation Idempotency

*For any* already-mounted filesystem at the specified mount point, calling `mount()` should return `SKIPPED` without executing mount commands.

**Validates: Requirements 7.4**

### Property 21: Unmount Command Construction

*For any* mount point, calling `unmount()` should construct `umount <mountpoint>`, and with `lazy=True` should construct `umount -l <mountpoint>`.

**Validates: Requirements 8.1, 8.2**

### Property 22: Unmount Operation Idempotency

*For any* non-mounted filesystem, calling `unmount()` should return `SKIPPED` without executing umount commands.

**Validates: Requirements 8.3**

### Property 23: Mount List Query Command Construction

*For any* call to `list_mounts()`, the system should construct the command `findmnt --json` and pass it to the command runner.

**Validates: Requirements 9.1**

### Property 24: Mount List Result Parsing

*For any* successful findmnt JSON output, `list_mounts()` should return a list of `MountInfo` objects with correctly parsed device, mount point, filesystem type, and options.

**Validates: Requirements 9.2**

### Property 25: Fstab Entry Addition Idempotency

*For any* fstab entry that already exists for the same device or mount point, calling `add_fstab_entry()` should return `SKIPPED` without modifying the fstab file.

**Validates: Requirements 10.2**

### Property 26: Fstab Entry Removal Idempotency

*For any* device or mount point that doesn't exist in fstab, calling `remove_fstab_entry()` should return `SKIPPED` without modifying the fstab file.

**Validates: Requirements 11.2**

### Property 27: Dry-Run Safety

*For any* write operation (create_directory, remove_directory, create_symlink, remove_symlink, mount, unmount, add_fstab_entry, remove_fstab_entry) with `dry_run=True`, the command runner should never be invoked for write commands, and the result status should be `DRY_RUN`.

**Validates: Requirements 1.7, 2.6, 3.7, 4.6, 7.7, 8.6, 10.5, 11.5**

### Property 28: Elevation for Write Operations

*For any* write operation with `elevated=True` in the config, the command runner should be invoked with `elevated=True` for all write commands.

**Validates: Requirements 12.1**

### Property 29: No Elevation for Read Operations

*For any* read operation (query_path, query_disk_usage, list_mounts), the command runner should never be invoked with `elevated=True` regardless of the elevation configuration.

**Validates: Requirements 12.2**

---

## Module Layout

```
source/packages/automojo/sysman/linux/redhat/
├── __init__.py
└── filesystemmanagement.py       ← FilesystemManager, config, models, exceptions

source/testroots/testbase/
└── automojo/tests/sysman/linux/redhat/
    └── test_filesystemmanagement.py
```

---

---

## Usage Examples

### Local Execution (Default)

```python
from automojo.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from automojo.sysman.linux.redhat.filesystemmanagement import FilesystemManager, FilesystemManagerConfig, MountSpec, FstabEntry

# Create local runner
runner = LocalCommandRunner(LocalRunnerConfig())

# Inject into filesystem manager
config = FilesystemManagerConfig(elevated=True, dry_run=False)
fs_mgr = FilesystemManager(runner, config)

# Create directory with permissions
result = fs_mgr.create_directory("/opt/myapp", mode=0o755, owner="myapp", group="myapp")
print(f"Directory creation: {result.status}")

# Create symbolic link
link_result = fs_mgr.create_symlink("/usr/local/bin/myapp", "/opt/myapp/bin/myapp")
print(f"Symlink creation: {link_result.status}")

# Query path information
path_info = fs_mgr.query_path("/opt/myapp")
if path_info:
    print(f"Path type: {path_info.path_type}, Mode: {path_info.mode:o}")

# Mount filesystem
mount_spec = MountSpec(device="/dev/sdb1", mountpoint="/mnt/data", fstype="ext4", options="defaults,noatime")
mount_result = fs_mgr.mount(mount_spec)
print(f"Mount: {mount_result.status}")
```

### SSH Execution with Reliability Tracking

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.filesystemmanagement import FilesystemManager, FilesystemManagerConfig

# Create SSH runner for remote system
ssh_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0
)
runner = SSHCommandRunner(ssh_config)

# Inject into filesystem manager (same code as local!)
config = FilesystemManagerConfig(elevated=True)
fs_mgr = FilesystemManager(runner, config)

# Perform filesystem operations over SSH
result = fs_mgr.create_directory("/var/lib/myservice", mode=0o750, owner="myservice")

# Query disk usage on remote system
disk_usage = fs_mgr.query_disk_usage("/var")
print(f"Disk usage: {disk_usage.used_bytes}/{disk_usage.total_bytes} bytes ({disk_usage.use_percent}%)")

# List mounted filesystems
mounts = fs_mgr.list_mounts()
for mount in mounts:
    print(f"Mount: {mount.device} -> {mount.mountpoint} ({mount.fstype})")

# Check SSH reliability metrics
metrics = runner.get_metrics()
print(f"Success rate: {metrics.success_count / (metrics.success_count + metrics.failure_count):.2%}")
print(f"Average duration: {metrics.avg_duration_ms:.2f}ms")
```

### SSH Execution with Aspects

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.aspects import PersistenceAspect, RetryAspect, TimeoutAspect
from automojo.sysman.linux.redhat.aspects import PersistenceAspectConfig, RetryAspectConfig, TimeoutAspectConfig
from automojo.sysman.linux.redhat.storage import SQLiteStorage
from automojo.sysman.linux.redhat.filesystemmanagement import FilesystemManager, FilesystemManagerConfig, FstabEntry

# Create SSH runner
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))

# Add persistence aspect for audit logging
storage = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

# Add retry aspect for transient failures (e.g., device busy)
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[1, 16, 32],  # Filesystem errors
    retryable_error_patterns=["Device or resource busy", "Target is busy"]
))
runner.add_aspect(retry)

# Add timeout aspect for global policy
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=180.0))  # 3 minutes for filesystem ops
runner.add_aspect(timeout)

# Inject into filesystem manager
fs_mgr = FilesystemManager(runner, FilesystemManagerConfig(elevated=True))

# All filesystem operations now have:
# - Audit logging to SQLite
# - Automatic retry on device busy errors
# - 3-minute timeout enforcement

# Create directory structure
fs_mgr.create_directory("/opt/app/data", mode=0o755, owner="app", group="app")
fs_mgr.create_directory("/opt/app/logs", mode=0o755, owner="app", group="app")

# Add persistent mount to fstab
fstab_entry = FstabEntry(
    device="/dev/disk/by-uuid/12345678-1234-1234-1234-123456789012",
    mountpoint="/opt/app/data",
    fstype="ext4",
    options="defaults,noatime",
    dump=0,
    passno=2
)
fstab_result = fs_mgr.add_fstab_entry(fstab_entry)

# Mount the filesystem
mount_spec = MountSpec(device=fstab_entry.device, mountpoint=fstab_entry.mountpoint, fstype=fstab_entry.fstype)
mount_result = fs_mgr.mount(mount_spec)
```

### Dry-Run Mode Across Transports

```python
# Dry-run works identically for local and SSH
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin", dry_run=True))
fs_mgr = FilesystemManager(runner, FilesystemManagerConfig(elevated=True))

# No SSH connection is made, no commands executed
result = fs_mgr.remove_directory("/important/data", recursive=True)
assert result.status == FilesystemActionStatus.DRY_RUN
print("Would have removed:", result.path)
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` keyed on command args prefix.
- `stat`, `readlink`, `findmnt`, `cat fstab`, and `df` mock responses drive all
  idempotency and query logic.
- `fstab_path` in `FilesystemManagerConfig` is overridden to a test value in all fstab
  tests so path construction is exercised without touching `/etc/fstab`.
- fstab parsing and rendering logic is tested in pure Python by supplying mock `cat`
  output strings and verifying the `tee` stdin content the runner receives.
- `list_mounts()` JSON parsing is tested with representative `findmnt --json` output.
- No test requires root access or a live filesystem.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in Python. Each correctness property defined above will be implemented as a property-based test that:

- Generates random but valid inputs (paths, permissions, ownership, mount specifications)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific correctness property they validate, using the format: `# Feature: filesystem-management, Property N: <property description>`

**Property Test Coverage:**

- Properties 1-4: Directory creation tests generate random paths, permissions, and ownership
- Properties 5-6: Directory removal tests verify command selection and idempotency
- Properties 7-10: Symlink creation tests verify command construction and conflict handling
- Properties 11-13: Symlink removal tests verify command construction and protection
- Properties 14-16: Path query tests verify command construction and result parsing
- Properties 17-18: Disk usage tests verify command construction and result parsing
- Properties 19-22: Mount/unmount tests verify command construction and idempotency
- Properties 23-24: Mount listing tests verify command construction and JSON parsing
- Properties 25-26: Fstab manipulation tests verify idempotency and file handling
- Properties 27-29: Dry-run safety and elevation tests verify correct flag usage

Both unit tests and property-based tests are complementary: unit tests verify specific examples and edge cases, while property tests verify universal correctness across many inputs.
