# Design

## Overview

The kernel parameters feature is implemented as two cooperating classes in a single module,
`automojo.sysman.linux.redhat.kernelparameters`:

- **`KernelParamManager`** — manages sysctl kernel parameters at runtime and in persisted
  drop-in files under `/etc/sysctl.d/`.
- **`KernelModuleManager`** — manages kernel module load/unload at runtime and persisted
  boot-time load configuration under `/etc/modules-load.d/`.

Both classes are constructed with the same `ICommandRunner` implementing the command runner
interface. The coordinator instantiates them directly and calls their methods to perform
discrete maintenance actions.

All system interaction is delegated to the command runner. No direct file system access
or `subprocess` calls are made inside the module. The feature works identically whether
commands execute locally (via `LocalCommandRunner`) or remotely over SSH (via
`SSHCommandRunner`). When using SSH, the coordinator can track reliability metrics and
apply execution aspects without any changes to the manager code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md) for
details on local/SSH execution, reliability tracking, and aspect configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner (Local or SSH)                      │
│  - Configures aspects (optional)                           │
│  - Injects runner into managers                            │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
               ┌─────────┴────────────┐
               ▼                      ▼
┌─────────────────────────┐  ┌─────────────────────────────────┐
│ KernelParamManager      │  │ KernelModuleManager             │
│                         │  │                                 │
│ read()                  │  │ load()                          │
│ write()                 │  │ unload()                        │
│ persist()               │  │ query_loaded()                  │
│ remove_persisted()      │  │ persist_load()                  │
│ apply_all()             │  │ remove_persisted_load()         │
│ query_all()             │  │                                 │
└────────────┬────────────┘  └────────────┬────────────────────┘
             └──────────────┬──────────────┘
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

### `KernelParamManager`

**Location:** `source/packages/automojo/sysman/linux/redhat/kernelparameters.py`

| Method | Signature | Description |
|---|---|---|
| `read` | `(key) → KernelParamInfo \| None` | Read current runtime value of a sysctl key |
| `write` | `(key, value) → KernelParamActionResult` | Set a sysctl key at runtime |
| `persist` | `(key, value, filename) → KernelParamActionResult` | Write key to drop-in file |
| `remove_persisted` | `(key, filename) → KernelParamActionResult` | Remove key from drop-in file |
| `apply_all` | `() → KernelParamActionResult` | Apply all persisted sysctl settings |
| `query_all` | `() → list[KernelParamInfo]` | Return all current sysctl key-value pairs |

### `KernelModuleManager`

| Method | Signature | Description |
|---|---|---|
| `load` | `(name, params=None) → KernelModuleActionResult` | Load a module with optional params |
| `unload` | `(name) → KernelModuleActionResult` | Unload a module |
| `query_loaded` | `() → list[KernelModuleInfo]` | List all loaded modules |
| `persist_load` | `(name, filename) → KernelModuleActionResult` | Add module to boot load file |
| `remove_persisted_load` | `(name, filename) → KernelModuleActionResult` | Remove module from boot load file |

### `KernelManagerConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no commands or file writes are executed |
| `elevated` | `bool` | `False` | When `True`, write commands pass `elevated=True` |
| `sysctl_dir` | `str` | `"/etc/sysctl.d"` | Drop-in directory for sysctl persistence |
| `modules_load_dir` | `str` | `"/etc/modules-load.d"` | Drop-in directory for module persistence |

---

## Data Models

### Kernel Parameter Types

```python
@dataclass
class KernelParamInfo:
    key: str
    value: str

class KernelParamActionStatus(Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"
    DRY_RUN  = "dry_run"

@dataclass
class KernelParamActionResult:
    status: KernelParamActionStatus
    key: str
    current_value: str | None     # value before the action
    applied_value: str | None     # value after the action (or would-be in DRY_RUN)
    filename: str | None          # drop-in filename, for persist/remove operations
    diff: str | None              # unified diff for DRY_RUN file writes
    exit_code: int | None
    stdout: str | None
    stderr: str | None
    command: list[str] | None
```

### Kernel Module Types

