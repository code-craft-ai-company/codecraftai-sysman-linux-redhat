# Design

## Overview

The cockpit management feature is implemented as a single module,
`ccai.sysman.linux.redhat.cockpitmanagement`, that exposes a `CockpitManager` class.
The coordinator instantiates `CockpitManager` and calls its methods to query Cockpit
state, manage the `cockpit.socket` lifecycle, enumerate installed Cockpit modules under
`/usr/share/cockpit/`, configure the listen port through a systemd socket drop-in, edit
`/etc/cockpit/cockpit.conf`, install a login banner file, and query the installed
Cockpit version.

All command-line interaction (`systemctl`, `cockpit-bridge`) is delegated to a command
runner implementing the `ICommandRunner` interface. All filesystem interaction under
`/etc/cockpit/`, `/etc/systemd/system/cockpit.socket.d/`, `/usr/lib/systemd/system/`,
and `/usr/share/cockpit/` is delegated to a file operator implementing the
`IFileOperator` interface. The `CockpitManager` never calls `subprocess`, `open()`, or
direct filesystem APIs; it only constructs argument lists and file paths and passes
them to the injected abstractions. This keeps business logic separate from execution
and makes the feature fully testable with mock runners and mock file operators.

`CockpitManager` does not install the Cockpit RPMs, does not open firewall ports, and
does not issue `systemctl daemon-reload` or restart commands on its own. Instead it
composes with three peer features that the coordinator wires together:

- **`package-management`** is responsible for installing or updating the `cockpit`
  and `cockpit-*` RPM packages that bring `/usr/share/cockpit/` content and the
  `cockpit.socket` unit onto the system. `CockpitManager` assumes the packages are
  present and raises `CockpitNotInstalledError` when they are not.
- **`firewall-management`** is responsible for opening or closing the TCP port on
  which Cockpit listens. When `CockpitManager.set_listen_port()` changes the port,
  the coordinator is expected to call the firewall feature to synchronise the
  firewall rule set.
- **`service-management`** is responsible for `systemctl daemon-reload` and for the
  socket restart that must follow a drop-in write. `CockpitManager.set_listen_port()`
  and `CockpitManager.remove_listen_dropin()` write or delete the drop-in file only;
  the coordinator calls the service-management feature to pick up the change.

The feature works identically whether commands and file operations execute locally
(via `LocalCommandRunner` + `LocalFileOperator`) or remotely over SSH (via
`SSHCommandRunner` + `SSHFileOperator`). When using SSH, the coordinator can track
reliability metrics and apply execution aspects (persistence, retry, timeout) without
any changes to the `CockpitManager` code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md)
for details on local/SSH execution, reliability tracking, and aspect configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner + file operator (Local or SSH)      │
│  - Configures aspects (optional)                           │
│  - Wires CockpitManager with peer features                 │
│    (package-management, firewall-management,               │
│     service-management)                                    │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            CockpitManager                                   │
│  ccai.sysman.linux.redhat.cockpitmanagement             │
│                                                             │
│  get_socket_state()    lifecycle_action()                   │
│  list_modules()        get_listen_port()                    │
│  set_listen_port()     remove_listen_dropin()               │
│  get_config()          set_config()     delete_config()     │
│  install_banner()      get_version()                        │
└──────────┬───────────────────────────────┬──────────────────┘
           │ delegates commands to         │ delegates file I/O to
           ▼                               ▼
┌──────────────────────┐          ┌──────────────────────────┐
│   ICommandRunner     │          │      IFileOperator       │
│ run(args, elevated,  │          │ read(), write(), exists,│
│     cwd, env,        │          │ delete(), glob(),       │
│     timeout)         │          │ chmod(), chown()         │
│     → RunResult      │          │                          │
└──────────┬───────────┘          └────────────┬─────────────┘
           │                                   │
   ┌───────┴────────┐                  ┌───────┴────────┐
   ▼                ▼                  ▼                ▼
