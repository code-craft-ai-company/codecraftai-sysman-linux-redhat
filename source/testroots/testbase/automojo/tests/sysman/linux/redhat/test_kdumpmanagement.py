# Tests for KdumpManager.

from dataclasses import dataclass, field
from typing import Optional

from automojo import testbase

from automojo.sysman.linux.redhat.kdumpmanagement import (
    KdumpActionStatus,
    KdumpManager,
    KdumpManagerConfig,
)
from automojo.sysman.linux.redhat.runners.models import RunResult


@dataclass
class _MockRunner:
    results: dict = field(default_factory=dict)
    default_result: Optional[RunResult] = None
    calls: list = field(default_factory=list)

    def run(self, args, *, elevated=False, cwd=None, env=None, timeout=None,
            on_output=None, on_error=None, capture_output=True, stdin=None):
        self.calls.append({"args": list(args), "stdin": stdin})
        key = tuple(args)
        if key in self.results:
            return self.results[key]
        if self.default_result is not None:
            return self.default_result
        return RunResult(exit_code=0, stdout="", stderr="", command=list(args))

    def add_aspect(self, aspect): return
    def remove_aspect(self, aspect): return
    def get_metrics(self): return None


def test_query_status_reports_active_and_enabled():
    """
        query_status() reports active+enabled based on systemctl output.
    """
    runner = _MockRunner()
    runner.results[("systemctl", "is-active", "kdump.service")] = RunResult(
        exit_code=0, stdout="active\n", stderr="", command=["systemctl"],
    )
    runner.results[("systemctl", "is-enabled", "kdump.service")] = RunResult(
        exit_code=0, stdout="enabled\n", stderr="", command=["systemctl"],
    )
    mgr = KdumpManager(runner, KdumpManagerConfig())
    status = mgr.query_status()
    testbase.assert_true(status.active)
    testbase.assert_true(status.enabled)
    return


def test_enable_runs_systemctl_enable_now():
    """
        enable() runs ``systemctl enable --now kdump.service``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["systemctl"],
    ))
    mgr = KdumpManager(runner, KdumpManagerConfig(elevated=True))
    result = mgr.enable()
    testbase.assert_equal(result.status, KdumpActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"],
        ["systemctl", "enable", "--now", "kdump.service"],
    )
    return


def test_set_config_directive_writes_tee():
    """
        set_config_directive() writes kdump.conf via tee.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/kdump.conf")] = RunResult(
        exit_code=0, stdout="path /var/crash\n", stderr="", command=["cat"],
    )
    mgr = KdumpManager(runner, KdumpManagerConfig(elevated=True))
    result = mgr.set_config_directive("path", "/var/crash-alt")
    testbase.assert_equal(result.status, KdumpActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true("/var/crash-alt" in tee_calls[0]["stdin"])
    return
