# Design

## Overview

The audit management feature is implemented as a single module,
`automojo.sysman.linux.redhat.auditmanagement`, that exposes an `AuditManager` class.
The coordinator instantiates `AuditManager` and calls its methods to perform discrete
maintenance actions against the Linux Audit subsystem: the `auditd` service lifecycle,
the runtime and persistent rule sets, the daemon configuration file, and the
`ausearch`/`aureport` reporting tools.

All system command execution is delegated to a command runner implementing the
`ICommandRunner` interface, and all filesystem reads and writes against
`/etc/audit/auditd.conf` and the drop-ins under `/etc/audit/rules.d/` are delegated
to a file operator implementing the `IFileOperator` interface. The `AuditManager`
never directly calls `subprocess`, shell commands, or `open()`; it only constructs
command argument lists and file paths and passes them to the injected abstractions.
This keeps business logic separate from execution and makes the feature fully
testable via mocks without root access or a live system.

The feature works identically whether commands execute locally (via
`LocalCommandRunner` + `LocalFileOperator`) or remotely over SSH (via
`SSHCommandRunner` + `SSHFileOperator`). When using SSH, the coordinator can track
reliability metrics and apply execution aspects (persistence, retry, timeout) without
any changes to the `AuditManager` code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md) for
details on local/SSH execution, reliability tracking, and aspect configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner and file operator (Local or SSH)    │
│  - Configures aspects (optional)                           │
│  - Injects both into AuditManager                          │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            AuditManager                                     │
│  automojo.sysman.linux.redhat.auditmanagement               │
│                                                             │
│  get_service_state()   set_service_state()                  │
│  restart_service()                                          │
│  list_active_rules()   add_runtime_rule()                   │
│  remove_runtime_rule() flush_runtime_rules()                │
│  list_rule_files()     write_rule_file()                    │
│  remove_rule_file()    reload_rules()                       │
│  get_config_value()    set_config_value()                   │
│  delete_config_key()                                        │
│  search_events()       get_summary_report()                 │
│  get_kernel_state()                                         │
└──────────┬──────────────────────────────┬───────────────────┘
           │ delegates commands to        │ delegates file I/O to
           ▼                              ▼
┌──────────────────────┐        ┌──────────────────────┐
│   ICommandRunner     │        │    IFileOperator     │
│  run(args, elevated, │        │  read_text / write_  │
│  cwd, env, timeout)  │        │  text / exists /     │
│  → RunResult         │        │  list / delete /chmod│
└──────────┬───────────┘        └──────────┬───────────┘
           │                               │
   ┌───────┴────────┐              ┌───────┴────────┐
   ▼                ▼              ▼                ▼
┌──────────┐  ┌──────────┐   ┌──────────┐   ┌──────────┐
│ Local    │  │ SSH      │   │ Local    │   │ SSH      │
│ Runner   │  │ Runner   │   │ FileOp   │   │ FileOp   │
└──────────┘  └────┬─────┘   └──────────┘   └────┬─────┘
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

### `AuditManager`

**Location:** `source/packages/automojo/sysman/linux/redhat/auditmanagement.py`

The primary public class.  Constructed with an `ICommandRunner`, an `IFileOperator`,
and an `AuditManagerConfig`.

| Method | Description |
|---|---|
| `get_service_state()` → `AuditServiceState` | Query `auditd` active/enabled/PID state |
| `set_service_state(action)` → `AuditActionResult` | Start / stop / enable / disable `auditd` |
| `restart_service()` → `AuditActionResult` | Restart `auditd` via `service auditd restart` |
| `list_active_rules()` → `list[AuditRule]` | List kernel-active rules via `auditctl -l` |
| `add_runtime_rule(rule_args)` → `AuditActionResult` | Add a runtime rule via `auditctl -a` |
| `remove_runtime_rule(rule_args)` → `AuditActionResult` | Remove a runtime rule via `auditctl -d` |
| `flush_runtime_rules()` → `AuditActionResult` | Flush all runtime rules via `auditctl -D` |
| `list_rule_files()` → `list[AuditRuleFile]` | Enumerate `/etc/audit/rules.d/*.rules` |
| `write_rule_file(name, content)` → `AuditActionResult` | Write `/etc/audit/rules.d/<name>.rules` |
| `remove_rule_file(name)` → `AuditActionResult` | Delete `/etc/audit/rules.d/<name>.rules` |
| `reload_rules()` → `AuditActionResult` | Reload persistent rules via `augenrules --load` |
| `get_config_value(key)` → `str \| None` | Read a key from `/etc/audit/auditd.conf` |
| `set_config_value(key, value)` → `AuditActionResult` | Set or replace a key in `auditd.conf` |
| `delete_config_key(key)` → `AuditActionResult` | Remove a key from `auditd.conf` |
| `search_events(...)` → `list[AuditEvent]` | Search log via `ausearch` filters |
| `get_summary_report(category=None, ts=None, te=None)` → `AuditReport` | Generate an `aureport` result |
| `get_kernel_state()` → `AuditKernelState` | Query kernel state via `auditctl -s` |

