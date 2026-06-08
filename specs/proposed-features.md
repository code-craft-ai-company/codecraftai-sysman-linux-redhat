# Proposed Features

Concrete proposals for new feature specs that fill real gaps in the
`codecraftai-sysman-linux-redhat` package at the layers this package owns
(Layers 1 / 2 / 3 of the architecture model).

**Out of scope for this package — not listed here:**

- Layer 4 (Application Service) — coordinator / `MaintenanceSession` /
  declarative "apply this posture" engines belong in a calling package
  such as `codecraftai-provisioning`.
- Layer 6 (Presentation) — CLI / REST API / TUI belong in a separate
  presentation package that consumes this library.
- Layer 5 (Cross-Cutting / presentation-facing) — i18n, theming, UX
  flags don't apply to a library.
- Example coordinator code, cross-OS porting docs, runtime-harness
  scripts — belong in sibling repos.

Each proposal below names a folder, summarises scope, lists the shell
commands / config files involved, flags the seams with existing
features, and assigns a priority tier.

---

## Part A — New Layer 3 Feature Specs (Missing RHEL Admin Domains)

### A.1 Priority: High (enterprise-critical)

---

#### `3-authentication-management`

Local authentication stack: PAM module graph, password-quality policy,
faillock, login restrictions.

- **Tools:** `authselect`, direct writes to `/etc/pam.d/*`,
  `/etc/security/{faillock.conf,pwquality.conf,limits.conf,limits.d/*}`
- **Operations:** query active authselect profile; switch profile;
  add/remove PAM module lines in a named service stack; set
  password-quality policy; configure faillock thresholds; manage
  `limits.d/` drop-ins.
- **Complements:** `3-user-group-management` (accounts),
  `3-ssh-server-configuration` (PAM-over-SSH), `3-security-compliance`
  (PAM hardening checks).

---

#### `3-identity-management`

Enterprise directory integration: SSSD, realmd, Kerberos, LDAP
clients.

- **Tools:** `realm`, `adcli`, `sssctl`, `kinit`, `klist`, `kdestroy`,
  `sss_cache`; config files `/etc/sssd/sssd.conf`,
  `/etc/sssd/conf.d/*.conf`, `/etc/krb5.conf`, `/etc/krb5.conf.d/*`,
  `/etc/nsswitch.conf`.
- **Operations:** join / leave AD or IPA domain; write SSSD drop-ins
  for domain definitions; Kerberos ticket lifecycle; NSS source
  ordering; cache invalidation.
- **Complements:** `3-authentication-management` (PAM side),
  `3-certificate-trust-management` (enterprise-CA anchors required
  for LDAPS).

---

#### `3-encryption-management` (LUKS / dm-crypt)

Full-disk and volume-level encryption.

- **Tools:** `cryptsetup`, `systemd-cryptenroll`, `clevis`;
  `/etc/crypttab`.
- **Operations:** LUKS header query; initial format; key-slot add /
  remove / rotate; TPM2 / Clevis enrolment; `/etc/crypttab` entry
  management; runtime unlock / close.
- **Complements:** `3-storage-management` (LVM on top of LUKS
  often);  `3-filesystem-management` (`/etc/fstab` references the
  mapped device).

---

### A.2 Priority: Medium (common enterprise use)

---

#### `3-virtualization-management`

KVM / libvirt domain, network, and storage-pool management.

- **Tools:** `virsh`, `virt-install`, `virt-clone`; config files
  under `/etc/libvirt/`.
- **Operations:** list/start/stop/pause domains; define / undefine
  domains from XML; manage storage pools and volumes; manage virtual
  networks; snapshot lifecycle.
- **Complements:** `3-container-management` (Podman), no direct
  dependency but conceptually parallel.

---

#### `3-resource-control-management`

Systemd-based cgroup / resource limits for services and slices,
including `systemd-run` scopes.

- **Tools:** `systemctl set-property`, `systemd-run`,
  `systemd-cgtop`, `systemd-cgls`; config files under
  `/etc/systemd/system.control/`, slice drop-ins under
  `/etc/systemd/system/<slice>.d/`.
