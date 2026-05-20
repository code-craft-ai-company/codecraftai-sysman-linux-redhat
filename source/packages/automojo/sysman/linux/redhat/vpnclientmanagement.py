"""
    automojo.sysman.linux.redhat.vpnclientmanagement

    Concrete implementation of the vpn-client-management contract for
    the Red Hat family (RHEL / CentOS Stream / Fedora). Provisions and
    lifecycles **Tailscale** (``tailscaled``) via the official dnf/yum
    repository at ``pkgs.tailscale.com`` and the **Twingate Connector**
    via the official Twingate yum repository. ``systemctl`` controls
    both daemons.

    All commands route through an injected :class:`ICommandRunner`
    instance. No direct subprocess use; no direct filesystem writes.

    See:
        * Contract: ``automojo-sysman-common/specs/contracts/
          vpn-client-management/contract.md``
        * Shared base: :class:`VPNClientManagementBase`
        * Per-OS spec: ``specs/3-vpn-client-management/``
"""

import json
import os

from dataclasses import dataclass
from typing import Dict, List, Optional

from automojo.aspects.aspects import Aspects
from automojo.interfaces.icommandrunner import ICommandRunner

from automojo.sysman.common.vpnactionresult import VPNActionResult
from automojo.sysman.common.vpnactionstatus import VPNActionStatus
from automojo.sysman.common.vpnclientmanagementbase import (
    VERB_KIND_READ,
    VERB_KIND_WRITE,
    VPNClientManagementBase,
)
from automojo.sysman.common.vpnclientmanagerconfig import VPNClientManagerConfig
from automojo.sysman.common.vpnclientqueryerror import VPNClientQueryError
from automojo.sysman.common.vpnclientstatus import VPNClientStatus
from automojo.sysman.common.vpnprovider import VPNProvider


# ---------------------------------------------------------------------------
# RedHat-prefixed aliases per the contract Naming section.
# ---------------------------------------------------------------------------

RedHatVPNActionStatus = VPNActionStatus
RedHatVPNActionResult = VPNActionResult
RedHatVPNClientStatus = VPNClientStatus
RedHatVPNClientQueryError = VPNClientQueryError


_DEFAULT_TAILSCALE_REPO_PATH = "/etc/yum.repos.d/tailscale.repo"
_DEFAULT_TWINGATE_REPO_PATH = "/etc/yum.repos.d/twingate.repo"
_DEFAULT_TWINGATE_CONFIG_PATH = "/etc/twingate/connector.conf"

_TAILSCALE_PACKAGE = "tailscale"
_TWINGATE_PACKAGE = "twingate-connector"

_TAILSCALE_SERVICE = "tailscaled"
_TWINGATE_SERVICE = "twingate-connector"

_TAILSCALE_STATE_DIR = "/var/lib/tailscale"
_TWINGATE_STATE_DIR = "/etc/twingate"


@dataclass
class RedHatVPNClientManagerConfig(VPNClientManagerConfig):
    """
        Configuration for :class:`RedHatVPNClientManager`.

        Extends :class:`VPNClientManagerConfig` with RHEL-specific
        knobs that pin repo and config file locations and select the
        package manager binary.

        :param tailscale_channel: Tailscale release channel suffix
                                  ("stable", "unstable", ...).
        :param tailscale_repo_path: Path to the on-disk Tailscale
                                    ``.repo`` file.
        :param twingate_repo_path: Path to the on-disk Twingate
                                   ``.repo`` file.
        :param twingate_config_path: Path to the Twingate Connector
                                     configuration file.
        :param package_manager: ``"dnf"`` or ``"yum"``. Auto-detected
                                lazily when left at the default of
                                ``"dnf"``.
    """
    tailscale_channel: str = "stable"
    tailscale_repo_path: str = _DEFAULT_TAILSCALE_REPO_PATH
    twingate_repo_path: str = _DEFAULT_TWINGATE_REPO_PATH
    twingate_config_path: str = _DEFAULT_TWINGATE_CONFIG_PATH
    package_manager: str = "dnf"


