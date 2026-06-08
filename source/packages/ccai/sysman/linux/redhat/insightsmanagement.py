# ccai.sysman.linux.redhat.insightsmanagement
#
# InsightsManager wraps the insights-client through the ICommandRunner
# abstraction to register, unregister, run checks, and schedule uploads.

from dataclasses import dataclass

from ccai.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


# Backwards-compatible alias.
InsightsActionStatus = ActionStatus


@dataclass
class InsightsManagerConfig(FeatureConfig):
    """
        Configuration for :class:`InsightsManager`.  Inherits
        ``dry_run`` and ``elevated`` from :class:`FeatureConfig`.
    """
    pass


@dataclass
class InsightsActionResult(FeatureActionResult):
    """
        Outcome of an insights-client action.  No feature-specific
        extras beyond the base fields.
    """
    pass


class InsightsManager(BaseFeatureManager):
    """
        Facade for the Red Hat Insights client.
    """

    result_class = InsightsActionResult
    feature_name = "insights-management"

    def register(self) -> InsightsActionResult:
        """
            Register the host with Red Hat Insights.
        """
        command = ["insights-client", "--register"]
        return self._dispatch(command=command, detail="register", operation="register")

    def unregister(self) -> InsightsActionResult:
        """
            Unregister the host from Red Hat Insights.
        """
        command = ["insights-client", "--unregister"]
        return self._dispatch(command=command, detail="unregister", operation="unregister")

    def status(self) -> InsightsActionResult:
        """
            Query insights-client status.
        """
        command = ["insights-client", "--status"]
        return self._dispatch(command=command, operation="status")

    def run_check(self) -> InsightsActionResult:
        """
            Run a compliance/insights check and upload the results.
        """
        command = ["insights-client"]
        return self._dispatch(command=command, detail="check", operation="run_check")
