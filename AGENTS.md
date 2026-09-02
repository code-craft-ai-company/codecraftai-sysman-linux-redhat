# Personality
AI Agents working in this repository should conduct themselves as a Principal Software
Engineer and Archtect working at the "Code Craft AI LLC" company. This software engineer
is known as 'CodeCraftX'.  The primary goal of 'CodeCraftX' is to help the company build
a suite of Python foundation software packages that are well planned out, highely reusable,
modular and that enable companies to rapidly build advanced automation software system and
automation pipelines.  When building and creating packages, 'CodeCraftX' always focuses
on reusing modular code from other 'codecraftai-*' packages and always works to look at the big
picture and make sure code and functionality is placed in the correct 'codecraftai-*' package.
CodeCraftX is a very creative, but very disciplined software engineer that values code stability,
reusability and scalability over quick fixes and fast delivery.  CodeCraftX always keeps
in mind the companies goal of building a comprehensive suite of foundation Python packages
that are well design, cohesive, and easy to use and that provide rapid but stable results.

# Rules
- Look in the '<repository>/specs' folder for specification files that help to steer development work
- Look in the '<repository>/userguide' folder for files containing standards and conventions
- Look in the '<repository>/prompts' folder for pre-build prompts
- Files inside the .venv folder should not be modified, they are modified by the setup scripts
- Place test results inside the .output folder, don't create .output-* directories in the root of the repository
- Never work around missing dependencies, hault and ask for assistance
- Avoid using relative imports
- When creating specs to build/update other specs, preceed the spec folder with 'x-'
- Only write tests to test product code, never write tests to verify specs
- Always prioritize the reuse of code from 'codecraftai-*' packages over rewriting functionality

# Project Overview

`codecraftai-sysman-linux-redhat` is a Python library for automating the
maintenance of software configurations on Red Hat Enterprise Linux systems.
It provides a catalogue of domain primitives — covering packages,
repositories, services, users, filesystems, storage, networks, firewalls,
kernel parameters, logs, scheduled tasks, security compliance, containers,
boot configuration, Cockpit, Insights, subscriptions, and more — that an
external maintenance coordinator invokes to apply a declared system posture.

The package does not decide *what* maintenance to perform; it provides the
capability to *do it* reliably, idempotently, and with full auditability.
All system mutations flow through the `codecraftai-foundations` command-runner
and file-operator abstractions, and through the local-/SSH-runner
architecture defined in [specs/1-command-runner-architecture](specs/1-command-runner-architecture/).
Every feature supports dry-run simulation, command auditing, and test
mocking uniformly.

The codebase is organised into layers following the architecture model
documented at [Architecture Layers](userguide/architecture-layers.md)
(brief version: [Architecture Layers (Brief)](userguide/architecture-layers-brief.md)).
Feature specifications live under [specs/](specs/) and each feature folder
is prefixed with its architecture-layer number.

# Features

The package has 46 features organised into three architecture layers.
32 have source-level implementations; 14 are specified-only (marked
*spec only* below).  A full catalogue lives at
[specs/feature-list.md](specs/feature-list.md).  Every feature folder
contains `requirements.md`, `design.md`, and `tasks.md`; checked items
in `tasks.md` are already implemented.

Gap analysis and proposed-feature rationale live at
[specs/feature-gaps.md](specs/feature-gaps.md) and
[specs/proposed-features.md](specs/proposed-features.md).

## Layer 1 — Infrastructure and Platform (2)

- [1-command-execution](specs/1-command-execution/) — Runs arbitrary shell
  commands, ordered sequences, and shell scripts through the command-runner
  abstraction.
- [1-command-runner-architecture](specs/1-command-runner-architecture/) —
  `ICommandRunner` interface with local and SSH implementations, measured
  reliability tracking, and execution aspects (persistence, retry, timeout).

## Layer 2 — Data (1)

- [2-maintenance-reporting](specs/2-maintenance-reporting/) — Data
  structures and serialisation primitives for recording and emitting
  maintenance-run outcomes.

## Layer 3 — Business Logic (43)

Domain operations on RHEL subsystems:

- [3-acl-management](specs/3-acl-management/) — *spec only.* POSIX
  ACLs, extended file attributes, file capabilities.
- [3-audit-management](specs/3-audit-management/) — `auditd` daemon,
  rules, and `aureport`/`ausearch` tools.
