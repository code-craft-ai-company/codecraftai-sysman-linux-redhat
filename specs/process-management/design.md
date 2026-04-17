# Design

## Overview

The process management feature is implemented as a single module,
`automojo.sysman.linux.redhat.processmanagement`, that exposes a `ProcessManager`
class. The coordinator instantiates `ProcessManager` and calls its methods to
enumerate, inspect, signal, and adjust running processes on Red Hat Linux
systems.

All system interaction is delegated to a command runner implementing the
`ICommandRunner` interface and a file operator implementing `IFileOperator`
(used for `/proc` reads and for the `lsof` fallback). The `ProcessManager`
never directly calls `subprocess` or opens files; it only constructs command
argument lists or virtual-filesystem paths and passes them to the injected
collaborators. This keeps business logic separate from execution and makes
the feature fully testable via mocks without root access or a live system.

This feature does not manage systemd services. Service lifecycle (start,
stop, enable, disable, status) remains the responsibility of the
`service-management` feature. `ProcessManager` operates on arbitrary
processes identified by PID, pattern, or owner.

The feature works identically whether commands execute locally (via
`LocalCommandRunner`) or remotely over SSH (via `SSHCommandRunner`). When
using SSH, the coordinator can track reliability metrics and apply execution
aspects (persistence, retry, timeout) without any changes to the
`ProcessManager` code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md)
for details on local/SSH execution, reliability tracking, and aspect
configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner (Local or SSH)                       │
│  - Instantiates file operator                               │
│  - Configures aspects (optional)                            │
│  - Injects collaborators into ProcessManager                │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            ProcessManager                                   │
│  automojo.sysman.linux.redhat.processmanagement             │
│                                                             │
│  list_processes()     query_process()                       │
│  find_processes()     signal_pid()                          │
│  signal_by_pattern()  renice()                              │
│  ionice()             get_cpu_affinity()                    │
│  set_cpu_affinity()   list_open_files()                     │
│  wait_for_exit()                                            │
└──────────┬──────────────────────────────────┬───────────────┘
           │ delegates to                     │ delegates to
           ▼                                  ▼
┌────────────────────────┐       ┌────────────────────────────┐
│     ICommandRunner     │       │      IFileOperator         │
│  run(args, elevated,   │       │  exists(path)              │
│      capture_output)   │       │  read_text(path)           │
│        → RunResult     │       │  list_dir(path)            │
└───────────┬────────────┘       └────────────┬───────────────┘
            │                                 │
  ┌─────────┴─────────┐               ┌───────┴─────────┐
  ▼                   ▼               ▼                 ▼
┌──────────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│LocalCommandRunner│ │SSHCommandRunr│ │LocalFileOp   │ │SSHFileOp     │
│- subprocess.run  │ │- SSH conn    │ │- open()/os   │ │- SFTP reads  │
│- sudo elevation  │ │- reliability │ │- /proc reads │ │- /proc over  │
│                  │ │- retry       │ │              │ │  SSH         │
└──────────────────┘ └──────────────┘ └──────────────┘ └──────────────┘
           │                 │
           └────────┬────────┘
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

### `ProcessManager`

**Location:** `source/packages/automojo/sysman/linux/redhat/processmanagement.py`

The primary public class. Constructed with an `ICommandRunner`, an
`IFileOperator`, and a `ProcessManagerConfig`.

| Method | Description |
|---|---|
| `list_processes(user=None)` → `list[ProcessInfo]` | Enumerate running processes, optionally scoped to a user |
| `query_process(pid)` → `ProcessInfo \| None` | Return process info for a single PID or `None` |
| `find_processes(pattern, full=True, user=None)` → `list[int]` | Locate PIDs matching a name/regex pattern |
| `signal_pid(pid, signal)` → `ProcessActionResult` | Deliver a named POSIX signal to a single PID |
| `signal_by_pattern(pattern, signal, graceful=False, grace_period=10.0)` → `ProcessActionResult` | Signal all processes matching a pattern, optionally terminate-then-kill |
| `renice(pid, nice_value)` → `ProcessActionResult` | Change a process's CPU nice value |
| `ionice(pid, io_class, priority)` → `ProcessActionResult` | Change a process's I/O scheduling class and priority |
| `get_cpu_affinity(pid)` → `CpuAffinity \| None` | Query the CPU affinity mask and list for a PID |
| `set_cpu_affinity(pid, mask=None, cpu_list=None)` → `ProcessActionResult` | Pin a process to a mask or CPU id list |
| `list_open_files(pid)` → `list[OpenFile]` | Enumerate open file handles for a PID, falling back to `/proc` |
| `wait_for_exit(pid, timeout)` → `ProcessWaitResult` | Poll `/proc/<pid>` until exit or timeout |

