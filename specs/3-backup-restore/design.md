# Design

## Overview

The backup and restore feature is implemented as a single module,
`automojo.sysman.linux.redhat.backuprestore`, that exposes a `BackupRestoreManager` class.
The coordinator instantiates `BackupRestoreManager` and calls its methods to produce,
verify, restore, and prune file-tree archives on a Red Hat Linux system.

All system interaction is delegated to two injected interfaces: an `ICommandRunner` for
executing `tar`, `rsync`, `sha256sum`, `openssl enc`, and related binaries, and an
`IFileOperator` for creating, reading, renaming, and deleting files and directories under
the backup repository root. The `BackupRestoreManager` never directly calls `subprocess`,
`os`, or `pathlib` write APIs; it constructs command argument lists and file-operator
requests and passes them to the injected dependencies. This keeps business logic separate
from execution and makes the feature fully testable via mocks without root access or a
live system.

The feature is strictly scoped to **file-tree backup and restore**. It covers repository
initialization, tar archive creation, rsync mirror creation, archive verification,
manifest-driven listing, restore into same or alternate roots, archive content queries,
retention policy pruning, and optional `openssl`-based archive encryption. It does **not**
back up block devices, LVM volumes, or databases. Block-level and volume-level snapshot
capture and rollback remain the responsibility of the `storage-management` feature.
Application-consistent database dumps remain the responsibility of application-specific
tooling.

The feature works identically whether commands execute locally (via `LocalCommandRunner`
and `LocalFileOperator`) or remotely over SSH (via `SSHCommandRunner` and
`SSHFileOperator`). When using SSH, the coordinator can track reliability metrics and
apply execution aspects (persistence, retry, timeout) without any changes to the
`BackupRestoreManager` code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md) for
details on local/SSH execution, reliability tracking, and aspect configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner and file operator (Local or SSH)    │
│  - Configures aspects (optional)                           │
│  - Injects both into BackupRestoreManager                  │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            BackupRestoreManager                             │
│  automojo.sysman.linux.redhat.backuprestore                 │
│                                                             │
│  init_repository()      create_archive()                    │
│  create_rsync_mirror()  verify_archive()                    │
│  list_entries()         restore_archive()                   │
│  query_contents()       apply_retention()                   │
└──────────┬────────────────────────────────────┬─────────────┘
           │ delegates to                       │ delegates to
           ▼                                    ▼
┌────────────────────────────┐      ┌────────────────────────────┐
│      ICommandRunner        │      │       IFileOperator        │
│  run(args, elevated, ...)  │      │  make_dir / stat / rename  │
│  → RunResult               │      │  read_text / write_text    │
│                            │      │  remove / exists / chmod   │
└────────────┬───────────────┘      └────────────┬───────────────┘
             │                                   │
     ┌───────┴────────┐                  ┌───────┴────────┐
     ▼                ▼                  ▼                ▼
┌─────────────┐  ┌─────────────┐   ┌─────────────┐  ┌─────────────┐
│ LocalRunner │  │ SSHRunner   │   │ LocalFileOp │  │ SSHFileOp   │
│ subprocess  │  │ SSH channel │   │ pathlib/os  │  │ SFTP/SSH    │
│ sudo elev.  │  │ reliability │   │ sudo elev.  │  │ elevation   │
└──────┬──────┘  └──────┬──────┘   └─────────────┘  └─────────────┘
       │                │
       └────────┬───────┘
                │ optional
                ▼
