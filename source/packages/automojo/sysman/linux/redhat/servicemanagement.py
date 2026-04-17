# automojo.sysman.linux.redhat.servicemanagement
#
# ServiceManager that wraps systemctl and journalctl through the
# ICommandRunner abstraction.

import os

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.runners.base import ICommandRunner
from automojo.sysman.linux.redhat.runners.models import RunResult


class ServiceActionStatus(Enum):
    """
        Outcome status for service action operations.
    """
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class ServiceManagerConfig:
    """
        Configuration for :class:`ServiceManager`.

        :param dry_run: When True, no commands are executed.
        :param elevated: When True, write commands run with elevated privileges.
    """

    dry_run: bool = False
    elevated: bool = False


@dataclass
class ServiceInfo:
    """
        Active and enabled state for a service.

        :param service_name: Service unit name.
        :param active_state: Current active state string from systemctl.
        :param enabled_state: Current enabled state string from systemctl.
    """

    service_name: str
    active_state: str
    enabled_state: str


@dataclass
class ServiceActionResult:
    """
        Result of a service management write operation.

        :param status: Outcome of the action.
        :param service_name: Service the action targeted.
        :param exit_code: Raw exit code from the command runner.
        :param stdout: Standard output from the runner.
        :param stderr: Standard error from the runner.
        :param command: The command that was (or would have been) executed.
    """

    status: ServiceActionStatus
    service_name: str
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


class ServiceQueryError(Exception):
    """
        Raised when a query-like systemctl invocation fails for reasons other
        than returning state information.

        :param command: The command that was executed.
        :param exit_code: The exit code returned by the runner.
        :param stderr: Standard error captured from the runner.
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


class ServiceManager:
    """
        High-level facade for systemd service management.  Delegates every
        system call to the injected :class:`ICommandRunner`.
    """

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[ServiceManagerConfig] = None,
    ) -> None:
        """
            :param runner: Runner used for command execution.
            :param config: Service manager configuration.
        """
        if config is None:
            config = ServiceManagerConfig()
        self._runner: ICommandRunner = runner
        self._config: ServiceManagerConfig = config
        return

    @property
    def config(self) -> ServiceManagerConfig:
        """
            The active :class:`ServiceManagerConfig`.
        """
        return self._config

    @property
    def runner(self) -> ICommandRunner:
        """
            The injected :class:`ICommandRunner`.
        """
        return self._runner

    def start(self, name: str) -> ServiceActionResult:
        """
            Start a service unit.  Skipped when the unit is already active.
        """
        info = self.query(name)
        if info.active_state == "active":
            skipped = ServiceActionResult(
                status=ServiceActionStatus.SKIPPED,
                service_name=name,
            )
            return skipped
        command = [_SYSTEMCTL, "start", name]
        action_result = self._issue_write(name=name, command=command)
        return action_result

    def stop(self, name: str) -> ServiceActionResult:
        """
            Stop a service unit.  Skipped when already inactive.
        """
        info = self.query(name)
        if info.active_state == "inactive":
            skipped = ServiceActionResult(
                status=ServiceActionStatus.SKIPPED,
                service_name=name,
            )
            return skipped
        command = [_SYSTEMCTL, "stop", name]
        action_result = self._issue_write(name=name, command=command)
        return action_result

    def restart(self, name: str) -> ServiceActionResult:
        """
            Restart a service unit.  Always attempts the restart.
        """
        command = [_SYSTEMCTL, "restart", name]
        action_result = self._issue_write(name=name, command=command)
        return action_result

    def reload(self, name: str) -> ServiceActionResult:
        """
            Send reload signal to a running service.

            Returns a ``FAILED`` result when the service is not active.
        """
        info = self.query(name)
        if info.active_state != "active":
            failed = ServiceActionResult(
                status=ServiceActionStatus.FAILED,
                service_name=name,
                stderr="service is not active: {}".format(info.active_state),
            )
            return failed
        command = [_SYSTEMCTL, "reload", name]
        action_result = self._issue_write(name=name, command=command)
        return action_result

    def enable(self, name: str) -> ServiceActionResult:
        """
            Enable a service to start at boot.
        """
        info = self.query(name)
        if info.enabled_state == "enabled":
            skipped = ServiceActionResult(
                status=ServiceActionStatus.SKIPPED,
                service_name=name,
            )
            return skipped
        command = [_SYSTEMCTL, "enable", name]
        action_result = self._issue_write(name=name, command=command)
        return action_result

    def disable(self, name: str) -> ServiceActionResult:
        """
            Disable a service from starting at boot.
        """
        info = self.query(name)
        if info.enabled_state == "disabled":
            skipped = ServiceActionResult(
                status=ServiceActionStatus.SKIPPED,
                service_name=name,
            )
            return skipped
        command = [_SYSTEMCTL, "disable", name]
        action_result = self._issue_write(name=name, command=command)
        return action_result

    def mask(self, name: str) -> ServiceActionResult:
        """
            Mask a service unit so it cannot be started.
        """
        info = self.query(name)
        if info.enabled_state == "masked":
            skipped = ServiceActionResult(
                status=ServiceActionStatus.SKIPPED,
                service_name=name,
            )
            return skipped
        command = [_SYSTEMCTL, "mask", name]
        action_result = self._issue_write(name=name, command=command)
        return action_result

    def unmask(self, name: str) -> ServiceActionResult:
        """
            Unmask a service unit.
        """
        info = self.query(name)
        if info.enabled_state != "masked":
            skipped = ServiceActionResult(
                status=ServiceActionStatus.SKIPPED,
                service_name=name,
            )
            return skipped
        command = [_SYSTEMCTL, "unmask", name]
        action_result = self._issue_write(name=name, command=command)
        return action_result

    def daemon_reload(self) -> ServiceActionResult:
        """
            Reload the systemd manager configuration.
        """
        command = [_SYSTEMCTL, "daemon-reload"]
        action_result = self._issue_write(name="<daemon>", command=command)
        return action_result

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

    def _issue_write(
        self,
        *,
        name: str,
        command: list[str],
    ) -> ServiceActionResult:
        """
            Execute a write command honouring the dry-run configuration.
        """
        if self._config.dry_run:
            dry = ServiceActionResult(
                status=ServiceActionStatus.DRY_RUN,
                service_name=name,
                command=command,
            )
            return dry
        run_result = self._runner.run(command, elevated=self._config.elevated)
        result = self._result_from_run(name=name, run_result=run_result)
        return result

    def _query_state(
        self,
        *,
        command: list[str],
        valid_states: set[str],
    ) -> str:
        """
            Run a state-query command and return the reported state string.
            Non-zero exit codes are informational for state queries; any
            unrecognised state raises :class:`ServiceQueryError`.
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

    def _result_from_run(
        self,
        *,
        name: str,
        run_result: RunResult,
    ) -> ServiceActionResult:
        """
            Map a :class:`RunResult` to a :class:`ServiceActionResult`.
        """
        if run_result.exit_code == 0:
            status = ServiceActionStatus.SUCCESS
        else:
            status = ServiceActionStatus.FAILED
        action_result = ServiceActionResult(
            status=status,
            service_name=name,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
        return action_result
