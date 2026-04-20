# automojo.sysman.linux.redhat.bootmanagement
#
# BootManager wraps grub2 configuration and default kernel selection
# through the ICommandRunner abstraction.

from dataclasses import dataclass
from typing import Optional

from automojo.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


# Backwards-compatible alias.
BootActionStatus = ActionStatus


@dataclass
class BootManagerConfig(FeatureConfig):
    """
        Configuration for :class:`BootManager`.  Inherits ``dry_run``
        and ``elevated`` from :class:`FeatureConfig`.
    """
    pass


@dataclass
class BootActionResult(FeatureActionResult):
    """
        Outcome of a boot management action.  No feature-specific
        extras beyond the base fields.
    """
    pass


@dataclass
class BootEntry:
    """
        Summary of a grub2 boot entry.
    """
    index: int
    title: str


class BootManager(BaseFeatureManager):
    """
        Facade for grub2 and default kernel management.
    """

    result_class = BootActionResult
    feature_name = "boot-management"

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
        return self._dispatch(command=command, detail=str(index), operation="set_default")

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
        return self._dispatch(command=command, detail=path, operation="regenerate_config")

    def add_kernel_param(self, param: str) -> BootActionResult:
        """
            Add a kernel boot parameter via ``grubby --update-kernel=ALL``.
        """
        command = [
            "grubby", "--update-kernel=ALL", "--args={}".format(param),
        ]
        return self._dispatch(command=command, detail=param, operation="add_kernel_param")

    def remove_kernel_param(self, param: str) -> BootActionResult:
        """
            Remove a kernel boot parameter via ``grubby --remove-args``.
        """
        command = [
            "grubby", "--update-kernel=ALL", "--remove-args={}".format(param),
        ]
        return self._dispatch(command=command, detail=param, operation="remove_kernel_param")
