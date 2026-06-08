# Feature Gap Analysis

A cross-cutting review of the 32 feature specs and implementations,
identifying gaps at the package level.  Per-feature gaps are captured
inside each feature's own `tasks.md`; this document surfaces gaps that
span features or that indicate missing features entirely.

---

## 1. Missing Feature Areas

RHEL administration domains that are not represented by any feature.
Ordered by likely importance for enterprise use.

### 1.1 Authentication / Identity Integration (high priority)

No feature covers PAM, SSSD, realmd, Kerberos, or LDAP/Active Directory
integration.  This is a major gap for enterprise RHEL deployments.

Proposed feature(s):

- `authentication-management` — PAM stack (`/etc/pam.d/*`), password
  policies (pam_pwquality), account/session/auth modules
- `identity-management` — `realmd` join to AD/IPA, SSSD configuration
  (`/etc/sssd/sssd.conf`), Kerberos client (`/etc/krb5.conf`,
  `kinit`/`klist`), LDAP client configuration

### 1.2 LUKS Disk Encryption (high priority)

`3-storage-management` manages block devices and LVM but nothing
addresses encryption — no `cryptsetup` wrapper, no `/etc/crypttab`
management, no key-slot rotation, no TPM-bound unlocking.  Full-disk
encryption is standard in most RHEL deployments.

Proposed feature: `encryption-management`.

### 1.3 Virtualization (medium priority)

No libvirt / KVM / virsh wrapper.  `3-container-management` covers
Podman but VM workflows are absent.

Proposed feature: `virtualization-management` (libvirt networks,
storage pools, domain lifecycle, virsh).

### 1.4 Resource Control / cgroups (medium priority)

`3-tuned-management` applies performance profiles but there's no
direct `systemd-cgtop` / cgroup slice / `systemd-run` wrapper, nor
`/etc/systemd/system.control/` slice management.  Relevant to
containers and service resource limits.

Proposed feature: `resource-control-management` (or fold into
`3-service-management` as drop-in-file helpers).

### 1.5 File-Sharing Servers (medium priority)

`3-network-configuration` covers the client side of DNS/NTP; no
feature covers running NFS server, Samba/CIFS server, or similar
shared-storage services.

Proposed features: `nfs-server-management`, `samba-server-management`.

### 1.6 Mail / Postfix (medium priority)

No feature covers local mail delivery (postfix/sendmail), mail queue
management (`mailq`, `postqueue`), or mail aliases (`/etc/aliases`).

### 1.7 Filesystem Quotas and ACLs (medium priority)

`3-filesystem-management` handles mounts and directories but:

- No `quota` / `repquota` / `edquota` wrapper for per-user/group disk
  quotas
- No `setfacl` / `getfacl` wrapper for POSIX ACLs
- No `getattr` / `chattr` / `lsattr` wrapper for ext* extended
  attributes (immutable, append-only, etc.)

### 1.8 High Availability (lower priority)

No `corosync` / `pacemaker` / `pcs` cluster management feature.
Relevant for HA RHEL deployments but a large, separable concern.

### 1.9 PTP / Precision Time (lower priority)

`3-network-configuration` covers Chrony/NTP; no feature covers PTP
(`ptp4l`, `phc2sys`) for environments that need sub-microsecond time
sync.

### 1.10 DNS Server (lower priority)

`3-network-configuration` covers DNS *resolution* (the client side).
No feature covers running a DNS server (bind, unbound, dnsmasq).

### 1.11 Remote Log Forwarding (lower priority)

`3-log-management` handles `journalctl`, `logrotate`, and journal
vacuum locally.  No feature configures remote syslog forwarding
(`rsyslog.conf` forward rules, `systemd-journal-upload`,
`journald.conf RemoteForward=`).

### 1.12 Certificate Issuance (lower priority)

`3-certificate-trust-management` manages the CA anchor store (trust
in certificates issued elsewhere).  No feature covers issuing
certificates: `openssl req`/`ca` workflows, ACME/Let's Encrypt
(certbot), or IPA `ipa-getcert`.

---

## 2. Integration Seams That Should Exist But Don't

Places where features could / should compose but instead re-implement
the same underlying shell plumbing.

| Consumer | Should delegate to | Current state |
|---|---|---|
| `3-kdump-management` (for `crashkernel=`) | `3-boot-management` | Spec says "delegate" — wiring absent |
| `3-cockpit-management` (firewall ops) | `3-firewall-management` | Spells `firewall-cmd` directly |
| `3-container-management` (systemd units for Quadlet) | `3-service-management` | Not implemented; potential drift point |
| `3-scheduled-tasks` (systemd timer lifecycle) | `3-service-management` | Spells `systemctl` directly |
| `3-ssh-server-configuration` (sshd reload) | `3-service-management` | Spells `systemctl reload sshd` directly |
| `3-tuned-management` (tuned.service lifecycle) | `3-service-management` | Not implemented; potential drift point |
| `3-kdump-management` (kdump.service lifecycle) | `3-service-management` | Spells `systemctl` directly |
| `3-repository-management` (RHSM-managed repos) | `3-subscription-management` | Overlap undocumented |
| `3-security-compliance` (aggregated posture) | `3-selinux-management`, `3-ssh-server-configuration`, `3-firewall-management`, `3-audit-management` | `run_compliance_suite()` promised but never built |
| `3-security-compliance.set_selinux_mode` | `3-selinux-management` | Coarse vs fine duplication handled but seam undocumented |
| `3-hardware-inventory.list_mounts` | `3-filesystem-management.list_mounts` | Potential duplication if the hardware task is ever built |
| `3-cockpit-management` (port drop-in) | `3-service-management` drop-in helpers | Spec gap in both features |

