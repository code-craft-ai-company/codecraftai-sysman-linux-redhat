# Design

## Overview

The SSH server configuration feature is implemented as a single module,
`ccai.sysman.linux.redhat.sshserverconfiguration`, that exposes a
`SshServerManager` class. The coordinator instantiates `SshServerManager` and
calls its methods to manage the OpenSSH server on a Red Hat Linux system: the
sshd service lifecycle, effective configuration queries, drop-in files under
`/etc/ssh/sshd_config.d/`, `Match` blocks, host-key files, the login banner,
and configuration validation.

This feature **complements but does not duplicate** two other features:

- **`security-compliance`** performs read-only SSH hardening *checks* (e.g.
  verifies that `PermitRootLogin` is `no`). `SshServerManager` is the feature
  that actually *applies* SSH posture — it is the write-side counterpart to the
  read-side audit performed by `security-compliance`.
- **`user-group-management`** owns per-user `~/.ssh/authorized_keys` content.
  `SshServerManager` does not touch any user's home directory; it only manages
  server-wide configuration under `/etc/ssh/`.

All system interaction is delegated to two injected interfaces from
`codecraftai-interfaces`:

- `ICommandRunner` for process execution (`sshd`, `systemctl`, `ssh-keygen`).
- `IFileOperator` for reads, writes, and deletes under `/etc/ssh/`.

`SshServerManager` never directly calls `subprocess`, `open()`, or shell
commands. It only constructs command argument lists and file-operation
requests and passes them to the injected interfaces. This keeps business logic
separate from execution and makes the feature fully testable via mocks without
root access or a live sshd.

The feature works identically whether commands execute locally (via
`LocalCommandRunner` + `LocalFileOperator`) or remotely over SSH (via
`SSHCommandRunner` + `SSHFileOperator`). When using SSH, the coordinator can
track reliability metrics and apply execution aspects (persistence, retry,
timeout) without any changes to the `SshServerManager` code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md)
for details on local/SSH execution, reliability tracking, and aspect
configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner + file operator (Local or SSH)       │
│  - Configures aspects (optional)                            │
│  - Injects both into SshServerManager                       │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            SshServerManager                                 │
│  ccai.sysman.linux.redhat.sshserverconfiguration        │
│                                                             │
│  get_service_state()       validate_config()                │
│  get_effective_directive() reload_config()                  │
│  get_full_effective_config()                                │
│  set_directive_dropin()    generate_host_key()              │
│  delete_directive_dropin() remove_host_key()                │
│  list_dropins()            list_host_key_fingerprints()     │
│  write_match_block()       ensure_banner()                  │
│  remove_match_block()                                       │
└──────────┬──────────────────────────────┬───────────────────┘
           │ delegates to                 │ delegates to
           ▼                              ▼
┌──────────────────────────┐  ┌──────────────────────────────┐
│      ICommandRunner      │  │       IFileOperator          │
│  run(args, elevated,     │  │  read / write / delete /     │
│      cwd, env, timeout)  │  │  list / stat / chmod / chown │
│  → RunResult             │  │  → FileOpResult              │
└───────────┬──────────────┘  └──────────────┬───────────────┘
            │                                │
     ┌──────┴──────┐                  ┌──────┴──────┐
     ▼             ▼                  ▼             ▼
┌─────────┐  ┌───────────┐      ┌─────────┐  ┌───────────┐
│  Local  │  │    SSH    │      │  Local  │  │    SSH    │
│ Runner  │  │  Runner   │      │FileOper.│  │ FileOper. │
└────┬────┘  └─────┬─────┘      └────┬────┘  └─────┬─────┘
     └──────┬──────┘                 └──────┬──────┘
            │ optional                      │ optional
            ▼                               ▼
