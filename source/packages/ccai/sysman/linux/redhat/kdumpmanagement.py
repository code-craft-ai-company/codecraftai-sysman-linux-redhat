# ccai.sysman.linux.redhat.kdumpmanagement
#
# KdumpManager wraps the kdump service and configuration file.  The
# kdump.service lifecycle is delegated to ServiceManager (Part C seam);
# the crashkernel= kernel-command-line directive is delegated to
# BootManager (Part C seam).  Direct systemctl / grubby calls are no
# longer spelled here.

from dataclasses import dataclass
from typing import Optional

from ccai.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


# Backwards-compatible alias.
KdumpActionStatus = ActionStatus


def _as_kdump_result(base_result: FeatureActionResult) -> "KdumpActionResult":
    """
        Convert any :class:`FeatureActionResult` (e.g. one produced by
        :class:`ServiceManager`) into a :class:`KdumpActionResult`.
        Preserves every base field.
    """
    return KdumpActionResult(
        status=base_result.status,
        detail=base_result.detail,
        exit_code=base_result.exit_code,
        stdout=base_result.stdout,
        stderr=base_result.stderr,
        command=base_result.command,
    )


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
        Service lifecycle (enable / disable / query) is delegated to
        :class:`ServiceManager`; the ``crashkernel=`` kernel command-
        line is delegated to :class:`BootManager`.
    """

    result_class = KdumpActionResult
    feature_name = "kdump-management"

    def __init__(
        self,
        runner,
        config: Optional[KdumpManagerConfig] = None,
        *,
        service_manager=None,
        boot_manager=None,
        report=None,
    ) -> None:
        """
            :param runner: Command runner.
            :param config: Manager configuration.
            :param service_manager: Optional pre-built :class:`ServiceManager`.
                                    When None, one is constructed with the
                                    same runner and dry-run/elevated state.
            :param boot_manager: Optional pre-built :class:`BootManager`.
                                 When None, one is constructed with the
                                 same runner and dry-run/elevated state.
            :param report: Optional :class:`MaintenanceReport` for auto-
                           emitting action records.
        """
        super().__init__(runner=runner, config=config, report=report)
        if service_manager is None:
            from ccai.sysman.linux.redhat.servicemanagement import (
                ServiceManager,
                ServiceManagerConfig,
            )
            service_manager = ServiceManager(
                runner,
                ServiceManagerConfig(
                    dry_run=self._config.dry_run,
                    elevated=self._config.elevated,
                ),
            )
        if boot_manager is None:
            from ccai.sysman.linux.redhat.bootmanagement import (
                BootManager,
                BootManagerConfig,
            )
            boot_manager = BootManager(
                runner,
                BootManagerConfig(
                    dry_run=self._config.dry_run,
                    elevated=self._config.elevated,
                ),
            )
        self._service_manager = service_manager
        self._boot_manager = boot_manager
        return

    def query_status(self) -> KdumpStatus:
        """
            Return whether kdump.service is active and enabled at boot.
            Delegates the two systemctl queries to :class:`ServiceManager`.
        """
        info = self._service_manager.query("kdump.service")
        status = KdumpStatus(
            active=info.active_state == "active",
            enabled=info.enabled_state == "enabled",
        )
        return status

    def enable(self) -> KdumpActionResult:
        """
            Enable and start ``kdump.service`` via :class:`ServiceManager`.
        """
        enable_res = self._service_manager.enable("kdump.service")
        if enable_res.status == ActionStatus.FAILED:
            return _as_kdump_result(enable_res)
        start_res = self._service_manager.start("kdump.service")
        return _as_kdump_result(start_res)

    def disable(self) -> KdumpActionResult:
        """
            Stop and disable ``kdump.service`` via :class:`ServiceManager`.
        """
        stop_res = self._service_manager.stop("kdump.service")
        if stop_res.status == ActionStatus.FAILED:
            return _as_kdump_result(stop_res)
        disable_res = self._service_manager.disable("kdump.service")
        return _as_kdump_result(disable_res)

    def set_crashkernel(self, value: str) -> KdumpActionResult:
        """
            Set the ``crashkernel=`` kernel boot parameter via
            :class:`BootManager`.  New in Part C: the seam specified in
            the kdump-management spec requirements is now wired.
        """
        boot_res = self._boot_manager.add_kernel_param("crashkernel={}".format(value))
        return _as_kdump_result(boot_res)

    def remove_crashkernel(self, value: str) -> KdumpActionResult:
        """
            Remove the ``crashkernel=`` kernel boot parameter via
            :class:`BootManager`.
        """
        boot_res = self._boot_manager.remove_kernel_param(
            "crashkernel={}".format(value),
        )
        return _as_kdump_result(boot_res)

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
