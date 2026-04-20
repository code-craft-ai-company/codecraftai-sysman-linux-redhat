# Design

## Overview

The scheduled tasks feature is implemented as a single module,
`automojo.sysman.linux.redhat.scheduledtasks`, exposing a `ScheduledTaskManager` class.
The manager covers two scheduling backends — **cron** (drop-in files in `/etc/cron.d/`)
and **systemd timers** (`.timer` + `.service` unit-file pairs in `/etc/systemd/system/`)
— through a unified public API.

All file writes and deletes are delegated to an `IFileOperator` implementation.  All
`systemctl` invocations are delegated to an `ICommandRunner` implementation.  Cron
expression validation is performed with a small pure-Python validator so that no external
process is spawned.  The module has no dependency on third-party libraries.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner and file operator (Local or SSH)    │
│  - Configures aspects (optional)                           │
│  - Injects into ScheduledTaskManager                       │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              ScheduledTaskManager                           │
│  automojo.sysman.linux.redhat.scheduledtasks               │
│                                                             │
│  add_cron_job()           remove_cron_job()                 │
│  list_cron_jobs()         validate_cron_expression()        │
│  create_systemd_timer()   remove_systemd_timer()            │
│  set_systemd_timer_enabled()   list_systemd_timers()        │
│  query_task()                                               │
└──────────┬───────────────────────┬──────────────────────────┘
           │ file I/O              │ commands
           ▼                       ▼
┌──────────────────┐   ┌─────────────────────────────────────────┐
│  IFileOperator   │   │   ICommandRunner                        │
│  write_text()    │   │   run(args, elevated, timeout)          │
│  delete()        │   └────────────┬────────────────────────────┘
│  read_text()     │                │
│  list_dir()      │     ┌──────────┴──────────────┐
│  exists()        │     ▼                         ▼
└──────────────────┘   ┌──────────────────────┐  ┌──────────────────────┐
                       │ LocalCommandRunner   │  │ SSHCommandRunner     │
                       │ - subprocess.run()   │  │ - SSH connection     │
                       │ - sudo for elevation │  │ - reliability metrics│
                       │ - local execution    │  │ - connection retry   │
                       └──────────────────────┘  └──────────────────────┘
                                 │                         │
                                 └──────────┬──────────────┘
                                            │ optional
                                            ▼
                       ┌─────────────────────────────────────────────────┐
                       │            Execution Aspects                    │
                       │  - PersistenceAspect: audit logging             │
                       │  - RetryAspect: transient failure retry         │
                       │  - TimeoutAspect: global timeout policy         │
                       └─────────────────────────────────────────────────┘
```

---

## Components

### `ScheduledTaskManager`

**Location:** `source/packages/automojo/sysman/linux/redhat/scheduledtasks.py`

| Method | Signature | Description |
|---|---|---|
| `add_cron_job` | `(entry: CronJobEntry) → ScheduledTaskResult` | Write a cron drop-in file |
| `remove_cron_job` | `(name: str) → ScheduledTaskResult` | Delete a cron drop-in file |
| `list_cron_jobs` | `() → list[ScheduledTaskInfo]` | Enumerate files in `cron_dir` |
| `validate_cron_expression` | `(expression: str) → bool` | Pure-Python syntax check |
| `create_systemd_timer` | `(entry: SystemdTimerEntry) → ScheduledTaskResult` | Write unit files and enable timer |
| `remove_systemd_timer` | `(name: str) → ScheduledTaskResult` | Disable and delete unit files |
| `set_systemd_timer_enabled` | `(name: str, enabled: bool) → ScheduledTaskResult` | Enable or disable timer |
| `list_systemd_timers` | `() → list[ScheduledTaskInfo]` | Parse `systemctl list-timers` output |
| `query_task` | `(name: str, task_type: str) → ScheduledTaskInfo \| None` | Query individual task state |

### `ScheduledTaskConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no files are written/deleted and no commands run |
| `cron_dir` | `str` | `"/etc/cron.d"` | Directory for cron drop-in files |
| `unit_dir` | `str` | `"/etc/systemd/system"` | Directory for systemd unit files |

---

## Data Models

### `TaskType`

```python
class TaskType(Enum):
    CRON    = "cron"
    SYSTEMD = "systemd"
```

### `CronJobEntry`

```python
@dataclass
class CronJobEntry:
    name: str           # filename written to cron_dir (alphanumeric + hyphens only)
    schedule: str       # cron expression, e.g. "0 2 * * *" or "@daily"
    command: str        # shell command to execute
    user: str           # user to run the command as, e.g. "root"
    comment: str | None = None  # optional descriptive comment
```

### `SystemdTimerEntry`