┌─────────────────────────────────────────────────────────────┐
│                    Execution Aspects                        │
│  - PersistenceAspect: audit logging                         │
│  - RetryAspect: transient failure retry                     │
│  - TimeoutAspect: global timeout policy                     │
└─────────────────────────────────────────────────────────────┘
```

---

## Components

### `SshServerManager`

**Location:** `source/packages/ccai/sysman/linux/redhat/sshserverconfiguration.py`

The primary public class. Constructed with an `ICommandRunner`, an
`IFileOperator`, and a `SshServerManagerConfig`.

| Method | Description |
|---|---|
| `get_service_state()` → `SshdServiceState` | Query sshd active/enabled/present status |
| `get_effective_directive(name)` → `str \| None` | Return effective value of a single directive via `sshd -T` |
| `get_full_effective_config()` → `dict[str, str]` | Return full effective directive map via `sshd -T` |
| `set_directive_dropin(dropin_name, directive, value)` → `SshdActionResult` | Set a directive inside a named drop-in under `/etc/ssh/sshd_config.d/` |
| `delete_directive_dropin(dropin_name, directive)` → `SshdActionResult` | Remove a directive line from a named drop-in; delete file if empty |
| `list_dropins()` → `list[SshdDropIn]` | Enumerate all drop-in files with parsed directive/value pairs |
| `write_match_block(dropin_name, match_spec, directive_value_pairs)` → `SshdActionResult` | Write a `Match` block drop-in |
| `remove_match_block(dropin_name)` → `SshdActionResult` | Remove a `Match` block drop-in |
| `validate_config()` → `SshdValidateResult` | Invoke `sshd -t` and return parsed result |
| `reload_config()` → `SshdActionResult` | Validate then `systemctl reload sshd` |
| `generate_host_key(key_type, force=False)` → `SshdActionResult` | Generate a host key of type `rsa`, `ecdsa`, or `ed25519` |
| `remove_host_key(key_type)` → `SshdActionResult` | Delete the host-key pair for a type |
| `list_host_key_fingerprints()` → `list[HostKeyFingerprint]` | Enumerate host keys and compute fingerprints |
| `ensure_banner(path, text, set_directive=False)` → `SshdActionResult` | Ensure banner file exists with given content; optionally set `Banner` directive |

### `SshServerManagerConfig`

**Location:** `source/packages/ccai/sysman/linux/redhat/sshserverconfiguration.py`

A dataclass holding runtime configuration for `SshServerManager`.

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no mutating commands or file writes are executed |
| `elevated` | `bool` | `False` | When `True`, mutating commands and privileged reads use the runner's elevation mechanism |

---

## Data Models

### `SshdActionStatus`

```python
class SshdActionStatus(Enum):
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"   # already in desired state, no action needed
    DRY_RUN = "dry_run"   # would have acted, but dry_run=True
```

### `SshdActionResult`

Returned by all write operations (drop-in set/delete, match block write/remove,
reload, host-key generate/remove, banner ensure).

```python
@dataclass
class SshdActionResult:
    status: SshdActionStatus
    message: str | None            # human-readable note (e.g. "reload required")
    exit_code: int | None          # raw exit code from command runner, if any
    stdout: str | None
    stderr: str | None
    command: list[str] | None      # command that was (or would have been) run
    path: str | None               # file path touched, if any
```

### `SshdServiceState`

Returned by `get_service_state()`.

```python
@dataclass
class SshdServiceState:
    active: bool | None            # None when present=False
    enabled: bool | None           # None when present=False
    present: bool                  # False when unit is not known to systemd
    main_pid: int | None           # None when inactive or not available
```

### `SshdDropIn`

Returned as elements of `list_dropins()`.

```python
@dataclass
class SshdDropIn:
    name: str                                 # file stem, e.g. "50-hardening"
    path: str                                 # full path under /etc/ssh/sshd_config.d/
    directive_value_pairs: list[tuple[str, str]]
    valid: bool
    error: str | None                         # parse error when valid=False
```

### `SshdValidateResult`

Returned by `validate_config()`.

```python
@dataclass
class SshdValidateResult:
    ok: bool
    error_output: str | None       # captured stderr when ok=False
```

### `HostKeyFingerprint`

Returned as elements of `list_host_key_fingerprints()`.

```python
@dataclass
class HostKeyFingerprint:
    type: str                      # "rsa", "ecdsa", "ed25519", ...
    bits: int | None
    sha256_fingerprint: str | None # "SHA256:AAAAA..."
    comment: str | None
    valid: bool
    error: str | None              # captured error when valid=False
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Drop-In Set Byte-Identical Skip

*For any* drop-in name, directive, and value, calling
`set_directive_dropin()` when the drop-in file already contains a
byte-identical `<directive> <value>` line should return `SKIPPED` without
invoking the file operator's write or the command runner.

