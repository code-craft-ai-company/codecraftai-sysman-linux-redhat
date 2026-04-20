# automojo.sysman.linux.redhat.subscriptionmanagement
#
# SubscriptionManager wraps subscription-manager through the ICommandRunner
# abstraction to register hosts, attach subscriptions, and manage repos.

from dataclasses import dataclass, field
from typing import Optional

from automojo.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


# Backwards-compatible alias.
SubscriptionActionStatus = ActionStatus


@dataclass
class SubscriptionManagerConfig(FeatureConfig):
    """
        Configuration for :class:`SubscriptionManager`.  Inherits
        ``dry_run`` and ``elevated`` from :class:`FeatureConfig`.
    """
    pass


@dataclass
class SubscriptionActionResult(FeatureActionResult):
    """
        Outcome of a subscription-manager action.  No feature-specific
        extras beyond the base fields.
    """
    pass


@dataclass
class SubscriptionStatus:
    """
        Parsed output of ``subscription-manager status``.
    """
    overall: str
    details: list[str] = field(default_factory=list)


class SubscriptionManager(BaseFeatureManager):
    """
        Facade for subscription-manager operations.
    """

    result_class = SubscriptionActionResult
    feature_name = "subscription-management"

    def register(
        self,
        *,
        username: Optional[str] = None,
        password: Optional[str] = None,
        activation_key: Optional[str] = None,
        org: Optional[str] = None,
        auto_attach: bool = False,
    ) -> SubscriptionActionResult:
        """
            Register the host with Red Hat Subscription Management.
        """
        command = ["subscription-manager", "register"]
        if activation_key is not None and org is not None:
            command.extend(["--activationkey", activation_key])
            command.extend(["--org", org])
        elif username is not None and password is not None:
            command.extend(["--username", username])
            command.extend(["--password", password])
        if auto_attach:
            command.append("--auto-attach")
        return self._dispatch(command=command, detail="register", operation="register")

    def unregister(self) -> SubscriptionActionResult:
        """
            Unregister the host.
        """
        command = ["subscription-manager", "unregister"]
        return self._dispatch(command=command, detail="unregister", operation="unregister")

    def attach(
        self,
        *,
        pool: Optional[str] = None,
        auto: bool = False,
    ) -> SubscriptionActionResult:
        """
            Attach a subscription pool or auto-attach.
        """
        command = ["subscription-manager", "attach"]
        if pool is not None:
            command.extend(["--pool", pool])
        if auto:
            command.append("--auto")
        return self._dispatch(command=command, detail="attach", operation="attach")

    def refresh(self) -> SubscriptionActionResult:
        """
            Refresh subscription entitlement data.
        """
        command = ["subscription-manager", "refresh"]
        return self._dispatch(command=command, detail="refresh", operation="refresh")

    def enable_repo(self, repo_id: str) -> SubscriptionActionResult:
        """
            Enable a Red Hat repository via subscription-manager.
        """
        command = ["subscription-manager", "repos", "--enable", repo_id]
        return self._dispatch(command=command, detail=repo_id, operation="enable_repo")

    def disable_repo(self, repo_id: str) -> SubscriptionActionResult:
        """
            Disable a Red Hat repository via subscription-manager.
        """
        command = ["subscription-manager", "repos", "--disable", repo_id]
        return self._dispatch(command=command, detail=repo_id, operation="disable_repo")

    def status(self) -> SubscriptionStatus:
        """
            Parse ``subscription-manager status`` output.
        """
        run_result = self._runner.run(
            ["subscription-manager", "status"], elevated=self._config.elevated,
        )
        overall = "Unknown"
        details: list[str] = []
        for raw in run_result.stdout.splitlines():
            line = raw.strip()
            if line.lower().startswith("overall status:"):
                _, _, value = line.partition(":")
                overall = value.strip()
            elif len(line) > 0 and not line.startswith("-") and "status" not in line.lower():
                details.append(line)
        status = SubscriptionStatus(overall=overall, details=details)
        return status
