# ccai.sysman.linux.redhat.tunedmanagement
#
# TunedManager wraps tuned-adm through the ICommandRunner abstraction.
# The ``tuned.service`` unit lifecycle is delegated to ServiceManager
# (Part C seam).

from dataclasses import dataclass
from typing import Optional

from ccai.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


# Backwards-compatible alias.
TunedActionStatus = ActionStatus


def _as_tuned_result(base_result: FeatureActionResult) -> "TunedActionResult":
    """
        Convert a :class:`FeatureActionResult` produced by a delegated
        :class:`ServiceManager` call into a :class:`TunedActionResult`,
        preserving every base field.
    """
    return TunedActionResult(
        status=base_result.status,
        detail=base_result.detail,
        exit_code=base_result.exit_code,
        stdout=base_result.stdout,
        stderr=base_result.stderr,
        command=base_result.command,
    )


@dataclass
class TunedManagerConfig(FeatureConfig):
    """
        Configuration for :class:`TunedManager`.  Inherits ``dry_run``
        and ``elevated`` from :class:`FeatureConfig`.
    """
    pass


@dataclass
class TunedActionResult(FeatureActionResult):
    """
        Outcome of a tuned-adm action.  No feature-specific extras.
    """
    pass


class TunedManager(BaseFeatureManager):
    """
        Facade for tuned profile management.  The ``tuned.service``
        unit lifecycle (enable / disable / query) is delegated to
        :class:`ServiceManager`.
    """

    result_class = TunedActionResult
    feature_name = "tuned-management"

    def __init__(
        self,
        runner,
        config: Optional[TunedManagerConfig] = None,
        *,
        service_manager=None,
        report=None,
    ) -> None:
        """
            :param runner: Command runner.
            :param config: Manager configuration.
            :param service_manager: Optional pre-built :class:`ServiceManager`.
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
        self._service_manager = service_manager
        return

    def enable(self) -> TunedActionResult:
        """
            Enable and start ``tuned.service`` via :class:`ServiceManager`.
        """
        enable_res = self._service_manager.enable("tuned.service")
        if enable_res.status == ActionStatus.FAILED:
            return _as_tuned_result(enable_res)
        start_res = self._service_manager.start("tuned.service")
        return _as_tuned_result(start_res)

    def disable(self) -> TunedActionResult:
        """
            Stop and disable ``tuned.service`` via :class:`ServiceManager`.
        """
        stop_res = self._service_manager.stop("tuned.service")
        if stop_res.status == ActionStatus.FAILED:
            return _as_tuned_result(stop_res)
        disable_res = self._service_manager.disable("tuned.service")
        return _as_tuned_result(disable_res)

    def is_active(self) -> bool:
        """
            Return True when tuned.service is reported as active by
            :class:`ServiceManager`.
        """
        info = self._service_manager.query("tuned.service")
        return info.active_state == "active"

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
        return self._dispatch(command=command, detail=profile, operation="set_profile")

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
