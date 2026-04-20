# Design

## Overview

The DNS server management feature is implemented as a single
module,
`automojo.sysman.linux.redhat.dnsservermanagement`, exposing a
`DnsServerManager` class that wraps `named-checkconf` /
`named-checkzone` / `rndc`, `unbound-checkconf` /
`unbound-control`, and `dnsmasq --test` through the
`ICommandRunner` abstraction.  It manages the content of
BIND / Unbound / dnsmasq config drop-ins and (for BIND) zone-data
files.

Follows the standard manager pattern; delegates service lifecycle
to an injected `ServiceManager`.

**See Also:**
- `3-network-configuration` — owns DNS *client* resolution.
- `3-service-management` — named / unbound / dnsmasq service
  lifecycle.
- `3-firewall-management` — opening UDP/TCP 53 through firewalld.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│            Maintenance Coordinator                          │
│  - Declares DNS server posture (backend, zones, forwarders) │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            DnsServerManager                                 │
│                                                             │
│  Backend dispatch:                                          │
│    BIND | UNBOUND | DNSMASQ                                 │
│  BIND:       write_zone_file(), define_zone(),              │
│              remove_zone(), query_zone()                    │
│  Unbound:    add_forward_zone(), add_stub_zone(),           │
│              remove_forward_zone(), remove_stub_zone()      │
│  dnsmasq:    set_static_host(), set_forwarder_domain(),     │
│              remove_static_host(),                          │
│              remove_forwarder_domain()                      │
│  Common:     validate(), reload(), restart(), status()      │
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
class DnsServerBackend(Enum):
    BIND    = "bind"
    UNBOUND = "unbound"
    DNSMASQ = "dnsmasq"


@dataclass
class DnsServerManagerConfig:
    dry_run: bool = False
    elevated: bool = True
    backend: DnsServerBackend | None = None   # auto-detect when None

    # BIND
    bind_zones_dir: str = "/var/named"
    bind_conf_dropin_dir: str = "/etc/named/zones.d"

    # Unbound
    unbound_dropin_dir: str = "/etc/unbound/conf.d"
    unbound_dropin_basename: str = "automojo.conf"

    # dnsmasq
    dnsmasq_dropin_dir: str = "/etc/dnsmasq.d"
    dnsmasq_dropin_basename: str = "automojo.conf"
```

---

## Data Models

```python
class DnsActionStatus(Enum):
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class DnsActionResult:
    status: DnsActionStatus
    detail: str | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    command: list[str] | None = None


@dataclass
class ZoneEntry:
    name: str                       # 'example.com'
    type: str                       # 'master' | 'slave' | 'forward' | 'stub' | ...
    file_path: str
    file_content: str | None = None
    source_conf: str | None = None


@dataclass
class DnsValidateResult:
    backend: DnsServerBackend
    ok: bool
    exit_code: int
    stderr: str
    zone_check_results: list[tuple[str, bool, str]]   # (zone, ok, stderr)


@dataclass
class DnsServerStatus:
    running: bool
    version: str | None
    raw: str
```

---

## Exceptions

```python
class DnsBackendNotAvailableError(SemanticError):
    ...


class DnsBackendMismatchError(SemanticError):
    """Raised when a BIND-only method is called on Unbound / dnsmasq."""
```

---

## Key Method Designs

### `__init__(runner, config=None, service_manager=None)`

1. Store runner + config + service_manager.
2. If `config.backend is None`:
   - Probe `named`, `unbound`, `dnsmasq` via `command -v` through
     the runner.
   - Set `config.backend` to the first found; raise
     `DnsBackendNotAvailableError` when none are found (only on
     first operation — defer the error so `__init__` itself
     doesn't fail in dry-run).

### `validate()`

- BIND: `named-checkconf`.  For each declared zone, also run
  `named-checkzone <name> <file_path>` and collect results into
  `zone_check_results`.
- Unbound: `unbound-checkconf`.
- dnsmasq: `dnsmasq --test`.

### `define_zone(entry)`  (BIND)

1. Require `config.backend == BIND`; else raise
   `DnsBackendMismatchError`.
2. Read current drop-in at
   `{bind_conf_dropin_dir}/{entry.name}.conf` via `cat`.
3. Render the canonical `zone "<name>" { type <type>; file
   "<file_path>"; };` block.
4. If content matches → SKIPPED.
5. Dry-run: DRY_RUN.
6. Write via `tee`; call `reload()` at the end.

### `add_forward_zone(name, forwarders)`  (Unbound)

1. Require `config.backend == UNBOUND`.
2. Read current drop-in at
   `{unbound_dropin_dir}/{unbound_dropin_basename}` via `cat`.
3. Parse and upsert a `forward-zone:` block keyed by `name:`.
4. If the requested block is already identical → SKIPPED.
5. Rewrite via `tee`.
6. Call `reload()`.

### `set_static_host(host, ip)`  (dnsmasq)

1. Require `config.backend == DNSMASQ`.
2. Upsert `host-record=<host>,<ip>` in
   `{dnsmasq_dropin_dir}/{dnsmasq_dropin_basename}`.
3. Idempotent; reload delegation via `service_manager`.

### `reload()`

1. `validate()` first; FAILED if not OK.
2. BIND: `rndc reload`.
3. Unbound: `unbound-control reload`.
4. dnsmasq: `service_manager.restart("dnsmasq")`.

---

## Dry-Run Behaviour

Dry-run disables every mutating entry point.  Read-only queries
(`query_zone`, `validate`, `status`) run freely.

---

## Correctness Properties

1. **Backend discipline.** Backend-specific methods raise
   `DnsBackendMismatchError` when called against the wrong
   backend — no silent no-ops.
2. **Validate-before-reload.** `reload()` never dispatches the
   reload when validation fails.
3. **Idempotent config.** Every add / set / remove method SKIPs
   when the current drop-in content already matches the request.
4. **rm after strip.** `remove_zone` removes BOTH the drop-in
   block AND the zone-data file.

---

## Testing Strategy

- `_MockRunner` + `_MockServiceManager`.
- Tests for auto-detect backend (first found wins; raises when
  none available).
- Parser tests for `named-checkconf`, `unbound-checkconf`,
  `dnsmasq --test`, `rndc status`, `unbound-control status`.
- Tests for idempotent add / remove on each backend.
- Tests for `reload()` validate-then-reload chain.
- Tests for `DnsBackendMismatchError` when calling BIND-only
  methods under Unbound / dnsmasq.
- Dry-run and failure-mapping sweeps.

---

## Seams With Other Features

| Seam | Owner | This Feature |
|---|---|---|
| DNS *client* resolution | `3-network-configuration` | Orthogonal — separate concerns |
| Service lifecycle | `3-service-management` | `restart()` and dnsmasq reloads delegate |
| Port 53 open through firewalld | `3-firewall-management` | Out of scope here |