- **Operations:** set transient / persistent `CPUQuota`, `MemoryMax`,
  `IOWeight`, `TasksMax`; create a `systemd-run --slice=...`
  transient scope; query cgroup usage.
- **Complements:** `3-service-management` (lifecycle),
  `3-container-management` (Podman respects cgroups),
  `3-tuned-management` (complementary tuning surfaces).

---

#### `3-filesystem-quotas`

Per-user and per-group disk quotas, project quotas for XFS.

- **Tools:** `quota`, `repquota`, `edquota`, `setquota`,
  `quotaon`/`quotaoff`, XFS `xfs_quota` for project quotas.
- **Operations:** enable quotas on a mount; set soft/hard block and
  inode limits per user/group/project; report current usage; grace
  period configuration.
- **Complements:** `3-filesystem-management` (mounts must have
  `usrquota`/`grpquota` options).

---

#### `3-acl-management`

POSIX ACLs and extended attributes on files and directories.

- **Tools:** `setfacl`, `getfacl`, `chattr`, `lsattr`, `getcap`,
  `setcap`.
- **Operations:** get / set / remove ACL entries (user, group, mask,
  default); set extended attributes (`immutable`, `append-only`,
  `no-dump`); get / set file capabilities.
- **Complements:** `3-filesystem-management` (mount must support
  ACLs), `3-security-compliance` (ACL audits).

---

#### `3-nfs-server-management`

NFS v3 / v4 server: export configuration and server lifecycle.

- **Tools:** `exportfs`, `nfsstat`, `rpc.nfsd`,
  `systemctl <nfs-server|rpcbind|nfs-mountd>`; config files
  `/etc/exports`, `/etc/exports.d/*.exports`, `/etc/nfs.conf`,
  `/etc/idmapd.conf`.
- **Operations:** add / remove / update export lines; reload exports;
  query active exports and client mounts; manage NFSv4 idmapping.
- **Complements:** `3-service-management` (nfs-server.service),
  `3-firewall-management` (nfs service / rpc-bind ports).

---

#### `3-samba-server-management`

CIFS/SMB file-sharing server and Active Directory integration.

- **Tools:** `smbd`, `nmbd`, `testparm`, `smbpasswd`, `net` (AD /
  domain ops), `wbinfo`; config files `/etc/samba/smb.conf`,
  `/etc/samba/smb.conf.d/*`.
- **Operations:** share CRUD in `smb.conf`; validate config;
  Samba-user password management; join AD via `net ads join`
  (coordinates with `3-identity-management`).

---

#### `3-mail-transport-management`

Postfix local MTA configuration and mail queue operations.

- **Tools:** `postconf`, `postmap`, `postqueue`, `mailq`,
  `newaliases`; config files `/etc/postfix/main.cf`,
  `/etc/postfix/master.cf`, `/etc/aliases`,
  `/etc/postfix/*.db` maps.
- **Operations:** set / get `main.cf` directives; manage `/etc/aliases`
  and rebuild db; inspect and flush the mail queue; relay-host
  configuration.

---

### A.3 Priority: Lower (specialist / less common)

---

#### `3-certificate-issuance`

Complement to `3-certificate-trust-management`: issue certificates
rather than just trust ones.

- **Tools:** `openssl req/ca/x509`, `certtool` (GnuTLS), `certmonger`
  (`getcert`), ACME clients (`certbot`, `acme.sh`); IPA `ipa-getcert`.
- **Operations:** CSR generation; self-signed issuance; certmonger
  enrolment and renewal tracking; ACME issuance workflows; CRL /
  OCSP response handling.
- **Complements:** `3-certificate-trust-management`,
  `3-identity-management` (IPA-issued certificates).

---

#### `3-dns-server-management`

Run a DNS server — bind / unbound / dnsmasq.

- **Tools:** `named-checkconf`, `named-checkzone`, `rndc`;
  `unbound-checkconf`, `unbound-control`; dnsmasq config.
- **Operations:** manage forwarder / authoritative zone files; zone
  reload; query server status; key management for DNSSEC.
- **Complements:** `3-network-configuration` (which handles the DNS
  *client* side).

---