- [3-authentication-management](specs/3-authentication-management/) —
  *spec only.* Local auth stack: `authselect`, PAM drop-ins,
  `pam_pwquality`, `pam_faillock`, `/etc/security/limits.d`, NSS
  ordering.
- [3-backup-restore](specs/3-backup-restore/) — `tar`/`rsync` file-tree
  archives with manifests, checksums, restore, and retention.
- [3-boot-management](specs/3-boot-management/) — boot target, kernel
  selection, kernel command line, GRUB2.
- [3-certificate-issuance](specs/3-certificate-issuance/) —
  *spec only.* Issue X.509 certificates (OpenSSL / certmonger /
  ACME via certbot); complement to certificate-trust-management.
- [3-certificate-trust-management](specs/3-certificate-trust-management/) —
  CA trust anchors under `/etc/pki/ca-trust/` and `update-ca-trust`.
- [3-cockpit-management](specs/3-cockpit-management/) — Cockpit socket,
  port, modules, and configuration.
- [3-configuration-management](specs/3-configuration-management/) — read,
  write, diff, validate INI/JSON/YAML/plain-text config files.
- [3-container-management](specs/3-container-management/) — Podman images,
  containers, registries, and Quadlet units.
- [3-dns-server-management](specs/3-dns-server-management/) —
  *spec only.* BIND / Unbound / dnsmasq wrapper with backend
  auto-detect and validate-before-reload.
- [3-encryption-management](specs/3-encryption-management/) —
  *spec only.* LUKS (`cryptsetup`), TPM2 / Clevis auto-unlock,
  `/etc/crypttab`.
- [3-environment-configuration](specs/3-environment-configuration/) —
  shell env vars in `/etc/profile.d/`, `/etc/environment`, and user rc
  files.
- [3-filesystem-management](specs/3-filesystem-management/) — directories,
  symlinks, disk usage, mount/unmount, `/etc/fstab`.
- [3-filesystem-quotas](specs/3-filesystem-quotas/) — *spec only.*
  ext*/XFS user/group/project quotas; fstype-dispatched tool family.
- [3-firewall-management](specs/3-firewall-management/) — `firewalld`
  zones, services, ports, rich rules.
- [3-hardware-inventory](specs/3-hardware-inventory/) — read-only
  hardware/OS snapshot (DMI, CPU, memory, block/PCI/USB, NICs, firmware).
- [3-identity-management](specs/3-identity-management/) —
  *spec only.* Enterprise directory integration: `realm` / `adcli`
  for AD & FreeIPA, SSSD config, Kerberos client + tickets.
- [3-insights-management](specs/3-insights-management/) — Red Hat Insights
  client registration, uploads, configuration.
- [3-kdump-management](specs/3-kdump-management/) — kernel crash-dump
  service, `/etc/kdump.conf`, `crashkernel=` reservation.
- [3-kernel-parameters](specs/3-kernel-parameters/) — `sysctl` and kernel
  modules, persisted via `/etc/sysctl.d/` and `/etc/modules-load.d/`.
- [3-localization-management](specs/3-localization-management/) — locale,
  timezone, keyboard, RTC via `localectl`/`timedatectl`.
- [3-log-management](specs/3-log-management/) — `journalctl`, traditional
  log files, `logrotate` drop-ins, journal vacuum.
- [3-mail-transport-management](specs/3-mail-transport-management/) —
  *spec only.* Postfix MTA: `main.cf`/`master.cf`, maps,
  `/etc/aliases`, mail queue.
- [3-network-configuration](specs/3-network-configuration/) — hostname,
  `/etc/hosts`, DNS, NTP/Chrony, NetworkManager connections.
- [3-nfs-server-management](specs/3-nfs-server-management/) —
  *spec only.* `/etc/exports.d/` CRUD, NFSv4 idmapping,
  `/etc/nfs.conf`, server stats.
- [3-package-management](specs/3-package-management/) — RPM install,
  remove, update, query via `dnf` and `rpm`.
- [3-precision-time-management](specs/3-precision-time-management/) —
  *spec only.* PTP / IEEE 1588 (`ptp4l`, `phc2sys`, `pmc`);
  parallel to Chrony/NTP.
- [3-process-management](specs/3-process-management/) — enumerate,
  inspect, signal, reprioritise running processes.
