# Feature List

This document enumerates the feature specifications for the
`automojo-sysman-linux-redhat` package, organised according to the six-layer
architecture model documented in
[../usersguide/architecture-layers.md](../usersguide/architecture-layers.md).

The package is a library of primitives consumed by an external maintenance
coordinator.  It has no public-facing service API, no presentation layer, and
no presentation-bound cross-cuts, so only **Layers 1, 2, and 3** are populated.
Layers 4 (Application Service), 5 (Cross-Cutting Concerns), and 6
(Presentation) are not used.

Each feature directory under `specs/` contains three documents:

- `requirements.md` — user-story and acceptance-criteria requirements
- `design.md` — design and architecture for the feature
- `tasks.md` — implementation task breakdown (checked items are implemented)

## Feature Name Convention

Feature folders are prefixed with their layer number:
`{layer-number}-{feature-name}`.  This groups related features together in
directory listings and signals the layering at a glance.

---

## Layer 1 — Infrastructure and Platform Layer

Platform-level primitives that isolate the package from the host environment
and provide the reusable mechanisms every higher layer consumes.

### 1-command-execution

Executes arbitrary shell commands on a Red Hat Linux system.  Supports
one-shot command execution, ordered sequences of commands with configurable
failure handling, and interactive shell-script execution via a temporary
script file.  All commands are dispatched through the command-runner
abstraction so dry-run, auditing, and test mocking apply uniformly.

- [requirements.md](1-command-execution/requirements.md)
- [design.md](1-command-execution/design.md)
- [tasks.md](1-command-execution/tasks.md)

### 1-command-runner-architecture

The foundational abstraction for executing commands on RHEL — both local and
remote (SSH).  Defines `ICommandRunner`, pluggable local and SSH
implementations, measured reliability tracking for SSH operations, and
execution aspects for cross-cutting concerns (persistence, retry, timeout).

- [requirements.md](1-command-runner-architecture/requirements.md)
- [design.md](1-command-runner-architecture/design.md)
- [tasks.md](1-command-runner-architecture/tasks.md)

---

## Layer 2 — Data Layer

Data models, value objects, and serialization primitives.  No command
execution; no direct filesystem access.

### 2-maintenance-reporting

Data structures and serialisation primitives for recording, querying, and
emitting maintenance-run outcomes.  A coordinator builds a report
incrementally and finalises it at the end of the run.

- [requirements.md](2-maintenance-reporting/requirements.md)
- [design.md](2-maintenance-reporting/design.md)
- [tasks.md](2-maintenance-reporting/tasks.md)

---

## Layer 3 — Business Logic Layer

Domain-specific operations on RHEL subsystems.  Each feature encodes the
rules, invariants, and idempotent actions for a single subsystem, built on
Layer 1 and Layer 2 primitives.

### 3-acl-management

Manages POSIX ACLs (`setfacl`/`getfacl`), ext*-family extended
file attributes (`chattr`/`lsattr`), and file capabilities
(`setcap`/`getcap`).  Directory-only guards for default ACLs and
recursive `chattr`; canonical-form comparisons drive SKIPPED
semantics.

- [requirements.md](3-acl-management/requirements.md)
- [design.md](3-acl-management/design.md)
- [tasks.md](3-acl-management/tasks.md)

### 3-audit-management

Manages the Linux Audit subsystem (`auditd`): the daemon lifecycle, the
active and persistent rule sets, the daemon configuration under
`/etc/audit/`, and audit-log search and reporting tools (`auditctl`,
`augenrules`, `ausearch`, `aureport`).

- [requirements.md](3-audit-management/requirements.md)
- [design.md](3-audit-management/design.md)
- [tasks.md](3-audit-management/tasks.md)

### 3-authentication-management

Manages the local authentication stack: `authselect` profiles and
feature flags, direct PAM drop-ins under `/etc/pam.d/`,
password-quality policy (`pam_pwquality`), failed-login lockout
(`pam_faillock` + `faillock`), login resource limits
(`/etc/security/limits.d/`), and NSS source ordering
(`/etc/nsswitch.conf`).

- [requirements.md](3-authentication-management/requirements.md)
- [design.md](3-authentication-management/design.md)
- [tasks.md](3-authentication-management/tasks.md)

### 3-backup-restore

