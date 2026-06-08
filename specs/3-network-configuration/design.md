# Design

## Overview

The network configuration feature is implemented as a single module,
`ccai.sysman.linux.redhat.networkconfiguration`, that exposes a `NetworkManager` class.
The coordinator instantiates `NetworkManager` and calls its methods to perform discrete
maintenance actions across hostname, hosts file, DNS resolver, NTP/Chrony, and
NetworkManager connection management.

All system interaction is delegated to a command runner implementing the `ICommandRunner`
interface. File reads use `cat` through the runner; file writes use `tee` through the runner.
No direct file system access or `subprocess` calls are made inside the module.

The feature works identically whether commands execute locally (via `LocalCommandRunner`)
or remotely over SSH (via `SSHCommandRunner`). When using SSH, the coordinator can track
reliability metrics and apply execution aspects without any changes to the `NetworkManager`
code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md) for
details on local/SSH execution, reliability tracking, and aspect configuration.

> **Naming note:** The class is named `NetworkManager` within this package.  It wraps
> the `nmcli` CLI to interact with the system's `NetworkManager` daemon — it is not the
> daemon itself.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner (Local or SSH)                      │
│  - Configures aspects (optional)                           │
│  - Injects runner into NetworkManager                      │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                         ▼
┌─────────────────────────────────────────────────────────────┐
│          NetworkManager                                     │
│  ccai.sysman.linux.redhat.networkconfiguration         │
│                                                             │
│  set_hostname()    query_hostname()                         │
│  add_hosts_entry() remove_hosts_entry()                     │
│  query_hosts()                                              │
│  set_nameservers() set_search_domains()                     │
│  add_ntp_server()  remove_ntp_server()                      │
│  query_ntp_sync()                                           │
│  add_connection()  remove_connection()                      │
│  query_connections()                                        │
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

### `NetworkManager` (class)

**Location:** `source/packages/ccai/sysman/linux/redhat/networkconfiguration.py`

| Method | Signature | Description |
|---|---|---|
| `set_hostname` | `(hostname) → NetworkActionResult` | Set static hostname via hostnamectl |
| `query_hostname` | `() → HostnameInfo` | Query hostname fields via hostnamectl |
| `add_hosts_entry` | `(ip, hostnames) → NetworkActionResult` | Add or update /etc/hosts entry |
| `remove_hosts_entry` | `(ip) → NetworkActionResult` | Remove /etc/hosts line by IP |
| `query_hosts` | `() → list[HostsEntry]` | Return parsed /etc/hosts entries |
| `set_nameservers` | `(nameservers) → NetworkActionResult` | Set nameserver lines in resolv.conf |
| `set_search_domains` | `(domains) → NetworkActionResult` | Set search directive in resolv.conf |
| `add_ntp_server` | `(address) → NetworkActionResult` | Add server line to chrony.conf |
| `remove_ntp_server` | `(address) → NetworkActionResult` | Remove server line from chrony.conf |
| `query_ntp_sync` | `() → NtpSyncInfo` | Parse chronyc tracking output |
| `add_connection` | `(spec: NmConnectionSpec) → NetworkActionResult` | Add or modify NM connection |
| `remove_connection` | `(name) → NetworkActionResult` | Delete NM connection by name |
| `query_connections` | `() → list[NmConnectionInfo]` | List NM connections |

### `NetworkManagerConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no commands or writes executed |
| `elevated` | `bool` | `False` | When `True`, write commands pass `elevated=True` |
| `hosts_path` | `str` | `"/etc/hosts"` | Path to hosts file |
| `resolv_conf_path` | `str` | `"/etc/resolv.conf"` | Path to resolver config |
| `chrony_conf_path` | `str` | `"/etc/chrony.conf"` | Path to Chrony config |

---

## Data Models

### Input Types

```python
@dataclass
class NmConnectionSpec:
    name: str
    conn_type: str              # e.g. "ethernet", "wifi", "bond"
    device: str | None = None
    ip4: str | None = None      # CIDR notation e.g. "192.168.1.10/24"
    gw4: str | None = None
    dns4: list[str] = field(default_factory=list)
    autoconnect: bool = True
```

### Result Types

