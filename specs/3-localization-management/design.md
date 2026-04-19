# Design

## Overview

The localization management feature is implemented as a single module,
`automojo.sysman.linux.redhat.localizationmanagement`, that exposes a
`LocalizationManager` class. The coordinator instantiates `LocalizationManager`
and calls its methods to query or apply locale, timezone, real-time-clock, and
keyboard settings on Red Hat Linux systems.

All system interaction is delegated to a command runner implementing the
`ICommandRunner` interface. The `LocalizationManager` never directly calls
`subprocess` or shell commands; it only constructs command argument lists
(`timedatectl ...`, `localectl ...`) and passes them to the runner. This keeps
business logic separate from execution and makes the feature fully testable via
a mock runner without root access or a live system. File-operator usage is
optional and limited to reading advisory configuration files (for example
`/etc/locale.conf`, `/etc/vconsole.conf`) when cross-checking state.

Network time synchronization (Chrony / NTP server configuration) is **not**
handled here. Enabling, disabling, or configuring NTP servers is the
responsibility of the `network-configuration` feature. `LocalizationManager`
only reports NTP-related fields that `timedatectl show` already exposes
(`ntp`, `can_ntp`, `ntp_synchronized`) so the coordinator can observe state;
it does not mutate those fields.

The feature works identically whether commands execute locally (via
`LocalCommandRunner`) or remotely over SSH (via `SSHCommandRunner`). When using
SSH, the coordinator can track reliability metrics and apply execution aspects
(persistence, retry, timeout) without any changes to the `LocalizationManager`
code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md) for
details on local/SSH execution, reliability tracking, and aspect configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner (Local or SSH)                      │
│  - Configures aspects (optional)                           │
│  - Injects runner into LocalizationManager                 │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            LocalizationManager                              │
│  automojo.sysman.linux.redhat.localizationmanagement        │
│                                                             │
│  get_time_state()      set_timezone()                       │
│  list_timezones()      set_local_rtc()                      │
│  get_locale_state()    set_locale()                         │
│  list_locales()        set_vc_keymap()                      │
│  set_x11_keymap()      list_x11_layouts()                   │
│  list_x11_models()     list_x11_variants()                  │
│  list_x11_options()                                         │
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
│  - PersistenceAspect: audit logging                         │
│  - RetryAspect: transient failure retry                     │
│  - TimeoutAspect: global timeout policy                     │
└─────────────────────────────────────────────────────────────┘
```

---

## Components

### `LocalizationManager`

**Location:** `source/packages/automojo/sysman/linux/redhat/localizationmanagement.py`

The primary public class. Constructed with an `ICommandRunner` and a
`LocalizationManagerConfig`.

| Method | Description |
|---|---|
| `get_time_state()` → `TimeState` | Parse `timedatectl show` into a `TimeState` record |
| `set_timezone(zone)` → `LocalizationActionResult` | Set the system timezone via `timedatectl set-timezone` |
| `list_timezones()` → `list[str]` | Return the output of `timedatectl list-timezones` |
| `set_local_rtc(local, adjust_system_clock=True)` → `LocalizationActionResult` | Toggle RTC local vs UTC mode |
| `get_locale_state()` → `LocaleState` | Parse `localectl status` into a `LocaleState` record |
| `set_locale(variables)` → `LocalizationActionResult` | Apply one or more `LANG`/`LC_*` variables |
| `list_locales()` → `list[str]` | Return the output of `localectl list-locales` |
| `set_vc_keymap(keymap, convert=False)` → `LocalizationActionResult` | Set the virtual-console keymap |
| `set_x11_keymap(layout, model=None, variant=None, options=None, convert=False)` → `LocalizationActionResult` | Set the X11 keymap tuple |
| `list_x11_layouts()` → `list[str]` | Return the output of `localectl list-x11-keymap-layouts` |
| `list_x11_models()` → `list[str]` | Return the output of `localectl list-x11-keymap-models` |
| `list_x11_variants(layout)` → `list[str]` | Return the output of `localectl list-x11-keymap-variants <layout>` |
| `list_x11_options()` → `list[str]` | Return the output of `localectl list-x11-keymap-options` |

### `LocalizationManagerConfig`

**Location:** `source/packages/automojo/sysman/linux/redhat/localizationmanagement.py`

A dataclass holding runtime configuration for `LocalizationManager`.

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no commands are executed |
| `elevated` | `bool` | `False` | When `True`, mutating commands are issued with elevation |

---

## Data Models

### `LocalizationActionStatus`

```python
class LocalizationActionStatus(Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"   # already in desired state, no action needed
    DRY_RUN  = "dry_run"   # would have acted, but dry_run=True
```

### `LocalizationActionResult`

Returned by all mutating operations (`set_timezone`, `set_local_rtc`,
`set_locale`, `set_vc_keymap`, `set_x11_keymap`).

```python
@dataclass
class LocalizationActionResult:
    status: LocalizationActionStatus
    operation: str                # e.g. "set_timezone", "set_locale"
    target: str                   # e.g. zone name, locale string, keymap
    exit_code: int | None         # raw exit code from command runner
    stdout: str | None
    stderr: str | None
    command: list[str] | None     # the command that was (or would have been) run
    error_message: str | None     # populated on validation / FAILED paths
```

### `TimeState`

Returned by `get_time_state()`.

```python
@dataclass
class TimeState:
    timezone: str
    local_rtc: bool
    can_ntp: bool
    ntp: bool
    ntp_synchronized: bool
    time_usec: int
    rtc_time_usec: int
```

### `LocaleState`

Returned by `get_locale_state()`.

```python
@dataclass
class LocaleState:
    variables: dict[str, str]     # LANG, LC_CTYPE, LC_MESSAGES, ...
    vc_keymap: str | None
    x11_layout: str | None
    x11_model: str | None
    x11_variant: str | None
    x11_options: str | None
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Idempotent Set-Timezone

*For any* timezone string, calling `set_timezone()` when the current system
timezone already equals the requested value should return `SKIPPED` status
without invoking the command runner for a mutating command.

**Validates: Requirements 2.2**

### Property 2: Unknown Timezone Rejection

*For any* timezone string not present in `timedatectl list-timezones`,
`set_timezone()` should return `FAILED` with a clear `error_message` and MUST
NOT invoke the runner with `timedatectl set-timezone`.

**Validates: Requirements 2.3**

### Property 3: Idempotent Set-Locale

*For any* dict of locale variables, if every supplied variable already matches
the current `LocaleState.variables` entry, `set_locale()` should return
`SKIPPED` without invoking the runner. The comparison must be per-variable —
a partial match is not a skip.

**Validates: Requirements 6.3**

### Property 4: Unknown Locale Rejection

*For any* `LANG` value not present in `localectl list-locales`, `set_locale()`
should return `FAILED` with a clear `error_message` and MUST NOT invoke the
runner with `localectl set-locale`.

**Validates: Requirements 6.2**

### Property 5: Idempotent Set-VC-Keymap

*For any* VC keymap name, calling `set_vc_keymap()` when the current
`LocaleState.vc_keymap` already equals the requested value should return
`SKIPPED` without invoking the runner. Unknown keymaps (absent from
`localectl list-keymaps`) should return `FAILED` without invoking the runner.

**Validates: Requirements 8.3, 8.4**

### Property 6: Idempotent Set-X11-Keymap

*For any* `(layout, model, variant, options)` tuple, `set_x11_keymap()` should
return `SKIPPED` when every supplied (non-`None`) component already matches the
current `LocaleState` X11 fields. Unknown layouts (absent from
`list-x11-keymap-layouts`) should return `FAILED` without invoking the runner.

**Validates: Requirements 9.3, 9.4**

### Property 7: Idempotent Set-Local-RTC

*For any* boolean `local` value, calling `set_local_rtc()` when
`TimeState.local_rtc` already equals the requested value should return
`SKIPPED` without invoking the runner.

**Validates: Requirements 4.3**

### Property 8: Dry-Run Safety

*For any* mutating operation (`set_timezone`, `set_local_rtc`, `set_locale`,
`set_vc_keymap`, `set_x11_keymap`) with `dry_run=True`, the command runner
should never be invoked for the mutating command, and the result status
should be `DRY_RUN`.

**Validates: Requirements 2.6, 4.6, 6.5, 8.6, 9.6**

### Property 9: Elevation Consistency

*For any* mutating operation with `elevated=True` in the config, the command
runner should be invoked with `elevated=True`. For every read-only query
(`get_time_state`, `get_locale_state`, all `list_*` methods), the runner MUST
NOT be invoked with `elevated=True`.

**Validates: Requirements 11.1, 11.2, 11.3**

### Property 10: Status Mapping Correctness

*For any* mutating operation result from the command runner, the
`LocalizationActionResult.status` should correctly reflect the runner's exit
code: `0` maps to `SUCCESS`; non-zero maps to `FAILED` with `exit_code` and
`stderr` captured; validation failures map to `FAILED` with `error_message`
set and no runner invocation.

**Validates: Requirements 2.4, 2.5, 4.5, 6.4, 8.5, 9.5**

### Property 11: Query Failure Raises `LocalizationQueryError`

*For any* read-only query command (`timedatectl show`, `timedatectl
list-timezones`, `localectl status`, `localectl list-locales`, `localectl
list-keymaps`, `localectl list-x11-keymap-*`) that exits with a non-zero code,
`LocalizationManager` should raise `LocalizationQueryError` capturing the
exit code and stderr.

**Validates: Requirements 1.3, 3.3, 5.3, 7.3, 10.6**

---

## Interfaces

### `ICommandRunner` (from `automojo-interfaces`)

`LocalizationManager` depends on this interface, injected at construction time.

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

When `LocalizationManagerConfig.elevated` is `True`, the `LocalizationManager`
passes `elevated=True` to `ICommandRunner.run()` for mutating commands only.
The runner is responsible for prepending `sudo` or using whatever privilege
escalation mechanism is appropriate for the environment.
`LocalizationManager` has no knowledge of how elevation is implemented.

Read-only queries (`timedatectl show`, `timedatectl list-timezones`,
`localectl status`, `localectl list-*`) are always issued with
`elevated=False` regardless of config because systemd's query commands do not
require privilege.

---

## Command Mappings

| Operation | Command |
|---|---|
| Query time state | `timedatectl show` |
| Set timezone | `timedatectl set-timezone <zone>` |
| List timezones | `timedatectl list-timezones` |
| Set RTC to UTC | `timedatectl set-local-rtc 0` |
| Set RTC to local | `timedatectl set-local-rtc 1` |
| Set RTC without clock adjust | `timedatectl set-local-rtc <0\|1> --adjust-system-clock=false` |
| Query locale state | `localectl status` |
| Set locale vars | `localectl set-locale LANG=<v> [LC_CTYPE=<v> ...]` |
| List locales | `localectl list-locales` |
| Set VC keymap | `localectl set-keymap <keymap>` |
| Set VC keymap with convert | `localectl set-keymap <keymap> --convert` |
| Set X11 keymap | `localectl set-x11-keymap <layout> [<model> [<variant> [<options>]]]` |
| Set X11 keymap with convert | `localectl set-x11-keymap <layout> ... --convert` |
| List VC keymaps | `localectl list-keymaps` |
| List X11 layouts | `localectl list-x11-keymap-layouts` |
| List X11 models | `localectl list-x11-keymap-models` |
| List X11 variants | `localectl list-x11-keymap-variants <layout>` |
| List X11 options | `localectl list-x11-keymap-options` |

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `LocalizationQueryError` | Any read-only query command (`timedatectl show`, `timedatectl list-timezones`, `localectl status`, `localectl list-locales`, `localectl list-keymaps`, `localectl list-x11-keymap-*`) returns a non-zero exit code |

All mutating-operation failures are surfaced as a `LocalizationActionResult`
with `status=FAILED` so the coordinator can decide how to respond without
catching exceptions for normal operational failures.

### Idempotency Guards

Before executing a mutating command, `LocalizationManager` calls the relevant
`get_*_state()` query to determine current state. If the system is already in
the desired state the method returns `SKIPPED` without invoking the mutating
command. This makes all mutating operations safe to call multiple times.

### Input-Validation Rejection Paths

Before executing a mutating command, `LocalizationManager` validates user
input against the system's view of valid values:

- `set_timezone(zone)` — zone must appear in `list_timezones()`
- `set_locale(variables)` — the supplied `LANG` (if present) must appear in
  `list_locales()`
- `set_vc_keymap(keymap)` — keymap must appear in `localectl list-keymaps`
- `set_x11_keymap(layout, ...)` — layout must appear in `list_x11_layouts()`

When validation fails, the runner is NOT invoked and a
`LocalizationActionResult` with `status=FAILED` and a descriptive
`error_message` is returned.

---

## Module Layout

```
source/packages/automojo/sysman/linux/redhat/
├── __init__.py
└── localizationmanagement.py          ← LocalizationManager, config, models, exceptions

source/testroots/testbase/
└── automojo/tests/sysman/linux/redhat/
    └── test_localizationmanagement.py ← unit tests using MockCommandRunner
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` injected into `LocalizationManager` at
  construction time.
- Tests verify the *arguments* passed to the runner, not system state.
- Each public method has at minimum: a success path test, a skip/idempotency
  test (for mutating methods), a failure path test, and a dry-run test
  (for mutating methods).
- `get_time_state()` and `get_locale_state()` have parse-output tests covering
  canonical `timedatectl show` and `localectl status` output.
- Validation methods (`set_timezone`, `set_locale`, `set_vc_keymap`,
  `set_x11_keymap`) have tests verifying that invalid input returns `FAILED`
  without invoking the mutating runner call.
- No test requires root access or a live `systemd` installation.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in
Python. Each correctness property defined above will be implemented as a
property-based test that:

- Generates random but valid inputs (timezone strings, locale dicts, keymap
  tuples, boolean flags, exit codes)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific
correctness property they validate, using the format:
`# Feature: localization-management, Property N: <property description>`

**Property Test Coverage:**

- Property 1, 3, 5, 6, 7: Idempotency tests generate random target values and
  verify repeated or already-matching operations return `SKIPPED`
- Property 2, 4: Rejection tests generate names not in the list-output and
  verify `FAILED` status without runner invocation
- Property 8: Dry-run safety tests verify no mutating runner invocation across
  all five mutating operations
- Property 9: Elevation tests verify correct elevation flag usage across
  mutating and read-only operations
- Property 10: Status mapping tests verify correct result interpretation from
  random exit codes
- Property 11: Query failure tests verify `LocalizationQueryError` is raised
  for any non-zero read-only query exit code

Both unit tests and property-based tests are complementary: unit tests verify
specific examples and edge cases, while property tests verify universal
correctness across many inputs.

---

## Usage Examples

### Local Execution (Default)

```python
from automojo.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from automojo.sysman.linux.redhat.localizationmanagement import (
    LocalizationManager,
    LocalizationManagerConfig,
)

# Create local runner
runner = LocalCommandRunner(LocalRunnerConfig())

# Inject into localization manager
config = LocalizationManagerConfig(elevated=True, dry_run=False)
loc_manager = LocalizationManager(runner, config)

# Apply a declared posture: America/New_York + en_US.UTF-8
tz_result = loc_manager.set_timezone(zone="America/New_York")
print(f"Timezone: {tz_result.status}")

locale_result = loc_manager.set_locale(variables={"LANG": "en_US.UTF-8"})
print(f"Locale: {locale_result.status}")
```

### SSH Execution with Reliability Tracking

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.localizationmanagement import (
    LocalizationManager,
    LocalizationManagerConfig,
)

# Fleet timezone correction across many SSH targets
ssh_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0,
)
runner = SSHCommandRunner(ssh_config)