```python
@dataclass
class KernelModuleInfo:
    name: str
    size_bytes: int
    use_count: int

class KernelModuleActionStatus(Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"
    DRY_RUN  = "dry_run"

@dataclass
class KernelModuleActionResult:
    status: KernelModuleActionStatus
    module_name: str
    filename: str | None          # drop-in filename, for persist operations
    exit_code: int | None
    stdout: str | None
    stderr: str | None
    command: list[str] | None
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

---

## Command Mappings

### Kernel Parameter Commands

| Operation | Command |
|---|---|
| Read single parameter | `sysctl -n <key>` |
| Write parameter at runtime | `sysctl -w <key>=<value>` |
| Apply all persisted parameters | `sysctl --system` |
| Read all parameters | `sysctl -a` |
| Read drop-in file | `cat <sysctl_dir>/<filename>.conf` |
| Write drop-in file | `tee <sysctl_dir>/<filename>.conf` with content on stdin |

### Kernel Module Commands

| Operation | Command |
|---|---|
| Load module | `modprobe <name> [param=value ...]` |
| Unload module | `modprobe -r <name>` |
| List loaded modules | `lsmod` |
| Read module boot-load file | `cat <modules_load_dir>/<filename>.conf` |
| Write module boot-load file | `tee <modules_load_dir>/<filename>.conf` with content on stdin |

---

## Drop-in File Management

### sysctl Drop-in Files

Files in `/etc/sysctl.d/` use a simple `key = value` format, one entry per line, with
`#` comment lines.  `KernelParamManager` reads the file via `cat`, parses it in pure
Python, and performs the requested mutation in memory before writing back via `tee`.

**Parsing rules:**
- Lines beginning with `#` or empty lines are preserved verbatim.
- Non-comment lines are split on the first `=` character; leading/trailing whitespace is
  stripped from both key and value.
- Inline comments (` # ...`) after a value are preserved during reads but stripped during
  comparison to avoid false positive mismatches.

**Persist logic:**
1. Read file (empty string if absent).
2. Scan lines for a matching key.
3. If found with same value → `SKIPPED`.
4. If found with different value → replace the line.
5. If not found → append the line.
6. Write updated content via `tee`.

**Remove logic:**
1. Read file; if absent → `SKIPPED`.
2. Scan lines for matching key; if absent → `SKIPPED`.
3. Remove the matching line; write updated content via `tee`.

### modules-load Drop-in Files

Files in `/etc/modules-load.d/` contain one module name per line with `#` comment support.

**Persist logic:**
1. Read file (empty string if absent).
2. Scan non-comment lines for exact module name match.
3. If found → `SKIPPED`.
4. If not found → append module name line; write via `tee`.

**Remove logic:**
1. Read file; if absent → `SKIPPED`.
2. Scan non-comment lines; if name absent → `SKIPPED`.
3. Remove matching line; write updated content via `tee`.

---

## Idempotency Guards

| Method | Guard | Source of truth |
|---|---|---|
| `KernelParamManager.write()` | Current value matches desired | `sysctl -n <key>` |
| `KernelParamManager.persist()` | Key present with same value in file | `cat` + in-memory parse |
| `KernelParamManager.remove_persisted()` | Key absent from file or file absent | `cat` + in-memory parse |
| `KernelModuleManager.load()` | Module name present in `lsmod` output | `lsmod` parse |
| `KernelModuleManager.unload()` | Module name absent from `lsmod` output | `lsmod` parse |
| `KernelModuleManager.persist_load()` | Module name present in file | `cat` + in-memory parse |
| `KernelModuleManager.remove_persisted_load()` | Module name absent or file absent | `cat` + in-memory parse |

---

## `sysctl -a` Output Parsing

`sysctl -a` produces lines in the form `key = value`.  Lines that `sysctl` cannot read
due to permission restrictions are emitted to stderr and do not appear in stdout.  The
parser splits each stdout line on the first ` = ` separator and ignores any lines that do
not match this pattern.

## `lsmod` Output Parsing

`lsmod` produces a fixed-width table.  The first line is a header (`Module  Size  Used by`).
Subsequent lines have the format `<name>  <size>  <use_count>  [dependents]`.  The parser
splits on whitespace and reads the first three fields, discarding the dependents column.

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `KernelParamQueryError` | `sysctl -n` or `sysctl -a` fails for an unexpected reason |
| `KernelModuleQueryError` | `lsmod` returns a non-zero exit code |

All other failures from write operations are surfaced as action results with `status=FAILED`.

---

## Module Layout

```
source/packages/automojo/sysman/linux/redhat/
├── __init__.py
└── kernelparameters.py           ← KernelParamManager, KernelModuleManager,
                                     config, models, exceptions

source/testroots/testbase/
└── automojo/tests/sysman/linux/redhat/
    └── test_kernelparameters.py
```

---

---

## Usage Examples

### Local Execution (Default)

```python
from automojo.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from automojo.sysman.linux.redhat.kernelparameters import KernelParamManager, KernelModuleManager, KernelManagerConfig

# Create local runner
runner = LocalCommandRunner(LocalRunnerConfig())

# Inject into kernel managers
config = KernelManagerConfig(elevated=True, dry_run=False)
param_mgr = KernelParamManager(runner, config)
module_mgr = KernelModuleManager(runner, config)

# Set runtime kernel parameters
param_mgr.write("vm.swappiness", "10")
param_mgr.write("net.ipv4.ip_forward", "1")

# Make parameters persistent
param_mgr.persist("vm.swappiness", "10")
param_mgr.persist("net.ipv4.ip_forward", "1")

# Apply all persistent parameters
param_mgr.apply_all()

# Query current values
swappiness = param_mgr.read("vm.swappiness")
print(f"Current swappiness: {swappiness}")

# List all parameters
all_params = param_mgr.query_all()
print(f"Total parameters: {len(all_params)}")

# Load kernel modules
module_mgr.load("overlay")
module_mgr.load("br_netfilter")

# Make module loads persistent
module_mgr.persist_load("overlay")
module_mgr.persist_load("br_netfilter")

# Check loaded modules
loaded = module_mgr.query_loaded()
print(f"Loaded modules: {len(loaded)}")
```

