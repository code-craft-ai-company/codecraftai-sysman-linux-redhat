# automojo.sysman.linux.redhat.insightsmanagement
#
# InsightsManager wraps the insights-client through the ICommandRunner
# abstraction to register, unregister, run checks, and schedule uploads.

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.runners.base import ICommandRunner


class InsightsActionStatus(Enum):
    """
        Outcome status for insights-client actions.
    """
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class InsightsManagerConfig:
    """
        Configuration for :class:`InsightsManager`.

        :param dry_run: When True, no commands execute.
        :param elevated: When True, insights-client runs elevated.
    """
    dry_run: bool = False
    elevated: bool = False


@dataclass
class InsightsActionResult:
    """
        Outcome of an insights-client action.
    """
    status: InsightsActionStatus
    detail: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


class InsightsManager:
    """
        Facade for the Red Hat Insights client.
    """

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[InsightsManagerConfig] = None,
    ) -> None:
        """
            :param runner: Command runner.
            :param config: Manager configuration.
        """
        if config is None:
            config = InsightsManagerConfig()
        self._runner: ICommandRunner = runner
        self._config: InsightsManagerConfig = config
        return

    @property
    def config(self) -> InsightsManagerConfig:
        """
            The active :class:`InsightsManagerConfig`.
        """
        return self._config

    def register(self) -> InsightsActionResult:
        """
            Register the host with Red Hat Insights.
        """
        command = ["insights-client", "--register"]
        return self._dispatch(command=command, detail="register")

    def unregister(self) -> InsightsActionResult:
        """
            Unregister the host from Red Hat Insights.
        """
        command = ["insights-client", "--unregister"]
        return self._dispatch(command=command, detail="unregister")

    def status(self) -> InsightsActionResult:
        """
            Query insights-client status.
        """
        command = ["insights-client", "--status"]
        if self._config.dry_run:
            return InsightsActionResult(
                status=InsightsActionStatus.DRY_RUN,
                command=command,
            )
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = InsightsActionStatus.SUCCESS if run_result.exit_code == 0 else InsightsActionStatus.FAILED
        return InsightsActionResult(
            status=status,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )

    def run_check(self) -> InsightsActionResult:
        """
            Run a compliance/insights check and upload the results.
        """
        command = ["insights-client"]
        return self._dispatch(command=command, detail="check")

    def _dispatch(
        self,
        *,
        command: list[str],
        detail: str,
    ) -> InsightsActionResult:
        """
            Dispatch an insights-client command honouring dry-run.
        """
        if self._config.dry_run:
            return InsightsActionResult(
                status=InsightsActionStatus.DRY_RUN,
                detail=detail, command=command,
            )
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = InsightsActionStatus.SUCCESS if run_result.exit_code == 0 else InsightsActionStatus.FAILED
        return InsightsActionResult(
            status=status, detail=detail,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