**Validates: Requirements 3.2**

### Property 2: Set-then-Validate Rollback

*For any* `set_directive_dropin()` or `write_match_block()` call whose
resulting configuration fails `sshd -t`, the file operator's rollback
mechanism should restore the previous content and the result status should be
`FAILED`.

**Validates: Requirements 3.4, 5.4**

### Property 3: Delete Absent Directive Skip

*For any* drop-in name and directive, calling `delete_directive_dropin()` when
the drop-in file does not contain the directive (or does not exist) should
return `SKIPPED` without invoking the file operator's write or delete.

**Validates: Requirements 3.3**

### Property 4: List Stability

*For any* filesystem state under `/etc/ssh/sshd_config.d/`, two consecutive
calls to `list_dropins()` should return equivalent `SshdDropIn` records in the
same order when no files have changed between calls.

**Validates: Requirements 4.1, 4.2**

### Property 5: Match Block Byte-Identical Skip

*For any* match spec and directive-value pair set, calling
`write_match_block()` when the drop-in file already represents the same match
spec and directive set byte-for-byte should return `SKIPPED` without
invoking the file operator's write.

**Validates: Requirements 5.3**

### Property 6: Reload Validates First

*For any* call to `reload_config()`, the `sshd -t` validate command should be
executed before `systemctl reload sshd`. When validation fails, the reload
command should not be invoked and the result status should be `FAILED`.

**Validates: Requirements 7.1, 7.2**

### Property 7: Host-Key Generation Respects force=False

*For any* key type, calling `generate_host_key(key_type, force=False)` when
`/etc/ssh/ssh_host_<type>_key` already exists should return `SKIPPED` without
invoking `ssh-keygen`. With `force=True`, the existing private and public key
files should be deleted before `ssh-keygen` is invoked.

**Validates: Requirements 8.2, 8.3**

### Property 8: Host-Key File Modes

*For any* successful `generate_host_key()` call, the resulting private key
file should have mode `0600` and the public key file should have mode `0644`,
both owned by `root:root`.

**Validates: Requirements 8.5**

### Property 9: Banner Byte-Identical Skip

*For any* path and text, calling `ensure_banner()` when the file at `path`
already contains byte-identical content should return `SKIPPED` without
invoking the file operator's write.

**Validates: Requirements 10.2**

### Property 10: Dry-Run Safety

*For any* mutating operation (drop-in set/delete, match block write/remove,
reload, host-key generate/remove, banner ensure) with `dry_run=True`, neither
the command runner's mutating commands nor the file operator's write or
delete should be invoked, and the result status should be `DRY_RUN`.

**Validates: Requirements 3.6, 5.5, 7.5, 8.7, 10.4**

### Property 11: Elevation Consistency

*For any* mutating operation or privileged read (`sshd -T`, `sshd -t`,
`systemctl reload sshd`, `ssh-keygen` on `/etc/ssh/`, any write/delete under
`/etc/ssh/`) with `elevated=True` in the config, the command runner and file
operator should be invoked with `elevated=True`. For read-only file operator
queries (listing drop-ins, reading banner), elevation should not be used.

**Validates: Requirements 11.1, 11.2, 11.3, 11.4**

### Property 12: Service State Mapping

*For any* combination of `systemctl is-active sshd` and `systemctl is-enabled
sshd` results, the returned `SshdServiceState` should correctly reflect the
underlying service state: `active=True` iff `is-active` returned `active`,
`enabled=True` iff `is-enabled` returned `enabled`, and `present=False` iff
the unit is not known to systemd.

**Validates: Requirements 1.1, 1.2, 1.3**

---

## Interfaces

### `ICommandRunner` (from `codecraftai-interfaces`)

`SshServerManager` depends on this interface, injected at construction time.

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

`SshServerManager` depends on this interface, injected at construction time.