```python
class NetworkActionStatus(Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"
    DRY_RUN  = "dry_run"

@dataclass
class NetworkActionResult:
    status: NetworkActionStatus
    detail: str | None          # human-readable context (hostname, IP, connection name)
    diff: str | None            # unified diff for file-write DRY_RUN results
    exit_code: int | None
    stdout: str | None
    stderr: str | None
    command: list[str] | None
```

### Query Result Types

```python
@dataclass
class HostnameInfo:
    static_hostname: str
    transient_hostname: str
    pretty_hostname: str

@dataclass
class HostsEntry:
    ip: str
    hostnames: list[str]        # first element is primary name; rest are aliases

@dataclass
class NtpSyncInfo:
    reference_source: str
    system_time_offset_seconds: float
    is_synchronised: bool

@dataclass
class NmConnectionInfo:
    name: str
    conn_type: str
    device: str
    state: str                  # "activated" | "deactivated" | "--"
```

---

## Interfaces

### `ICommandRunner` (from `codecraftai-interfaces`)

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

### Hostname

| Operation | Command |
|---|---|
| Query hostname | `hostnamectl --json short` |
| Set hostname | `hostnamectl set-hostname <hostname>` |

### /etc/hosts

| Operation | Command |
|---|---|
| Read hosts file | `cat <hosts_path>` |
| Write hosts file | `tee <hosts_path>` with content on stdin |

### DNS Resolver

| Operation | Command |
|---|---|
| Read resolv.conf | `cat <resolv_conf_path>` |
| Write resolv.conf | `tee <resolv_conf_path>` with content on stdin |

### NTP / Chrony

| Operation | Command |
|---|---|
| Read chrony.conf | `cat <chrony_conf_path>` |
| Write chrony.conf | `tee <chrony_conf_path>` with content on stdin |
| Restart chronyd | `systemctl restart chronyd` |
| Query sync status | `chronyc tracking` |

### NetworkManager (nmcli)

| Operation | Command |
|---|---|
| List connections | `nmcli -t -f NAME,TYPE,DEVICE,STATE connection show` |
| Add connection | `nmcli connection add type <type> con-name <name> [options...]` |
| Modify connection | `nmcli connection modify <name> [options...]` |
| Activate connection | `nmcli connection up <name>` |
| Delete connection | `nmcli connection delete <name>` |

---

## File Management Strategy

All file reads use `cat <path>` through the runner.  All writes use `tee <path>` with
content passed as `stdin`.  Each write method follows the read-parse-modify-write pattern:

1. Read current content via `cat`.
2. Parse in pure Python.
3. Apply the requested mutation in memory.
4. Compare mutated content to original; return `SKIPPED` if identical.
5. Compute unified diff for `DRY_RUN` result.
6. Write mutated content via `tee` when not in dry-run mode.

### /etc/hosts Parsing

Lines are parsed into `(ip, hostnames)` tuples.  Comment lines (`#`) and blank lines are
preserved verbatim in the output so they survive a read-write cycle unchanged.

### /etc/resolv.conf Parsing

Lines are categorised by directive prefix: `nameserver`, `search`, `domain`, or other.
`set_nameservers()` replaces all `nameserver` lines while leaving `search`, `domain`, and
unknown lines intact.  `set_search_domains()` replaces the `search` line while leaving
`nameserver` and other lines intact.

### /etc/chrony.conf Parsing

Lines matching `^server\s+<address>` are identified as NTP server entries.
`add_ntp_server()` appends a `server <address> iburst` line when not already present.
`remove_ntp_server()` removes lines matching the address.  All other lines are preserved.
After a successful file write, `systemctl restart chronyd` is invoked to apply the change.

---

## Idempotency Guards

| Method | Guard | Source of truth |
|---|---|---|
| `set_hostname()` | Current static hostname matches desired | `hostnamectl --json short` |
| `add_hosts_entry()` | Identical line already exists | `cat /etc/hosts` parse |
| `remove_hosts_entry()` | No line for that IP | `cat /etc/hosts` parse |
| `set_nameservers()` | Nameserver list identical | `cat /etc/resolv.conf` parse |
| `set_search_domains()` | Search domain list identical | `cat /etc/resolv.conf` parse |
| `add_ntp_server()` | Server address already present | `cat /etc/chrony.conf` parse |
| `remove_ntp_server()` | Server address not present | `cat /etc/chrony.conf` parse |
| `add_connection()` | Connection exists with all fields matching | `nmcli show` parse |
| `remove_connection()` | No connection with that name | `nmcli show` parse |