### `AuditManagerConfig`

**Location:** `source/packages/automojo/sysman/linux/redhat/auditmanagement.py`

A dataclass holding runtime configuration for `AuditManager`.

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no mutating commands or file writes are executed |
| `elevated` | `bool` | `False` | When `True`, mutating commands and file writes are executed with elevation |

---

## Data Models

### `AuditActionStatus`

```python
class AuditActionStatus(Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"   # already in desired state, no action needed
    DRY_RUN  = "dry_run"   # would have acted, but dry_run=True
```

### `AuditActionResult`

Returned by all mutating operations (lifecycle, add/remove rule, drop-in write/delete,
reload, config set/delete).

```python
@dataclass
class AuditActionResult:
    status: AuditActionStatus
    target: str                   # rule text, file name, config key, service action
    exit_code: int | None         # raw exit code from command runner
    stdout: str | None
    stderr: str | None
    command: list[str] | None     # the command that was (or would have been) run
    note: str | None              # informational note (e.g. "restart required")
```

### `AuditRule`

Returned by `list_active_rules()` and used as the parsed representation of a single
active or declared audit rule.

```python
@dataclass
class AuditRule:
    raw: str                      # verbatim rule text from auditctl -l
    filter_list: str | None       # e.g. "exit", "task", "exclude" (parsed from -a)
    action: str | None            # e.g. "always", "never" (parsed from -a)
    key: str | None               # parsed -k value, if present
```

### `AuditRuleFile`

Returned as elements of `list_rule_files()`.

```python
@dataclass
class AuditRuleFile:
    name: str                     # drop-in base name (without .rules suffix)
    path: str                     # "/etc/audit/rules.d/<name>.rules"
    size_bytes: int
```

### `AuditEvent`

Returned as elements of `search_events()`.

```python
@dataclass
class AuditEvent:
    event_id: str                 # ausearch event id
    timestamp: float              # unix epoch seconds
    event_type: str               # e.g. "SYSCALL", "USER_LOGIN"
    serial: int
    raw: str                      # raw event block text
```

### `AuditReport`

Returned by `get_summary_report()`.

```python
@dataclass
class AuditReport:
    category: str                 # "summary", "users", "executables", "failed-auth", ...
    raw: str                      # verbatim aureport output
    parsed: dict[str, str] | None # structured key/value parse where feasible
```

### `AuditServiceState`

Returned by `get_service_state()`.

```python
@dataclass
class AuditServiceState:
    present: bool                 # False if the unit file is not installed
    active: bool | None
    enabled: bool | None
    pid: int | None
```

### `AuditKernelState`

Returned by `get_kernel_state()`.

