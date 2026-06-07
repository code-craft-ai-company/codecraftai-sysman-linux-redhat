# Design

## Overview

The log management feature is implemented as a single module,
`ccai.sysman.linux.redhat.logmanagement`, that exposes a `LogManager` class. The
coordinator instantiates `LogManager` and calls its methods to query the systemd journal,
read and search traditional log files, vacuum journal data, trigger logrotate runs, and
manage logrotate drop-in configuration files.

All command execution goes through `ICommandRunner` implementing the command runner
interface. All file reads, writes, existence checks, and deletes go through
`IFileOperator`. No `subprocess`, `open`, or direct `pathlib` calls are made inside the
module.

The feature works identically whether commands execute locally (via `LocalCommandRunner`)
or remotely over SSH (via `SSHCommandRunner`). When using SSH, the coordinator can track
reliability metrics and apply execution aspects without any changes to the `LogManager`
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
│  - Injects runner into LogManager                          │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              LogManager                                     │
│  ccai.sysman.linux.redhat.logmanagement                │
│                                                             │
│  query_journal()   journal_disk_usage()                     │
│  vacuum_by_size()  vacuum_by_time()                         │
│  read_log_file()   search_log_file()                        │
│  rotate_log()                                               │
│  write_logrotate_dropin()                                   │
│  remove_logrotate_dropin()                                  │
│  query_logrotate_dropin()                                   │
└──────────┬────────────────┬─────────────────────────────────┘
           │                │
           ▼                ▼
┌──────────────────┐  ┌─────────────────────────────────────────┐
│  ICommandRunner  │  │   IFileOperator                         │
│  run(args, ...)  │  │  read_text / write                      │
│  → RunResult     │  │  exists / delete                        │
└────────┬─────────┘  └─────────────────────────────────────────┘
         │
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

### `LogManager`

**Location:** `source/packages/ccai/sysman/linux/redhat/logmanagement.py`

| Method | Signature | Description |
|---|---|---|
| `query_journal` | `(params: JournalQueryParams) → JournalQueryResult` | Run `journalctl` with filters |
| `journal_disk_usage` | `() → JournalDiskUsage` | Run `journalctl --disk-usage` |
| `vacuum_by_size` | `(size: str) → LogActionResult` | Run `journalctl --vacuum-size=<size>` |
| `vacuum_by_time` | `(period: str) → LogActionResult` | Run `journalctl --vacuum-time=<period>` |
| `read_log_file` | `(path: str, lines: int) → LogFileResult` | Run `tail --lines=<n> <path>` |
| `search_log_file` | `(path: str, pattern: str, *, context_lines, max_matches) → LogFileResult` | Run `grep -E` |
| `rotate_log` | `(config_path: str) → LogActionResult` | Run `logrotate --force` or `--debug` |
| `write_logrotate_dropin` | `(name: str, config: LogrotateConfig) → LogActionResult` | Write `/etc/logrotate.d/<name>` |
| `remove_logrotate_dropin` | `(name: str) → LogActionResult` | Delete `/etc/logrotate.d/<name>` |
| `query_logrotate_dropin` | `(name: str) → LogrotateDropInState` | Check existence and content |

### `LogManagerConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no commands with side effects or file writes are executed |
| `elevated` | `bool` | `False` | When `True`, write/vacuum commands pass `elevated=True` |

---

## Data Models

### Journal Types

```python
@dataclass
class JournalQueryParams:
    unit: str | None = None
    since: str | None = None
    until: str | None = None
    priority: str | None = None     # emerg/alert/crit/err/warning/notice/info/debug
    lines: int | None = None
    grep: str | None = None
    output_format: str | None = None  # short/json/cat/verbose

@dataclass
class JournalQueryResult:
    success: bool
    lines: list[str]
    stderr: str | None
    command: list[str]

@dataclass
class JournalDiskUsage:
    size_human: str     # e.g. "1.2G"
    size_bytes: int
```

### Action Result Types