### `ProcessManagerConfig`

**Location:** `source/packages/automojo/sysman/linux/redhat/processmanagement.py`

A dataclass holding runtime configuration for `ProcessManager`.

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no mutating commands are executed |
| `elevated` | `bool` | `False` | When `True`, mutating commands against foreign-owned processes are prefixed with sudo |

---

## Data Models

### `ProcessActionStatus`

```python
class ProcessActionStatus(Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"   # non-existent PID, no match, or already-in-state
    DRY_RUN  = "dry_run"   # would have acted, but dry_run=True
```

### `ProcessActionResult`

Returned by all write operations (signal, renice, ionice, set_cpu_affinity).

```python
@dataclass
class ProcessActionResult:
    status: ProcessActionStatus
    pid: int | None
    pattern: str | None            # for pattern-based operations
    matched_count: int | None      # for pattern-based signal operations
    resulting_value: str | None    # e.g. resolved nice value or mask
    exit_code: int | None
    stdout: str | None
    stderr: str | None
    command: list[str] | None
```

### `ProcessInfo`

Returned by `list_processes()` and `query_process()`.

```python
@dataclass
class ProcessInfo:
    pid: int
    ppid: int
    uid: int
    user: str
    pgid: int
    sid: int
    stat: str
    nice: int
    pri: int
    pcpu: float
    pmem: float
    rss: int
    vsz: int
    etime: str
    comm: str
    args: str
```

### `OpenFile`

Returned as elements of `list_open_files()`.

```python
@dataclass
class OpenFile:
    fd: str
    type: str | None
    device: str | None
    size: int | None
    node: str | None
    name: str
```

### `CpuAffinity`

Returned by `get_cpu_affinity()`.

```python
@dataclass
class CpuAffinity:
    mask: str          # hex mask as reported by taskset
    cpus: list[int]    # expanded list of CPU ids
```

### `ProcessWaitResult`

Returned by `wait_for_exit()`.