```python
@dataclass
class AuditKernelState:
    enabled: int                  # 0 = disabled, 1 = enabled, 2 = immutable
    failure: int | None
    pid: int | None
    rate_limit: int | None
    backlog_limit: int | None
    backlog: int | None
    lost: int | None
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Lifecycle Idempotency

*For any* lifecycle action (`start`, `stop`, `enable`, `disable`), if the current
service state already matches the requested state, `set_service_state()` should return
`SKIPPED` without invoking the command runner.

**Validates: Requirements 2.2**

### Property 2: Runtime Rule Add Idempotency

*For any* rule argument list, if the same rule is already present in the active rule
set, `add_runtime_rule()` should return `SKIPPED` without invoking `auditctl -a`.

**Validates: Requirements 4.3**

### Property 3: Runtime Rule Remove Idempotency

*For any* rule argument list, if the rule is not present in the active rule set,
`remove_runtime_rule()` should return `SKIPPED` without invoking `auditctl -d`.

**Validates: Requirements 5.3**

### Property 4: Rules.d Drop-In Round Trip

*For any* drop-in `name` and `content`, after `write_rule_file(name, content)` returns
`SUCCESS`, a subsequent call to `write_rule_file(name, content)` with the same content
should return `SKIPPED`, and the file listed by `list_rule_files()` should report the
same `name` and a `size_bytes` equal to the byte length of `content`.

**Validates: Requirements 6.2, 6.4**

### Property 5: Immutable-Mode Refusal

*For any* mutating rule operation (`add_runtime_rule`, `remove_runtime_rule`,
`flush_runtime_rules`, `reload_rules`), when `get_kernel_state().enabled == 2` the
method should return an `AuditActionResult` with `status=FAILED` and an
`AuditImmutableError` note, and the command runner should never be invoked with the
mutating command.

**Validates: Requirements 4.2, 7.2**

### Property 6: Reload Skip When Nothing Changed

*For any* invocation of `write_rule_file()` that returns `SKIPPED` (content unchanged),
no reload is implicitly triggered — reloading is an explicit coordinator decision, and
calling `reload_rules()` is the only way to invoke `augenrules --load`.

**Validates: Requirements 6.2, 7.1**

### Property 7: Dry-Run Safety

*For any* mutating operation (`set_service_state`, `restart_service`,
`add_runtime_rule`, `remove_runtime_rule`, `flush_runtime_rules`, `write_rule_file`,
`remove_rule_file`, `reload_rules`, `set_config_value`, `delete_config_key`) with
`dry_run=True`, neither the command runner nor the file operator should be invoked
with a mutating call, and the result status should be `DRY_RUN`.

**Validates: Requirements 2.5, 4.6, 5.5, 6.5, 7.5, 8.6**

### Property 8: Elevation Consistency

*For any* mutating operation with `elevated=True` in the config, the command runner
and file operator should be invoked with `elevated=True`; for read-only query
operations (`list_active_rules`, `get_kernel_state`, `get_config_value`,
`search_events`, `get_summary_report`, `list_rule_files`) `elevated` should be `False`
unless the underlying files are unreadable.

**Validates: Requirements 12.1, 12.2, 12.3**

### Property 9: Search Empty-Result Contract

*For any* `search_events(...)` invocation where `ausearch` exits with the documented
"no results" exit code, the returned value should be an empty `list[AuditEvent]` and
no exception should be raised.

**Validates: Requirements 9.3**

### Property 10: Status Mapping Correctness

*For any* operation result from the command runner or file operator, the
`AuditActionResult` status should correctly reflect the underlying outcome: success
maps to `SUCCESS`, failure maps to `FAILED` (with exit code and stderr populated),
skipped precondition maps to `SKIPPED`, and dry-run maps to `DRY_RUN`.

**Validates: Requirements 2.3, 2.4, 4.4, 4.5, 5.4, 6.3, 7.3, 7.4, 8.4, 8.5**

### Property 11: Config Set Idempotency

*For any* key and value, if `get_config_value(key)` already equals `value`,
`set_config_value(key, value)` should return `SKIPPED` without invoking a file write.

**Validates: Requirements 8.4**

### Property 12: Restart Uses `service` Not `systemctl`

*For any* invocation of `restart_service()`, the command passed to the runner should
begin with `service auditd restart` and never `systemctl restart auditd`, because
the `auditd.service` unit is explicitly protected against systemctl-driven restart.

**Validates: Requirements 2.1**

---

## Interfaces

### `ICommandRunner` (from `automojo-interfaces`)

`AuditManager` depends on this interface, injected at construction time.

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

`AuditManager` depends on this interface for reading and writing configuration files
and rule drop-ins.

```python
class IFileOperator(Protocol):
    def exists(self, path: str) -> bool: ...
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
    def delete(self, path: str, elevated: bool = False) -> None: ...
    def list_dir(self, path: str, pattern: str | None = None) -> list[str]: ...
    def stat_size(self, path: str) -> int: ...
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

When `AuditManagerConfig.elevated` is `True`, the `AuditManager` passes
`elevated=True` to both `ICommandRunner.run()` and `IFileOperator` mutating methods.
The runner and file operator are responsible for prepending `sudo` or using whatever
privilege escalation mechanism is appropriate for the environment. `AuditManager` has
no knowledge of how elevation is implemented.

---

## Command Mappings

