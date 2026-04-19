# Design

## Overview

The certificate trust management feature is implemented as a single module,
`automojo.sysman.linux.redhat.certificatetrustmanagement`, that exposes a
`CertificateTrustManager` class. The coordinator instantiates
`CertificateTrustManager` and calls its methods to install, remove, inspect, and
verify CA trust anchors on Red Hat Linux systems.

All system command execution is delegated to a command runner implementing the
`ICommandRunner` interface (for `openssl` and `update-ca-trust`). All filesystem
reads and writes under `/etc/pki/ca-trust/source/` are delegated to a file
operator implementing the `IFileOperator` interface. The
`CertificateTrustManager` never directly calls `subprocess`, shell commands, or
`open()`; it only constructs command argument lists and file-operator calls.
This keeps business logic separate from execution and makes the feature fully
testable via mock runners and mock file operators without root access or a live
system.

The feature works identically whether commands and file operations execute
locally or remotely over SSH. When using SSH, the coordinator can track
reliability metrics and apply execution aspects (persistence, retry, timeout)
without any changes to the `CertificateTrustManager` code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md)
for details on local/SSH execution, reliability tracking, and aspect
configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner and file operator (Local or SSH)    │
│  - Configures aspects (optional)                           │
│  - Injects both into CertificateTrustManager               │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            CertificateTrustManager                          │
│  automojo.sysman.linux.redhat.certificatetrustmanagement    │
│                                                             │
│  install_anchor()         remove_anchor()                   │
│  list_anchors()           refresh_trust_store()             │
│  enable_trust_store()     disable_trust_store()             │
│  query_trust_state()      verify_certificate()              │
│  get_certificate_metadata()                                 │
│  install_blocklist_anchor()  remove_blocklist_anchor()      │
└──────────┬───────────────────────────────────┬──────────────┘
           │ delegates commands to             │ delegates file I/O to
           ▼                                   ▼
┌──────────────────────────┐      ┌──────────────────────────┐
│     ICommandRunner       │      │      IFileOperator       │
│  run(args, elevated,     │      │  exists / read_bytes /   │
│      cwd, env, timeout)  │      │  write_bytes / remove /  │
│     → RunResult          │      │  listdir / stat          │
└────────────┬─────────────┘      └────────────┬─────────────┘
             │                                 │
   ┌─────────┴─────────┐             ┌─────────┴─────────┐
   ▼                   ▼             ▼                   ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ LocalCommand │ │ SSHCommand   │ │ LocalFile    │ │ SSHFile      │
│    Runner    │ │    Runner    │ │   Operator   │ │   Operator   │
│ subprocess   │ │ SSH session  │ │ local FS     │ │ SFTP / SSH   │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       └────────────────┴────────┬────────┴────────────────┘
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

### `CertificateTrustManager`

**Location:** `source/packages/automojo/sysman/linux/redhat/certificatetrustmanagement.py`

The primary public class. Constructed with an `ICommandRunner`, an
`IFileOperator`, and a `CertificateTrustManagerConfig`.

| Method | Description |
|---|---|
| `install_anchor(name, source_path)` → `CertTrustActionResult` | Validate a PEM file and copy it to `/etc/pki/ca-trust/source/anchors/<name>.pem` |
| `remove_anchor(name)` → `CertTrustActionResult` | Remove a previously-installed anchor by file name |
| `list_anchors()` → `list[TrustAnchor]` | Enumerate installed anchors and parse metadata from each |
| `refresh_trust_store(extract=True)` → `CertTrustActionResult` | Invoke `update-ca-trust extract` (or bare `update-ca-trust`) to rebuild the consolidated bundle |
| `enable_trust_store()` → `CertTrustActionResult` | Invoke `update-ca-trust enable`, skipping when already enabled |
| `disable_trust_store()` → `CertTrustActionResult` | Invoke `update-ca-trust disable`, skipping when already disabled |
| `query_trust_state()` → `TrustStoreState` | Invoke `update-ca-trust check` and parse the enabled/disabled state |
| `verify_certificate(cert_path, ca_file=None, untrusted=None)` → `CertVerifyResult` | Run `openssl verify` against the system store or an explicit CA file |
| `get_certificate_metadata(cert_path)` → `CertMetadata` | Parse subject, issuer, serial, dates, fingerprint, and SANs from a certificate |
| `install_blocklist_anchor(name, source_path)` → `CertTrustActionResult` | Copy a PEM to `/etc/pki/ca-trust/source/blacklist/<name>.pem` |
| `remove_blocklist_anchor(name)` → `CertTrustActionResult` | Remove a distrust anchor by file name |

