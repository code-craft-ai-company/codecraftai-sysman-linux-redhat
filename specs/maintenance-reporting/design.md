# Design

## Overview

The maintenance reporting feature is implemented as a single module,
`automojo.sysman.linux.redhat.maintenancereporting`, that exposes a `MaintenanceReport`
class. The coordinator instantiates `MaintenanceReport` at the start of a run, calls
`add_action` after each operation, calls `finalise` at the end, and then calls `to_json`,
`to_text`, or `write_report` to persist or display the results.

The module contains no I/O beyond the file-operator abstraction used by `write_report`.
Timestamps are obtained via a `_clock` callable (defaulting to `datetime.utcnow`) that
can be replaced in tests. All serialisation is implemented in pure Python using the
standard `json` module and string formatting — no third-party dependencies are required.

The feature works identically whether file operations execute locally (via `LocalFileOperator`)
or remotely over SSH (via `SSHFileOperator`). When using SSH, the coordinator can track
reliability metrics and apply execution aspects without any changes to the `MaintenanceReport`
code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md) for
details on local/SSH execution, reliability tracking, and aspect configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates file operator (Local or SSH)               │
│  - Configures aspects (optional)                           │
│  - Injects file operator into MaintenanceReport            │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│          MaintenanceReport                                  │
│  automojo.sysman.linux.redhat.maintenancereporting         │
│                                                             │
│  add_action()        finalise()                             │
│  summary_counts()    to_json()                              │
│  to_text()           write_report()                         │
│  actions_by_status() actions_by_feature()                   │
└────────────────────────┬────────────────────────────────────┘
                         │ delegates file I/O to
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                   IFileOperator                             │
│  write_text(path, content, elevated) → FileResult           │
└────────────────────────┬────────────────────────────────────┘
                         │
          ┌──────────────┴──────────────┐
          ▼                             ▼
┌──────────────────────┐      ┌──────────────────────┐
│ LocalFileOperator    │      │ SSHFileOperator      │
│ - pathlib operations │      │ - SSH file transfer  │
│ - local file I/O     │      │ - reliability metrics│
│ - sudo for elevation │      │ - connection retry   │
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

### `MaintenanceReport`

**Location:** `source/packages/automojo/sysman/linux/redhat/maintenancereporting.py`

| Method | Signature | Description |
|---|---|---|
| `add_action` | `(record: ActionRecord) → None` | Append an action to the report |
| `finalise` | `() → None` | Set `finished_at` and compute `overall_status` |
| `summary_counts` | `() → ReportSummary` | Count actions by status |
| `to_json` | `(indent: int \| None = 2) → str` | Serialise to JSON string |
| `to_text` | `() → str` | Serialise to human-readable plain text |
| `write_report` | `(path: str, format: str) → ReportWriteResult` | Write JSON or text to file |
| `actions_by_status` | `(*statuses: ActionStatus) → list[ActionRecord]` | Filter by status |
| `actions_by_feature` | `(feature: str) → list[ActionRecord]` | Filter by feature name |

### `MaintenanceReportConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, `write_report` does not write any file |

---

## Data Models

### `ActionStatus`

```python
class ActionStatus(Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"
    DRY_RUN  = "dry_run"
```

### `ActionRecord`

```python
@dataclass
class ActionRecord:
    feature: str                    # e.g. "package-management"
    operation: str                  # e.g. "install_package"
    status: ActionStatus
    detail: str | None = None       # human-readable description of what happened
    error: str | None = None        # error message or captured stderr on failure
    duration_seconds: float | None = None
    timestamp: datetime | None = None   # defaults to utcnow() at construction time
```

`timestamp` is set to `datetime.utcnow()` by the `__post_init__` hook when `None` is
passed, making it effectively automatic while remaining injectable for tests.

### `ReportSummary`

```python
@dataclass
class ReportSummary:
    total: int
    succeeded: int
    failed: int
    skipped: int
    dry_run: int
```

### `ReportWriteResult`

```python
@dataclass
class ReportWriteResult:
    success: bool
    path: str
    dry_run: bool = False
    error: str | None = None
```

