# Design

## Overview

The repository management feature is implemented as a single module,
`automojo.sysman.linux.redhat.repositorymanagement`, that exposes a `RepositoryManager`
class. The coordinator instantiates `RepositoryManager` and calls its methods to perform
discrete maintenance actions against the `/etc/yum.repos.d/` directory and the `dnf`
toolchain.

All system interaction is delegated to a command runner implementing the `ICommandRunner`
interface. File write and delete operations are also expressed as commands through the runner
so that dry-run mode, auditing, and test mocking apply uniformly to both shell commands and
file system mutations. `RepositoryManager` has no direct dependency on the file system or
on `subprocess`.

The feature works identically whether commands execute locally (via `LocalCommandRunner`) or
remotely over SSH (via `SSHCommandRunner`). When using SSH, the coordinator can track
reliability metrics and apply execution aspects (persistence, retry, timeout) without any
changes to the `RepositoryManager` code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md) for
details on local/SSH execution, reliability tracking, and aspect configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner (Local or SSH)                      │
│  - Configures aspects (optional)                           │
│  - Injects runner into RepositoryManager                   │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│          RepositoryManager                                  │
│  automojo.sysman.linux.redhat.repositorymanagement         │
│                                                             │
│  add()           remove()                                   │
│  enable()        disable()                                  │
│  refresh()       list_repos()                               │
│  set_priority()  import_gpg_key()                           │
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

### `RepositoryManager`

**Location:** `source/packages/automojo/sysman/linux/redhat/repositorymanagement.py`

The primary public class.  Constructed with an `ICommandRunner` and a
`RepositoryManagerConfig`.

| Method | Description |
|---|---|
| `add(repo_def)` | Write a `.repo` file for a new repository definition |
| `remove(repo_id)` | Delete the `.repo` file for a repository |
| `enable(repo_id)` | Enable a repository using `dnf config-manager` |
| `disable(repo_id)` | Disable a repository using `dnf config-manager` |
| `refresh(repo_id=None)` | Clean and rebuild `dnf` metadata cache |
| `list_repos()` → `list[RepoInfo]` | Return all configured repositories and their state |
| `set_priority(repo_id, priority)` | Set the `priority=` field in a `.repo` file |
| `import_gpg_key(key_source)` | Import a GPG key by URL or local file path |

### `RepositoryManagerConfig`

**Location:** `source/packages/automojo/sysman/linux/redhat/repositorymanagement.py`

A dataclass holding runtime configuration for `RepositoryManager`.

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no commands or file writes are executed |
| `elevated` | `bool` | `False` | When `True`, write operations are run with sudo |
| `repos_dir` | `str` | `"/etc/yum.repos.d"` | Path to repository definition directory |

### `RepoDefinition`

An input dataclass passed to `add()` describing the repository to create.

| Field | Type | Required | Description |
|---|---|---|---|
| `repo_id` | `str` | Yes | Unique repository identifier (used as file name stem) |
| `name` | `str` | Yes | Human-readable repository name |
| `base_url` | `str` | No | Base URL for package downloads |
| `mirror_list` | `str` | No | URL to a mirror list file (mutually exclusive with `base_url`) |
| `enabled` | `bool` | Yes | Initial enabled state (`1` or `0` in file) |
| `gpg_check` | `bool` | Yes | Whether to enforce GPG signature checking |
| `gpg_key` | `str` | No | URL or path to the GPG key for this repository |
| `priority` | `int` | No | Repository priority (lower number = higher priority) |

---

## Data Models

### `RepoActionResult`

Returned by all write operations (add, remove, enable, disable, refresh, set-priority,
import-key).

```python
@dataclass
class RepoActionResult:
    status: RepoActionStatus      # SUCCESS | FAILED | SKIPPED | DRY_RUN
    repo_id: str
    exit_code: int | None         # raw exit code from command runner, if applicable
    stdout: str | None
    stderr: str | None
    command: list[str] | None     # the command that was (or would have been) run
    file_path: str | None         # the file path affected, for file operations
```

### `RepoActionStatus`

```python
class RepoActionStatus(Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"   # already in desired state
    DRY_RUN  = "dry_run"   # would have acted, but dry_run=True
```

### `RepoInfo`

Returned as elements of `list_repos()`.

