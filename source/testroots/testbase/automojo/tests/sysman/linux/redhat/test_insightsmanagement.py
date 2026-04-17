# Tests for InsightsManager.

from dataclasses import dataclass, field
from typing import Optional

from automojo import testbase

from automojo.sysman.linux.redhat.insightsmanagement import (
    InsightsActionStatus,
    InsightsManager,
    InsightsManagerConfig,
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


def test_register_dispatches_insights_client():
    """
        register() dispatches ``insights-client --register``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["insights-client"],
    ))
    mgr = InsightsManager(runner, InsightsManagerConfig(elevated=True))
    result = mgr.register()
    testbase.assert_equal(result.status, InsightsActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"], ["insights-client", "--register"],
    )
    return


def test_run_check_dispatches_insights_client():
    """
        run_check() runs ``insights-client`` with no flags.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["insights-client"],
    ))
    mgr = InsightsManager(runner, InsightsManagerConfig(elevated=True))
    result = mgr.run_check()
    testbase.assert_equal(result.status, InsightsActionStatus.SUCCESS)
    testbase.assert_equal(runner.calls[-1]["args"], ["insights-client"])
    return