### `MaintenanceReport` Fields

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | Unique identifier for the run |
| `host` | `str` | Hostname or system identifier |
| `description` | `str \| None` | Human-readable description of the run's purpose |
| `started_at` | `datetime` | UTC time the report was created |
| `finished_at` | `datetime \| None` | UTC time `finalise` was last called |
| `overall_status` | `ActionStatus \| None` | Set by `finalise`; `None` until finalised |
| `actions` | `list[ActionRecord]` | Ordered list of recorded actions |

---

## `finalise()` Logic

```
if any action.status == FAILED:
    overall_status = FAILED
else:
    overall_status = SUCCESS
finished_at = utcnow()
```

This is recalculated each time `finalise()` is called, so late-added actions are always
reflected correctly.

---

## `to_json()` Serialisation

The report is converted to a plain `dict` by a private `_to_dict()` method, then passed
to `json.dumps`.

```python
def _to_dict(self) -> dict:
    return {
        "run_id": self.run_id,
        "host": self.host,
        "description": self.description,
        "started_at": self.started_at.isoformat() + "Z",
        "finished_at": self.finished_at.isoformat() + "Z" if self.finished_at else None,
        "overall_status": self.overall_status.value if self.overall_status else None,
        "summary": dataclasses.asdict(self.summary_counts()),
        "actions": [_action_to_dict(a) for a in self.actions],
    }
```

`_action_to_dict` maps an `ActionRecord` to a plain dict, converting `timestamp` to ISO
8601 and `status` to its string value.

---

## `to_text()` Format

```
═══════════════════════════════════════════════════
MAINTENANCE REPORT
═══════════════════════════════════════════════════
Run ID    : <run_id>
Host      : <host>
Desc      : <description or "(none)">
Started   : <started_at ISO 8601>
Finished  : <finished_at ISO 8601 or "in progress">
Status    : <overall_status or "in progress">

Summary   : <total> total  <succeeded> ok  <failed> failed  <skipped> skipped  <dry_run> dry-run
───────────────────────────────────────────────────
Actions
───────────────────────────────────────────────────
[SUCCESS ] package-management / install_package    installed httpd 2.4.51        0.82s
[FAILED  ] service-management / enable_service     enable httpd failed
             Error: Unit httpd.service not found
[SKIPPED ] firewall-management / add_service       http already in public zone
───────────────────────────────────────────────────
(no actions recorded)     ← only when actions list is empty
```

Each action line: `[<STATUS padded to 7>] <feature> / <operation>  <detail>  <duration>`.
Error text is indented four spaces on the next line with the prefix `Error: `.

---

## `write_report()` Logic

1. If `config.dry_run=True`: return `ReportWriteResult(success=True, path=path, dry_run=True)`.
2. Serialise: call `to_json()` when `format="json"`, `to_text()` when `format="text"`.
3. Call `file_operator.write_text(path, content)`.
4. On success: return `ReportWriteResult(success=True, path=path)`.
5. On exception: return `ReportWriteResult(success=False, path=path, error=str(e))`.

---

## Clock Injection

`MaintenanceReport.__init__` accepts an optional `_clock` parameter (a callable returning
`datetime`).  It defaults to `datetime.utcnow`.  `ActionRecord.__post_init__` accepts an
optional `_clock` parameter with the same default.  This allows tests to supply a fixed
time without monkeypatching.

---

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Report Creation Timestamp

*For any* maintenance report creation, the `started_at` field should be set to the current UTC timestamp at creation time.

**Validates: Requirements 1.1**

### Property 2: Run ID Storage

*For any* run_id provided during report creation, the report should store the exact run_id value.

**Validates: Requirements 1.2**

### Property 3: Host Storage

*For any* host string provided during report creation, the report should store the exact host value.

**Validates: Requirements 1.3**

### Property 4: Description Storage

*For any* description string provided during report creation, the report should store the exact description value.

**Validates: Requirements 1.4**

### Property 5: Initial Report State

*For any* newly created maintenance report, the actions list should be empty and finished_at should be None.

**Validates: Requirements 1.5**

### Property 6: Action Ordering Preservation

*For any* sequence of action records added to a report, calling `add_action()` should preserve the insertion order in the actions list.

**Validates: Requirements 2.1**

### Property 7: Action Record Structure

*For any* action record created, it should contain all required fields: feature, operation, status, and optional fields: detail, error, duration_seconds, timestamp.