Impact: SKIPPED semantics are inconsistent across features.
`3-service-management` has the canonical "already in desired state"
logic; features that re-spell `systemctl` miss it.

---

## 3. codecraftai-* Reuse Opportunities

Sibling packages that features in this repo could delegate to rather
than reimplementing locally.

| Sibling package | Current consumer | Opportunity |
|---|---|---|
| `codecraftai-configuration` | Every feature that reads/writes config files (~20 features) | Config parsing (INI/JSON/YAML/plain text) currently duplicated in `3-configuration-management`, `3-environment-configuration`, `3-network-configuration`, etc. — consider a shared parser surface |
| `codecraftai-credentials` | `3-subscription-management` (password on argv!), `3-user-group-management` (SSH keys), `3-container-management` (registry auth) | Route secrets through credential objects, never on argv |
| `codecraftai-errors` | `3-log-management`'s bespoke `LogManagementError`, many features' ad-hoc `*QueryError` classes | Unified error classification + reporting |
| `codecraftai-interop` | `3-hardware-inventory` (PCI/USB/block parsing), `3-process-management` (/proc reads), SSH transport | Already used by `SSHCommandRunner` (`SshAgent`); evaluate for hardware/process helpers |
| `codecraftai-networking` | `3-network-configuration` | Big overlap — hostname, /etc/hosts, DNS, NTP, NetworkManager.  Risk of double implementation with different semantics |
| `codecraftai-storage-database` | `source/packages/.../storage/sqlite.py` | Built-in `SQLiteStorage` for persistence aspect — evaluate whether the sibling package offers a better-tested SQLite surface |
| `codecraftai-uefi` | `3-boot-management` (EFI paths, bootctl) | The `codecraftai-sysman-uefi` companion likely provides EFI-specific helpers |
| `codecraftai-landscaping` / `codecraftai-dataprofiles` | `3-hardware-inventory.collect_snapshot` | Snapshot schemas + landscape descriptions may already exist |
| `codecraftai-provisioning` | Top-level maintenance coordinator (not in this package) | Coordinator workflows likely belong there |

Per the project memory: check before reimplementing.

---

## 4. Cross-Cutting Quality Gaps

Patterns that should be consistent across all features but aren't.

### 4.1 Dry-run test coverage (pervasive)

Every feature implements `dry_run` short-circuits correctly, but
**almost no feature has tests that assert the runner is NOT called
in dry-run mode for every mutating method**.  Currently only
`1-command-execution`, `2-maintenance-reporting`, a couple of the
early features, and `3-package-management` exercise dry-run paths
explicitly.

### 4.2 Failure-mapping tests (pervasive)

Tests for "runner returns non-zero exit → result status FAILED with
`exit_code`/`stderr` preserved" are missing across almost every
feature.  Most tests only cover the happy path and a few SKIPPED
cases.

### 4.3 Per-method test completeness

Many features have 5–10 methods but only 3–5 test functions.  Coverage
is clustered around whichever methods landed first; counterparts
(e.g. `remove_*`, `disable_*`, `unmask_*`) often have no test at all.

### 4.4 `datetime.utcnow()` deprecation (package-wide)

At least `2-maintenance-reporting` and `3-configuration-management`
use the deprecated `datetime.utcnow()`.  A sweep of all modules and
migration to `datetime.now(timezone.utc)` is worth one focused PR.

### 4.5 Reliability metrics are SSH-only

`SSHCommandRunner` tracks `SSHReliabilityMetrics`.  `LocalCommandRunner`
returns `None` from `get_metrics()`.  For production use, local-run
reliability data (success/failure counts, durations) would also be
useful.  Either lift metrics into the base class or document the
intentional difference.

### 4.6 Property-based testing promised but not implemented

The original `1-command-execution` spec promised `hypothesis`-based
property tests for 11 correctness properties.  None are implemented.
My reverse-engineering pass removed the aspirational text, but the
decision to NOT do property-based testing should be explicit at the
package level rather than quietly dropped.

### 4.7 Integration testing

Every test uses a mock runner.  There is no integration layer that
runs against a real RHEL target (container, VM, or SSH to a lab
host).  The runners themselves are covered, but the end-to-end
"coordinator invokes manager invokes runner against live system" path
is not.