```python
@dataclass
class SystemdTimerEntry:
    name: str               # base name; produces <name>.timer and <name>.service
    description: str        # [Unit] Description= for both unit files
    on_calendar: str        # OnCalendar= value, e.g. "daily" or "*-*-* 02:00:00"
    exec_start: str         # ExecStart= value for the .service unit
    enabled: bool = True    # whether to run systemctl enable --now after writing
    after: str | None = None        # [Unit] After= directive for the .service unit
    wants_by: str | None = None     # [Install] WantedBy= for the .timer (defaults to timers.target)
```

### `ScheduledTaskInfo`

```python
@dataclass
class ScheduledTaskInfo:
    name: str
    task_type: TaskType
    schedule: str | None = None     # cron expression or OnCalendar value
    command: str | None = None      # exec_start or cron command
    user: str | None = None         # cron user field; None for systemd timers
    enabled: bool | None = None     # systemd: from systemctl show; cron: always True if file present
    active: bool | None = None      # systemd only: from systemctl show ActiveState
    readable: bool = True           # False when file exists but could not be read (cron)
```

### `ScheduledTaskResult`

```python
@dataclass
class ScheduledTaskResult:
    success: bool
    name: str
    dry_run: bool = False
    error: str | None = None
```

### `ScheduledTaskQueryError`

```python
class ScheduledTaskQueryError(Exception):
    """Raised when a query operation fails for a reason other than the task not existing."""
```

---

## Cron File Format

Each managed cron drop-in file written to `<cron_dir>/<name>` has the form:

```
# <comment>               ← omitted when CronJobEntry.comment is None
# Managed by automojo-sysman. Do not edit manually.
<schedule> <user> <command>
```

Example for `name="nightly-backup"`:

```
# Take nightly filesystem backup
# Managed by automojo-sysman. Do not edit manually.
0 2 * * * root /usr/local/bin/backup.sh
```

`list_cron_jobs` reads each file in `cron_dir`, skips lines beginning with `#`, and
parses the first non-comment, non-blank line as `schedule user command` (splitting on
whitespace with `maxsplit=2`).  Files that cannot be parsed yield a `ScheduledTaskInfo`
with `readable=False`.

---

## Systemd Unit File Templates

### `<name>.service`

```ini
[Unit]
Description=<entry.description>
After=<entry.after>            # omitted if entry.after is None

[Service]
Type=oneshot
ExecStart=<entry.exec_start>
```

### `<name>.timer`

```ini
[Unit]
Description=<entry.description> (timer)

[Timer]
OnCalendar=<entry.on_calendar>
Persistent=true

[Install]
WantedBy=<entry.wants_by or "timers.target">
```

---

## `create_systemd_timer()` Sequence

```
1. Render .service content from template
2. file_operator.write_text(<unit_dir>/<name>.service, content, elevated=True)
3. Render .timer content from template
4. file_operator.write_text(<unit_dir>/<name>.timer, content, elevated=True)
5. command_runner.run(["systemctl", "daemon-reload"], elevated=True)
6. If entry.enabled:
       command_runner.run(["systemctl", "enable", "--now", f"{name}.timer"], elevated=True)
7. Return ScheduledTaskResult(success=True, name=name)
On any failure at step 2–6: return ScheduledTaskResult(success=False, name=name, error=...)
```

## `remove_systemd_timer()` Sequence

```
1. Check file_operator.exists(<unit_dir>/<name>.timer) OR
         file_operator.exists(<unit_dir>/<name>.service)
   If neither: return ScheduledTaskResult(success=False, name=name, error="timer not found")
2. command_runner.run(["systemctl", "disable", "--now", f"{name}.timer"], elevated=True)
   (ignore non-zero exit — unit may already be inactive/disabled)
3. file_operator.delete(<unit_dir>/<name>.timer, elevated=True)   # if exists
4. file_operator.delete(<unit_dir>/<name>.service, elevated=True) # if exists
5. command_runner.run(["systemctl", "daemon-reload"], elevated=True)
6. Return ScheduledTaskResult(success=True, name=name)
On failure at step 3–5: return ScheduledTaskResult(success=False, name=name, error=...)
```

---

## `validate_cron_expression()` Logic

The validator does not invoke any external process.

```
SHORTHANDS = {"@reboot", "@daily", "@weekly", "@monthly", "@yearly", "@hourly"}

1. Strip leading/trailing whitespace.
2. If expression starts with "@": return expression in SHORTHANDS.
3. Split on whitespace → fields.  If len(fields) != 5: return False.
4. For each field at index i, call _validate_cron_field(field, field_ranges[i]).
5. Return True only if all five fields are valid.
```

`_validate_cron_field(field, (min_val, max_val))` accepts:
- `"*"` — always valid
- `"*/n"` — n must be a positive integer within range
- `"a-b"` — both a and b must be integers within range; a ≤ b
- `"a,b,c,..."` — each element must pass the single-value check
- A bare integer string within `[min_val, max_val]`

