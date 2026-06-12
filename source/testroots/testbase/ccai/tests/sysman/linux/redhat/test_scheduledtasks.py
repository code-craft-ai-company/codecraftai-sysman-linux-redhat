# Tests for the ScheduledTaskManager facade.

from dataclasses import dataclass, field
from typing import Callable, Optional

from ccai import testbase

from ccai.sysman.linux.redhat.runners.models import RunResult
from ccai.sysman.linux.redhat.scheduledtasks import (
    CronJobEntry,
    ScheduledTaskConfig,
    ScheduledTaskManager,
    SystemdTimerEntry,
    TaskType,
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


def test_add_cron_job_writes_drop_in():
    """
        add_cron_job() writes a cron drop-in file via tee.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/cron.d/backup")] = RunResult(
        exit_code=1, stdout="", stderr="", command=["cat"],
    )
    mgr = ScheduledTaskManager(runner, ScheduledTaskConfig(elevated=True))
    entry = CronJobEntry(
        name="backup",
        schedule="0 2 * * *",
        command="/usr/local/bin/backup.sh",
        user="root",
    )
    result = mgr.add_cron_job(entry)
    testbase.assert_true(result.success)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true("0 2 * * * root /usr/local/bin/backup.sh" in tee_calls[0]["stdin"])
    return


def test_add_cron_job_skipped_when_content_matches():
    """
        add_cron_job() returns success without tee when the file already matches.
    """
    expected = (
        "SHELL=/bin/bash\n"
        "PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin\n"
        "0 2 * * * root /usr/local/bin/backup.sh\n"
    )
    runner = _MockRunner()
    runner.results[("cat", "/etc/cron.d/backup")] = RunResult(
        exit_code=0, stdout=expected, stderr="", command=["cat"],
    )
    mgr = ScheduledTaskManager(runner, ScheduledTaskConfig())
    entry = CronJobEntry(
        name="backup",
        schedule="0 2 * * *",
        command="/usr/local/bin/backup.sh",
        user="root",
    )
    result = mgr.add_cron_job(entry)
    testbase.assert_true(result.success)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(tee_calls, [])
    return


def test_add_systemd_timer_writes_units():
    """
        add_systemd_timer() writes both .service and .timer files and
        delegates lifecycle to ServiceManager (Part C seam): a
        daemon-reload followed by enable + start for the timer unit.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/systemd/system/app.service")] = RunResult(
        exit_code=1, stdout="", stderr="", command=["cat"],
    )
    runner.results[("cat", "/etc/systemd/system/app.timer")] = RunResult(
        exit_code=1, stdout="", stderr="", command=["cat"],
    )
    # ServiceManager.enable() / .start() each query state first; seed
    # responses so they're not SKIPPED.
    runner.results[("systemctl", "is-active", "app.timer")] = RunResult(
        exit_code=0, stdout="inactive\n", stderr="", command=["systemctl"],
    )
    runner.results[("systemctl", "is-enabled", "app.timer")] = RunResult(
        exit_code=0, stdout="disabled\n", stderr="", command=["systemctl"],
    )
    mgr = ScheduledTaskManager(runner, ScheduledTaskConfig(elevated=True))
    result = mgr.add_systemd_timer(SystemdTimerEntry(
        name="app",
        description="App periodic task",
        on_calendar="daily",
        exec_start="/usr/local/bin/app-run.sh",
    ))
    testbase.assert_true(result.success)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 2)
    paths = [c["args"][1] for c in tee_calls]
    testbase.assert_true("/etc/systemd/system/app.service" in paths)
    testbase.assert_true("/etc/systemd/system/app.timer" in paths)
    enable_calls = [
        c for c in runner.calls
        if c["args"][:3] == ["systemctl", "enable", "app.timer"]
    ]
    testbase.assert_equal(len(enable_calls), 1)
    start_calls = [
        c for c in runner.calls
        if c["args"][:3] == ["systemctl", "start", "app.timer"]
    ]
    testbase.assert_equal(len(start_calls), 1)
    daemon_reload_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["systemctl", "daemon-reload"]
    ]
    testbase.assert_equal(len(daemon_reload_calls), 1)
    return


def test_query_cron_returns_info_when_present():
    """
        query_cron() parses the drop-in body when present.
    """
    body = (
        "SHELL=/bin/bash\n"
        "PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin\n"
        "0 2 * * * root /usr/local/bin/backup.sh\n"
    )
    runner = _MockRunner()
    runner.results[("cat", "/etc/cron.d/backup")] = RunResult(
        exit_code=0, stdout=body, stderr="", command=["cat"],
    )
    mgr = ScheduledTaskManager(runner, ScheduledTaskConfig())
    info = mgr.query_cron("backup")
    testbase.assert_true(info is not None)
    testbase.assert_equal(info.task_type, TaskType.CRON)
    testbase.assert_equal(info.schedule, "0 2 * * *")
    testbase.assert_equal(info.user, "root")
    testbase.assert_equal(info.command, "/usr/local/bin/backup.sh")
    return


def test_query_cron_returns_none_when_absent():
    """
        query_cron() returns None when the drop-in file is missing.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/cron.d/ghost")] = RunResult(
        exit_code=1, stdout="", stderr="", command=["cat"],
    )
    mgr = ScheduledTaskManager(runner, ScheduledTaskConfig())
    testbase.assert_equal(mgr.query_cron("ghost"), None)
    return


def test_invalid_name_raises_value_error():
    """
        add_cron_job() rejects names that contain disallowed characters.
    """
    runner = _MockRunner()
    mgr = ScheduledTaskManager(runner, ScheduledTaskConfig())
    raised = False
    try:
        mgr.add_cron_job(CronJobEntry(
            name="bad name",
            schedule="@daily",
            command="true",
            user="root",
        ))
    except ValueError:
        raised = True
    testbase.assert_true(raised)
    return