```python
class LogActionStatus(Enum):
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"

@dataclass
class LogActionResult:
    status: LogActionStatus
    detail: str | None      # human-readable: path, size, period, or debug output
    exit_code: int | None
    stdout: str | None
    stderr: str | None
    command: list[str] | None
```

### Log File Types

```python
@dataclass
class LogFileResult:
    success: bool
    lines: list[str]
    stderr: str | None
    command: list[str]
```

### logrotate Types

```python
@dataclass
class LogrotateConfig:
    log_path: str               # glob path to rotate, e.g. "/var/log/myapp/*.log"
    rotate: int = 7             # number of rotated files to keep
    frequency: str = "daily"    # daily / weekly / monthly / yearly
    compress: bool = True
    missingok: bool = True
    notifempty: bool = True
    copytruncate: bool = False
    postrotate: str | None = None  # shell command for postrotate stanza

@dataclass
class LogrotateDropInState:
    name: str
    exists: bool
    content: str | None
```

### Exception

```python
class LogManagementError(Exception):
    """Raised when a non-optional query operation fails."""
    def __init__(self, message: str, exit_code: int | None = None, stderr: str | None = None): ...
```

---

## Command Mappings

### Read Commands (no elevation)

| Method | Command |
|---|---|
| `query_journal` | `journalctl [--unit=] [--since=] [--until=] [--priority=] [--lines=] [--grep=] [--output=] --no-pager` |
| `journal_disk_usage` | `journalctl --disk-usage --no-pager` |
| `read_log_file` | `tail --lines=<n> <path>` |
| `search_log_file` | `grep --extended-regexp [--context=] [--max-count=] <pattern> <path>` |

### Write Commands (elevation-aware)

| Method | Command |
|---|---|
| `vacuum_by_size` | `journalctl --vacuum-size=<size>` |
| `vacuum_by_time` | `journalctl --vacuum-time=<period>` |
| `rotate_log` (normal) | `logrotate --force <config_path>` |
| `rotate_log` (dry-run) | `logrotate --debug <config_path>` |

---

## `journalctl --disk-usage` Output Parsing

`journalctl --disk-usage` produces a line like:

```
Archived and active journals take up 1.2G in the file system.
```

The parser extracts the human-readable size (`1.2G`) using a regex, then converts it to
bytes:

| Suffix | Multiplier |
|---|---|
| `B` | 1 |
| `K` | 1 024 |
| `M` | 1 048 576 |
| `G` | 1 073 741 824 |
| `T` | 1 099 511 627 776 |

If the output does not match the expected pattern the parser raises `LogManagementError`.

---

## `LogrotateConfig` Rendering

`LogrotateConfig` is rendered to a configuration block string by `_render_logrotate_config()`:

```
<log_path> {
    <frequency>
    rotate <rotate>
    compress
    missingok
    notifempty
    [copytruncate]
    [postrotate
        <postrotate>
    endscript]
}
```

Only fields set to non-default/truthy values are emitted (e.g., `copytruncate` is only
written when `copytruncate=True`).

---

## Idempotency for `write_logrotate_dropin`

Before writing, `query_logrotate_dropin()` is called to read the current content.  If
the rendered config string exactly matches the existing file content the method returns
`SKIPPED`.

---

## `rotate_log` Dry-Run Behaviour

`rotate_log` is special: in dry-run mode it still executes a command (`logrotate
--debug`) so that the coordinator receives useful simulation output.  It returns status
`DRY_RUN` to make clear that no rotation occurred.  All other write methods return
`DRY_RUN` without executing anything.

---

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Journal Query Command Construction

*For any* journal query parameters, calling `query_journal()` should construct the appropriate `journalctl` command with all specified flags and pass it to the command runner.

**Validates: Requirements 1.1**

### Property 2: Journal Unit Parameter Handling

*For any* unit name, specifying `unit` in query parameters should add `--unit=<unit>` to the journalctl command.

**Validates: Requirements 1.2**

### Property 3: Journal Time Parameter Handling

