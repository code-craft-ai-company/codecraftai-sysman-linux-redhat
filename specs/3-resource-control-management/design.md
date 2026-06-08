# Design

## Overview

The resource control management feature is implemented as a single
module,
`ccai.sysman.linux.redhat.resourcecontrolmanagement`, exposing
a `ResourceControlManager` class wrapping `systemctl set-property`,
systemd drop-in files under `/etc/systemd/system/<unit>.d/`,
`systemd-run`, `systemd-cgls`, `systemd-cgtop`, and the
`/sys/fs/cgroup/**` pseudo-filesystem (via `cat` through the runner).

Follows the standard `ActionStatus` / `Config` / `ActionResult` /
`_dispatch` pattern.  Delegates `daemon-reload` to an injected
`ServiceManager`.

**See Also:**
- `3-service-management` — canonical systemd lifecycle surface;
  this feature delegates `daemon-reload` rather than re-spelling it.
- `3-container-management` — Podman containers get resource
  controls via their own engine; this feature governs systemd-owned
  units.
- `3-tuned-management` — applies global performance profiles; this
  feature applies per-unit constraints.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│            Maintenance Coordinator                          │
│  - Declares per-unit resource ceilings                      │
│  - Declares slice hierarchy                                 │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            ResourceControlManager                           │
│                                                             │
│  query_properties(), set_properties_transient()             │
│  set_properties_persistent(), remove_property()             │
│  define_slice(), remove_slice(), attach_to_slice()          │
│  run_scoped(),                                              │
│  cgls(), cgtop(), read_cgroup_attr()                        │
└────────────────┬──────────────────────────┬─────────────────┘
                 │                          │
                 ▼                          ▼
      ┌─────────────────────┐   ┌─────────────────────────┐
      │   ICommandRunner    │   │ Injected ServiceManager │
      │                     │   │ (daemon-reload, enable) │
      └─────────────────────┘   └─────────────────────────┘
```

---

## Configuration

```python
@dataclass
class ResourceControlManagerConfig:
    dry_run: bool = False
    elevated: bool = True
    systemd_etc_dir: str = "/etc/systemd/system"
    dropin_basename: str = "10-resource-control.conf"
    cgroup_root: str = "/sys/fs/cgroup"
    default_scope_slice: str = "system.slice"
```

---

## Data Models

```python
class ResourceActionStatus(Enum):
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class ResourceActionResult:
    status: ResourceActionStatus
    detail: str | None = None
    unit: str | None = None
    changed_keys: list[str] = field(default_factory=list)
    skipped_keys: list[str] = field(default_factory=list)
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    command: list[str] | None = None


@dataclass
class CgroupUsage:
    path: str
    tasks: int | None
    cpu_percent: float | None
    memory_bytes: int | None
    input_per_s: int | None
    output_per_s: int | None


@dataclass
class SliceSpec:
    name: str                              # without `.slice` suffix
    description: str | None = None
    before: list[str] = field(default_factory=list)   # e.g. ['slices.target']
    wants: list[str] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)
```

---

## Key Method Designs

### `query_properties(unit, keys=None)`

1. Use `keys` if supplied, else the default set.
2. Dispatch `systemctl show <unit> --property <comma-joined-keys>`.
3. Parse `key=value\n` lines into `dict[str, str]`.
4. Return empty dict when exit is non-zero.

### `set_properties_transient(unit, properties)`

1. Call `query_properties(unit, keys=list(properties))`.
2. Compare each requested `(key, value)` to the current value.
3. If every key matches → `ResourceActionResult(status=SKIPPED,
   skipped_keys=list(properties))`.
4. Dry-run: return DRY_RUN with `detail` describing the intended
   change.
5. Build `["systemctl", "set-property", "--runtime", unit,
   *(f"{k}={v}" for k, v in properties.items())]`.
6. Dispatch.  `changed_keys` = keys whose current value didn't
   match; `skipped_keys` = the rest.

### `set_properties_persistent(unit, properties)`

1. `path = f"{systemd_etc_dir}/{unit}.d/{dropin_basename}"`.
2. Read current content via `_read_file(path)`.
3. Parse existing `[Section]` body into a
   `dict[str, str]` preserving unrelated sections and lines.
4. Upsert each `(key, value)`.  If every key already matches →
   SKIPPED.
5. Dry-run: return DRY_RUN.
6. Write merged content via `tee`, `mkdir -p` the parent directory
   first.
7. Invoke `service_manager.daemon_reload()`; propagate any failure
   through the returned result.

### `define_slice(spec)`

1. Build slice unit-file content from `spec` (`[Unit]` +
   `[Slice]` sections).
2. Write to `<systemd_etc_dir>/<spec.name>.slice` using the
   idempotent write pattern (SKIPPED when content matches).
3. Call `service_manager.daemon_reload()` on change.
4. Optionally call `service_manager.enable(f"{spec.name}.slice")`
   if the caller requested auto-enable.

### `run_scoped(command, slice_=None, properties=None)`

1. `slice_ = slice_ or config.default_scope_slice`.
2. Build args: `["systemd-run", "--scope", "--slice", slice_,
   *(f"--property={k}={v}" for k, v in (properties or {}).items()),
   *command]`.
3. Dispatch; return `ResourceActionResult`.

---

## Dry-Run Behaviour

Dry-run disables:
`set_properties_transient`, `set_properties_persistent`,
`remove_property`, `define_slice`, `remove_slice`,
`attach_to_slice`, and `run_scoped`.

Read-only queries (`query_properties`, `cgls`, `cgtop`,
`read_cgroup_attr`) run freely.

---

## Correctness Properties

1. **Idempotency.** Transient and persistent set-paths return
   SKIPPED when the unit's current property values already match.
2. **Empty drop-in cleanup.** `remove_property` deletes the
   drop-in file when stripping the directive leaves no directives
   behind.
3. **daemon-reload delegation.** The feature never spells
   `systemctl daemon-reload` directly — it routes through the
   injected `ServiceManager`.
4. **No silent partial failure.** Setting multiple properties
   returns a single `ResourceActionResult` with explicit
   `changed_keys` / `skipped_keys` lists; no change is silently
   dropped.

---

## Testing Strategy

- `_MockRunner` + `_MockServiceManager` with a `daemon_reload`
  capturer.
- Unit tests per public method with happy / SKIPPED / FAILED /
  DRY_RUN branches.
- Parser tests for `systemctl show --property` output and
  `systemd-cgtop --raw`.
- Test that `remove_property` on a drop-in whose only remaining
  content is the section header triggers `rm -f` of the file.
- Test that the feature never issues a raw `systemctl
  daemon-reload` — instead the mock `ServiceManager.daemon_reload`
  is called.
- No live systemd required.

---

## Seams With Other Features

| Seam | Owner | This Feature |
|---|---|---|
| `systemctl daemon-reload` | `3-service-management` | Delegated via injected `ServiceManager` |
| Slice unit enable / disable | `3-service-management` | Delegated when `auto_enable=True` |
| Container resource limits | `3-container-management` | Out of scope — Podman applies cgroups directly to its scope |
| Performance profile (CPU governor, etc.) | `3-tuned-management` | Out of scope — orthogonal tuning surface |