---

## 5. Architectural Gaps

### 5.1 Layer 4 (Application Service) is empty

Every feature exposes a bare Manager class.  There is no:

- Top-level coordinator class / `MaintenanceSession` facade
- Declarative "posture" or "desired-state" object that describes a
  full system configuration and a workflow that applies it
- Composable "use-case" layer (e.g. "harden this machine for PCI
  compliance") that orchestrates multiple managers

This is the kind of class that the `codecraftai-provisioning` sibling
package might own.  Worth confirming before building one here.

### 5.2 Layer 6 (Presentation) is empty

The package has no CLI and no REST surface.  It's strictly a library
for a caller to drive programmatically.  That is a valid design
choice — but should be stated explicitly in the package overview.
The caller ergonomics are also clunky (each feature requires an
explicit runner + config injection).

### 5.3 Duplicated boilerplate across features

Every Layer 3 feature defines its own:

- `<Feature>ActionStatus` enum with identical members (SUCCESS /
  FAILED / SKIPPED / DRY_RUN)
- `<Feature>Config` dataclass with identical fields (`dry_run`,
  `elevated`, plus feature-specific ones)
- `<Feature>ActionResult` dataclass with near-identical fields
- `<Feature>Manager.__init__` that accepts runner + config

A shared base would eliminate hundreds of lines of duplication and
let the status/result types be unified for reporting.  Candidates:

- `BaseFeatureConfig` with `dry_run` + `elevated` as defaults
- `BaseActionStatus` enum (features can still subclass if they need
  more statuses)
- `BaseActionResult` / `BaseManager` providing the common `_dispatch`
  pattern that is currently re-implemented in every feature

### 5.4 No feature registry / factory

Callers have to know the exact manager class names to instantiate.
A `FeatureRegistry` with name-based lookup would:

- Allow tests / coordinators to enumerate available features
- Make serialisation of maintenance plans possible (`"feature":
  "package-management", "op": "install", ...`)
- Make it easier to keep `specs/feature-list.md` and the code in sync

### 5.5 No declarative state model

Every feature is imperative: the caller describes *what to do* per
method call.  There is no way to describe a target *state* ("system
X should have nginx installed, firewalld with HTTP allowed, ...") and
ask the package to converge to it.  This is the "desired state vs
current state" gap familiar from Puppet/Ansible/Terraform — and is
tracked by the same `run_compliance_suite()` idea that
`3-security-compliance` gestures at but doesn't build.

### 5.6 Reporting coupling

Features do not call into `2-maintenance-reporting` to produce
`ActionRecord` entries automatically.  Every caller has to bridge
between `<Feature>ActionResult` shapes and
`ActionRecord`/`ActionStatus`.  A mapping helper — or, better, having
each manager optionally take a `MaintenanceReport` and auto-append —
would close the loop.

---

## 6. Documentation / Onboarding Gaps

### 6.1 No example coordinator

There is no reference code showing how a maintenance coordinator
composes managers, runners, aspects, and a report into a real
workflow.  Every feature's `design.md` has "Usage Examples" snippets,
but nothing end-to-end.

### 6.2 README is still template boilerplate

`README.rst` is the python-package-template README.  Replace with a
package-specific overview that mirrors the intro in `AGENTS.md`.

### 6.3 No cross-OS migration guide

The stated long-term plan is to derive Ubuntu, Windows, and macOS
versions of this package from these specs.  A guide is needed:

- Which parts of each spec are OS-agnostic vs. RHEL-specific?
- What's the copy/adapt process?
- Which features make sense on Windows/macOS at all?

### 6.4 `userguide/` vs `usersguide/` inconsistency — ✅ resolved

Resolved: the two new `.md` architecture docs were moved into
`userguide/` (the established folder) and the `usersguide/`
directory was removed.  References in `AGENTS.md` and
`specs/feature-list.md` updated accordingly.

---

## 7. Suggested Prioritisation

If building from here, my recommended order:

1. **Close quality gaps (4.1, 4.2, 4.4)** — dry-run + failure-mapping
   tests + `datetime` deprecation.  Small, durable, makes every
   downstream change safer.
2. **Extract shared base (5.3)** — eliminate the per-feature
   boilerplate before writing any more features.  Bigger refactor
   but pays off immediately.
3. **Wire the obvious integration seams (section 2)** — especially
   the `3-service-management` delegations and the
   `run_compliance_suite()` aggregator.  No new features, just
   consolidation.
4. **`codecraftai-*` reuse sweep (section 3)** — per the project memory.
   Delegate credential handling, error types, and (selectively)
   config parsing to the siblings.
5. **Fill the high-priority feature gaps (1.1, 1.2)** —
   authentication/identity and LUKS.  Both are blockers for real
   RHEL admin use.
6. **Decide on Layer 4 (5.1)** — either build a coordinator here or
   confirm it belongs in `codecraftai-provisioning` and document the
   seam.
