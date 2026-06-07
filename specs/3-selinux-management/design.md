# Design

## Overview

The SELinux management feature is implemented as a single module,
`ccai.sysman.linux.redhat.selinuxmanagement`, that exposes a `SELinuxManager` class.
The coordinator instantiates `SELinuxManager` and calls its methods to perform discrete
SELinux maintenance actions: query and modify file contexts, manage local file-context
rules, toggle policy booleans, assign port contexts, install and remove policy modules,
persist the SELinux mode in `/etc/selinux/config`, and generate audit-driven policy
previews.

This feature complements — and does not duplicate — the runtime SELinux mode query and
`setenforce` operations already provided by the `security-compliance` feature. The
`security-compliance` feature owns the *runtime* mode (enforcing/permissive) via
`getenforce`/`setenforce`; this feature owns *everything else* on the SELinux surface,
including the *persistent* mode recorded in `/etc/selinux/config`.

All system interaction is delegated to a command runner implementing the `ICommandRunner`
interface and, for policy-config file reads and writes, an `IFileOperator` implementation.
The `SELinuxManager` never directly calls `subprocess`, opens files, or invokes shell
commands; it only constructs command argument lists and file paths and passes them to the
injected collaborators. This keeps business logic separate from execution and makes the
feature fully testable without root access or a live SELinux system.

The feature works identically whether commands execute locally (via `LocalCommandRunner`)
or remotely over SSH (via `SSHCommandRunner`). When using SSH, the coordinator can track
reliability metrics and apply execution aspects (persistence, retry, timeout) without any
changes to the `SELinuxManager` code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md) for
details on local/SSH execution, reliability tracking, and aspect configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner (Local or SSH)                      │
│  - Instantiates file operator (Local or SSH)               │
│  - Configures aspects (optional)                           │
│  - Injects collaborators into SELinuxManager               │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            SELinuxManager                                   │
│  ccai.sysman.linux.redhat.selinuxmanagement             │
│                                                             │
│  query_context()     set_context()     restore_context()    │
│  add_fcontext_rule() remove_fcontext_rule()                 │
│  list_fcontext_rules()                                      │
│  get_boolean()       set_boolean()     list_booleans()      │
│  add_port_context()  remove_port_context()                  │
│  list_port_contexts()                                       │
│  install_module()    remove_module()   list_modules()       │
│  set_persistent_mode()                                      │
│  generate_allow_module()                                    │
└──────────────┬─────────────────────────────┬────────────────┘
               │ delegates to                │ delegates to
               ▼                             ▼
┌──────────────────────────┐      ┌──────────────────────────┐
│     ICommandRunner       │      │     IFileOperator        │
│ run(args, elevated, ...) │      │ read_text(path)          │
│          → RunResult     │      │ write_text(path, data,   │
│                          │      │            elevated)     │
│                          │      │ exists(path)             │
└──────────┬───────────────┘      └──────────┬───────────────┘
           │                                 │
   ┌───────┴────────┐                ┌───────┴────────┐
   ▼                ▼                ▼                ▼
┌──────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐
│ LocalCommand │ │ SSHCommand │ │ LocalFile  │ │ SSHFile    │
│ Runner       │ │ Runner     │ │ Operator   │ │ Operator   │
│ - subprocess │ │ - SSH conn │ │ - local fs │ │ - SFTP     │
│ - sudo       │ │ - metrics  │ │ - sudo tee │ │ - sudo tee │
└──────┬───────┘ └──────┬─────┘ └────────────┘ └────────────┘
       │                │
       └────────┬───────┘
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

### `SELinuxManager`

**Location:** `source/packages/ccai/sysman/linux/redhat/selinuxmanagement.py`

The primary public class. Constructed with an `ICommandRunner`, an `IFileOperator`, and a
`SELinuxManagerConfig`.

