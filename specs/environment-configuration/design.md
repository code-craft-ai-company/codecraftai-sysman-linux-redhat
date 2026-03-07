# Design

## Overview

The environment configuration feature is implemented as a single module,
`automojo.sysman.linux.redhat.environmentconfiguration`, that exposes an
`EnvironmentConfigurator` class. The coordinator instantiates `EnvironmentConfigurator`
and calls its methods to set, unset, and query environment variables in system-wide and
per-user configuration files.

All file reads and writes go through the `IFileOperator` abstraction from
`automojo-interfaces`. User home directory resolution is the only place where
`pwd.getpwnam` is called directly, isolated in a private helper. No subprocess calls
are made; no direct open/write calls touch the filesystem outside the file operator.

The feature works identically whether the underlying file operations execute locally or
remotely over SSH through the command runner architecture. When using SSH, the coordinator
can track reliability metrics and apply execution aspects (persistence, retry, timeout)
without any changes to the `EnvironmentConfigurator` code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md) for
details on local/SSH execution, reliability tracking, and aspect configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner (Local or SSH)                      │
│  - Configures aspects (optional)                           │
│  - Injects runner into EnvironmentConfigurator             │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│        EnvironmentConfigurator                              │
│  automojo.sysman.linux.redhat.environmentconfiguration     │
│                                                             │
│  set_system_var()   unset_system_var()                     │
│  set_etc_env_var()  unset_etc_env_var()                    │
│  set_user_var()     unset_user_var()                       │
│  query_system()     query_etc_env()                        │
│  query_user()                                               │
└────────────────────────┬────────────────────────────────────┘
                         │ delegates to
                         ▼
┌─────────────────────────────────────────────────────────────┐
│        IFileOperator                                        │
│  read_text(path) → str                                      │
│  write_text(path, content, elevated)                       │
│  delete(path, elevated)                                     │
│  exists(path) → bool                                        │
└────────────────────────┬────────────────────────────────────┘
                         │ implemented via
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

### `EnvironmentConfigurator`

**Location:** `source/packages/automojo/sysman/linux/redhat/environmentconfiguration.py`

| Method | Signature | Description |
|---|---|---|
| `set_system_var` | `(name, value, filename) → EnvActionResult` | Set `export NAME="value"` in `/etc/profile.d/<filename>` |
| `unset_system_var` | `(name, filename) → EnvActionResult` | Remove `export NAME=...` from `/etc/profile.d/<filename>` |
| `set_etc_env_var` | `(name, value) → EnvActionResult` | Set `NAME="value"` in `/etc/environment` |
| `unset_etc_env_var` | `(name) → EnvActionResult` | Remove `NAME=...` from `/etc/environment` |
| `set_user_var` | `(username, name, value, rc_file) → EnvActionResult` | Set `export NAME="value"` in `~<username>/<rc_file>` |
| `unset_user_var` | `(username, name, rc_file) → EnvActionResult` | Remove `export NAME=...` from `~<username>/<rc_file>` |
| `query_system` | `(filename) → dict[str, str]` | Read variables from `/etc/profile.d/<filename>` |
| `query_etc_env` | `() → dict[str, str]` | Read variables from `/etc/environment` |
| `query_user` | `(username, rc_file) → dict[str, str]` | Read variables from `~<username>/<rc_file>` |

### `EnvironmentConfiguratorConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no files are written or deleted |
| `elevated` | `bool` | `False` | When `True`, system-wide writes pass `elevated=True` |

---

## Data Models

### Result Types

```python
class EnvActionStatus(Enum):
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"

@dataclass
class EnvActionResult:
    status: EnvActionStatus
    path: str | None          # file that was (or would be) modified
    name: str | None          # variable name
    value: str | None         # new value (None for unset operations)
    message: str | None       # human-readable detail for failures/skips
```

### Exception

```python
class EnvConfigError(Exception):
    """Raised when a query operation cannot complete (e.g., unknown user)."""
```

---

## File Format Conventions

### `/etc/profile.d/<filename>` (set_system_var / unset_system_var)

- Created with a `#!/usr/bin/env bash` shebang when the file does not exist.
- Each managed variable occupies exactly one line: `export NAME="value"`.
- Values containing double quotes are escaped with `\"`.
- Lines not matching `export NAME=...` for the target name are left untouched.
- After removing a variable, if the remaining file content has no non-shebang,
  non-blank lines the file is deleted.

### `/etc/environment` (set_etc_env_var / unset_etc_env_var)