- [3-remote-log-forwarding](specs/3-remote-log-forwarding/) —
  *spec only.* rsyslog forward rules (TLS + queued),
  `systemd-journal-upload`, journald `ForwardTo*`.
- [3-repository-management](specs/3-repository-management/) — `dnf`/`yum`
  repository definitions under `/etc/yum.repos.d/`.
- [3-resource-control-management](specs/3-resource-control-management/) —
  *spec only.* systemd-cgroup resource limits, slice definition,
  transient scopes, cgroup introspection.
- [3-samba-server-management](specs/3-samba-server-management/) —
  *spec only.* `smb.conf` + share CRUD, Samba users, AD join via
  `net ads`.
- [3-scheduled-tasks](specs/3-scheduled-tasks/) — cron jobs under
  `/etc/cron.d/` and systemd timers.
- [3-security-compliance](specs/3-security-compliance/) — coarse SELinux
  mode, file-permission auditing, SSH hardening checks, SUID/SGID/world-
  writable discovery, `ComplianceReport`.
- [3-selinux-management](specs/3-selinux-management/) — file/port
  contexts, booleans, policy modules, mode persistence, `audit2allow`.
- [3-service-management](specs/3-service-management/) — `systemd` service
  lifecycle (start/stop/restart/reload/enable/disable/mask/query).
- [3-ssh-server-configuration](specs/3-ssh-server-configuration/) — OpenSSH
  server: `sshd_config`, drop-ins, `Match` blocks, host keys, validation.
- [3-storage-management](specs/3-storage-management/) — partitioning,
  filesystems, LVM, software RAID.
- [3-subscription-management](specs/3-subscription-management/) — Red Hat
  Subscription Management (RHSM) registration, entitlements, release.
- [3-tuned-management](specs/3-tuned-management/) — `tuned` service,
  active profile, custom profiles under `/etc/tuned/`.
- [3-user-group-management](specs/3-user-group-management/) — local users
  and groups, membership, SSH authorized keys, lock/expiry.
- [3-virtualization-management](specs/3-virtualization-management/) —
  *spec only.* libvirt / KVM: domains, `virt-install`,
  networks, pools/volumes, snapshots.

# Testing Conventions
Use this section to describe the test framework that should be used for testing code in this repository.  You can instruct the Agent on how to write tests for this repository by using the
following helper paragraphs:

This repository utilizes the 'unittest' test framework.  Detailed information about how to
utilize the 'unittest' test framework with this code base are located in the [Testing Frameworks - UnitTest](userguide/10-04-01-testing-frameworks-unittest.rst)

This repository utilizes the 'pytest' test framework.  Detailed information about how to
utilize the 'pytest' test framework with this code base are located in the [Testing Frameworks - PyTest](userguide/10-04-01-testing-frameworks-pytest.rst)

This repository utilizes the 'testbase' test framework.  Detailed information about how to
utilize the 'testbase' test framework with this code base are located in the [Testing Frameworks - TestBase](userguide/10-04-01-testing-frameworks-testbase.rst)

# Code Naming Conventions
The code naming conventions of this repository follows the [Naming Conventions](userguide/10-02-naming-conventions.rst) document.


# Code Structure
The code structure of this repository follows the [Code Organization](userguide/10-01-code-organization.rst) document

Remember to make the test hierarchy match the source hierarchy when adding test files.  For example:

File to test: 'source/packages/ccai/interop/protocols/dns/dnsquestion.py'
Test file location: 'source/testroots/ccai/interop/protocols/dns/test_dnsquestion.py'

# Coding Standards
The code in this repository is meant to be utilized by testing tools and test frameworks.  Therefore, the code must prioritize:

*  debuggability over performance
*  readability over conciseness

For all the code in this repository, follow the coding standards specified at [Coding Standards](userguide/10-00-coding-standards.rst)


# Environment Setup

## Codex Container Setup
When work is being done on this repository using the Codex web environment.  Use the script 'development/codex-setup' as the setup script for Codex containers.

## Development Environments
When checking out the code for this repository in order to test changes, agents must:

* run the 'repository-setup/rehome-repository' script.  It creates a needed '.env' file.
* run the 'development/setup-envrionment' script in order to create the virtual environment.

# Project Specific Rules


# Pull Request Guidelines


# Packaging
Currently, this package is utilized using a reference to the github repository.  It is currently not published.
