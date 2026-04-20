# automojo.sysman.linux.redhat.tunedmanagement
#
# TunedManager wraps tuned-adm through the ICommandRunner abstraction.

from dataclasses import dataclass
from typing import Optional

from automojo.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


# Backwards-compatible alias.
TunedActionStatus = ActionStatus


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
        Facade for tuned profile management.
    """

    result_class = TunedActionResult
    feature_name = "tuned-management"

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
