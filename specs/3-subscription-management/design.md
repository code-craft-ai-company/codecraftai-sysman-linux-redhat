# Design

## Overview

The subscription management feature is implemented as a single module,
`automojo.sysman.linux.redhat.subscriptionmanagement`, that exposes a `SubscriptionManager`
class. The coordinator instantiates `SubscriptionManager` and calls its methods to perform
discrete maintenance actions against Red Hat Subscription Management (RHSM).

All system interaction is delegated to a command runner implementing the `ICommandRunner`
interface. The `SubscriptionManager` never directly calls `subprocess` or shell commands;
it only constructs `subscription-manager` argument lists and passes them to the runner.
Because `subscription-manager` mutating commands require root, all write operations run
elevated through the runner's elevation mechanism. Read-only query commands run without
elevation. This keeps business logic separate from execution and makes the feature fully
testable via a mock runner without root access or a live system.

The feature works identically whether commands execute locally (via `LocalCommandRunner`)
or remotely over SSH (via `SSHCommandRunner`). When using SSH, the coordinator can track
reliability metrics and apply execution aspects (persistence, retry, timeout) without any
changes to the `SubscriptionManager` code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md) for
details on local/SSH execution, reliability tracking, and aspect configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner (Local or SSH)                      │
│  - Configures aspects (optional)                           │
│  - Injects runner into SubscriptionManager                 │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            SubscriptionManager                              │
│  automojo.sysman.linux.redhat.subscriptionmanagement        │
│                                                             │
│  register()    unregister()    identity()                   │
│  status()      attach()        remove()                     │
│  list_repos()  enable_repo()   disable_repo()               │
│  set_release() unset_release() show_release()               │
│  list_releases()               refresh()                    │
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
│  - PersistenceAspect: audit logging (credentials redacted)  │
│  - RetryAspect: transient failure retry                     │
│  - TimeoutAspect: global timeout policy                     │
└─────────────────────────────────────────────────────────────┘
```

---

## Components

### `SubscriptionManager`

**Location:** `source/packages/automojo/sysman/linux/redhat/subscriptionmanagement.py`

The primary public class. Constructed with an `ICommandRunner` and a
`SubscriptionManagerConfig`.

| Method | Description |
|---|---|
| `register(username=None, password=None, org=None, activation_key=None, force=False)` | Register the system using credentials or an activation key |
| `unregister()` | Unregister the system from RHSM |
| `identity()` → `SubscriptionIdentity \| None` | Return the registered system identity or `None` |
| `status()` → `SubscriptionStatus` | Return the overall subscription status |
| `attach(auto=False, pool_ids=None, quantity=None)` | Attach subscriptions automatically or by pool id |
| `remove(all=False, serials=None)` | Remove attached subscriptions by `--all` or by serial list |
| `list_repos(filter=None)` → `list[SubscriptionRepo]` | List all, enabled-only, or disabled-only repositories |
| `enable_repo(repo_id)` | Enable a subscription-provided repository |
| `disable_repo(repo_id)` | Disable a subscription-provided repository |
| `set_release(version)` | Pin the system to a specific minor release |
| `unset_release()` | Clear any pinned release |
| `show_release()` → `str \| None` | Return the currently pinned release string |
| `list_releases()` → `list[str]` | Return the available release strings |
| `refresh()` | Force a refresh of subscription and entitlement data |

### `SubscriptionManagerConfig`

**Location:** `source/packages/automojo/sysman/linux/redhat/subscriptionmanagement.py`

A dataclass holding runtime configuration for `SubscriptionManager`.

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no commands are executed |
| `elevated` | `bool` | `False` | When `True`, write commands are run with elevation |

---

## Data Models

### `SubscriptionActionStatus`

```python
class SubscriptionActionStatus(Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"   # already in desired state, no action needed
    DRY_RUN  = "dry_run"   # would have acted, but dry_run=True
```

### `SubscriptionActionResult`

Returned by all write operations (register, unregister, attach, remove, enable_repo,
disable_repo, set_release, unset_release, refresh).

```python
@dataclass
class SubscriptionActionResult:
    status: SubscriptionActionStatus   # SUCCESS | FAILED | SKIPPED | DRY_RUN
    operation: str                     # e.g. "register", "attach", "enable_repo"
    system_uuid: str | None            # populated on successful registration
    attached_pool_ids: list[str] | None  # populated by successful attach()
    exit_code: int | None              # raw exit code from command runner
    stdout: str | None                 # redacted of credential values
    stderr: str | None                 # redacted of credential values
    command: list[str] | None          # redacted copy of the command that was run
```

### `SubscriptionIdentity`

Returned by `identity()`.

```python
@dataclass
class SubscriptionIdentity:
    uuid: str
    name: str
    organization: str
```

### `SubscriptionStatus`

Returned by `status()`.

```python
class SubscriptionOverallStatus(Enum):
    CURRENT      = "current"
    INSUFFICIENT = "insufficient"
    INVALID      = "invalid"
    DISABLED     = "disabled"
    UNKNOWN      = "unknown"


@dataclass
class SubscriptionStatus:
    overall: SubscriptionOverallStatus
    raw_output: str
```

### `SubscriptionRepo`

Returned as elements of `list_repos()`.

```python
@dataclass
class SubscriptionRepo:
    repo_id: str
    name: str
    url: str
    enabled: bool
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Idempotent Registration

*For any* call to `register()` when the system is already registered and `--force` is not
supplied, the command runner should not be invoked with a `register` command, and the
result status should be `SKIPPED`.

**Validates: Requirements 1.4**

### Property 2: Idempotent Unregistration

*For any* call to `unregister()` when the system is not registered, the command runner
should not be invoked, and the result status should be `SKIPPED`.

**Validates: Requirements 2.2**

### Property 3: Dry-Run Safety

*For any* mutating operation (register, unregister, attach, remove, enable_repo,
disable_repo, set_release, unset_release, refresh) with `dry_run=True`, the command runner
should never be invoked with a write command, and the result status should be `DRY_RUN`.

**Validates: Requirements 1.7, 2.5, 5.6, 6.6, 8.6, 9.6, 10.4**

### Property 4: Elevation Consistency

*For any* mutating subscription operation with `elevated=True` in the config, the command
runner should be invoked with `elevated=True`; for read-only operations (`identity`,
`status`, `repos --list*`, `release --show`, `release --list`), `elevated` should never
be `True`.

**Validates: Requirements 11.1, 11.2, 11.3**

### Property 5: Overall Status Mapping Correctness

*For any* `subscription-manager status` output, the `overall` field of the returned
`SubscriptionStatus` should correctly map each known textual value (`Current`,
`Insufficient`, `Invalid`, `Disabled`) to its corresponding `SubscriptionOverallStatus`
enum member, and any unrecognized value should map to `UNKNOWN`.

**Validates: Requirements 4.2, 4.3**

### Property 6: Command Construction Correctness

*For any* subscription operation, the constructed command arguments passed to the runner
should match the expected command format specified in the Command Mappings table.

**Validates: Requirements 1.1, 1.2, 1.3, 2.1, 3.1, 4.1, 5.1, 5.2, 5.3, 6.1, 6.2, 7.1, 7.2, 7.3, 8.1, 8.2, 9.1, 9.2, 9.3, 9.4, 10.1**

### Property 7: Repository Enable Idempotency

*For any* repository id, calling `enable_repo()` when the repository is already enabled
should return `SKIPPED` without invoking the command runner; the same property holds for
`disable_repo()` when the repository is already disabled.

**Validates: Requirements 8.3**

### Property 8: Release Set/Unset Idempotency

*For any* release version, calling `set_release()` with a version equal to the currently
pinned release should return `SKIPPED` without invoking the command runner; calling
`unset_release()` when no release is currently pinned should likewise return `SKIPPED`.

**Validates: Requirements 9.1, 9.2**

### Property 9: Attach Empty-Pool Handling

*For any* call to `attach()` with `auto=False` and an empty `pool_ids` list, the command
runner should not be invoked and the method should raise a `SemanticError`; when `auto`
is `True`, exactly one `attach --auto` command should be issued regardless of `pool_ids`.

**Validates: Requirements 5.1, 5.2**

### Property 10: Remove Empty-State Handling

*For any* call to `remove()` when no subscriptions are currently attached, the command
runner should not be invoked and the result status should be `SKIPPED`.

**Validates: Requirements 6.3**

### Property 11: Query Error Propagation

*For any* read-only query (`identity`, `status`, `list_repos`, `show_release`,
`list_releases`), if the command runner returns an unexpected non-zero exit code, a
`SubscriptionQueryError` carrying the exit code and captured stderr should be raised.

**Validates: Requirements 3.4, 4.4, 7.5**

### Property 12: Credential Redaction

*For any* command containing `--password <value>` or `--activationkey <value>`, the
`SubscriptionActionResult.command` field and any audited representation of the command
should replace the argument value with a fixed masking token while preserving the
presence of the flag. Captured stdout and stderr should have any literal credential
string replaced with the same token before being returned.

**Validates: Requirements 12.1, 12.2, 12.3, 12.4**

---

## Interfaces

### `ICommandRunner` (from `automojo-interfaces`)

`SubscriptionManager` depends on this interface, injected at construction time.

```python
class ICommandRunner(Protocol):
    def run(
        self,
        args: list[str],
        elevated: bool = False,
        capture_output: bool = True,
    ) -> RunResult: ...
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

When `SubscriptionManagerConfig.elevated` is `True`, the `SubscriptionManager` passes
`elevated=True` to `ICommandRunner.run()` for every mutating operation. The runner is
responsible for prepending `sudo` or using whatever privilege escalation mechanism is
appropriate for the environment. `SubscriptionManager` has no knowledge of how elevation
is implemented. Read-only query commands always pass `elevated=False`.

---

## Command Mappings

| Operation | Command |
|---|---|
| Register (credentials) | `subscription-manager register --username <user> --password <pass>` |
| Register (activation key) | `subscription-manager register --org <org-id> --activationkey <key>` |
| Register (force re-register) | `subscription-manager register ... --force` |
| Unregister | `subscription-manager unregister` |
| Query identity | `subscription-manager identity` |
| Query status | `subscription-manager status` |
| Attach auto | `subscription-manager attach --auto` |
| Attach by pool | `subscription-manager attach --pool <pool-id>` |
| Attach with quantity | `subscription-manager attach --pool <pool-id> --quantity <n>` |
| Remove all | `subscription-manager remove --all` |
| Remove by serial | `subscription-manager remove --serial <serial>` |
| List all repos | `subscription-manager repos --list` |
| List enabled repos | `subscription-manager repos --list-enabled` |
| List disabled repos | `subscription-manager repos --list-disabled` |
| Enable repo | `subscription-manager repos --enable <repo-id>` |
| Disable repo | `subscription-manager repos --disable <repo-id>` |
| Set release | `subscription-manager release --set <version>` |
| Unset release | `subscription-manager release --unset` |
| Show release | `subscription-manager release --show` |
| List releases | `subscription-manager release --list` |
| Refresh | `subscription-manager refresh` |

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `SubscriptionQueryError` | A read-only `subscription-manager` query returns an unexpected non-zero exit code (i.e. not the well-known not-registered code for `identity`) |
| `SubscriptionNotRegisteredError` | *(optional)* A mutating operation that requires registration is invoked against an unregistered system |

All other failures are surfaced as a `SubscriptionActionResult` with `status=FAILED` so
the coordinator can decide how to respond without catching exceptions for normal
operational failures.

### Idempotency Guards

Before executing mutating commands, `SubscriptionManager` calls the appropriate query
method (`identity()`, `list_repos()`, `show_release()`) to determine current state. When
the system is already in the desired state the method returns `SKIPPED` without invoking
`subscription-manager`. This makes all mutating operations safe to call multiple times.

### Credential Redaction

`subscription-manager register` accepts secrets on the command line via `--password` and
`--activationkey`. The `SubscriptionManager` never records the raw values. Before
constructing the `SubscriptionActionResult`, and before returning the command to the
persistence aspect for auditing, the module walks the argument list and replaces the
token immediately following `--password` and `--activationkey` with a fixed masking
token (`"***REDACTED***"`). The flag itself is preserved so audit consumers can see that
a credential was supplied without being exposed to it. Captured `stdout` and `stderr`
are scanned for the literal credential string and the same replacement is applied before
either field is attached to the returned result.

---

## Module Layout

```
source/packages/automojo/sysman/linux/redhat/
├── __init__.py
└── subscriptionmanagement.py          ← SubscriptionManager, config, models, exceptions

source/testroots/testbase/
└── automojo/tests/sysman/linux/redhat/
    └── test_subscriptionmanagement.py ← unit tests using MockCommandRunner
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` injected into `SubscriptionManager` at construction
  time.
- Tests verify the *arguments* passed to the runner, not system state.
- Each public method has at minimum: a success path test, a skip/idempotency test, a
  failure path test, and a dry-run test.
- `register()` has additional tests covering credential-based, activation-key-based, and
  `--force` re-registration paths.
- `register()` has an additional test that verifies `--password` and `--activationkey`
  values are masked in the `SubscriptionActionResult.command` field and that the original
  credential string is also masked when it appears in captured `stdout`/`stderr`.
- `status()` has tests mapping each overall status string (`Current`, `Insufficient`,
  `Invalid`, `Disabled`, and an unknown value) to the expected enum.
- No test requires root access or a live `subscription-manager` installation.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in Python.
Each correctness property defined above will be implemented as a property-based test
that:

- Generates random but valid inputs (usernames, passwords, activation keys, pool ids,
  repo ids, release strings, configurations)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific correctness
property they validate, using the format:
`# Feature: subscription-management, Property N: <property description>`

**Property Test Coverage:**

- Property 1-2: Idempotency tests for `register()`/`unregister()` given random prior
  identity state
- Property 3: Dry-run safety tests generate random mutating operations with `dry_run=True`
- Property 4: Elevation tests verify correct elevation flag usage across mutating and
  read-only operations
- Property 5: Status-mapping tests generate random `subscription-manager status` outputs
- Property 6: Command-construction tests verify correct argument formatting across every
  operation
- Property 7: Repo enable/disable idempotency given random existing enabled/disabled sets
- Property 8: Release set/unset idempotency given random currently-pinned releases
- Property 9: Attach behavior with empty and non-empty pool lists, and auto vs non-auto
- Property 10: Remove behavior with empty and non-empty attached-subscription state
- Property 11: Query error propagation across random non-zero exit codes
- Property 12: Credential redaction across random credential strings and command layouts

Both unit tests and property-based tests are complementary: unit tests verify specific
examples and edge cases, while property tests verify universal correctness across many
inputs.

---

## Usage Examples

### Local Execution (Default)

```python
from automojo.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from automojo.sysman.linux.redhat.subscriptionmanagement import (
    SubscriptionManager,
    SubscriptionManagerConfig,
)

# Create local runner
runner = LocalCommandRunner(LocalRunnerConfig())

# Inject into subscription manager
config = SubscriptionManagerConfig(elevated=True, dry_run=False)
sub_manager = SubscriptionManager(runner, config)

# Register, auto-attach, and enable an additional repo
reg_result = sub_manager.register(username="admin", password="s3cret")
print(f"Registration: {reg_result.status} (uuid={reg_result.system_uuid})")

att_result = sub_manager.attach(auto=True)
print(f"Attach: {att_result.status}")

enable_result = sub_manager.enable_repo(repo_id="rhel-9-for-x86_64-appstream-rpms")
print(f"Enable repo: {enable_result.status}")
```

### SSH Execution with Reliability Tracking

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.subscriptionmanagement import (
    SubscriptionManager,
    SubscriptionManagerConfig,
)

# Create SSH runner for remote system
ssh_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0,
)
runner = SSHCommandRunner(ssh_config)

