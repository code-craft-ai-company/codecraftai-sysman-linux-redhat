# Requirements

## Introduction

The vpn-client-management feature provides the `codecraftai-sysman-linux-redhat`
package with the ability to install, configure, and lifecycle the **Tailscale**
client (`tailscaled`) and the **Twingate Connector** on RHEL / CentOS Stream /
Fedora hosts. This spec implements the vpn-client-management contract defined
at `codecraftai-sysman-common/specs/contracts/vpn-client-management/contract.md`
for the Red Hat family using the official Tailscale dnf/yum repository, the
official Twingate yum repository, and `systemctl` for service control.

A maintenance coordinator drives all operations through this feature's public
interfaces. All system commands flow through `ICommandRunner`, with the
`aspects` parameter forwarded on every call. Companion features
[`1-command-execution`](../1-command-execution/requirements.md),
[`2-maintenance-reporting`](../2-maintenance-reporting/requirements.md),
[`3-package-management`](../3-package-management/requirements.md),
[`3-repository-management`](../3-repository-management/requirements.md), and
[`3-service-management`](../3-service-management/requirements.md) supply the
runner, reporting, package, repository, and service primitives this feature
composes on.

---

## Requirements

### Requirement 1: Install Tailscale via the Official dnf/yum Repository

**User Story:** As a maintenance coordinator, I want to install Tailscale on
a Red Hat family host using the official Tailscale yum/dnf repository, so
that the `tailscale` package is present and managed by the system package
manager.

#### Acceptance Criteria

1. WHEN the coordinator calls `install_tailscale(version=None,
   channel="stable", aspects=…)` THE SYSTEM SHALL write the Tailscale
   repository file to `/etc/yum.repos.d/tailscale.repo` via the runner, then
   run `dnf install -y tailscale` (or `yum install -y tailscale` on systems
   without dnf).
2. WHEN `version` is a concrete string THE SYSTEM SHALL install
   `tailscale-<version>`.
3. WHEN `tailscale` is already installed at the requested version THE SYSTEM
   SHALL return `RedHatVPNActionResult(status=SKIPPED, provider=TAILSCALE)`
   without invoking dnf.
4. WHERE the runner is configured for elevation THE SYSTEM SHALL pass
   `elevated=True` for the repository file write and the install commands.
5. IF the install commands fail THEN THE SYSTEM SHALL return
   `RedHatVPNActionResult(status=FAILED, …)` populated with exit code,
   stdout, and stderr.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command and
   SHALL return `RedHatVPNActionResult(status=DRY_RUN, …)`.

---

### Requirement 2: Install the Twingate Connector via dnf/yum

**User Story:** As a maintenance coordinator, I want to install the Twingate
Connector on a Red Hat family host using the official Twingate yum
repository, so that the `twingate-connector` package is present and managed
by the system package manager.

#### Acceptance Criteria

1. WHEN the coordinator calls `install_twingate_connector(version=None,
   aspects=…)` THE SYSTEM SHALL write the Twingate `.repo` file under
   `/etc/yum.repos.d/twingate.repo` and run `dnf install -y
   twingate-connector`.
2. WHEN `version` is a concrete string THE SYSTEM SHALL install
   `twingate-connector-<version>`.
3. WHEN the package is already present at the requested version THE SYSTEM
   SHALL return `RedHatVPNActionResult(status=SKIPPED, provider=TWINGATE)`.
4. WHERE the runner is configured for elevation THE SYSTEM SHALL pass
   `elevated=True` for all install commands.
5. IF install fails THEN THE SYSTEM SHALL return
   `RedHatVPNActionResult(status=FAILED, …)`.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command and
   SHALL return `RedHatVPNActionResult(status=DRY_RUN, …)`.

---

### Requirement 3: Configure a Provider

**User Story:** As a maintenance coordinator, I want to write provider-specific
configuration files for the Twingate Connector, so the daemon has the inputs
it needs before bring-up.

#### Acceptance Criteria

1. WHEN the coordinator calls `configure(provider=VPNProvider.TWINGATE,
   settings=…, aspects=…)` THE SYSTEM SHALL render
   `/etc/twingate/connector.conf` via the runner with `elevated=True` WHERE
   the runner is configured for elevation.
2. WHEN `provider=VPNProvider.TAILSCALE` THE SYSTEM SHALL return
   `RedHatVPNActionResult(status=SKIPPED, provider=TAILSCALE,
   detail="tailscale has no on-disk configure step on rhel")`.
3. WHEN the rendered configuration already matches what is on disk THE SYSTEM
   SHALL return `RedHatVPNActionResult(status=SKIPPED, …)`.
4. IF the file write fails THEN THE SYSTEM SHALL return
   `RedHatVPNActionResult(status=FAILED, …)`.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT write any file and SHALL
   return `RedHatVPNActionResult(status=DRY_RUN, …)`.

---

