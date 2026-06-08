# Design

## Overview

The insights management feature is implemented as a single module,
`ccai.sysman.linux.redhat.insightsmanagement`, that exposes an `InsightsManager`
class. The coordinator instantiates `InsightsManager` and calls its methods to perform
discrete Red Hat Insights lifecycle actions: registration state queries, registration
and unregistration, on-demand uploads, periodic check-ins, local collection without
upload, version discovery, connection testing, and management of the Insights client
configuration file and tag metadata file.

All command execution is delegated to an object implementing the `ICommandRunner`
interface. All reads and writes against `/etc/insights-client/insights-client.conf`
and `/etc/insights-client/tags.yaml` are delegated to an object implementing the
`IFileOperator` interface. The `InsightsManager` never directly calls `subprocess`,
`open()`, or shell commands; it only constructs command argument lists and file-
operator calls. This keeps business logic separate from execution and makes the
feature fully testable via mock runners and file operators without root access or a
live system.

The feature works identically whether commands execute locally (via
`LocalCommandRunner` / `LocalFileOperator`) or remotely over SSH (via
`SSHCommandRunner` / `SSHFileOperator`). When using SSH, the coordinator can track
reliability metrics and apply execution aspects (persistence, retry, timeout) without
any changes to the `InsightsManager` code.

This feature depends on two sibling features:

- **`package-management`** — the `insights-client` RPM must already be installed on
  the target system. `InsightsManager` does not install packages itself; if the
  binary is missing it raises `InsightsNotInstalledError` and defers remediation to
  the coordinator.
- **`subscription-management`** — the system must be registered with Red Hat
  Subscription Management (RHSM) before Insights registration can succeed. RHSM
  registration is the responsibility of `subscription-management` and is not
  performed or validated here; `insights-client --register` will surface an
  actionable failure if RHSM is missing.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md)
for details on local/SSH execution, reliability tracking, and aspect configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner + file operator                      │
│  - Configures aspects (optional)                            │
│  - Injects dependencies into InsightsManager                │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            InsightsManager                                  │
│  ccai.sysman.linux.redhat.insightsmanagement            │
│                                                             │
│  get_registration_state()   get_version()                   │
│  register()   unregister()                                  │
│  upload()     checkin()     collect_no_upload()             │
│  test_connection()                                          │
│  get_config()   set_config()   delete_config()              │
│  read_tags()    write_tags()                                │
└───────────┬────────────────────────────────┬────────────────┘
            │ delegates commands to          │ delegates file I/O to
            ▼                                ▼
┌──────────────────────────┐      ┌──────────────────────────┐
│     ICommandRunner       │      │      IFileOperator       │
│  run(args, elevated,     │      │  read(path, elevated)    │
│      cwd, env, timeout)  │      │  write(path, data,       │
│    → RunResult           │      │    mode, owner, group,   │
│                          │      │    elevated)             │
│                          │      │  exists(path)            │
└─────────┬────────────────┘      └─────────┬────────────────┘
          │                                 │
          ├─────────────────┬───────────────┤
          ▼                 ▼               ▼
┌──────────────────┐  ┌──────────────────────┐
│ Local (runner +  │  │ SSH (runner +        │
│ file operator)   │  │ file operator)       │
│ - subprocess     │  │ - paramiko / SFTP    │
│ - sudo elevation │  │ - reliability metrics│
└─────────┬────────┘  └─────────┬────────────┘
          │                     │
          └──────────┬──────────┘
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

### `InsightsManager`

**Location:** `source/packages/ccai/sysman/linux/redhat/insightsmanagement.py`

The primary public class. Constructed with an `ICommandRunner`, an `IFileOperator`,
and an `InsightsManagerConfig`.