┌─────────────────────────────────────────────────────────────┐
│                    Execution Aspects                        │
│  - PersistenceAspect: audit logging (passphrase redacted)   │
│  - RetryAspect: transient failure retry                     │
│  - TimeoutAspect: global timeout policy                     │
└─────────────────────────────────────────────────────────────┘
```

---

## Components

### `BackupRestoreManager`

**Location:** `source/packages/automojo/sysman/linux/redhat/backuprestore.py`

The primary public class. Constructed with an `ICommandRunner`, an `IFileOperator`, and a
`BackupRestoreManagerConfig`.

| Method | Description |
|---|---|
| `init_repository(root)` | Create the repository layout (`archives/`, `manifests/`, `tmp/`, `repository.json`) |
| `create_archive(name, sources, excludes=None, one_file_system=False, encryption=False, passphrase=None)` | Create a tar archive into the repository with manifest and checksum |
| `create_rsync_mirror(name, source, previous=None, link_dest=True)` | Create an rsync mirror with optional `--link-dest` to a previous mirror |
| `verify_archive(manifest_name, include_tar_test=False, passphrase=None)` | Verify archive bytes against the manifest checksum, optionally also `tar --test-label` |
| `list_entries(name_prefix=None)` → `list[BackupEntry]` | Enumerate manifests and return archive metadata |
| `restore_archive(manifest_name, target_root, restrict_paths=None, require_existing=False, passphrase=None)` | Verify then extract archive into target root |
| `query_contents(manifest_name, include_paths=True, passphrase=None)` → `BackupContents` | List paths inside an archive using `tar --list` |
| `apply_retention(policy)` | Remove archives and manifests according to retention policy |

### `BackupRestoreManagerConfig`

**Location:** `source/packages/automojo/sysman/linux/redhat/backuprestore.py`

A dataclass holding runtime configuration for `BackupRestoreManager`.

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no mutating commands or file operations are executed |
| `elevated` | `bool` | `False` | When `True`, mutating commands and file operations use the runner's elevation mechanism |
| `default_compression` | `BackupCompression` | `BackupCompression.NONE` | Default compression (`none`, `gzip`, `zstd`, `xz`) |
| `repository_root` | `Path` | `Path("/var/lib/automojo/backups")` | Default repository root used when a method is not given an explicit one |

---

## Data Models

### `BackupActionStatus`

```python
class BackupActionStatus(Enum):
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"   # already in desired state, no action needed
    DRY_RUN = "dry_run"   # would have acted, but dry_run=True
```

### `BackupCompression`

```python
class BackupCompression(Enum):
    NONE = "none"
    GZIP = "gzip"
    ZSTD = "zstd"
    XZ   = "xz"
```

### `BackupActionResult`

Returned by all write and destructive operations (init, create, rsync, restore, retention).

```python
@dataclass
class BackupActionResult:
    status: BackupActionStatus
    archive_path: Path | None
    manifest_path: Path | None
    sha256: str | None
    exit_code: int | None
    stdout: str | None
    stderr: str | None
    command: list[str] | None
    removed_entries: list[str] | None
    removed_bytes: int | None
    planned_paths: list[str] | None
    error_message: str | None
```

### `BackupEntry`

Returned as elements of `list_entries()`.

```python
@dataclass
class BackupEntry:
    name: str
    timestamp: str
    archive_path: Path
    manifest_path: Path
    size_bytes: int
    compression: BackupCompression
    sha256: str
```

### `BackupVerifyResult`

Returned by `verify_archive()`.

```python
@dataclass
class BackupVerifyResult:
    ok: bool
    computed_sha: str
    recorded_sha: str
    tar_test_output: str | None
```

### `BackupContents`

Returned by `query_contents()`.

```python
@dataclass
class BackupContents:
    total_entries: int
    entries_list: list[str]
```

### `BackupRetentionPolicy`

Input to `apply_retention()`.

```python
@dataclass
class BackupRetentionPolicy:
    keep_last_n: int | None
    keep_older_than_days: int | None
    keep_min_total_bytes: int | None
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Verify-After-Create Matches Recorded Checksum

*For any* successful `create_archive()` call, a subsequent `verify_archive()` against the
same manifest SHALL return `ok=True` with `computed_sha == recorded_sha`.

**Validates: Requirements 2.3, 2.5, 4.1, 4.2**

### Property 2: Restore Verifies Before Extracting

*For any* `restore_archive()` call, the verify capability SHALL be invoked before the
extract command is issued, and if verification fails the extract command SHALL NOT be
invoked.

**Validates: Requirements 6.1, 6.2**

### Property 3: Retention Rule Intersection

*For any* retention policy with multiple rules (`keep_last_n`, `keep_older_than_days`,
`keep_min_total_bytes`), an entry SHALL be removed only when *all* supplied rules permit
its removal.

**Validates: Requirements 8.1**

### Property 4: Retention Safety Without Rules

*For any* retention invocation where no supplied rule marks any entry for removal, the
result SHALL be `SKIPPED` and no file operator delete SHALL be issued.