#### `3-remote-log-forwarding`

Ship logs off the host — the counterpart to the existing local
logrotate / journal work in `3-log-management`.

- **Tools:** rsyslog forward rules, `systemd-journal-upload`,
  `journald.conf RemoteForward` / `Storage=volatile`.
- **Operations:** configure TLS-wrapped rsyslog forwarding to a
  remote collector; enable/disable `systemd-journal-upload.service`;
  configure journal remote endpoints.

---

#### `3-precision-time-management`

PTP / IEEE 1588 time synchronisation — parallel to Chrony/NTP
(already handled in `3-network-configuration`).

- **Tools:** `ptp4l`, `phc2sys`, `pmc`; config
  `/etc/linuxptp/*.conf`, `/etc/sysconfig/ptp4l`,
  `/etc/sysconfig/phc2sys`.
- **Operations:** configure PTP interfaces and domains; query offset
  and drift; reconcile the PHC with system clock; service lifecycle.

---

#### `3-cluster-management` (pacemaker / corosync / pcs)

High-availability clustering.  Large surface — likely warrants its
own separate package rather than a feature here.  Listed so the
decision is explicit.

- **Tools:** `pcs`, `crm_mon`, `pcsd`; config `/etc/corosync/`,
  `/var/lib/pacemaker/`.
- **Recommendation:** defer, or create a dedicated
  `codecraftai-sysman-linux-redhat-cluster` package.

---

## Part B — Library-Appropriate Cross-Cutting Work

**Status (as of the current PR):** B.1a, B.1b, B.2, B.3, B.4, B.5
are complete.  B.6 is partially addressed by the base-class tests;
per-feature dry-run + failure-mapping spot tests are tracked as
gaps in each feature's `tasks.md`.



These are legitimate library concerns — not new features, but
refactors or infrastructure that would improve the existing 32
features without crossing into higher-layer territory.

### B.1 Shared feature base (Layer 3 refactor) — ✅ landed

Currently every Layer 3 feature defines its own identical
`<Feature>ActionStatus` enum, `<Feature>Config` dataclass (with
`dry_run` + `elevated`), and a `_dispatch` helper implementing the
same dry-run-short-circuit + runner-result mapping.

Proposed module: `source/packages/ccai/sysman/linux/redhat/_feature_base.py`

- `ActionStatus` enum (SUCCESS / FAILED / SKIPPED / DRY_RUN) — shared
- `FeatureConfig` dataclass with `dry_run: bool = False` +
  `elevated: bool = False`
- `FeatureActionResult` dataclass carrying common fields
  (`status`, `detail`, `exit_code`, `stdout`, `stderr`, `command`)
- `BaseFeatureManager` providing `__init__(runner, config)`,
  `_dispatch(command, ...)`, and the common RunResult-to-result
  mapping

Deletes hundreds of lines of duplication; lets reporting code
consume a single `ActionResult` shape.

---

### B.2 Auto-wired maintenance reporting — ✅ landed

`2-maintenance-reporting` defines `ActionRecord` but features do not
emit records.  Callers currently have to translate every
`<Feature>ActionResult` into an `ActionRecord` by hand.

Proposed: optional `report: MaintenanceReport | None = None` on
`BaseFeatureManager`.  When present, every `_dispatch` appends an
`ActionRecord` using the feature name, the operation method name, and
the outcome.  Backward-compatible (defaults to `None`).

---

### B.3 Feature registry (`FeatureRegistry`) — ✅ landed

A small registry module keyed by feature name (`"package-management"`)
that maps to the manager class.  Enables programmatic enumeration and
makes it easier to keep `specs/feature-list.md` and the code in sync.
Also supports tooling like "run this JSON maintenance plan" built by
a caller.

Single-file, no external dependencies, stays within library scope.

---

### B.4 `LocalCommandRunner` reliability metrics — ✅ landed

Parity with `SSHCommandRunner`.  `LocalCommandRunner.get_metrics()`
currently returns `None`.  Lift the metrics bookkeeping into the
runner base (or a mixin) so local runs also track success/failure
counts and durations.  Useful for reports even when SSH is not in
play.

---