*For any* time specification, specifying `since` or `until` in query parameters should add the corresponding `--since=<time>` or `--until=<time>` flags to the journalctl command.

**Validates: Requirements 1.3, 1.4**

### Property 4: Journal Priority Parameter Handling

*For any* valid priority level (emerg, alert, crit, err, warning, notice, info, debug), specifying `priority` in query parameters should add `--priority=<priority>` to the journalctl command.

**Validates: Requirements 1.5**

### Property 5: Journal Lines Parameter Handling

*For any* positive integer, specifying `lines` in query parameters should add `--lines=<n>` to the journalctl command.

**Validates: Requirements 1.6**

### Property 6: Journal Grep Parameter Handling

*For any* search pattern, specifying `grep` in query parameters should add `--grep=<pattern>` to the journalctl command.

**Validates: Requirements 1.7**

### Property 7: Journal Output Format Parameter Handling

*For any* valid output format (short, json, cat, verbose), specifying `output_format` in query parameters should add `--output=<format>` to the journalctl command.

**Validates: Requirements 1.8**

### Property 8: Journal Query Success Result Handling

*For any* successful journalctl execution (exit code 0), `query_journal()` should return a `JournalQueryResult` with `success=True` and stdout split into lines.

**Validates: Requirements 1.9**

### Property 9: Journal Query Error Result Handling

*For any* failed journalctl execution (non-zero exit code), `query_journal()` should return a `JournalQueryResult` with `success=False` and captured stderr.

**Validates: Requirements 1.10**

### Property 10: Journal Disk Usage Command Construction

*For any* call to `journal_disk_usage()`, the system should construct the command `journalctl --disk-usage` and pass it to the command runner.

**Validates: Requirements 2.1**

### Property 11: Journal Disk Usage Result Parsing

*For any* successful journalctl --disk-usage output, `journal_disk_usage()` should return a `JournalDiskUsage` with correctly parsed human-readable size and byte count.

**Validates: Requirements 2.2**

### Property 12: Journal Disk Usage Error Handling

*For any* failed journalctl --disk-usage execution, `journal_disk_usage()` should raise a `LogManagementError` with exit code and stderr.

**Validates: Requirements 2.3**

### Property 13: Journal Vacuum Size Command Construction

*For any* size string, calling `vacuum_by_size()` should construct the command `journalctl --vacuum-size=<size>` and pass it to the command runner.

**Validates: Requirements 3.1**

### Property 14: Journal Vacuum Time Command Construction

*For any* time period string, calling `vacuum_by_time()` should construct the command `journalctl --vacuum-time=<period>` and pass it to the command runner.

**Validates: Requirements 4.1**

### Property 15: Log File Read Command Construction

*For any* file path and line count, calling `read_log_file()` should construct the command `tail --lines=<n> <path>` and pass it to the command runner.

**Validates: Requirements 5.1**

### Property 16: Log File Non-Existence Handling

*For any* non-existent file path, calling `read_log_file()` should return a `LogFileResult` with `success=False` and a descriptive error message.

**Validates: Requirements 5.2**

### Property 17: Log File Read Success Handling

*For any* successful tail command execution, `read_log_file()` should return a `LogFileResult` with `success=True` and captured lines.

**Validates: Requirements 5.3**

### Property 18: Log File Read Error Handling

*For any* failed tail command execution, `read_log_file()` should return a `LogFileResult` with `success=False` and captured stderr.

**Validates: Requirements 5.4**

### Property 19: Log File Search Command Construction

*For any* file path and search pattern, calling `search_log_file()` should construct the command `grep --extended-regexp <pattern> <path>` and pass it to the command runner.

**Validates: Requirements 6.1**

### Property 20: Log File Search Context Parameter Handling

*For any* context line count, specifying `context_lines` should add `--context=<n>` to the grep command.

**Validates: Requirements 6.2**

### Property 21: Log File Search Max Matches Parameter Handling

*For any* maximum match count, specifying `max_matches` should add `--max-count=<n>` to the grep command.