Produces, verifies, restores, and prunes file-tree archives of directories
and configuration trees.  Covers `tar`/`rsync` archive creation, manifest
and checksum generation, restore to same or alternate locations, retention
policy application, and archive content queries.

- [requirements.md](3-backup-restore/requirements.md)
- [design.md](3-backup-restore/design.md)
- [tasks.md](3-backup-restore/tasks.md)

### 3-boot-management

Manages boot-time behaviour: default systemd boot target, installed kernels
known to the bootloader, default kernel, kernel command-line arguments, and
GRUB2 configuration (`grubby`, `grub2-mkconfig`, `grub2-editenv`, `bootctl`).

- [requirements.md](3-boot-management/requirements.md)
- [design.md](3-boot-management/design.md)
- [tasks.md](3-boot-management/tasks.md)

### 3-certificate-issuance

Issues X.509 certificates — complement to the trust-store surface
in `3-certificate-trust-management`.  OpenSSL key / CSR /
self-signed generation; certmonger enrolment with renewal
tracking; ACME issuance via `certbot` (webroot / standalone /
dns-\<plugin\>).  Passphrase material flows on stdin, never
argv; mode-bit invariants enforced (keys 0600, certs 0644);
temp OpenSSL configs always cleaned up.

- [requirements.md](3-certificate-issuance/requirements.md)
- [design.md](3-certificate-issuance/design.md)
- [tasks.md](3-certificate-issuance/tasks.md)

### 3-certificate-trust-management

Manages the system-wide CA trust store: installing and removing trust
anchors under `/etc/pki/ca-trust/source/anchors/`, refreshing the
consolidated bundle via `update-ca-trust`, verifying certificates, and
inspecting certificate metadata.

- [requirements.md](3-certificate-trust-management/requirements.md)
- [design.md](3-certificate-trust-management/design.md)
- [tasks.md](3-certificate-trust-management/tasks.md)

### 3-cockpit-management

Manages the Cockpit web management console: the `cockpit.socket`
lifecycle, listening port, installed Cockpit module set, and main
configuration file at `/etc/cockpit/cockpit.conf`.

- [requirements.md](3-cockpit-management/requirements.md)
- [design.md](3-cockpit-management/design.md)
- [tasks.md](3-cockpit-management/tasks.md)

### 3-configuration-management

Writes, updates, backs up, restores, diffs, and validates configuration
files in INI/key-value, JSON, YAML, and template-rendered plain-text
formats.

- [requirements.md](3-configuration-management/requirements.md)
- [design.md](3-configuration-management/design.md)
- [tasks.md](3-configuration-management/tasks.md)

### 3-container-management

Manages containers and images using Podman — the default container engine
on RHEL.  Covers image lifecycle, container lifecycle, registry
configuration and authentication, and systemd-unit generation for
containers (Quadlet).

- [requirements.md](3-container-management/requirements.md)
- [design.md](3-container-management/design.md)
- [tasks.md](3-container-management/tasks.md)

### 3-dns-server-management

Manages a local DNS server — auto-selects between BIND (`named`
+ `rndc`), Unbound (`unbound-control`), or dnsmasq backends.
BIND zone drop-ins plus zone-data files; Unbound forward-zone /
stub-zone blocks; dnsmasq `host-record=` / `server=` entries.
`validate()` runs before every `reload()`.

- [requirements.md](3-dns-server-management/requirements.md)
- [design.md](3-dns-server-management/design.md)
- [tasks.md](3-dns-server-management/tasks.md)

### 3-encryption-management

Manages block-level encryption using LUKS (`cryptsetup`): header
query, format, open/close/resize, key-slot lifecycle (add / remove /
rotate), TPM2 and Clevis auto-unlock enrolment, and `/etc/crypttab`
entries.  Passphrase material routes through runner `stdin`, never
argv; honours a last-slot safety rail before removing key slots.

- [requirements.md](3-encryption-management/requirements.md)
- [design.md](3-encryption-management/design.md)
- [tasks.md](3-encryption-management/tasks.md)

### 3-environment-configuration

Manages shell environment variables in `/etc/profile.d/`, `/etc/environment`,
and user-level shell rc files (`.bashrc`, `.bash_profile`).

- [requirements.md](3-environment-configuration/requirements.md)
- [design.md](3-environment-configuration/design.md)
- [tasks.md](3-environment-configuration/tasks.md)

### 3-filesystem-management

