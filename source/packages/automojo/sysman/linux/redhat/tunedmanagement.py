# automojo.sysman.linux.redhat.tunedmanagement
#
# TunedManager wraps tuned-adm through the ICommandRunner abstraction.

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.runners.base import ICommandRunner


class TunedActionStatus(Enum):
    """
        Outcome status for tuned-adm actions.
    """
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class TunedManagerConfig:
    """
        Configuration for :class:`TunedManager`.

        :param dry_run: When True, no commands execute.
        :param elevated: When True, tuned-adm runs elevated.
    """
    dry_run: bool = False
    elevated: bool = False


@dataclass
class TunedActionResult:
    """
        Outcome of a tuned-adm action.
    """
    status: TunedActionStatus
    detail: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


class TunedManager:
    """
        Facade for tuned profile management.
    """

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[TunedManagerConfig] = None,
    ) -> None:
        """
            :param runner: Command runner.
            :param config: Manager configuration.
        """
        if config is None:
            config = TunedManagerConfig()
        self._runner: ICommandRunner = runner
        self._config: TunedManagerConfig = config
        return

    @property
    def config(self) -> TunedManagerConfig:
        """
            The active :class:`TunedManagerConfig`.
        """
        return self._config

    def current_profile(self) -> Optional[str]:
        """
            Return the currently active profile via ``tuned-adm active``.
        """
        run_result = self._runner.run(
            ["tuned-adm", "active"], elevated=False,
        )
        if run_result.exit_code != 0:
            return None
        line = run_result.stdout.strip()
        marker = "Current active profile:"
        if line.startswith(marker):
            return line[len(marker):].strip()
        return None

    def list_profiles(self) -> list[str]:
        """
            Return the list of available profiles via ``tuned-adm list``.
        """
        run_result = self._runner.run(
            ["tuned-adm", "list"], elevated=False,
        )
        if run_result.exit_code != 0:
            return []
        profiles: list[str] = []
        for raw in run_result.stdout.splitlines():
            stripped = raw.strip()
            if not stripped.startswith("-"):
                continue
            tokens = stripped[1:].strip().split()
            if len(tokens) == 0:
                continue
            profiles.append(tokens[0])
        return profiles

    def set_profile(self, profile: str) -> TunedActionResult:
        """
            Switch to ``profile`` via ``tuned-adm profile``.
        """
        if self.current_profile() == profile:
            return TunedActionResult(
                status=TunedActionStatus.SKIPPED, detail=profile,
            )
        command = ["tuned-adm", "profile", profile]
        return self._dispatch(command=command, detail=profile)

    def recommend(self) -> Optional[str]:
        """
            Return the recommended profile via ``tuned-adm recommend``.
        """
        run_result = self._runner.run(
            ["tuned-adm", "recommend"], elevated=False,
        )
        if run_result.exit_code != 0:
            return None
        value = run_result.stdout.strip()
        if len(value) == 0:
            return None
        return value

    def _dispatch(
        self,
        *,
        command: list[str],
        detail: str,
    ) -> TunedActionResult:
        """
            Dispatch a tuned-adm command honouring dry-run.
        """
        if self._config.dry_run:
            return TunedActionResult(
                status=TunedActionStatus.DRY_RUN,
                detail=detail, command=command,
            )
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = TunedActionStatus.SUCCESS if run_result.exit_code == 0 else TunedActionStatus.FAILED
        return TunedActionResult(
            status=status, detail=detail,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