# Inject into subscription manager (same code as local!)
config = SubscriptionManagerConfig(elevated=True)
sub_manager = SubscriptionManager(runner, config)

# Register with an activation key (credentials are redacted from audit records)
reg_result = sub_manager.register(
    org="1234567",
    activation_key="production-rhel9-key",
)

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
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.aspects import (
    PersistenceAspect,
    RetryAspect,
    TimeoutAspect,
)
from automojo.sysman.linux.redhat.aspects import (
    PersistenceAspectConfig,
    RetryAspectConfig,
    TimeoutAspectConfig,
)
from automojo.sysman.linux.redhat.storage import SQLiteStorage
from automojo.sysman.linux.redhat.subscriptionmanagement import (
    SubscriptionManager,
    SubscriptionManagerConfig,
)

# Create SSH runner
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))

# Add persistence aspect for audit logging (credentials will be redacted by
# SubscriptionManager before the command is handed to the runner)
storage = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

# Add retry aspect for transient failures (e.g., network glitches to RHSM)
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[1, 255],
    retryable_error_patterns=["Connection reset", "Temporary failure"],
))
runner.add_aspect(retry)

# Add timeout aspect for global policy
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=300.0))
runner.add_aspect(timeout)

# Inject into subscription manager
sub_manager = SubscriptionManager(runner, SubscriptionManagerConfig(elevated=True))

# All subscription operations now have:
# - Audit logging to SQLite (with --password/--activationkey masked)
# - Automatic retry on transient failures
# - 5-minute timeout enforcement
reg_result = sub_manager.register(
    org="1234567",
    activation_key="production-rhel9-key",
)
```

### Dry-Run Mode Across Transports

```python
# Dry-run works identically for local and SSH
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin", dry_run=True))
sub_manager = SubscriptionManager(runner, SubscriptionManagerConfig(elevated=True))

# No SSH connection is made, no commands executed, credentials are redacted
# from the returned command list
reg_result = sub_manager.register(username="admin", password="s3cret")
assert reg_result.status == SubscriptionActionStatus.DRY_RUN
assert "s3cret" not in " ".join(reg_result.command)
```