| Method | Description |
|---|---|
| `query_context(path)` → `SELinuxContext \| None` | Read the current SELinux context of a path |
| `set_context(path, type_, user=None, role=None, recursive=False)` | Apply a context to a path with `chcon` |
| `restore_context(path, recursive=False, force=False)` | Restore default context with `restorecon` |
| `add_fcontext_rule(path_regex, type_, file_type=None)` | Declare a persistent file-context rule |
| `remove_fcontext_rule(path_regex)` | Remove a local file-context rule |
| `list_fcontext_rules(local_only=True)` → `list[SELinuxFileContextRule]` | List file-context rules |
| `get_boolean(name)` → `bool` | Query a single policy boolean |
| `set_boolean(name, value, persistent=False)` | Toggle a policy boolean |
| `list_booleans()` → `list[SELinuxBoolean]` | List all booleans with metadata |
| `add_port_context(port, protocol, type_)` | Associate a port with an SELinux type |
| `remove_port_context(port, protocol)` | Remove a locally-declared port context |
| `list_port_contexts(local_only=False)` → `list[SELinuxPortRule]` | List port contexts |
| `install_module(filepath)` | Install a compiled policy module (`.pp` file) |
| `remove_module(name)` | Remove an installed policy module by name |
| `list_modules()` → `list[SELinuxModule]` | List installed policy modules |
| `set_persistent_mode(mode)` | Update `SELINUX=` in `/etc/selinux/config` |
| `generate_allow_module(module_name, working_dir)` → `SELinuxGeneratedModule` | Preview / build an audit2allow module |

### `SELinuxManagerConfig`

**Location:** `source/packages/ccai/sysman/linux/redhat/selinuxmanagement.py`

A dataclass holding runtime configuration for `SELinuxManager`.

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no commands are executed and no files are written |
| `elevated` | `bool` | `False` | When `True`, mutating commands are prefixed with sudo via the runner |

---

## Data Models

### `SELinuxActionStatus`

```python
class SELinuxActionStatus(Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"   # already in desired state, no action needed
    DRY_RUN  = "dry_run"   # would have acted, but dry_run=True
```

### `SELinuxActionResult`

Returned by all write operations (`set_context`, `restore_context`, `add_fcontext_rule`,
`remove_fcontext_rule`, `set_boolean`, `add_port_context`, `remove_port_context`,
`install_module`, `remove_module`, `set_persistent_mode`).

```python
@dataclass
class SELinuxActionResult:
    status: SELinuxActionStatus
    target: str                   # path, boolean name, port, or module name
    changed: bool                 # True iff the system state changed
    exit_code: int | None         # raw exit code from command runner
    stdout: str | None
    stderr: str | None
    command: list[str] | None     # the command that was (or would have been) run
    warning: str | None           # e.g. "reboot required" for disabled persistent mode
```

### `SELinuxContext`

Returned by `query_context()`.

```python
@dataclass
class SELinuxContext:
    user: str
    role: str
    type_: str
    level: str
```

### `SELinuxFileContextRule`

Returned as elements of `list_fcontext_rules()`.

```python
@dataclass
class SELinuxFileContextRule:
    path_regex: str
    file_type: str | None         # e.g. "--" for regular file, "-d" for dir, None for any
    selinux_type: str
```

### `SELinuxPortRule`

Returned as elements of `list_port_contexts()`.

```python
@dataclass
class SELinuxPortRule:
    selinux_type: str
    protocol: str                 # "tcp" or "udp"
    port_range: str               # e.g. "8080" or "8000-8010"
```

### `SELinuxBoolean`

Returned as elements of `list_booleans()`.

```python
@dataclass
class SELinuxBoolean:
    name: str
    current_value: bool
    default_value: bool
    description: str
```

### `SELinuxModule`

Returned as elements of `list_modules()`.

```python
@dataclass
class SELinuxModule:
    name: str
    priority: int | None          # module priority when reported by semodule
```

### `SELinuxGeneratedModule`

Returned by `generate_allow_module()`.

