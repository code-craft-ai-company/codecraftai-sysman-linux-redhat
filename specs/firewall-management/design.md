# Design

## Overview

The firewall management feature is implemented as a single module,
`automojo.sysman.linux.redhat.firewallmanagement`, that exposes a `FirewallManager` class.
The coordinator instantiates `FirewallManager` and calls its methods to perform discrete
maintenance actions against the `firewalld` daemon via the `firewall-cmd` CLI.

All state queries used for idempotency guards go through `firewall-cmd --zone=<zone>
--list-all` or `firewall-cmd --list-all-zones`. These are parsed in pure Python. All
system interaction is delegated to a command runner implementing the `ICommandRunner`
interface. No direct file system access or `subprocess` calls are made inside the module.

The permanent flag (`--permanent`) is always used for write operations so that changes
survive a reload or reboot. After a batch of permanent changes the coordinator is
expected to call `reload()` to apply them to the runtime configuration.

The feature works identically whether commands execute locally (via `LocalCommandRunner`) or
remotely over SSH (via `SSHCommandRunner`). When using SSH, the coordinator can track
reliability metrics and apply execution aspects (persistence, retry, timeout) without any
changes to the `FirewallManager` code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md) for
details on local/SSH execution, reliability tracking, and aspect configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner (Local or SSH)                      │
│  - Configures aspects (optional)                           │
│  - Injects runner into FirewallManager                     │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            FirewallManager                                  │
│  automojo.sysman.linux.redhat.firewallmanagement           │
│                                                             │
│  add_service()      remove_service()                        │
│  add_port()         remove_port()                           │
│  add_rich_rule()    remove_rich_rule()                      │
│  set_default_zone() reload()                                │
│  query_zone()       query_all_zones()                       │
│  query_state()                                              │
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

### `FirewallManager`

**Location:** `source/packages/automojo/sysman/linux/redhat/firewallmanagement.py`

| Method | Signature | Description |
|---|---|---|
| `add_service` | `(service, zone) → FirewallActionResult` | Add service to zone (permanent) |
| `remove_service` | `(service, zone) → FirewallActionResult` | Remove service from zone (permanent) |
| `add_port` | `(port, protocol, zone) → FirewallActionResult` | Open port/protocol in zone (permanent) |
| `remove_port` | `(port, protocol, zone) → FirewallActionResult` | Close port/protocol in zone (permanent) |
| `add_rich_rule` | `(rule, zone) → FirewallActionResult` | Add rich rule to zone (permanent) |
| `remove_rich_rule` | `(rule, zone) → FirewallActionResult` | Remove rich rule from zone (permanent) |
| `set_default_zone` | `(zone) → FirewallActionResult` | Set the default zone |
| `reload` | `() → FirewallActionResult` | Apply permanent changes to runtime |
| `query_zone` | `(zone) → ZoneInfo` | Query services, ports, rich rules for a zone |
| `query_all_zones` | `() → list[ZoneInfo]` | Query all zones |
| `query_state` | `() → FirewallState` | Query running state and default zone |

### `FirewallManagerConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no commands are executed |
| `elevated` | `bool` | `False` | When `True`, write commands pass `elevated=True` |

---

## Data Models

### Result Types

```python
class FirewallActionStatus(Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"
    DRY_RUN  = "dry_run"

@dataclass
class FirewallActionResult:
    status: FirewallActionStatus
    zone: str | None
    detail: str | None          # service name, port/proto, rule string, or zone name
    exit_code: int | None
    stdout: str | None
    stderr: str | None
    command: list[str] | None
```

### Query Result Types

```python
@dataclass
class ZoneInfo:
    zone: str
    services: list[str]
    ports: list[str]            # each entry is "<port>/<protocol>"
    rich_rules: list[str]
    interfaces: list[str]
    sources: list[str]
    is_default: bool

@dataclass
class FirewallState:
    running: bool
    default_zone: str
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
    ) -> RunResult: ...
```

---

## Command Mappings

### Write Commands (always use `--permanent`)

