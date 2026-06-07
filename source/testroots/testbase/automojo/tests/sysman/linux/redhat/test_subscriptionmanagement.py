# Tests for the SubscriptionManager facade.

from dataclasses import dataclass, field
from typing import Callable, Optional

from ccai import testbase

from ccai.sysman.linux.redhat.runners.models import RunResult
from ccai.sysman.linux.redhat.subscriptionmanagement import (
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
        self.calls.append({"args": list(args), "stdin": stdin})
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


# ---------------------------------------------------------------------
# register(credential=...) — Part C credential routing
# ---------------------------------------------------------------------


class _StubCredential:
    """
        Minimal duck-type of codecraftai-credentials' BasicCredential.
    """

    def __init__(self, *, username: str, password: str) -> None:
        self.username = username
        self.password = password
        return


def test_register_credential_path_puts_password_on_stdin():
    """
        Preferred credential path: password travels on runner's stdin
        and NEVER appears on argv.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["subscription-manager"],
    ))
    sm = SubscriptionManager(runner, SubscriptionManagerConfig(elevated=True))
    result = sm.register(
        credential=_StubCredential(username="alice", password="s3cret"),
    )
    testbase.assert_equal(result.status, SubscriptionActionStatus.SUCCESS)
    reg_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["subscription-manager", "register"]
    ]
    testbase.assert_equal(len(reg_calls), 1)
    argv = reg_calls[0]["args"]
    testbase.assert_true("--username" in argv)
    testbase.assert_true("alice" in argv)
    testbase.assert_true("--password" not in argv)
    testbase.assert_true("s3cret" not in argv)
    testbase.assert_equal(reg_calls[0].get("stdin"), "s3cret")
    return


def test_register_legacy_username_password_still_works():
    """
        Backward-compatibility: the old username/password argv-path
        continues to work when no credential is supplied.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["subscription-manager"],
    ))
    sm = SubscriptionManager(runner, SubscriptionManagerConfig(elevated=True))
    result = sm.register(username="alice", password="s3cret")
    testbase.assert_equal(result.status, SubscriptionActionStatus.SUCCESS)
    reg_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["subscription-manager", "register"]
    ]
    argv = reg_calls[0]["args"]
    # Legacy path: argv DOES contain the password (deliberately left
    # intact for backward compat).
    testbase.assert_true("--password" in argv)
    testbase.assert_true("s3cret" in argv)
    testbase.assert_equal(reg_calls[0].get("stdin"), None)
    return


def test_register_activation_key_path_overrides_credential():
    """
        Activation-key path takes precedence over a supplied credential.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["subscription-manager"],
    ))
    sm = SubscriptionManager(runner, SubscriptionManagerConfig(elevated=True))
    sm.register(
        activation_key="AK-1234",
        org="example-org",
        credential=_StubCredential(username="alice", password="s3cret"),
    )
    reg_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["subscription-manager", "register"]
    ]
    argv = reg_calls[0]["args"]
    testbase.assert_true("--activationkey" in argv)
    testbase.assert_true("AK-1234" in argv)
    testbase.assert_true("--org" in argv)
    testbase.assert_true("example-org" in argv)
    testbase.assert_true("--username" not in argv)
    testbase.assert_true("s3cret" not in argv)
    return