### B.5 `datetime.utcnow()` migration — ✅ landed

Sweep the package, replace every `datetime.utcnow()` with
`datetime.now(timezone.utc)`.  Known offenders:
`2-maintenance-reporting` (ActionRecord default timestamp, report
timestamps) and `3-configuration-management` (backup suffix
generator).  Probably others.

---

### B.6 Dry-run and failure-mapping test sweep — 🟡 partial

Base-class tests in `test_featurebase.py` exercise the dry-run
short-circuit and the failure-mapping (non-zero exit →
`FAILED`) branches once for every feature that uses the base
`_dispatch`.  For features that still own their own dispatch
logic (scheduled-tasks, environment-configuration,
filesystem-management, repository-management,
user-group-management, configuration-management,
package-management, security-compliance), per-feature dry-run
and failure tests remain as `[ ]` entries in each feature's
`tasks.md`.

Add the two missing tests per mutating method across all 32
features:

1. **Dry-run short-circuit test** — with `dry_run=True`, assert the
   mock runner was NOT called and the result status is `DRY_RUN`.
2. **Failure-mapping test** — queue a runner result with non-zero
   exit; assert `status == FAILED`, `exit_code` and `stderr`
   preserved.

Mechanical but high-value; closes a pervasive coverage gap
identified in every `tasks.md`.

---

## Part C — Integration Seams to Wire ✅ COMPLETE

Not new features — existing features that should compose.  All ten
seams landed; see commits `4d3ecd7`, `abf6efb`, `19100ef`, `3bda624`.

| Consumer | Delegates to | Outcome | Status |
|---|---|---|---|
| `3-kdump-management` | `3-boot-management` for `crashkernel=` | Wired via `BootManager.add_kernel_param` / `remove_kernel_param` | ✅ |
| `3-cockpit-management` | `3-firewall-management` for cockpit service / port | Now calls `FirewallManager.add_service("cockpit", zone)`; no direct `firewall-cmd` | ✅ |
| `3-scheduled-tasks` | `3-service-management` for `.timer` enable/disable/daemon-reload | `add_systemd_timer` / `remove_systemd_timer` delegate via `ServiceManager`; SKIPPED semantics inherited | ✅ |
| `3-ssh-server-configuration` | `3-service-management` for `sshd` reload | `reload_config` now delegates `ServiceManager.reload("sshd")` | ✅ |
| `3-tuned-management` | `3-service-management` for `tuned.service` lifecycle | `TunedManager.enable` / `disable` / `is_active` delegate through `ServiceManager`; SKIPPED semantics inherited | ✅ |
| `3-kdump-management` | `3-service-management` for `kdump.service` lifecycle | `KdumpManager.enable` / `disable` / `query_status` delegate through `ServiceManager` | ✅ |
| `3-security-compliance.run_compliance_suite()` (to build) | `3-selinux-management`, `3-ssh-server-configuration`, `3-firewall-management`, `3-audit-management` | New `run_compliance_suite()` aggregator returns `ComplianceReport` with per-category findings; injectable sibling managers for test composition | ✅ |
| `3-subscription-management` | `codecraftai-credentials` for password handling | `SubscriptionManager.register(credential=...)` routes password through runner `stdin`; `--password` never on argv | ✅ |
| `3-container-management` | `codecraftai-credentials` for registry login | New `ContainerManager.registry_login(registry, credential)` uses `podman login --password-stdin`; `registry_logout()` added for symmetry | ✅ |
| `3-user-group-management` | `codecraftai-credentials` for password setting | New `UserManager.set_password(username, credential)` uses `chpasswd` with `<user>:<pw>` on stdin | ✅ |

---

## Summary Tallies

- **New Layer 3 feature specs proposed:** 13 (3 high-priority, 7
  medium, 3 lower)
- **Library-appropriate refactors:** 6 (shared base, reporting
  auto-wire, registry, local metrics, datetime sweep, test sweep)
- **Integration seams to wire:** 10 (no new features — consolidation
  inside existing ones)

After these, the package would cover ~45 features across Layers 1–3.
Layers 4–6 remain intentionally empty and belong elsewhere.