- No shebang line.
- Each managed variable occupies exactly one line: `NAME="value"`.
- Values containing double quotes are escaped with `\"`.
- Lines not matching `NAME=...` for the target name are left untouched.

### `~<username>/<rc_file>` (set_user_var / unset_user_var)

- File created with mode 0644 if it does not exist.
- Each managed variable occupies exactly one line: `export NAME="value"`.
- Values containing double quotes are escaped with `\"`.
- Lines not matching `export NAME=...` for the target name are left untouched.

---

## Parsing

### `_parse_export_lines(text: str) -> dict[str, str]`

Used by `query_system()` and `query_user()`.

1. Split text into lines.
2. For each line: strip whitespace; skip blank lines and lines starting with `#`.
3. Match against `^export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$`.
4. Strip optional surrounding double quotes from the value.
5. Return `{name: value}`.

### `_parse_env_lines(text: str) -> dict[str, str]`

Used by `query_etc_env()`.

1. Split text into lines.
2. For each line: strip whitespace; skip blank lines and lines starting with `#`.
3. Match against `^([A-Za-z_][A-Za-z0-9_]*)=(.*)$`.
4. Strip optional surrounding double quotes from the value.
5. Return `{name: value}`.

---

## Line Update Strategy

All set and unset operations use an **in-place line rewrite** approach:

```
current_lines = file_operator.read_text(path).splitlines(keepends=True)
new_lines = _apply_change(current_lines, name, value_or_None)
file_operator.write_text(path, "".join(new_lines), elevated=elevated)
```

`_apply_change` for **set** operations:
1. Scan lines for one matching the target variable name.
2. If found with same value: return lines unchanged (caller returns `SKIPPED`).
3. If found with different value: replace that line; return updated lines.
4. If not found: append new line; return updated lines.

`_apply_change` for **unset** operations:
1. Scan lines for one matching the target variable name.
2. If not found: return lines unchanged (caller returns `SKIPPED`).
3. If found: remove that line; return updated lines.

Trailing newline is preserved: if the original file ends with `\n` the rewritten file
does too.

---

## User Home Directory Resolution

```python
def _resolve_home(username: str) -> str:
    import pwd
    try:
        return pwd.getpwnam(username).pw_dir
    except KeyError:
        raise EnvConfigError(f"User not found: {username}")
```

Called by `set_user_var`, `unset_user_var`, and `query_user` before any file operation.

---

## Idempotency Guards

| Method | Guard | Mechanism |
|---|---|---|
| `set_system_var` | Variable already present with same value | Parse file, compare |
| `unset_system_var` | Variable not present in file | Parse file, check presence |
| `set_etc_env_var` | Variable already present with same value | Parse file, compare |
| `unset_etc_env_var` | Variable not present in file | Parse file, check presence |
| `set_user_var` | Variable already present with same value | Parse file, compare |
| `unset_user_var` | Variable not present in file | Parse file, check presence |

When a file does not exist:
- **set** operations: skip the read, write a new file.
- **unset** operations: return `SKIPPED` immediately.

---

## File Deletion After Unset

For `unset_system_var`: after removing the target line, if the remaining lines contain
no non-shebang non-blank content, `file_operator.delete(path, elevated=elevated)` is
called instead of `write_text`.

For `unset_etc_env_var` and `unset_user_var`: empty files are written rather than
deleted (preserving the file as a managed artifact).

---

## Dry-Run Behaviour

When `EnvironmentConfiguratorConfig.dry_run=True` all write and delete calls to the file
operator are skipped.  Parse operations (reads) still occur so that the guard logic can
determine what *would* happen, enabling accurate `SKIPPED` vs `DRY_RUN` reporting.

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Set Operation Idempotency

*For any* variable name, value, and target file (system, /etc/environment, or user rc), calling the corresponding set operation twice with the same arguments should result in the first call returning `SUCCESS` and the second call returning `SKIPPED` with no file modification on the second call.

**Validates: Requirements 1.2, 3.2, 5.2**

### Property 2: Update In-Place Correctness

*For any* variable that exists in a file with value V1, setting it to a different value V2 should update the existing line in place (preserving line position) and return `SUCCESS`, and a subsequent query should return V2.

**Validates: Requirements 1.3, 3.3, 5.3**

### Property 3: Unset Operation Idempotency

*For any* variable name and target file, calling the corresponding unset operation on a non-existent variable should return `SKIPPED` without modifying the file or invoking write operations.

