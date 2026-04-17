# Tests for the LogManager facade.

from dataclasses import dataclass, field
from typing import Callable, Optional

from automojo import testbase

from automojo.sysman.linux.redhat.logmanagement import (
    JournalQueryParams,
    LogActionStatus,
    LogManagementError,
    LogManager,
    LogManagerConfig,
    LogrotateConfig,
)
from automojo.sysman.linux.redhat.runners.models import RunResult


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


def test_query_journal_builds_command():
    """
        query_journal() appends filter options based on JournalQueryParams.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="line1\nline2\n", stderr="", command=["journalctl"],
    ))
    lm = LogManager(runner, LogManagerConfig())
    params = JournalQueryParams(
        unit="nginx.service", since="yesterday", lines=50, grep="error",
    )
    result = lm.query_journal(params)
    testbase.assert_true(result.success)
    testbase.assert_equal(result.lines, ["line1", "line2"])
    command_args = runner.calls[-1]["args"]
    testbase.assert_true("--unit=nginx.service" in command_args)
    testbase.assert_true("--since=yesterday" in command_args)
    testbase.assert_true("--lines=50" in command_args)
    testbase.assert_true("--grep=error" in command_args)
    return


def test_journal_disk_usage_parses_size():
    """
        journal_disk_usage() parses the ``take up`` phrase.
    """
    stdout = "Archived and active journals take up 1.2G in the file system.\n"
    runner = _MockRunner()
    runner.results[("journalctl", "--disk-usage", "--no-pager")] = RunResult(
        exit_code=0, stdout=stdout, stderr="", command=["journalctl"],
    )
    lm = LogManager(runner, LogManagerConfig())
    usage = lm.journal_disk_usage()
    testbase.assert_equal(usage.size_human, "1.2G")
    testbase.assert_true(usage.size_bytes > 0)
    return


def test_journal_disk_usage_raises_on_failure():
    """
        journal_disk_usage() raises LogManagementError on failure.
    """
    runner = _MockRunner()
    runner.results[("journalctl", "--disk-usage", "--no-pager")] = RunResult(
        exit_code=1, stdout="", stderr="boom", command=["journalctl"],
    )
    lm = LogManager(runner, LogManagerConfig())
    raised = False
    try:
        lm.journal_disk_usage()
    except LogManagementError:
        raised = True
    testbase.assert_true(raised)
    return


def test_vacuum_by_size_runs_journalctl():
    """
        vacuum_by_size() dispatches ``journalctl --vacuum-size=<size>``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["journalctl"],
    ))
    lm = LogManager(runner, LogManagerConfig(elevated=True))
    result = lm.vacuum_by_size("1G")
    testbase.assert_equal(result.status, LogActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"], ["journalctl", "--vacuum-size=1G"],
    )
    return


def test_read_log_file_dispatches_tail():
    """
        read_log_file() dispatches ``tail --lines=N <path>``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="a\nb\n", stderr="", command=["tail"],
    ))
    lm = LogManager(runner, LogManagerConfig())
    result = lm.read_log_file("/var/log/app.log", 10)
    testbase.assert_true(result.success)
    testbase.assert_equal(result.lines, ["a", "b"])
    testbase.assert_equal(
        runner.calls[-1]["args"], ["tail", "--lines=10", "/var/log/app.log"],
    )
    return


def test_search_log_file_no_matches_still_success():
    """
        search_log_file() treats grep exit 1 (no matches) as success.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=1, stdout="", stderr="", command=["grep"],
    ))
    lm = LogManager(runner, LogManagerConfig())
    result = lm.search_log_file("/var/log/app.log", "ERROR")
    testbase.assert_true(result.success)
    testbase.assert_equal(result.lines, [])
    return


def test_write_logrotate_dropin_writes_file_when_new():
    """
        write_logrotate_dropin() writes the file via tee on first run.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/logrotate.d/app")] = RunResult(
        exit_code=1, stdout="", stderr="", command=["cat"],
    )
    lm = LogManager(runner, LogManagerConfig(elevated=True))
    cfg = LogrotateConfig(log_path="/var/log/app/*.log", rotate=5, frequency="daily")
    result = lm.write_logrotate_dropin("app", cfg)
    testbase.assert_equal(result.status, LogActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true("rotate 5" in tee_calls[0]["stdin"])
    return


def test_write_logrotate_dropin_skipped_when_content_matches():
    """
        write_logrotate_dropin() returns SKIPPED when content matches.
    """
    runner = _MockRunner()
    lm = LogManager(runner, LogManagerConfig())
    cfg = LogrotateConfig(log_path="/var/log/app/*.log", rotate=7)
    rendered = (
        "/var/log/app/*.log {\n"
        "    daily\n"
        "    rotate 7\n"
        "    compress\n"
        "    missingok\n"
        "    notifempty\n"
        "}\n"
    )
    runner.results[("cat", "/etc/logrotate.d/app")] = RunResult(
        exit_code=0, stdout=rendered, stderr="", command=["cat"],
    )
    result = lm.write_logrotate_dropin("app", cfg)
    testbase.assert_equal(result.status, LogActionStatus.SKIPPED)
    return


def test_query_logrotate_dropin_reports_existence():
    """
        query_logrotate_dropin() reports exists=True when cat returns content.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/logrotate.d/app")] = RunResult(
        exit_code=0, stdout="config-body\n", stderr="", command=["cat"],
    )
    lm = LogManager(runner, LogManagerConfig())
    state = lm.query_logrotate_dropin("app")
    testbase.assert_true(state.exists)
    testbase.assert_equal(state.content, "config-body\n")
    return
