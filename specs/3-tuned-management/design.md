# Design

## Overview

The tuned management feature is implemented as a single module,
`ccai.sysman.linux.redhat.tunedmanagement`, that exposes a `TunedManager` class.
The coordinator instantiates `TunedManager` and calls its methods to perform discrete
maintenance actions against the `tuned` performance-tuning subsystem.

All system command interaction is delegated to a command runner implementing the
`ICommandRunner` interface, and all filesystem interaction under `/etc/tuned/` is
delegated to a file operator implementing the `IFileOperator` interface. The
`TunedManager` never directly calls `subprocess`, `os`, or `pathlib`; it only
constructs command argument lists and file payloads, then passes them to the
injected dependencies. This keeps business logic separate from execution and makes
the feature fully testable via a mock runner and mock file operator without root
access or a live system.

The feature works identically whether commands execute locally (via
`LocalCommandRunner` and `LocalFileOperator`) or remotely over SSH (via
`SSHCommandRunner` and `SSHFileOperator`). When using SSH, the coordinator can track
reliability metrics and apply execution aspects (persistence, retry, timeout)
without any changes to the `TunedManager` code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md)
for details on local/SSH execution, reliability tracking, and aspect configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner + file operator (Local or SSH)       │
│  - Configures aspects (optional)                            │
│  - Injects both into TunedManager                           │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            TunedManager                                     │
│  ccai.sysman.linux.redhat.tunedmanagement               │
│                                                             │
│  get_service_state()                                        │
│  lifecycle_action(action)                                   │
│  list_profiles()   get_active_profile()                     │
│  apply_profile(name)    profile_off()                       │
│  recommend_profile()    verify_profile()                    │
│  install_custom_profile(name, body, payload)                │
│  remove_custom_profile(name)                                │
└─────────┬──────────────────────────────────┬────────────────┘
          │ delegates commands to            │ delegates files to
          ▼                                  ▼
┌──────────────────────────────┐  ┌──────────────────────────────┐
│        ICommandRunner        │  │        IFileOperator         │
│  run(args, elevated, ...)    │  │  read / write / remove /     │
│       → RunResult            │  │  mkdir / exists / stat       │
└──────────────┬───────────────┘  └──────────────┬───────────────┘
               │                                 │
     ┌─────────┴─────────┐               ┌───────┴─────────┐
     ▼                   ▼               ▼                 ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│ LocalCommand     │ │ SSHCommand       │ │ LocalFile        │ │ SSHFile          │
│ Runner           │ │ Runner           │ │ Operator         │ │ Operator         │
│ - subprocess.run │ │ - SSH connection │ │ - local fs I/O   │ │ - sftp / channel │
│ - sudo elevation │ │ - reliability    │ │ - sudo tee write │ │ - remote writes  │
└──────────────────┘ └──────────────────┘ └──────────────────┘ └──────────────────┘
          │                   │                   │                   │
          └───────────────────┴───────┬───────────┴───────────────────┘
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

### `TunedManager`

**Location:** `source/packages/ccai/sysman/linux/redhat/tunedmanagement.py`

The primary public class. Constructed with an `ICommandRunner`, an `IFileOperator`,
and a `TunedManagerConfig`.

| Method | Description |
|---|---|
| `get_service_state()` → `TunedServiceState` | Query `tuned.service` active/enabled/present status |
| `lifecycle_action(action)` → `TunedActionResult` | Perform `start` / `stop` / `restart` / `enable` / `disable` on `tuned.service` |
| `list_profiles()` → `list[TunedProfile]` | List all stock and custom profiles known to tuned |
| `get_active_profile()` → `str \| None` | Return the name of the currently active profile, or `None` |
| `apply_profile(name)` → `TunedActionResult` | Activate the named profile via `tuned-adm profile <name>` |
| `profile_off()` → `TunedActionResult` | Disable the active profile via `tuned-adm off` |
| `recommend_profile()` → `str \| None` | Return tuned's recommended profile for the system |
| `verify_profile()` → `TunedVerifyResult` | Check whether the active profile's settings are intact |
| `install_custom_profile(name, body, payload=None)` → `TunedActionResult` | Write a `/etc/tuned/<name>/tuned.conf` (and optional payload files) |
| `remove_custom_profile(name)` → `TunedActionResult` | Recursively remove `/etc/tuned/<name>/` |

