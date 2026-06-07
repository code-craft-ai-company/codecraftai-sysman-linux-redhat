# Design

## Overview

The vpn-client-management feature is implemented as a single module,
`ccai.sysman.linux.redhat.vpnclientmanagement`, that exposes a
`RedHatVPNClientManager` class subclassing `VPNClientManagementBase` from
`codecraftai-sysman-common`. The manager installs and lifecycles **Tailscale**
(`tailscaled`) via the official `pkgs.tailscale.com` yum/dnf repository, and
the **Twingate Connector** via the official Twingate yum repository.
`systemctl` controls the daemons.

All commands route through `ICommandRunner` — package operations, `.repo`
file writes via `tee`, `systemctl` calls, and `tailscale` / `twingate` CLI
invocations. No direct subprocess use, no direct filesystem writes.

**See Also:**
- [vpn-client-management contract](../../../codecraftai-sysman-common/specs/contracts/vpn-client-management/contract.md)
- [vpn-client-management-base](../../../codecraftai-sysman-common/specs/vpn-client-management-base/design.md)
- [1-command-execution](../1-command-execution/design.md)
- [2-maintenance-reporting](../2-maintenance-reporting/design.md)
- [3-package-management](../3-package-management/design.md)
- [3-repository-management](../3-repository-management/design.md)
- [3-service-management](../3-service-management/design.md)
- [Ubuntu sibling impl](../../../codecraftai-sysman-linux-ubuntu/specs/3-vpn-client-management/design.md)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner (Local or SSH)                       │
│  - Configures aspects                                       │
│  - Injects runner + RedHatVPNClientManagerConfig            │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            RedHatVPNClientManager                           │
│  ccai.sysman.linux.redhat.vpnclientmanagement           │
│  inherits VPNClientManagementBase                           │
│                                                             │
│  install_tailscale()  install_twingate_connector()          │
│  configure()          bring_up()                            │
│  bring_down()         status()                              │
│  uninstall()                                                │
└────────────────────────┬────────────────────────────────────┘
                         │
        ┌────────────────┼─────────────────┐
        ▼                ▼                 ▼
┌─────────────────┐ ┌────────────┐ ┌──────────────────┐
│ dnf/yum + tee   │ │ systemctl  │ │ tailscale /      │
│ for .repo file, │ │ enable /   │ │ twingate CLIs    │
│ install, remove │ │ start /    │ │ (up/down/status) │
│                 │ │ stop       │ │                  │
└────────┬────────┘ └─────┬──────┘ └────────┬─────────┘
         │                │                 │
         └────────────────┼─────────────────┘
                          ▼
                   ICommandRunner
```

---

## Components

### `RedHatVPNClientManager`

**Location:** `source/packages/ccai/sysman/linux/redhat/vpnclientmanagement.py`

| Method | Signature | Description |
|---|---|---|
| `install_tailscale` | `(*, version, channel, aspects)` | Write `.repo` + `dnf install -y tailscale` |
| `install_twingate_connector` | `(*, version, aspects)` | Same shape, Twingate repo |
| `configure` | `(*, provider, settings, aspects)` | Render `/etc/twingate/connector.conf`; SKIPPED for Tailscale |
| `bring_up` | `(*, provider, authkey, hostname, advertise_tags, advertise_routes, exit_node, aspects)` | `systemctl enable --now tailscaled` + `tailscale up …`, or `systemctl enable --now twingate-connector` |
| `bring_down` | `(*, provider, aspects)` | `tailscale down` or `systemctl stop twingate-connector` |
| `status` | `(*, provider, aspects)` | `tailscale status --json` / `twingate status` |
| `uninstall` | `(*, provider, purge, aspects)` | `dnf remove` + repo + state cleanup |

### `RedHatVPNClientManagerConfig`

Extends `VPNClientManagerConfig`:

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no commands are executed |
| `elevated` | `bool` | `False` | When `True`, write commands pass `elevated=True` |
| `tailscale_channel` | `str` | `"stable"` | Repo channel suffix |
| `tailscale_repo_path` | `str` | `"/etc/yum.repos.d/tailscale.repo"` | yum sources file |
| `twingate_repo_path` | `str` | `"/etc/yum.repos.d/twingate.repo"` | yum sources file |
| `twingate_config_path` | `str` | `"/etc/twingate/connector.conf"` | Twingate Connector config |
| `package_manager` | `str` | `"dnf"` | `"dnf"` or `"yum"` — auto-detected by the manager on first call |

---

## Data Models

Re-export the shared contract types under RedHat-prefixed aliases:

```python
RedHatVPNActionStatus  = VPNActionStatus
RedHatVPNActionResult  = VPNActionResult
RedHatVPNClientStatus  = VPNClientStatus
RedHatVPNClientQueryError = VPNClientQueryError
```

---

## Provider-Backend Mapping

| Provider | Install command | Daemon | Bring-up command | Bring-down command | Status command |
|---|---|---|---|---|---|
| Tailscale | `<dnf\|yum> install -y tailscale[-<version>]` | `tailscaled.service` | `systemctl enable --now tailscaled` + `tailscale up …` | `tailscale down` | `tailscale status --json` |
| Twingate | `<dnf\|yum> install -y twingate-connector[-<version>]` | `twingate-connector.service` | `systemctl enable --now twingate-connector` | `systemctl stop twingate-connector` | `twingate status` |

---

## Command Mappings

### Repository Setup (Tailscale)

| Step | Command |
|---|---|
| Detect package manager | `command -v dnf` then `command -v yum` |
| Write `.repo` | `tee <tailscale_repo_path>` with body containing `name`, `baseurl=https://pkgs.tailscale.com/<channel>/<distro>/<release>/`, `enabled=1`, `gpgcheck=1`, `gpgkey=…` |
| Install | `<dnf\|yum> install -y tailscale[-<version>]` |