**Validates: Requirements 2.2, 4.2, 6.2**

### Property 4: Dry-Run Safety

*For any* write operation (set or unset) across all file types with `dry_run=True`, the file operator should never be invoked for write or delete operations, and the result status should be `DRY_RUN`.

**Validates: Requirements 1.5, 2.5, 3.5, 4.4, 5.6, 6.5**

### Property 5: System File Shebang Creation

*For any* new `/etc/profile.d/<filename>` file created by `set_system_var()`, the file should begin with `#!/usr/bin/env bash\n` followed by the export statement.

**Validates: Requirements 1.4**

### Property 6: User RC File Creation

*For any* non-existent user rc file, calling `set_user_var()` should create the file and write the export statement, returning `SUCCESS`.

**Validates: Requirements 5.4**

### Property 7: Empty File Cleanup

*For any* `/etc/profile.d/<filename>` file, if `unset_system_var()` removes the last variable leaving only a shebang or no content lines, the file should be deleted rather than written.

**Validates: Requirements 2.3**

### Property 8: User Validation for Set Operations

*For any* non-existent username provided to `set_user_var()`, the operation should return an `EnvActionResult` with status `FAILED` and a descriptive error message without attempting file operations.

**Validates: Requirements 5.5**

### Property 9: User Validation for Unset Operations

*For any* non-existent username provided to `unset_user_var()`, the operation should return an `EnvActionResult` with status `FAILED` and a descriptive error message without attempting file operations.

**Validates: Requirements 6.4**

### Property 10: User Validation for Query Operations

*For any* non-existent username provided to `query_user()`, the operation should raise an `EnvConfigError` with a descriptive message.

**Validates: Requirements 8.3**

### Property 11: System Variable Format Correctness

*For any* variable name and value set via `set_system_var()`, the written line should match the format `export <NAME>="<value>"` with proper quote escaping for values containing double quotes.

**Validates: Requirements 1.1**

### Property 12: /etc/environment Format Correctness

*For any* variable name and value set via `set_etc_env_var()`, the written line should match the format `<NAME>="<value>"` (no `export` keyword) with proper quote escaping.

**Validates: Requirements 3.1**

### Property 13: User Variable Format Correctness

*For any* variable name and value set via `set_user_var()`, the written line should match the format `export <NAME>="<value>"` with proper quote escaping.

**Validates: Requirements 5.1**

### Property 14: Variable Removal Correctness

*For any* variable present in a file, calling the corresponding unset operation should remove exactly the line matching that variable name and return `SUCCESS`, leaving all other lines unchanged.

**Validates: Requirements 2.1, 4.1, 6.1**

### Property 15: Query Parsing Correctness

*For any* file containing valid variable definitions, the corresponding query operation should return a dict where each key-value pair matches a variable definition in the file, with proper quote unescaping.

**Validates: Requirements 7.1, 7.2, 8.1**

### Property 16: Query Non-Existent File Handling

*For any* non-existent file path, the corresponding query operation should return an empty dict without raising an error.

**Validates: Requirements 7.3, 8.2**

### Property 17: Parser Robustness

*For any* file containing lines that do not match the expected format (`export NAME=value` or `NAME=value`), the parser should skip those lines without raising an error and return only the valid variable definitions.

**Validates: Requirements 7.4, 8.4**

### Property 18: Elevation for System Writes

*For any* write operation to `/etc/profile.d/` or `/etc/environment` with `elevated=True` in the config, the file operator should be invoked with `elevated=True`.

**Validates: Requirements 9.1**

### Property 19: No Elevation for Queries

*For any* query operation (query_system, query_etc_env, query_user), the file operator should never be invoked with `elevated=True`.

**Validates: Requirements 9.2**

### Property 20: Set-Query Round Trip

*For any* variable name and value, after successfully setting it in a file, querying that file should return a dict containing that variable name mapped to the same value.

**Validates: Requirements 1.1, 3.1, 5.1, 7.1, 7.2, 8.1**

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Set Operation Idempotency

*For any* variable name, value, and target file (system, /etc/environment, or user rc), calling the corresponding set operation twice with the same arguments should result in the first call returning `SUCCESS` and the second call returning `SKIPPED` with no file modification on the second call.

**Validates: Requirements 1.2, 3.2, 5.2**

### Property 2: Update In-Place Correctness

*For any* variable that exists in a file with value V1, setting it to a different value V2 should update the existing line in place (preserving line position) and return `SUCCESS`, and a subsequent query should return V2.