Field ranges: minute `(0,59)`, hour `(0,23)`, dom `(1,31)`, month `(1,12)`,
dow `(0,7)` (both 0 and 7 represent Sunday).

---

## `list_systemd_timers()` Parsing

`systemctl list-timers --all --no-legend --no-pager` output columns (space-separated):
`NEXT LEFT LAST PASSED UNIT ACTIVATES`

The `UNIT` column contains `<name>.timer`.  The parser strips `.timer` suffix to obtain
`name` and constructs a `ScheduledTaskInfo` with `task_type=SYSTEMD`, `enabled=None`
(not provided by this command), and `active=True` if the `NEXT` column is not `n/a`.

---

## `query_task()` Logic

**Cron:**
1. Check `file_operator.exists(<cron_dir>/<name>)`.  If absent: return `None`.
2. `content = file_operator.read_text(<cron_dir>/<name>, elevated=True)`.
3. Parse non-comment, non-blank line → `ScheduledTaskInfo(task_type=CRON, enabled=True, ...)`.
4. On parse failure: return `ScheduledTaskInfo(readable=False, ...)`.

**Systemd:**
1. Run `systemctl show <name>.timer --no-pager`.
2. If exit code indicates unit not found: return `None`.
3. Parse key=value lines; extract `ActiveState`, `UnitFileState`, `OnCalendar*` and
   `ExecStart` from the corresponding `.service` show output.
4. Return `ScheduledTaskInfo(task_type=SYSTEMD, enabled=..., active=..., schedule=..., command=...)`.
5. On command failure (exit non-zero, not "not found"): raise `ScheduledTaskQueryError`.

---

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Cron Job File Writing Correctness

*For any* valid cron job entry, calling `add_cron_job()` should write the correct cron format to the drop-in file via the file operator.

**Validates: Requirements 1.1**

### Property 2: Cron Job Comment Inclusion

*For any* cron job entry with a comment, the written file should include the comment as a #-prefixed line above the cron entry.

**Validates: Requirements 1.2**

### Property 3: Cron Job File Overwrite Behavior

*For any* cron job name that already exists, calling `add_cron_job()` should overwrite the existing file with the new entry.

**Validates: Requirements 1.6**

### Property 4: Cron Job Removal Correctness

*For any* existing cron job name, calling `remove_cron_job()` should delete the corresponding drop-in file and return success.

**Validates: Requirements 2.1**

### Property 5: Cron Job Removal Error Handling

*For any* non-existent cron job name, calling `remove_cron_job()` should return failure with an appropriate error message.

**Validates: Requirements 2.2**

### Property 6: Cron Job Listing Correctness

*For any* cron directory state, calling `list_cron_jobs()` should return a ScheduledTaskInfo object for each file present in the directory.

**Validates: Requirements 3.1**

### Property 7: Cron Job Unreadable File Handling

*For any* unreadable file in the cron directory, `list_cron_jobs()` should include it with readable=False and no schedule/command fields.

**Validates: Requirements 3.3**

### Property 8: Cron Expression Validation Correctness

*For any* syntactically valid five-field cron expression, `validate_cron_expression()` should return True.

**Validates: Requirements 4.1**

### Property 9: Cron Expression Invalid Field Detection

*For any* cron expression with invalid fields, `validate_cron_expression()` should return False.

**Validates: Requirements 4.2**

### Property 10: Cron Expression Special Shorthand Support

*For any* special cron expression (@reboot, @daily, @weekly, @monthly, @yearly), `validate_cron_expression()` should return True.

**Validates: Requirements 4.3**

### Property 11: Cron Expression Field Count Validation

*For any* cron expression with incorrect field count (not 5 fields and not @ shorthand), `validate_cron_expression()` should return False.

**Validates: Requirements 4.4**

### Property 12: Cron Validation Side-Effect Freedom

*For any* cron expression validation, no external commands should be executed and no files should be written.

**Validates: Requirements 4.5**

### Property 13: Systemd Timer File Creation

*For any* valid systemd timer entry, calling `create_systemd_timer()` should write both .service and .timer unit files via the file operator.

**Validates: Requirements 5.1**

### Property 14: Systemd Timer Daemon Reload and Enable

*For any* successful systemd timer creation with enabled=True, the system should run systemctl daemon-reload and systemctl enable --now commands.

**Validates: Requirements 5.2**

### Property 15: Systemd Timer Conditional Enable

*For any* systemd timer entry with enabled=False, the system should write files and run daemon-reload but not run systemctl enable.

**Validates: Requirements 5.3**

### Property 16: Systemd Timer File Overwrite

*For any* systemd timer name that already exists, calling `create_systemd_timer()` should overwrite the existing unit files.