┌─────────────┐ ┌─────────────┐ ┌──────────────┐ ┌──────────────┐
│ LocalCommand│ │ SSHCommand  │ │ LocalFile    │ │ SSHFile      │
│ Runner      │ │ Runner      │ │ Operator     │ │ Operator     │
│ subprocess  │ │ SSH+metrics │ │ local FS     │ │ SFTP/SSH     │
└──────┬──────┘ └──────┬──────┘ └──────┬───────┘ └──────┬───────┘
       │               │               │                │
       └───────┬───────┘               └────────┬───────┘
               │ optional                       │ optional
               ▼                                ▼
┌─────────────────────────────────────────────────────────────┐
│                    Execution Aspects                        │
│  - PersistenceAspect: audit logging                         │
│  - RetryAspect: transient failure retry                     │
│  - TimeoutAspect: global timeout policy                     │
└─────────────────────────────────────────────────────────────┘
```

---

## Components

### `CockpitManager`

**Location:** `source/packages/ccai/sysman/linux/redhat/cockpitmanagement.py`

The primary public class. Constructed with an `ICommandRunner`, an `IFileOperator`,
and a `CockpitManagerConfig`.

| Method | Description |
|---|---|
| `get_socket_state()` → `CockpitSocketState` | Query `active`, `enabled`, `present`, and effective listen entries for `cockpit.socket` |
| `lifecycle_action(action)` → `CockpitActionResult` | Perform one of `start`, `stop`, `restart`, `enable`, `disable` on `cockpit.socket` with idempotency guard |
| `list_modules()` → `list[CockpitModule]` | Enumerate and parse `/usr/share/cockpit/*/manifest.json` |
| `get_listen_port()` → `CockpitListen` | Report effective `(address, port)` entries via `systemctl show` and unit/drop-in files |
| `set_listen_port(address, port)` → `CockpitActionResult` | Write `/etc/systemd/system/cockpit.socket.d/listen.conf` with the requested address and port |
| `remove_listen_dropin()` → `CockpitActionResult` | Delete the `listen.conf` drop-in if present |
| `get_config(section, key)` → `str \| None` | Read a key from `/etc/cockpit/cockpit.conf` |
| `set_config(section, key, value)` → `CockpitActionResult` | Set or replace an INI key, preserving comments and ordering |
| `delete_config(section, key)` → `CockpitActionResult` | Remove an INI key; remove the section header if the section becomes empty |
| `install_banner(text, path)` → `CockpitActionResult` | Write a banner file with byte-identical skip behaviour |
| `get_version()` → `str` | Return the captured output of `cockpit-bridge --version` |

### `CockpitManagerConfig`

**Location:** `source/packages/ccai/sysman/linux/redhat/cockpitmanagement.py`

A dataclass holding runtime configuration for `CockpitManager`.

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no commands are executed and no files are written or deleted |
| `elevated` | `bool` | `False` | When `True`, mutating commands and file operations are invoked with elevation |

---

## Data Models

### `CockpitActionStatus`

```python
class CockpitActionStatus(Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"   # already in desired state, no action needed
    DRY_RUN  = "dry_run"   # would have acted, but dry_run=True
```

### `CockpitActionResult`

Returned by all write operations (lifecycle, set/remove listen drop-in, set/delete
config, install banner).

```python
@dataclass
class CockpitActionResult:
    status: CockpitActionStatus
    target: str                 # logical target: "cockpit.socket", path, or "section.key"
    exit_code: int | None       # raw exit code from command runner, when applicable
    stdout: str | None
    stderr: str | None
    command: list[str] | None   # the command that was (or would have been) run
    path: str | None            # the file path that was (or would have been) touched
    note: str | None            # e.g. "restart cockpit.socket for effect"
```

### `CockpitSocketState`

Returned by `get_socket_state()`.

```python
@dataclass
class CockpitSocketState:
    present: bool
    active: bool | None          # None when present=False
    enabled: bool | None         # None when present=False
    listen: "CockpitListen | None"  # None when present=False
```

### `CockpitModule`

Returned as elements of `list_modules()`.

```python
@dataclass
class CockpitModule:
    name: str
    version: str | None
    priority: int | None
    requires: dict[str, str]     # empty dict when no requirements
    path: str                    # absolute path to the manifest.json
    valid: bool                  # False when the manifest failed to parse
    error: str | None            # populated when valid=False
```

### `CockpitListen`

Returned by `get_listen_port()` and embedded in `CockpitSocketState`.

```python
@dataclass
class CockpitListen:
    entries: list[tuple[str, int]]   # list of (address, port)
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Lifecycle Idempotency

*For any* lifecycle action (`start`, `stop`, `restart`, `enable`, `disable`), when the
socket is already in the requested state, `lifecycle_action()` shall return `SKIPPED`
without invoking `systemctl <action> cockpit.socket`. (`restart` is never skipped; it
always performs the command when the socket is active.)

**Validates: Requirements 2.2**

### Property 2: Listen Drop-In Byte-Identical Skip

*For any* invocation of `set_listen_port(address, port)`, when the existing
`/etc/systemd/system/cockpit.socket.d/listen.conf` contains byte-identical content to
the content that would be written, the write shall be skipped and the result status
shall be `SKIPPED`.

**Validates: Requirements 5.2**

### Property 3: Listen Drop-In Remove When Absent

*For any* invocation of `remove_listen_dropin()`, when
`/etc/systemd/system/cockpit.socket.d/listen.conf` does not exist on disk, the file
operator's `delete()` shall not be invoked and the result status shall be `SKIPPED`.

**Validates: Requirements 5.4**

### Property 4: Config Set Idempotency With Comment Preservation

*For any* invocation of `set_config(section, key, value)`, when the current value of
the key already equals the requested value the write shall be skipped and the result
status shall be `SKIPPED`. When a write does occur, comments and the ordering of
unrelated keys in `cockpit.conf` shall be preserved.

**Validates: Requirements 6.2, 6.4**

### Property 5: Config Delete With Empty Section Removal

*For any* invocation of `delete_config(section, key)`, when the key is the last
remaining key in its section, both the key line and the `[section]` header shall be
removed from `/etc/cockpit/cockpit.conf`.

**Validates: Requirements 6.3**

### Property 6: Banner Byte-Identical Skip

*For any* invocation of `install_banner(text, path)`, when the existing file at
`path` contains byte-identical content to `text`, the file operator's `write()` shall
not be invoked and the result status shall be `SKIPPED`.

**Validates: Requirements 7.2**

### Property 7: Module Manifest Parse-Error Tolerance

*For any* invocation of `list_modules()`, a malformed or unreadable `manifest.json`
shall produce a `CockpitModule` record with `valid=False` and a populated `error`
field rather than aborting the enumeration or raising an exception.

**Validates: Requirements 3.3**

### Property 8: Dry-Run Safety

*For any* write operation (`lifecycle_action`, `set_listen_port`,
`remove_listen_dropin`, `set_config`, `delete_config`, `install_banner`) with
`dry_run=True`, the command runner shall never be invoked with a mutating command,
the file operator's `write()`/`delete()`/`chmod()`/`chown()` shall never be invoked,
and the result status shall be `DRY_RUN`.

**Validates: Requirements 2.5, 5.5, 6.6, 7.4**

### Property 9: Elevation Consistency

*For any* write operation with `elevated=True` in the config, the command runner and
file operator shall be invoked with `elevated=True`; for read operations
(`get_socket_state`, `list_modules`, `get_listen_port`, `get_config`, `get_version`),
`elevated` shall never be `True`.

**Validates: Requirements 9.1, 9.2, 9.3**

### Property 10: Status Mapping Correctness

*For any* operation result from the command runner, the `CockpitActionResult.status`
shall correctly reflect the runner's exit code: `0` maps to `SUCCESS`, non-zero maps
to `FAILED` (unless the operation was skipped or in dry-run mode).

**Validates: Requirements 2.3, 2.4**

### Property 11: Version Query Not-Installed Mapping

*For any* invocation of `get_version()`, when the command runner reports that the
`cockpit-bridge` binary is not found (for example exit code `127` or an explicit
"command not found" signal), `CockpitNotInstalledError` shall be raised; any other
non-zero exit code shall raise `CockpitQueryError`.

**Validates: Requirements 8.3, 8.4**

---

## Interfaces

### `ICommandRunner` (from `codecraftai-interfaces`)

`CockpitManager` depends on this interface, injected at construction time.

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

`CockpitManager` depends on this interface for all filesystem interaction under
`/etc/cockpit/`, `/etc/systemd/system/cockpit.socket.d/`,
`/usr/lib/systemd/system/`, and `/usr/share/cockpit/`.

```python
class IFileOperator(Protocol):
    def exists(self, path: str) -> bool: ...
    def read(self, path: str) -> bytes: ...
    def write(
        self,
        path: str,
        content: bytes,
        mode: int = 0o644,
        owner: str | None = None,
        group: str | None = None,
        elevated: bool = False,
    ) -> None: ...
    def delete(self, path: str, elevated: bool = False) -> None: ...
    def glob(self, pattern: str) -> list[str]: ...
    def makedirs(
        self,
        path: str,
        mode: int = 0o755,
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

When `CockpitManagerConfig.elevated` is `True`, the `CockpitManager` passes
`elevated=True` to both `ICommandRunner.run()` and `IFileOperator.write()` /
`IFileOperator.delete()` / `IFileOperator.makedirs()` for mutating operations. The
runner and file operator are responsible for prepending `sudo` or using whatever
privilege escalation mechanism is appropriate for the environment. `CockpitManager`
has no knowledge of how elevation is implemented. Read-only operations never pass
`elevated=True`.

---

## Command Mappings

| Operation | Command or Path |
|---|---|
| Query active state | `systemctl is-active cockpit.socket` |
| Query enabled state | `systemctl is-enabled cockpit.socket` |
| Lifecycle action | `systemctl <action> cockpit.socket` where `<action>` ∈ {`start`, `stop`, `restart`, `enable`, `disable`} |
| Query effective listen | `systemctl show cockpit.socket --property=Listen` |
| Packaged socket unit (read) | `/usr/lib/systemd/system/cockpit.socket` |
| Socket drop-in directory (read / write / delete) | `/etc/systemd/system/cockpit.socket.d/` |
| Socket listen drop-in (read / write / delete) | `/etc/systemd/system/cockpit.socket.d/listen.conf` |
| Cockpit config file (read / write) | `/etc/cockpit/cockpit.conf` |
| Cockpit config directory (create) | `/etc/cockpit/` with mode `0755` |
| Module manifest glob | `/usr/share/cockpit/*/manifest.json` |
| Banner file | Caller-supplied path (e.g. `/etc/cockpit/login.banner`) |
| Version query | `cockpit-bridge --version` |

### Listen Drop-In Content

When `set_listen_port(address, port)` writes the drop-in, the file body is exactly:

```
[Socket]
ListenStream=
ListenStream=<address>:<port>
```

The leading `ListenStream=` line is deliberate: it resets the list of listen
addresses inherited from the packaged unit, so that `<address>:<port>` becomes the
sole entry.

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `CockpitQueryError` | `systemctl is-active/is-enabled/show` or `cockpit-bridge --version` returns an unexpected non-zero exit code, or the fallback unit-file read fails |
| `CockpitNotInstalledError` | `cockpit-bridge` is not present on the target system (e.g. exit code `127` or equivalent not-found signal) |

All other failures are surfaced as a `CockpitActionResult` with `status=FAILED` so
the coordinator can decide how to respond without catching exceptions for normal
operational failures.

### Delegated Operations

`CockpitManager` deliberately does **not** perform `systemctl daemon-reload` or
`systemctl restart cockpit.socket` after writing or removing the listen drop-in, and
does **not** install the Cockpit RPMs. These are the responsibility of the
`service-management` and `package-management` features respectively. The coordinator
is responsible for calling them in sequence. This feature's
`CockpitActionResult.note` field carries a human-readable reminder (for example,
`"run service-management.daemon_reload() and restart cockpit.socket for effect"`)
when such a follow-up is required.

### Idempotency Guards

Before executing mutating commands or writing files, `CockpitManager` reads the
current state via `get_socket_state()`, `get_config()`, or a byte-level compare
through `IFileOperator.read()`. If the system is already in the desired state, the
method returns `SKIPPED` without invoking the runner or file operator. This makes
all write operations safe to call multiple times.

### Module Manifest Tolerance

`list_modules()` never aborts on a single bad manifest. A JSON parse error,
permission error, or missing required field produces a `CockpitModule` record with
`valid=False` and `error` populated. The enumeration continues for the remaining
manifests.

---

## Module Layout

```
source/packages/ccai/sysman/linux/redhat/
├── __init__.py
└── cockpitmanagement.py          ← CockpitManager, config, models, exceptions