### `TunedManagerConfig`

**Location:** `source/packages/ccai/sysman/linux/redhat/tunedmanagement.py`

A dataclass holding runtime configuration for `TunedManager`.

| Field      | Type   | Default  | Description                                                      |
|------------|--------|----------|------------------------------------------------------------------|
| `dry_run`  | `bool` | `False`  | When `True`, no commands, writes, or deletes are executed        |
| `elevated` | `bool` | `False`  | When `True`, mutating commands/writes are passed with elevation  |

---

## Data Models

### `TunedActionStatus`

```python
class TunedActionStatus(Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"   # already in desired state, no action needed
    DRY_RUN  = "dry_run"   # would have acted, but dry_run=True
```

### `TunedActionResult`

Returned by all write / lifecycle operations (lifecycle_action, apply_profile,
profile_off, install_custom_profile, remove_custom_profile).

```python
@dataclass
class TunedActionResult:
    status: TunedActionStatus
    action: str                   # e.g. "apply_profile", "start", "install_custom"
    target: str | None            # profile name or service verb being acted on
    exit_code: int | None         # raw exit code from command runner (when applicable)
    stdout: str | None
    stderr: str | None
    command: list[str] | None     # the command that was (or would have been) run
    error_message: str | None     # populated on FAILED with typed context
```

### `TunedServiceState`

Returned by `get_service_state()`.

```python
@dataclass
class TunedServiceState:
    present: bool                 # False if systemctl reports the unit is missing
    active: bool | None           # None when present=False
    enabled: bool | None          # None when present=False
```

### `TunedProfile`

Returned as elements of `list_profiles()`.

```python
@dataclass
class TunedProfile:
    name: str
    description: str
    active: bool                  # True if this is the current active profile
```

### `TunedVerifyResult`

Returned by `verify_profile()`.