**Validates: Requirements 8.2**

### Property 5: Repository Init Idempotence

*For any* `init_repository()` call against a path whose on-disk layout already matches the
required layout (directory modes, subdirectory set, `repository.json` present), the result
SHALL be `SKIPPED` and no file operator write SHALL be issued.

**Validates: Requirements 1.2**

### Property 6: Passphrase Never in argv

*For any* `create_archive()`, `verify_archive()`, `restore_archive()`, or
`query_contents()` call with `encryption=True`, the constructed command argument list
passed to the runner SHALL NOT contain the passphrase; the passphrase SHALL only be
delivered via stdin.

**Validates: Requirements 9.1, 9.3, 9.4**

### Property 7: Encryption Extension and Manifest Record

*For any* successful encrypted `create_archive()` call, the archive path SHALL end with
`.enc` and the written manifest SHALL contain `encryption="aes-256-cbc/pbkdf2"`.

**Validates: Requirements 9.2**

### Property 8: rsync link-dest Only When Previous Exists

*For any* `create_rsync_mirror()` call where no previous mirror is supplied or found, the
constructed `rsync` command SHALL NOT contain a `--link-dest` argument. When a previous
mirror is supplied and exists, the command SHALL contain exactly one `--link-dest=<path>`
argument.

**Validates: Requirements 3.1, 3.2**

### Property 9: Dry-Run Safety

*For any* method call with `dry_run=True`, no command other than read-only listing
(`tar --list` for restore planning) SHALL be executed and no file operator write, rename,
or delete SHALL be issued.

**Validates: Requirements 1.5, 2.7, 3.5, 6.7, 8.6, 9.5**

### Property 10: Elevation Consistency

*For any* mutating operation (`init_repository`, `create_archive`, `create_rsync_mirror`,
`restore_archive`, `apply_retention`) with `elevated=True` in the config, every command
and every file operator mutation SHALL be invoked with `elevated=True`. Read-only
operations (`verify_archive` without tar-test, `list_entries`, `query_contents`) SHALL
NOT use elevation unless the archive or manifest is unreadable to the calling user.

**Validates: Requirements 10.1, 10.2, 10.3**

### Property 11: Status Mapping Correctness

*For any* operation result from the command runner, the `BackupActionResult.status` SHALL
correctly reflect the runner's exit code: `0` maps to `SUCCESS`, non-zero maps to
`FAILED`, unless the operation was skipped or in dry-run mode.

**Validates: Requirements 2.5, 2.6, 3.4, 6.5, 6.6, 8.4, 8.5**

### Property 12: Byte-Identical Repository Init Skip

*For any* pair of `init_repository()` calls against the same root where the first
succeeded, the second call's reported `repository.json` content SHALL be byte-identical to
the one written by the first, and no rewrite SHALL occur.

**Validates: Requirements 1.2, 1.4**

### Property 13: Temp File Cleanup on Failure

*For any* `create_archive()` call whose tar command fails, the temporary archive file
under `tmp/` SHALL be removed via the file operator before the method returns.

**Validates: Requirements 2.6**

---

## Interfaces

### `ICommandRunner` (from `automojo-interfaces`)

`BackupRestoreManager` depends on this interface, injected at construction time.

```python
class ICommandRunner(Protocol):
    def run(
        self,
        args: list[str],
        elevated: bool = False,
        capture_output: bool = True,
        stdin_data: bytes | None = None,
    ) -> RunResult: ...
```

The `stdin_data` parameter is used to deliver the encryption passphrase to `openssl enc`
without placing it on the command line.

### `IFileOperator` (from `automojo-interfaces`)

`BackupRestoreManager` also depends on this interface, injected at construction time.