| Operation | Command |
|---|---|
| Query service active | `systemctl is-active auditd` |
| Query service enabled | `systemctl is-enabled auditd` |
| Start service | `systemctl start auditd` |
| Stop service | `systemctl stop auditd` |
| Enable service | `systemctl enable auditd` |
| Disable service | `systemctl disable auditd` |
| Restart service | `service auditd restart` |
| List active rules | `auditctl -l` |
| Kernel state | `auditctl -s` |
| Add runtime rule | `auditctl -a <rule-args>` |
| Remove runtime rule | `auditctl -d <rule-args>` |
| Flush runtime rules | `auditctl -D` |
| Reload persistent rules | `augenrules --load` |
| Search by key | `ausearch -k <key>` |
| Search by message type | `ausearch -m <type>` |
| Search by user id | `ausearch -ui <uid>` |
| Search by time start | `ausearch -ts <start>` |
| Search by time end | `ausearch -te <end>` |
| Search by executable | `ausearch -x <path>` |
| Summary report | `aureport --summary` |
| User report | `aureport -u` |
| Executable report | `aureport -x` |
| Failed-auth report | `aureport -au --failed` |

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `AuditQueryError` | A read-only query (`auditctl -l`, `auditctl -s`, `ausearch`, `aureport`, `systemctl is-*`) returns an unexpected non-zero exit code |
| `AuditImmutableError` | A mutating rule operation is attempted while the kernel reports `enabled 2` (immutable mode) |

Mutating-operation failures are surfaced as an `AuditActionResult` with
`status=FAILED` so the coordinator can decide how to respond without catching
exceptions for normal operational failures. `AuditImmutableError` is used both as an
exception type (when explicitly raised) and as the `note`/error text on an
`AuditActionResult` returned from a mutating rule operation that was blocked by
immutable mode.

### Immutable-Mode Note

Once the audit kernel is placed in immutable mode (`enabled 2`), no rule mutation or
rule reload is possible until reboot. All mutating rule operations and
`reload_rules()` query `get_kernel_state()` first and short-circuit with a `FAILED`
result and an `AuditImmutableError` message if immutable mode is active. This is a
deliberate refusal, not a silent failure.

### Idempotency Guards

Before executing mutating commands, `AuditManager` calls the matching query method
(`get_service_state`, `list_active_rules`, `get_config_value`, or a file-operator
`read_text`/`stat_size` comparison) to determine current state. If the system is
already in the desired state the method returns `SKIPPED` without invoking the
runner or the file operator. This makes all mutating operations safe to call
multiple times.

---

## Module Layout

```
source/packages/automojo/sysman/linux/redhat/
├── __init__.py
└── auditmanagement.py            ← AuditManager, config, models, exceptions

source/testroots/testbase/
└── automojo/tests/sysman/linux/redhat/
    └── test_auditmanagement.py   ← unit tests using MockCommandRunner + MockFileOperator
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` and a `MockFileOperator` injected into
  `AuditManager` at construction time.
- Tests verify the *arguments* passed to the runner and the *paths/content* passed to
  the file operator, not system state.
- Each public method has at minimum: a success path test, a skip/idempotency test, a
  failure path test, and a dry-run test.
- `restart_service()` has an additional test asserting the command is
  `service auditd restart` and never `systemctl restart auditd`.
- Mutating rule methods and `reload_rules()` have an additional test asserting
  immutable-mode refusal.
- `search_events()` has an additional test for the `ausearch` "no results" exit code
  being treated as an empty list.
- No test requires root access or a live `auditd` installation.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in Python.
Each correctness property defined above will be implemented as a property-based test
that:

- Generates random but valid inputs (rule arguments, drop-in names and content,
  config keys and values, lifecycle actions, kernel-state snapshots)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific correctness
property they validate, using the format:
`# Feature: audit-management, Property N: <property description>`

**Property Test Coverage:**

- Property 1: Lifecycle idempotency over random current/target state combinations
- Property 2-3: Rule add/remove idempotency over random rule argument lists
- Property 4: Drop-in round-trip over random names and content
- Property 5: Immutable-mode refusal over all mutating rule operations
- Property 6: Reload independence from write_rule_file skip results
- Property 7: Dry-run safety across all mutating operations
- Property 8: Elevation consistency across mutating and read-only operations
- Property 9: Empty search result contract over random filter inputs
- Property 10: Status mapping over random exit codes
- Property 11: Config set idempotency over random key/value pairs
- Property 12: Restart uses `service` not `systemctl`

Both unit tests and property-based tests are complementary: unit tests verify
specific examples and edge cases, while property tests verify universal correctness
across many inputs.

