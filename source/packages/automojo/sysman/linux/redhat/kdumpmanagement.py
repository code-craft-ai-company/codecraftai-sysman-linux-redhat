# automojo.sysman.linux.redhat.kdumpmanagement
#
# KdumpManager wraps the kdump service and configuration file through the
# ICommandRunner abstraction.

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.runners.base import ICommandRunner


class KdumpActionStatus(Enum):
    """
        Outcome status for kdump actions.
    """
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class KdumpManagerConfig:
    """
        Configuration for :class:`KdumpManager`.

        :param dry_run: When True, no commands execute.
        :param elevated: When True, write commands run elevated.
        :param config_path: Path to kdump.conf.
    """
    dry_run: bool = False
    elevated: bool = False
    config_path: str = "/etc/kdump.conf"


@dataclass
class KdumpActionResult:
    """
        Outcome of a kdump management action.
    """
    status: KdumpActionStatus
    detail: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


@dataclass
class KdumpStatus:
    """
        Summary of the kdump service state.
    """
    active: bool
    enabled: bool


class KdumpManager:
    """
        Facade for kdump service and configuration management.
    """

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[KdumpManagerConfig] = None,
    ) -> None:
        """
            :param runner: Command runner.
            :param config: Manager configuration.
        """
        if config is None:
            config = KdumpManagerConfig()
        self._runner: ICommandRunner = runner
        self._config: KdumpManagerConfig = config
        return

    @property
    def config(self) -> KdumpManagerConfig:
        """
            The active :class:`KdumpManagerConfig`.
        """
        return self._config

    def query_status(self) -> KdumpStatus:
        """
            Return whether kdump.service is active and enabled at boot.
        """
        active = self._runner.run(
            ["systemctl", "is-active", "kdump.service"], elevated=False,
        )
        enabled = self._runner.run(
            ["systemctl", "is-enabled", "kdump.service"], elevated=False,
        )
        status = KdumpStatus(
            active=active.stdout.strip() == "active",
            enabled=enabled.stdout.strip() == "enabled",
        )
        return status

    def enable(self) -> KdumpActionResult:
        """
            Enable and start ``kdump.service``.
        """
        command = ["systemctl", "enable", "--now", "kdump.service"]
        return self._dispatch(command=command, detail="kdump.service")

    def disable(self) -> KdumpActionResult:
        """
            Disable and stop ``kdump.service``.
        """
        command = ["systemctl", "disable", "--now", "kdump.service"]
        return self._dispatch(command=command, detail="kdump.service")

    def set_config_directive(
        self,
        directive: str,
        value: str,
    ) -> KdumpActionResult:
        """
            Set ``<directive> <value>`` in kdump.conf.
        """
        current = self._read(path=self._config.config_path)
        new_content = _apply_directive(
            content=current, directive=directive, value=value,
        )
        if new_content == current:
            return KdumpActionResult(
                status=KdumpActionStatus.SKIPPED, detail=directive,
            )
        command = ["tee", self._config.config_path]
        if self._config.dry_run:
            return KdumpActionResult(
                status=KdumpActionStatus.DRY_RUN,
                detail=directive, command=command,
            )
        run_result = self._runner.run(
            command, elevated=self._config.elevated, stdin=new_content,
        )
        status = KdumpActionStatus.SUCCESS if run_result.exit_code == 0 else KdumpActionStatus.FAILED
        return KdumpActionResult(
            status=status, detail=directive,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )

    def _read(self, *, path: str) -> str:
        """
            Read kdump.conf via ``cat``, returning empty on missing.
        """
        run_result = self._runner.run(["cat", path], elevated=False)
        if run_result.exit_code != 0:
            return ""
        return run_result.stdout

    def _dispatch(
        self,
        *,
        command: list[str],
        detail: str,
    ) -> KdumpActionResult:
        """
            Dispatch a kdump command honouring dry-run.
        """
        if self._config.dry_run:
            return KdumpActionResult(
                status=KdumpActionStatus.DRY_RUN,
                detail=detail, command=command,
            )
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = KdumpActionStatus.SUCCESS if run_result.exit_code == 0 else KdumpActionStatus.FAILED
        return KdumpActionResult(
            status=status, detail=detail,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )


def _apply_directive(
    *,
    content: str,
    directive: str,
    value: str,
) -> str:
    """
        Replace or append ``<directive> <value>`` in the kdump.conf body.
    """
    new_line = "{} {}".format(directive, value)
    lines = content.splitlines()
    updated: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if len(stripped) == 0 or stripped.startswith("#"):
            updated.append(line)
            continue
        tokens = stripped.split(None, 1)
        if len(tokens) == 0:
            updated.append(line)
            continue
        if tokens[0] == directive:
            updated.append(new_line)
            replaced = True
            continue
        updated.append(line)
    if not replaced:
        updated.append(new_line)
    body = "\n".join(updated)
    if not body.endswith("\n"):
        body = body + "\n"
    return body