**Validates: Requirements 6.3**

### Property 22: Log File Search Success Handling

*For any* successful grep execution (exit code 0), `search_log_file()` should return a `LogFileResult` with `success=True` and captured lines.

**Validates: Requirements 6.4**

### Property 23: Log File Search No Matches Handling

*For any* grep execution with no matches (exit code 1), `search_log_file()` should return a `LogFileResult` with `success=True` and empty lines list.

**Validates: Requirements 6.5**

### Property 24: Log File Search Error Handling

*For any* grep execution with error (exit code 2), `search_log_file()` should return a `LogFileResult` with `success=False` and captured stderr.

**Validates: Requirements 6.6**

### Property 25: Log Rotation Command Construction

*For any* logrotate configuration file path, calling `rotate_log()` should construct the command `logrotate --force <config_path>` and pass it to the command runner.

**Validates: Requirements 7.1**

### Property 26: Log Rotation Dry-Run Behavior

*For any* logrotate configuration file path with `dry_run=True`, calling `rotate_log()` should construct the command `logrotate --debug <config_path>` and return status `DRY_RUN`.

**Validates: Requirements 7.4**

### Property 27: Logrotate Drop-In Writing Correctness

*For any* drop-in name and logrotate configuration, calling `write_logrotate_dropin()` should write the rendered configuration to `/etc/logrotate.d/<name>` via the file operator.

**Validates: Requirements 8.1**

### Property 28: Logrotate Drop-In Writing Idempotency

*For any* drop-in that already exists with identical content, calling `write_logrotate_dropin()` should return status `SKIPPED` without writing.

**Validates: Requirements 8.2**

### Property 29: Logrotate Drop-In Writing Update Behavior

*For any* drop-in that doesn't exist or has different content, calling `write_logrotate_dropin()` should write the file and return status `SUCCESS`.

**Validates: Requirements 8.3**

### Property 30: Logrotate Drop-In Writing Elevation

*For any* drop-in write operation with `elevated=True` in config, the file operator should be invoked with `elevated=True`.

**Validates: Requirements 8.5**

### Property 31: Logrotate Drop-In Deletion Correctness

*For any* drop-in name, calling `remove_logrotate_dropin()` should delete `/etc/logrotate.d/<name>` via the file operator.

**Validates: Requirements 9.1**

### Property 32: Logrotate Drop-In Deletion Idempotency

*For any* non-existent drop-in, calling `remove_logrotate_dropin()` should return status `SKIPPED` without attempting deletion.

**Validates: Requirements 9.2**

### Property 33: Logrotate Drop-In Query Correctness

*For any* drop-in name, calling `query_logrotate_dropin()` should check existence of `/etc/logrotate.d/<name>` via the file operator.

**Validates: Requirements 10.1**

### Property 34: Logrotate Drop-In Query Existence Handling

*For any* existing drop-in file, `query_logrotate_dropin()` should return `LogrotateDropInState` with `exists=True` and file content.

**Validates: Requirements 10.2**

### Property 35: Logrotate Drop-In Query Non-Existence Handling

*For any* non-existent drop-in file, `query_logrotate_dropin()` should return `LogrotateDropInState` with `exists=False` and `content=None`.

**Validates: Requirements 10.3**

### Property 36: Dry-Run Safety

*For any* write operation (vacuum_by_size, vacuum_by_time, rotate_log, write_logrotate_dropin, remove_logrotate_dropin) with `dry_run=True`, the command runner should never be invoked for write commands, and the result status should be `DRY_RUN`.

**Validates: Requirements 3.4, 4.4, 7.4, 8.4, 9.4**

### Property 37: Elevation for Write Operations

*For any* write operation with `elevated=True` in the config, the command runner should be invoked with `elevated=True` for journalctl vacuum and logrotate commands.

**Validates: Requirements 11.1**

### Property 38: No Elevation for Read Operations

