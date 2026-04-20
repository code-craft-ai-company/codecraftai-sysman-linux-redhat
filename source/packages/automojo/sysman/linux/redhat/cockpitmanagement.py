# automojo.sysman.linux.redhat.cockpitmanagement
#
# CockpitManager wraps the cockpit.socket service and port configuration
# through the ICommandRunner abstraction.

from dataclasses import dataclass

from automojo.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


CockpitActionStatus = ActionStatus


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
        Facade for the cockpit web console.
    """

    result_class = CockpitActionResult
    feature_name = "cockpit-management"

    def enable(self) -> CockpitActionResult:
        """
            Enable and start the ``cockpit.socket`` unit.
        """
        command = ["systemctl", "enable", "--now", "cockpit.socket"]
        return self._dispatch(command=command, detail="cockpit.socket", operation="enable")

    def disable(self) -> CockpitActionResult:
        """
            Disable and stop the ``cockpit.socket`` unit.
        """
        command = ["systemctl", "disable", "--now", "cockpit.socket"]
        return self._dispatch(command=command, detail="cockpit.socket", operation="disable")

    def is_active(self) -> bool:
        """
            Return True when cockpit.socket is reported as active by systemctl.
        """
        run_result = self._runner.run(
            ["systemctl", "is-active", "cockpit.socket"], elevated=False,
        )
        return run_result.stdout.strip() == "active"

    def open_firewall(
        self,
        *,
        zone: str = "public",
    ) -> CockpitActionResult:
        """
            Permit cockpit traffic through firewalld by adding the service.
        """
        command = [
            "firewall-cmd", "--zone={}".format(zone),
            "--add-service=cockpit", "--permanent",
        ]
        return self._dispatch(command=command, detail="cockpit firewall", operation="open_firewall")

    def close_firewall(
        self,
        *,
        zone: str = "public",
    ) -> CockpitActionResult:
        """
            Remove the cockpit service from ``zone`` in firewalld.
        """
        command = [
            "firewall-cmd", "--zone={}".format(zone),
            "--remove-service=cockpit", "--permanent",
        ]
        return self._dispatch(command=command, detail="cockpit firewall", operation="close_firewall")