### `CertificateTrustManagerConfig`

**Location:** `source/packages/automojo/sysman/linux/redhat/certificatetrustmanagement.py`

A dataclass holding runtime configuration for `CertificateTrustManager`.

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no commands are executed and no files are written or deleted |
| `elevated` | `bool` | `False` | When `True`, mutating commands and writes are invoked with elevated privileges |

---

## Data Models

### `CertTrustActionStatus`

```python
class CertTrustActionStatus(Enum):
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"   # already in desired state, no action needed
    DRY_RUN = "dry_run"   # would have acted, but dry_run=True
```

### `CertTrustActionResult`

Returned by all mutating operations (install_anchor, remove_anchor,
refresh_trust_store, enable_trust_store, disable_trust_store,
install_blocklist_anchor, remove_blocklist_anchor).

```python
@dataclass
class CertTrustActionResult:
    status: CertTrustActionStatus
    name: str | None
    path: str | None
    exit_code: int | None
    stdout: str | None
    stderr: str | None
    command: list[str] | None
    note: str | None
    subject: str | None
    sha256_fingerprint: str | None
    error: str | None
```

### `TrustAnchor`

Returned as elements of `list_anchors()`.

```python
@dataclass
class TrustAnchor:
    name: str
    path: str
    subject: str | None
    issuer: str | None
    not_before: str | None
    not_after: str | None
    sha256_fingerprint: str | None
    valid: bool
    error: str | None
```

### `CertMetadata`

Returned by `get_certificate_metadata()`.

```python
@dataclass
class CertMetadata:
    subject: str
    issuer: str
    serial: str
    not_before: str
    not_after: str
    sha256_fingerprint: str
    sans: list[str]
```

### `CertVerifyResult`

Returned by `verify_certificate()`.

```python
@dataclass
class CertVerifyResult:
    ok: bool
    output: str
```

### `TrustStoreState`

Returned by `query_trust_state()`.

```python
@dataclass
class TrustStoreState:
    enabled: bool
    raw_status: str
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: PEM Validation Before Copy

*For any* call to `install_anchor()` or `install_blocklist_anchor()`, the
supplied source file SHALL be validated as a syntactically valid PEM
certificate via `openssl x509 -noout -in <path>` before the file operator is
asked to write the destination. If validation fails, the file operator SHALL
NOT be invoked for any write.

**Validates: Requirements 1.1, 1.2, 8.1**

### Property 2: Byte-Identical Install Skip

*For any* call to `install_anchor()` or `install_blocklist_anchor()`, when a
file with the destination name already exists and its bytes are identical to
the source bytes, the operation SHALL return `SKIPPED` and the file operator
SHALL NOT be asked to write the destination.

**Validates: Requirements 1.4, 8.3**

### Property 3: Remove-Absent Skip

*For any* call to `remove_anchor()` or `remove_blocklist_anchor()`, when the
target file does not exist, the operation SHALL return `SKIPPED` and the file
operator SHALL NOT be asked to remove the destination.

**Validates: Requirements 2.2, 8.2**

### Property 4: Refresh Determinism

*For any* call to `refresh_trust_store()`, the command runner SHALL be invoked
with `update-ca-trust extract` (or the bare `update-ca-trust` form when
explicitly requested) on every non-dry-run call, and the returned
`CertTrustActionResult` status SHALL be deterministic with respect to the
runner's exit code: zero maps to `SUCCESS`, non-zero maps to `FAILED`.

**Validates: Requirements 4.1, 4.2, 4.3, 4.4**

### Property 5: Trust State Query Freshness

*For any* call to `query_trust_state()`, the command runner SHALL be invoked
with `update-ca-trust check` every time; the result SHALL NOT be cached
between calls. The returned `TrustStoreState.enabled` SHALL be derived purely
from the parsed output of that invocation.

**Validates: Requirements 5.3**

### Property 6: Verify Returns Result, Does Not Throw on Mismatch

*For any* call to `verify_certificate()` against an existing certificate file,
a non-zero exit code from `openssl verify` SHALL produce a `CertVerifyResult`
with `ok=False` and the captured output; the method SHALL NOT raise an
exception for a verification failure. Only a missing input file SHALL raise
`CertTrustPathNotFoundError`.

**Validates: Requirements 6.1, 6.4, 6.5**

### Property 7: Dry-Run Safety

*For any* mutating operation (install_anchor, remove_anchor,
refresh_trust_store, enable_trust_store, disable_trust_store,
install_blocklist_anchor, remove_blocklist_anchor) with `dry_run=True`, the
command runner SHALL NOT be invoked with a mutating command and the file
operator SHALL NOT be invoked for any write or remove. The returned status
SHALL be `DRY_RUN`.

**Validates: Requirements 1.6, 2.4, 4.5, 5.5, 8.5**

### Property 8: Elevation Consistency

*For any* mutating operation with `elevated=True` in the config, the command
runner and file operator SHALL be invoked with `elevated=True`. For read-only
operations (list_anchors, query_trust_state, verify_certificate,
get_certificate_metadata), `elevated` SHALL never be `True`.

**Validates: Requirements 9.1, 9.2, 9.3**

### Property 9: Metadata Round Trip

*For any* certificate for which `get_certificate_metadata()` returns a
`CertMetadata`, the `subject`, `issuer`, and `sha256_fingerprint` fields SHALL
match the values populated in the `TrustAnchor` record returned by
`list_anchors()` when the same file is installed as an anchor.

**Validates: Requirements 3.2, 7.1, 7.2**

### Property 10: Blocklist Symmetry

*For any* blocklist anchor name, `install_blocklist_anchor()` followed by
`remove_blocklist_anchor()` with the same name SHALL leave the blocklist
directory in its original state with respect to that file, and the command
runner SHALL NOT have been invoked with any `update-ca-trust` write between
them.

**Validates: Requirements 8.1, 8.2, 8.4**

---

## Interfaces

### `ICommandRunner` (from `automojo-interfaces`)

`CertificateTrustManager` depends on this interface, injected at construction
time.

```python
class ICommandRunner(Protocol):
    def run(
        self,
        args: list[str],
        elevated: bool = False,
        capture_output: bool = True,
    ) -> RunResult: ...
