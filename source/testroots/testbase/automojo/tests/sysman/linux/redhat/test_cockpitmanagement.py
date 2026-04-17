# Tests for CockpitManager.

from dataclasses import dataclass, field
from typing import Optional

from automojo import testbase

from automojo.sysman.linux.redhat.cockpitmanagement import (
    CockpitActionStatus,
    CockpitManager,
    CockpitManagerConfig,
)
from automojo.sysman.linux.redhat.runners.models import RunResult


@dataclass
class _MockRunner:
    results: dict = field(default_factory=dict)
    default_result: Optional[RunResult] = None
    calls: list = field(default_factory=list)

    def run(self, args, *, elevated=False, cwd=None, env=None, timeout=None,
            on_output=None, on_error=None, capture_output=True, stdin=None):
        self.calls.append({"args": list(args)})
        key = tuple(args)
        if key in self.results:
            return self.results[key]
        if self.default_result is not None:
            return self.default_result
        return RunResult(exit_code=0, stdout="", stderr="", command=list(args))

    def add_aspect(self, aspect): return
    def remove_aspect(self, aspect): return
    def get_metrics(self): return None


def test_enable_dispatches_systemctl_enable():
    """
        enable() runs ``systemctl enable --now cockpit.socket``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["systemctl"],
    ))
    mgr = CockpitManager(runner, CockpitManagerConfig(elevated=True))
    result = mgr.enable()
    testbase.assert_equal(result.status, CockpitActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"],
        ["systemctl", "enable", "--now", "cockpit.socket"],
    )
    return


def test_is_active_checks_systemctl():
    """
        is_active() returns True when systemctl reports ``active``.
    """
    runner = _MockRunner()
    runner.results[("systemctl", "is-active", "cockpit.socket")] = RunResult(
        exit_code=0, stdout="active\n", stderr="", command=["systemctl"],
    )
    mgr = CockpitManager(runner, CockpitManagerConfig())
    testbase.assert_true(mgr.is_active())
    return


def test_open_firewall_dispatches_firewall_cmd():
    """
        open_firewall() adds the cockpit service to firewalld permanently.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["firewall-cmd"],
    ))
    mgr = CockpitManager(runner, CockpitManagerConfig(elevated=True))
    result = mgr.open_firewall()
    testbase.assert_equal(result.status, CockpitActionStatus.SUCCESS)
    args = runner.calls[-1]["args"]
    testbase.assert_true("--add-service=cockpit" in args)
    testbase.assert_true("--permanent" in args)
    return