```python
@dataclass
class SELinuxGeneratedModule:
    module_name: str
    pp_path: str | None           # None in dry-run / preview mode
    te_path: str | None           # None in dry-run / preview mode
    raw_rules: str                # stdout of `audit2allow -a`
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Context Read-Write Round-Trip

*For any* path `p` and context `c`, after a successful `set_context(p, c)` call, a
subsequent `query_context(p)` should return a `SELinuxContext` whose `type_` (and
optional `user`/`role`) fields match `c`.

**Validates: Requirements 1.2, 2.5**

### Property 2: Set-Context Idempotency

*For any* path already labeled with the requested type, calling `set_context()` a second
time with the same arguments should return `SKIPPED` without invoking `chcon`.

**Validates: Requirements 2.4**

### Property 3: Restorecon No-Op When Context Matches Default

*For any* path whose current context already matches the default policy, calling
`restore_context()` should return `SUCCESS` with `changed=False` and no further mutations
on a repeat call.

**Validates: Requirements 3.4**

### Property 4: Boolean Set Idempotency

*For any* boolean name and target value, if the boolean's current value already equals the
target value, `set_boolean()` should return `SKIPPED` without invoking `setsebool`.

**Validates: Requirements 8.3**

### Property 5: Persistent-Mode Idempotency

*For any* mode value `m`, if `/etc/selinux/config` already records `SELINUX=m`, calling
`set_persistent_mode(m)` should return `SKIPPED` without writing to the file.

**Validates: Requirements 16.2**

### Property 6: Policy-Module Install Idempotency

*For any* `.pp` path, if a module with the same name is already installed at the same
priority, `install_module()` should return `SKIPPED` without invoking `semodule -i`.

**Validates: Requirements 13.3, 14.2**

### Property 7: Fcontext Add-Then-Remove Symmetry

*For any* path regex `r` and type `t`, after `add_fcontext_rule(r, t)` followed by
`remove_fcontext_rule(r)`, `list_fcontext_rules(local_only=True)` should not contain a
rule for `r`.

**Validates: Requirements 4.3, 5.3, 6.3**

### Property 8: Port-Context Add-Then-Remove Symmetry

*For any* port `p` and protocol `proto`, after `add_port_context(p, proto, t)` followed by
`remove_port_context(p, proto)`, `list_port_contexts(local_only=True)` should not contain
a rule for `(p, proto)`.

**Validates: Requirements 10.3, 11.3, 12.3**

### Property 9: Dry-Run Safety

*For any* mutating operation (`set_context`, `restore_context`, `add_fcontext_rule`,
`remove_fcontext_rule`, `set_boolean`, `add_port_context`, `remove_port_context`,
`install_module`, `remove_module`, `set_persistent_mode`) with `dry_run=True`, neither
`ICommandRunner.run()` nor `IFileOperator.write_text()` should be invoked, and the result
status should be `DRY_RUN`.

**Validates: Requirements 2.7, 3.6, 4.5, 5.4, 8.6, 10.5, 11.4, 13.6, 14.4, 16.6, 17.6**

### Property 10: Elevation Consistency

*For any* mutating SELinux command (`chcon`, `restorecon`, `setsebool -P`, `semanage
-a|-m|-d`, `semodule -i|-r`, writes to `/etc/selinux/config`), the runner should be
invoked with `elevated=True` when the config's `elevated` flag is set; read-only commands
(`stat -Z`, `getsebool`, `semanage -l`, `semodule -l`) should never request elevation.

**Validates: Requirements 18.1, 18.2, 18.3**

### Property 11: Path-Not-Found Validation

*For any* call to `install_module()` with a `.pp` path that does not exist, a
`SELinuxPathNotFoundError` should be raised before the command runner is invoked.

**Validates: Requirements 13.1**

### Property 12: Status Mapping Correctness

*For any* `RunResult` returned by the command runner, the `SELinuxActionResult.status`
should map exit code `0` to `SUCCESS` and any non-zero exit code to `FAILED`, unless the
operation was short-circuited to `SKIPPED` or `DRY_RUN` before command execution.

**Validates: Requirements 2.5, 2.6, 3.5, 4.3, 4.4, 8.4, 8.5, 10.4, 13.4, 13.5, 16.4, 16.5**

### Property 13: Boolean Not-Found Propagation

*For any* call to `get_boolean(name)` where `name` is not a known boolean, a
`SELinuxBooleanNotFoundError` should be raised rather than returning `False`.

**Validates: Requirements 7.3**

### Property 14: audit2allow Read-Only When Dry-Run

*For any* call to `generate_allow_module()` with `dry_run=True`, the implementation should
invoke only `audit2allow -a` (preview) and SHALL NOT invoke `audit2allow -M`, and the
returned `SELinuxGeneratedModule` should have `pp_path=None` and `te_path=None`.

**Validates: Requirements 17.1, 17.5, 17.6**

---

## Interfaces

### `ICommandRunner` (from `codecraftai-interfaces`)

`SELinuxManager` depends on this interface, injected at construction time.

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

`SELinuxManager` uses this interface to read and write `/etc/selinux/config`. All direct
file I/O is mediated through the operator, so local and SSH transports are transparent.

```python
class IFileOperator(Protocol):
    def read_text(self, path: str) -> str: ...
    def write_text(self, path: str, data: str, elevated: bool = False) -> None: ...
    def exists(self, path: str) -> bool: ...
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