| Operation | Command |
|---|---|
| Add service | `firewall-cmd --zone=<zone> --add-service=<service> --permanent` |
| Remove service | `firewall-cmd --zone=<zone> --remove-service=<service> --permanent` |
| Add port | `firewall-cmd --zone=<zone> --add-port=<port>/<proto> --permanent` |
| Remove port | `firewall-cmd --zone=<zone> --remove-port=<port>/<proto> --permanent` |
| Add rich rule | `firewall-cmd --zone=<zone> --add-rich-rule=<rule> --permanent` |
| Remove rich rule | `firewall-cmd --zone=<zone> --remove-rich-rule=<rule> --permanent` |
| Set default zone | `firewall-cmd --set-default-zone=<zone>` |
| Reload | `firewall-cmd --reload` |

### Read Commands (no elevation)

| Operation | Command |
|---|---|
| Query one zone | `firewall-cmd --zone=<zone> --list-all` |
| Query all zones | `firewall-cmd --list-all-zones` |
| Query running state | `firewall-cmd --state` |
| Get default zone | `firewall-cmd --get-default-zone` |

---

## `firewall-cmd --list-all` Output Parsing

`firewall-cmd --zone=<zone> --list-all` produces output in the following format:

```
public (active)
  target: default
  icmp-block-inversion: no
  interfaces: eth0
  sources:
  services: dhcpv6-client ssh
  ports: 8080/tcp 9090/tcp
  protocols:
  forward: no
  masquerade: no
  forward-ports:
  source-ports:
  icmp-blocks:
  rich rules:
        rule family="ipv4" source address="10.0.0.0/8" accept
```

The parser processes this output as follows:

| Output line prefix | Maps to `ZoneInfo` field |
|---|---|
| First line (zone name) | `zone` (strip `(active)` suffix) |
| `services:` | `services` — split on whitespace |
| `ports:` | `ports` — split on whitespace |
| `rich rules:` | `rich_rules` — subsequent indented lines until next field |
| `interfaces:` | `interfaces` — split on whitespace |
| `sources:` | `sources` — split on whitespace |

`is_default` is populated by comparing the zone name to the result of
`firewall-cmd --get-default-zone` in `query_state()` or set externally by
`query_all_zones()`.

## `firewall-cmd --list-all-zones` Output Parsing

`firewall-cmd --list-all-zones` produces the same per-zone block format as `--list-all`
repeated for every configured zone, separated by blank lines.  The parser splits the
output on blank lines to isolate each zone block, then applies the same per-block parsing
as `query_zone()`.

---

## Idempotency Guards

All idempotency checks call `query_zone()` first to obtain the current `ZoneInfo`, then
compare the desired state to the current state in pure Python.

| Method | Guard | Field inspected |
|---|---|---|
| `add_service()` | Service already in zone | `ZoneInfo.services` |
| `remove_service()` | Service not in zone | `ZoneInfo.services` |
| `add_port()` | Port/proto already open | `ZoneInfo.ports` |
| `remove_port()` | Port/proto not open | `ZoneInfo.ports` |
| `add_rich_rule()` | Identical rule already present | `ZoneInfo.rich_rules` |
| `remove_rich_rule()` | Rule not present | `ZoneInfo.rich_rules` |
| `set_default_zone()` | Default zone already matches | `FirewallState.default_zone` |

`reload()` has no idempotency guard — it is always executed when not in dry-run mode.

---

## Rich Rule Comparison

Rich rule strings are compared after normalising whitespace (collapsing multiple spaces to
one, stripping leading/trailing whitespace) to avoid false mismatches from formatting
differences between what the coordinator supplies and what `firewall-cmd --list-all`
returns.

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `FirewallQueryError` | `query_zone()`, `query_all_zones()`, or `query_state()` (get-default-zone path) returns a non-zero exit code |

`firewall-cmd --state` returning non-zero is **not** an error — it means firewalld is not
running, which is surfaced as `FirewallState(running=False, default_zone="")`.

All other failures from write operations are surfaced as `FirewallActionResult` with
`status=FAILED`.

