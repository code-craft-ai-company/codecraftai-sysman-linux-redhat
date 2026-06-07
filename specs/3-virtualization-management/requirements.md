# Requirements

## Introduction

The virtualization management feature provides the
`codecraftai-sysman-linux-redhat` package with the ability to manage
KVM virtual machines on Red Hat Linux systems through libvirt.  It
covers domain (VM), network, and storage-pool/volume lifecycle via
`virsh`, `virt-install`, and `virt-clone`.

All system commands are executed through the `codecraftai-interfaces`
command-runner abstraction.  Reads and writes of XML definitions
and `/etc/libvirt/*` files route through the runner (`cat`/`tee`).
All mutating operations respect dry-run mode.

This feature complements — and does not duplicate — the Podman
container surface owned by `3-container-management` and the
libvirt-network firewall holes owned by `3-firewall-management`.

---

## Requirements

### Requirement 1: Domain Lifecycle

**User Story:** As a maintenance coordinator, I want to query,
start, stop, reboot, and destroy libvirt domains, so that VM
runtime state matches the declared desired state.

#### Acceptance Criteria

1. WHEN the coordinator lists domains THE SYSTEM SHALL invoke
   `virsh list --all --name` and return `list[DomainInfo]`
   enriched with state from `virsh domstate`.
2. WHEN the coordinator queries a single domain THE SYSTEM SHALL
   invoke `virsh dominfo <name>` parsed into `DomainInfo`; `None`
   when not defined.
3. WHEN the coordinator starts a domain THE SYSTEM SHALL invoke
   `virsh start <name>`; SKIPPED when state is already `running`.
4. WHEN the coordinator shuts down a domain THE SYSTEM SHALL invoke
   `virsh shutdown <name>`; SKIPPED when state is `shut off`.
5. WHEN the coordinator forcibly stops a domain THE SYSTEM SHALL
   invoke `virsh destroy <name>`; SKIPPED when state is `shut off`.
6. WHEN the coordinator reboots a domain THE SYSTEM SHALL invoke
   `virsh reboot <name>`.
7. WHEN the coordinator sets autostart THE SYSTEM SHALL invoke
   `virsh autostart [--disable] <name>`; SKIPPED when already in
   requested state.

---

### Requirement 2: Domain Definition

**User Story:** As a maintenance coordinator, I want to define and
undefine libvirt domains from XML, so that VMs persist across
reboots.

#### Acceptance Criteria

1. WHEN the coordinator defines a domain from XML content THE
   SYSTEM SHALL write the XML to a temporary path, invoke `virsh
   define <path>`, and remove the temporary file on completion
   (success or failure).
2. WHEN the coordinator undefines a domain THE SYSTEM SHALL invoke
   `virsh undefine <name> [--remove-all-storage] [--nvram]`;
   SKIPPED when not defined.
3. WHEN the coordinator dumps the current XML THE SYSTEM SHALL
   invoke `virsh dumpxml <name>` and return the raw XML text.

---

### Requirement 3: `virt-install` — Create a New Domain

**User Story:** As a maintenance coordinator, I want to create a
new VM from an installation source, so that provisioning a fresh
guest is a single call.

#### Acceptance Criteria

1. WHEN the coordinator requests a new VM THE SYSTEM SHALL invoke
   `virt-install` with the supplied `VirtInstallSpec` (name, memory,
   vcpus, disk, network, location or cdrom, os_variant,
   graphics, noautoconsole).
2. WHEN a domain with the same name already exists THE SYSTEM SHALL
   return `SKIPPED` without dispatching unless `replace=True`, in
   which case the existing domain SHALL be undefined first.
3. WHEN dry-run is active THE SYSTEM SHALL NOT dispatch
   `virt-install`.

---

### Requirement 4: Virtual Networks

**User Story:** As a maintenance coordinator, I want to define,
start, stop, and autostart libvirt virtual networks.

#### Acceptance Criteria

1. WHEN the coordinator lists networks THE SYSTEM SHALL invoke
   `virsh net-list --all --name` and return `list[NetworkInfo]`.
2. WHEN the coordinator defines a network from XML THE SYSTEM SHALL
   write the XML to a temp file and invoke `virsh net-define
   <path>`; idempotent via XML content compare when the network
   already exists.
3. WHEN the coordinator starts / stops / autostarts a network THE
   SYSTEM SHALL invoke `virsh net-start|net-destroy|net-autostart
   <name>` with SKIPPED semantics.
4. WHEN the coordinator undefines a network THE SYSTEM SHALL invoke
   `virsh net-undefine <name>`; SKIPPED when not defined.

---

### Requirement 5: Storage Pools and Volumes

**User Story:** As a maintenance coordinator, I want to define
storage pools and create/remove volumes so that VMs have backing
storage.

#### Acceptance Criteria

1. WHEN the coordinator lists pools THE SYSTEM SHALL invoke `virsh
   pool-list --all --details` parsed into `list[StoragePoolInfo]`.
2. WHEN the coordinator defines / starts / autostarts a pool THE
   SYSTEM SHALL wrap the corresponding `virsh pool-*` commands
   with SKIPPED semantics.
3. WHEN the coordinator creates a volume THE SYSTEM SHALL invoke
   `virsh vol-create-as <pool> <name> <size> [--format <fmt>]`;
   SKIPPED when a volume with the same name already exists.
4. WHEN the coordinator deletes a volume THE SYSTEM SHALL invoke
   `virsh vol-delete --pool <pool> <name>`; SKIPPED when absent.
5. WHEN the coordinator lists volumes THE SYSTEM SHALL invoke
   `virsh vol-list --pool <pool> --details` parsed into
   `list[StorageVolumeInfo]`.

---

### Requirement 6: Snapshots

**User Story:** As a maintenance coordinator, I want to create,
list, revert to, and delete VM snapshots.

#### Acceptance Criteria

1. WHEN the coordinator creates a snapshot THE SYSTEM SHALL invoke
   `virsh snapshot-create-as <domain> <name> [description]`.
2. WHEN the coordinator lists snapshots THE SYSTEM SHALL invoke
   `virsh snapshot-list <domain> --tree` parsed into
   `list[SnapshotInfo]`.
3. WHEN the coordinator reverts to a snapshot THE SYSTEM SHALL
   invoke `virsh snapshot-revert <domain> <name>`.
4. WHEN the coordinator deletes a snapshot THE SYSTEM SHALL invoke
   `virsh snapshot-delete <domain> <name>`; SKIPPED when absent.

---

### Requirement 7: Dry-Run, Elevation, and Error Reporting

**User Story:** As a maintenance coordinator, I want every mutating
operation to respect dry-run, run elevated, and surface failures
structurally.

#### Acceptance Criteria

1. WHEN `config.dry_run=True` THE SYSTEM SHALL NOT dispatch any
   mutating `virsh` / `virt-install` command and SHALL return
   `VirtActionResult(status=DRY_RUN)`.
2. WHEN `config.elevated=True` THE SYSTEM SHALL dispatch commands
   with `elevated=True`.
3. WHEN the runner returns non-zero THE SYSTEM SHALL return
   `VirtActionResult(status=FAILED, exit_code=<code>,
   stderr=<captured>)`.
4. THE SYSTEM SHALL default `config.uri` to `qemu:///system` and
   include `--connect <uri>` on every `virsh` invocation unless the
   caller overrides it to `""`.