When `SELinuxManagerConfig.elevated` is `True`, the `SELinuxManager` passes
`elevated=True` to `ICommandRunner.run()` for every mutating command and to
`IFileOperator.write_text()` when updating `/etc/selinux/config`. The runner/operator is
responsible for prepending `sudo` or using whatever privilege-escalation mechanism is
appropriate for the environment. `SELinuxManager` has no knowledge of how elevation is
implemented.

---

## Command Mappings

| Operation | Command |
|---|---|
| Query context of a path | `stat --printf='%C' <path>` |
| Set type on a path | `chcon -t <type> <path>` |
| Set type recursively | `chcon -R -t <type> <path>` |
| Set user on a path | `chcon -u <user> <path>` |
| Set role on a path | `chcon -r <role> <path>` |
| Restore default context | `restorecon -v <path>` |
| Restore recursively | `restorecon -R -v <path>` |
| Force-relabel on restore | `restorecon -F -v <path>` |
| Add local fcontext rule | `semanage fcontext -a -t <type> "<path-regex>"` |
| Modify local fcontext rule | `semanage fcontext -m -t <type> "<path-regex>"` |
| Delete local fcontext rule | `semanage fcontext -d "<path-regex>"` |
| List local fcontext rules | `semanage fcontext -l -C` |
| List all fcontext rules | `semanage fcontext -l` |
| Query one boolean | `getsebool <name>` |
| Query all booleans | `getsebool -a` |
| Set boolean (runtime only) | `setsebool <name> on\|off` |
| Set boolean (persistent) | `setsebool -P <name> on\|off` |
| List booleans with metadata | `semanage boolean -l` |
| Add port context | `semanage port -a -t <type> -p <proto> <port>` |
| Modify port context | `semanage port -m -t <type> -p <proto> <port>` |
| Delete port context | `semanage port -d -p <proto> <port>` |
| List port contexts | `semanage port -l` |
| List local port contexts | `semanage port -l -C` |
| Install policy module | `semodule -i <path>` |
| Remove policy module | `semodule -r <name>` |
| List installed modules | `semodule -l` |
| Generate allow preview | `audit2allow -a` |
| Generate compiled module | `audit2allow -a -M <module-name>` |

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `SELinuxQueryError` | A read-only command (`semanage -l`, `semodule -l`, `getsebool -a`, `audit2allow`) returns an unexpected non-zero exit code |
| `SELinuxPathNotFoundError` | `query_context()` or `install_module()` is called with a filesystem path that does not exist |
| `SELinuxBooleanNotFoundError` | `get_boolean()` or `set_boolean()` is called with a boolean name that the kernel does not know |