**Validates: Requirements 1.3, 3.3, 5.3**

### Property 3: Unset Operation Idempotency

*For any* variable name and target file, calling the corresponding unset operation on a non-existent variable should return `SKIPPED` without modifying the file or invoking write operations.

**Validates: Requirements 2.2, 4.2, 6.2**

### Property 4: Dry-Run Safety

*For any* write operation (set or unset) across all file types with `dry_run=True`, the file operator should never be invoked for write or delete operations, and the result status should be `DRY_RUN`.

**Validates: Requirements 1.5, 2.5, 3.5, 4.4, 5.6, 6.5**

### Property 5: System File Shebang Creation

*For any* new `/etc/profile.d/<filename>` file created by `set_system_var()`, the file should begin with `#!/usr/bin/env bash\n` followed by the export statement.

**Validates: Requirements 1.4**

### Property 6: User RC File Creation

*For any* non-existent user rc file, calling `set_user_var()` should create the file and write the export statement, returning `SUCCESS`.

**Validates: Requirements 5.4**

### Property 7: Empty File Cleanup

*For any* `/etc/profile.d/<filename>` file, if `unset_system_var()` removes the last variable leaving only a shebang or no content lines, the file should be deleted rather than written.

**Validates: Requirements 2.3**

### Property 8: User Validation for Set Operations

*For any* non-existent username provided to `set_user_var()`, the operation should return an `EnvActionResult` with status `FAILED` and a descriptive error message without attempting file operations.

**Validates: Requirements 5.5**

### Property 9: User Validation for Unset Operations

*For any* non-existent username provided to `unset_user_var()`, the operation should return an `EnvActionResult` with status `FAILED` and a descriptive error message without attempting file operations.

**Validates: Requirements 6.4**

### Property 10: User Validation for Query Operations

*For any* non-existent username provided to `query_user()`, the operation should raise an `EnvConfigError` with a descriptive message.

**Validates: Requirements 8.3**

### Property 11: System Variable Format Correctness

*For any* variable name and value set via `set_system_var()`, the written line should match the format `export <NAME>="<value>"` with proper quote escaping for values containing double quotes.

**Validates: Requirements 1.1**

### Property 12: /etc/environment Format Correctness

*For any* variable name and value set via `set_etc_env_var()`, the written line should match the format `<NAME>="<value>"` (no `export` keyword) with proper quote escaping.

**Validates: Requirements 3.1**

### Property 13: User Variable Format Correctness

*For any* variable name and value set via `set_user_var()`, the written line should match the format `export <NAME>="<value>"` with proper quote escaping.

**Validates: Requirements 5.1**

### Property 14: Variable Removal Correctness

*For any* variable present in a file, calling the corresponding unset operation should remove exactly the line matching that variable name and return `SUCCESS`, leaving all other lines unchanged.

**Validates: Requirements 2.1, 4.1, 6.1**

### Property 15: Query Parsing Correctness

*For any* file containing valid variable definitions, the corresponding query operation should return a dict where each key-value pair matches a variable definition in the file, with proper quote unescaping.

**Validates: Requirements 7.1, 7.2, 8.1**

### Property 16: Query Non-Existent File Handling

*For any* non-existent file path, the corresponding query operation should return an empty dict without raising an error.

**Validates: Requirements 7.3, 8.2**

### Property 17: Parser Robustness

*For any* file containing lines that do not match the expected format (`export NAME=value` or `NAME=value`), the parser should skip those lines without raising an error and return only the valid variable definitions.

**Validates: Requirements 7.4, 8.4**

### Property 18: Elevation for System Writes

*For any* write operation to `/etc/profile.d/` or `/etc/environment` with `elevated=True` in the config, the file operator should be invoked with `elevated=True`.

**Validates: Requirements 9.1**

### Property 19: No Elevation for Queries

*For any* query operation (query_system, query_etc_env, query_user), the file operator should never be invoked with `elevated=True`.

**Validates: Requirements 9.2**

### Property 20: Set-Query Round Trip

*For any* variable name and value, after successfully setting it in a file, querying that file should return a dict containing that variable name mapped to the same value.

**Validates: Requirements 1.1, 3.1, 5.1, 7.1, 7.2, 8.1**

---

## Module Layout

```
source/packages/automojo/sysman/linux/redhat/
├── __init__.py
└── environmentconfiguration.py   ← EnvironmentConfigurator, config, models, exceptions

source/testroots/testbase/
└── automojo/tests/sysman/linux/redhat/
    └── test_environmentconfiguration.py
```

