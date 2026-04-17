# automojo.sysman.linux.redhat.subscriptionmanagement
#
# SubscriptionManager wraps subscription-manager through the ICommandRunner
# abstraction to register hosts, attach subscriptions, and manage repos.

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.runners.base import ICommandRunner


class SubscriptionActionStatus(Enum):
    """
        Outcome status for subscription manager actions.
    """
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class SubscriptionManagerConfig:
    """
        Configuration for :class:`SubscriptionManager`.

        :param dry_run: When True, no commands execute.
        :param elevated: When True, subscription-manager runs elevated.
    """
    dry_run: bool = False
    elevated: bool = False


@dataclass
class SubscriptionActionResult:
    """
        Outcome of a subscription-manager action.
    """
    status: SubscriptionActionStatus
    detail: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


@dataclass
class SubscriptionStatus:
    """
        Parsed output of ``subscription-manager status``.
    """
    overall: str
    details: list[str] = field(default_factory=list)


class SubscriptionManager:
    """
        Facade for subscription-manager operations.
    """

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[SubscriptionManagerConfig] = None,
    ) -> None:
        """
            :param runner: Command runner.
            :param config: Manager configuration.
        """
        if config is None:
            config = SubscriptionManagerConfig()
        self._runner: ICommandRunner = runner
        self._config: SubscriptionManagerConfig = config
        return

    @property
    def config(self) -> SubscriptionManagerConfig:
        """
            The active :class:`SubscriptionManagerConfig`.
        """
        return self._config

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
        return self._dispatch(command=command, detail="register")

    def unregister(self) -> SubscriptionActionResult:
        """
            Unregister the host.
        """
        command = ["subscription-manager", "unregister"]
        return self._dispatch(command=command, detail="unregister")

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
        return self._dispatch(command=command, detail="attach")

    def refresh(self) -> SubscriptionActionResult:
        """
            Refresh subscription entitlement data.
        """
        command = ["subscription-manager", "refresh"]
        return self._dispatch(command=command, detail="refresh")

    def enable_repo(self, repo_id: str) -> SubscriptionActionResult:
        """
            Enable a Red Hat repository via subscription-manager.
        """
        command = ["subscription-manager", "repos", "--enable", repo_id]
        return self._dispatch(command=command, detail=repo_id)

    def disable_repo(self, repo_id: str) -> SubscriptionActionResult:
        """
            Disable a Red Hat repository via subscription-manager.
        """
        command = ["subscription-manager", "repos", "--disable", repo_id]
        return self._dispatch(command=command, detail=repo_id)

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

    def _dispatch(
        self,
        *,
        command: list[str],
        detail: str,
    ) -> SubscriptionActionResult:
        """
            Dispatch a subscription-manager command honouring dry-run.
        """
        if self._config.dry_run:
            return SubscriptionActionResult(
                status=SubscriptionActionStatus.DRY_RUN,
                detail=detail, command=command,
            )
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = SubscriptionActionStatus.SUCCESS if run_result.exit_code == 0 else SubscriptionActionStatus.FAILED
        return SubscriptionActionResult(
            status=status, detail=detail,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