---

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Service Addition Command Construction

*For any* service name and zone name, calling `add_service()` should construct the command `firewall-cmd --zone=<zone> --add-service=<service> --permanent` and pass it to the command runner.

**Validates: Requirements 1.1**

### Property 2: Service Addition Idempotency

*For any* service that is already present in a zone, calling `add_service()` should return `SKIPPED` without executing any commands.

**Validates: Requirements 1.2**

### Property 3: Service Removal Command Construction

*For any* service name and zone name, calling `remove_service()` should construct the command `firewall-cmd --zone=<zone> --remove-service=<service> --permanent` and pass it to the command runner.

**Validates: Requirements 2.1**

### Property 4: Service Removal Idempotency

*For any* service that is not present in a zone, calling `remove_service()` should return `SKIPPED` without executing any commands.

**Validates: Requirements 2.2**

### Property 5: Port Addition Command Construction

*For any* port number, protocol, and zone name, calling `add_port()` should construct the command `firewall-cmd --zone=<zone> --add-port=<port>/<protocol> --permanent` and pass it to the command runner.

**Validates: Requirements 3.1**

### Property 6: Port Addition Idempotency

*For any* port/protocol combination that is already open in a zone, calling `add_port()` should return `SKIPPED` without executing any commands.

**Validates: Requirements 3.2**

### Property 7: Port Removal Command Construction

*For any* port number, protocol, and zone name, calling `remove_port()` should construct the command `firewall-cmd --zone=<zone> --remove-port=<port>/<protocol> --permanent` and pass it to the command runner.

**Validates: Requirements 4.1**

### Property 8: Port Removal Idempotency

*For any* port/protocol combination that is not open in a zone, calling `remove_port()` should return `SKIPPED` without executing any commands.

**Validates: Requirements 4.2**

### Property 9: Rich Rule Addition Command Construction

*For any* rich rule string and zone name, calling `add_rich_rule()` should construct the command `firewall-cmd --zone=<zone> --add-rich-rule='<rule>' --permanent` and pass it to the command runner.

**Validates: Requirements 5.1**

### Property 10: Rich Rule Addition Idempotency

*For any* rich rule that is already present in a zone, calling `add_rich_rule()` should return `SKIPPED` without executing any commands.

**Validates: Requirements 5.2**

### Property 11: Rich Rule Removal Command Construction

*For any* rich rule string and zone name, calling `remove_rich_rule()` should construct the command `firewall-cmd --zone=<zone> --remove-rich-rule='<rule>' --permanent` and pass it to the command runner.

**Validates: Requirements 6.1**

### Property 12: Rich Rule Removal Idempotency

*For any* rich rule that is not present in a zone, calling `remove_rich_rule()` should return `SKIPPED` without executing any commands.

**Validates: Requirements 6.2**

### Property 13: Default Zone Setting Command Construction

*For any* zone name, calling `set_default_zone()` should construct the command `firewall-cmd --set-default-zone=<zone>` and pass it to the command runner.

**Validates: Requirements 7.1**

### Property 14: Default Zone Setting Idempotency

*For any* zone that is already the default zone, calling `set_default_zone()` should return `SKIPPED` without executing any commands.

**Validates: Requirements 7.2**

### Property 15: Firewall Reload Command Construction

*For any* call to `reload()`, the system should construct the command `firewall-cmd --reload` and pass it to the command runner.

**Validates: Requirements 8.1**

### Property 16: Zone Query Command Construction

*For any* zone name, calling `query_zone()` should construct the command `firewall-cmd --zone=<zone> --list-all` and pass it to the command runner.

**Validates: Requirements 9.1**

### Property 17: Zone Query Result Parsing

*For any* successful zone query output, `query_zone()` should return a `ZoneInfo` object with correctly parsed zone name, services list, ports list, and rich rules list.

**Validates: Requirements 9.2**

### Property 18: Non-Existent Zone Error Handling

*For any* non-existent zone name, calling `query_zone()` should raise a `FirewallQueryError` when the command fails with zone not found.

