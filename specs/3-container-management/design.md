# Design

## Overview

The container management feature is implemented as a single module,
`automojo.sysman.linux.redhat.containermanagement`, that exposes a `ContainerManager`
class. The coordinator instantiates `ContainerManager` and calls its methods to
perform discrete container, image, registry, and Quadlet actions on a Red Hat
Enterprise Linux host using Podman.

All system interaction is delegated to a command runner implementing the
`ICommandRunner` interface and a file operator implementing the `IFileOperator`
interface. The `ContainerManager` never directly calls `subprocess`, shell commands,
or `open()`; it only constructs command argument lists, TOML/INI bodies, and file
paths, and passes them to the injected runners. This keeps business logic separate
from execution and makes the feature fully testable via mock implementations without
root access, a live Podman daemon, or a writable filesystem.

The feature works identically whether commands execute locally (via
`LocalCommandRunner` and `LocalFileOperator`) or remotely over SSH (via
`SSHCommandRunner` and `SSHFileOperator`). When using SSH, the coordinator can track
reliability metrics and apply execution aspects (persistence, retry, timeout) without
any changes to the `ContainerManager` code.

This feature has narrow, explicit relationships with three sibling features and does
not duplicate their responsibilities:

- **`package-management`** — installs and updates the Podman RPM and any companion
  RPMs (`podman-docker`, `skopeo`, `container-selinux`). `ContainerManager` assumes
  Podman is already installed and never invokes `dnf`.
- **`firewall-management`** — opens firewall ports for containers that publish
  services. `ContainerManager` does not touch `firewalld`; the coordinator calls
  the firewall feature separately after `run_container()` succeeds.
- **`service-management`** — performs `systemctl daemon-reload` and enables,
  starts, stops, and restarts the systemd units that Quadlet generates.
  `ContainerManager` writes or removes the `.container` file only; it never calls
  `systemctl`.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md)
for details on local/SSH execution, reliability tracking, and aspect configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner (Local or SSH)                       │
│  - Instantiates file operator (Local or SSH)                │
│  - Configures aspects (optional)                            │
│  - Injects runner + file operator into ContainerManager     │
│  - Delegates daemon-reload to service-management            │
│  - Delegates port opening to firewall-management            │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            ContainerManager                                 │
│  automojo.sysman.linux.redhat.containermanagement           │
│                                                             │
│  pull_image()    remove_image()    list_images()            │
│  run_container() stop_container()  start_container()        │
│  restart_container()  pause_container()  unpause_container()│
│  kill_container()     remove_container()                    │
│  list_containers()    inspect_container()                   │
│  login_registry()     logout_registry()                     │
│  read_registry_config()  write_registry_dropin()            │
│  remove_registry_dropin()                                   │
│  install_quadlet()    remove_quadlet()                      │
│  prune_containers()   prune_images()   prune_volumes()      │
│  query_info()                                               │
└─────────┬───────────────────────────────────────┬───────────┘
          │ delegates podman argv to              │ delegates reads/writes to
          ▼                                       ▼
┌──────────────────────────────┐      ┌──────────────────────────────┐
│       ICommandRunner         │      │       IFileOperator          │
│  run(args, elevated, ...)    │      │  read_text / write_text      │
│         → RunResult          │      │  remove / stat / exists      │
└──────────────┬───────────────┘      └──────────────┬───────────────┘
               │                                     │
     ┌─────────┴─────────┐                 ┌─────────┴─────────┐
     ▼                   ▼                 ▼                   ▼