Creates and removes directories, manages symbolic links, queries disk
usage, mounts and unmounts filesystems, and manages `/etc/fstab` entries.

- [requirements.md](3-filesystem-management/requirements.md)
- [design.md](3-filesystem-management/design.md)
- [tasks.md](3-filesystem-management/tasks.md)

### 3-filesystem-quotas

Manages ext* and XFS disk quotas — user / group / XFS project
block + inode limits, grace-period configuration, `quotaon`/`off`
toggles, XFS project plumbing via atomic `/etc/projects` +
`/etc/projid` edits.  Fstype dispatch chooses between the
traditional `quota` tooling and `xfs_quota` automatically.

- [requirements.md](3-filesystem-quotas/requirements.md)
- [design.md](3-filesystem-quotas/design.md)
- [tasks.md](3-filesystem-quotas/tasks.md)

### 3-firewall-management

Manages `firewalld`: services, ports, and rich rules within zones;
queries state; reloads to apply permanent changes to the runtime
configuration.

- [requirements.md](3-firewall-management/requirements.md)
- [design.md](3-firewall-management/design.md)
- [tasks.md](3-firewall-management/tasks.md)

### 3-hardware-inventory

Queries and aggregates hardware and low-level system identity information:
DMI/SMBIOS, CPU topology, memory, block devices, PCI/USB buses, network
interfaces, firmware, and OS release.  Read-only — no dry-run distinction.

- [requirements.md](3-hardware-inventory/requirements.md)
- [design.md](3-hardware-inventory/design.md)
- [tasks.md](3-hardware-inventory/tasks.md)

### 3-identity-management

Integrates the system with enterprise directory services: domain
discovery and join via `realm` / `adcli` (Active Directory and
FreeIPA), SSSD configuration under `/etc/sssd/` and its drop-in
directory, Kerberos client configuration (`/etc/krb5.conf` and
`/etc/krb5.conf.d/`), and the Kerberos ticket lifecycle (`kinit`,
`klist`, `kdestroy`).  Passphrase / OTP material flows through
runner `stdin`, never argv; NSS ordering edits delegate to
`3-authentication-management`.

- [requirements.md](3-identity-management/requirements.md)
- [design.md](3-identity-management/design.md)
- [tasks.md](3-identity-management/tasks.md)

### 3-insights-management

Manages the Red Hat Insights client (`insights-client`): registration
state, inventory uploads, check-ins, client configuration, and connection
testing.

- [requirements.md](3-insights-management/requirements.md)
- [design.md](3-insights-management/design.md)
- [tasks.md](3-insights-management/tasks.md)

### 3-kdump-management

Manages the kernel crash-dump capture subsystem: `kdump.service`
lifecycle, `/etc/kdump.conf` configuration, the `crashkernel` kernel
command-line reservation (delegated to `boot-management`), and the kdump
initramfs.

- [requirements.md](3-kdump-management/requirements.md)
- [design.md](3-kdump-management/design.md)
- [tasks.md](3-kdump-management/tasks.md)

### 3-kernel-parameters

Reads and writes `sysctl` kernel parameters, persists them via
`/etc/sysctl.d/`, and loads and persists kernel modules via
`/etc/modules-load.d/`.

- [requirements.md](3-kernel-parameters/requirements.md)
- [design.md](3-kernel-parameters/design.md)
- [tasks.md](3-kernel-parameters/tasks.md)

### 3-localization-management

Manages locale, timezone, keyboard, and real-time-clock settings using
`localectl` and `timedatectl`.  Network time sync (Chrony/NTP) lives in
`network-configuration`.

- [requirements.md](3-localization-management/requirements.md)
- [design.md](3-localization-management/design.md)
- [tasks.md](3-localization-management/tasks.md)

### 3-log-management

Queries the `systemd` journal via `journalctl`, reads and rotates
traditional log files, manages `logrotate` drop-ins, and vacuums old
journal data to reclaim disk space.

- [requirements.md](3-log-management/requirements.md)
- [design.md](3-log-management/design.md)
- [tasks.md](3-log-management/tasks.md)

### 3-mail-transport-management

Wraps a local Postfix MTA: `main.cf` / `master.cf` directive
management via `postconf`; lookup-table map CRUD with automatic
`postmap` after content changes; `/etc/aliases` management with
`newaliases` chaining; and full mail-queue lifecycle (list /
flush / delete / requeue).  Service lifecycle delegated to
`3-service-management`.