---

## `hostnamectl --json short` Parsing

`hostnamectl --json short` returns a JSON object.  The relevant fields are:

```json
{
  "StaticHostname": "myhost.example.com",
  "TransientHostname": "myhost",
  "PrettyHostname": ""
}
```

These are mapped directly to `HostnameInfo` fields.  Missing keys default to empty string.

## `chronyc tracking` Parsing

`chronyc tracking` produces a multi-line key-value output.  The parser extracts:
- `Reference ID` line → `reference_source`
- `System time` line → `system_time_offset_seconds` (float, direction sign applied)
- `Leap status` line → `is_synchronised` (`True` when value is `Normal`)

## `nmcli -t` Parsing

`nmcli -t -f NAME,TYPE,DEVICE,STATE connection show` produces colon-delimited lines.
Each line maps to a `NmConnectionInfo` record by splitting on `:`.

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `NetworkQueryError` | `query_hostname()`, `query_hosts()`, `query_ntp_sync()`, or `query_connections()` returns an unexpected non-zero exit code |

All other failures from write operations are surfaced as `NetworkActionResult` with
`status=FAILED`.

---

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Hostname Setting Idempotency

*For any* hostname string, calling `set_hostname()` multiple times with the same hostname should result in the first call returning `SUCCESS` and subsequent calls returning `SKIPPED` with no additional hostnamectl commands.

**Validates: Requirements 1.2**

### Property 2: Hostname Setting Command Construction

*For any* hostname string, calling `set_hostname()` should construct the command `hostnamectl set-hostname <hostname>` and pass it to the command runner.

**Validates: Requirements 1.1**

### Property 3: Hostname Query Command Construction

*For any* call to `query_hostname()`, the system should construct the command `hostnamectl --json short` and pass it to the command runner.

**Validates: Requirements 2.1**

### Property 4: Hostname Query Result Parsing

*For any* successful hostnamectl --json output, `query_hostname()` should return a `HostnameInfo` with correctly parsed static, transient, and pretty hostnames.

**Validates: Requirements 2.2**

### Property 5: Hosts Entry Addition Correctness

*For any* IP address and hostname list, calling `add_hosts_entry()` should append the correct format line to `/etc/hosts` via the command runner.

**Validates: Requirements 3.1**

### Property 6: Hosts Entry Update Behavior

*For any* IP address that already exists in `/etc/hosts`, calling `add_hosts_entry()` should update the existing line with new hostnames and return `SUCCESS`.

**Validates: Requirements 3.2**

### Property 7: Hosts Entry Addition Idempotency

*For any* identical hosts entry, calling `add_hosts_entry()` multiple times should result in the first call returning `SUCCESS` and subsequent calls returning `SKIPPED`.

**Validates: Requirements 3.3**

### Property 8: Hosts Entry Removal Correctness

*For any* IP address, calling `remove_hosts_entry()` should remove the matching line from `/etc/hosts` via the command runner.

**Validates: Requirements 4.1**

### Property 9: Hosts Entry Removal Idempotency

*For any* non-existent IP address, calling `remove_hosts_entry()` should return `SKIPPED` without modifying the file.

**Validates: Requirements 4.2**

### Property 10: Hosts File Query Command Construction

*For any* call to `query_hosts()`, the system should read `/etc/hosts` via the command runner.

**Validates: Requirements 5.1**

### Property 11: Hosts File Parsing Correctness

*For any* successful hosts file read, `query_hosts()` should return a list of `HostsEntry` records parsed from non-comment lines.

**Validates: Requirements 5.2**

### Property 12: DNS Nameserver Configuration Correctness

*For any* list of nameserver IP addresses, calling `set_nameservers()` should write the correct nameserver lines to `/etc/resolv.conf` while preserving search/domain directives.

**Validates: Requirements 6.1**

### Property 13: DNS Nameserver Configuration Idempotency

*For any* nameserver list that already matches the current configuration, calling `set_nameservers()` should return `SKIPPED` without modifying the file.

**Validates: Requirements 6.2**

### Property 14: DNS Search Domain Configuration Correctness

*For any* list of search domains, calling `set_search_domains()` should update the search directive in `/etc/resolv.conf` while preserving nameserver directives.

**Validates: Requirements 7.1**

### Property 15: DNS Search Domain Configuration Idempotency