| Method | Description |
|---|---|
| `get_registration_state()` → `InsightsState` | Query registered/unregistered state and machine-id |
| `register(display_name=None, group=None, force=False)` → `InsightsActionResult` | Register with Insights; idempotent unless `force=True` |
| `unregister()` → `InsightsActionResult` | Unregister from Insights; idempotent when already unregistered |
| `upload()` → `InsightsActionResult` | Run the default collect-and-upload action |
| `checkin()` → `InsightsActionResult` | Send a lightweight check-in without a full collection |
| `collect_no_upload()` → `InsightsActionResult` | Collect the payload locally without transmitting |
| `get_version()` → `InsightsVersion` | Return the installed client version |
| `test_connection()` → `InsightsConnectionResult` | Exercise the client's connectivity to the Insights service |
| `get_config(key)` → `str \| None` | Read a key from `/etc/insights-client/insights-client.conf` |
| `set_config(key, value)` → `InsightsActionResult` | Set or replace a key in the `[insights-client]` section |
| `delete_config(key)` → `InsightsActionResult` | Remove a key from the `[insights-client]` section |
| `read_tags()` → `dict[str, object]` | Parse and return `/etc/insights-client/tags.yaml` |
| `write_tags(tags)` → `InsightsActionResult` | Serialize and write `/etc/insights-client/tags.yaml` |

### `InsightsManagerConfig`

**Location:** `source/packages/ccai/sysman/linux/redhat/insightsmanagement.py`

A dataclass holding runtime configuration for `InsightsManager`.

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no commands are executed and no files are written |
| `elevated` | `bool` | `False` | When `True`, commands and file writes are performed with elevation |

---

## Data Models

### `InsightsActionStatus`

```python
class InsightsActionStatus(Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"   # already in desired state, no action needed
    DRY_RUN  = "dry_run"   # would have acted, but dry_run=True
```

### `InsightsActionResult`

Returned by all write operations (register, unregister, upload, checkin,
collect_no_upload, set_config, delete_config, write_tags).

```python
@dataclass
class InsightsActionResult:
    status: InsightsActionStatus
    exit_code: int | None            # raw exit code from command runner
    stdout: str | None
    stderr: str | None
    command: list[str] | None        # the command that was (or would have been) run
    machine_id: str | None           # set by register() on success, when available
    payload_path: str | None         # set by collect_no_upload() on success
    detail: str | None               # human-readable detail, e.g. "not registered"
```

### `InsightsState`

Returned by `get_registration_state()`.

```python
@dataclass
class InsightsState:
    registered: bool
    machine_id: str | None
```

### `InsightsVersion`

Returned by `get_version()`.

```python
@dataclass
class InsightsVersion:
    client_version: str
    core_version: str | None
```

### `InsightsConnectionResult`

Returned by `test_connection()`.

