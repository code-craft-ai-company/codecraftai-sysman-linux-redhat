# automojo.sysman.linux.redhat.kdumpmanagement
#
# KdumpManager wraps the kdump service and configuration file through the
# ICommandRunner abstraction.

from dataclasses import dataclass

from automojo.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


# Backwards-compatible alias.
KdumpActionStatus = ActionStatus


@dataclass
class KdumpManagerConfig(FeatureConfig):
    """
        Configuration for :class:`KdumpManager`.  Inherits ``dry_run``
        and ``elevated`` from :class:`FeatureConfig`.

        :param config_path: Path to kdump.conf.
    """
    config_path: str = "/etc/kdump.conf"


@dataclass
class KdumpActionResult(FeatureActionResult):
    """
        Outcome of a kdump management action.  No feature-specific
        extras beyond the base fields.
    """
    pass


@dataclass
class KdumpStatus:
    """
        Summary of the kdump service state.
    """
    active: bool
    enabled: bool


class KdumpManager(BaseFeatureManager):
    """
        Facade for kdump service and configuration management.
    """

    result_class = KdumpActionResult
    feature_name = "kdump-management"

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
        return self._dispatch(command=command, detail="kdump.service", operation="enable")

    def disable(self) -> KdumpActionResult:
        """
            Disable and stop ``kdump.service``.
        """
        command = ["systemctl", "disable", "--now", "kdump.service"]
        return self._dispatch(command=command, detail="kdump.service", operation="disable")

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
        return self._dispatch(
            command=["tee", self._config.config_path],
            detail=directive,
            stdin=new_content,
            operation="set_config_directive",
        )

    def _read(self, *, path: str) -> str:
        """
            Read kdump.conf via ``cat``, returning empty on missing.
        """
        run_result = self._runner.run(["cat", path], elevated=False)
        if run_result.exit_code != 0:
            return ""
        return run_result.stdout


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
