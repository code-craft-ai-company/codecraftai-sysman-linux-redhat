# automojo.sysman.linux.redhat.bootmanagement
#
# BootManager wraps grub2 configuration and default kernel selection
# through the ICommandRunner abstraction.

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.runners.base import ICommandRunner


class BootActionStatus(Enum):
    """
        Outcome status for boot management actions.
    """
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class BootManagerConfig:
    """
        Configuration for :class:`BootManager`.

        :param dry_run: When True, no commands execute.
        :param elevated: When True, write commands run elevated.
    """
    dry_run: bool = False
    elevated: bool = False


@dataclass
class BootActionResult:
    """
        Outcome of a boot management action.
    """
    status: BootActionStatus
    detail: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


@dataclass
class BootEntry:
    """
        Summary of a grub2 boot entry.
    """
    index: int
    title: str


class BootManager:
    """
        Facade for grub2 and default kernel management.
    """

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[BootManagerConfig] = None,
    ) -> None:
        """
            :param runner: Command runner.
            :param config: Manager configuration.
        """
        if config is None:
            config = BootManagerConfig()
        self._runner: ICommandRunner = runner
        self._config: BootManagerConfig = config
        return

    @property
    def config(self) -> BootManagerConfig:
        """
            The active :class:`BootManagerConfig`.
        """
        return self._config

    def list_entries(self) -> list[BootEntry]:
        """
            List boot entries via ``grubby --info=ALL``.
        """
        run_result = self._runner.run(
            ["grubby", "--info=ALL"], elevated=self._config.elevated,
        )
        if run_result.exit_code != 0:
            return []
        entries: list[BootEntry] = []
        current: dict[str, str] = {}
        index = 0
        for raw in run_result.stdout.splitlines():
            line = raw.strip()
            if line.startswith("index="):
                if len(current) > 0:
                    entries.append(BootEntry(
                        index=int(current.get("index", "0")),
                        title=current.get("title", ""),
                    ))
                current = {"index": line.split("=", 1)[1]}
                index = int(current["index"])
            elif line.startswith("title="):
                current["title"] = line.split("=", 1)[1].strip('"')
        if len(current) > 0:
            entries.append(BootEntry(
                index=int(current.get("index", str(index))),
                title=current.get("title", ""),
            ))
        return entries

    def set_default(self, index: int) -> BootActionResult:
        """
            Set the default grub entry via ``grub2-set-default``.
        """
        command = ["grub2-set-default", str(index)]
        return self._dispatch(command=command, detail=str(index))

    def get_default(self) -> Optional[int]:
        """
            Return the configured default index or None.
        """
        run_result = self._runner.run(
            ["grub2-editenv", "list"], elevated=False,
        )
        if run_result.exit_code != 0:
            return None
        for raw in run_result.stdout.splitlines():
            line = raw.strip()
            if line.startswith("saved_entry="):
                value = line.split("=", 1)[1]
                try:
                    return int(value)
                except ValueError:
                    return None
        return None

    def regenerate_config(self, path: str = "/boot/grub2/grub.cfg") -> BootActionResult:
        """
            Rebuild the grub configuration via ``grub2-mkconfig``.
        """
        command = ["grub2-mkconfig", "-o", path]
        return self._dispatch(command=command, detail=path)

    def add_kernel_param(self, param: str) -> BootActionResult:
        """
            Add a kernel boot parameter via ``grubby --update-kernel=ALL``.
        """
        command = [
            "grubby", "--update-kernel=ALL", "--args={}".format(param),
        ]
        return self._dispatch(command=command, detail=param)

    def remove_kernel_param(self, param: str) -> BootActionResult:
        """
            Remove a kernel boot parameter via ``grubby --remove-args``.
        """
        command = [
            "grubby", "--update-kernel=ALL", "--remove-args={}".format(param),
        ]
        return self._dispatch(command=command, detail=param)

    def _dispatch(
        self,
        *,
        command: list[str],
        detail: str,
    ) -> BootActionResult:
        """
            Dispatch a boot management command honouring dry-run.
        """
        if self._config.dry_run:
            return BootActionResult(
                status=BootActionStatus.DRY_RUN,
                detail=detail, command=command,
            )
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = BootActionStatus.SUCCESS if run_result.exit_code == 0 else BootActionStatus.FAILED
        return BootActionResult(
            status=status, detail=detail,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