```

### `IFileOperator` (from `automojo-interfaces`)

`CertificateTrustManager` depends on this interface, injected at construction
time. Used for reading, writing, enumerating, and deleting files under
`/etc/pki/ca-trust/source/`.

```python
class IFileOperator(Protocol):
    def exists(self, path: str) -> bool: ...
    def read_bytes(self, path: str) -> bytes: ...
    def write_bytes(
        self,
        path: str,
        data: bytes,
        mode: int = 0o644,
        owner: str | None = None,
        group: str | None = None,
        elevated: bool = False,
    ) -> None: ...
    def remove(self, path: str, elevated: bool = False) -> None: ...
    def listdir(self, path: str) -> list[str]: ...
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

When `CertificateTrustManagerConfig.elevated` is `True`, the
`CertificateTrustManager` passes `elevated=True` to `ICommandRunner.run()` and
to `IFileOperator.write_bytes()` / `IFileOperator.remove()` for all mutating
operations. The runner and file operator are responsible for prepending `sudo`
or using whatever privilege escalation mechanism is appropriate for the
environment. `CertificateTrustManager` has no knowledge of how elevation is
implemented.

---

## Command Mappings

| Operation | Command |
|---|---|
| Validate PEM syntax | `openssl x509 -noout -in <path>` |
| Read certificate metadata | `openssl x509 -noout -subject -issuer -serial -startdate -enddate -fingerprint -sha256 -ext subjectAltName -in <path>` |
| List-anchor metadata (compact) | `openssl x509 -noout -subject -issuer -dates -fingerprint -sha256 -in <path>` |
| Verify against system store | `openssl verify <path>` |
| Verify with explicit CA file | `openssl verify -CAfile <ca-path> <path>` |
| Verify with untrusted chain | `openssl verify -untrusted <chain-path> <path>` |
| Refresh consolidated store (default) | `update-ca-trust extract` |
| Refresh consolidated store (bare) | `update-ca-trust` |
| Enable trust store | `update-ca-trust enable` |
| Disable trust store | `update-ca-trust disable` |
| Query trust-store state | `update-ca-trust check` |
| Install anchor (file write) | `IFileOperator.write_bytes("/etc/pki/ca-trust/source/anchors/<name>.pem", data, mode=0o644, owner="root", group="root", elevated=True)` |
| Remove anchor (file remove) | `IFileOperator.remove("/etc/pki/ca-trust/source/anchors/<name>", elevated=True)` |
| Install blocklist anchor (file write) | `IFileOperator.write_bytes("/etc/pki/ca-trust/source/blacklist/<name>.pem", data, mode=0o644, owner="root", group="root", elevated=True)` |
| Remove blocklist anchor (file remove) | `IFileOperator.remove("/etc/pki/ca-trust/source/blacklist/<name>", elevated=True)` |
| List anchor directory | `IFileOperator.listdir("/etc/pki/ca-trust/source/anchors/")` |

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `CertTrustQueryError` | `openssl x509` metadata parsing fails, `update-ca-trust check` returns an unexpected exit code, or metadata fields cannot be parsed |
| `CertTrustPathNotFoundError` | `verify_certificate()` or `get_certificate_metadata()` is called with a path that does not exist on disk |