```python
@dataclass
class RepoInfo:
    repo_id: str
    name: str
    enabled: bool
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Idempotent Repository Addition

*For any* repository definition, calling `add()` when a `.repo` file with identical content already exists should return `SKIPPED` status without invoking the write command.

**Validates: Requirements 1.2**

### Property 2: Idempotent Repository Removal

*For any* repository ID, calling `remove()` when no `.repo` file exists should return `SKIPPED` status without invoking the command runner.

**Validates: Requirements 2.2**

### Property 3: Idempotent Repository Enable

*For any* repository ID, calling `enable()` on an already enabled repository should return `SKIPPED` status without invoking the command runner.

**Validates: Requirements 3.2**

### Property 4: Idempotent Repository Disable

*For any* repository ID, calling `disable()` on an already disabled repository should return `SKIPPED` status without invoking the command runner.

**Validates: Requirements 4.2**

### Property 5: Idempotent Priority Setting

*For any* repository ID and priority value, calling `set_priority()` when the priority is already set to the requested value should return `SKIPPED` status without modifying the file.

**Validates: Requirements 7.2**

### Property 6: Dry-Run Safety

*For any* write operation (add, remove, enable, disable, refresh, set_priority, import_gpg_key) with `dry_run=True`, the command runner should never be invoked, and the result status should be `DRY_RUN`.

**Validates: Requirements 1.6, 2.5, 3.6, 4.6, 5.5, 7.5, 8.6**

### Property 7: File Existence Validation

*For any* local file path provided to `import_gpg_key()`, if the file does not exist on disk, a `RepoKeyFileNotFoundError` should be raised before the command runner is invoked.

**Validates: Requirements 8.3**

### Property 8: Repository File Not Found Precondition

*For any* repository ID, calling `set_priority()` when the `.repo` file does not exist should raise `RepoNotFoundError` before any command runner invocation.

**Validates: Requirements 7.3**

### Property 9: Command Construction Correctness

*For any* repository operation, the constructed command arguments passed to the runner should match the expected command format specified in the Command Mappings table.

**Validates: Requirements 1.1, 2.1, 3.1, 4.1, 5.1, 5.2, 6.1, 7.1, 8.1, 8.2**

### Property 10: Elevation Consistency

*For any* write operation with `elevated=True` in the config, the command runner should be invoked with `elevated=True`; for read operations (list_repos), `elevated` should never be `True`.

**Validates: Requirements 9.1, 9.2, 9.4**

### Property 11: Repo File Rendering Consistency

*For any* repository definition, rendering the same `RepoDefinition` multiple times should produce identical `.repo` file content.

**Validates: Requirements 1.1**

### Property 12: Status Mapping Correctness

*For any* operation result from the command runner, the `RepoActionResult` status should correctly reflect the runner's exit code: 0 maps to `SUCCESS`, non-zero maps to `FAILED` (unless the operation was skipped or in dry-run mode).

**Validates: Requirements 1.4, 1.5, 2.3, 2.4, 3.3, 3.4, 3.5, 4.3, 4.5, 5.3, 5.4, 7.4, 8.4, 8.5**

---

## Interfaces

### `ICommandRunner` (from `automojo-interfaces`)

`RepositoryManager` depends on this interface, injected at construction time.  File write
and delete operations are expressed as `tee` and `rm` commands so they flow through the
same runner and are subject to the same dry-run and elevation handling as `dnf` commands.

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

---

## Command Mappings

| Operation | Command |
|---|---|
| Write `.repo` file | `tee /etc/yum.repos.d/<repo-id>.repo` (stdin = rendered content) |
| Remove `.repo` file | `rm -f /etc/yum.repos.d/<repo-id>.repo` |
| Enable repository | `dnf config-manager --set-enabled <repo-id>` |
| Disable repository | `dnf config-manager --set-disabled <repo-id>` |
| Refresh all repos | `dnf clean metadata` then `dnf makecache` |
| Refresh one repo | `dnf clean metadata --disablerepo="*" --enablerepo=<id>` then `dnf makecache --disablerepo="*" --enablerepo=<id>` |
| List repositories | `dnf repolist all` |
| Set priority | `dnf config-manager --setopt=<repo-id>.priority=<n> --save` |
| Import GPG key | `rpm --import <key-url-or-path>` |

### `.repo` File Rendering

The `add()` method renders the `RepoDefinition` into an INI-style `.repo` file.  The
rendering is done in pure Python (no file system calls) and the resulting string is written
via the command runner using `tee`.

Example rendered output:

```ini
[myrepo]
name=My Custom Repository
baseurl=https://example.com/repo/
enabled=1
gpgcheck=1
gpgkey=https://example.com/repo/RPM-GPG-KEY
priority=10
```

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `RepoQueryError` | `dnf repolist all` returns a non-zero exit code |
| `RepoNotFoundError` | `set_priority()` is called for a repo whose `.repo` file does not exist |
| `RepoKeyFileNotFoundError` | `import_gpg_key()` is called with a local file path that does not exist |

All other failures are surfaced as a `RepoActionResult` with `status=FAILED` so the
coordinator can decide how to respond without catching exceptions for normal operational
failures.

### Idempotency Guards

| Method | Guard |
|---|---|
| `add()` | Reads existing `.repo` file content (via `cat` through the runner) and compares to rendered content before writing |
| `remove()` | Checks for existence of `.repo` file before issuing `rm` |
| `enable()` | Calls `list_repos()` to confirm current enabled state before running `config-manager` |
| `disable()` | Calls `list_repos()` to confirm current disabled state before running `config-manager` |
| `set_priority()` | Reads the current `priority=` value in the file before writing |

---

## Module Layout

```
source/packages/automojo/sysman/linux/redhat/
├── __init__.py
└── repositorymanagement.py       ← RepositoryManager, config, models, exceptions