```python
@dataclass
class TunedVerifyResult:
    success: bool
    mismatches: list[str]         # lines describing drift when success=False
    stdout: str
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid
executions of a system — essentially, a formal statement about what the system
should do. Properties serve as the bridge between human-readable specifications
and machine-verifiable correctness guarantees.*

### Property 1: Apply-Profile Idempotency

*For any* profile name, calling `apply_profile(name)` when `name` is already the
active profile SHALL return `SKIPPED` without invoking the command runner.

**Validates: Requirements 5.3**

### Property 2: Apply-When-Service-Not-Active Fails Cleanly

*For any* `apply_profile(name)` call made while `tuned.service` is not active,
the method SHALL return `TunedActionResult` with status `FAILED` and a descriptive
error message, and SHALL NOT invoke `tuned-adm profile`.

**Validates: Requirements 5.2**

### Property 3: Profile-Not-Available Fails Cleanly

*For any* `apply_profile(name)` call where `name` is not in the output of
`tuned-adm list`, the method SHALL return `FAILED` with an error listing the
missing name and SHALL NOT invoke `tuned-adm profile`.

**Validates: Requirements 5.4**

### Property 4: Off-When-No-Profile Skip

*For any* `profile_off()` call made while no profile is currently active, the
method SHALL return `SKIPPED` without invoking the command runner.

**Validates: Requirements 6.2**

### Property 5: Custom Profile Install Byte-for-Byte Skip

*For any* `install_custom_profile(name, body)` call, if `tuned.conf` already
exists under `/etc/tuned/<name>/` with content equal to `body` byte-for-byte AND
no additional payload is requested, the method SHALL return `SKIPPED` without
invoking the file operator write path.

**Validates: Requirements 9.3**

### Property 6: Custom Profile Remove When Absent Skip

*For any* `remove_custom_profile(name)` call where `/etc/tuned/<name>/` does not
exist, the method SHALL return `SKIPPED` without invoking the file operator
delete path.

**Validates: Requirements 9.4**

### Property 7: Active Profile Removal Refusal

*For any* `remove_custom_profile(name)` call where `name` equals the currently
active profile, the method SHALL return `FAILED` with an error indicating the
profile must be deactivated first, and SHALL NOT invoke the file operator delete
path.

**Validates: Requirements 9.5**

### Property 8: Dry-Run Safety

*For any* mutating operation (lifecycle_action, apply_profile, profile_off,
install_custom_profile, remove_custom_profile) with `dry_run=True`, the command
runner SHALL NOT be invoked with a mutating command, the file operator SHALL NOT
be invoked with a write or delete, and the result status SHALL be `DRY_RUN`.

**Validates: Requirements 2.5, 5.6, 6.4, 9.6**

### Property 9: Elevation Consistency

*For any* mutating operation with `elevated=True` in the config, the command
runner or file operator SHALL be invoked with `elevated=True`; for read-only
query operations, `elevated` SHALL never be `True`.

**Validates: Requirements 10.1, 10.2, 10.3**

### Property 10: Status Mapping Correctness

*For any* operation result from the command runner, the `TunedActionResult`
status SHALL correctly reflect the runner's exit code: 0 maps to `SUCCESS`,
non-zero maps to `FAILED` (unless the operation was skipped or in dry-run
mode). For `TunedServiceState`, `active` reflects `systemctl is-active`
returning exit code 0, and `enabled` reflects `systemctl is-enabled` returning
exit code 0.

**Validates: Requirements 1.2, 2.3, 2.4, 5.5, 6.3, 8.2, 8.3**

---

## Interfaces

### `ICommandRunner` (from `codecraftai-interfaces`)

`TunedManager` depends on this interface, injected at construction time.

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

`TunedManager` depends on this interface, injected at construction time. It is
used exclusively for reads, writes, and removals under `/etc/tuned/`.

```python
class IFileOperator(Protocol):
    def exists(self, path: str) -> bool: ...
    def read_bytes(self, path: str) -> bytes: ...
    def write_bytes(
        self,
        path: str,
        data: bytes,
        mode: int = 0o644,
        owner: str = "root",
        group: str = "root",
        elevated: bool = False,
    ) -> None: ...
    def mkdir(
        self,
        path: str,
        mode: int = 0o755,
        elevated: bool = False,
        parents: bool = True,
    ) -> None: ...
    def remove_tree(self, path: str, elevated: bool = False) -> None: ...
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

When `TunedManagerConfig.elevated` is `True`, the `TunedManager` passes
`elevated=True` to `ICommandRunner.run()` and to `IFileOperator` mutating
methods. Each transport is responsible for prepending `sudo` or using whatever
privilege escalation mechanism is appropriate for the environment.
`TunedManager` has no knowledge of how elevation is implemented.

---

## Command Mappings

| Operation                    | Command / File Operation                                           |
|------------------------------|--------------------------------------------------------------------|
| Query service active         | `systemctl is-active tuned`                                        |
| Query service enabled        | `systemctl is-enabled tuned`                                       |
| Start service                | `systemctl start tuned`                                            |
| Stop service                 | `systemctl stop tuned`                                             |
| Restart service              | `systemctl restart tuned`                                          |
| Enable service               | `systemctl enable tuned`                                           |
| Disable service              | `systemctl disable tuned`                                          |
| List profiles                | `tuned-adm list`                                                   |
| Get active profile           | `tuned-adm active`                                                 |
| Recommend profile            | `tuned-adm recommend`                                              |
| Apply profile                | `tuned-adm profile <name>`                                         |
| Profile off                  | `tuned-adm off`                                                    |
| Verify profile               | `tuned-adm verify`                                                 |
| Install custom profile (dir) | `mkdir /etc/tuned/<name>/` (mode `0755`, via IFileOperator)        |
| Install custom profile (conf)| write `/etc/tuned/<name>/tuned.conf` (mode `0644`, root:root)      |
| Install custom payload file  | write `/etc/tuned/<name>/<payload_path>` (supplied mode)           |
| Remove custom profile        | recursive delete of `/etc/tuned/<name>/` (via IFileOperator)       |