source/testroots/testbase/
└── ccai/tests/sysman/linux/redhat/
    └── test_cockpitmanagement.py ← unit tests using MockCommandRunner +
                                    MockFileOperator
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` and a `MockFileOperator` injected into
  `CockpitManager` at construction time.
- Tests verify the *arguments* passed to the runner and the *paths, contents, mode,
  owner, and elevation* passed to the file operator — not real system state.
- Each public method has at minimum: a success path test, a skip/idempotency test,
  a failure path test, and a dry-run test.
- `list_modules()` has additional tests for malformed manifests (`valid=False`
  records) and for a missing `/usr/share/cockpit/` directory (empty list).
- `get_version()` has a dedicated test for the not-installed path mapping to
  `CockpitNotInstalledError`.
- `set_config()` has a dedicated test for comment preservation.
- `delete_config()` has a dedicated test for empty-section removal.
- No test requires root access, a live `systemctl`, or a real Cockpit installation.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in
Python. Each correctness property defined above will be implemented as a
property-based test that:

- Generates random but valid inputs (lifecycle actions, addresses, ports, section
  and key names, values, banner text, manifest payloads, exit codes)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific correctness
property they validate, using the format:
`# Feature: cockpit-management, Property N: <property description>`

**Property Test Coverage:**

- Property 1: Lifecycle idempotency across all five actions and both current states
- Property 2: Listen drop-in byte-identical skip across random address/port pairs
- Property 3: Listen drop-in remove-when-absent across random filesystem states
- Property 4: Config set idempotency with randomised INI documents preserving
  comments