source/testroots/testbase/
└── automojo/tests/sysman/linux/redhat/
    └── test_repositorymanagement.py  ← unit tests using MockCommandRunner
```

---

---

## Usage Examples

### Local Execution (Default)

```python
from automojo.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from automojo.sysman.linux.redhat.repositorymanagement import RepositoryManager, RepositoryManagerConfig

# Create local runner
runner = LocalCommandRunner(LocalRunnerConfig())

# Inject into repository manager
config = RepositoryManagerConfig(elevated=True, dry_run=False)
repo_manager = RepositoryManager(runner, config)

# Use repository manager
result = repo_manager.add("epel", "https://dl.fedoraproject.org/pub/epel/epel-release-latest-8.noarch.rpm")
print(f"Repository add: {result.status}")

# List repositories
repos = repo_manager.list_repos()
for repo in repos:
    print(f"Repo: {repo.id}, Enabled: {repo.enabled}")
```

### SSH Execution with Reliability Tracking

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.repositorymanagement import RepositoryManager, RepositoryManagerConfig

# Create SSH runner for remote system
ssh_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0
)
runner = SSHCommandRunner(ssh_config)

# Inject into repository manager (same code as local!)
config = RepositoryManagerConfig(elevated=True)
repo_manager = RepositoryManager(runner, config)

# Use repository manager - it doesn't know it's SSH
result = repo_manager.refresh()

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
from automojo.sysman.linux.redhat.repositorymanagement import RepositoryManager, RepositoryManagerConfig

# Create SSH runner
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))

# Add persistence aspect for audit logging
storage = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

# Add retry aspect for transient failures (e.g., network issues)
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[1, 2],  # dnf temporary failures
    retryable_error_patterns=["Temporary failure", "Cannot retrieve metalink"]
))
runner.add_aspect(retry)

# Add timeout aspect for global policy
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=600.0))  # 10 minutes for repo operations
runner.add_aspect(timeout)

# Inject into repository manager
repo_manager = RepositoryManager(runner, RepositoryManagerConfig(elevated=True))

# All repository operations now have:
# - Audit logging to SQLite
# - Automatic retry on network failures
# - 10-minute timeout enforcement
result = repo_manager.add("epel", "https://dl.fedoraproject.org/pub/epel/epel-release-latest-8.noarch.rpm")

# Import GPG key with same aspects applied
key_result = repo_manager.import_gpg_key("https://dl.fedoraproject.org/pub/epel/RPM-GPG-KEY-EPEL-8")
```

### Dry-Run Mode Across Transports

```python
# Dry-run works identically for local and SSH
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin", dry_run=True))
repo_manager = RepositoryManager(runner, RepositoryManagerConfig(elevated=True))

# No SSH connection is made, no commands executed
result = repo_manager.add("epel", "https://example.com/repo.rpm")
assert result.status == RepositoryActionStatus.DRY_RUN
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` injected into `RepositoryManager` at construction time.
- Tests verify the *arguments* passed to the runner, not file system or system state.
- The `repos_dir` field in `RepositoryManagerConfig` is overridden in tests to a temp path
  so that any file-path construction is exercised without touching `/etc/yum.repos.d/`.
- Each public method has at minimum: a success path test, a skip/idempotency test, a
  failure path test, and a dry-run test.
- `refresh()` is tested for both the all-repos and single-repo variants.
- `import_gpg_key()` is tested for both URL and local file path inputs.
- No test requires root access or a live `dnf` installation.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in Python. Each correctness property defined above will be implemented as a property-based test that:

- Generates random but valid inputs (repository IDs, definitions, priorities, configurations)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific correctness property they validate, using the format: `# Feature: repository-management, Property N: <property description>`

**Property Test Coverage:**

- Properties 1-5: Idempotency tests generate random repository data and verify repeated operations
- Property 6: Dry-run safety tests verify no runner invocation across all operations
- Properties 7-8: Precondition tests verify correct exception raising
- Property 9: Command construction tests verify correct argument formatting
- Property 10: Elevation tests verify correct elevation flag usage
- Property 11: Rendering consistency tests verify deterministic output
- Property 12: Status mapping tests verify correct result interpretation

Both unit tests and property-based tests are complementary: unit tests verify specific examples and edge cases, while property tests verify universal correctness across many inputs.