*For any* search domain list that already matches the current configuration, calling `set_search_domains()` should return `SKIPPED` without modifying the file.

**Validates: Requirements 7.2**

### Property 16: NTP Server Addition Correctness

*For any* NTP server address, calling `add_ntp_server()` should add the correct server line to `/etc/chrony.conf` via the command runner.

**Validates: Requirements 8.1**

### Property 17: NTP Server Addition Idempotency

*For any* NTP server address already present in the configuration, calling `add_ntp_server()` should return `SKIPPED` without modifying the file.

**Validates: Requirements 8.2**

### Property 18: NTP Server Addition Service Restart

*For any* successful NTP server addition, the system should restart the chronyd service via the command runner.

**Validates: Requirements 8.3**

### Property 19: NTP Server Addition Error Handling

*For any* failed NTP server addition, the system should return `FAILED` without restarting the chronyd service.

**Validates: Requirements 8.4**

### Property 20: NTP Server Removal Correctness

*For any* NTP server address, calling `remove_ntp_server()` should remove the matching server line from `/etc/chrony.conf` via the command runner.

**Validates: Requirements 9.1**

### Property 21: NTP Server Removal Idempotency

*For any* NTP server address not present in the configuration, calling `remove_ntp_server()` should return `SKIPPED` without modifying the file.

**Validates: Requirements 9.2**

### Property 22: NTP Server Removal Service Restart

*For any* successful NTP server removal, the system should restart the chronyd service via the command runner.

**Validates: Requirements 9.3**

### Property 23: NTP Server Removal Error Handling

*For any* failed NTP server removal, the system should return `FAILED` without restarting the chronyd service.

**Validates: Requirements 9.4**

### Property 24: NTP Sync Status Query Command Construction

*For any* call to `query_ntp_sync()`, the system should construct the command `chronyc tracking` and pass it to the command runner.

**Validates: Requirements 10.1**

### Property 25: NTP Sync Status Result Parsing

*For any* successful chronyc tracking output, `query_ntp_sync()` should return an `NtpSyncInfo` with correctly parsed reference source, offset, and sync status.

**Validates: Requirements 10.2**

### Property 26: NetworkManager Connection Management

*For any* connection name and configuration, calling `add_connection()` should invoke the appropriate nmcli command (add or modify) based on whether the connection exists.

**Validates: Requirements 11.1**

### Property 27: NetworkManager Connection Idempotency

*For any* connection with matching name and settings, calling `add_connection()` should return `SKIPPED` without executing commands.

**Validates: Requirements 11.2**

### Property 28: NetworkManager Connection Activation

*For any* successful connection add/modify operation, the system should invoke `nmcli connection up <name>` to activate the connection.

**Validates: Requirements 11.3**

### Property 29: NetworkManager Connection Deletion

*For any* connection name, calling `remove_connection()` should construct the command `nmcli connection delete <name>` and pass it to the command runner.

**Validates: Requirements 12.1**

### Property 30: NetworkManager Connection Deletion Idempotency

*For any* non-existent connection name, calling `remove_connection()` should return `SKIPPED` without executing commands.

**Validates: Requirements 12.2**

### Property 31: NetworkManager Connection Query Command Construction

*For any* call to `query_connections()`, the system should construct the command `nmcli -t -f NAME,TYPE,DEVICE,STATE connection show` and pass it to the command runner.

**Validates: Requirements 13.1**

### Property 32: NetworkManager Connection Query Result Parsing

*For any* successful nmcli connection show output, `query_connections()` should return a list of `NmConnectionInfo` records with correctly parsed name, type, device, and state.

**Validates: Requirements 13.2**

### Property 33: Dry-Run Safety

*For any* write operation (set_hostname, add_hosts_entry, remove_hosts_entry, set_nameservers, set_search_domains, add_ntp_server, remove_ntp_server, add_connection, remove_connection) with `dry_run=True`, the command runner should never be invoked for write commands, and the result status should be `DRY_RUN`.

**Validates: Requirements 1.5, 3.6, 4.5, 6.5, 7.5, 8.5, 9.5, 11.5, 12.5**

### Property 34: Elevation for Write Operations

*For any* write operation with `elevated=True` in the config, the command runner should be invoked with `elevated=True` for all network configuration write commands.

**Validates: Requirements 14.1**

### Property 35: No Elevation for Read Operations

