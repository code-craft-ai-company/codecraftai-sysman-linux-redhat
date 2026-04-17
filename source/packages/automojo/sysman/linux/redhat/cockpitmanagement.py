# automojo.sysman.linux.redhat.cockpitmanagement
#
# CockpitManager wraps the cockpit.socket service and port configuration
# through the ICommandRunner abstraction.  It delegates service state
# transitions to systemctl and firewalld service membership to firewall-cmd.

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.runners.base import ICommandRunner


class CockpitActionStatus(Enum):
    """
        Outcome status for cockpit management actions.
    """
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class CockpitManagerConfig:
    """
        Configuration for :class:`CockpitManager`.

        :param dry_run: When True, no commands execute.
        :param elevated: When True, write commands run elevated.
    """
    dry_run: bool = False
    elevated: bool = False


@dataclass
class CockpitActionResult:
    """
        Outcome of a cockpit management action.
    """
    status: CockpitActionStatus
    detail: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


class CockpitManager:
    """
        Facade for the cockpit web console.
    """

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[CockpitManagerConfig] = None,
    ) -> None:
        """
            :param runner: Command runner.
            :param config: Manager configuration.
        """
        if config is None:
            config = CockpitManagerConfig()
        self._runner: ICommandRunner = runner
        self._config: CockpitManagerConfig = config
        return

    @property
    def config(self) -> CockpitManagerConfig:
        """
            The active :class:`CockpitManagerConfig`.
        """
        return self._config

    def enable(self) -> CockpitActionResult:
        """
            Enable and start the ``cockpit.socket`` unit.
        """
        command = ["systemctl", "enable", "--now", "cockpit.socket"]
        return self._dispatch(command=command, detail="cockpit.socket")

    def disable(self) -> CockpitActionResult:
        """
            Disable and stop the ``cockpit.socket`` unit.
        """
        command = ["systemctl", "disable", "--now", "cockpit.socket"]
        return self._dispatch(command=command, detail="cockpit.socket")

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
        return self._dispatch(command=command, detail="cockpit firewall")

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
        return self._dispatch(command=command, detail="cockpit firewall")

    def _dispatch(
        self,
        *,
        command: list[str],
        detail: str,
    ) -> CockpitActionResult:
        """
            Dispatch a cockpit management command honouring dry-run.
        """
        if self._config.dry_run:
            return CockpitActionResult(
                status=CockpitActionStatus.DRY_RUN,
                detail=detail, command=command,
            )
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = CockpitActionStatus.SUCCESS if run_result.exit_code == 0 else CockpitActionStatus.FAILED
        return CockpitActionResult(
            status=status, detail=detail,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