┌───────────┐     ┌───────────┐     ┌───────────┐     ┌───────────┐
│  Local    │     │   SSH     │     │  Local    │     │   SSH     │
│  Command  │     │  Command  │     │   File    │     │   File    │
│  Runner   │     │  Runner   │     │  Operator │     │  Operator │
└─────┬─────┘     └─────┬─────┘     └─────┬─────┘     └─────┬─────┘
      │                 │                 │                 │
      └────────┬────────┘                 └────────┬────────┘
               │                                   │
               ▼                                   ▼
        ┌──────────────┐                    ┌──────────────┐
        │   Rootful    │                    │ /etc/        │
        │  elevated=T  │ ─── XOR ──►        │  containers/ │
        │              │                    │              │
        │   Rootless   │                    │ ~/.config/   │
        │  elevated=F  │                    │  containers/ │
        └──────────────┘                    └──────────────┘
               │
               │ optional
               ▼
┌─────────────────────────────────────────────────────────────┐
│                    Execution Aspects                        │
│  - PersistenceAspect: audit logging (with credential redact)│
│  - RetryAspect: transient failure retry                     │
│  - TimeoutAspect: global timeout policy                     │
└─────────────────────────────────────────────────────────────┘
```

The rootless vs rootful branch is selected per operation from
`rootless_default` on the config or an explicit `rootless=` parameter, and controls
both the runner's `elevated` flag and which filesystem prefix the file operator
writes to.

---

## Components

### `ContainerManager`

**Location:** `source/packages/automojo/sysman/linux/redhat/containermanagement.py`

The primary public class. Constructed with an `ICommandRunner`, an `IFileOperator`,
and a `ContainerManagerConfig`.

| Method | Description |
|---|---|
| `pull_image(reference, creds=None, authfile=None, rootless=None)` | Pull an image by reference; skip when digest already present |
| `remove_image(id_or_ref, force=False, rootless=None)` | Remove an image; fail if dependents exist and `force=False` |
| `list_images(rootless=None)` → `list[ContainerImage]` | List local images from `podman images --format json` |
| `run_container(name, image, run_args, replace=False, interactive=False, rootless=None)` → `ContainerActionResult` | Run a detached container; optionally replace a same-named container |
| `stop_container(name, timeout=None, rootless=None)` | Stop a container with optional `-t <n>` |
| `start_container(name, rootless=None)` | Start a stopped container |
| `restart_container(name, rootless=None)` | Restart a running container |
| `pause_container(name, rootless=None)` | Pause a running container |
| `unpause_container(name, rootless=None)` | Unpause a paused container |
| `kill_container(name, signal=None, rootless=None)` | Send a signal to a container |
| `remove_container(name, force=False, rootless=None)` | Remove a container by name |
| `list_containers(rootless=None)` → `list[ContainerInfo]` | List containers from `podman ps -a --format json` |
| `inspect_container(name, rootless=None)` → `ContainerInfo \| None` | Inspect one container by name |
| `login_registry(registry, username, password, rootless=None)` | Login via `--username` + `--password-stdin` |
| `logout_registry(registry, rootless=None)` | Logout from a registry |
| `read_registry_config(rootless=None)` → `RegistryConfig` | Merge base config and drop-ins into a single record |
| `write_registry_dropin(name, body, rootless=None)` | Write a TOML drop-in, byte-identical skip |
| `remove_registry_dropin(name, rootless=None)` | Remove a drop-in file, skip if absent |
| `install_quadlet(name, body, scope)` → `ContainerActionResult` | Write a `.container` file, byte-identical skip |
| `remove_quadlet(name, scope)` | Remove a `.container` file, skip if absent |
| `prune_containers(rootless=None)` | `podman container prune -f` |
| `prune_images(include_unreferenced=False, rootless=None)` | `podman image prune -f [-a]` |
| `prune_volumes(rootless=None)` | `podman volume prune -f` |
| `query_info(rootless=None)` → `PodmanInfo` | `podman info --format json` |

### `ContainerManagerConfig`

**Location:** `source/packages/automojo/sysman/linux/redhat/containermanagement.py`

A dataclass holding runtime configuration for `ContainerManager`.

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no commands or file writes are executed |
| `elevated` | `bool` | `False` | When `True`, rootful mutating commands and `/etc/containers/` writes are elevated |
| `rootless_default` | `bool` | `False` | Per-method `rootless=None` defaults to this value |

---

## Data Models

### `ContainerActionStatus`

```python
class ContainerActionStatus(Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"   # already in desired state, no action needed
    DRY_RUN  = "dry_run"   # would have acted, but dry_run=True
```

### `ContainerActionResult`

Returned by all write operations (pull, remove, run, lifecycle, login/logout,
registry drop-in, Quadlet, prune).

```python
@dataclass
class ContainerActionResult:
    status: ContainerActionStatus
    target: str                   # image ref, container name, drop-in name, etc.
    image_id: str | None          # populated by pull_image / run_container
    container_id: str | None      # populated by run_container
    reclaimed_count: int | None   # populated by prune_*
    reclaimed_bytes: int | None   # populated by prune_*
    exit_code: int | None
    stdout: str | None
    stderr: str | None
    command: list[str] | None     # argv passed to runner, with creds redacted
```

### `ContainerImage`

Returned as elements of `list_images()`.

```python
@dataclass
class ContainerImage:
    id: str
    names: list[str]
    digest: str | None
    created: str
    size: int
```

### `ContainerInfo`

Returned by `inspect_container()` and as elements of `list_containers()`.

```python
@dataclass
class ContainerInfo:
    id: str
    name: str
    image: str
    status: str
    created: str
    started: str | None
    restart_count: int
    ports: list[str]
    mounts: list[str]
    env: dict[str, str]
```

### `PodmanInfo`

Returned by `query_info()`.

```python
@dataclass
class PodmanInfo:
    version: str
    api_version: str
    graph_root: str
    storage_driver: str
    rootless: bool
    cgroup_version: str
    registries: list[str]
```

### `RegistryConfig`

Returned by `read_registry_config()`.

```python
@dataclass
class RegistryEntry:
    location: str
    insecure: bool
    blocked: bool
    mirrors: list[str]


@dataclass
class RegistryConfig:
    unqualified_search: list[str]
    entries: list[RegistryEntry]
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Pull-at-Digest Idempotency

*For any* image reference that pins an exact digest (`repo@sha256:...`), calling
`pull_image()` a second time when that digest is already present locally SHALL
return `SKIPPED` and SHALL NOT invoke the command runner.

**Validates: Requirements 1.4**

### Property 2: Remove Image Without Dependents

*For any* image that has no dependent containers, `remove_image(force=False)` SHALL
invoke `podman rmi` without the `-f` flag; if dependents exist, the method SHALL
return `FAILED` naming those dependents and SHALL NOT invoke removal.

**Validates: Requirements 2.3, 2.4**

### Property 3: Run Conflict Detection

*For any* `run_container()` call where a container with the requested name already
exists, the method SHALL return `FAILED` without invoking `podman run` unless
`replace=True`, in which case it SHALL first invoke `podman rm -f <name>`.

**Validates: Requirements 3.2**

### Property 4: Lifecycle Idempotency

*For any* lifecycle action (`start`, `stop`, `restart`, `pause`, `unpause`, `kill`,
`remove`) where the container is already in the requested terminal state or does
not exist, the method SHALL return `SKIPPED` and SHALL NOT invoke the command
runner.

**Validates: Requirements 4.4, 4.5**

### Property 5: Stop Timeout Propagation

*For any* `stop_container(timeout=n)` call with an integer `n`, the argv passed to
the runner SHALL contain `-t` followed by the string form of `n`; when `timeout` is
`None`, neither token SHALL appear.

**Validates: Requirements 4.2**

### Property 6: Credential Redaction Never Leaks

*For any* call to `login_registry()` or any `pull_image(creds=...)` call, the
recorded `command` on `ContainerActionResult` SHALL NOT contain the password, and
the `stderr` echoed into the result SHALL have the password substring replaced with
`***`. Passwords SHALL be delivered over stdin via `--password-stdin` only.

**Validates: Requirements 1.3, 6.1, 6.3, 6.5**

### Property 7: Registry Drop-in Byte-Identical Skip

*For any* `write_registry_dropin(name, body)` where the existing drop-in at
`/etc/containers/registries.conf.d/<name>.conf` matches `body` byte-for-byte, the
method SHALL return `SKIPPED` without invoking `IFileOperator.write_text`.

**Validates: Requirements 7.3**

### Property 8: Quadlet Byte-Identical Skip

*For any* `install_quadlet(name, body, scope)` where the existing `.container` file
at the scope-correct destination matches `body` byte-for-byte, the method SHALL
return `SKIPPED` without invoking `IFileOperator.write_text`.

**Validates: Requirements 8.3**

### Property 9: Rootless vs Rootful Scope Mismatch

*For any* operation that pairs a rootful command (`rootless=False`) with a user-scope
Quadlet target, or a rootless command with a system-scope Quadlet target, the
method SHALL raise `ContainerScopeMismatchError` before any command or write is
attempted.

**Validates: Requirements 11.3**

### Property 10: Dry-Run Safety

*For any* write operation (pull, rmi, run, lifecycle, rm, login, logout, drop-in
write/delete, Quadlet install/remove, prune) with `dry_run=True`, neither the
command runner nor the file operator SHALL be invoked for a mutating call, and the
result status SHALL be `DRY_RUN`.

**Validates: Requirements 1.7, 2.6, 3.6, 4.7, 6.6, 7.5, 8.5, 9.5**

### Property 11: Elevation Consistency (Rootful Only)

*For any* rootful (`rootless=False`) mutating operation with `config.elevated=True`,
the command runner SHALL be invoked with `elevated=True`; for any rootless
operation the runner SHALL be invoked with `elevated=False` regardless of
`config.elevated`.

**Validates: Requirements 11.1, 11.2, 12.1, 12.2, 12.3**

### Property 12: Status Mapping Correctness

*For any* operation result from the command runner, the `ContainerActionResult`
status SHALL correctly reflect the runner's exit code: `0` maps to `SUCCESS`,
non-zero maps to `FAILED` (unless the operation was skipped or in dry-run mode).

**Validates: Requirements 1.5, 1.6, 3.4, 3.5, 4.6, 6.4, 6.5, 9.4**

### Property 13: JSON Parse Stability

*For any* successful invocation of a `--format json` query (`podman images`,
`podman ps -a`, `podman inspect`, `podman info`), malformed or empty JSON SHALL
raise `ContainerQueryError` and SHALL NOT silently return an empty list.

**Validates: Requirements 5.5, 10.3**

---

## Interfaces

### `ICommandRunner` (from `automojo-interfaces`)

`ContainerManager` depends on this interface, injected at construction time.

```python
class ICommandRunner(Protocol):
    def run(
        self,
        args: list[str],
        elevated: bool = False,
        capture_output: bool = True,
        stdin_text: str | None = None,
    ) -> RunResult: ...
```

### `IFileOperator` (from `automojo-interfaces`)

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
    def remove(self, path: str, elevated: bool = False) -> None: ...
    def exists(self, path: str, elevated: bool = False) -> bool: ...
    def list_dir(self, path: str, elevated: bool = False) -> list[str]: ...
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

When `ContainerManagerConfig.elevated` is `True` AND the operation is rootful,
`ContainerManager` passes `elevated=True` to `ICommandRunner.run()` and to
`IFileOperator.write_text()` for paths under `/etc/containers/`. The runner and the
file operator are responsible for prepending `sudo` or using whatever privilege
escalation mechanism is appropriate for the environment. `ContainerManager` has no
knowledge of how elevation is implemented. Rootless operations (including
user-scope Quadlet writes under `~/.config/containers/systemd/`) always pass
`elevated=False`.

---

## Command Mappings

| Operation | Command |
|---|---|
| Pull image | `podman pull <ref>` |
| Pull with authfile | `podman pull --authfile <path> <ref>` |
| Pull with creds | `podman pull --creds <user>:<pass> <ref>` (redacted in log) |
| List images | `podman images --format json` |
| Remove image | `podman rmi <id-or-ref>` |
| Remove image (force) | `podman rmi -f <id-or-ref>` |
| Run container | `podman run -d --name <name> <structured-args> <image>` |
| Run interactive | `podman run -it --name <name> <structured-args> <image>` |
| Start container | `podman start <name>` |
| Stop container | `podman stop <name>` |
| Stop with timeout | `podman stop -t <n> <name>` |
| Restart container | `podman restart <name>` |
| Pause container | `podman pause <name>` |
| Unpause container | `podman unpause <name>` |
| Kill container | `podman kill <name>` |
| Kill with signal | `podman kill -s <sig> <name>` |
| Remove container | `podman rm <name>` |
| Remove container (force) | `podman rm -f <name>` |
| List containers | `podman ps -a --format json` |
| Inspect container | `podman inspect --format json <name>` |
| Login registry | `podman login --username <user> --password-stdin <registry>` |
| Logout registry | `podman logout <registry>` |
| Prune containers | `podman container prune -f` |
| Prune images | `podman image prune -f` |
| Prune images (all) | `podman image prune -f -a` |
| Prune volumes | `podman volume prune -f` |
| Query info | `podman info --format json` |
| Read registry base | `IFileOperator.read_text("/etc/containers/registries.conf")` |
| Read registry dropins | `IFileOperator.list_dir("/etc/containers/registries.conf.d/")` |
| Write registry dropin | `IFileOperator.write_text("/etc/containers/registries.conf.d/<name>.conf", body, mode=0o644, owner="root", group="root", elevated=True)` |
| Remove registry dropin | `IFileOperator.remove("/etc/containers/registries.conf.d/<name>.conf", elevated=True)` |
| Install system Quadlet | `IFileOperator.write_text("/etc/containers/systemd/<name>.container", body, mode=0o644, elevated=True)` |
| Install user Quadlet | `IFileOperator.write_text("~/.config/containers/systemd/<name>.container", body, mode=0o644, elevated=False)` |
| Remove system Quadlet | `IFileOperator.remove("/etc/containers/systemd/<name>.container", elevated=True)` |
| Remove user Quadlet | `IFileOperator.remove("~/.config/containers/systemd/<name>.container", elevated=False)` |

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `ContainerQueryError` | A `--format json` query (`podman images`, `ps -a`, `inspect`, `info`) fails with an unexpected exit code, or the returned JSON cannot be parsed |
| `ContainerScopeMismatchError` | A rootful command is paired with a user-scope Quadlet target, or a rootless command is paired with a system-scope Quadlet target |

All other failures are surfaced as a `ContainerActionResult` with `status=FAILED`
so the coordinator can decide how to respond without catching exceptions for normal
operational failures.

### Credential Redaction

Credential material never appears on a process argv and never survives into an
audit log. `ContainerManager` guarantees this through three rules:

1. **Password delivery over stdin only.** `login_registry()` sends the password
   through `stdin_text` on `ICommandRunner.run()` together with the
   `--password-stdin` flag. The password is never appended to the argv list.
2. **Argv redaction before logging.** When `pull_image()` is invoked with
   `creds="user:pass"`, the argv recorded on `ContainerActionResult.command` has
   the `:pass` segment replaced with `:***` before it is returned to the caller or
   passed to any aspect. The real argv handed to the runner is identical except
   the runner is instructed (via its own redaction contract) to redact the same
   token when writing to persistence.
3. **Stderr redaction.** Any occurrence of the username or password substring in
   `stderr` is replaced with `***` before being stored on
   `ContainerActionResult.stderr`. Usernames may be kept in argv only when the
   caller explicitly opts in via `log_username=True`; the default is to redact
   them.

The redaction helper is a single private function used by every code path that
handles credentials, so the invariant is enforced in one place.

### JSON Parse Failures

Any `json.JSONDecodeError` or missing required key on a `--format json` response is
reclassified into `ContainerQueryError` with the raw stdout attached for
debuggability.

### Idempotency Guards

Before executing mutating commands, `ContainerManager` queries current state
(`list_images`, `inspect_container`, `read_text` on the destination file). If the
system is already in the desired state the method returns `SKIPPED` without
invoking the runner or the file operator. This makes all write operations safe to
call multiple times.

---

## Module Layout

```
source/packages/automojo/sysman/linux/redhat/
├── __init__.py
└── containermanagement.py          ← ContainerManager, config, models, exceptions

source/testroots/testbase/
└── automojo/tests/sysman/linux/redhat/
    └── test_containermanagement.py ← unit tests using MockCommandRunner + MockFileOperator
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` and a `MockFileOperator` injected into
  `ContainerManager` at construction time.
- Tests verify the *arguments* passed to the runner and the *paths and content*
  passed to the file operator, not system state.
- Each public method has at minimum: a success path test, a skip/idempotency test,
  a failure path test, and a dry-run test.
- `login_registry()` has a dedicated test that asserts the password is never in
  the captured argv and is delivered via `stdin_text`.
- `install_quadlet()` and `write_registry_dropin()` each have a byte-identical
  skip test and a scope-mismatch test.
- `query_info()`, `list_images()`, `list_containers()`, and `inspect_container()`
  each have a malformed-JSON test that asserts `ContainerQueryError`.
- No test requires root access, a live Podman daemon, or a writable filesystem.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in
Python. Each correctness property defined above is implemented as a property-based
test that:

- Generates random but valid inputs (image refs, container names, Quadlet bodies,
  credentials, rootless flags, configurations)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific correctness
property they validate, using the format:
`# Feature: container-management, Property N: <property description>`

**Property Test Coverage:**

- Property 1: Pull-at-digest idempotency — generates digests, asserts skip
- Property 2: Remove image dependents — generates dependent lists
- Property 3: Run conflict detection — generates name collisions with/without replace
- Property 4: Lifecycle idempotency — generates state pairs
- Property 5: Stop timeout propagation — generates integer timeouts
- Property 6: Credential redaction — generates random passwords, asserts never in argv/stderr
- Property 7: Registry drop-in byte-identical skip — generates body pairs
- Property 8: Quadlet byte-identical skip — generates body pairs
- Property 9: Rootless vs rootful scope mismatch — generates scope/rootless combinations
- Property 10: Dry-run safety — generates every write operation under dry_run=True
- Property 11: Elevation consistency — generates rootless/elevated combinations
- Property 12: Status mapping — generates exit codes
- Property 13: JSON parse stability — generates malformed JSON strings

Both unit tests and property-based tests are complementary: unit tests verify
specific examples and edge cases, while property tests verify universal correctness
across many inputs.

---

## Usage Examples

### Local Rootful Execution — Deploy nginx from Image and Quadlet

```python
from automojo.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from automojo.sysman.linux.redhat.fileops import LocalFileOperator, LocalFileOperatorConfig
from automojo.sysman.linux.redhat.containermanagement import (
    ContainerManager,
    ContainerManagerConfig,
)

runner = LocalCommandRunner(LocalRunnerConfig())
fileop = LocalFileOperator(LocalFileOperatorConfig())

config = ContainerManagerConfig(elevated=True, dry_run=False, rootless_default=False)
cmgr = ContainerManager(runner=runner, fileop=fileop, config=config)

pull_result = cmgr.pull_image(reference="docker.io/library/nginx:1.27")
print("pull:", pull_result.status)

quadlet_body = """[Unit]
Description=nginx edge
After=network-online.target

[Container]
Image=docker.io/library/nginx:1.27
PublishPort=8080:80

[Install]
WantedBy=default.target
"""

install_result = cmgr.install_quadlet(name="nginx-edge", body=quadlet_body, scope="system")
print("quadlet:", install_result.status)
# coordinator now asks service-management to run systemctl daemon-reload
# and to enable + start nginx-edge.service
```

### SSH Rootful Execution with Reliability Tracking

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.fileops import SSHFileOperator, SSHFileOperatorConfig
from automojo.sysman.linux.redhat.containermanagement import (
    ContainerManager,
    ContainerManagerConfig,
)

ssh_runner_config = SSHRunnerConfig(
    host="10.0.0.21",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0,
)
runner = SSHCommandRunner(ssh_runner_config)
fileop = SSHFileOperator(SSHFileOperatorConfig.from_runner(runner))

cmgr = ContainerManager(
    runner=runner,
    fileop=fileop,
    config=ContainerManagerConfig(elevated=True, rootless_default=False),
)

# Restart a fleet-managed container across the host
restart_result = cmgr.restart_container(name="nginx-edge")
print("restart:", restart_result.status)

metrics = runner.get_metrics()
total = metrics.success_count + metrics.failure_count
rate = metrics.success_count / total if total > 0 else 0.0
print("success rate: {:.2%}".format(rate))
print("average duration ms: {:.2f}".format(metrics.avg_duration_ms))
```

### SSH Execution with Aspects and Credential Redaction

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.fileops import SSHFileOperator, SSHFileOperatorConfig
from automojo.sysman.linux.redhat.aspects import (
    PersistenceAspect,
    PersistenceAspectConfig,
    RetryAspect,
    RetryAspectConfig,
    TimeoutAspect,
    TimeoutAspectConfig,
)
from automojo.sysman.linux.redhat.storage import SQLiteStorage
from automojo.sysman.linux.redhat.containermanagement import (
    ContainerManager,
    ContainerManagerConfig,
)

runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))

storage = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[1, 255],
    retryable_error_patterns=["Connection reset", "Temporary failure"],
))
runner.add_aspect(retry)

timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=300.0))
runner.add_aspect(timeout)

fileop = SSHFileOperator(SSHFileOperatorConfig.from_runner(runner))

cmgr = ContainerManager(
    runner=runner,
    fileop=fileop,
    config=ContainerManagerConfig(elevated=True, rootless_default=False),
)

# Password is passed to login_registry and then delivered to podman over stdin.
# It never appears on argv, and the value recorded by the PersistenceAspect has
# the username value replaced with *** and the password entirely absent.
login_result = cmgr.login_registry(
    registry="registry.internal.example.com",
    username="deploy-bot",
    password="s3cret-token-value",
)
assert login_result.status.value in ("success", "failed")
assert "s3cret-token-value" not in " ".join(login_result.command or [])
```

### Rootless Local Execution

```python
from automojo.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from automojo.sysman.linux.redhat.fileops import LocalFileOperator, LocalFileOperatorConfig
from automojo.sysman.linux.redhat.containermanagement import (
    ContainerManager,
    ContainerManagerConfig,
)

runner = LocalCommandRunner(LocalRunnerConfig())
fileop = LocalFileOperator(LocalFileOperatorConfig())

cmgr = ContainerManager(
    runner=runner,
    fileop=fileop,
    config=ContainerManagerConfig(elevated=False, rootless_default=True),
)

# Rootless pull and run; podman runs as the calling user.
cmgr.pull_image(reference="quay.io/prometheus/node-exporter:latest")

user_quadlet = """[Unit]
Description=node exporter

[Container]
Image=quay.io/prometheus/node-exporter:latest
PublishPort=9100:9100

[Install]
WantedBy=default.target
"""

cmgr.install_quadlet(name="node-exporter", body=user_quadlet, scope="user")
```

### Dry-Run Mode Across Transports

```python
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))
fileop = SSHFileOperator(SSHFileOperatorConfig.from_runner(runner))
cmgr = ContainerManager(
    runner=runner,
    fileop=fileop,
    config=ContainerManagerConfig(elevated=True, dry_run=True),
)

result = cmgr.pull_image(reference="docker.io/library/nginx:1.27")
assert result.status.value == "dry_run"
```
