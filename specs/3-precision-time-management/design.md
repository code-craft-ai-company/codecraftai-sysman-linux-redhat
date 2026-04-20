# Design

## Overview

The precision time management feature is implemented as a single
module,
`automojo.sysman.linux.redhat.precisiontimemanagement`, exposing
a `PrecisionTimeManager` class that wraps `ptp4l`, `phc2sys`,
and `pmc` through the `ICommandRunner` abstraction.  It also
manages the content of `/etc/linuxptp/ptp4l.conf`,
`/etc/sysconfig/ptp4l`, and `/etc/sysconfig/phc2sys`.

Follows the standard manager pattern; delegates service lifecycle
to an injected `ServiceManager`.

**See Also:**
- `3-network-configuration` — parallel Chrony/NTP surface.
- `3-service-management` — `ptp4l.service` / `phc2sys.service`
  lifecycle.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│            Maintenance Coordinator                          │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            PrecisionTimeManager                             │
│                                                             │
│  ptp4l.conf:                                                │
│    get_directives(), set_directive(),                       │
│    remove_directive(), set_interfaces(),                    │
│    set_domain(), set_transport(), set_delay_mechanism()     │
│  sysconfig:                                                 │
│    get_ptp4l_options(), set_ptp4l_options(),                │
│    get_phc2sys_options(), set_phc2sys_options()             │
│  Service lifecycle: (delegated)                             │
│    start_ptp4l(), stop_ptp4l(), enable_ptp4l(),             │
│    start_phc2sys(), stop_phc2sys(), enable_phc2sys()        │
│  pmc queries:                                               │
│    current_data_set(), parent_data_set(),                   │
│    port_data_sets()                                         │
└────────────────┬──────────────────────────┬─────────────────┘
                 │                          │
                 ▼                          ▼
      ┌─────────────────────┐   ┌─────────────────────────┐
      │   ICommandRunner    │   │ Injected ServiceManager │
      └─────────────────────┘   └─────────────────────────┘
```

---

## Configuration

```python
@dataclass
class PrecisionTimeManagerConfig:
    dry_run: bool = False
    elevated: bool = True
    ptp4l_conf: str = "/etc/linuxptp/ptp4l.conf"
    ptp4l_sysconfig: str = "/etc/sysconfig/ptp4l"
    phc2sys_sysconfig: str = "/etc/sysconfig/phc2sys"
```

---

## Data Models

```python
class PtpActionStatus(Enum):
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class PtpActionResult:
    status: PtpActionStatus
    detail: str | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    command: list[str] | None = None


@dataclass
class PtpCurrentDataSet:
    steps_removed: int
    offset_from_master_ns: int
    mean_path_delay_ns: int
    raw: str


@dataclass
class PtpParentDataSet:
    parent_port_identity: str
    grandmaster_identity: str
    grandmaster_priority_1: int
    grandmaster_priority_2: int
    raw: str


@dataclass
class PtpPortDataSet:
    port_identity: str
    port_state: str                     # 'MASTER' | 'SLAVE' | 'PASSIVE' | ...
    log_announce_interval: int
    log_sync_interval: int
    delay_mechanism: str                # 'E2E' | 'P2P'
    raw: str
```

---

## Key Method Designs

### `set_directive(section, key, value)`

1. Read `ptp4l.conf`.
2. Parse into ordered section → list[(key, value_str)].
   Note: ptp4l uses whitespace-separated `key value`, not `key =
   value`.
3. Upsert `(key, value)` within `[section]` (create the section
   when absent).
4. If the config already contains the requested directive with the
   matching value → SKIPPED.
5. Dry-run: DRY_RUN.
6. Write via `tee`.

### `set_interfaces(interfaces)`

1. Read current config.
2. Determine existing interface sections (`[eth0]`, `[ens5f0]`,
   etc.).
3. For every interface in `interfaces` not already present → add
   section.
4. For every existing interface section not in `interfaces` →
   strip it.
5. Preserve `[global]` and caller-supplied extra directives.
6. Rewrite via `tee` when content changed; SKIPPED otherwise.

### `current_data_set()`

1. Dispatch `["pmc", "-u", "-b", "0", "GET CURRENT_DATA_SET"]`.
2. Parse output lines for `stepsRemoved`, `offsetFromMaster`,
   `meanPathDelay`.
3. If exit non-zero OR output empty → return
   `PtpCurrentDataSet(steps_removed=-1,
   offset_from_master_ns=0, mean_path_delay_ns=0,
   raw=<stderr>)` with `result.detail = "ptp4l not running"`.

### `set_ptp4l_options(value)`  (and `set_phc2sys_options`)

1. Read the sysconfig file.
2. Upsert `OPTIONS="<value>"` (with the existing quoting style
   preserved when present).
3. SKIPPED when unchanged.
4. Set `result.detail = "<service>.service restart required"`.

---

## Dry-Run Behaviour

Dry-run disables every mutating entry point.  Read-only queries
(`get_*`, `pmc` queries) run freely.

---

## Correctness Properties

1. **ptp4l config syntax discipline.** Unlike INI, ptp4l uses
   `key value` (whitespace-separated).  Renderer + parser MUST
   use that format; do NOT accidentally emit `key = value`.
2. **Interface-section sync.** `set_interfaces` drops stale
   interface sections the caller did not include, preventing
   surprise retention of old config.
3. **Idempotent writes.** All `set_*` paths SKIP when the live
   content already matches.
4. **Service delegation.** This feature never issues `systemctl`
   directly.
5. **`pmc` never raises.** Query methods degrade to empty/stub
   records with `detail` explaining the state.

---

## Testing Strategy

- `_MockRunner` + `_MockServiceManager`.
- Parser tests for `ptp4l.conf` with mixed `[global]` +
  interface sections and trailing comments.
- Tests for `set_directive` / `remove_directive` idempotency and
  preservation of unrelated sections.
- Tests for `set_interfaces` adding / removing interface sections
  based on the requested list.
- Tests for `pmc` parsers across `CURRENT_DATA_SET`,
  `PARENT_DATA_SET`, `PORT_DATA_SET` outputs.
- Tests that `pmc` query methods don't raise when the daemon is
  not running.
- Dry-run and failure-mapping sweeps.

---

## Seams With Other Features

| Seam | Owner | This Feature |
|---|---|---|
| Chrony / NTP client | `3-network-configuration` | Parallel, not competing; operators may run both |
| ptp4l / phc2sys service lifecycle | `3-service-management` | Delegated |
| phc2sys depends on ptp4l | This feature | Enforced in `start_phc2sys`: refuses without ptp4l posture |