```python
class IFileOperator(Protocol):
    def exists(self, path: Path, elevated: bool = False) -> bool: ...
    def is_dir(self, path: Path, elevated: bool = False) -> bool: ...
    def stat_mode(self, path: Path, elevated: bool = False) -> int: ...
    def make_dir(self, path: Path, mode: int, elevated: bool = False) -> None: ...
    def read_text(self, path: Path, elevated: bool = False) -> str: ...
    def write_text(self, path: Path, content: str, mode: int, elevated: bool = False) -> None: ...
    def rename(self, src: Path, dst: Path, elevated: bool = False) -> None: ...
    def remove(self, path: Path, elevated: bool = False) -> None: ...
    def chmod(self, path: Path, mode: int, elevated: bool = False) -> None: ...
    def chown(self, path: Path, owner: str, group: str, elevated: bool = False) -> None: ...
    def list_dir(self, path: Path, elevated: bool = False) -> list[Path]: ...
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

### Elevation Strategy

When `BackupRestoreManagerConfig.elevated` is `True`, the `BackupRestoreManager` passes
`elevated=True` to `ICommandRunner.run()` and to `IFileOperator` mutations. The runner
and file operator are responsible for prepending `sudo` or using whatever privilege
escalation mechanism is appropriate for the environment. `BackupRestoreManager` has no
knowledge of how elevation is implemented.

---

## Command Mappings

| Operation | Command |
|---|---|
| Create archive (no compression) | `tar --create --file <tmp> --acls --xattrs --selinux --numeric-owner --sparse [--one-file-system] [--exclude=<pat> ...] -- <sources>` |
| Create archive (gzip) | `tar --create --file <tmp> --acls --xattrs --selinux --numeric-owner --sparse [--one-file-system] [--exclude=<pat> ...] --gzip -- <sources>` |
| Create archive (zstd) | `tar --create --file <tmp> --acls --xattrs --selinux --numeric-owner --sparse [--one-file-system] [--exclude=<pat> ...] --zstd -- <sources>` |
| Create archive (xz) | `tar --create --file <tmp> --acls --xattrs --selinux --numeric-owner --sparse [--one-file-system] [--exclude=<pat> ...] --xz -- <sources>` |
| Extract archive | `tar --extract --file <archive> --acls --xattrs --selinux --numeric-owner -C <target-root> [compression] [<restricted-path> ...]` |
| List archive contents | `tar --list --file <archive> [compression]` |
| Test archive label | `tar --test-label --file <archive>` |
| SHA-256 checksum | `sha256sum <path>` |
| rsync mirror (fresh) | `rsync -aAXH --numeric-ids --delete <source>/ <target>/` |
| rsync mirror (link-dest) | `rsync -aAXH --numeric-ids --delete --link-dest=<previous> <source>/ <target>/` |
| rsync mirror (dry-run) | `rsync -aAXH --numeric-ids --delete [--link-dest=<previous>] --dry-run <source>/ <target>/` |
| Encrypt (via pipe, passphrase on stdin) | `openssl enc -aes-256-cbc -salt -pbkdf2 -out <archive>.enc -pass stdin` |
| Decrypt (via pipe, passphrase on stdin) | `openssl enc -d -aes-256-cbc -pbkdf2 -in <archive>.enc -pass stdin` |
| Create repo dir (file operator) | `make_dir(<root>, mode=0o750, elevated=True)` |
| Create subdir (file operator) | `make_dir(<root>/archives, mode=0o750, elevated=True)` |
| Write repo metadata (file operator) | `write_text(<root>/repository.json, <json>, mode=0o640, elevated=True)` |
| Rename temp to final (file operator) | `rename(<root>/tmp/<x>, <root>/archives/<y>, elevated=True)` |
| Remove archive (file operator) | `remove(<root>/archives/<x>, elevated=True)` |
| Remove manifest (file operator) | `remove(<root>/manifests/<x>, elevated=True)` |

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `BackupNotFoundError` | A requested archive or manifest name does not resolve to an existing file under the repository |
| `BackupRepositoryNotFoundError` | A list or query operation is issued against a repository root that does not exist or is missing the expected layout |
| `BackupQueryError` | `tar --list` or `sha256sum` on a read path returns an unexpected non-zero exit code |

All other failures are surfaced as a `BackupActionResult` with `status=FAILED` so the
coordinator can decide how to respond without catching exceptions for normal operational
failures.

### Passphrase Handling

Encryption passphrases are treated as sensitive material. The `BackupRestoreManager`
enforces the following rules:

1. The passphrase is delivered to `openssl enc` exclusively via the `stdin_data`
   parameter on `ICommandRunner.run()`. It is never placed on the command line (no
   `-k`, `-pass pass:`, or similar argv forms).
2. The `command` field stored in `RunResult` and echoed into `BackupActionResult` uses
   `-pass stdin` as the placeholder so audit records never capture the secret.
3. Any logging or audit aspect observing the command list SHALL receive the redacted
   form. The `BackupRestoreManager` does not write the passphrase to any log, manifest,
   or on-disk file.
4. The passphrase parameter on public methods is typed as `SecretStr | None`; when the
   method returns, the local reference is overwritten before the method exits.
5. When `dry_run=True`, no `openssl enc` invocation is issued, so no passphrase is ever
   read or consumed.

### Idempotency Guards

Before executing mutating commands, `BackupRestoreManager` inspects the current state of
the repository through the file operator. If the system is already in the desired state
(e.g. the repository layout matches, or retention has no eligible entries) the method
returns `SKIPPED` without invoking the runner or mutating the file operator.

---

## Module Layout

```
source/packages/automojo/sysman/linux/redhat/
├── __init__.py
└── backuprestore.py              ← BackupRestoreManager, config, models, exceptions

