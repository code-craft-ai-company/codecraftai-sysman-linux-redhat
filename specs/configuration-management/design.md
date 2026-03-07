# Design

## Overview

The configuration management feature is implemented as a single module,
`automojo.sysman.linux.redhat.configurationmanagement`, that exposes a
`ConfigurationManager` class. The coordinator instantiates `ConfigurationManager` and
calls its methods to read, write, diff, backup, restore, validate, and adjust permissions
on system configuration files.

All file system mutations are delegated to a command runner implementing the `ICommandRunner`
interface using `tee`, `cp`, `chmod`, and `chown` commands. Read operations use `cat` and
`stat` through the same runner. This ensures dry-run mode, elevation, and auditing apply
uniformly to every operation without direct file system access in the module.

The feature works identically whether commands execute locally (via `LocalCommandRunner`) or
remotely over SSH (via `SSHCommandRunner`). When using SSH, the coordinator can track
reliability metrics and apply execution aspects (persistence, retry, timeout) without any
changes to the `ConfigurationManager` code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md) for
details on local/SSH execution, reliability tracking, and aspect configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner (Local or SSH)                      │
│  - Configures aspects (optional)                           │
│  - Injects runner into ConfigurationManager                │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│        ConfigurationManager                                 │
│  automojo.sysman.linux.redhat.configurationmanagement     │
│                                                             │
│  write()           write_template()                         │
│  backup()          restore()                                │
│  diff()            validate()                               │
│  set_permissions() set_ownership()                          │
│  set_ini_key()     remove_ini_key()                         │
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

### `ConfigurationManager`

**Location:** `source/packages/automojo/sysman/linux/redhat/configurationmanagement.py`

| Method | Signature | Description |
|---|---|---|
| `write` | `(path, content, mode=None, owner=None, group=None) → ConfigActionResult` | Write content to a file |
| `write_template` | `(path, template, variables, mode=None, owner=None, group=None) → ConfigActionResult` | Render template and write |
| `backup` | `(path) → ConfigActionResult` | Timestamped copy of a file |
| `restore` | `(backup_path) → ConfigActionResult` | Restore from a backup file |
| `diff` | `(path, desired_content) → ConfigDiffResult` | Unified diff of current vs desired |
| `validate` | `(path, validator_id) → ConfigValidationResult` | Run syntax validator |
| `set_permissions` | `(path, mode) → ConfigActionResult` | Apply chmod to file |
| `set_ownership` | `(path, user=None, group=None) → ConfigActionResult` | Apply chown to file |
| `set_ini_key` | `(path, section, key, value) → ConfigActionResult` | Update one INI key |
| `remove_ini_key` | `(path, section, key) → ConfigActionResult` | Remove one INI key |

### `ConfigurationManagerConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no writes, copies, or chmod/chown are executed |
| `elevated` | `bool` | `False` | When `True`, write commands run with `elevated=True` |

### `ValidatorRegistry`

A module-level dict mapping validator identifier strings to command builder callables.

```python
VALIDATORS: dict[str, Callable[[str], list[str]]] = {
    "nginx":   lambda path: ["nginx",      "-t", "-c", path],
    "sshd":    lambda path: ["sshd",       "-t", "-f", path],
    "httpd":   lambda path: ["httpd",      "-t", "-f", path],
    "named":   lambda path: ["named-checkconf",  path],
    "rsyslog": lambda path: ["rsyslogd",   "-N1", "-f", path],
    "sudoers": lambda path: ["visudo",     "-c", "-f", path],
}
```

Additional validators can be registered at runtime via `ConfigurationManager.register_validator()`.

---

## Data Models

### `ConfigActionResult`

```python
@dataclass
class ConfigActionResult:
    status: ConfigActionStatus    # SUCCESS | FAILED | SKIPPED | DRY_RUN
    path: str
    backup_path: str | None       # populated by backup() and restore()
    diff: str | None              # populated in DRY_RUN results when content changes
    exit_code: int | None
    stdout: str | None
    stderr: str | None
    command: list[str] | None
```

### `ConfigActionStatus`

```python
class ConfigActionStatus(Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"
    DRY_RUN  = "dry_run"
```

### `ConfigDiffResult`

```python
@dataclass
class ConfigDiffResult:
    path: str
    has_diff: bool
    diff: str          # unified diff string; empty string when has_diff=False
```

### `ConfigValidationResult`

```python
@dataclass
class ConfigValidationResult:
    path: str
    validator_id: str
    valid: bool
    exit_code: int
    stdout: str
    stderr: str
```

---

## Interfaces