**Validates: Requirements 2.2**

### Property 8: Timestamp Defaulting

*For any* action record created without an explicit timestamp, the system should default it to the current UTC time.

**Validates: Requirements 2.3**

### Property 9: Finalization Timestamp

*For any* report finalization, calling `finalise()` should set finished_at to the current UTC timestamp.

**Validates: Requirements 3.1**

### Property 10: Success Status Calculation

*For any* report containing only actions with status success, skipped, or dry_run, finalizing should set overall_status to SUCCESS.

**Validates: Requirements 3.2**

### Property 11: Failure Status Calculation

*For any* report containing at least one action with status failed, finalizing should set overall_status to FAILED.

**Validates: Requirements 3.3**

### Property 12: Empty Report Status

*For any* report with no actions, finalizing should set overall_status to SUCCESS.

**Validates: Requirements 3.4**

### Property 13: Re-finalization Behavior

*For any* report that is finalized multiple times, each call to `finalise()` should update finished_at and recalculate overall_status based on the current action list.

**Validates: Requirements 3.5**

### Property 14: Summary Counts Accuracy

*For any* report with actions, calling `summary_counts()` should return accurate counts for total, succeeded, failed, skipped, and dry_run actions.

**Validates: Requirements 4.1**

### Property 15: Empty Summary Counts

*For any* report with no actions, calling `summary_counts()` should return zero for all count fields.

**Validates: Requirements 4.2**

### Property 16: JSON Serialization Validity

*For any* report, calling `to_json()` should return a valid JSON string containing all report fields.

**Validates: Requirements 5.1**

### Property 17: JSON Indentation

*For any* indent value specified in `to_json()`, the JSON serializer should use that indentation for formatting.

**Validates: Requirements 5.2**

### Property 18: DateTime ISO 8601 Serialization

*For any* report with started_at or finished_at timestamps, JSON serialization should format them as ISO 8601 strings.

**Validates: Requirements 5.3**

### Property 19: Null Finished At Serialization

*For any* unfinalized report, JSON serialization should represent finished_at as null.

**Validates: Requirements 5.4**

### Property 20: Null Duration Serialization

*For any* action record with None duration_seconds, JSON serialization should represent it as null.

**Validates: Requirements 5.5**

### Property 21: Text Format Completeness

*For any* report, calling `to_text()` should return a formatted string containing run ID, host, description, start time, finish time (or "in progress"), overall status, summary counts, and action records.

**Validates: Requirements 6.1**

### Property 22: Error Text Formatting

*For any* action record with an error field, the text output should include the error text indented below the action line.

**Validates: Requirements 6.2**

### Property 23: Empty Report Text Indication

*For any* report with no actions, the text output should include a line indicating no actions were recorded.

**Validates: Requirements 6.3**

### Property 24: File Writing Format Handling

*For any* file path and format (json or text), calling `write_report()` should serialize the report in the requested format and write it via the file operator.

**Validates: Requirements 7.1**

### Property 25: JSON Format Writing

*For any* write operation with format="json", the system should write the JSON serialization to the file.

**Validates: Requirements 7.2**

### Property 26: Text Format Writing

*For any* write operation with format="text", the system should write the plain-text summary to the file.

**Validates: Requirements 7.3**

### Property 27: Successful Write Result

*For any* successful file write operation, `write_report()` should return a ReportWriteResult with success=True and the file path.

**Validates: Requirements 7.4**

### Property 28: Failed Write Result

*For any* failed file write operation, `write_report()` should return a ReportWriteResult with success=False and the error message.

**Validates: Requirements 7.5**

### Property 29: Dry-Run Write Behavior

*For any* write operation with dry_run=True, the system should not write any file and return a ReportWriteResult with success=True, dry_run=True, and the target path.

**Validates: Requirements 7.6**

### Property 30: Status Filtering Correctness

*For any* status values provided to `actions_by_status()`, the method should return only action records whose status matches any of the provided values, preserving insertion order.

**Validates: Requirements 8.1**

### Property 31: Empty Status Filter Results

*For any* status filter that matches no actions, `actions_by_status()` should return an empty list.

**Validates: Requirements 8.2**