---

## Usage Examples

### Local Execution: Add Rule + Reload

```python
from automojo.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from automojo.sysman.linux.redhat.fileops import LocalFileOperator, LocalFileOperatorConfig
from automojo.sysman.linux.redhat.auditmanagement import AuditManager, AuditManagerConfig

# Create local runner and file operator
runner = LocalCommandRunner(LocalRunnerConfig())
fileop = LocalFileOperator(LocalFileOperatorConfig())

# Inject into audit manager
config = AuditManagerConfig(elevated=True, dry_run=False)
audit_manager = AuditManager(runner, fileop, config)

# Add a runtime rule, then write it as a persistent drop-in and reload
rule_args = ["-w", "/etc/passwd", "-p", "wa", "-k", "identity"]
runtime_result = audit_manager.add_runtime_rule(rule_args)
print(f"Runtime: {runtime_result.status}")

persist_result = audit_manager.write_rule_file(
    name="identity",
    content="-w /etc/passwd -p wa -k identity\n",
)
print(f"Persist: {persist_result.status}")

reload_result = audit_manager.reload_rules()
print(f"Reload: {reload_result.status}")
```

### SSH Execution with Reliability Tracking: Search Events

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.fileops import SSHFileOperator, SSHFileOperatorConfig
from automojo.sysman.linux.redhat.auditmanagement import AuditManager, AuditManagerConfig

# Create SSH runner for remote system
ssh_runner_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0,
)
runner = SSHCommandRunner(ssh_runner_config)

ssh_fileop_config = SSHFileOperatorConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
)
fileop = SSHFileOperator(ssh_fileop_config)

# Inject into audit manager (same code as local!)
config = AuditManagerConfig(elevated=True)
audit_manager = AuditManager(runner, fileop, config)

# Search the audit log for identity-key events
events = audit_manager.search_events(key="identity")
print(f"Found {len(events)} identity events")
for event in events:
    print(f"  [{event.timestamp}] {event.event_type} serial={event.serial}")

# Check SSH reliability metrics
metrics = runner.get_metrics()
print(f"Success rate: {metrics.success_count / (metrics.success_count + metrics.failure_count):.2%}")
print(f"Average duration: {metrics.avg_duration_ms:.2f}ms")
```

### SSH Execution with Aspects: Apply a Baseline Rule Set

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.fileops import SSHFileOperator, SSHFileOperatorConfig
from automojo.sysman.linux.redhat.aspects import PersistenceAspect, RetryAspect, TimeoutAspect
from automojo.sysman.linux.redhat.aspects import PersistenceAspectConfig, RetryAspectConfig, TimeoutAspectConfig
from automojo.sysman.linux.redhat.storage import SQLiteStorage
from automojo.sysman.linux.redhat.auditmanagement import AuditManager, AuditManagerConfig

# Create SSH runner and file operator
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))
fileop = SSHFileOperator(SSHFileOperatorConfig(host="remote-server", username="admin"))

# Add persistence aspect for audit logging
storage = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

# Add retry aspect for transient failures
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[1, 255],
    retryable_error_patterns=["Connection reset", "Temporary failure"],
))
runner.add_aspect(retry)

# Add timeout aspect for global policy
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=300.0))
runner.add_aspect(timeout)

# Inject into audit manager
audit_manager = AuditManager(runner, fileop, AuditManagerConfig(elevated=True))

# Apply a baseline rule set: write all drop-ins, then trigger a single reload
baseline_rules = {
    "identity":  "-w /etc/passwd -p wa -k identity\n",
    "sudoers":   "-w /etc/sudoers -p wa -k sudoers\n",
    "modules":   "-w /sbin/insmod -p x -k modules\n",
}
for name, content in baseline_rules.items():
    result = audit_manager.write_rule_file(name=name, content=content)
    print(f"{name}: {result.status}")

reload_result = audit_manager.reload_rules()
print(f"Reload: {reload_result.status}")
```

### Dry-Run Mode Across Transports

```python
# Dry-run works identically for local and SSH
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))
fileop = SSHFileOperator(SSHFileOperatorConfig(host="remote-server", username="admin"))
audit_manager = AuditManager(runner, fileop, AuditManagerConfig(elevated=True, dry_run=True))

# No mutating command or file write is performed
result = audit_manager.write_rule_file(name="identity", content="-w /etc/passwd -p wa -k identity\n")
assert result.status == AuditActionStatus.DRY_RUN
```