- Property 5: Config delete with empty-section removal across randomised sections
- Property 6: Banner byte-identical skip across randomised banner bodies
- Property 7: Module manifest tolerance across randomly malformed JSON payloads
- Property 8: Dry-run safety across every write method
- Property 9: Elevation flag propagation across every method
- Property 10: Status mapping across randomised exit codes
- Property 11: Version query not-installed vs. query-error mapping across
  randomised exit codes

Both unit tests and property-based tests are complementary: unit tests verify
specific examples and edge cases, while property tests verify universal correctness
across many inputs.

---

## Usage Examples

### Local Execution (Enable Socket, Set Banner, Change Port)

```python
from ccai.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from ccai.sysman.linux.redhat.fileops import LocalFileOperator, LocalFileOperatorConfig
from ccai.sysman.linux.redhat.cockpitmanagement import (
    CockpitManager,
    CockpitManagerConfig,
)

# Create local runner + file operator
runner = LocalCommandRunner(LocalRunnerConfig())
fileop = LocalFileOperator(LocalFileOperatorConfig())

# Inject into cockpit manager
config = CockpitManagerConfig(elevated=True, dry_run=False)
cockpit = CockpitManager(runner, fileop, config)

# Enable the socket so Cockpit listens at boot
enable_result = cockpit.lifecycle_action(action="enable")
print(f"Enable socket: {enable_result.status}")

# Install a login banner file and point cockpit.conf at it
banner_path = "/etc/cockpit/login.banner"
banner_result = cockpit.install_banner(
    text="Authorized users only. All activity is logged.\n",
    path=banner_path,
)
print(f"Install banner: {banner_result.status}")

banner_cfg_result = cockpit.set_config(
    section="Session",
    key="Banner",
    value=banner_path,
)
print(f"Set Session.Banner: {banner_cfg_result.status}")

# Move Cockpit from 9090 to 9443
port_result = cockpit.set_listen_port(address="0.0.0.0", port=9443)
print(f"Set listen port: {port_result.status} (note: {port_result.note})")
```