**Validates: Requirements 5.7**

### Property 17: Systemd Timer Removal Sequence

*For any* systemd timer name, calling `remove_systemd_timer()` should execute the correct sequence: disable, delete files, daemon-reload.

**Validates: Requirements 6.1**

### Property 18: Systemd Timer Removal Error Handling

*For any* non-existent systemd timer, calling `remove_systemd_timer()` should return failure with an appropriate error message.

**Validates: Requirements 6.2**

### Property 19: Systemd Timer Enable Command

*For any* timer name with enabled=True, calling `set_systemd_timer_enabled()` should run systemctl enable --now command.

**Validates: Requirements 7.1**

### Property 20: Systemd Timer Disable Command

*For any* timer name with enabled=False, calling `set_systemd_timer_enabled()` should run systemctl disable --now command.

**Validates: Requirements 7.2**

### Property 21: Systemd Timer Listing Command Construction

*For any* call to `list_systemd_timers()`, the system should construct and execute the command systemctl list-timers --all --no-legend --no-pager.

**Validates: Requirements 8.1**

### Property 22: Systemd Timer Listing Error Handling

*For any* failed systemctl list-timers command, `list_systemd_timers()` should raise ScheduledTaskQueryError with the error text.

**Validates: Requirements 8.3**

### Property 23: Cron Task Query Correctness

*For any* cron task name, calling `query_task()` with task_type="cron" should look for the correct file path and return populated ScheduledTaskInfo when found.

**Validates: Requirements 9.1**

### Property 24: Systemd Task Query Correctness

*For any* systemd task name, calling `query_task()` with task_type="systemd" should run systemctl show command and return populated ScheduledTaskInfo.

**Validates: Requirements 9.2**

### Property 25: Task Query Not Found Handling

*For any* non-existent task (cron file absent or systemd unit not found), `query_task()` should return None.

**Validates: Requirements 9.3**

### Property 26: Dry-Run Safety

*For any* write operation (add_cron_job, remove_cron_job, create_systemd_timer, remove_systemd_timer, set_systemd_timer_enabled) with dry_run=True, no files should be written/deleted and no commands should be executed, with result status DRY_RUN.

**Validates: Requirements 1.5, 2.4, 5.6, 6.5, 7.5**

### Property 27: File Operation Elevation

*For any* file write or delete operation, the file operator should be invoked with elevated=True.

**Validates: Requirements 10.1, 10.2**

### Property 28: Command Elevation

*For any* systemctl command, the command runner should be invoked with elevated=True.

**Validates: Requirements 10.3**

### Property 29: Dry-Run Elevation Behavior

*For any* dry-run operation, elevation parameters should be set but no actual elevated operations should be performed.

**Validates: Requirements 10.4**

---

## Module Layout

```
source/packages/automojo/sysman/linux/redhat/
├── __init__.py
└── scheduledtasks.py   ← ScheduledTaskManager, models, config, exception

source/testroots/testbase/
└── automojo/tests/sysman/linux/redhat/
    └── test_scheduledtasks.py
```

---

## Testing Strategy

### Unit Testing

- `MockFileOperator` records all `write_text`, `delete`, `read_text`, `list_dir`, and
  `exists` calls with their arguments for assertion.
- `MockCommandRunner` returns configurable `(exit_code, stdout, stderr)` tuples per
  command prefix, and records all invocations.
- Dry-run tests confirm zero calls to `MockFileOperator.write_text` /
  `MockCommandRunner.run`.
- `validate_cron_expression` tests are pure unit tests requiring no mocks.
- `list_cron_jobs` tests supply canned file content via `MockFileOperator.read_text`.
- `list_systemd_timers` tests supply canned `systemctl list-timers` stdout.
- `query_task` tests cover cron (file found/absent/unreadable) and systemd (unit
  found/not-found/command error) branches.
- No test requires file system access or live command execution.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in Python. Each correctness property defined above will be implemented as a property-based test that:

- Generates random but valid inputs (cron expressions, timer names, systemd configurations)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific correctness property they validate, using the format: `# Feature: scheduled-tasks, Property N: <property description>`

**Property Test Coverage:**

- Properties 1-7: Cron job management tests generate random cron entries and verify file operations
- Properties 8-12: Cron expression validation tests generate random expressions and verify validation logic
- Properties 13-18: Systemd timer creation/removal tests generate random timer configurations
- Properties 19-22: Systemd timer enable/disable and listing tests verify systemctl operations
- Properties 23-25: Task query tests verify correct file/command operations for both cron and systemd
- Properties 26-29: Dry-run safety and elevation tests verify correct behavior across all operations

Both unit tests and property-based tests are complementary: unit tests verify specific examples and edge cases, while property tests verify universal correctness across many inputs.