### `ICommandRunner` (from `automojo-interfaces`)

```python
class ICommandRunner(Protocol):
    def run(
        self,
        args: list[str],
        elevated: bool = False,
        capture_output: bool = True,
        stdin: str | None = None,
    ) -> RunResult: ...
```

The `stdin` parameter is used by `write()` and `set_ini_key()` to pipe content through
`tee`.

---

## Command Mappings

| Operation | Command |
|---|---|
| Read file content | `cat <path>` |
| Read file stat | `stat -c "%a %U %G" <path>` (mode, user, group) |
| Write file | `tee <path>` with content on stdin |
| Copy (backup/restore) | `cp -p <src> <dst>` |
| Set permissions | `chmod <mode> <path>` |
| Set ownership | `chown <user>:<group> <path>` |
| Validate | validator-specific command (see `ValidatorRegistry`) |

---

## File Writing Strategy

All writes go through `tee <path>` with content passed as `stdin` to the runner.  This
avoids shell expansion issues with special characters in content and keeps the file write
within the runner's permission and dry-run envelope.

### Write Idempotency

Before writing, `ConfigurationManager` reads the current file content with `cat`.  If the
read returns a non-zero exit code (file does not exist) the current content is treated as
empty string.  A unified diff is computed in pure Python using `difflib.unified_diff`.  If
the diff is empty, `SKIPPED` is returned without writing.

### Template Rendering

Templates are rendered using Python's `string.Template` with `safe_substitute` to detect
missing keys.  After rendering, the result is passed to the standard write path.  Missing
keys cause a `ConfigTemplateError` to be raised before any file is touched.

### INI Key Management

INI files are read via `cat`, parsed in memory using Python's `configparser`, mutated, and
serialised back to a string.  The serialised string is compared to the current file content.
If unchanged, `SKIPPED` is returned.  Otherwise the new content is written via `tee`.
`configparser` preserves comments in sections it does not modify (Python 3.2+).

### Backup Naming

```
<filepath>.bak.<YYYYMMDDTHHMMSS>
```

Example: `/etc/nginx/nginx.conf.bak.20240315T143022`

Timestamp is generated in pure Python from `datetime.utcnow()` at call time.

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `ConfigFileNotFoundError` | `backup()`, `restore()`, `set_permissions()`, `set_ownership()` called for a path that does not exist |
| `ConfigTemplateError` | A required variable is absent from the template variables dict |
| `ConfigValidatorNotFoundError` | `validate()` called with an unregistered validator identifier |

All other failures are surfaced as `ConfigActionResult` with `status=FAILED`.

---

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Write Operation Idempotency

*For any* file path and content, calling `write()` multiple times with the same arguments should result in the first call returning `SUCCESS` and subsequent calls returning `SKIPPED` with no file modification.

**Validates: Requirements 1.2**

### Property 2: File Content Correctness

*For any* file path and content string, after a successful `write()` operation, reading the file should return the exact content that was written.

**Validates: Requirements 1.1, 1.3**

### Property 3: Permission Application Correctness

*For any* file path and valid octal permission mode, calling `write()` with a mode parameter should result in the file having exactly that permission mode after the operation completes.

**Validates: Requirements 1.4**

### Property 4: Ownership Application Correctness

*For any* file path and valid user/group combination, calling `write()` with ownership parameters should result in the file having exactly that ownership after the operation completes.

**Validates: Requirements 1.5**

### Property 5: Dry-Run Safety

*For any* write operation (write, write_template, backup, restore, set_permissions, set_ownership, set_ini_key, remove_ini_key) with `dry_run=True`, the command runner should never be invoked for write commands, and the result status should be `DRY_RUN`.

**Validates: Requirements 1.7, 2.5, 3.5, 4.5, 7.6, 8.8, 9.6**

### Property 6: Template Rendering Correctness

*For any* valid template string and variables dictionary, the rendered output should contain all variable substitutions and no unresolved template placeholders.

**Validates: Requirements 2.1, 2.2**

### Property 7: Template Variable Validation

*For any* template string containing undefined variables, calling `write_template()` should raise a `ConfigTemplateError` before any file operations are attempted.

**Validates: Requirements 2.3**

### Property 8: Backup Path Generation

*For any* file path, calling `backup()` should generate a backup path following the format `<filepath>.bak.<ISO8601-timestamp>` where the timestamp is valid and unique.

**Validates: Requirements 3.1**

### Property 9: File Existence Validation

*For any* non-existent file path, operations that require the file to exist (backup, set_permissions, set_ownership) should raise a `ConfigFileNotFoundError` before invoking the command runner.