All other failures are surfaced as a `CertTrustActionResult` with
`status=FAILED` so the coordinator can decide how to respond without catching
exceptions for normal operational failures. In particular,
`verify_certificate()` returns `CertVerifyResult(ok=False, output=...)` for a
non-zero `openssl verify` exit code rather than raising.

### PEM Validation Before Copy

Before any write to `/etc/pki/ca-trust/source/anchors/` or
`/etc/pki/ca-trust/source/blacklist/`, `CertificateTrustManager` invokes
`openssl x509 -noout -in <source>` via the command runner. If the validation
command exits non-zero, the method returns `CertTrustActionResult` with
`status=FAILED`, the captured stderr in the `error` field, and no write is
attempted through the file operator.

### Idempotency Guards

Before writing an anchor, `CertificateTrustManager` reads the destination (if
it exists) via the file operator and compares bytes. Identical content results
in `SKIPPED` without a write. Before removing an anchor, the manager checks
existence via the file operator; an absent file results in `SKIPPED` without
a remove. Before calling `update-ca-trust enable` or `update-ca-trust disable`,
the manager calls `query_trust_state()` and returns `SKIPPED` when already in
the desired state.

---

## Module Layout

```
source/packages/automojo/sysman/linux/redhat/
├── __init__.py
└── certificatetrustmanagement.py   ← CertificateTrustManager, config, models, exceptions

source/testroots/testbase/
└── automojo/tests/sysman/linux/redhat/
    └── test_certificatetrustmanagement.py   ← unit tests using MockCommandRunner + MockFileOperator
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` and a `MockFileOperator` injected into
  `CertificateTrustManager` at construction time.
- Tests verify the *arguments* passed to the runner and file operator, not
  system state.
- Each public method has at minimum: a success-path test, a skip/idempotency
  test where applicable, a failure-path test, and a dry-run test.
- `install_anchor()` has additional tests for PEM-validation failure,
  byte-identical skip, and elevated write parameters.
- `verify_certificate()` has additional tests for non-zero exit producing
  `ok=False`, for `-CAfile` and `-untrusted` flag wiring, and for
  `CertTrustPathNotFoundError` when the input file is missing.
- `get_certificate_metadata()` has additional tests for
  `CertTrustPathNotFoundError` and `CertTrustQueryError`.
- No test requires root access, live `openssl`, or a live `update-ca-trust`
  installation.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in
Python. Each correctness property defined above will be implemented as a
property-based test that:

- Generates random but valid inputs (anchor names, byte payloads,
  configurations, exit codes)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific
correctness property they validate, using the format:
`# Feature: certificate-trust-management, Property N: <property description>`

**Property Test Coverage:**

- Property 1: PEM validation tests generate random byte payloads and verify the
  file operator is never invoked for writes when validation fails
- Property 2: Byte-identical-skip tests generate random identical payloads and
  verify `SKIPPED` with no write
- Property 3: Remove-absent-skip tests generate random names and verify
  `SKIPPED` with no remove call
- Property 4: Refresh determinism tests generate random exit codes and verify
  result status mapping
- Property 5: Trust state freshness tests verify runner is invoked on every
  `query_trust_state()` call
- Property 6: Verify-no-throw tests generate non-zero exit codes and verify
  `ok=False` is returned
- Property 7: Dry-run safety tests verify no runner or file-operator mutation
  across all operations
- Property 8: Elevation tests verify correct elevation flag propagation on
  mutating operations and its absence on read-only operations
- Property 9: Metadata round-trip tests verify field equality between
  `get_certificate_metadata()` and `list_anchors()` entries
- Property 10: Blocklist symmetry tests verify install-then-remove restores
  the prior state

Both unit tests and property-based tests are complementary: unit tests verify
specific examples and edge cases, while property tests verify universal
correctness across many inputs.

---