### SSH Execution with Reliability Tracking (Enable Cockpit Across Fleet)

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.fileops import SSHFileOperator, SSHFileOperatorConfig
from ccai.sysman.linux.redhat.cockpitmanagement import (
    CockpitManager,
    CockpitManagerConfig,
)

fleet = ["192.168.1.101", "192.168.1.102", "192.168.1.103"]

for host in fleet:
    ssh_runner_config = SSHRunnerConfig(
        host=host,
        username="admin",
        key_filename="/home/user/.ssh/id_rsa",
        max_connection_retries=5,
        keepalive_interval=60.0,
    )
    runner = SSHCommandRunner(ssh_runner_config)

    ssh_fileop_config = SSHFileOperatorConfig(
        host=host,
        username="admin",
        key_filename="/home/user/.ssh/id_rsa",
    )
    fileop = SSHFileOperator(ssh_fileop_config)

    cockpit_config = CockpitManagerConfig(elevated=True)
    cockpit = CockpitManager(runner, fileop, cockpit_config)

    # Same code as local — the manager does not know it is SSH
    state = cockpit.get_socket_state()
    if state.present is True and state.enabled is False:
        cockpit.lifecycle_action(action="enable")
    if state.present is True and state.active is False:
        cockpit.lifecycle_action(action="start")

    metrics = runner.get_metrics()
    total = metrics.success_count + metrics.failure_count
    if total > 0:
        print(f"{host}: success rate {metrics.success_count / total:.2%}")
