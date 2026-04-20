# Design

## Overview

The virtualization management feature is implemented as a single
module,
`automojo.sysman.linux.redhat.virtualizationmanagement`, exposing a
`VirtualizationManager` class that wraps `virsh`, `virt-install`,
and `virt-clone` through the `ICommandRunner` abstraction.

Follows the same `ActionStatus` / `Config` / `ActionResult` /
`_dispatch` pattern as other Layer 3 managers in this package.

**See Also:**
- `3-container-management` — parallel container surface.
- `3-firewall-management` — libvirt virtual networks may require
  firewall coordination.
- `3-storage-management` — libvirt storage pools often back onto
  LVM / filesystem resources managed there.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│            Maintenance Coordinator                          │
│  - Declares desired VM / network / pool posture             │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            VirtualizationManager                            │
│  Domains: list/query/start/stop/destroy/reboot/autostart    │
│           define/undefine/dumpxml                           │
│  virt-install wrapper                                       │
│  Networks: list/define/start/stop/autostart/undefine        │
│  Pools:    list/define/start/autostart                      │
│  Volumes:  create/delete/list                               │
│  Snapshots: create/list/revert/delete                       │
└────────────────────────┬────────────────────────────────────┘
                         │ delegates to
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                   ICommandRunner                            │
└─────────────────────────────────────────────────────────────┘
```

All `virsh` invocations include `--connect <uri>` sourced from
`config.uri` (default `qemu:///system`).

---

## Configuration

```python
@dataclass
class VirtualizationManagerConfig:
    dry_run: bool = False
    elevated: bool = True
    uri: str = "qemu:///system"            # empty string suppresses --connect
    virsh_bin: str = "virsh"
    virt_install_bin: str = "virt-install"
```

---

## Data Models

```python
class VirtActionStatus(Enum):
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class VirtActionResult:
    status: VirtActionStatus
    detail: str | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    command: list[str] | None = None


@dataclass
class DomainInfo:
    name: str
    uuid: str | None
    state: str                          # 'running', 'shut off', 'paused', ...
    autostart: bool
    persistent: bool
    max_memory_kib: int | None
    used_memory_kib: int | None
    vcpus: int | None


@dataclass
class VirtInstallSpec:
    name: str
    memory_mib: int
    vcpus: int
    disk: str                           # 'pool=default,size=20,format=qcow2' or 'path=...'
    network: str                        # 'network=default' or 'bridge=br0'
    os_variant: str                     # 'rhel9.0', 'fedora40', ...
    location: str | None = None         # install tree URL or --cdrom target
    cdrom: str | None = None
    graphics: str = "none"
    noautoconsole: bool = True
    extra_args: list[str] | None = None


@dataclass
class NetworkInfo:
    name: str
    uuid: str | None
    active: bool
    persistent: bool
    autostart: bool


@dataclass
class StoragePoolInfo:
    name: str
    uuid: str | None
    state: str
    autostart: bool
    capacity_bytes: int | None
    allocation_bytes: int | None
    available_bytes: int | None


@dataclass
class StorageVolumeInfo:
    name: str
    path: str
    capacity_bytes: int | None
    allocation_bytes: int | None


@dataclass
class SnapshotInfo:
    domain: str
    name: str
    parent: str | None
    creation_time: str | None
    state: str | None                   # libvirt snapshot state
```

---

## Key Method Designs

### `create_vm(spec, replace=False)`

1. Call `query_domain(spec.name)`.
2. If exists and `replace=False` → SKIPPED.
3. If exists and `replace=True` → `undefine_domain(name,
   remove_storage=True)` first, ensuring the previous domain is
   gone before the fresh install.
4. Dry-run: return DRY_RUN with the assembled `virt-install` args.
5. Dispatch `virt-install --name <...> --memory <...> --vcpus
   <...> --disk <...> --network <...> --os-variant <...>
   [--location <...> | --cdrom <...>] --graphics <...>
   --noautoconsole <...> extra_args`.

### `define_domain(xml_content)`

1. Write `xml_content` to a `tempfile.NamedTemporaryFile(suffix=".xml")`.
2. Dispatch `virsh define <path>` through runner.
3. Always unlink the temp file in a `finally` block
   (`Path.unlink(missing_ok=True)`).

### `create_volume(pool, name, size, format_="qcow2")`

1. Call `list_volumes(pool)`.
2. If name already present → SKIPPED.
3. Dispatch `virsh vol-create-as <pool> <name> <size>
   [--format <format_>]`.

---

## Dry-Run Behaviour

Dry-run disables every mutating entry point (create/start/stop/
destroy/undefine/define/snapshot/volume create-or-delete) and
returns `DRY_RUN` with the command that would have been issued.
Read-only queries (`list_*`, `query_*`, `dumpxml`) run freely.

---

## Correctness Properties

1. **Idempotency.** Every lifecycle transition returns SKIPPED when
   the resource is already in the requested state.
2. **Temp XML cleanup.** `define_domain` / `define_network` always
   delete the temporary XML file, even on failure.
3. **URI override.** `config.uri == ""` suppresses `--connect` so
   callers can rely on default libvirt URI resolution.

---

## Testing Strategy

- `_MockRunner` stub queuing `RunResult` objects per command key.
- Unit tests per public method: happy / SKIPPED / FAILED / DRY_RUN.
- Parser tests for `virsh list`, `virsh dominfo`, `virsh net-list`,
  `virsh pool-list --details`, `virsh vol-list --details`.
- Temp-file-cleanup test for `define_domain` — assert the temp file
  path no longer exists after the call.
- No live libvirt required.

---

## Seams With Other Features

| Seam | Owner | This Feature |
|---|---|---|
| libvirt-service lifecycle | `3-service-management` | Callers may start `libvirtd` before invoking this feature |
| Networks that need firewall holes | `3-firewall-management` | Coordinator opens ports explicitly |
| Pools backed by LVM | `3-storage-management` | Coordinator prepares VG/LV, then defines the libvirt pool here |
