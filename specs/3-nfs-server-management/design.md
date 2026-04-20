# Design

## Overview

The NFS server management feature is implemented as a single
module, `automojo.sysman.linux.redhat.nfsservermanagement`,
exposing an `NfsServerManager` class that wraps `exportfs`,
`nfsstat`, `showmount`, and manages the content of
`/etc/exports`, `/etc/exports.d/`, `/etc/idmapd.conf`, and
`/etc/nfs.conf` through the `ICommandRunner` abstraction.

Follows the standard manager pattern; delegates service lifecycle
to an injected `ServiceManager`.

**See Also:**
- `3-service-management` — `nfs-server.service` and
  `nfs-idmapd.service` lifecycle.
- `3-firewall-management` — opening the `nfs` / `mountd` /
  `rpc-bind` service ports.
- `3-filesystem-management` — NFS client-side mount points and
  `/etc/fstab`.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│            Maintenance Coordinator                          │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            NfsServerManager                                 │
│                                                             │
│  Exports:                                                   │
│    list_active(), list_configured(),                        │
│    add_export(), update_export(), remove_export()           │
│    reload(), unexport_all()                                 │
│  Idmapping:                                                 │
│    get_idmap_domain(), set_idmap_domain()                   │
│  nfs.conf:                                                  │
│    get_directives(), set_directive(),                       │
│    remove_directive()                                       │
│  Stats:                                                     │
│    server_stats(), showmount()                              │
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
class NfsServerManagerConfig:
    dry_run: bool = False
    elevated: bool = True
    exports_main: str = "/etc/exports"
    exports_dir: str = "/etc/exports.d"
    exports_dropin: str = "automojo.exports"
    idmapd_conf: str = "/etc/idmapd.conf"
    nfs_conf: str = "/etc/nfs.conf"
```

---

## Data Models

```python
class NfsActionStatus(Enum):
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class NfsActionResult:
    status: NfsActionStatus
    detail: str | None = None
    path: str | None = None                 # export path when applicable
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    command: list[str] | None = None


@dataclass
class ClientSpec:
    client: str                             # '*', '192.168.1.0/24', 'host.example'
    options: list[str]                      # ['rw', 'sync', 'no_root_squash', ...]


@dataclass
class ExportEntry:
    path: str
    clients: list[ClientSpec]
    source_file: str | None = None          # '/etc/exports' or a .d drop-in


@dataclass
class NfsServerStats:
    rpc_calls: int
    rpc_badcalls: int
    nfs_v3_op_counts: dict[str, int]
    nfs_v4_op_counts: dict[str, int]
    raw: str
```

---

## Key Method Designs

### `add_export(entry)`

1. Read the drop-in at
   `{exports_dir}/{exports_dropin}` via `cat`.
2. Render `entry` to the canonical export line
   `{entry.path} {client_spec_string}`.
3. If the rendered line is already present → SKIPPED.
4. Dry-run: DRY_RUN with args.
5. Append line; write via `tee`.
6. Dispatch `exportfs -r` to reload; propagate failure.

### `update_export(entry)`

1. Read drop-in, find existing line(s) matching `entry.path`.
2. If the rendered line for `entry` exactly matches → SKIPPED.
3. Replace matching lines with the new rendered line (single
   replacement even if multiple lines matched — collapse to one).
4. Write via `tee`; dispatch `exportfs -r`.

### `remove_export(path)`

1. Read drop-in.
2. Strip every line whose first token equals `path`.
3. If no lines matched → SKIPPED.
4. If remaining content is empty (ignoring comments / blanks) →
   `rm -f` the drop-in; else rewrite via `tee`.
5. Dispatch `exportfs -r`.

### `set_idmap_domain(value)`

1. Read `/etc/idmapd.conf`.
2. Upsert `Domain = <value>` in `[General]`.
3. Match → SKIPPED.
4. Rewrite via `tee`.
5. Set `result.detail = "nfs-idmapd.service restart required for
   change to take effect"`.

---

## Dry-Run Behaviour

Dry-run disables every mutating entry point (`add_export`,
`update_export`, `remove_export`, `reload`, `unexport_all`,
`set_idmap_domain`, `set_directive`, `remove_directive`,
`restart`).  Read-only queries run freely.

---

## Correctness Properties

1. **Idempotent exports.** `add_export` / `update_export` /
   `remove_export` all SKIP when the current drop-in content
   already matches the requested state.
2. **Empty-file cleanup.** `remove_export` deletes the drop-in
   file when the last export line is stripped.
3. **Reload-after-change.** Every mutation that touches
   `/etc/exports.d/*` is followed by `exportfs -r` unless the
   mutation was SKIPPED.
4. **Service delegation.** `restart` and any `-idmapd.service`
   restart go through the injected `ServiceManager`, never via a
   direct `systemctl` call here.

---

## Testing Strategy

- `_MockRunner` + `_MockServiceManager`.
- Parser tests for `exportfs -v`, `showmount -e`, `nfsstat -s`.
- Tests for the idempotent add / update / remove flows including
  the empty-file cleanup.
- Test that the feature never issues `systemctl restart
  nfs-server` directly — always via the mock.
- Parser tests for `/etc/nfs.conf` multi-section INI.
- Dry-run and failure-mapping sweeps.

---

## Seams With Other Features

| Seam | Owner | This Feature |
|---|---|---|
| `nfs-server.service` lifecycle | `3-service-management` | Delegated |
| `nfs-idmapd.service` restart after idmap change | `3-service-management` | Result detail directs caller to restart via `ServiceManager` |
| Opening NFS ports | `3-firewall-management` | Coordinator opens `nfs`/`mountd`/`rpc-bind` services there |
| Client-side NFS mounts | `3-filesystem-management` | Out of scope |