```python
@dataclass
class InsightsConnectionResult:
    ok: bool
    output: str
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid
executions of a system — essentially, a formal statement about what the system
should do. Properties serve as the bridge between human-readable specifications and
machine-verifiable correctness guarantees.*

### Property 1: Register Idempotency

*For any* system already registered with Insights, calling `register()` without
`force=True` should return `SKIPPED` without invoking the command runner. Calling
`register(force=True)` should always invoke `insights-client --register
--force-reregister`, regardless of current state.

**Validates: Requirements 2.4, 2.5**

### Property 2: Unregister Idempotency

*For any* system that is not currently registered, calling `unregister()` should
return `SKIPPED` without invoking the command runner.

**Validates: Requirements 3.2**

### Property 3: Upload Requires Registration

*For any* call to `upload()`, `checkin()`, or `collect_no_upload()` on a system that
is not registered, the operation should fail cleanly with status `FAILED` and a
detail string explaining that registration is required, without invoking
`insights-client` for the upload.

**Validates: Requirements 4.3**

### Property 4: State Query Freshness

*For any* call to `get_registration_state()`, the returned `InsightsState` should
reflect the result of the most recent `insights-client --status` invocation, not a
cached value. Two sequential calls that observe different runner outputs should
return different `InsightsState` values.

**Validates: Requirements 1.1, 1.2, 1.3**

### Property 5: Version Stability

*For any* call to `get_version()`, if the command succeeds, repeated calls against
the same runner output should yield `InsightsVersion` records that compare equal.

**Validates: Requirements 6.1, 6.2**

### Property 6: Config Set Idempotency

*For any* call to `set_config(key, value)` where the current on-disk value already
equals `value`, the operation should return `SKIPPED` and must not invoke the file
operator's write method.

**Validates: Requirements 8.4**

### Property 7: Tags YAML Equivalence Skip

*For any* call to `write_tags(tags)` where the current on-disk YAML parses to a dict
equal to `tags`, the operation should return `SKIPPED`. Equivalence is decided by
comparing parsed YAML structures, never by byte-level comparison (so whitespace,
key order, and quoting differences do not force a rewrite).

**Validates: Requirements 9.2**

### Property 8: Dry-Run Safety

*For any* write operation (register, unregister, upload, checkin, collect_no_upload,
set_config, delete_config, write_tags) with `dry_run=True`, the command runner
should never be invoked with a write command and the file operator should never be
invoked with a write call. The result status should be `DRY_RUN`.

**Validates: Requirements 2.8, 3.5, 4.6, 5.5, 8.6, 9.4**

### Property 9: Elevation Consistency

*For any* `insights-client` invocation or write under `/etc/insights-client/`,
`InsightsManagerConfig.elevated=True` should cause the dependency to be invoked
with `elevated=True`. Pure reads of `/etc/insights-client/` files may transparently
use elevation when the unprivileged read would fail.

**Validates: Requirements 10.1, 10.2, 10.3**

### Property 10: Status Mapping Correctness

*For any* operation result from the command runner, the `InsightsActionResult`
status should correctly reflect the runner's exit code: exit code `0` maps to
`SUCCESS`, non-zero maps to `FAILED`, unless the operation was skipped or in
dry-run mode. The `exit_code`, `stdout`, and `stderr` fields should always be
populated from the `RunResult`.

**Validates: Requirements 2.6, 2.7, 3.3, 3.4, 4.4, 4.5, 5.3, 5.4, 7.2, 7.3**

---

## Interfaces

### `ICommandRunner` (from `codecraftai-interfaces`)

`InsightsManager` depends on this interface, injected at construction time.

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

`InsightsManager` depends on this interface, injected at construction time.

```python
class IFileOperator(Protocol):
    def exists(self, path: str) -> bool: ...
    def read(self, path: str, elevated: bool = False) -> bytes: ...
    def write(
        self,
        path: str,
        data: bytes,
        mode: int = 0o644,
        owner: str | None = None,
        group: str | None = None,
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

When `InsightsManagerConfig.elevated` is `True`, the `InsightsManager` passes
`elevated=True` to `ICommandRunner.run()` for every `insights-client` invocation
and to `IFileOperator.write()` for every write under `/etc/insights-client/`. The
runner and file operator are responsible for prepending `sudo` or using whatever
privilege escalation mechanism is appropriate for the environment. `InsightsManager`
has no knowledge of how elevation is implemented. When reading configuration files,
`InsightsManager` may transparently elevate the read if the unprivileged attempt
reports a permission error, so that reads remain usable when the config file mode
excludes the calling user.

---

## Command Mappings

| Operation | Command / Path |
|---|---|
| Query registration state | `insights-client --status` |
| Register | `insights-client --register` |
| Register with display name | `insights-client --register --display-name=<value>` |
| Register into group | `insights-client --register --group=<value>` |
| Force re-registration | `insights-client --register --force-reregister` |
| Unregister | `insights-client --unregister` |
| Default upload (collect + upload) | `insights-client` |
| Lightweight check-in | `insights-client --checkin` |
| Collect without uploading | `insights-client --no-upload` |
| Query client version | `insights-client --version` |
| Test connection to Insights | `insights-client --test-connection` |
| Client configuration file | `/etc/insights-client/insights-client.conf` (INI, `[insights-client]` section) |
| Tag metadata file | `/etc/insights-client/tags.yaml` (YAML mapping) |

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `InsightsQueryError` | `insights-client --status` or `--version` returns an unexpected non-zero exit code |
| `InsightsNotInstalledError` | The `insights-client` binary is absent from the target system |
| `InsightsPermissionError` | The command runner reports a permission-denied failure that elevation cannot resolve |

All other failures are surfaced as an `InsightsActionResult` with `status=FAILED`
so the coordinator can decide how to respond without catching exceptions for normal
operational failures.

### Idempotency Guards

Before executing write commands, `InsightsManager` inspects current state:

- `register()` calls `get_registration_state()` and returns `SKIPPED` when already
  registered unless `force=True`.
- `unregister()` calls `get_registration_state()` and returns `SKIPPED` when not
  registered.
- `upload()`, `checkin()`, and `collect_no_upload()` call `get_registration_state()`
  and return `FAILED` with an explanatory `detail` when not registered.
- `set_config()` reads the current value and returns `SKIPPED` when the value
  already matches.
- `write_tags()` parses the current YAML and returns `SKIPPED` when the parsed
  structure equals the requested dict.

### Missing Binary Detection

`InsightsManager` detects a missing binary by inspecting the `RunResult` of
`insights-client --status` or `--version`. An exit code combined with stderr
indicating `command not found` or `No such file or directory` is reclassified as
`InsightsNotInstalledError` before it can surface as a generic failure.

---

## Module Layout

```
source/packages/ccai/sysman/linux/redhat/
├── __init__.py
└── insightsmanagement.py              ← InsightsManager, config, models, exceptions

source/testroots/testbase/
└── ccai/tests/sysman/linux/redhat/
    └── test_insightsmanagement.py     ← unit tests using MockCommandRunner + MockFileOperator
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` and a `MockFileOperator` injected into
  `InsightsManager` at construction time.
- Tests verify the *arguments* passed to the runner and the *path/data/mode/owner/
  group/elevated* passed to the file operator, not system state.
- Each public method has at minimum: a success path test, a skip/idempotency test,
  a failure path test, and a dry-run test.
- `get_registration_state()` has additional tests covering registered, unregistered,
  missing-binary, and unexpected-exit-code cases.
- `register()` has additional tests for `display_name`, `group`, and `force=True`
  argument wiring.
- `upload()`, `checkin()`, and `collect_no_upload()` have additional tests covering
  the "not registered" precondition.
- `set_config()` / `delete_config()` have tests covering section creation,
  comment preservation, and dry-run file-operator silence.
- `write_tags()` has a test verifying that YAML-equivalent content (different
  whitespace, key order) produces `SKIPPED`.
- No test requires root access or a live `insights-client` installation.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in
Python. Each correctness property defined above is implemented as a property-based
test that:

- Generates random but valid inputs (display names, groups, config keys/values,
  tag dicts, exit codes, state outputs)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific correctness
property they validate, using the format:
`# Feature: insights-management, Property N: <property description>`

**Property Test Coverage:**

- Property 1: Register idempotency tests generate random display names and groups
- Property 2: Unregister idempotency tests verify skip behavior when not registered
- Property 3: Upload precondition tests verify failure without registration
- Property 4: State query freshness tests verify no caching
- Property 5: Version stability tests verify equal records across repeated calls
- Property 6: Config set idempotency tests verify skip on equal values
- Property 7: Tags YAML equivalence tests generate semantically equal but textually
  different YAML to verify parsed-structure comparison
- Property 8: Dry-run safety tests verify no runner or file-operator writes across
  all write operations
- Property 9: Elevation consistency tests verify the elevated flag is forwarded
- Property 10: Status mapping tests verify result interpretation across exit codes

Both unit tests and property-based tests are complementary: unit tests verify
specific examples and edge cases, while property tests verify universal correctness
across many inputs.

---

## Usage Examples

### Local Execution (Register + Configure Display Name + Upload)

```python
from ccai.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from ccai.sysman.linux.redhat.fileops import LocalFileOperator, LocalFileOperatorConfig
from ccai.sysman.linux.redhat.insightsmanagement import (
    InsightsManager,
    InsightsManagerConfig,
)

# Create local runner and file operator
runner = LocalCommandRunner(LocalRunnerConfig())
file_operator = LocalFileOperator(LocalFileOperatorConfig())

# Inject into insights manager
config = InsightsManagerConfig(elevated=True, dry_run=False)
insights = InsightsManager(runner, file_operator, config)

# Register the host
register_result = insights.register(display_name="web-01.example.com")
print(f"Registration: {register_result.status}")

# Update a config key declaratively (skipped if already set)
config_result = insights.set_config(key="display_name", value="web-01.example.com")
print(f"Config write: {config_result.status}")

# Push an on-demand upload so the Insights view reflects the current host state
upload_result = insights.upload()
print(f"Upload: {upload_result.status}")
```

### SSH Execution with Reliability Tracking (Fleet Check-in)

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.fileops import SSHFileOperator, SSHFileOperatorConfig
from ccai.sysman.linux.redhat.insightsmanagement import (
    InsightsManager,
    InsightsManagerConfig,
)

hosts = ["10.0.0.11", "10.0.0.12", "10.0.0.13"]
for host in hosts:
    ssh_runner_config = SSHRunnerConfig(
        host=host,
        username="admin",
        key_filename="/home/user/.ssh/id_rsa",
        max_connection_retries=5,
        keepalive_interval=60.0,
    )
    runner = SSHCommandRunner(ssh_runner_config)
    file_operator = SSHFileOperator(SSHFileOperatorConfig(host=host, username="admin"))

    insights = InsightsManager(
        runner,
        file_operator,
        InsightsManagerConfig(elevated=True),
    )

    state = insights.get_registration_state()
    if state.registered is True:
        result = insights.checkin()
        print(f"{host}: checkin={result.status}")
    else:
        print(f"{host}: skipping checkin, not registered")

    metrics = runner.get_metrics()
    attempts = metrics.success_count + metrics.failure_count
    if attempts > 0:
        success_rate = metrics.success_count / attempts
        print(f"{host}: success_rate={success_rate:.2%}, retries={metrics.retry_count}")
```

### SSH Execution with Aspects (Daily Check-in Sweep)

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.fileops import SSHFileOperator, SSHFileOperatorConfig
from ccai.sysman.linux.redhat.aspects import (
    PersistenceAspect,
    PersistenceAspectConfig,
    RetryAspect,
    RetryAspectConfig,
    TimeoutAspect,
    TimeoutAspectConfig,
)
from ccai.sysman.linux.redhat.storage import SQLiteStorage
from ccai.sysman.linux.redhat.insightsmanagement import (
    InsightsManager,
    InsightsManagerConfig,
)

runner = SSHCommandRunner(SSHRunnerConfig(host="host-01", username="admin"))
file_operator = SSHFileOperator(SSHFileOperatorConfig(host="host-01", username="admin"))

# Persistence aspect for audit logging
storage = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

# Retry aspect for transient Insights upload failures and SSH glitches
retry_config = RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[1, 255],
    retryable_error_patterns=["Connection reset", "Temporary failure", "HTTP 5"],
)
retry = RetryAspect(retry_config)
runner.add_aspect(retry)

# Timeout aspect for the daily check-in sweep
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=300.0))
runner.add_aspect(timeout)

insights = InsightsManager(runner, file_operator, InsightsManagerConfig(elevated=True))

# All insights operations now have persistence, retry, and timeout enforcement
checkin_result = insights.checkin()
print(f"Checkin: {checkin_result.status}")
```