```python
@dataclass
class ProcessWaitResult:
    exited: bool
    elapsed: float     # seconds
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all
valid executions of a system — essentially, a formal statement about what
the system should do. Properties serve as the bridge between human-readable
specifications and machine-verifiable correctness guarantees.*

### Property 1: List Returns Parsed Records

*For any* successful invocation of `list_processes()`, the returned list
should contain one `ProcessInfo` record per non-empty output line from
`ps`, with whitespace-split columns mapped to their typed fields and the
`args` column preserving embedded whitespace.

**Validates: Requirements 1.1, 1.2, 1.3**

### Property 2: Query Returns None for Non-Existent PID

*For any* PID that is not present on the system, `query_process()` should
return `None` rather than raise an exception.

**Validates: Requirements 2.2, 2.3**

### Property 3: Signal to Non-Existent PID Returns SKIPPED

*For any* call to `signal_pid()` or `signal_by_pattern()` where the target
PID or pattern matches no running process, the returned
`ProcessActionResult` should have status `SKIPPED` and no `FAILED` status.

**Validates: Requirements 4.3, 5.2**

### Property 4: Graceful Terminate-Then-Kill Escalation

*For any* call to `signal_by_pattern(..., graceful=True)`, the system
should first deliver `TERM`, poll `pgrep -f <pattern>` for at most the
supplied grace period, and only deliver `KILL` to PIDs still remaining
after the grace period.

**Validates: Requirements 5.4**

### Property 5: Renice No-Op When Already at Value

*For any* call to `renice(pid, nice_value)` where the current nice value
of the process already equals the target value, the runner should not be
invoked and the returned `ProcessActionResult` should have status
`SKIPPED`.

**Validates: Requirements 6.1, 6.4**

### Property 6: Set CPU Affinity Idempotency

*For any* call to `set_cpu_affinity()` where the supplied mask or CPU
list is equivalent to the process's current affinity, the runner should
not be invoked and the returned `ProcessActionResult` should have status
`SKIPPED`.

**Validates: Requirements 8.3**

### Property 7: Wait-For-Exit Polling Cap

*For any* call to `wait_for_exit()`, the interval between successive
`/proc/<pid>` existence checks should never exceed 500 ms.

**Validates: Requirements 10.1**

### Property 8: Lsof Fallback to /proc

*For any* call to `list_open_files()` on a system where `lsof` is not
installed, the system should fall back to reading `/proc/<pid>/fd/` and
`/proc/<pid>/maps` through the file operator and return `OpenFile`
records populated with the subset of fields available there.

**Validates: Requirements 9.3**

### Property 9: Dry-Run Safety

*For any* write operation (`signal_pid`, `signal_by_pattern`, `renice`,
`ionice`, `set_cpu_affinity`) with `dry_run=True`, the command runner
should never be invoked with a mutating command, and the returned result
status should be `DRY_RUN`. For `wait_for_exit()` with `dry_run=True`,
no filesystem polling should occur and the result should be
`exited=True, elapsed=0.0`.

**Validates: Requirements 4.6, 5.5, 6.6, 7.5, 8.5, 10.4**

### Property 10: Elevation Consistency (UID Match Does Not Elevate)

*For any* mutating operation where the coordinator's effective UID
matches the target process owner's UID, the command runner should be
invoked with `elevated=False`; when the effective UID differs and the
config has `elevated=True`, the runner should be invoked with
`elevated=True`. Read-only operations (`ps`, `pgrep`, `lsof`, `/proc`)
should never request elevation.

**Validates: Requirements 11.1, 11.2, 11.3, 11.4**

### Property 11: Status Mapping Correctness

*For any* operation result from the command runner, the
`ProcessActionResult` status should correctly reflect the runner's exit
code: `0` maps to `SUCCESS`, non-zero maps to `FAILED` (unless the
operation was skipped or in dry-run mode). Exit codes indicating
"no such process" should be re-mapped to `SKIPPED`.

**Validates: Requirements 4.4, 4.5, 5.3, 6.4, 6.5, 7.3, 7.4, 8.4**

### Property 12: Empty Pattern Match Returns Empty List

*For any* call to `find_processes()` where no process matches the
supplied pattern, the returned list should be empty rather than
`None` and no exception should be raised.

**Validates: Requirements 3.4**

---

## Interfaces

### `ICommandRunner` (from `automojo-interfaces`)

`ProcessManager` depends on this interface, injected at construction time.

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

Used for `/proc` reads (wait-for-exit polling) and the `lsof` fallback
(`/proc/<pid>/fd/` and `/proc/<pid>/maps`).

```python
class IFileOperator(Protocol):
    def exists(self, path: str) -> bool: ...
    def read_text(self, path: str) -> str: ...
    def list_dir(self, path: str) -> list[str]: ...
    def read_link(self, path: str) -> str: ...
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

Elevation is used only when the coordinator's effective UID differs from
the target process owner's UID. `ProcessManager` resolves the target
owner via a prior `query_process()` call and compares to the effective
UID reported by the runner's environment.

- Effective UID equals target owner → `elevated=False` regardless of
  `ProcessManagerConfig.elevated`.
- Effective UID differs AND `ProcessManagerConfig.elevated=True` →
  `elevated=True`.
- Process is root → `elevated=False` (root needs no sudo).
- Read-only commands (`ps`, `pgrep`, `lsof`, `/proc` reads) →
  `elevated=False` unconditionally; the command runner may still
  opportunistically elevate a `/proc` read if the coordinator has
  explicitly granted permission, but `ProcessManager` does not request
  it.

`ProcessManager` has no knowledge of how elevation is implemented by
the runner.

---

## Command Mappings

| Operation | Command |
|---|---|
| List all processes | `ps -eo pid,ppid,uid,user,pgid,sid,stat,nice,pri,pcpu,pmem,rss,vsz,etime,comm,args --no-headers` |
| Query one process | `ps -p <pid> -o pid,ppid,uid,user,pgid,sid,stat,nice,pri,pcpu,pmem,rss,vsz,etime,comm,args --no-headers` |
| List processes by user | `ps -U <user> -o pid,ppid,uid,user,pgid,sid,stat,nice,pri,pcpu,pmem,rss,vsz,etime,comm,args --no-headers` |
| Find PIDs by pattern (full args) | `pgrep -f <pattern>` |
| Find PIDs by pattern (basename) | `pgrep <pattern>` |
| Find PIDs by pattern and user | `pgrep -u <user> -f <pattern>` |
| Signal by pattern | `pkill -s <signal> -f <pattern>` |
| Signal by PID | `kill -s <signal> <pid>` |
| Renice | `renice -n <value> -p <pid>` |
| Ionice | `ionice -c <class-num> -n <priority> -p <pid>` |
| Query CPU affinity | `taskset -p <pid>` |
| Set CPU affinity (mask) | `taskset -p <mask> <pid>` |
| Set CPU affinity (CPU list) | `taskset -pc <list> <pid>` |
| List open files | `lsof -p <pid> -F pcuftDsidrno` |
| Fallback: enumerate fds | read `/proc/<pid>/fd/` via `IFileOperator.list_dir()` |
| Fallback: map entries | read `/proc/<pid>/maps` via `IFileOperator.read_text()` |
| Wait-for-exit | `IFileOperator.exists("/proc/<pid>")` polled at ≤ 500 ms |

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `ProcessQueryError` | `ps`, `pgrep`, or `lsof` returns an unexpected non-zero exit code, or a `/proc` read fails for a reason other than "process does not exist" |