**Validates: Requirements 3.2, 4.2, 7.2, 8.4**

### Property 10: Restore Path Logic

*For any* backup file path following the format `<filepath>.bak.<timestamp>`, calling `restore()` should correctly extract the original file path and restore to that location.

**Validates: Requirements 4.1, 4.3**

### Property 11: Diff Computation Accuracy

*For any* file path and desired content, the `diff()` operation should return `has_diff=False` when current and desired content are identical, and `has_diff=True` with a valid unified diff when they differ.

**Validates: Requirements 5.1, 5.3, 5.4**

### Property 12: Non-Existent File Diff Handling

*For any* non-existent file path and desired content, the `diff()` operation should treat the current content as empty and produce a diff showing all desired content as additions.

**Validates: Requirements 5.2**

### Property 13: Validator Command Construction

*For any* registered validator identifier and file path, calling `validate()` should construct the correct command arguments as defined in the validator registry.

**Validates: Requirements 6.1**

### Property 14: Validation Result Mapping

*For any* validation command execution, the result should correctly map exit code 0 to `valid=True` and non-zero exit codes to `valid=False` with appropriate error details.

**Validates: Requirements 6.2, 6.3**

### Property 15: Unknown Validator Handling

*For any* unregistered validator identifier, calling `validate()` should raise a `ConfigValidatorNotFoundError` before attempting command execution.

**Validates: Requirements 6.4**

### Property 16: Validation in Dry-Run Mode

*For any* validation operation with `dry_run=True`, the validator command should still be executed since validation is read-only and safe.

**Validates: Requirements 6.5**

### Property 17: Permission Setting Idempotency

*For any* file path and permission mode, calling `set_permissions()` multiple times with the same mode should result in the first call succeeding and subsequent calls returning `SKIPPED`.

**Validates: Requirements 7.3**

### Property 18: Ownership Setting Idempotency

*For any* file path and ownership specification, calling `set_ownership()` multiple times with the same user/group should result in the first call succeeding and subsequent calls returning `SKIPPED`.

**Validates: Requirements 8.5**

### Property 19: INI Key Setting Idempotency

*For any* INI file path, section, key, and value, calling `set_ini_key()` multiple times with the same parameters should result in the first call succeeding and subsequent calls returning `SKIPPED`.

**Validates: Requirements 9.3**

### Property 20: INI Section Creation

*For any* INI file and non-existent section name, calling `set_ini_key()` should create the section before adding the key-value pair.

**Validates: Requirements 9.2**

### Property 21: INI Key Removal and Section Cleanup

*For any* INI file where removing a key would leave a section empty, calling `remove_ini_key()` should remove both the key and the empty section.

**Validates: Requirements 9.4**

### Property 22: Elevation for Write Operations

*For any* write operation with `elevated=True` in the config, the command runner should be invoked with `elevated=True` for all write commands.

**Validates: Requirements 10.1**

### Property 23: No Elevation for Read Operations

*For any* read operation (diff, validate), the command runner should never be invoked with `elevated=True` regardless of the elevation configuration.

**Validates: Requirements 10.2**

---

## Module Layout

```
source/packages/automojo/sysman/linux/redhat/
├── __init__.py
└── configurationmanagement.py    ← ConfigurationManager, config, models, exceptions,
                                     ValidatorRegistry

source/testroots/testbase/
└── automojo/tests/sysman/linux/redhat/
    └── test_configurationmanagement.py
```

---

---

## Usage Examples

### Local Execution (Default)

```python
from automojo.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from automojo.sysman.linux.redhat.configurationmanagement import ConfigurationManager, ConfigurationManagerConfig

# Create local runner
runner = LocalCommandRunner(LocalRunnerConfig())

# Inject into configuration manager
config = ConfigurationManagerConfig(elevated=True, dry_run=False)
config_mgr = ConfigurationManager(runner, config)

# Write configuration file
result = config_mgr.write("/etc/myapp/config.ini", "[database]\nhost=localhost\nport=5432", 
                         mode=0o644, owner="myapp", group="myapp")
print(f"Write: {result.status}")

# Validate configuration
validation = config_mgr.validate("/etc/nginx/nginx.conf", "nginx")
print(f"Valid: {validation.valid}")

# Backup before changes
backup_result = config_mgr.backup("/etc/important.conf")
print(f"Backup created: {backup_result.backup_path}")
```

### SSH Execution with Reliability Tracking

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.configurationmanagement import ConfigurationManager, ConfigurationManagerConfig

# Create SSH runner for remote system
ssh_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0
)
runner = SSHCommandRunner(ssh_config)