class RedHatVPNClientManager(VPNClientManagementBase):
    """
        Concrete RHEL/Fedora VPN client manager.

        Implements every abstract verb declared on
        :class:`VPNClientManagementBase` for Tailscale and Twingate.
        All commands route through the injected
        :class:`ICommandRunner`.
    """

    feature_name = "vpn-client-management"

    def __init__(
        self,
        *,
        runner: ICommandRunner,
        config: Optional[RedHatVPNClientManagerConfig] = None,
    ) -> None:
        """
            :param runner: Command runner instance injected by the
                           maintenance coordinator.
            :param config: Optional manager configuration. Defaults to
                           :class:`RedHatVPNClientManagerConfig`.
        """
        if config is None:
            cfg = RedHatVPNClientManagerConfig()
        else:
            cfg = config

        super().__init__(runner=runner, config=cfg)
        self._config: RedHatVPNClientManagerConfig = cfg
        self._detected_pm: Optional[str] = None
        return

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _detect_package_manager(self, *, aspects: Aspects) -> str:
        """
            Detect the system package manager binary, preferring
            ``dnf`` when present and falling back to ``yum``.

            The result is cached on the instance after the first call.

            :param aspects: Behaviour controls forwarded to the runner.

            :returns: ``"dnf"`` or ``"yum"``.
        """
        if self._detected_pm is not None:
            rtnval = self._detected_pm
            return rtnval

        dnf_probe = self._runner.run(
            ["command", "-v", "dnf"],
            elevated=False,
        )
        if dnf_probe.exit_code == 0:
            self._detected_pm = "dnf"
            rtnval = self._detected_pm
            return rtnval

        yum_probe = self._runner.run(
            ["command", "-v", "yum"],
            elevated=False,
        )
        if yum_probe.exit_code == 0:
            self._detected_pm = "yum"
            rtnval = self._detected_pm
            return rtnval

        # Honour configured fallback when neither probe succeeds.
        self._detected_pm = self._config.package_manager
        rtnval = self._detected_pm
        return rtnval

    def _rpm_installed_version(
        self,
        *,
        package: str,
        aspects: Aspects,
    ) -> Optional[str]:
        """
            Return the installed version of an RPM, or ``None`` when
            the package is not installed.

            :param package: RPM name.
            :param aspects: Behaviour controls forwarded to the runner.

            :returns: Version string or ``None``.
        """
        command = ["rpm", "-q", "--queryformat", "%{VERSION}", package]
        run_result = self._runner.run(command, elevated=False)
        if run_result.exit_code != 0:
            rtnval: Optional[str] = None
            return rtnval

        version_text = (run_result.stdout or "").strip()
        if len(version_text) == 0:
            rtnval = None
            return rtnval

        # rpm returns text containing "not installed" sometimes with exit 0
        # on certain hosts; guard against that.
        lowered = version_text.lower()
        if "not installed" in lowered:
            rtnval = None
            return rtnval

        rtnval = version_text
        return rtnval

    def _ensure_tailscale_repo(
        self,
        *,
        aspects: Aspects,
        distro: str = "rhel",
        release: str = "9",
    ) -> VPNActionResult:
        """
            Write the Tailscale ``.repo`` file via ``tee``.

            :param aspects: Behaviour controls forwarded to the runner.
            :param distro: Distribution slug for the repo URL.
            :param release: Release version slug for the repo URL.

            :returns: A :class:`VPNActionResult` describing the outcome.
        """
        channel = self._config.tailscale_channel
        repo_path = self._config.tailscale_repo_path
        baseurl_lines = [
            "[tailscale-stable]",
            "name=Tailscale stable",
            "baseurl=https://pkgs.tailscale.com/{}/{}/{}/".format(
                channel, distro, release,
            ),
            "enabled=1",
            "type=rpm",
            "repo_gpgcheck=1",
            "gpgcheck=0",
            "gpgkey=https://pkgs.tailscale.com/{}/{}/{}/repo.gpg".format(
                channel, distro, release,
            ),
        ]
        body = os.linesep.join(baseurl_lines) + os.linesep
        command = ["tee", repo_path]

        run_elevated = self._should_elevate(kind=VERB_KIND_WRITE)
        run_result = self._runner.run(
            command,
            elevated=run_elevated,
            stdin=body,
        )

        if run_result.exit_code == 0:
            mapped_status = VPNActionStatus.SUCCESS
        else:
            mapped_status = VPNActionStatus.FAILED

        rtnval = VPNActionResult(
            status=mapped_status,
            provider=VPNProvider.TAILSCALE,
            detail="write_repo:{}".format(repo_path),
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(command),
        )
        return rtnval

    def _ensure_twingate_repo(
        self,
        *,
        aspects: Aspects,
    ) -> VPNActionResult:
        """
            Write the Twingate ``.repo`` file via ``tee``.

            :param aspects: Behaviour controls forwarded to the runner.

            :returns: A :class:`VPNActionResult` describing the outcome.
        """
        repo_path = self._config.twingate_repo_path
        lines = [
            "[twingate]",
            "name=Twingate",
            "baseurl=https://packages.twingate.com/rpm/",
            "enabled=1",
            "type=rpm",
            "repo_gpgcheck=1",
            "gpgcheck=0",
            "gpgkey=https://packages.twingate.com/rpm/gpg.key",
        ]
        body = os.linesep.join(lines) + os.linesep
        command = ["tee", repo_path]

        run_elevated = self._should_elevate(kind=VERB_KIND_WRITE)
        run_result = self._runner.run(
            command,
            elevated=run_elevated,
            stdin=body,
        )

        if run_result.exit_code == 0:
            mapped_status = VPNActionStatus.SUCCESS
        else:
            mapped_status = VPNActionStatus.FAILED

        rtnval = VPNActionResult(
            status=mapped_status,
            provider=VPNProvider.TWINGATE,
            detail="write_repo:{}".format(repo_path),
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(command),
        )
        return rtnval

    def _read_file(
        self,
        *,
        path: str,
    ) -> Optional[str]:
        """
            Read a file's contents via the runner.

            :param path: File path on the target.

            :returns: File contents on success, ``None`` on failure.
        """
        run_result = self._runner.run(["cat", path], elevated=False)
        if run_result.exit_code != 0:
            rtnval: Optional[str] = None
            return rtnval

        rtnval = run_result.stdout
        return rtnval

    # ------------------------------------------------------------------
    # Verb: install_tailscale
    # ------------------------------------------------------------------

    def install_tailscale(
        self,
        *,
        version: Optional[str],
        channel: str,
        aspects: Aspects,
    ) -> VPNActionResult:
        """
            Install the Tailscale client.

            :param version: Specific package version, or ``None`` for
                            the channel default.
            :param channel: Release channel (forwarded into the
                            ``.repo`` file body).
            :param aspects: Behaviour controls forwarded to the runner.

            :returns: A :class:`VPNActionResult` describing the outcome.
        """
        # Honour the per-call channel override.
        if channel is not None and len(channel) > 0:
            self._config.tailscale_channel = channel

        if version is not None:
            target_pkg = "{}-{}".format(_TAILSCALE_PACKAGE, version)
        else:
            target_pkg = _TAILSCALE_PACKAGE

        pm = self._detect_package_manager(aspects=aspects)
        full_command = [pm, "install", "-y", target_pkg]

        # Dry-run short-circuit.
        dry_result = self._short_circuit_dry_run(
            provider=VPNProvider.TAILSCALE,
            command=full_command,
            detail="install_tailscale:{}".format(target_pkg),
        )
        if dry_result is not None:
            rtnval = dry_result
            return rtnval

        # Idempotency guard.
        installed_version = self._rpm_installed_version(
            package=_TAILSCALE_PACKAGE,
            aspects=aspects,
        )
        if installed_version is not None:
            already_satisfied = False
            if version is None:
                already_satisfied = True
            elif installed_version == version:
                already_satisfied = True

            if already_satisfied is True:
                rtnval = VPNActionResult(
                    status=VPNActionStatus.SKIPPED,
                    provider=VPNProvider.TAILSCALE,
                    detail="already_installed:{}".format(installed_version),
                    exit_code=None,
                    stdout=None,
                    stderr=None,
                    command=full_command,
                )
                return rtnval

        # Write repo first.
        repo_result = self._ensure_tailscale_repo(aspects=aspects)
        if repo_result.status == VPNActionStatus.FAILED:
            rtnval = repo_result
            return rtnval

        # Now install.
        install_result = self._dispatch_write(
            provider=VPNProvider.TAILSCALE,
            command=full_command,
            detail="install_tailscale:{}".format(target_pkg),
            aspects=aspects,
        )
        rtnval = install_result
        return rtnval

    # ------------------------------------------------------------------
    # Verb: install_twingate_connector
    # ------------------------------------------------------------------

    def install_twingate_connector(
        self,
        *,
        version: Optional[str],
        aspects: Aspects,
    ) -> VPNActionResult:
        """
            Install the Twingate Connector.

            :param version: Specific package version, or ``None`` for
                            the repository default.
            :param aspects: Behaviour controls forwarded to the runner.

            :returns: A :class:`VPNActionResult` describing the outcome.
        """
        if version is not None:
            target_pkg = "{}-{}".format(_TWINGATE_PACKAGE, version)
        else:
            target_pkg = _TWINGATE_PACKAGE

        pm = self._detect_package_manager(aspects=aspects)
        full_command = [pm, "install", "-y", target_pkg]

        dry_result = self._short_circuit_dry_run(
            provider=VPNProvider.TWINGATE,
            command=full_command,
            detail="install_twingate:{}".format(target_pkg),
        )
        if dry_result is not None:
            rtnval = dry_result
            return rtnval

        installed_version = self._rpm_installed_version(
            package=_TWINGATE_PACKAGE,
            aspects=aspects,
        )
        if installed_version is not None:
            already_satisfied = False
            if version is None:
                already_satisfied = True
            elif installed_version == version:
                already_satisfied = True

            if already_satisfied is True:
                rtnval = VPNActionResult(
                    status=VPNActionStatus.SKIPPED,
                    provider=VPNProvider.TWINGATE,
                    detail="already_installed:{}".format(installed_version),
                    exit_code=None,
                    stdout=None,
                    stderr=None,
                    command=full_command,
                )
                return rtnval

        repo_result = self._ensure_twingate_repo(aspects=aspects)
        if repo_result.status == VPNActionStatus.FAILED:
            rtnval = repo_result
            return rtnval

        install_result = self._dispatch_write(
            provider=VPNProvider.TWINGATE,
            command=full_command,
            detail="install_twingate:{}".format(target_pkg),
            aspects=aspects,
        )
        rtnval = install_result
        return rtnval

    # ------------------------------------------------------------------
    # Verb: configure
    # ------------------------------------------------------------------

    def configure(
        self,
        *,
        provider: VPNProvider,
        settings: Dict[str, str],
        aspects: Aspects,
    ) -> VPNActionResult:
        """
            Write provider-specific configuration.

            Tailscale has no on-disk configure step on RHEL — returns
            ``SKIPPED``. Twingate renders ``/etc/twingate/connector.conf``
            via ``tee``.

            :param provider: Target provider.
            :param settings: Provider-specific key/value settings.
            :param aspects: Behaviour controls forwarded to the runner.

            :returns: A :class:`VPNActionResult` describing the outcome.
        """
        validation = self._validate_provider(
            provider=provider,
            supported=[VPNProvider.TAILSCALE, VPNProvider.TWINGATE],
        )
        if validation is not None:
            rtnval = validation
            return rtnval

        if provider == VPNProvider.TAILSCALE:
            rtnval = VPNActionResult(
                status=VPNActionStatus.SKIPPED,
                provider=VPNProvider.TAILSCALE,
                detail="tailscale has no on-disk configure step on rhel",
                exit_code=None,
                stdout=None,
                stderr=None,
                command=None,
            )
            return rtnval

        # Twingate path — render connector.conf.
        if settings is None:
            settings_local: Dict[str, str] = {}
        else:
            settings_local = dict(settings)

        rendered_body = _render_twingate_conf(settings=settings_local)
        config_path = self._config.twingate_config_path
        command = ["tee", config_path]

        dry_result = self._short_circuit_dry_run(
            provider=VPNProvider.TWINGATE,
            command=command,
            detail="configure_twingate:{}".format(config_path),
        )
        if dry_result is not None:
            rtnval = dry_result
            return rtnval

        existing = self._read_file(path=config_path)
        if existing is not None and existing == rendered_body:
            rtnval = VPNActionResult(
                status=VPNActionStatus.SKIPPED,
                provider=VPNProvider.TWINGATE,
                detail="configure_twingate:matches_on_disk",
                exit_code=None,
                stdout=None,
                stderr=None,
                command=command,
            )
            return rtnval

        run_elevated = self._should_elevate(kind=VERB_KIND_WRITE)
        run_result = self._runner.run(
            command,
            elevated=run_elevated,
            stdin=rendered_body,
        )
        if run_result.exit_code == 0:
            mapped_status = VPNActionStatus.SUCCESS
        else:
            mapped_status = VPNActionStatus.FAILED

        rtnval = VPNActionResult(
            status=mapped_status,
            provider=VPNProvider.TWINGATE,
            detail="configure_twingate:{}".format(config_path),
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(command),
        )
        return rtnval

    # ------------------------------------------------------------------
    # Verb: bring_up
    # ------------------------------------------------------------------

    def bring_up(
        self,
        *,
        provider: VPNProvider,
        authkey: str,
        hostname: Optional[str],
        advertise_tags: Optional[List[str]],
        advertise_routes: Optional[List[str]],
        exit_node: bool,
        aspects: Aspects,
    ) -> VPNActionResult:
        """
            Bring the tunnel up.

            :param provider: Target provider.
            :param authkey: Vendor-issued auth key.
            :param hostname: Hostname to register.
            :param advertise_tags: Tags to advertise.
            :param advertise_routes: Subnets to advertise.
            :param exit_node: Whether to act as an exit node.
            :param aspects: Behaviour controls forwarded to the runner.

            :returns: A :class:`VPNActionResult` describing the outcome.
        """
        validation = self._validate_provider(
            provider=provider,
            supported=[VPNProvider.TAILSCALE, VPNProvider.TWINGATE],
        )
        if validation is not None:
            rtnval = validation
            return rtnval

        if provider == VPNProvider.TAILSCALE:
            rtnval = self._bring_up_tailscale(
                authkey=authkey,
                hostname=hostname,
                advertise_tags=advertise_tags,
                advertise_routes=advertise_routes,
                exit_node=exit_node,
                aspects=aspects,
            )
            return rtnval

        rtnval = self._bring_up_twingate(aspects=aspects)
        return rtnval

    def _bring_up_tailscale(
        self,
        *,
        authkey: str,
        hostname: Optional[str],
        advertise_tags: Optional[List[str]],
        advertise_routes: Optional[List[str]],
        exit_node: bool,
        aspects: Aspects,
    ) -> VPNActionResult:
        """
            Tailscale-specific bring-up.

            :returns: A :class:`VPNActionResult` describing the outcome.
        """
        # Build the tailscale-up argv.
        up_command: List[str] = ["tailscale", "up", "--authkey={}".format(authkey)]
        if hostname is not None and len(hostname) > 0:
            up_command.append("--hostname={}".format(hostname))
        if advertise_tags is not None and len(advertise_tags) > 0:
            tags_joined = ",".join(advertise_tags)
            up_command.append("--advertise-tags={}".format(tags_joined))
        if advertise_routes is not None and len(advertise_routes) > 0:
            routes_joined = ",".join(advertise_routes)
            up_command.append("--advertise-routes={}".format(routes_joined))
        if exit_node is True:
            up_command.append("--advertise-exit-node")

        dry_result = self._short_circuit_dry_run(
            provider=VPNProvider.TAILSCALE,
            command=up_command,
            detail="bring_up_tailscale",
        )
        if dry_result is not None:
            rtnval = dry_result
            return rtnval

        # Idempotency guard via status.
        current_status = self.status(
            provider=VPNProvider.TAILSCALE,
            aspects=aspects,
        )
        if current_status.running is True:
            host_match = True
            if hostname is not None and len(hostname) > 0:
                host_match = (current_status.hostname == hostname)
            if host_match is True:
                rtnval = VPNActionResult(
                    status=VPNActionStatus.SKIPPED,
                    provider=VPNProvider.TAILSCALE,
                    detail="bring_up_tailscale:already_running",
                    exit_code=None,
                    stdout=None,
                    stderr=None,
                    command=up_command,
                )
                return rtnval

        # Enable + start the daemon first.
        systemctl_command = ["systemctl", "enable", "--now", _TAILSCALE_SERVICE]
        systemctl_result = self._dispatch_write(
            provider=VPNProvider.TAILSCALE,
            command=systemctl_command,
            detail="enable_now:tailscaled",
            aspects=aspects,
        )
        if systemctl_result.status == VPNActionStatus.FAILED:
            rtnval = systemctl_result
            return rtnval

        # Now run tailscale up.
        up_result = self._dispatch_write(
            provider=VPNProvider.TAILSCALE,
            command=up_command,
            detail="bring_up_tailscale",
            aspects=aspects,
        )
        rtnval = up_result
        return rtnval

    def _bring_up_twingate(
        self,
        *,
        aspects: Aspects,
    ) -> VPNActionResult:
        """
            Twingate-specific bring-up.

            :returns: A :class:`VPNActionResult` describing the outcome.
        """
        command = ["systemctl", "enable", "--now", _TWINGATE_SERVICE]

        dry_result = self._short_circuit_dry_run(
            provider=VPNProvider.TWINGATE,
            command=command,
            detail="bring_up_twingate",
        )
        if dry_result is not None:
            rtnval = dry_result
            return rtnval

        current_status = self.status(
            provider=VPNProvider.TWINGATE,
            aspects=aspects,
        )
        if current_status.running is True:
            rtnval = VPNActionResult(
                status=VPNActionStatus.SKIPPED,
                provider=VPNProvider.TWINGATE,
                detail="bring_up_twingate:already_running",
                exit_code=None,
                stdout=None,
                stderr=None,
                command=command,
            )
            return rtnval

        rtnval = self._dispatch_write(
            provider=VPNProvider.TWINGATE,
            command=command,
            detail="bring_up_twingate",
            aspects=aspects,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Verb: bring_down
    # ------------------------------------------------------------------

    def bring_down(
        self,
        *,
        provider: VPNProvider,
        aspects: Aspects,
    ) -> VPNActionResult:
        """
            Bring the tunnel down without uninstalling.

            :param provider: Target provider.
            :param aspects: Behaviour controls forwarded to the runner.

            :returns: A :class:`VPNActionResult` describing the outcome.
        """
        validation = self._validate_provider(
            provider=provider,
            supported=[VPNProvider.TAILSCALE, VPNProvider.TWINGATE],
        )
        if validation is not None:
            rtnval = validation
            return rtnval

        if provider == VPNProvider.TAILSCALE:
            command = ["tailscale", "down"]
        else:
            command = ["systemctl", "stop", _TWINGATE_SERVICE]

        dry_result = self._short_circuit_dry_run(
            provider=provider,
            command=command,
            detail="bring_down:{}".format(provider.value),
        )
        if dry_result is not None:
            rtnval = dry_result
            return rtnval

        current_status = self.status(provider=provider, aspects=aspects)
        if current_status.running is False:
            rtnval = VPNActionResult(
                status=VPNActionStatus.SKIPPED,
                provider=provider,
                detail="bring_down:{}:already_down".format(provider.value),
                exit_code=None,
                stdout=None,
                stderr=None,
                command=command,
            )
            return rtnval

        rtnval = self._dispatch_write(
            provider=provider,
            command=command,
            detail="bring_down:{}".format(provider.value),
            aspects=aspects,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Verb: status (READ)
    # ------------------------------------------------------------------

    def status(
        self,
        *,
        provider: VPNProvider,
        aspects: Aspects,
    ) -> VPNClientStatus:
        """
            Report the local daemon state.

            :param provider: Target provider.
            :param aspects: Behaviour controls forwarded to the runner.

            :returns: A :class:`VPNClientStatus` snapshot.
            :raises VPNClientQueryError: When the underlying query
                fails for reasons other than "daemon not installed" or
                "daemon not running".
        """
        if provider == VPNProvider.TAILSCALE:
            rtnval = self._status_tailscale(aspects=aspects)
            return rtnval

        if provider == VPNProvider.TWINGATE:
            rtnval = self._status_twingate(aspects=aspects)
            return rtnval

        err_msg_lines = ["Unsupported VPN provider for status."]
        err_msg_lines.append("    PROVIDED: {!r}".format(provider))
        err_msg = os.linesep.join(err_msg_lines)
        raise VPNClientQueryError(message=err_msg)

    def _status_tailscale(self, *, aspects: Aspects) -> VPNClientStatus:
        """
            Tailscale-specific status query.

            :returns: A :class:`VPNClientStatus` snapshot.
        """
        installed_version = self._rpm_installed_version(
            package=_TAILSCALE_PACKAGE,
            aspects=aspects,
        )
        if installed_version is None:
            rtnval = VPNClientStatus(
                provider=VPNProvider.TAILSCALE,
                installed=False,
                running=False,
            )
            return rtnval

        run_result = self._runner.run(
            ["tailscale", "status", "--json"],
            elevated=False,
        )
        # Treat non-zero exit as "not running" only when the daemon is
        # responsive enough to report a stopped backend. Otherwise raise.
        if run_result.exit_code != 0:
            stderr_lc = (run_result.stderr or "").lower()
            if "stopped" in stderr_lc or "not running" in stderr_lc:
                rtnval = VPNClientStatus(
                    provider=VPNProvider.TAILSCALE,
                    installed=True,
                    running=False,
                    version=installed_version,
                )
                return rtnval

            err_msg_lines = ["tailscale status --json failed."]
            err_msg_lines.append(
                "    EXIT_CODE: {}".format(run_result.exit_code)
            )
            err_msg_lines.append(
                "    STDERR: {}".format(run_result.stderr)
            )
            err_msg = os.linesep.join(err_msg_lines)
            raise VPNClientQueryError(
                message=err_msg,
                exit_code=run_result.exit_code,
                stderr=run_result.stderr,
            )

        rtnval = _parse_tailscale_status_json(
            payload=run_result.stdout,
            installed_version=installed_version,
        )
        return rtnval

    def _status_twingate(self, *, aspects: Aspects) -> VPNClientStatus:
        """
            Twingate-specific status query.

            :returns: A :class:`VPNClientStatus` snapshot.
        """
        installed_version = self._rpm_installed_version(
            package=_TWINGATE_PACKAGE,
            aspects=aspects,
        )
        if installed_version is None:
            rtnval = VPNClientStatus(
                provider=VPNProvider.TWINGATE,
                installed=False,
                running=False,
            )
            return rtnval

        run_result = self._runner.run(
            ["twingate", "status"],
            elevated=False,
        )
        if run_result.exit_code != 0:
            stderr_lc = (run_result.stderr or "").lower()
            if "not running" in stderr_lc or "stopped" in stderr_lc:
                rtnval = VPNClientStatus(
                    provider=VPNProvider.TWINGATE,
                    installed=True,
                    running=False,
                    version=installed_version,
                )
                return rtnval

            err_msg_lines = ["twingate status failed."]
            err_msg_lines.append(
                "    EXIT_CODE: {}".format(run_result.exit_code)
            )
            err_msg_lines.append(
                "    STDERR: {}".format(run_result.stderr)
            )
            err_msg = os.linesep.join(err_msg_lines)
            raise VPNClientQueryError(
                message=err_msg,
                exit_code=run_result.exit_code,
                stderr=run_result.stderr,
            )

        rtnval = _parse_twingate_status_text(
            payload=run_result.stdout,
            installed_version=installed_version,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Verb: uninstall
    # ------------------------------------------------------------------

    def uninstall(
        self,
        *,
        provider: VPNProvider,
        purge: bool,
        aspects: Aspects,
    ) -> VPNActionResult:
        """
            Uninstall the daemon and optionally purge residual state.

            :param provider: Target provider.
            :param purge: When True, additionally remove the repo file
                          and the daemon state directory.
            :param aspects: Behaviour controls forwarded to the runner.

            :returns: A :class:`VPNActionResult` describing the outcome.
        """
        validation = self._validate_provider(
            provider=provider,
            supported=[VPNProvider.TAILSCALE, VPNProvider.TWINGATE],
        )
        if validation is not None:
            rtnval = validation
            return rtnval

        if provider == VPNProvider.TAILSCALE:
            package = _TAILSCALE_PACKAGE
            service = _TAILSCALE_SERVICE
            repo_path = self._config.tailscale_repo_path
            state_dir = _TAILSCALE_STATE_DIR
        else:
            package = _TWINGATE_PACKAGE
            service = _TWINGATE_SERVICE
            repo_path = self._config.twingate_repo_path
            state_dir = _TWINGATE_STATE_DIR

        pm = self._detect_package_manager(aspects=aspects)
        remove_command = [pm, "remove", "-y", package]

        dry_result = self._short_circuit_dry_run(
            provider=provider,
            command=remove_command,
            detail="uninstall:{}".format(package),
        )
        if dry_result is not None:
            rtnval = dry_result
            return rtnval

        installed_version = self._rpm_installed_version(
            package=package,
            aspects=aspects,
        )
        if installed_version is None:
            rtnval = VPNActionResult(
                status=VPNActionStatus.SKIPPED,
                provider=provider,
                detail="uninstall:{}:already_absent".format(package),
                exit_code=None,
                stdout=None,
                stderr=None,
                command=remove_command,
            )
            return rtnval

        # Stop the service first; ignore failure since the package may
        # still be removable.
        stop_command = ["systemctl", "stop", service]
        _ = self._dispatch_write(
            provider=provider,
            command=stop_command,
            detail="stop_service:{}".format(service),
            aspects=aspects,
        )

        remove_result = self._dispatch_write(
            provider=provider,
            command=remove_command,
            detail="uninstall:{}".format(package),
            aspects=aspects,
        )
        if remove_result.status == VPNActionStatus.FAILED:
            rtnval = remove_result
            return rtnval

        if purge is True:
            purge_command = ["rm", "-rf", repo_path, state_dir]
            purge_result = self._dispatch_write(
                provider=provider,
                command=purge_command,
                detail="purge:{}".format(package),
                aspects=aspects,
            )
            if purge_result.status == VPNActionStatus.FAILED:
                rtnval = purge_result
                return rtnval

        rtnval = remove_result
        return rtnval


# ---------------------------------------------------------------------------
# Module-level parsers and renderers
# ---------------------------------------------------------------------------


def _render_twingate_conf(*, settings: Dict[str, str]) -> str:
    """
        Render a deterministic ``connector.conf`` body.

        Lines are emitted as ``KEY=VALUE`` sorted by key. A trailing
        newline is appended so the rendered content compares cleanly
        against on-disk content read via ``cat``.

        :param settings: Setting key/value pairs.

        :returns: Rendered configuration as text.
    """
    keys_sorted = sorted(settings.keys())
    lines: List[str] = []
    for key in keys_sorted:
        value = settings[key]
        lines.append("{}={}".format(key, value))

    body = os.linesep.join(lines) + os.linesep
    return body


def _parse_tailscale_status_json(
    *,
    payload: str,
    installed_version: Optional[str],
) -> VPNClientStatus:
    """
        Parse ``tailscale status --json`` output.

        :param payload: Raw JSON text.
        :param installed_version: Version reported by ``rpm -q``, used
                                  as a fallback when JSON omits it.

        :returns: A :class:`VPNClientStatus` snapshot.
    """
    try:
        document = json.loads(payload)
    except json.JSONDecodeError:
        rtnval = VPNClientStatus(
            provider=VPNProvider.TAILSCALE,
            installed=True,
            running=False,
            version=installed_version,
        )
        return rtnval

    backend_state = document.get("BackendState", "")
    is_running = backend_state == "Running"

    self_block = document.get("Self") or {}
    tailscale_ips = self_block.get("TailscaleIPs") or []
    if len(tailscale_ips) > 0:
        self_address: Optional[str] = tailscale_ips[0]
    else:
        self_address = None

    hostname = self_block.get("HostName") or None

    magic_dns = document.get("MagicDNSSuffix") or document.get("CurrentTailnet", {}).get("Name") or None
    tailnet = magic_dns

    version_field = document.get("Version") or installed_version

    peer_block = document.get("Peer") or {}
    peer_addresses: Dict[str, str] = {}
    for _peer_key, peer_value in peer_block.items():
        peer_host = peer_value.get("HostName")
        peer_ips = peer_value.get("TailscaleIPs") or []
        if peer_host is None:
            continue
        if len(peer_ips) == 0:
            continue
        peer_addresses[peer_host] = peer_ips[0]

    rtnval = VPNClientStatus(
        provider=VPNProvider.TAILSCALE,
        installed=True,
        running=is_running,
        self_address=self_address,
        hostname=hostname,
        tailnet=tailnet,
        peer_addresses=peer_addresses,
        version=version_field,
    )
    return rtnval


def _parse_twingate_status_text(
    *,
    payload: str,
    installed_version: Optional[str],
) -> VPNClientStatus:
    """
        Parse ``twingate status`` text output.

        The Twingate Connector reports a small block of text; we only
        need to know whether it is running. Per the contract, Twingate
        has no per-host overlay IP and no peer list.

        :param payload: Raw text output from ``twingate status``.
        :param installed_version: Version reported by ``rpm -q``.

        :returns: A :class:`VPNClientStatus` snapshot.
    """
    lower_payload = (payload or "").lower()
    is_running = False
    if "running" in lower_payload or "online" in lower_payload or "connected" in lower_payload:
        is_running = True
    if "not running" in lower_payload:
        is_running = False
    if "stopped" in lower_payload:
        is_running = False

    rtnval = VPNClientStatus(
        provider=VPNProvider.TWINGATE,
        installed=True,
        running=is_running,
        self_address=None,
        hostname=None,
        tailnet=None,
        peer_addresses={},
        version=installed_version,
    )
    return rtnval


__all__ = [
    "RedHatVPNActionResult",
    "RedHatVPNActionStatus",
    "RedHatVPNClientManager",
    "RedHatVPNClientManagerConfig",
    "RedHatVPNClientQueryError",
    "RedHatVPNClientStatus",
    "VPNProvider",
]