# Same API as local
config = LocalizationManagerConfig(elevated=True)
loc_manager = LocalizationManager(runner, config)

# Correct timezone on the remote host
result = loc_manager.set_timezone(zone="UTC")

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
from automojo.sysman.linux.redhat.aspects import PersistenceAspect, RetryAspect, TimeoutAspect
from automojo.sysman.linux.redhat.aspects import PersistenceAspectConfig, RetryAspectConfig, TimeoutAspectConfig
from automojo.sysman.linux.redhat.storage import SQLiteStorage
from automojo.sysman.linux.redhat.localizationmanagement import (
    LocalizationManager,
    LocalizationManagerConfig,
)

# Create SSH runner
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))

# Add persistence aspect for audit logging
storage = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

# Add retry aspect for transient failures (e.g., network glitches)
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[1, 255],
    retryable_error_patterns=["Connection reset", "Temporary failure"],
))
runner.add_aspect(retry)

# Add timeout aspect for global policy
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=60.0))
runner.add_aspect(timeout)

# Inject into localization manager
loc_manager = LocalizationManager(runner, LocalizationManagerConfig(elevated=True))

# All localization operations now have:
# - Audit logging to SQLite
# - Automatic retry on transient failures
# - 60-second timeout enforcement
result = loc_manager.set_locale(variables={"LANG": "en_US.UTF-8", "LC_TIME": "en_GB.UTF-8"})
```

### Dry-Run Mode Across Transports

```python
# Dry-run works identically for local and SSH
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin", dry_run=True))
loc_manager = LocalizationManager(runner, LocalizationManagerConfig(elevated=True))

# No SSH connection is made, no mutating commands executed
result = loc_manager.set_timezone(zone="America/New_York")
assert result.status == LocalizationActionStatus.DRY_RUN
```
