# Tests for the TunedManager.

from dataclasses import dataclass, field
from typing import Callable, Optional

from automojo import testbase

from automojo.sysman.linux.redhat.runners.models import RunResult
from automojo.sysman.linux.redhat.tunedmanagement import (
    TunedActionStatus,
    TunedManager,
    TunedManagerConfig,
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


def test_current_profile_parses_active_output():
    """
        current_profile() extracts the profile name from ``tuned-adm active``.
    """
    runner = _MockRunner()
    runner.results[("tuned-adm", "active")] = RunResult(
        exit_code=0, stdout="Current active profile: balanced\n",
        stderr="", command=["tuned-adm"],
    )
    mgr = TunedManager(runner, TunedManagerConfig())
    testbase.assert_equal(mgr.current_profile(), "balanced")
    return


def test_set_profile_skipped_when_already_active():
    """
        set_profile() returns SKIPPED when profile already matches.
    """
    runner = _MockRunner()
    runner.results[("tuned-adm", "active")] = RunResult(
        exit_code=0, stdout="Current active profile: balanced\n",
        stderr="", command=["tuned-adm"],
    )
    mgr = TunedManager(runner, TunedManagerConfig())
    result = mgr.set_profile("balanced")
    testbase.assert_equal(result.status, TunedActionStatus.SKIPPED)
    return


def test_set_profile_dispatches_command():
    """
        set_profile() dispatches ``tuned-adm profile <profile>``.
    """
    runner = _MockRunner()
    runner.results[("tuned-adm", "active")] = RunResult(
        exit_code=0, stdout="Current active profile: balanced\n",
        stderr="", command=["tuned-adm"],
    )
    mgr = TunedManager(runner, TunedManagerConfig(elevated=True))
    result = mgr.set_profile("throughput-performance")
    testbase.assert_equal(result.status, TunedActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"],
        ["tuned-adm", "profile", "throughput-performance"],
    )
    return


def test_list_profiles_parses_list_output():
    """
        list_profiles() extracts profile names from ``tuned-adm list`` output.
    """
    body = (
        "Available profiles:\n"
        "- balanced - General non-specialized tuned profile\n"
        "- desktop - Optimize for the desktop\n"
        "- powersave - Power save\n"
        "Current active profile: balanced\n"
    )
    runner = _MockRunner()
    runner.results[("tuned-adm", "list")] = RunResult(
        exit_code=0, stdout=body, stderr="", command=["tuned-adm"],
    )
    mgr = TunedManager(runner, TunedManagerConfig())
    profiles = mgr.list_profiles()
    testbase.assert_true("balanced" in profiles)
    testbase.assert_true("desktop" in profiles)
    return