### Property 32: Feature Filtering Correctness

*For any* feature name provided to `actions_by_feature()`, the method should return only action records whose feature matches the provided name, preserving insertion order.

**Validates: Requirements 9.1**

### Property 33: Empty Feature Filter Results

*For any* feature filter that matches no actions, `actions_by_feature()` should return an empty list.

**Validates: Requirements 9.2**

---

## Module Layout

```
source/packages/automojo/sysman/linux/redhat/
├── __init__.py
└── maintenancereporting.py     ← MaintenanceReport, models, config

source/testroots/testbase/
└── automojo/tests/sysman/linux/redhat/
    └── test_maintenancereporting.py
```

---

---

## Usage Examples

### Local Execution (Default)

```python
from automojo.sysman.linux.redhat.fileoperators import LocalFileOperator, LocalFileOperatorConfig
from automojo.sysman.linux.redhat.maintenancereporting import MaintenanceReport, MaintenanceReportConfig, ActionRecord, ActionStatus

# Create local file operator
file_operator = LocalFileOperator(LocalFileOperatorConfig())

# Create maintenance report
config = MaintenanceReportConfig(dry_run=False)
report = MaintenanceReport(
    run_id="maint-2024-01-15-001",
    host="web-server-01",
    description="Monthly security updates",
    file_operator=file_operator,
    config=config
)

# Record maintenance actions
report.add_action(ActionRecord(
    feature="package-management",
    operation="update_package",
    status=ActionStatus.SUCCESS,
    detail="Updated nginx to 1.20.2",
    duration_seconds=45.2
))

report.add_action(ActionRecord(
    feature="service-management", 
    operation="restart_service",
    status=ActionStatus.SUCCESS,
    detail="Restarted nginx service",
    duration_seconds=2.1
))

report.add_action(ActionRecord(
    feature="firewall-management",
    operation="add_rule",
    status=ActionStatus.FAILED,
    detail="Failed to add custom rule",
    error="Permission denied: firewall-cmd requires root",
    duration_seconds=0.5
))

# Finalize the report
report.finalise()

# Generate summary
summary = report.summary_counts()
print(f"Total actions: {summary.total}, Failed: {summary.failed}")

# Export as JSON
json_report = report.to_json(indent=2)
print("JSON Report:", json_report)

# Export as text
text_report = report.to_text()
print("Text Report:", text_report)

# Write to file
result = report.write_report("/var/log/maintenance/report-2024-01-15.json", "json")
if result.success:
    print(f"Report written to {result.path}")
```

### SSH Execution with Reliability Tracking

```python
from automojo.sysman.linux.redhat.fileoperators import SSHFileOperator, SSHFileOperatorConfig
from automojo.sysman.linux.redhat.maintenancereporting import MaintenanceReport, MaintenanceReportConfig, ActionRecord, ActionStatus

# Create SSH file operator for remote system
ssh_config = SSHFileOperatorConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0
)
file_operator = SSHFileOperator(ssh_config)

# Create maintenance report (same code as local!)
config = MaintenanceReportConfig()
report = MaintenanceReport(
    run_id="remote-maint-001",
    host="remote-server-01", 
    description="Remote system maintenance",
    file_operator=file_operator,
    config=config
)

# Record actions from remote maintenance
report.add_action(ActionRecord(
    feature="log-management",
    operation="vacuum_journal",
    status=ActionStatus.SUCCESS,
    detail="Vacuumed journal to 500MB",
    duration_seconds=12.3
))

# Finalize and write report to remote system
report.finalise()
result = report.write_report("/var/log/maintenance/remote-report.json", "json")

# Check SSH reliability metrics
metrics = file_operator.get_metrics()
print(f"File operation success rate: {metrics.success_count / (metrics.success_count + metrics.failure_count):.2%}")
print(f"Average transfer time: {metrics.avg_duration_ms:.2f}ms")
```

### SSH Execution with Aspects