```python
class IFileOperator(Protocol):
    def read(self, path: str, elevated: bool = False) -> bytes: ...
    def write(
        self,
        path: str,
        content: bytes,
        mode: int | None = None,
        owner: str | None = None,
        group: str | None = None,
        elevated: bool = False,
    ) -> FileOpResult: ...
    def delete(self, path: str, elevated: bool = False) -> FileOpResult: ...
    def exists(self, path: str, elevated: bool = False) -> bool: ...
    def list_dir(self, path: str, elevated: bool = False) -> list[str]: ...
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

When `SshServerManagerConfig.elevated` is `True`, the `SshServerManager`
passes `elevated=True` to both `ICommandRunner.run()` and the mutating
`IFileOperator` methods. The injected runner and file operator are responsible
for prepending `sudo` or using whatever privilege-escalation mechanism is
appropriate for the environment. `SshServerManager` has no knowledge of how
elevation is implemented.

Read-only file-operator calls that do not require elevation (e.g. listing
`/etc/ssh/sshd_config.d/`, reading the banner file) are always invoked with
`elevated=False`. The `sshd -T` and `sshd -t` commands always receive
`elevated=True` regardless of operation type because the daemon reads the
host-key files during both operations.

### Rollback Mechanism

Before any write under `/etc/ssh/sshd_config.d/`, `SshServerManager` reads the
current content (if any) via `IFileOperator.read()` and holds it in memory for
the duration of the method call. After the write completes, `sshd -t` is
invoked. If validation fails, the original content is restored via a second
`IFileOperator.write()` (or `delete()` when the file did not previously
exist). The result status is `FAILED` and the validation stderr is captured
in `SshdActionResult.stderr`.

---

## Command Mappings

| Operation | Command |
|---|---|
| Query service active | `systemctl is-active sshd` |
| Query service enabled | `systemctl is-enabled sshd` |
| Query service presence | `systemctl list-unit-files sshd.service` |
| Reload service | `systemctl reload sshd` |
| Effective full config (elevated) | `sshd -T` |
| Effective directive (elevated) | `sshd -T` (then parse for directive) |
| Validate configuration (elevated) | `sshd -t` |
| Generate host key | `ssh-keygen -t <type> -f /etc/ssh/ssh_host_<type>_key -N ''` |
| Fingerprint public key | `ssh-keygen -l -f /etc/ssh/ssh_host_<type>_key.pub` |
| Write drop-in | `IFileOperator.write('/etc/ssh/sshd_config.d/<name>.conf', ..., mode=0o600, owner='root', group='root', elevated=True)` |
| Delete drop-in | `IFileOperator.delete('/etc/ssh/sshd_config.d/<name>.conf', elevated=True)` |
| List drop-ins | `IFileOperator.list_dir('/etc/ssh/sshd_config.d/', elevated=False)` |
| Write banner | `IFileOperator.write(<path>, ..., mode=0o644, owner='root', group='root', elevated=True)` |
| Write host key mode | `IFileOperator.stat`/`chmod` → private `0o600`, public `0o644` |
| Delete host key pair | `IFileOperator.delete('/etc/ssh/ssh_host_<type>_key', elevated=True)` + `.pub` |

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `SshdQueryError` | `sshd -T`, `systemctl is-active sshd`, or `systemctl is-enabled sshd` returns an unexpected non-zero exit code for a reason other than "unit not present" |
| `SshdNotInstalledError` | `sshd` binary is not installed (invoked during validate) |

All other failures are surfaced as an `SshdActionResult` with `status=FAILED`
so the coordinator can decide how to respond without catching exceptions for
normal operational failures.

### Validate-Then-Write-Rollback Pattern

Mutating drop-in writes (`set_directive_dropin`, `delete_directive_dropin`,
`write_match_block`, `remove_match_block`) follow this sequence:

1. Read the current file content via `IFileOperator.read()` (or record that
   the file did not exist).
2. Compute the new content. If it is byte-identical to the current content,
   return `SKIPPED` without any further action.
3. Write the new content via `IFileOperator.write()`.
4. Invoke `sshd -t` via `ICommandRunner.run()`.
5. If `sshd -t` fails, restore the previous content (or delete the file if it
   did not previously exist). Return `SshdActionResult` with status `FAILED`
   and the validation stderr.
6. If `sshd -t` passes, return `SshdActionResult` with status `SUCCESS` and a
   message indicating that `systemctl reload sshd` is required for the change
   to take effect.

This ensures that a failed validation never leaves the system in a broken
state, even if a later reload or restart would have picked up the bad file.

### Service-Not-Present Handling

`systemctl is-active sshd` and `systemctl is-enabled sshd` both return a
non-zero exit code with a well-known stdout value (e.g. `not-found`) when the
unit is not known to systemd. `SshServerManager` treats this as
`present=False` and does not raise. Any other non-zero exit code raises
`SshdQueryError`.

---

## Module Layout

```
source/packages/ccai/sysman/linux/redhat/
├── __init__.py
└── sshserverconfiguration.py        ← SshServerManager, config, models, exceptions

