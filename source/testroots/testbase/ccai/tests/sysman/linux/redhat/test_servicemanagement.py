# Tests for the ServiceManager facade.

from dataclasses import dataclass, field
from typing import Callable, Optional

from ccai import testbase

from ccai.sysman.linux.redhat.runners.models import RunResult
from ccai.sysman.linux.redhat.servicemanagement import (
    ServiceActionStatus,
    ServiceManager,
    ServiceManagerConfig,
    ServiceQueryError,
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
        self.calls.append({"args": list(args), "elevated": elevated})
        key = tuple(args)
        if key in self.results:
            return self.results[key]
        if self.default_result is not None:
            return self.default_result
        default = RunResult(
            exit_code=0, stdout="active\n", stderr="", command=list(args)
        )
        return default

    def add_aspect(self, aspect) -> None:
        return

    def remove_aspect(self, aspect) -> None:
        return

    def get_metrics(self):
        return None


def _state_result(state: str, exit_code: int = 0) -> RunResult:
    """
        Build a canonical state-query RunResult for ``systemctl is-*``.
    """
    result = RunResult(
        exit_code=exit_code, stdout=state + "\n", stderr="", command=["systemctl"],
    )
    return result


def _inactive_service_runner(name: str) -> _MockRunner:
    """
        Build a runner stub that reports a service as inactive/disabled.
    """
    runner = _MockRunner()
    runner.results[("systemctl", "is-active", name)] = _state_result("inactive", 3)
    runner.results[("systemctl", "is-enabled", name)] = _state_result("disabled", 1)
    return runner


def _active_service_runner(name: str, enabled: str = "enabled") -> _MockRunner:
    """
        Build a runner stub that reports a service as active with the given
        enabled state.
    """
    runner = _MockRunner()
    runner.results[("systemctl", "is-active", name)] = _state_result("active", 0)
    runner.results[("systemctl", "is-enabled", name)] = _state_result(enabled, 0)
    return runner


def test_start_runs_systemctl_when_inactive():
    """
        start() on an inactive service must dispatch ``systemctl start``.
    """
    runner = _inactive_service_runner("nginx")
    runner.results[("systemctl", "start", "nginx")] = RunResult(
        exit_code=0, stdout="", stderr="", command=["systemctl"]
    )
    sm = ServiceManager(runner, ServiceManagerConfig(elevated=True))
    result = sm.start("nginx")
    testbase.assert_equal(result.status, ServiceActionStatus.SUCCESS)
    write_call = runner.calls[-1]
    testbase.assert_equal(write_call["args"], ["systemctl", "start", "nginx"])
    testbase.assert_true(write_call["elevated"])
    return


def test_start_skipped_when_already_active():
    """
        start() on an already active service must return SKIPPED.
    """
    runner = _active_service_runner("nginx")
    sm = ServiceManager(runner, ServiceManagerConfig())
    result = sm.start("nginx")
    testbase.assert_equal(result.status, ServiceActionStatus.SKIPPED)
    write_calls = [c for c in runner.calls if c["args"][:2] == ["systemctl", "start"]]
    testbase.assert_equal(write_calls, [])
    return


def test_stop_skipped_when_inactive():
    """
        stop() on an already inactive service must return SKIPPED.
    """
    runner = _inactive_service_runner("nginx")
    sm = ServiceManager(runner, ServiceManagerConfig())
    result = sm.stop("nginx")
    testbase.assert_equal(result.status, ServiceActionStatus.SKIPPED)
    return


def test_restart_unconditional():
    """
        restart() must always dispatch ``systemctl restart``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["systemctl"]
    ))
    sm = ServiceManager(runner, ServiceManagerConfig())
    result = sm.restart("nginx")
    testbase.assert_equal(result.status, ServiceActionStatus.SUCCESS)
    testbase.assert_equal(runner.calls[0]["args"], ["systemctl", "restart", "nginx"])
    return


def test_reload_fails_when_inactive():
    """
        reload() when the service is inactive must return FAILED.
    """
    runner = _inactive_service_runner("nginx")
    sm = ServiceManager(runner, ServiceManagerConfig())
    result = sm.reload("nginx")
    testbase.assert_equal(result.status, ServiceActionStatus.FAILED)
    return


def test_reload_runs_when_active():
    """
        reload() on an active service must dispatch ``systemctl reload``.
    """
    runner = _active_service_runner("nginx")
    runner.results[("systemctl", "reload", "nginx")] = RunResult(
        exit_code=0, stdout="", stderr="", command=["systemctl"]
    )
    sm = ServiceManager(runner, ServiceManagerConfig())
    result = sm.reload("nginx")
    testbase.assert_equal(result.status, ServiceActionStatus.SUCCESS)
    return


def test_dry_run_write():
    """
        With dry_run=True no write commands must be dispatched.
    """
    runner = _inactive_service_runner("nginx")
    sm = ServiceManager(runner, ServiceManagerConfig(dry_run=True))
    result = sm.start("nginx")
    testbase.assert_equal(result.status, ServiceActionStatus.DRY_RUN)
    write_calls = [c for c in runner.calls if c["args"][:2] == ["systemctl", "start"]]
    testbase.assert_equal(write_calls, [])
    return


def test_enable_runs_when_disabled():
    """
        enable() when disabled must dispatch ``systemctl enable``.
    """
    runner = _inactive_service_runner("nginx")
    runner.results[("systemctl", "enable", "nginx")] = RunResult(
        exit_code=0, stdout="", stderr="", command=["systemctl"]
    )
    sm = ServiceManager(runner, ServiceManagerConfig())
    result = sm.enable("nginx")
    testbase.assert_equal(result.status, ServiceActionStatus.SUCCESS)
    return


def test_enable_skipped_when_already_enabled():
    """
        enable() when already enabled must return SKIPPED.
    """
    runner = _active_service_runner("nginx")
    sm = ServiceManager(runner, ServiceManagerConfig())
    result = sm.enable("nginx")
    testbase.assert_equal(result.status, ServiceActionStatus.SKIPPED)
    return


def test_disable_runs_when_enabled():
    """
        disable() when enabled must dispatch ``systemctl disable``.
    """
    runner = _active_service_runner("nginx")
    runner.results[("systemctl", "disable", "nginx")] = RunResult(
        exit_code=0, stdout="", stderr="", command=["systemctl"]
    )
    sm = ServiceManager(runner, ServiceManagerConfig())
    result = sm.disable("nginx")
    testbase.assert_equal(result.status, ServiceActionStatus.SUCCESS)
    return


def test_mask_runs_when_not_masked():
    """
        mask() on a non-masked service must dispatch ``systemctl mask``.
    """
    runner = _active_service_runner("nginx")
    runner.results[("systemctl", "mask", "nginx")] = RunResult(
        exit_code=0, stdout="", stderr="", command=["systemctl"]
    )
    sm = ServiceManager(runner, ServiceManagerConfig())
    result = sm.mask("nginx")
    testbase.assert_equal(result.status, ServiceActionStatus.SUCCESS)
    return


def test_mask_skipped_when_masked():
    """
        mask() on an already masked service must return SKIPPED.
    """
    runner = _active_service_runner("nginx", enabled="masked")
    sm = ServiceManager(runner, ServiceManagerConfig())
    result = sm.mask("nginx")
    testbase.assert_equal(result.status, ServiceActionStatus.SKIPPED)
    return


def test_unmask_skipped_when_not_masked():
    """
        unmask() on a non-masked service must return SKIPPED.
    """
    runner = _active_service_runner("nginx", enabled="disabled")
    sm = ServiceManager(runner, ServiceManagerConfig())
    result = sm.unmask("nginx")
    testbase.assert_equal(result.status, ServiceActionStatus.SKIPPED)
    return


def test_query_returns_info():
    """
        query() must populate both active and enabled state fields.
    """
    runner = _MockRunner()
    runner.results[("systemctl", "is-active", "nginx")] = _state_result("active")
    runner.results[("systemctl", "is-enabled", "nginx")] = _state_result("enabled")
    sm = ServiceManager(runner, ServiceManagerConfig())
    info = sm.query("nginx")
    testbase.assert_equal(info.active_state, "active")
    testbase.assert_equal(info.enabled_state, "enabled")
    return


def test_query_raises_on_unknown_state():
    """
        query() must raise ServiceQueryError when a state is unrecognised.
    """
    runner = _MockRunner()
    runner.results[("systemctl", "is-active", "nginx")] = RunResult(
        exit_code=7, stdout="weird", stderr="boom", command=["systemctl"]
    )
    sm = ServiceManager(runner, ServiceManagerConfig())
    raised = False
    try:
        sm.query("nginx")
    except ServiceQueryError:
        raised = True
    testbase.assert_true(raised)
    return


def test_get_journal_returns_stdout():
    """
        get_journal() must return stdout text from journalctl.
    """
    runner = _MockRunner()
    runner.results[(
        "journalctl", "-u", "nginx", "--no-pager", "-n", "50",
    )] = RunResult(
        exit_code=0, stdout="logs here", stderr="", command=["journalctl"]
    )
    sm = ServiceManager(runner, ServiceManagerConfig())
    output = sm.get_journal("nginx")
    testbase.assert_equal(output, "logs here")
    return


def test_get_journal_raises_on_failure():
    """
        get_journal() must raise ServiceQueryError when journalctl fails.
    """
    runner = _MockRunner()
    runner.results[(
        "journalctl", "-u", "nginx", "--no-pager", "-n", "50",
    )] = RunResult(
        exit_code=1, stdout="", stderr="boom", command=["journalctl"]
    )
    sm = ServiceManager(runner, ServiceManagerConfig())
    raised = False
    try:
        sm.get_journal("nginx")
    except ServiceQueryError:
        raised = True
    testbase.assert_true(raised)
    return


def test_daemon_reload_runs():
    """
        daemon_reload() must dispatch ``systemctl daemon-reload``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["systemctl"]
    ))
    sm = ServiceManager(runner, ServiceManagerConfig(elevated=True))
    result = sm.daemon_reload()
    testbase.assert_equal(result.status, ServiceActionStatus.SUCCESS)
    testbase.assert_equal(runner.calls[-1]["args"], ["systemctl", "daemon-reload"])
    testbase.assert_true(runner.calls[-1]["elevated"])
    return
