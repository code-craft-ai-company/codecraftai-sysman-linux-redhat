# Ubuntu Repo Configuration Prompt

Paste the text below (everything after the horizontal rule) as your first message in the
`automojo-sysmgr-linux-ubuntu` repo session.

---

## Context

This repository, `automojo-sysmgr-linux-ubuntu`, is the Ubuntu/Debian port of the
`automojo-sysmgr-linux-redhat` package.  It provides a Python system-management library
for Ubuntu Linux hosts.  The two packages share the same architecture, the same
`automojo-interfaces` abstractions (`ICommandRunner`, `IFileOperator`), and the same
Kiro spec conventions.  The only differences are the Ubuntu-specific tools, paths, and
subsystem names that replace their Red Hat equivalents.

The spec files (`requirements.md`, `design.md`, `tasks.md`) have been copied from the
Red Hat repo.  Your job is to adapt each spec so it describes the Ubuntu implementation
rather than the Red Hat one.  Do not change requirements that are tool-agnostic (data
models, dry-run behaviour, elevation handling, dependency injection, testing strategy).
Only change the parts that are Red Hat-specific.

---

## Spec File Conventions

Every feature lives under `specs/<feature-name>/` and contains exactly three files:

| File | Purpose |
|---|---|
| `requirements.md` | User stories and acceptance criteria (what the system does) |
| `design.md` | Architecture, data models, command sequences, module layout (how it works) |
| `tasks.md` | Ordered implementation checklist with requirement cross-references |

Follow these rules when editing specs:

- Keep the same requirement numbering and user story phrasing unless the tool change
  forces a semantic difference.
- Acceptance criteria that reference a specific Red Hat tool, path, or file format must
  be updated to the Ubuntu equivalent.
- Acceptance criteria that are tool-agnostic (result shapes, dry-run, elevation,
  idempotency, error propagation) should be left unchanged.
- In `design.md`, update module paths from
  `automojo.sysman.linux.redhat.<feature>` to `automojo.sysman.linux.ubuntu.<feature>`,
  and update file paths from `source/packages/automojo/sysman/linux/redhat/` to
  `source/packages/automojo/sysman/linux/ubuntu/`.
- In `tasks.md`, update any command strings, file paths, or tool names that differ on
  Ubuntu.  Task count and structure should remain similar.

---

## Red Hat → Ubuntu Tool and Path Substitutions

Apply these substitutions throughout all spec files.

### Package Management

| Red Hat | Ubuntu |
|---|---|
| `dnf install` | `apt-get install -y` |
| `dnf remove` | `apt-get remove -y` |
| `dnf update` | `apt-get upgrade -y` |
| `dnf check-update` | `apt-get -s upgrade` (simulate) |
| `dnf info` | `apt-cache show` |
| `dnf group install` | `tasksel install` |
| `dnf group remove` | `tasksel remove` |
| `rpm -qp` (local RPM) | `dpkg -i` (local .deb) |
| `dnf mark install` (hold) | `apt-mark hold` |
| `dnf mark remove` (release hold) | `apt-mark unhold` |
| `rpm -q` (query installed) | `dpkg-query -W -f='${Status}'` |

### Repository Management

| Red Hat | Ubuntu |
|---|---|
| `/etc/yum.repos.d/*.repo` | `/etc/apt/sources.list.d/*.list` or `*.sources` |
| `dnf repolist` | `apt-cache policy` |
| `dnf config-manager --add-repo` | `add-apt-repository` or manual `.list` file write |
| `dnf config-manager --enable` | `add-apt-repository --enable` or edit `.list` file |
| `dnf config-manager --disable` | comment out or `add-apt-repository --remove` |
| `dnf makecache` | `apt-get update` |
| RPM GPG key import (`rpm --import`) | `gpg --dearmor` + write to `/etc/apt/keyrings/` |
| `.repo` file format (INI) | one-line `deb` format or DEB822 `*.sources` format |

### Firewall Management

| Red Hat | Ubuntu |
|---|---|
| `firewalld` daemon | `ufw` (Uncomplicated Firewall) |
| `firewall-cmd` | `ufw` |
| `firewall-cmd --zone=<z> --add-service` | `ufw allow <service>` |
| `firewall-cmd --zone=<z> --remove-service` | `ufw delete allow <service>` |
| `firewall-cmd --zone=<z> --add-port` | `ufw allow <port>/<proto>` |
| `firewall-cmd --zone=<z> --remove-port` | `ufw delete allow <port>/<proto>` |
| `firewall-cmd --zone=<z> --add-rich-rule` | `ufw allow from <ip> to any port <port>` |
| `firewall-cmd --reload` | `ufw reload` |
| `firewall-cmd --state` | `ufw status` |
| `firewall-cmd --list-all` | `ufw status verbose` |
| Zones concept | No zone concept in ufw; use rule direction (in/out) instead |
| `--permanent` flag | ufw rules are persistent by default |