---

## Error Handling

### Typed Exceptions

| Exception         | Raised When                                                                    |
|-------------------|--------------------------------------------------------------------------------|
| `TunedQueryError` | Any read-only query (`systemctl is-*`, `tuned-adm list\|active\|recommend\|verify`) fails for a reason other than a valid reported state |

All other failures (failed lifecycle actions, failed applies, failed writes) are
surfaced as a `TunedActionResult` with `status=FAILED` so the coordinator can
decide how to respond without catching exceptions for normal operational
failures.

### `systemctl is-active` / `is-enabled` Exit Codes

`systemctl is-active tuned` returns exit code `0` for active and `3` for
inactive; `systemctl is-enabled tuned` returns exit code `0` for enabled and
`1` for disabled. Both conditions are valid states (not errors) and are mapped
onto `TunedServiceState`. A `systemctl` error indicating the unit is not
present (stderr containing `not-found` or `could not be found`) is mapped to
`present=False`. Any other exit code / stderr combination raises
`TunedQueryError`.

### `tuned-adm verify` Exit Code 1

`tuned-adm verify` exits `0` on success and `1` on drift. Both are valid
verify outcomes mapped onto `TunedVerifyResult`. Any other exit code raises
`TunedQueryError`.

### Idempotency Guards

Before executing mutating commands, `TunedManager` calls the appropriate query
method (`get_service_state`, `get_active_profile`, `list_profiles`, file
operator `exists` + `read_bytes`) to determine current state. If the system is
already in the desired state the method returns `SKIPPED` without invoking the
runner or file operator write path. This makes all mutating operations safe to
call multiple times.

---

## Module Layout

```
source/packages/ccai/sysman/linux/redhat/
├── __init__.py
└── tunedmanagement.py           ← TunedManager, config, models, exceptions

source/testroots/testbase/
└── ccai/tests/sysman/linux/redhat/
    └── test_tunedmanagement.py  ← unit + property tests using mock runner
                                   and mock file operator
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` and `MockFileOperator` injected into
  `TunedManager` at construction time.
- Tests verify the *arguments* passed to the runner and file operator, not
  system state.
- Each public method has at minimum: a success path test, a skip/idempotency
  test, a failure path test, and a dry-run test.
- `get_service_state()` has additional tests for `is-active` exit code `3`,
  `is-enabled` exit code `1`, and the unit-not-found path.
- `verify_profile()` has additional tests for exit code `0` (clean) and `1`
  (drift with mismatches).
- `install_custom_profile()` has tests for byte-for-byte skip, directory
  creation, and payload file writes.
- `remove_custom_profile()` has tests for the absent-directory skip and the
  active-profile refusal.
- No test requires root access, a live `tuned` installation, or an SSH
  connection.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in
Python. Each correctness property defined above will be implemented as a
property-based test that:

- Generates random but valid inputs (profile names, service states, verify
  outputs, byte payloads)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific
correctness property they validate, using the format:
`# Feature: tuned-management, Property N: <property description>`

**Property Test Coverage:**

- Property 1: Apply-profile idempotency tests generate random profile names
  and matching active-profile state
- Property 2: Apply-when-service-not-active tests generate random `active`
  flags and assert failure when inactive
- Property 3: Profile-not-available tests generate random missing names
- Property 4: Off-when-no-profile tests generate the `None` active-profile case
- Property 5: Custom-install byte-for-byte tests generate random bodies and
  assert skip when content matches
- Property 6: Custom-remove absent tests generate random names with non-existent
  directories
- Property 7: Active-profile removal refusal tests generate the name-matches-active
  case