*For any* read operation (query_hostname, query_hosts, query_ntp_sync, query_connections), the command runner should never be invoked with `elevated=True` regardless of the elevation configuration.

**Validates: Requirements 14.2**

---

## Module Layout

```
source/packages/ccai/sysman/linux/redhat/
├── __init__.py
└── networkconfiguration.py       ← NetworkManager class, config, models, exceptions

source/testroots/testbase/
└── ccai/tests/sysman/linux/redhat/
    └── test_networkconfiguration.py
```

---

---

## Usage Examples

### Local Execution (Default)

```python
from ccai.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from ccai.sysman.linux.redhat.networkconfiguration import NetworkManager, NetworkManagerConfig

# Create local runner
runner = LocalCommandRunner(LocalRunnerConfig())

# Inject into network manager
config = NetworkManagerConfig(elevated=True, dry_run=False)
net_mgr = NetworkManager(runner, config)

# Set system hostname
result = net_mgr.set_hostname("web-server-01.example.com")
print(f"Hostname set: {result.status}")

# Add hosts entries
net_mgr.add_hosts_entry("192.168.1.10", ["database.local", "db"])
net_mgr.add_hosts_entry("192.168.1.20", ["cache.local"])

# Configure DNS
net_mgr.set_nameservers(["8.8.8.8", "8.8.4.4"])
net_mgr.set_search_domains(["example.com", "local"])

# Configure NTP
net_mgr.add_ntp_server("pool.ntp.org")
net_mgr.add_ntp_server("time.google.com")

# Query network status
hostname_info = net_mgr.query_hostname()
print(f"Current hostname: {hostname_info.static}")

hosts_entries = net_mgr.query_hosts()
print(f"Hosts entries: {len(hosts_entries)}")

ntp_status = net_mgr.query_ntp_sync()
print(f"NTP sync: {ntp_status.synchronized}")
```

### SSH Execution with Reliability Tracking

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.networkconfiguration import NetworkManager, NetworkManagerConfig

# Create SSH runner for remote system
ssh_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0
)
runner = SSHCommandRunner(ssh_config)

# Inject into network manager (same code as local!)
config = NetworkManagerConfig(elevated=True)
net_mgr = NetworkManager(runner, config)

# Configure remote network settings over SSH
net_mgr.set_hostname("remote-server-01")
net_mgr.add_hosts_entry("10.0.0.1", ["gateway.internal"])
net_mgr.set_nameservers(["10.0.0.53", "10.0.0.54"])

# Check SSH reliability metrics
metrics = runner.get_metrics()
print(f"Success rate: {metrics.success_count / (metrics.success_count + metrics.failure_count):.2%}")
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` keyed on command args prefix.
- `hostnamectl`, `cat /etc/hosts`, `cat /etc/resolv.conf`, `cat /etc/chrony.conf`,
  `nmcli show`, and `chronyc tracking` mock responses drive all idempotency and query logic.
- `hosts_path`, `resolv_conf_path`, and `chrony_conf_path` are overridden in config for
  all file-operation tests.
- File mutation logic (hosts, resolv.conf, chrony.conf) is tested by supplying mock `cat`
  output and verifying the `tee` stdin content.
- `chronyc tracking` parsing tests use representative multi-line output strings.
- `nmcli -t` parsing tests verify correct field splitting on colons.
- No test requires root access or a live network stack.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in Python. Each correctness property defined above will be implemented as a property-based test that:

- Generates random but valid inputs (hostnames, IP addresses, domain names, NTP servers, connection configs)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific correctness property they validate, using the format: `# Feature: network-configuration, Property N: <property description>`

**Property Test Coverage:**

- Properties 1-4: Hostname management tests generate random hostnames and verify command construction/parsing
- Properties 5-11: Hosts file management tests generate random IP/hostname combinations and verify file operations
- Properties 12-15: DNS configuration tests generate random nameserver/domain lists and verify resolv.conf operations
- Properties 16-25: NTP configuration tests generate random server addresses and verify chrony.conf operations plus service restarts
- Properties 26-32: NetworkManager tests generate random connection configurations and verify nmcli operations
- Properties 33-35: Dry-run safety and elevation tests verify correct flag usage across all operations

Both unit tests and property-based tests are complementary: unit tests verify specific examples and edge cases, while property tests verify universal correctness across many inputs.