**Validates: Requirements 9.3**

### Property 19: All Zones Query Command Construction

*For any* call to `query_all_zones()`, the system should construct the command `firewall-cmd --list-all-zones` and pass it to the command runner.

**Validates: Requirements 10.1**

### Property 20: All Zones Query Result Parsing

*For any* successful all zones query output, `query_all_zones()` should return a list of `ZoneInfo` objects with correctly parsed information for each zone.

**Validates: Requirements 10.2**

### Property 21: Firewall State Query Command Construction

*For any* call to `query_state()`, the system should construct the commands `firewall-cmd --state` and `firewall-cmd --get-default-zone` and pass them to the command runner.

**Validates: Requirements 11.1**

### Property 22: Firewall State Result Parsing

*For any* successful state query, `query_state()` should return a `FirewallState` object with correctly parsed running status and default zone.

**Validates: Requirements 11.2**

### Property 23: Firewall Not Running Handling

*For any* failed `firewall-cmd --state` command, `query_state()` should return a `FirewallState` with `running=False` and empty default zone.

**Validates: Requirements 11.3**

### Property 24: Default Zone Query Error Handling

*For any* unexpected failure of `firewall-cmd --get-default-zone`, `query_state()` should raise a `FirewallQueryError`.

**Validates: Requirements 11.4**

### Property 25: Dry-Run Safety

*For any* write operation (add_service, remove_service, add_port, remove_port, add_rich_rule, remove_rich_rule, set_default_zone, reload) with `dry_run=True`, the command runner should never be invoked for write commands, and the result status should be `DRY_RUN`.

**Validates: Requirements 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.4**

### Property 26: Elevation for Write Operations

*For any* write operation with `elevated=True` in the config, the command runner should be invoked with `elevated=True` for all write commands.

**Validates: Requirements 12.1**

### Property 27: No Elevation for Read Operations

*For any* read operation (query_zone, query_all_zones, query_state), the command runner should never be invoked with `elevated=True` regardless of the elevation configuration.

**Validates: Requirements 12.2**

### Property 28: Error Result Consistency

*For any* failed firewall command, the returned `FirewallActionResult` should have status `FAILED` and contain the exit code and stderr from the command runner.

**Validates: Requirements 1.4, 2.4, 3.4, 4.4, 5.4, 6.4, 7.4, 8.3**

---

## Module Layout

```
source/packages/automojo/sysman/linux/redhat/
├── __init__.py
└── firewallmanagement.py         ← FirewallManager, config, models, exceptions

source/testroots/testbase/
└── automojo/tests/sysman/linux/redhat/
    └── test_firewallmanagement.py
```

---

---

## Usage Examples

### Local Execution (Default)

```python
from automojo.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from automojo.sysman.linux.redhat.firewallmanagement import FirewallManager, FirewallManagerConfig

# Create local runner
runner = LocalCommandRunner(LocalRunnerConfig())

# Inject into firewall manager
config = FirewallManagerConfig(elevated=True, dry_run=False)
fw_mgr = FirewallManager(runner, config)

# Add services to zones
result = fw_mgr.add_service("http", "public")
print(f"Add service: {result.status}")

result = fw_mgr.add_service("https", "public")
print(f"Add service: {result.status}")

# Open custom port
port_result = fw_mgr.add_port(8080, "tcp", "public")
print(f"Add port: {port_result.status}")

# Add rich rule for specific source
rich_rule = "rule family='ipv4' source address='192.168.1.0/24' service name='ssh' accept"
rule_result = fw_mgr.add_rich_rule(rich_rule, "public")
print(f"Add rich rule: {rule_result.status}")

# Apply changes
reload_result = fw_mgr.reload()
print(f"Reload: {reload_result.status}")

# Query zone state
zone_info = fw_mgr.query_zone("public")
print(f"Zone services: {zone_info.services}")
print(f"Zone ports: {zone_info.ports}")
```

### SSH Execution with Reliability Tracking

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.firewallmanagement import FirewallManager, FirewallManagerConfig