*For any* read operation (query_journal, journal_disk_usage, read_log_file, search_log_file, query_logrotate_dropin), the command runner should never be invoked with `elevated=True` regardless of the elevation configuration.

**Validates: Requirements 11.2**

---

## Module Layout

```
source/packages/ccai/sysman/linux/redhat/
├── __init__.py
└── logmanagement.py        ← LogManager, config, models, exceptions

source/testroots/testbase/
└── ccai/tests/sysman/linux/redhat/
    └── test_logmanagement.py
```

---

---

## Usage Examples

### Local Execution (Default)

```python
from ccai.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from ccai.sysman.linux.redhat.logmanagement import LogManager, LogManagerConfig, JournalQueryParams, LogrotateConfig

# Create local runner
runner = LocalCommandRunner(LocalRunnerConfig())

# Inject into log manager
config = LogManagerConfig(elevated=True, dry_run=False)
log_mgr = LogManager(runner, config)

# Query systemd journal
query_params = JournalQueryParams(
    unit="nginx.service",
    since="2024-01-01",
    priority="err",
    lines=100
)
result = log_mgr.query_journal(query_params)
print(f"Found {len(result.lines)} error entries for nginx")

# Check journal disk usage
usage = log_mgr.journal_disk_usage()
print(f"Journal using {usage.size_human} ({usage.size_bytes} bytes)")

# Vacuum old journal entries
if usage.size_bytes > 1_000_000_000:  # > 1GB
    vacuum_result = log_mgr.vacuum_by_size("500M")
    print(f"Journal vacuum: {vacuum_result.status}")

# Read recent log file entries
log_result = log_mgr.read_log_file("/var/log/messages", lines=50)
if log_result.success:
    print(f"Read {len(log_result.lines)} lines from messages")

# Search for specific patterns
search_result = log_mgr.search_log_file(
    "/var/log/secure", 
    "Failed password",
    context_lines=2,
    max_matches=10
)
if search_result.success:
    print(f"Found {len(search_result.lines)} failed login attempts")

# Configure logrotate for custom application
logrotate_config = LogrotateConfig(
    log_paths=["/var/log/myapp/*.log"],
    rotate_count=7,
    frequency="daily",
    compress=True,
    delaycompress=True,
    missingok=True,
    notifempty=True
)
write_result = log_mgr.write_logrotate_dropin("myapp", logrotate_config)
print(f"Logrotate config: {write_result.status}")

# Trigger immediate rotation
rotate_result = log_mgr.rotate_log("/etc/logrotate.d/myapp")
print(f"Log rotation: {rotate_result.status}")
```

### SSH Execution with Reliability Tracking

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.logmanagement import LogManager, LogManagerConfig, JournalQueryParams

# Create SSH runner for remote system
ssh_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0
)
runner = SSHCommandRunner(ssh_config)

# Inject into log manager (same code as local!)
config = LogManagerConfig(elevated=True)
log_mgr = LogManager(runner, config)

# Query remote journal over SSH
query_params = JournalQueryParams(
    since="1 hour ago",
    priority="warning",
    output_format="json"
)
result = log_mgr.query_journal(query_params)
print(f"Remote journal entries: {len(result.lines)}")

# Manage remote log rotation over SSH
log_mgr.vacuum_by_time("30d")
log_mgr.rotate_log("/etc/logrotate.conf")