All other failures are surfaced as a `SELinuxActionResult` with `status=FAILED` so the
coordinator can decide how to respond without catching exceptions for normal operational
failures.

### Idempotency Guards

Before executing mutating commands, `SELinuxManager` performs a lightweight query to
determine current state:

- `set_context()` calls `query_context()` and skips when the requested type is already set
- `set_boolean()` calls `get_boolean()` and skips when the value already matches
- `set_persistent_mode()` reads `/etc/selinux/config` and skips when the `SELINUX=` line
  already matches
- `install_module()` consults `list_modules()` to detect an already-installed module
- `remove_module()` consults `list_modules()` to short-circuit when absent
- `add_fcontext_rule()` consults `list_fcontext_rules()` to choose between `-a` and `-m`
- `remove_fcontext_rule()`, `remove_port_context()` skip when the rule is absent

This makes all write operations safe to call multiple times.

### `/etc/selinux/config` Persistent-Mode Restart Note

When `set_persistent_mode()` writes `SELINUX=disabled`, the kernel cannot transition to
disabled at runtime — a reboot is required. `SELinuxManager` does not attempt to reboot;
instead it returns a `SELinuxActionResult` with `status=SUCCESS` and a human-readable
`warning` string so the coordinator can surface or schedule the reboot. Transitions
between `enforcing` and `permissive` do not carry this warning (the runtime transition
belongs to the `security-compliance` feature).

---

## Module Layout

```
source/packages/ccai/sysman/linux/redhat/
├── __init__.py
└── selinuxmanagement.py         ← SELinuxManager, config, models, exceptions

source/testroots/testbase/
└── ccai/tests/sysman/linux/redhat/
    └── test_selinuxmanagement.py ← unit tests using MockCommandRunner & MockFileOperator
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` and `MockFileOperator` injected into
  `SELinuxManager` at construction time.
- Tests verify the *arguments* passed to the runner and the *contents* passed to the file
  operator, not system state.
- Each public method has at minimum: a success path test, a skip/idempotency test, a
  failure path test, and a dry-run test.
- `query_context()` has additional tests for path-not-found and SELinux-disabled paths.
- `install_module()` has a test for `SELinuxPathNotFoundError` before the runner is
  called.
- `set_persistent_mode(disabled)` has a test verifying the reboot warning is attached.
- No test requires root access or a live SELinux-enabled system.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in Python.
Each correctness property defined above will be implemented as a property-based test
that:

- Generates random but valid inputs (paths, regexes, types, booleans, ports, protocols,
  modes)
- Executes the operation under test against mock collaborators
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific correctness
property they validate, using the format: `# Feature: selinux-management, Property N:
<property description>`

**Property Test Coverage:**

- Property 1: Context read/write round-trip via mock state machine
- Property 2-6: Idempotency tests for context, restorecon, boolean, persistent-mode, and
  module operations
- Property 7-8: Add-then-remove symmetry for fcontext and port rules
- Property 9: Dry-run safety across every mutating entry point
- Property 10: Elevation flag propagation for mutating vs. read-only commands
- Property 11: Path-not-found validation for `install_module()`
- Property 12: Status-mapping correctness across random exit codes
- Property 13: `SELinuxBooleanNotFoundError` propagation for unknown booleans
- Property 14: `generate_allow_module()` read-only behavior under dry-run

Both unit tests and property-based tests are complementary: unit tests verify specific
examples and edge cases (output parsing, warnings, error wrapping), while property tests
verify universal correctness across many inputs.

---

## Usage Examples

### Local Execution (Default)