source/testroots/testbase/
└── ccai/tests/sysman/linux/redhat/
    └── test_sshserverconfiguration.py  ← unit + property tests using mocks
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` and a `MockFileOperator` injected into
  `SshServerManager` at construction time.
- Tests verify the *arguments* passed to the runner and file operator, not
  real system state.
- Each public method has at minimum: a success path test, a skip/idempotency
  test, a failure path test, and a dry-run test.
- `reload_config()` has an additional test verifying that validate runs
  before reload and that reload is skipped when validate fails.
- `generate_host_key()` has an additional test for `force=True` deleting the
  existing pair before `ssh-keygen` is invoked.
- `ensure_banner()` has an additional test for the optional `set_directive`
  path delegating to `set_directive_dropin()`.
- No test requires root access, a live sshd, or a live systemd.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing
in Python. Each correctness property defined above will be implemented as a
property-based test that:

- Generates random but valid inputs (directive names, values, drop-in names,
  key types, service states).
- Executes the operation under test.
- Verifies the property holds across all generated inputs.
- Runs a minimum of 100 iterations per property test.

Property-based tests are tagged with comments referencing the specific
correctness property they validate, using the format:
`# Feature: ssh-server-configuration, Property N: <property description>`

**Property Test Coverage:**

- Property 1, 5, 9: Byte-identical skip tests generate random file contents
  and verify no write occurs when content matches.
- Property 2: Rollback tests simulate `sshd -t` failure and verify the
  original content is restored.
- Property 3: Delete-absent tests generate drop-ins missing the target
  directive and verify `SKIPPED`.
- Property 4: List stability tests verify equivalent results across
  consecutive calls with unchanged filesystem state.
- Property 6: Reload-validate-first tests verify ordering of runner calls.
- Property 7: Host-key force tests verify skip vs. delete-then-generate
  behaviour.
- Property 8: Mode tests verify private `0o600` and public `0o644` across
  random key types.
- Property 10: Dry-run safety tests verify no mutating runner or file
  operator call for any mutating method.
- Property 11: Elevation tests verify elevated flag propagation on mutating
  and privileged-read operations.
- Property 12: Service state tests verify correct mapping across random
  combinations of systemctl outputs.

Both unit tests and property-based tests are complementary: unit tests verify
specific examples and edge cases, while property tests verify universal
correctness across many inputs.

---

## Usage Examples

### Local Execution — Disable Root Login via Drop-In

```python
from ccai.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from ccai.sysman.linux.redhat.fileops import LocalFileOperator, LocalFileOperatorConfig
from ccai.sysman.linux.redhat.sshserverconfiguration import (
    SshServerManager,
    SshServerManagerConfig,
    SshdActionStatus,
)

# Create local runner and file operator
runner = LocalCommandRunner(LocalRunnerConfig())
fileop = LocalFileOperator(LocalFileOperatorConfig())

# Inject into ssh server manager
config = SshServerManagerConfig(elevated=True, dry_run=False)
ssh_mgr = SshServerManager(runner, fileop, config)

# Set PermitRootLogin no via a drop-in; set_directive_dropin validates via sshd -t
# and rolls back on failure.
result = ssh_mgr.set_directive_dropin(
    dropin_name="50-harden",
    directive="PermitRootLogin",
    value="no",
)
print(f"Set directive: {result.status} — {result.message}")

# Explicit validate + reload so the change takes effect.
validate = ssh_mgr.validate_config()
if validate.ok is True:
    reload_result = ssh_mgr.reload_config()
    print(f"Reload: {reload_result.status}")
else:
    print(f"Validate failed: {validate.error_output}")
```

### SSH Execution with Reliability Tracking — Rotate Host Keys Across a Fleet

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.fileops import SSHFileOperator, SSHFileOperatorConfig
from ccai.sysman.linux.redhat.sshserverconfiguration import (
    SshServerManager,
    SshServerManagerConfig,
)