# Inject into configuration manager (same code as local!)
config = ConfigurationManagerConfig(elevated=True)
config_mgr = ConfigurationManager(runner, config)

# Write template-based configuration over SSH
template = """
server {
    listen ${port};
    server_name ${domain};
    root ${document_root};
}
"""
variables = {"port": "80", "domain": "example.com", "document_root": "/var/www/html"}
result = config_mgr.write_template("/etc/nginx/sites-available/mysite", template, variables)

# Check SSH reliability metrics
metrics = runner.get_metrics()
print(f"Success rate: {metrics.success_count / (metrics.success_count + metrics.failure_count):.2%}")
print(f"Average duration: {metrics.avg_duration_ms:.2f}ms")
print(f"Connection retries: {metrics.retry_count}")
```

### SSH Execution with Aspects

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.aspects import PersistenceAspect, RetryAspect, TimeoutAspect
from automojo.sysman.linux.redhat.aspects import PersistenceAspectConfig, RetryAspectConfig, TimeoutAspectConfig
from automojo.sysman.linux.redhat.storage import SQLiteStorage
from automojo.sysman.linux.redhat.configurationmanagement import ConfigurationManager, ConfigurationManagerConfig

# Create SSH runner
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))

# Add persistence aspect for audit logging
storage = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

# Add retry aspect for transient failures (e.g., file system busy)
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[1, 16],  # File system errors
    retryable_error_patterns=["Device or resource busy", "Text file busy"]
))
runner.add_aspect(retry)

# Add timeout aspect for global policy
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=120.0))
runner.add_aspect(timeout)

# Inject into configuration manager
config_mgr = ConfigurationManager(runner, ConfigurationManagerConfig(elevated=True))

# All configuration operations now have:
# - Audit logging to SQLite
# - Automatic retry on file system busy
# - 2-minute timeout enforcement

# Write configuration with backup
backup_result = config_mgr.backup("/etc/httpd/httpd.conf")
write_result = config_mgr.write("/etc/httpd/httpd.conf", new_config_content)

# Set INI configuration values
ini_result = config_mgr.set_ini_key("/etc/myapp/settings.ini", "database", "host", "new-db-server")

# Validate configuration before applying
diff_result = config_mgr.diff("/etc/nginx/nginx.conf", proposed_config)
if diff_result.has_diff:
    print("Configuration changes:")
    print(diff_result.diff)
    validation = config_mgr.validate("/tmp/nginx-test.conf", "nginx")
    if validation.valid:
        config_mgr.write("/etc/nginx/nginx.conf", proposed_config)
```

### Dry-Run Mode Across Transports

```python
# Dry-run works identically for local and SSH
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin", dry_run=True))
config_mgr = ConfigurationManager(runner, ConfigurationManagerConfig(elevated=True))

# No SSH connection is made, no files modified
result = config_mgr.write("/etc/critical-config.conf", dangerous_config)
assert result.status == ConfigActionStatus.DRY_RUN
print("Would have written:", result.message)  # Shows diff of what would change
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` injected at construction time.
- `MockCommandRunner` is configured to return specific `RunResult` values keyed on the
  command args prefix (e.g. `["cat", ...]`, `["tee", ...]`, `["stat", ...]`).
- Diff computation is tested in pure Python without a runner (it uses `difflib` directly).
- Template rendering is tested in pure Python for both success and missing-variable cases.
- INI key management is tested by supplying mock `cat` output and verifying the `tee` stdin
  content that the runner receives.
- No test requires root access or real files on disk.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in Python. Each correctness property defined above will be implemented as a property-based test that:

- Generates random but valid inputs (file paths, content, permissions, ownership, templates)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific correctness property they validate, using the format: `# Feature: configuration-management, Property N: <property description>`

**Property Test Coverage:**

- Properties 1-2: Idempotency and content correctness tests generate random file paths and content
- Properties 3-4: Permission and ownership tests generate random valid modes and user/group combinations
- Property 5: Dry-run safety tests verify no runner invocation across all write operations
- Properties 6-7: Template rendering tests generate random templates and variable dictionaries
- Properties 8-10: Backup and restore tests verify path generation and file existence validation
- Properties 11-12: Diff computation tests verify accuracy across various content combinations
- Properties 13-16: Validation tests verify command construction and result mapping
- Properties 17-21: Permission, ownership, and INI manipulation idempotency tests
- Properties 22-23: Elevation tests verify correct elevation flag usage

Both unit tests and property-based tests are complementary: unit tests verify specific examples and edge cases, while property tests verify universal correctness across many inputs.