### Requirement 4: Bring the Tunnel Up

**User Story:** As a maintenance coordinator, I want to authenticate the host
to the tailnet (Tailscale) or network (Twingate) and bring the tunnel up,
so the device is reachable on the overlay.

#### Acceptance Criteria

1. WHEN the coordinator calls `bring_up(provider=TAILSCALE, authkey=<key>,
   hostname=<name>, advertise_tags=<tags>, advertise_routes=<routes>,
   exit_node=<bool>, aspects=…)` THE SYSTEM SHALL invoke
   `systemctl enable --now tailscaled` (if not already running) followed by
   `tailscale up --authkey=<key> --hostname=<name> …`.
2. WHEN the coordinator calls `bring_up(provider=TWINGATE, …)` THE SYSTEM
   SHALL invoke `systemctl enable --now twingate-connector` after
   `configure` has written the token.
3. WHEN `status(provider=…)` already shows `running=True` with matching
   hostname/tags THE SYSTEM SHALL return
   `RedHatVPNActionResult(status=SKIPPED, …)`.
4. WHERE the runner is configured for elevation THE SYSTEM SHALL pass
   `elevated=True`.
5. IF the command fails THEN THE SYSTEM SHALL return
   `RedHatVPNActionResult(status=FAILED, …)`.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command and
   SHALL return `RedHatVPNActionResult(status=DRY_RUN, …)`.

---

### Requirement 5: Bring the Tunnel Down

**User Story:** As a maintenance coordinator, I want to take the host off the
overlay without uninstalling.

#### Acceptance Criteria

1. WHEN the coordinator calls `bring_down(provider=TAILSCALE, aspects=…)`
   THE SYSTEM SHALL invoke `tailscale down`.
2. WHEN the coordinator calls `bring_down(provider=TWINGATE, aspects=…)`
   THE SYSTEM SHALL invoke `systemctl stop twingate-connector`.
3. WHEN `status` already shows `running=False` THE SYSTEM SHALL return
   `RedHatVPNActionResult(status=SKIPPED, …)`.
4. WHERE the runner is configured for elevation THE SYSTEM SHALL pass
   `elevated=True`.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command and
   SHALL return `RedHatVPNActionResult(status=DRY_RUN, …)`.

---

### Requirement 6: Report Client Status

**User Story:** As a maintenance coordinator, I want to read the current state
of the VPN client without invoking writes.

#### Acceptance Criteria

1. WHEN the coordinator calls `status(provider=TAILSCALE, aspects=…)` THE
   SYSTEM SHALL invoke `tailscale status --json` without elevation and parse
   the JSON into a `RedHatVPNClientStatus`.
2. WHEN backend state is `Running` THE SYSTEM SHALL populate `running=True`,
   `self_address`, `hostname`, `tailnet`, `peer_addresses`, and `version` per
   the JSON.
3. WHEN the coordinator calls `status(provider=TWINGATE, …)` THE SYSTEM SHALL
   invoke `twingate status` and parse the text output into
   `RedHatVPNClientStatus(self_address=None, peer_addresses={})`.
4. WHEN the daemon binary is not installed THE SYSTEM SHALL return
   `RedHatVPNClientStatus(installed=False, running=False, …)`.
5. IF the status command fails for any other reason THEN THE SYSTEM SHALL
   raise `RedHatVPNClientQueryError` with the exit code and stderr.

---

### Requirement 7: Uninstall

**User Story:** As a maintenance coordinator, I want to uninstall a VPN
client and optionally purge its state.

#### Acceptance Criteria

1. WHEN the coordinator calls `uninstall(provider=TAILSCALE, purge=False,
   aspects=…)` THE SYSTEM SHALL stop the service and `dnf remove -y
   tailscale`.
2. WHEN `purge=True` THE SYSTEM SHALL additionally remove
   `/etc/yum.repos.d/tailscale.repo` and the Tailscale state directory under
   `/var/lib/tailscale`.
3. WHEN the coordinator calls `uninstall(provider=TWINGATE, …)` THE SYSTEM
   SHALL apply the equivalent steps for `twingate-connector`.
4. WHEN the package is already absent THE SYSTEM SHALL return
   `RedHatVPNActionResult(status=SKIPPED, …)`.
5. WHERE the runner is configured for elevation THE SYSTEM SHALL pass
   `elevated=True`.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command and
   SHALL return `RedHatVPNActionResult(status=DRY_RUN, …)`.

---

### Requirement 8: Aspects Forwarding

**User Story:** As a maintenance coordinator, I want every behaviour-bearing
operation to take an `aspects` parameter.

#### Acceptance Criteria

1. THE SYSTEM SHALL accept an `aspects: Aspects` parameter on every write
   and read verb.
2. THE SYSTEM SHALL forward `aspects` to the runner verbatim.
3. THE SYSTEM SHALL NOT apply any `@retry` or behaviour-modifying decorator.