source/testroots/testbase/
└── automojo/tests/sysman/linux/redhat/
    └── test_backuprestore.py     ← unit + property-based tests using mocks
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` and a `MockFileOperator` injected into
  `BackupRestoreManager` at construction time.
- Tests verify the *arguments* passed to the runner and the *operations* issued against
  the file operator, not real on-disk state.
- Each public method has at minimum: a success path test, a skip/idempotency test, a
  failure path test, and a dry-run test.
- `create_archive()` has additional tests for each compression option and for the
  encrypted path (verifying the passphrase is never in argv and the `.enc` extension is
  applied).
- `create_rsync_mirror()` has additional tests for the `--link-dest` branch and the
  fresh-mirror branch.
- `verify_archive()` has additional tests for the optional `tar --test-label` branch.
- `restore_archive()` has additional tests for the `require_existing=True` branch and
  the restricted-paths branch.
- `apply_retention()` has additional tests for each rule and for the intersection
  behavior.
- No test requires root access or a live `tar`/`rsync`/`openssl` installation.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in Python.
Each correctness property defined above is implemented as a property-based test that:

- Generates random but valid inputs (repository layouts, archive names, source lists,
  compression choices, retention policies, exit codes).
- Executes the operation under test against the mocks.
- Verifies the property holds across all generated inputs.
- Runs a minimum of 100 iterations per property test.

Property-based tests are tagged with comments referencing the specific correctness
property they validate, using the format:
`# Feature: backup-restore, Property N: <property description>`

**Property Test Coverage:**

- Property 1: Verify-after-create matches across random archive names and compressions
- Property 2: Restore invokes verify before extract across random manifest names
- Property 3: Retention intersection across random rule combinations
- Property 4: Retention skip when no rule permits removal
- Property 5: Repository init idempotence across random matching layouts
- Property 6: Passphrase never in argv across random passphrase values
- Property 7: Encrypted archive extension and manifest record across random names
- Property 8: rsync link-dest presence across previous/no-previous mirror scenarios
- Property 9: Dry-run safety across all mutating methods
- Property 10: Elevation consistency across elevated/non-elevated configurations
- Property 11: Status mapping across random exit codes
- Property 12: Byte-identical repository.json on repeat init
- Property 13: Temp file cleanup on tar failure

Both unit tests and property-based tests are complementary: unit tests verify specific
examples and edge cases, while property tests verify universal correctness across many
inputs.

---

## Usage Examples

### Local Execution (Default)