- [requirements.md](3-mail-transport-management/requirements.md)
- [design.md](3-mail-transport-management/design.md)
- [tasks.md](3-mail-transport-management/tasks.md)

### 3-network-configuration

Sets the system hostname, manages `/etc/hosts`, configures DNS resolution,
manages NTP/Chrony sources and verifies sync, and manages NetworkManager
connections.

- [requirements.md](3-network-configuration/requirements.md)
- [design.md](3-network-configuration/design.md)
- [tasks.md](3-network-configuration/tasks.md)

### 3-nfs-server-management

Manages a local NFS v3/v4 server.  `/etc/exports.d/` drop-in
CRUD with automatic `exportfs -r` reload; NFSv4 idmap domain via
`/etc/idmapd.conf`; `/etc/nfs.conf` multi-section directive
management; server-stats via `nfsstat -s`.  Service lifecycle
delegated.

- [requirements.md](3-nfs-server-management/requirements.md)
- [design.md](3-nfs-server-management/design.md)
- [tasks.md](3-nfs-server-management/tasks.md)

### 3-package-management

Installs, removes, updates, and queries RPM packages using `dnf` and
`rpm`.

- [requirements.md](3-package-management/requirements.md)
- [design.md](3-package-management/design.md)
- [tasks.md](3-package-management/tasks.md)

### 3-precision-time-management

Manages IEEE 1588 PTP (`ptp4l`, `phc2sys`, `pmc`).  Parallel to
the Chrony/NTP surface in `3-network-configuration`; uses ptp4l's
whitespace-separated `key value` config syntax (not INI `key =
value`).  Preset helpers for domain / transport / delay-mechanism
/ interfaces; `pmc` runtime queries that degrade to stub records
when the daemon is down rather than raising.  `phc2sys` has an
explicit ptp4l-dependency check.

- [requirements.md](3-precision-time-management/requirements.md)
- [design.md](3-precision-time-management/design.md)
- [tasks.md](3-precision-time-management/tasks.md)

### 3-process-management

Enumerates, inspects, signals, and adjusts running processes: listing,
attribute queries, signal delivery, priority and CPU-affinity adjustment,
owner-scoped operations.  Does not manage systemd services — that's
`service-management`.

- [requirements.md](3-process-management/requirements.md)
- [design.md](3-process-management/design.md)
- [tasks.md](3-process-management/tasks.md)

### 3-remote-log-forwarding

Ships logs off-host — counterpart to `3-log-management`'s local
surface.  rsyslog forward-rule drop-ins under `/etc/rsyslog.d/`
with TLS + queue-on-disk support; `systemd-journal-upload`
configuration; journald `ForwardTo*` directives; a non-raising
collector probe for reachability + TLS verification.  Always
`rsyslogd -N1`-validates before delegating a service reload.

- [requirements.md](3-remote-log-forwarding/requirements.md)
- [design.md](3-remote-log-forwarding/design.md)
- [tasks.md](3-remote-log-forwarding/tasks.md)

### 3-repository-management

Adds, removes, enables, disables, and refreshes `dnf`/`yum` software
repositories defined under `/etc/yum.repos.d/`.

- [requirements.md](3-repository-management/requirements.md)
- [design.md](3-repository-management/design.md)
- [tasks.md](3-repository-management/tasks.md)

### 3-resource-control-management

Manages systemd-cgroup resource limits.  Transient via
`systemctl set-property --runtime`; persistent via
`<unit>.d/10-resource-control.conf` drop-in with aggregated
changed/skipped-keys reporting; slice definition +
attach-to-slice; transient scopes via `systemd-run`.  Cgroup
introspection via `systemd-cgls`, `systemd-cgtop`, and
`/sys/fs/cgroup` direct reads.  Delegates `daemon-reload` to
`3-service-management`.

- [requirements.md](3-resource-control-management/requirements.md)
- [design.md](3-resource-control-management/design.md)
- [tasks.md](3-resource-control-management/tasks.md)

### 3-samba-server-management

Wraps a local Samba SMB/CIFS server.  `smb.conf` global + share
CRUD with block-boundary strip; `testparm` validation before
every reload; Samba-user management via `smbpasswd` + `pdbedit`;
AD join via `net ads` with password on stdin.  Passphrase
material never on argv; `SambaActionResult.command` sanitised.
Service lifecycle delegated.