All other failures are surfaced as a `ProcessActionResult` with
`status=FAILED` so the coordinator can decide how to respond without
catching exceptions for normal operational failures.

### Noop-on-Not-Found Paths

Several commands return non-zero exit codes to communicate "the target
is not there" rather than "a real error occurred":

| Command | Exit Code | Meaning | `ProcessManager` Handling |
|---|---|---|---|
| `ps -p <pid>` | `1` | PID not found | `query_process()` → `None` |
| `pgrep ...` | `1` | no match | `find_processes()` → `[]` |
| `pkill ...` | `1` | no match | `signal_by_pattern()` → `SKIPPED` |
| `kill <pid>` | ESRCH | PID vanished between check and signal | `signal_pid()` → `SKIPPED` |
| `renice -p <pid>` | ESRCH | PID not found | `renice()` → `SKIPPED` |
| `ionice -p <pid>` | ESRCH | PID not found | `ionice()` → `SKIPPED` |
| `taskset -p <pid>` | ESRCH | PID not found | `get_cpu_affinity()` → `None`; `set_cpu_affinity()` → `SKIPPED` |
| `lsof -p <pid>` | `1` | PID not found | `list_open_files()` → `[]` |

All other non-zero exit codes from read-only commands raise
`ProcessQueryError`; mutating commands map to `ProcessActionResult`
with `status=FAILED`.

### Idempotency Guards

Before executing `renice` or `set_cpu_affinity`, `ProcessManager` calls
`query_process()` (for nice) or `get_cpu_affinity()` (for affinity) to
determine current state. If the system is already in the desired state
the method returns `SKIPPED` without invoking the runner. This makes
these operations safe to call repeatedly.

---

## Module Layout

```
source/packages/automojo/sysman/linux/redhat/
├── __init__.py
└── processmanagement.py            ← ProcessManager, config, models, exceptions

source/testroots/testbase/
└── automojo/tests/sysman/linux/redhat/
    └── test_processmanagement.py   ← unit tests using MockCommandRunner + MockFileOperator
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` and a `MockFileOperator` injected
  into `ProcessManager` at construction time.
- Tests verify the *arguments* passed to the runner and the *paths*
  requested from the file operator, not system state.
- Each public method has at minimum: a success path test, a skip/not-found
  path test, a failure path test, and a dry-run test (write operations
  only).
- `wait_for_exit()` has additional tests for the polling cap and the
  dry-run short-circuit.
- `list_open_files()` has a test for the `lsof`-absent fallback, where
  the mock runner reports "command not found" and the mock file operator
  serves `/proc/<pid>/fd/` and `/proc/<pid>/maps`.
- No test requires root access or a live process to exist.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based
testing in Python. Each correctness property defined above will be
implemented as a property-based test that:

- Generates random but valid inputs (PIDs, patterns, nice values, masks,
  configurations)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific
correctness property they validate, using the format:
`# Feature: process-management, Property N: <property description>`

**Property Test Coverage:**

- Property 1: Parser tests generate random valid `ps` output lines and
  verify field mapping and args whitespace preservation.
- Property 2: Query tests generate random PIDs and verify `None` return
  on "not found" exit codes.
- Property 3: Signal tests generate random missing PIDs/patterns and
  verify `SKIPPED` status.
- Property 4: Graceful-escalation tests simulate mixed survivor/exiter
  process sets and verify only survivors receive `KILL`.
- Property 5: Renice tests generate random current/target nice pairs
  and verify the no-op path.