---

---

## Usage Examples

### Local Execution (Default)

```python
from automojo.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from automojo.sysman.linux.redhat.environmentconfiguration import EnvironmentConfigurator, EnvironmentConfiguratorConfig

# Create local runner and file operator
runner = LocalCommandRunner(LocalRunnerConfig())
file_operator = LocalFileOperator(runner)  # IFileOperator implementation

# Inject into environment configurator
config = EnvironmentConfiguratorConfig(elevated=True, dry_run=False)
env_config = EnvironmentConfigurator(file_operator, config)

# Set system-wide environment variable
result = env_config.set_system_var("JAVA_HOME", "/usr/lib/jvm/java-11", "java.sh")
print(f"Set system var: {result.status}")

# Query user environment variables
user_vars = env_config.query_user("appuser", ".bashrc")
print(f"User vars: {user_vars}")
```

### SSH Execution with Reliability Tracking

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.environmentconfiguration import EnvironmentConfigurator, EnvironmentConfiguratorConfig

# Create SSH runner for remote system
ssh_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0
)
runner = SSHCommandRunner(ssh_config)
file_operator = SSHFileOperator(runner)  # IFileOperator implementation over SSH

# Inject into environment configurator (same code as local!)
config = EnvironmentConfiguratorConfig(elevated=True)
env_config = EnvironmentConfigurator(file_operator, config)

# Set environment variable over SSH
result = env_config.set_etc_env_var("DATABASE_URL", "postgresql://localhost/myapp")

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
from automojo.sysman.linux.redhat.environmentconfiguration import EnvironmentConfigurator, EnvironmentConfiguratorConfig

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
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=60.0))
runner.add_aspect(timeout)

# Create file operator and environment configurator
file_operator = SSHFileOperator(runner)
env_config = EnvironmentConfigurator(file_operator, EnvironmentConfiguratorConfig(elevated=True))

# All environment operations now have:
# - Audit logging to SQLite
# - Automatic retry on file system busy
# - 1-minute timeout enforcement
result = env_config.set_user_var("appuser", "NODE_ENV", "production", ".bashrc")

# Set multiple environment variables
env_config.set_system_var("PYTHON_PATH", "/opt/python/lib", "python.sh")
env_config.set_etc_env_var("LANG", "en_US.UTF-8")
```

### Dry-Run Mode Across Transports

```python
# Dry-run works identically for local and SSH
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin", dry_run=True))
file_operator = SSHFileOperator(runner)
env_config = EnvironmentConfigurator(file_operator, EnvironmentConfiguratorConfig(elevated=True))

# No SSH connection is made, no files modified
result = env_config.set_system_var("DANGEROUS_VAR", "value", "danger.sh")
assert result.status == EnvActionStatus.DRY_RUN
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockFileOperator` backed by an in-memory `dict[str, str]` mapping
  paths to file contents.
- `_parse_export_lines` and `_parse_env_lines` are tested in pure Python with
  representative multi-line strings including blank lines, comments, and values with
  embedded quotes.
- User resolution is tested by monkeypatching `pwd.getpwnam`.
- The post-unset file deletion path for `/etc/profile.d/` is tested by verifying
  `MockFileOperator.delete` is called when only the shebang line remains.
- Dry-run tests verify that `MockFileOperator.write_text` and `.delete` are never called
  while reads still occur.
- No test requires root access or a live filesystem.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in Python. Each correctness property defined above will be implemented as a property-based test that:

- Generates random but valid inputs (variable names, values, file paths, usernames)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific correctness property they validate, using the format: `# Feature: environment-configuration, Property N: <property description>`

**Property Test Coverage:**

- Properties 1-3: Idempotency tests generate random variable names/values and verify repeated operations
- Property 4: Dry-run safety tests verify no file operator write invocation across all operations
- Properties 5-7: File creation and cleanup tests verify correct file handling
- Properties 8-10: User validation tests generate random usernames including non-existent ones
- Properties 11-13: Format correctness tests verify proper line formatting and quote escaping
- Property 14: Removal tests verify correct line deletion
- Properties 15-17: Query and parsing tests verify correct dict construction and error handling
- Properties 18-19: Elevation tests verify correct elevation flag usage
- Property 20: Round-trip tests verify set-query consistency

Both unit tests and property-based tests are complementary: unit tests verify specific examples and edge cases, while property tests verify universal correctness across many inputs.
