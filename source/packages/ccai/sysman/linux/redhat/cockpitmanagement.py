# ccai.sysman.linux.redhat.cockpitmanagement
#
# CockpitManager wraps the cockpit.socket service and firewall
# integration.  Service-lifecycle calls delegate to ServiceManager
# (Part C seam); firewall calls delegate to FirewallManager (Part C
# seam).  Direct systemctl / firewall-cmd calls are no longer spelled
# here.

from dataclasses import dataclass
from typing import Optional

from ccai.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


CockpitActionStatus = ActionStatus


def _as_cockpit_result(base_result: FeatureActionResult) -> "CockpitActionResult":
    """
        Convert a :class:`FeatureActionResult` produced by a delegated
        :class:`ServiceManager` or :class:`FirewallManager` call into a
        :class:`CockpitActionResult`.
    """
    return CockpitActionResult(
        status=base_result.status,
        detail=base_result.detail,
        exit_code=base_result.exit_code,
        stdout=base_result.stdout,
        stderr=base_result.stderr,
        command=base_result.command,
    )


@dataclass
class CockpitManagerConfig(FeatureConfig):
    """
        Configuration for :class:`CockpitManager`.  Inherits ``dry_run``
        and ``elevated`` from :class:`FeatureConfig`.
    """
    pass


@dataclass
class CockpitActionResult(FeatureActionResult):
    """
        Outcome of a cockpit management action.
    """
    pass


class CockpitManager(BaseFeatureManager):
    """
        Facade for the cockpit web console.  Delegates systemctl +
        firewall-cmd calls to the canonical managers.
    """

    result_class = CockpitActionResult
    feature_name = "cockpit-management"

    def __init__(
        self,
        runner,
        config: Optional[CockpitManagerConfig] = None,
        *,
        service_manager=None,
        firewall_manager=None,
        report=None,
    ) -> None:
        super().__init__(runner=runner, config=config, report=report)
        if service_manager is None:
            from ccai.sysman.linux.redhat.servicemanagement import (
                ServiceManager,
                ServiceManagerConfig,
            )
            service_manager = ServiceManager(
                runner,
                ServiceManagerConfig(
                    dry_run=self._config.dry_run,
                    elevated=self._config.elevated,
                ),
            )
        if firewall_manager is None:
            from ccai.sysman.linux.redhat.firewallmanagement import (
                FirewallManager,
                FirewallManagerConfig,
            )
            firewall_manager = FirewallManager(
                runner,
                FirewallManagerConfig(
                    dry_run=self._config.dry_run,
                    elevated=self._config.elevated,
                ),
            )
        self._service_manager = service_manager
        self._firewall_manager = firewall_manager
        return

    def enable(self) -> CockpitActionResult:
        """
            Enable and start the ``cockpit.socket`` unit.
        """
        enable_res = self._service_manager.enable("cockpit.socket")
        if enable_res.status == ActionStatus.FAILED:
            return _as_cockpit_result(enable_res)
        start_res = self._service_manager.start("cockpit.socket")
        return _as_cockpit_result(start_res)

    def disable(self) -> CockpitActionResult:
        """
            Disable and stop the ``cockpit.socket`` unit.
        """
        stop_res = self._service_manager.stop("cockpit.socket")
        if stop_res.status == ActionStatus.FAILED:
            return _as_cockpit_result(stop_res)
        disable_res = self._service_manager.disable("cockpit.socket")
        return _as_cockpit_result(disable_res)

    def is_active(self) -> bool:
        """
            Return True when cockpit.socket is reported as active by
            :class:`ServiceManager`.
        """
        info = self._service_manager.query("cockpit.socket")
        return info.active_state == "active"

    def open_firewall(
        self,
        *,
        zone: str = "public",
    ) -> CockpitActionResult:
        """
            Permit cockpit traffic through firewalld by adding the
            ``cockpit`` service to ``zone``.
        """
        fw_res = self._firewall_manager.add_service("cockpit", zone)
        return _as_cockpit_result(fw_res)

    def close_firewall(
        self,
        *,
        zone: str = "public",
    ) -> CockpitActionResult:
        """
            Remove the cockpit service from ``zone`` in firewalld.
        """
        fw_res = self._firewall_manager.remove_service("cockpit", zone)
        return _as_cockpit_result(fw_res)
