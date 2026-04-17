# Tests for the SubscriptionManager facade.

from dataclasses import dataclass, field
from typing import Callable, Optional

from automojo import testbase

from automojo.sysman.linux.redhat.runners.models import RunResult
from automojo.sysman.linux.redhat.subscriptionmanagement import (
    SubscriptionActionStatus,
    SubscriptionManager,
    SubscriptionManagerConfig,
)


@dataclass
class _MockRunner:

    results: dict = field(default_factory=dict)
    default_result: Optional[RunResult] = None
    calls: list = field(default_factory=list)

    def run(
        self,
        args: list[str],
        *,
        elevated: bool = False,
        cwd: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
        timeout: Optional[float] = None,
        on_output: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        capture_output: bool = True,
        stdin: Optional[str] = None,
    ) -> RunResult:
        self.calls.append({"args": list(args)})
        key = tuple(args)
        if key in self.results:
            return self.results[key]
        if self.default_result is not None:
            return self.default_result
        return RunResult(exit_code=0, stdout="", stderr="", command=list(args))

    def add_aspect(self, aspect) -> None:
        return

    def remove_aspect(self, aspect) -> None:
        return

    def get_metrics(self):
        return None


def test_register_with_activation_key():
    """
        register() uses --activationkey/--org when both are provided.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["subscription-manager"],
    ))
    sm = SubscriptionManager(runner, SubscriptionManagerConfig(elevated=True))
    result = sm.register(activation_key="abc123", org="Default_Org", auto_attach=True)
    testbase.assert_equal(result.status, SubscriptionActionStatus.SUCCESS)
    args = runner.calls[-1]["args"]
    testbase.assert_true("--activationkey" in args)
    testbase.assert_true("--auto-attach" in args)
    return


def test_register_with_username_password():
    """
        register() uses --username/--password when no activation key is supplied.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["subscription-manager"],
    ))
    sm = SubscriptionManager(runner, SubscriptionManagerConfig(elevated=True))
    sm.register(username="user", password="pw")
    args = runner.calls[-1]["args"]
    testbase.assert_true("--username" in args)
    testbase.assert_true("--password" in args)
    return


def test_enable_repo_dispatches_subscription_manager():
    """
        enable_repo() runs ``subscription-manager repos --enable <id>``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["subscription-manager"],
    ))
    sm = SubscriptionManager(runner, SubscriptionManagerConfig(elevated=True))
    result = sm.enable_repo("rhel-9-for-x86_64-baseos-rpms")
    testbase.assert_equal(result.status, SubscriptionActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"],
        ["subscription-manager", "repos", "--enable", "rhel-9-for-x86_64-baseos-rpms"],
    )
    return


def test_status_parses_overall_status():
    """
        status() extracts the ``Overall Status`` value from output.
    """
    runner = _MockRunner()
    runner.results[("subscription-manager", "status")] = RunResult(
        exit_code=0,
        stdout="Overall Status: Current\n",
        stderr="",
        command=["subscription-manager"],
    )
    sm = SubscriptionManager(runner, SubscriptionManagerConfig())
    status = sm.status()
    testbase.assert_equal(status.overall, "Current")
    return