> **Note on zones:** ufw has no zone model.  Requirements that refer to a `zone`
> parameter should be changed to use a `direction` parameter (`in` or `out`), or
> removed where the concept does not translate.  Rules are always persistent in ufw
> (no equivalent to the Red Hat `--permanent` + `reload` pattern); the `reload()`
> method still exists for consistency but is a no-op or runs `ufw reload`.

### Service Management

Service management commands are identical on both platforms (both use systemd), so no
substitutions are needed for `service-management`.

### Kernel Parameters

Kernel parameter management (`sysctl`) is identical on both platforms.  No substitutions
needed for `kernel-parameters`.

### Network Configuration

| Red Hat | Ubuntu |
|---|---|
| NetworkManager / `nmcli` | Netplan (`/etc/netplan/*.yaml`) + `netplan apply` |
| `/etc/sysconfig/network-scripts/ifcfg-*` | `/etc/netplan/*.yaml` |
| `nmcli connection add` | Edit/write a Netplan YAML file |
| `nmcli connection modify` | Edit the Netplan YAML file |
| `nmcli connection delete` | Remove the Netplan YAML file |
| `nmcli connection up/down` | `netplan apply` (applies all active Netplan configs) |
| `nmcli device show` | `ip addr show` / `ip link show` |

### Security Compliance

| Red Hat | Ubuntu |
|---|---|
| SELinux (`getenforce`, `setenforce`, `sestatus`) | AppArmor (`aa-status`, `aa-enforce`, `aa-complain`) |
| SELinux modes: Enforcing / Permissive / Disabled | AppArmor modes: enforce / complain / disabled |
| `setenforce 1` | `aa-enforce /etc/apparmor.d/*` |
| `setenforce 0` | `aa-complain /etc/apparmor.d/*` |
| `/etc/selinux/config` | `/etc/default/grub` (for kernel boot param) |
| SELinux policy name (e.g. "targeted") | AppArmor profiles directory `/etc/apparmor.d/` |

> Rename `SELinuxMode` → `AppArmorMode`, `SELinuxStatus` → `AppArmorStatus`,
> `check_selinux` → `check_apparmor`, `set_selinux_mode` → `set_apparmor_mode`.

### Scheduled Tasks

Cron drop-in directory and systemd timer conventions are the same on Ubuntu.
Substitute `/etc/cron.d/` stays the same.  No changes needed for the cron half.
For systemd timers no changes needed.

### Log Management

| Red Hat | Ubuntu |
|---|---|
| `journalctl` (same) | `journalctl` (same) |
| `/var/log/messages` | `/var/log/syslog` |
| `rsyslog` config path `/etc/rsyslog.d/` | `/etc/rsyslog.d/` (same) |
| `logrotate` config path `/etc/logrotate.d/` | `/etc/logrotate.d/` (same) |

### User and Group Management

`useradd`, `usermod`, `userdel`, `groupadd`, `groupmod`, `groupdel`, `passwd`, `chage`
are identical on both platforms.  No substitutions needed.

### Filesystem Management

`mount`, `umount`, `/etc/fstab`, `lsblk`, `df`, `mkfs.*` are identical.  No
substitutions needed.

### Environment Configuration

`/etc/environment`, `/etc/profile.d/`, shell init files are the same.  No substitutions
needed.

### Configuration Management

File read/write/backup conventions via `IFileOperator` are the same.  No substitutions
needed.

### Command Execution

The `ICommandRunner` abstraction is platform-neutral.  No substitutions needed.

### Maintenance Reporting

Entirely data structures and serialisation — no platform-specific content.  No
substitutions needed.

---

## Module Path Pattern

Every occurrence of the pattern `automojo.sysman.linux.redhat` must become
`automojo.sysman.linux.ubuntu` in module names, import paths, class docstrings, and
module layout diagrams.

Every occurrence of `source/packages/automojo/sysman/linux/redhat/` must become
`source/packages/automojo/sysman/linux/ubuntu/`.

Every occurrence of `automojo/tests/sysman/linux/redhat/` must become
`automojo/tests/sysman/linux/ubuntu/`.

---

## What to Do When Asked to Modify a Spec

When asked to adapt a spec (e.g. "modify the package-management spec for Ubuntu"):

1. Read all three files (`requirements.md`, `design.md`, `tasks.md`) for that feature.
2. Apply the tool/path substitutions from the table above.
3. Update module paths throughout.
4. Adjust command sequences in `design.md` to use the Ubuntu tool.
5. Where a concept has no Ubuntu equivalent (e.g. firewall zones, SELinux), adapt the
   design as described in the notes above and flag the change clearly.
6. Do not change data model field names, result shapes, dry-run behaviour, elevation
   handling, or testing strategy unless the Ubuntu tool imposes a structural difference.
7. Commit and push each feature's three files together in a single commit with a message
   following the pattern:
   `Adapt <feature-name> spec for Ubuntu (<summary of key substitutions>)`

---

## Branch

Develop all changes on the branch designated for this session (check the session
instructions for the branch name).  Never push to `main` directly.