# Check SSH reliability metrics
metrics = runner.get_metrics()
print(f"Success rate: {metrics.success_count / (metrics.success_count + metrics.failure_count):.2%}")
print(f"Average duration: {metrics.avg_duration_ms:.2f}ms")
print(f"Connection retries: {metrics.retry_count}")
```

### SSH Execution with Aspects

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.aspects import PersistenceAspect, RetryAspect, TimeoutAspect
from ccai.sysman.linux.redhat.aspects import PersistenceAspectConfig, RetryAspectConfig, TimeoutAspectConfig
from ccai.sysman.linux.redhat.storage import SQLiteStorage
from ccai.sysman.linux.redhat.logmanagement import LogManager, LogManagerConfig, JournalQueryParams

# Create SSH runner
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))

# Add persistence aspect for audit logging
storage = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

# Add retry aspect for transient failures
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[1, 2],  # journalctl/logrotate errors
    retryable_error_patterns=["Journal not accessible", "Logrotate already running"]
))
runner.add_aspect(retry)

# Add timeout aspect for global policy
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=120.0))
runner.add_aspect(timeout)

# Inject into log manager
config = LogManagerConfig(elevated=True)
log_mgr = LogManager(runner, config)

# All log operations now have:
# - Audit logging to SQLite
# - Automatic retry on journal/logrotate busy states
# - 2-minute timeout enforcement

# Comprehensive log management workflow
# Query recent critical events
critical_query = JournalQueryParams(
    since="24 hours ago",
    priority="crit",
    output_format="short"
)
critical_events = log_mgr.query_journal(critical_query)

# Check disk usage and vacuum if needed
usage = log_mgr.journal_disk_usage()
if usage.size_bytes > 2_000_000_000:  # > 2GB
    log_mgr.vacuum_by_size("1G")

# Search for security events
security_events = log_mgr.search_log_file(
    "/var/log/secure",
    "(Failed|Invalid) (password|user)",
    context_lines=1,
    max_matches=50
)

# Configure application log rotation
app_config = LogrotateConfig(
    log_paths=["/var/log/app/*.log"],
    rotate_count=14,
    frequency="daily",
    compress=True
)
log_mgr.write_logrotate_dropin("application", app_config)

# Force rotation of all logs
log_mgr.rotate_log("/etc/logrotate.conf")
```

### Dry-Run Mode Across Transports

```python
# Dry-run works identically for local and SSH
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin", dry_run=True))
log_mgr = LogManager(runner, LogManagerConfig(elevated=True))

# No SSH connection is made, no commands executed
result = log_mgr.vacuum_by_size("1G")
assert result.status == LogActionStatus.DRY_RUN
print("Would have vacuumed journal:", result.message)

result = log_mgr.rotate_log("/etc/logrotate.d/myapp")
assert result.status == LogActionStatus.DRY_RUN
print("Would have rotated logs:", result.detail)
```

---

## Testing Strategy

### Unit Testing

- All tests use `MockCommandRunner` and `MockFileOperator` keyed on command args prefix
  and file path respectively.
- `_render_logrotate_config()` is tested in pure Python with representative
  `LogrotateConfig` objects verifying all optional fields (compress, copytruncate,
  postrotate).
- `journal_disk_usage` parsing is tested with representative output strings including
  all size suffixes.
- `search_log_file` exit-code mapping (0 = matches, 1 = no matches, 2 = error) is tested
  independently.
- `rotate_log` dry-run path verifies that `logrotate --debug` is called and status is
  `DRY_RUN`.
- `write_logrotate_dropin` idempotency is tested by pre-populating `MockFileOperator`
  with matching content.
- No test requires root access or a live system.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in Python. Each correctness property defined above will be implemented as a property-based test that:

- Generates random but valid inputs (journal parameters, file paths, search patterns, logrotate configs)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific correctness property they validate, using the format: `# Feature: log-management, Property N: <property description>`

**Property Test Coverage:**

- Properties 1-9: Journal query tests generate random query parameters and verify journalctl command construction
- Properties 10-12: Journal disk usage tests verify command construction and result parsing
- Properties 13-14: Journal vacuum tests generate random size/time specifications
- Properties 15-18: Log file read tests generate random file paths and line counts
- Properties 19-26: Log file search tests generate random patterns and parameters
- Properties 25-26: Log rotation tests verify command construction and dry-run behavior
- Properties 27-35: Logrotate drop-in tests verify file operations and idempotency
- Properties 36-38: Dry-run safety and elevation tests verify correct flag usage

Both unit tests and property-based tests are complementary: unit tests verify specific examples and edge cases, while property tests verify universal correctness across many inputs.