```python
from ccai.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from ccai.sysman.linux.redhat.fileops import LocalFileOperator
from ccai.sysman.linux.redhat.selinuxmanagement import (
    SELinuxManager,
    SELinuxManagerConfig,
)

# Create local runner and file operator
runner = LocalCommandRunner(LocalRunnerConfig())
fileop = LocalFileOperator()

# Inject into SELinux manager
config = SELinuxManagerConfig(elevated=True, dry_run=False)
selinux = SELinuxManager(runner, fileop, config)

# Enable httpd network access (persistent) and label a custom web root
bool_result = selinux.set_boolean(name="httpd_can_network_connect", value=True, persistent=True)
print(f"Boolean set: {bool_result.status}")

fcontext_result = selinux.add_fcontext_rule(
    path_regex="/srv/web(/.*)?",
    type_="httpd_sys_content_t",
)
print(f"Fcontext rule: {fcontext_result.status}")

restore_result = selinux.restore_context(path="/srv/web", recursive=True)
print(f"Relabel: {restore_result.status} (changed={restore_result.changed})")
```

### SSH Execution with Reliability Tracking

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.fileops import SSHFileOperator
from ccai.sysman.linux.redhat.selinuxmanagement import (
    SELinuxManager,
    SELinuxManagerConfig,
)

# Create SSH runner and file operator for remote system
ssh_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0,
)
runner = SSHCommandRunner(ssh_config)
fileop = SSHFileOperator(ssh_config)

# Inject into SELinux manager (same code as local!)
config = SELinuxManagerConfig(elevated=True)
selinux = SELinuxManager(runner, fileop, config)

# Reserve a non-default port for the web service
port_result = selinux.add_port_context(port=8443, protocol="tcp", type_="http_port_t")
print(f"Port context: {port_result.status}")

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
from ccai.sysman.linux.redhat.fileops import SSHFileOperator
from ccai.sysman.linux.redhat.aspects import (
    PersistenceAspect,
    RetryAspect,
    TimeoutAspect,
    PersistenceAspectConfig,
    RetryAspectConfig,
    TimeoutAspectConfig,
)
from ccai.sysman.linux.redhat.storage import SQLiteStorage
from ccai.sysman.linux.redhat.selinuxmanagement import (
    SELinuxManager,
    SELinuxManagerConfig,
)

# Create SSH runner
ssh_config = SSHRunnerConfig(host="remote-server", username="admin")
runner = SSHCommandRunner(ssh_config)
fileop = SSHFileOperator(ssh_config)

# Add persistence aspect for audit logging
storage = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

# Add retry aspect for transient SSH failures
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[1, 255],
    retryable_error_patterns=["Connection reset", "Temporary failure"],
))
runner.add_aspect(retry)

# Add timeout aspect for global policy
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=300.0))
runner.add_aspect(timeout)

# Inject into SELinux manager
selinux = SELinuxManager(runner, fileop, SELinuxManagerConfig(elevated=True))

# Apply a full SELinux profile for a hardened web host:
#  - set persistent mode to enforcing
#  - enable an httpd boolean
#  - add a custom port reservation
#  - install a custom policy module
mode_result = selinux.set_persistent_mode(mode="enforcing")
bool_result = selinux.set_boolean(name="httpd_unified", value=True, persistent=True)
port_result = selinux.add_port_context(port=8443, protocol="tcp", type_="http_port_t")
module_result = selinux.install_module(filepath="/var/lib/maintenance/policy/myweb.pp")

# All SELinux operations now have:
# - Audit logging to SQLite
# - Automatic retry on transient failures
# - 5-minute timeout enforcement
for result in (mode_result, bool_result, port_result, module_result):
    print(f"{result.target}: {result.status} (changed={result.changed})")
```

### Dry-Run Mode Across Transports

```python
# Dry-run works identically for local and SSH
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin", dry_run=True))
fileop = SSHFileOperator(SSHRunnerConfig(host="remote-server", username="admin", dry_run=True))
selinux = SELinuxManager(runner, fileop, SELinuxManagerConfig(elevated=True, dry_run=True))

# No SSH connection is made, no commands executed, no file writes
result = selinux.set_persistent_mode(mode="enforcing")
assert result.status == SELinuxActionStatus.DRY_RUN
```