# Create SSH runner for remote system
ssh_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0
)
runner = SSHCommandRunner(ssh_config)

# Inject into firewall manager (same code as local!)
config = FirewallManagerConfig(elevated=True)
fw_mgr = FirewallManager(runner, config)

# Configure firewall over SSH
fw_mgr.add_service("ssh", "public")
fw_mgr.add_service("http", "public")
fw_mgr.add_port(443, "tcp", "public")
fw_mgr.set_default_zone("public")
fw_mgr.reload()

# Query firewall state
fw_state = fw_mgr.query_state()
print(f"Firewall running: {fw_state.running}")
print(f"Default zone: {fw_state.default_zone}")

# List all zones
all_zones = fw_mgr.query_all_zones()
for zone in all_zones:
    print(f"Zone {zone.name}: {len(zone.services)} services, {len(zone.ports)} ports")

# Check SSH reliability metrics
metrics = runner.get_metrics()
print(f"Success rate: {metrics.success_count / (metrics.success_count + metrics.failure_count):.2%}")
print(f"Average duration: {metrics.avg_duration_ms:.2f}ms")
```

### SSH Execution with Aspects

```python
from automojo.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from automojo.sysman.linux.redhat.aspects import PersistenceAspect, RetryAspect, TimeoutAspect
from automojo.sysman.linux.redhat.aspects import PersistenceAspectConfig, RetryAspectConfig, TimeoutAspectConfig
from automojo.sysman.linux.redhat.storage import SQLiteStorage
from automojo.sysman.linux.redhat.firewallmanagement import FirewallManager, FirewallManagerConfig

# Create SSH runner
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))

# Add persistence aspect for audit logging
storage = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

# Add retry aspect for transient failures (e.g., firewalld busy)
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[1, 252],  # firewalld temporary failures
    retryable_error_patterns=["FirewallD is not running", "COMMAND_FAILED"]
))
runner.add_aspect(retry)

# Add timeout aspect for global policy
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=60.0))
runner.add_aspect(timeout)

# Inject into firewall manager
fw_mgr = FirewallManager(runner, FirewallManagerConfig(elevated=True))

# All firewall operations now have:
# - Audit logging to SQLite
# - Automatic retry on firewalld busy states
# - 1-minute timeout enforcement

# Configure web server firewall rules
fw_mgr.add_service("http", "public")
fw_mgr.add_service("https", "public")

# Add custom application port
fw_mgr.add_port(8080, "tcp", "public")

# Add rich rule for database access from app servers
db_rule = "rule family='ipv4' source address='10.0.1.0/24' port port='5432' protocol='tcp' accept"
fw_mgr.add_rich_rule(db_rule, "internal")

# Apply all changes
fw_mgr.reload()

# Verify configuration
public_zone = fw_mgr.query_zone("public")
print(f"Public zone services: {public_zone.services}")
print(f"Public zone ports: {public_zone.ports}")
```

### Dry-Run Mode Across Transports

```python
# Dry-run works identically for local and SSH
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin", dry_run=True))
fw_mgr = FirewallManager(runner, FirewallManagerConfig(elevated=True))

# No SSH connection is made, no commands executed
result = fw_mgr.remove_service("ssh", "public")  # Dangerous operation!
assert result.status == FirewallActionStatus.DRY_RUN
print("Would have removed SSH service from public zone")
```

---

## Testing Strategy

- All tests use a `MockCommandRunner` keyed on command args prefix.
- `firewall-cmd --zone=<zone> --list-all` mock responses drive all idempotency guards for
  service, port, and rich rule methods.
- `firewall-cmd --list-all-zones` mock responses drive `query_all_zones()` parsing tests.
- `firewall-cmd --state` and `firewall-cmd --get-default-zone` mocks drive `query_state()`
  tests including the non-running case.
- Rich rule normalisation is tested in pure Python with representative rule strings.
- `--list-all` and `--list-all-zones` parsing are tested with representative multi-line
  output strings including zones with multiple services, ports, and rich rules.
- No test requires root access or a live `firewalld` daemon.