```python
from automojo.sysman.linux.redhat.fileoperators import SSHFileOperator, SSHFileOperatorConfig
from automojo.sysman.linux.redhat.aspects import PersistenceAspect, RetryAspect, TimeoutAspect
from automojo.sysman.linux.redhat.aspects import PersistenceAspectConfig, RetryAspectConfig, TimeoutAspectConfig
from automojo.sysman.linux.redhat.storage import SQLiteStorage
from automojo.sysman.linux.redhat.maintenancereporting import MaintenanceReport, MaintenanceReportConfig

# Create SSH file operator
file_operator = SSHFileOperator(SSHFileOperatorConfig(host="remote-server", username="admin"))

# Add persistence aspect for audit logging
storage = SQLiteStorage("/var/log/maintenance/file_operations.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
file_operator.add_aspect(persistence)

# Add retry aspect for transient failures
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_error_patterns=["Connection reset", "No space left on device"]
))
file_operator.add_aspect(retry)

# Add timeout aspect for global policy
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=30.0))
file_operator.add_aspect(timeout)

# Create maintenance report
report = MaintenanceReport(
    run_id="comprehensive-maint-001",
    host="production-server",
    description="Comprehensive system maintenance",
    file_operator=file_operator,
    config=MaintenanceReportConfig()
)

# All file operations now have:
# - Audit logging to SQLite
# - Automatic retry on connection issues
# - 30-second timeout enforcement

# Record comprehensive maintenance workflow
actions = [
    ("package-management", "update_all", ActionStatus.SUCCESS, "Updated 47 packages"),
    ("service-management", "restart_services", ActionStatus.SUCCESS, "Restarted critical services"),
    ("log-management", "rotate_logs", ActionStatus.SUCCESS, "Rotated application logs"),
    ("firewall-management", "update_rules", ActionStatus.SKIPPED, "No rule changes needed"),
]

for feature, operation, status, detail in actions:
    report.add_action(ActionRecord(
        feature=feature,
        operation=operation,
        status=status,
        detail=detail
    ))

# Finalize and write comprehensive report
report.finalise()
report.write_report("/var/log/maintenance/comprehensive-report.json", "json")
report.write_report("/var/log/maintenance/comprehensive-report.txt", "text")
```

### Dry-Run Mode Across Transports

```python
# Dry-run works identically for local and SSH
file_operator = SSHFileOperator(SSHFileOperatorConfig(host="remote-server", username="admin"))
report = MaintenanceReport(
    run_id="dry-run-test",
    host="test-server",
    description="Dry run test",
    file_operator=file_operator,
    config=MaintenanceReportConfig(dry_run=True)
)

# Add some test actions
report.add_action(ActionRecord(
    feature="test-feature",
    operation="test_operation", 
    status=ActionStatus.SUCCESS,
    detail="Test action"
))

report.finalise()

# No SSH connection is made, no files written
result = report.write_report("/tmp/test-report.json", "json")
assert result.success and result.dry_run
print("Would have written report to:", result.path)
```

---

## Testing Strategy

### Unit Testing

- `MockFileOperator` records `write_text` calls and their content for assertion.
- A fixed `_clock` lambda is passed to `MaintenanceReport` and `ActionRecord` in all
  tests requiring deterministic timestamps.
- `to_json` output is parsed back with `json.loads` to assert field values rather than
  string-matching.
- `to_text` output is tested by asserting that key substrings appear in the output
  (run ID, host, status labels, action lines).
- `finalise` is tested before and after adding a failing action to verify `overall_status`
  recalculation.
- No test requires file system access or live command execution.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in Python. Each correctness property defined above will be implemented as a property-based test that:

- Generates random but valid inputs (run IDs, host names, action records, file paths)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific correctness property they validate, using the format: `# Feature: maintenance-reporting, Property N: <property description>`

**Property Test Coverage:**

- Properties 1-5: Report creation tests generate random report parameters and verify initialization
- Properties 6-8: Action management tests generate random action sequences and verify ordering/structure
- Properties 9-13: Finalization tests verify timestamp setting and status calculation logic
- Properties 14-15: Summary calculation tests generate reports with various action combinations
- Properties 16-20: JSON serialization tests verify format correctness and null handling
- Properties 21-23: Text formatting tests verify output completeness and error handling
- Properties 24-29: File writing tests verify format handling and dry-run behavior
- Properties 30-33: Filtering tests verify status and feature filtering correctness

Both unit tests and property-based tests are complementary: unit tests verify specific examples and edge cases, while property tests verify universal correctness across many inputs.