```

### SSH Execution with Aspects (Module Install + Firewall + Enable via Delegation)

This example bundles persistence, retry, and timeout aspects around an orchestrated
sequence that spans three peer features: `package-management` installs the Cockpit
RPMs, `firewall-management` opens the port, and `service-management` performs the
`daemon-reload` and socket restart after `CockpitManager` writes the listen drop-in.

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.fileops import SSHFileOperator, SSHFileOperatorConfig
from ccai.sysman.linux.redhat.aspects import (
    PersistenceAspect, PersistenceAspectConfig,
    RetryAspect, RetryAspectConfig,
    TimeoutAspect, TimeoutAspectConfig,
)
from ccai.sysman.linux.redhat.storage import SQLiteStorage
from ccai.sysman.linux.redhat.packagemanagement import (
    PackageManager, PackageManagerConfig,
)
from ccai.sysman.linux.redhat.firewallmanagement import (
    FirewallManager, FirewallManagerConfig,
)
from ccai.sysman.linux.redhat.servicemanagement import (
    ServiceManager, ServiceManagerConfig,
)
from ccai.sysman.linux.redhat.cockpitmanagement import (
    CockpitManager, CockpitManagerConfig,
)

# Shared transports
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))
fileop = SSHFileOperator(SSHFileOperatorConfig(host="remote-server", username="admin"))

# Aspects applied once on the runner, shared across every feature
storage = SQLiteStorage("/var/log/maintenance/commands.db")
runner.add_aspect(PersistenceAspect(PersistenceAspectConfig(storage_backend=storage)))
runner.add_aspect(RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[1, 255],
    retryable_error_patterns=["Connection reset", "Temporary failure"],
)))
runner.add_aspect(TimeoutAspect(TimeoutAspectConfig(default_timeout=300.0)))

# Peer features
pkg_mgr = PackageManager(runner, PackageManagerConfig(elevated=True))
fw_mgr = FirewallManager(runner, FirewallManagerConfig(elevated=True))
svc_mgr = ServiceManager(runner, ServiceManagerConfig(elevated=True))
cockpit = CockpitManager(runner, fileop, CockpitManagerConfig(elevated=True))

desired_port = 9443

# 1. Install the Cockpit RPMs (package-management)
pkg_mgr.install(name="cockpit")
pkg_mgr.install(name="cockpit-storaged")

# 2. Open the firewall port (firewall-management)
fw_mgr.add_port(port=desired_port, protocol="tcp", zone="public")

# 3. Write the listen drop-in (cockpit-management)
port_result = cockpit.set_listen_port(address="0.0.0.0", port=desired_port)

# 4. Apply the drop-in (service-management) — NOT cockpit-management
if port_result.status.name in ("SUCCESS",):
    svc_mgr.daemon_reload()
    svc_mgr.restart(unit="cockpit.socket")

# 5. Enable + start cockpit.socket (cockpit-management idempotency guards apply)
cockpit.lifecycle_action(action="enable")
cockpit.lifecycle_action(action="start")

# Every command above picked up persistence, retry, and timeout aspects.
```