- Property 6: Affinity tests generate random masks/lists that match
  current affinity and verify `SKIPPED`.
- Property 7: Polling tests instrument the clock and assert maximum
  interval ≤ 500 ms across random timeouts.
- Property 8: Fallback tests generate random `/proc` file-operator
  responses and verify `OpenFile` record synthesis.
- Property 9: Dry-run tests iterate every write method with
  `dry_run=True` and assert runner/file-operator non-invocation.
- Property 10: Elevation tests generate random owner-UID vs
  effective-UID pairs and verify correct `elevated` flag usage.
- Property 11: Status-mapping tests generate random exit codes and
  verify `SUCCESS`/`FAILED`/`SKIPPED` classification.
- Property 12: Empty-match tests generate random non-matching
  patterns and verify `[]` return.

Both unit tests and property-based tests are complementary: unit tests
verify specific examples and edge cases, while property tests verify
universal correctness across many inputs.

---

## Usage Examples

### Local Execution (Default)

```python
from automojo.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from automojo.sysman.linux.redhat.fileops import LocalFileOperator
from automojo.sysman.linux.redhat.processmanagement import ProcessManager, ProcessManagerConfig

# Create local runner and file operator
runner = LocalCommandRunner(LocalRunnerConfig())
file_op = LocalFileOperator()

# Inject into process manager
config = ProcessManagerConfig(elevated=True, dry_run=False)
proc_manager = ProcessManager(runner, file_op, config)

# Find a runaway python script and gracefully terminate it
matching_pids = proc_manager.find_processes(pattern="scripts/runaway.py", full=True)
if len(matching_pids) > 0:
    result = proc_manager.signal_by_pattern(
        pattern="scripts/runaway.py",
        signal="TERM",
        graceful=True,
        grace_period=10.0,
    )
    print(f"Termination: {result.status.value}, matched={result.matched_count}")
```

### SSH Execution with Reliability Tracking

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.fileops import SSHFileOperator
from automojo.sysman.linux.redhat.processmanagement import ProcessManager, ProcessManagerConfig

# Create SSH runner and file operator for remote system
ssh_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0,
)
runner = SSHCommandRunner(ssh_config)
file_op = SSHFileOperator(runner)

# Inject into process manager (same code as local!)
config = ProcessManagerConfig(elevated=True)
proc_manager = ProcessManager(runner, file_op, config)

# Renice a stuck analytics worker across the fleet
target_pids = proc_manager.find_processes(pattern="analytics-worker", full=False)
for pid in target_pids:
    result = proc_manager.renice(pid=pid, nice_value=10)
    print(f"pid={pid} status={result.status.value}")

# Inspect SSH reliability metrics
metrics = runner.get_metrics()
print(f"Success rate: {metrics.success_count / (metrics.success_count + metrics.failure_count):.2%}")
print(f"Average duration: {metrics.avg_duration_ms:.2f}ms")
```

### SSH Execution with Aspects (Persistence + Retry + Timeout)

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.fileops import SSHFileOperator
from automojo.sysman.linux.redhat.aspects import PersistenceAspect, RetryAspect, TimeoutAspect
from automojo.sysman.linux.redhat.aspects import PersistenceAspectConfig, RetryAspectConfig, TimeoutAspectConfig
from automojo.sysman.linux.redhat.storage import SQLiteStorage
from automojo.sysman.linux.redhat.processmanagement import ProcessManager, ProcessManagerConfig

# Create SSH runner and file operator
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))
file_op = SSHFileOperator(runner)

# Persistence aspect for audit logging
storage = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

# Retry aspect for transient SSH failures during long wait loops
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[255],
    retryable_error_patterns=["Connection reset", "Temporary failure"],
))
runner.add_aspect(retry)

# Timeout aspect for global policy
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=300.0))
runner.add_aspect(timeout)

# Wire up process manager
proc_manager = ProcessManager(runner, file_op, ProcessManagerConfig(elevated=True))

# Orchestrated shutdown workflow: terminate, then wait-for-exit with timeout
stop_result = proc_manager.signal_by_pattern(
    pattern="batch-loader",
    signal="TERM",
    graceful=False,
)

if stop_result.status.value == "success":
    remaining = proc_manager.find_processes(pattern="batch-loader", full=True)
    for pid in remaining:
        wait_result = proc_manager.wait_for_exit(pid=pid, timeout=30.0)
        if wait_result.exited is False:
            proc_manager.signal_pid(pid=pid, signal="KILL")
```