```python
from pathlib import Path

from automojo.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from automojo.sysman.linux.redhat.fileoperators import LocalFileOperator, LocalFileOperatorConfig
from automojo.sysman.linux.redhat.backuprestore import (
    BackupRestoreManager,
    BackupRestoreManagerConfig,
    BackupCompression,
)

# Create local runner and file operator
runner = LocalCommandRunner(LocalRunnerConfig())
fileop = LocalFileOperator(LocalFileOperatorConfig())

# Configure the manager
config = BackupRestoreManagerConfig(
    dry_run=False,
    elevated=True,
    default_compression=BackupCompression.ZSTD,
    repository_root=Path("/var/lib/automojo/backups"),
)
manager = BackupRestoreManager(runner, fileop, config)

# Initialize the repository
init_result = manager.init_repository(root=Path("/var/lib/automojo/backups"))
print(f"Init: {init_result.status}")

# Archive /etc
create_result = manager.create_archive(
    name="etc-preupgrade",
    sources=[Path("/etc")],
    excludes=["/etc/machine-id"],
)
print(f"Archive: {create_result.status} at {create_result.archive_path}")

# Verify the archive
verify_result = manager.verify_archive(
    manifest_name="etc-preupgrade",
    include_tar_test=True,
)
print(f"Verify: ok={verify_result.ok}")

# List entries
entries_list = manager.list_entries()
for entry in entries_list:
    print(f"  {entry.name} {entry.timestamp} ({entry.size_bytes} bytes)")
```

### SSH Execution with Reliability Tracking

```python
from pathlib import Path

from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.fileoperators import SSHFileOperator, SSHFileOperatorConfig
from automojo.sysman.linux.redhat.backuprestore import (
    BackupRestoreManager,
    BackupRestoreManagerConfig,
)

# Create SSH runner and SSH file operator for remote system
ssh_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0,
)
runner = SSHCommandRunner(ssh_config)
fileop = SSHFileOperator(SSHFileOperatorConfig(host="192.168.1.100", username="admin"))

# Inject into backup manager (same code as local)
config = BackupRestoreManagerConfig(elevated=True)
manager = BackupRestoreManager(runner, fileop, config)

# Fleet restore workflow: verify then restore each host's /etc snapshot
verify_result = manager.verify_archive(manifest_name="etc-preupgrade")
if verify_result.ok is True:
    restore_result = manager.restore_archive(
        manifest_name="etc-preupgrade",
        target_root=Path("/"),
    )
    print(f"Restore: {restore_result.status}")
else:
    print(f"Verify failed: computed={verify_result.computed_sha} "
          f"recorded={verify_result.recorded_sha}")

# Check SSH reliability metrics
metrics = runner.get_metrics()
success_rate = metrics.success_count / (metrics.success_count + metrics.failure_count)
print(f"Success rate: {success_rate:.2%}")
print(f"Average duration: {metrics.avg_duration_ms:.2f}ms")
```

### SSH Execution with Aspects and Encryption

```python
from pathlib import Path

from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.fileoperators import SSHFileOperator, SSHFileOperatorConfig
from automojo.sysman.linux.redhat.aspects import (
    PersistenceAspect, PersistenceAspectConfig,
    RetryAspect, RetryAspectConfig,
    TimeoutAspect, TimeoutAspectConfig,
)
from automojo.sysman.linux.redhat.storage import SQLiteStorage
from automojo.sysman.linux.redhat.backuprestore import (
    BackupRestoreManager,
    BackupRestoreManagerConfig,
    BackupCompression,
)
from automojo.secrets import SecretStr

# Create SSH runner
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))
fileop = SSHFileOperator(SSHFileOperatorConfig(host="remote-server", username="admin"))

# Add persistence aspect for audit logging (passphrase is redacted in captured argv)
storage = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

# Add retry aspect for transient SSH failures
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[255],
    retryable_error_patterns=["Connection reset", "Temporary failure"],
))
runner.add_aspect(retry)

# Add timeout aspect for global policy
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=1800.0))
runner.add_aspect(timeout)

# Configure the manager with zstd default compression
manager = BackupRestoreManager(
    runner,
    fileop,
    BackupRestoreManagerConfig(
        elevated=True,
        default_compression=BackupCompression.ZSTD,
        repository_root=Path("/var/lib/automojo/backups"),
    ),
)

# Load passphrase from a secure source (environment vault, etc.)
passphrase = SecretStr.from_env("AUTOMOJO_BACKUP_PASSPHRASE")

# Create an encrypted archive; the passphrase is delivered via stdin only
create_result = manager.create_archive(
    name="etc-offsite",
    sources=[Path("/etc")],
    encryption=True,
    passphrase=passphrase,
)
print(f"Encrypted archive: {create_result.archive_path}")
# Expected: path ends with '.tar.zst.enc', manifest records 'aes-256-cbc/pbkdf2'
```
