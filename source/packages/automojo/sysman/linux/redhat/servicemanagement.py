# automojo.sysman.linux.redhat.servicemanagement
#
# ServiceManager that wraps systemctl and journalctl through the
# ICommandRunner abstraction.

import os

from dataclasses import dataclass
from typing import Optional

from automojo.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


ServiceActionStatus = ActionStatus


@dataclass
class ServiceManagerConfig(FeatureConfig):
    """
        Configuration for :class:`ServiceManager`.  Inherits ``dry_run``
        and ``elevated`` from :class:`FeatureConfig`.
    """
    pass


@dataclass
class ServiceInfo:
    """
        Active and enabled state for a service.
    """
    service_name: str
    active_state: str
    enabled_state: str


@dataclass
class ServiceActionResult(FeatureActionResult):
    """
        Result of a service management write operation.  Extends
        :class:`FeatureActionResult` with ``service_name``.
    """
    service_name: str = ""


class ServiceQueryError(Exception):
    """
        Raised when a query-like systemctl invocation fails for reasons other
        than returning state information.
    """

    def __init__(
        self,
        *,
        command: list[str],
        exit_code: int,
        stderr: str,
    ) -> None:
        self.command = command
        self.exit_code = exit_code
        self.stderr = stderr
        err_msg_lines = [
            "Service query command failed."
        ]
        err_msg_lines.append("    COMMAND: {}".format(" ".join(command)))
        err_msg_lines.append("    EXIT_CODE: {}".format(exit_code))
        err_msg_lines.append("    STDERR: {}".format(stderr))
        err_msg = os.linesep.join(err_msg_lines)
        super().__init__(err_msg)
        return


_SYSTEMCTL = "systemctl"
_JOURNALCTL = "journalctl"

_VALID_ACTIVE_STATES = {
    "active", "inactive", "failed", "activating", "deactivating",
    "reloading", "not-found",
}
_VALID_ENABLED_STATES = {
    "enabled", "disabled", "masked", "static", "alias",
    "indirect", "generated", "enabled-runtime", "not-found",
}


class ServiceManager(BaseFeatureManager):
    """
        High-level facade for systemd service management.
    """

    result_class = ServiceActionResult
    feature_name = "service-management"

    def start(self, name: str) -> ServiceActionResult:
        """
            Start a service unit.  Skipped when the unit is already active.
        """
        info = self.query(name)
        if info.active_state == "active":
            return ServiceActionResult(
                status=ServiceActionStatus.SKIPPED,
                service_name=name,
            )
        return self._dispatch(
            command=[_SYSTEMCTL, "start", name],
            detail=name,
            operation="start",
            service_name=name,
        )

    def stop(self, name: str) -> ServiceActionResult:
        """
            Stop a service unit.  Skipped when already inactive.
        """
        info = self.query(name)
        if info.active_state == "inactive":
            return ServiceActionResult(
                status=ServiceActionStatus.SKIPPED,
                service_name=name,
            )
        return self._dispatch(
            command=[_SYSTEMCTL, "stop", name],
            detail=name,
            operation="stop",
            service_name=name,
        )

    def restart(self, name: str) -> ServiceActionResult:
        """
            Restart a service unit.  Always attempts the restart.
        """
        return self._dispatch(
            command=[_SYSTEMCTL, "restart", name],
            detail=name,
            operation="restart",
            service_name=name,
        )

    def reload(self, name: str) -> ServiceActionResult:
        """
            Send reload signal to a running service.

            Returns a ``FAILED`` result when the service is not active.
        """
        info = self.query(name)
        if info.active_state != "active":
            return ServiceActionResult(
                status=ServiceActionStatus.FAILED,
                service_name=name,
                stderr="service is not active: {}".format(info.active_state),
            )
        return self._dispatch(
            command=[_SYSTEMCTL, "reload", name],
            detail=name,
            operation="reload",
            service_name=name,
        )

    def enable(self, name: str) -> ServiceActionResult:
        """
            Enable a service to start at boot.
        """
        info = self.query(name)
        if info.enabled_state == "enabled":
            return ServiceActionResult(
                status=ServiceActionStatus.SKIPPED,
                service_name=name,
            )
        return self._dispatch(
            command=[_SYSTEMCTL, "enable", name],
            detail=name,
            operation="enable",
            service_name=name,
        )

    def disable(self, name: str) -> ServiceActionResult:
        """
            Disable a service from starting at boot.
        """
        info = self.query(name)
        if info.enabled_state == "disabled":
            return ServiceActionResult(
                status=ServiceActionStatus.SKIPPED,
                service_name=name,
            )
        return self._dispatch(
            command=[_SYSTEMCTL, "disable", name],
            detail=name,
            operation="disable",
            service_name=name,
        )

    def mask(self, name: str) -> ServiceActionResult:
        """
            Mask a service unit so it cannot be started.
        """
        info = self.query(name)
        if info.enabled_state == "masked":
            return ServiceActionResult(
                status=ServiceActionStatus.SKIPPED,
                service_name=name,
            )
        return self._dispatch(
            command=[_SYSTEMCTL, "mask", name],
            detail=name,
            operation="mask",
            service_name=name,
        )

    def unmask(self, name: str) -> ServiceActionResult:
        """
            Unmask a service unit.
        """
        info = self.query(name)
        if info.enabled_state != "masked":
            return ServiceActionResult(
                status=ServiceActionStatus.SKIPPED,
                service_name=name,
            )
        return self._dispatch(
            command=[_SYSTEMCTL, "unmask", name],
            detail=name,
            operation="unmask",
            service_name=name,
        )

    def daemon_reload(self) -> ServiceActionResult:
        """
            Reload the systemd manager configuration.
        """
        return self._dispatch(
            command=[_SYSTEMCTL, "daemon-reload"],
            detail="<daemon>",
            operation="daemon_reload",
            service_name="<daemon>",
        )

    def query(self, name: str) -> ServiceInfo:
        """
            Query the active and enabled state of a service.

            :raises ServiceQueryError: When a query command fails unexpectedly.
        """
        active_state = self._query_state(
            command=[_SYSTEMCTL, "is-active", name],
            valid_states=_VALID_ACTIVE_STATES,
        )
        enabled_state = self._query_state(
            command=[_SYSTEMCTL, "is-enabled", name],
            valid_states=_VALID_ENABLED_STATES,
        )
        info = ServiceInfo(
            service_name=name,
            active_state=active_state,
            enabled_state=enabled_state,
        )
        return info

    def get_journal(self, name: str, lines: int = 50) -> str:
        """
            Return recent journal output for a service.

            :raises ServiceQueryError: When journalctl fails.
        """
        command = [
            _JOURNALCTL, "-u", name, "--no-pager", "-n", str(lines)
        ]
        run_result = self._runner.run(command, elevated=False)
        if run_result.exit_code != 0:
            raise ServiceQueryError(
                command=command,
                exit_code=run_result.exit_code,
                stderr=run_result.stderr,
            )
        return run_result.stdout

    def _query_state(
        self,
        *,
        command: list[str],
        valid_states: set[str],
    ) -> str:
        """
            Run a state-query command and return the reported state string.
        """
        run_result = self._runner.run(command, elevated=False)
        reported_state = run_result.stdout.strip()
        if len(reported_state) == 0:
            reported_state = run_result.stderr.strip()

        if reported_state in valid_states:
            return reported_state

        raise ServiceQueryError(
            command=command,
            exit_code=run_result.exit_code,
            stderr=run_result.stderr,
        )