hosts = ["192.168.1.100", "192.168.1.101", "192.168.1.102"]
fingerprints_by_host: dict[str, list] = {}

for host in hosts:
    ssh_runner_config = SSHRunnerConfig(
        host=host,
        username="admin",
        key_filename="/home/user/.ssh/id_rsa",
        max_connection_retries=5,
        keepalive_interval=60.0,
    )
    runner = SSHCommandRunner(ssh_runner_config)
    fileop = SSHFileOperator(SSHFileOperatorConfig(runner=runner))

    config = SshServerManagerConfig(elevated=True)
    ssh_mgr = SshServerManager(runner, fileop, config)

    # Rotate the ed25519 host key (force=True to overwrite in place).
    gen_result = ssh_mgr.generate_host_key(key_type="ed25519", force=True)
    print(f"[{host}] generate: {gen_result.status}")

    # Reload so the new key is used.
    reload_result = ssh_mgr.reload_config()
    print(f"[{host}] reload: {reload_result.status}")

    # Collect the new fingerprints for the inventory.
    fingerprints = ssh_mgr.list_host_key_fingerprints()
    fingerprints_by_host[host] = fingerprints

    # Report SSH reliability metrics.
    metrics = runner.get_metrics()
    total = metrics.success_count + metrics.failure_count
    success_rate = (metrics.success_count / total) if total > 0 else 0.0
    print(f"[{host}] success rate: {success_rate:.2%}")
```

### SSH Execution with Aspects — Applying a Baseline Hardening Drop-In

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.fileops import SSHFileOperator, SSHFileOperatorConfig
from ccai.sysman.linux.redhat.aspects import (
    PersistenceAspect, RetryAspect, TimeoutAspect,
    PersistenceAspectConfig, RetryAspectConfig, TimeoutAspectConfig,
)
from ccai.sysman.linux.redhat.storage import SQLiteStorage
from ccai.sysman.linux.redhat.sshserverconfiguration import (
    SshServerManager,
    SshServerManagerConfig,
)

# Create SSH runner + file operator.
ssh_runner_config = SSHRunnerConfig(host="remote-server", username="admin")
runner = SSHCommandRunner(ssh_runner_config)
fileop = SSHFileOperator(SSHFileOperatorConfig(runner=runner))

# Add persistence aspect for audit logging.
storage = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

# Add retry aspect for transient failures (e.g. network glitches).
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[255],
    retryable_error_patterns=["Connection reset", "Temporary failure"],
))
runner.add_aspect(retry)

# Add timeout aspect for global policy.
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=120.0))
runner.add_aspect(timeout)

# Inject into ssh server manager.
config = SshServerManagerConfig(elevated=True)
ssh_mgr = SshServerManager(runner, fileop, config)

# Apply a baseline hardening drop-in. Every sshd/systemctl/ssh-keygen call now:
# - is audit-logged to SQLite
# - retries on transient SSH failures
# - obeys the 2-minute timeout
baseline_directives = [
    ("PermitRootLogin", "no"),
    ("PasswordAuthentication", "no"),
    ("X11Forwarding", "no"),
]
for directive, value in baseline_directives:
    result = ssh_mgr.set_directive_dropin(
        dropin_name="50-harden",
        directive=directive,
        value=value,
    )
    print(f"{directive}={value}: {result.status}")

# Validate once at the end and reload.
validate_result = ssh_mgr.validate_config()
if validate_result.ok is True:
    reload_result = ssh_mgr.reload_config()
    print(f"Reload: {reload_result.status}")
else:
    print(f"Validate failed: {validate_result.error_output}")
```

### Dry-Run Mode Across Transports

```python
# Dry-run works identically for local and SSH. No commands execute, no files
# are written, and every mutating method returns SshdActionStatus.DRY_RUN.
config = SshServerManagerConfig(elevated=True, dry_run=True)
ssh_mgr = SshServerManager(runner, fileop, config)

result = ssh_mgr.set_directive_dropin(
    dropin_name="50-harden",
    directive="PermitRootLogin",
    value="no",
)
assert result.status == SshdActionStatus.DRY_RUN
```