### Repository Setup (Twingate)

| Step | Command |
|---|---|
| Write `.repo` | `tee <twingate_repo_path>` with the Twingate yum config |
| Install | `<dnf\|yum> install -y twingate-connector[-<version>]` |

### Configuration

| Provider | Path | Mechanism |
|---|---|---|
| Tailscale | (none) | Returned `SKIPPED` |
| Twingate | `<twingate_config_path>` | Render via runner heredoc piped through `tee` |

### Read Commands (no elevation)

| Operation | Command |
|---|---|
| Tailscale status | `tailscale status --json` |
| Twingate status | `twingate status` |
| Installed check | `rpm -q --queryformat '%{VERSION}' <pkg>` |

---

## Idempotency Guards

| Verb | Guard | Mechanism |
|---|---|---|
| `install_tailscale` | Package present at requested version | `rpm -q --queryformat '%{VERSION}' tailscale` |
| `install_twingate_connector` | Same | `rpm -q` for `twingate-connector` |
| `configure` (Twingate) | Rendered content matches on-disk content | `cat <path>` via runner |
| `bring_up` | `status()` already shows `running=True` with matching hostname/tags | Pre-call status query |
| `bring_down` | `status()` already shows `running=False` | Pre-call status query |
| `uninstall` | Package already absent | `rpm -q` non-zero |

---

## Elevation Policy

- Writes pass `elevated=True` when `config.elevated=True`.
- Reads (status, `rpm -q`) never elevate.
- Implemented via the shared `_should_elevate` helper.

---

## Module Layout

```
source/packages/ccai/sysman/linux/redhat/
├── __init__.py
└── vpnclientmanagement.py     ← RedHatVPNClientManager, config, aliases

source/testroots/testbase/
└── ccai/tests/sysman/linux/redhat/
    └── test_vpnclientmanagement.py
```

---

## See Also

- [vpn-client-management contract](../../../codecraftai-sysman-common/specs/contracts/vpn-client-management/contract.md)
- [vpn-client-management-base](../../../codecraftai-sysman-common/specs/vpn-client-management-base/design.md)
- [3-firewall-management](../3-firewall-management/design.md) — sibling sysman feature pattern
- [3-service-management](../3-service-management/design.md) — `systemctl` reused

---

## Testing Strategy

All tests use a `MockCommandRunner` keyed on the command-args prefix. No
test requires a live host.

**Example-based tests:**

- One command-construction test per write verb per provider.
- One idempotency test per write verb (install, configure, bring_up,
  bring_down, uninstall).
- One dry-run test per write verb.
- One parser test per provider — `tailscale status --json` and
  `twingate status` mock outputs covering running and stopped backend states.
- One elevation test proving writes pass `elevated=True` and reads do not.
- One package-manager detection test verifying `dnf` is preferred when
  present and `yum` is used otherwise.

**Property tests (hypothesis):**

- `install_tailscale` idempotency — second call returns `SKIPPED`.
- `uninstall` idempotency for both providers.
- `bring_up` idempotency on matching state.
- Dry-run safety across generated inputs.