## Usage Examples

### Local Execution (Install Internal CA and Refresh)

```python
from automojo.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from automojo.sysman.linux.redhat.fileops import LocalFileOperator, LocalFileOperatorConfig
from automojo.sysman.linux.redhat.certificatetrustmanagement import (
    CertificateTrustManager,
    CertificateTrustManagerConfig,
)

# Create local runner and file operator
runner = LocalCommandRunner(LocalRunnerConfig())
fileop = LocalFileOperator(LocalFileOperatorConfig())

# Inject into trust manager
config = CertificateTrustManagerConfig(elevated=True, dry_run=False)
trust_manager = CertificateTrustManager(runner, fileop, config)

# Install an internal CA as a trust anchor
install_result = trust_manager.install_anchor(
    name="internal-root-ca",
    source_path="/opt/pki/internal-root-ca.pem",
)
print(f"Install: {install_result.status}")

# Refresh the consolidated bundle so TLS clients pick up the new anchor
refresh_result = trust_manager.refresh_trust_store(extract=True)
print(f"Refresh: {refresh_result.status}")
```

### SSH Execution with Reliability Tracking (Deploy CA Bundle to Fleet)

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.fileops import SSHFileOperator, SSHFileOperatorConfig
from automojo.sysman.linux.redhat.certificatetrustmanagement import (
    CertificateTrustManager,
    CertificateTrustManagerConfig,
)

# Create SSH runner and SSH file operator for a remote system
ssh_runner_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0,
)
runner = SSHCommandRunner(ssh_runner_config)

ssh_fileop_config = SSHFileOperatorConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
)
fileop = SSHFileOperator(ssh_fileop_config)

# Inject into trust manager (same code as local!)
config = CertificateTrustManagerConfig(elevated=True)
trust_manager = CertificateTrustManager(runner, fileop, config)

# Deploy a CA bundle to the remote host
install_result = trust_manager.install_anchor(
    name="corp-issuing-ca",
    source_path="/opt/pki/corp-issuing-ca.pem",
)
refresh_result = trust_manager.refresh_trust_store()

# Check SSH reliability metrics
metrics = runner.get_metrics()
total = metrics.success_count + metrics.failure_count
success_rate = metrics.success_count / total if total > 0 else 0.0
print(f"Success rate: {success_rate:.2%}")
print(f"Average duration: {metrics.avg_duration_ms:.2f}ms")
print(f"Connection retries: {metrics.retry_count}")
if metrics.last_error is not None:
    print(f"Last error: {metrics.last_error_type.value} - {metrics.last_error}")
```

### SSH Execution with Aspects (Persistence + Retry + Timeout)

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.fileops import SSHFileOperator, SSHFileOperatorConfig
from automojo.sysman.linux.redhat.aspects import (
    PersistenceAspect, RetryAspect, TimeoutAspect,
    PersistenceAspectConfig, RetryAspectConfig, TimeoutAspectConfig,
)
from automojo.sysman.linux.redhat.storage import SQLiteStorage
from automojo.sysman.linux.redhat.certificatetrustmanagement import (
    CertificateTrustManager,
    CertificateTrustManagerConfig,
)

# Create SSH runner and file operator
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))
fileop = SSHFileOperator(SSHFileOperatorConfig(host="remote-server", username="admin"))

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
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=300.0))
runner.add_aspect(timeout)

# Inject into trust manager
trust_manager = CertificateTrustManager(
    runner,
    fileop,
    CertificateTrustManagerConfig(elevated=True),
)

# All trust-store operations now have:
# - Audit logging to SQLite
# - Automatic retry on transient failures
# - 5-minute timeout enforcement
install_result = trust_manager.install_anchor(
    name="corp-issuing-ca",
    source_path="/opt/pki/corp-issuing-ca.pem",
)
refresh_result = trust_manager.refresh_trust_store()
```

### Dry-Run Mode Across Transports

```python
# Dry-run works identically for local and SSH
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin", dry_run=True))
fileop = SSHFileOperator(SSHFileOperatorConfig(host="remote-server", username="admin"))
trust_manager = CertificateTrustManager(
    runner,
    fileop,
    CertificateTrustManagerConfig(elevated=True, dry_run=True),
)

# No SSH connection is made, no commands executed, no files written
result = trust_manager.install_anchor(
    name="corp-issuing-ca",
    source_path="/opt/pki/corp-issuing-ca.pem",
)
assert result.status == CertTrustActionStatus.DRY_RUN
```