- Property 8: Dry-run safety tests verify no runner or file operator mutation
  across all operations
- Property 9: Elevation tests verify correct elevation flag propagation
- Property 10: Status mapping tests verify correct result interpretation from
  random exit codes

Both unit tests and property-based tests are complementary: unit tests verify
specific examples and edge cases, while property tests verify universal
correctness across many inputs.

---

## Usage Examples

### Local Execution (Default)

```python
from ccai.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from ccai.sysman.linux.redhat.fileops import LocalFileOperator
from ccai.sysman.linux.redhat.tunedmanagement import (
    TunedManager, TunedManagerConfig
)

# Create local runner and file operator
runner = LocalCommandRunner(LocalRunnerConfig())
fileop = LocalFileOperator()

# Inject into tuned manager
config = TunedManagerConfig(elevated=True, dry_run=False)
tuned = TunedManager(runner, fileop, config)

# Apply the throughput-performance profile
result = tuned.apply_profile(name="throughput-performance")
print(f"Apply: {result.status}")
```

### SSH Execution with Reliability Tracking (Verify Profile Drift Across a Fleet)

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.fileops import SSHFileOperator
from ccai.sysman.linux.redhat.tunedmanagement import (
    TunedManager, TunedManagerConfig
)

fleet_hosts = ["host-01", "host-02", "host-03"]
drift_report = []

for host in fleet_hosts:
    ssh_config = SSHRunnerConfig(
        host=host,
        username="admin",
        key_filename="/home/user/.ssh/id_rsa",
        max_connection_retries=5,
        keepalive_interval=60.0,
    )
    runner = SSHCommandRunner(ssh_config)
    fileop = SSHFileOperator(ssh_config)

    # Same code as local
    config = TunedManagerConfig(elevated=False)
    tuned = TunedManager(runner, fileop, config)

    verify_result = tuned.verify_profile()
    if not verify_result.success:
        drift_report.append((host, verify_result.mismatches))

    # Check SSH reliability metrics for this host
    metrics = runner.get_metrics()
    total_calls = metrics.success_count + metrics.failure_count
    success_rate = metrics.success_count / total_calls if total_calls > 0 else 0.0
    print(f"{host}: success_rate={success_rate:.2%}, retries={metrics.retry_count}")

print(f"Hosts with drift: {len(drift_report)}")
```

### SSH Execution with Aspects

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.fileops import SSHFileOperator
from ccai.sysman.linux.redhat.aspects import (
    PersistenceAspect, RetryAspect, TimeoutAspect,
    PersistenceAspectConfig, RetryAspectConfig, TimeoutAspectConfig,
)
from ccai.sysman.linux.redhat.storage import SQLiteStorage
from ccai.sysman.linux.redhat.tunedmanagement import (
    TunedManager, TunedManagerConfig
)

# Create SSH runner and file operator
ssh_config = SSHRunnerConfig(host="remote-server", username="admin")
runner = SSHCommandRunner(ssh_config)
fileop = SSHFileOperator(ssh_config)

# Add persistence aspect for audit logging
storage = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(
    PersistenceAspectConfig(storage_backend=storage)
)
runner.add_aspect(persistence)

# Add retry aspect for transient failures
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[255],
    retryable_error_patterns=["Connection reset", "Temporary failure"],
))
runner.add_aspect(retry)

# Add timeout aspect
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=300.0))
runner.add_aspect(timeout)

# Inject into tuned manager
config = TunedManagerConfig(elevated=True)
tuned = TunedManager(runner, fileop, config)

# Install a custom profile; all writes now have:
# - Audit logging to SQLite
# - Automatic retry on transient failures
# - 5-minute timeout enforcement
conf_body = b"[main]\nsummary=Site-specific low-latency tuning\n\n[cpu]\ngovernor=performance\n"
result = tuned.install_custom_profile(
    name="site-lowlatency",
    body=conf_body,
)
print(f"Install: {result.status}")
```