- [requirements.md](3-samba-server-management/requirements.md)
- [design.md](3-samba-server-management/design.md)
- [tasks.md](3-samba-server-management/tasks.md)

### 3-scheduled-tasks

Manages cron jobs (`/etc/cron.d/`) and systemd timers (`.timer` +
`.service` unit pairs) for recurring and one-time scheduled work.

- [requirements.md](3-scheduled-tasks/requirements.md)
- [design.md](3-scheduled-tasks/design.md)
- [tasks.md](3-scheduled-tasks/tasks.md)

### 3-security-compliance

Audits and remediates security-posture concerns: coarse SELinux mode,
file-permission auditing, SSH daemon hardening checks, world-writable and
SUID/SGID discovery; extensible compliance-check runner that produces a
structured `ComplianceReport`.  Complements `selinux-management` and
`ssh-server-configuration`.

- [requirements.md](3-security-compliance/requirements.md)
- [design.md](3-security-compliance/design.md)
- [tasks.md](3-security-compliance/tasks.md)

### 3-selinux-management

Manages the full SELinux surface: file and port contexts, policy booleans,
policy modules, mode persistence, and audit-driven policy generation
(`semanage`, `semodule`, `chcon`, `restorecon`, `audit2allow`).
Complements the coarse SELinux operations in `security-compliance`.

- [requirements.md](3-selinux-management/requirements.md)
- [design.md](3-selinux-management/design.md)
- [tasks.md](3-selinux-management/tasks.md)

### 3-service-management

Starts, stops, restarts, reloads, enables, disables, masks, and queries
`systemd` services; reloads the daemon after unit-file changes.

- [requirements.md](3-service-management/requirements.md)
- [design.md](3-service-management/design.md)
- [tasks.md](3-service-management/tasks.md)

### 3-ssh-server-configuration

Manages the OpenSSH server: the service lifecycle, main
`/etc/ssh/sshd_config`, drop-in files under `/etc/ssh/sshd_config.d/`,
`Match` blocks, host-key files, and configuration validation.
Complements `security-compliance` (hardening checks) and
`user-group-management` (`authorized_keys`).

- [requirements.md](3-ssh-server-configuration/requirements.md)
- [design.md](3-ssh-server-configuration/design.md)
- [tasks.md](3-ssh-server-configuration/tasks.md)

### 3-storage-management

Manages block-level storage: device discovery, partitioning, filesystem
creation, LVM (PVs, VGs, LVs), and software RAID (`mdadm`).  Complements
`filesystem-management` (mounts and `/etc/fstab`).

- [requirements.md](3-storage-management/requirements.md)
- [design.md](3-storage-management/design.md)
- [tasks.md](3-storage-management/tasks.md)

### 3-subscription-management

Registers a RHEL system with Red Hat Subscription Management (RHSM),
attaches entitlements, manages subscription-provided repositories, sets
release version, and queries subscription state.

- [requirements.md](3-subscription-management/requirements.md)
- [design.md](3-subscription-management/design.md)
- [tasks.md](3-subscription-management/tasks.md)

### 3-tuned-management

Manages the `tuned` performance-tuning subsystem: `tuned.service`
lifecycle, active profile, profile listing and verification, and custom
profiles under `/etc/tuned/`.

- [requirements.md](3-tuned-management/requirements.md)
- [design.md](3-tuned-management/design.md)
- [tasks.md](3-tuned-management/tasks.md)

### 3-user-group-management

Creates, modifies, and deletes local users and groups.  Manages
group membership, SSH authorized keys, account lock state, and
password expiry.

- [requirements.md](3-user-group-management/requirements.md)
- [design.md](3-user-group-management/design.md)
- [tasks.md](3-user-group-management/tasks.md)

### 3-virtualization-management

Wraps libvirt / KVM: domain lifecycle, `virt-install`, virtual
networks, storage pools and volumes, and snapshots.  All `virsh`
invocations thread `--connect` through a configurable URI;
`define_domain` uses a temp-XML-file pattern with guaranteed
cleanup.

- [requirements.md](3-virtualization-management/requirements.md)
- [design.md](3-virtualization-management/design.md)
- [tasks.md](3-virtualization-management/tasks.md)
