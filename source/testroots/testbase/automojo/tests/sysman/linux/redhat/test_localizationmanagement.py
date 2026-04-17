# Tests for LocalizationManager.

from dataclasses import dataclass, field
from typing import Optional

from automojo import testbase

from automojo.sysman.linux.redhat.localizationmanagement import (
    LocalizationActionStatus,
    LocalizationManager,
    LocalizationManagerConfig,
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


_LOCALECTL_OUTPUT = (
    "   System Locale: LANG=en_US.UTF-8\n"
    "       VC Keymap: us\n"
    "      X11 Layout: us\n"
)

_TIMEDATECTL_OUTPUT = (
    "Timezone=America/Los_Angeles\n"
    "NTP=yes\n"
    "LocalRTC=no\n"
    "TimeUSec=2024-01-01\n"
)


def test_query_locale_parses_localectl_status():
    """
        query_locale() parses ``localectl status`` output into LocaleInfo.
    """
    runner = _MockRunner()
    runner.results[("localectl", "status")] = RunResult(
        exit_code=0, stdout=_LOCALECTL_OUTPUT, stderr="", command=["localectl"],
    )
    mgr = LocalizationManager(runner, LocalizationManagerConfig())
    info = mgr.query_locale()
    testbase.assert_equal(info.lang, "en_US.UTF-8")
    return


def test_set_locale_skipped_when_current_matches():
    """
        set_locale() returns SKIPPED when LANG already matches.
    """
    runner = _MockRunner()
    runner.results[("localectl", "status")] = RunResult(
        exit_code=0, stdout=_LOCALECTL_OUTPUT, stderr="", command=["localectl"],
    )
    mgr = LocalizationManager(runner, LocalizationManagerConfig())
    result = mgr.set_locale(lang="en_US.UTF-8")
    testbase.assert_equal(result.status, LocalizationActionStatus.SKIPPED)
    return


def test_query_timezone_parses_timedatectl_show():
    """
        query_timezone() parses ``timedatectl show`` into TimezoneInfo.
    """
    runner = _MockRunner()
    runner.results[("timedatectl", "show")] = RunResult(
        exit_code=0, stdout=_TIMEDATECTL_OUTPUT, stderr="",
        command=["timedatectl"],
    )
    mgr = LocalizationManager(runner, LocalizationManagerConfig())
    info = mgr.query_timezone()
    testbase.assert_equal(info.timezone, "America/Los_Angeles")
    testbase.assert_true(info.ntp_enabled)
    testbase.assert_false(info.local_rtc)
    return


def test_set_timezone_dispatches_timedatectl():
    """
        set_timezone() dispatches ``timedatectl set-timezone <tz>`` when different.
    """
    runner = _MockRunner()
    runner.results[("timedatectl", "show")] = RunResult(
        exit_code=0, stdout=_TIMEDATECTL_OUTPUT, stderr="",
        command=["timedatectl"],
    )
    mgr = LocalizationManager(runner, LocalizationManagerConfig(elevated=True))
    result = mgr.set_timezone("UTC")
    testbase.assert_equal(result.status, LocalizationActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"], ["timedatectl", "set-timezone", "UTC"],
    )
    return