### SSH Execution with Reliability Tracking

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.kernelparameters import KernelParamManager, KernelModuleManager, KernelManagerConfig

# Create SSH runner for remote system
ssh_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0
)
runner = SSHCommandRunner(ssh_config)

# Inject into kernel managers (same code as local!)
config = KernelManagerConfig(elevated=True)
param_mgr = KernelParamManager(runner, config)
module_mgr = KernelModuleManager(runner, config)

# Configure kernel parameters over SSH
param_mgr.write("kernel.pid_max", "4194304")
param_mgr.persist("kernel.pid_max", "4194304")

# Configure kernel modules over SSH
module_mgr.load("ip_vs")
module_mgr.persist_load("ip_vs")

# Apply all persistent settings
param_mgr.apply_all()

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
from automojo.sysman.linux.redhat.kernelparameters import KernelParamManager, KernelModuleManager, KernelManagerConfig

# Create SSH runner
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))

# Add persistence aspect for audit logging
storage = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

# Add retry aspect for transient failures
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[1, 2],  # sysctl/modprobe errors
    retryable_error_patterns=["Device or resource busy", "Operation not permitted"]
))
runner.add_aspect(retry)

# Add timeout aspect for global policy
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=60.0))
runner.add_aspect(timeout)

# Inject into kernel managers
config = KernelManagerConfig(elevated=True)
param_mgr = KernelParamManager(runner, config)
module_mgr = KernelModuleManager(runner, config)

# All kernel operations now have:
# - Audit logging to SQLite
# - Automatic retry on busy/permission errors
# - 1-minute timeout enforcement

# Configure comprehensive kernel tuning
param_mgr.write("vm.dirty_ratio", "15")
param_mgr.write("vm.dirty_background_ratio", "5")
param_mgr.write("net.core.rmem_max", "16777216")
param_mgr.write("net.core.wmem_max", "16777216")

# Make all changes persistent
param_mgr.persist("vm.dirty_ratio", "15")
param_mgr.persist("vm.dirty_background_ratio", "5")
param_mgr.persist("net.core.rmem_max", "16777216")
param_mgr.persist("net.core.wmem_max", "16777216")

# Load required modules
module_mgr.load("nf_conntrack")
module_mgr.persist_load("nf_conntrack")

# Apply all persistent settings
param_mgr.apply_all()
```

### Dry-Run Mode Across Transports

```python
# Dry-run works identically for local and SSH
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin", dry_run=True))
param_mgr = KernelParamManager(runner, KernelManagerConfig(elevated=True))

# No SSH connection is made, no commands executed
result = param_mgr.write("dangerous.parameter", "unsafe_value")
print("Would have set parameter:", result.message)

result = param_mgr.persist("dangerous.parameter", "unsafe_value")
print("Would have persisted parameter:", result.message)
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` keyed on command args prefix.
- `sysctl -n`, `sysctl -a`, and `lsmod` mock responses drive idempotency guards.
- `cat` mock responses supply drop-in file content for persist/remove tests.
- `sysctl_dir` and `modules_load_dir` in config are overridden in tests to avoid touching
  system paths.
- `tee` stdin content is verified in persist tests to confirm correct file rendering.
- `lsmod` and `sysctl -a` parsing tests are pure Python using representative output strings.
- No test requires root access or a live kernel.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in Python. Each correctness property defined above will be implemented as a property-based test that:

- Generates random but valid inputs (parameter names/values, module names)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific correctness property they validate, using the format: `# Feature: kernel-parameters, Property N: <property description>`

**Property Test Coverage:**

- Properties 1-3: Runtime parameter tests generate random parameter names/values and verify sysctl command construction
- Properties 4-6: Persistent parameter tests verify file operations and idempotency
- Properties 7-9: Parameter query tests verify command construction and result parsing
- Properties 10-11: Persistent query tests verify file reading and edge cases
- Properties 12-13: Persistent removal tests verify file modification correctness
- Properties 14-19: System operation tests (reload, list_all, query_pattern) verify command construction and parsing
- Properties 20-22: Dry-run safety and elevation tests verify correct flag usage

Both unit tests and property-based tests are complementary: unit tests verify specific examples and edge cases, while property tests verify universal correctness across many inputs.
