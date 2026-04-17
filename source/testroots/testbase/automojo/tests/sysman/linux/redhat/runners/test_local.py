# Tests for LocalCommandRunner.

import subprocess

from dataclasses import dataclass
from unittest import mock

from automojo import testbase

from automojo.sysman.linux.redhat.aspects.base import IExecutionAspect
from automojo.sysman.linux.redhat.runners.local import (
    LocalCommandRunner,
    LocalRunnerConfig,
)
from automojo.sysman.linux.redhat.runners.models import ExecutionContext, RunResult


@dataclass
class _RecordingAspect:
    """
        Simple aspect that records the order and values of before/after
        callbacks for use in assertions.
    """
    name: str
    events: list
    enabled: bool = True

    def before_execute(self, context: ExecutionContext) -> None:
        self.events.append(("before", self.name))
        return

    def after_execute(
        self,
        context: ExecutionContext,
        result: RunResult,
    ) -> RunResult:
        self.events.append(("after", self.name))
        return result


def test_run_invokes_subprocess_with_expected_args():
    """
        run() must call subprocess.run with the given argv and cwd.
    """
    runner = LocalCommandRunner(LocalRunnerConfig())
    completed = subprocess.CompletedProcess(
        args=["echo", "hi"], returncode=0, stdout="hi\n", stderr=""
    )
    with mock.patch("subprocess.run", return_value=completed) as run_mock:
        result = runner.run(["echo", "hi"], cwd="/tmp")

    testbase.assert_equal(result.exit_code, 0)
    testbase.assert_equal(result.stdout, "hi\n")
    testbase.assert_equal(result.transport, "local")
    testbase.assert_true(result.duration_ms is not None)
    run_mock.assert_called_once()
    call_args = run_mock.call_args
    testbase.assert_equal(call_args.args[0], ["echo", "hi"])
    testbase.assert_equal(call_args.kwargs["cwd"], "/tmp")
    return


def test_elevated_prepends_sudo():
    """
        elevated=True must result in sudo being prepended to the argv.
    """
    runner = LocalCommandRunner(LocalRunnerConfig(sudo_path="/usr/bin/sudo"))
    completed = subprocess.CompletedProcess(
        args=["sudo", "-n", "ls"], returncode=0, stdout="", stderr=""
    )
    with mock.patch("subprocess.run", return_value=completed) as run_mock:
        runner.run(["ls"], elevated=True)
    called_args = run_mock.call_args.args[0]
    testbase.assert_equal(called_args[:3], ["/usr/bin/sudo", "-n", "ls"])
    return


def test_env_merged_with_process_environ():
    """
        Caller env must be merged with os.environ and overrides must win.
    """
    runner = LocalCommandRunner(LocalRunnerConfig())
    completed = subprocess.CompletedProcess(
        args=["env"], returncode=0, stdout="", stderr=""
    )
    with mock.patch.dict("os.environ", {"EXISTING": "1"}, clear=False):
        with mock.patch("subprocess.run", return_value=completed) as run_mock:
            runner.run(["env"], env={"OVERRIDE": "value"})
    env_passed = run_mock.call_args.kwargs["env"]
    testbase.assert_true("EXISTING" in env_passed)
    testbase.assert_equal(env_passed["OVERRIDE"], "value")
    return


def test_timeout_produces_timed_out_result():
    """
        A subprocess TimeoutExpired must produce a RunResult with timed_out=True.
    """
    runner = LocalCommandRunner(LocalRunnerConfig())
    expired = subprocess.TimeoutExpired(cmd=["sleep", "10"], timeout=1.0)
    with mock.patch("subprocess.run", side_effect=expired):
        result = runner.run(["sleep", "10"], timeout=1.0)
    testbase.assert_true(result.timed_out)
    testbase.assert_equal(result.exit_code, -1)
    testbase.assert_false(result.success)
    return


def test_dry_run_skips_subprocess():
    """
        dry_run must suppress subprocess invocation and return a synthetic success.
    """
    runner = LocalCommandRunner(LocalRunnerConfig(dry_run=True))
    with mock.patch("subprocess.run") as run_mock:
        result = runner.run(["rm", "-rf", "/"])
    testbase.assert_true(result.dry_run)
    testbase.assert_equal(result.exit_code, 0)
    run_mock.assert_not_called()
    return


def test_aspect_invocation_order():
    """
        Aspects must be invoked in registration order before execute and
        reverse order after execute.
    """
    events: list = []
    aspect_a = _RecordingAspect(name="A", events=events)
    aspect_b = _RecordingAspect(name="B", events=events)
    runner = LocalCommandRunner(LocalRunnerConfig())
    runner.add_aspect(aspect_a)
    runner.add_aspect(aspect_b)
    completed = subprocess.CompletedProcess(
        args=["echo"], returncode=0, stdout="", stderr=""
    )
    with mock.patch("subprocess.run", return_value=completed):
        runner.run(["echo"])
    expected_order = [
        ("before", "A"),
        ("before", "B"),
        ("after", "B"),
        ("after", "A"),
    ]
    testbase.assert_equal(events, expected_order)
    return


def test_disabled_aspect_skipped():
    """
        An aspect with enabled=False must not participate in execution.
    """
    events: list = []
    aspect = _RecordingAspect(name="X", events=events, enabled=False)
    runner = LocalCommandRunner(LocalRunnerConfig())
    runner.add_aspect(aspect)
    completed = subprocess.CompletedProcess(
        args=["echo"], returncode=0, stdout="", stderr=""
    )
    with mock.patch("subprocess.run", return_value=completed):
        runner.run(["echo"])
    testbase.assert_equal(events, [])
    return


def test_remove_aspect():
    """
        remove_aspect must unregister an aspect so it does not fire.
    """
    events: list = []
    aspect = _RecordingAspect(name="R", events=events)
    runner = LocalCommandRunner(LocalRunnerConfig())
    runner.add_aspect(aspect)
    runner.remove_aspect(aspect)
    completed = subprocess.CompletedProcess(
        args=["echo"], returncode=0, stdout="", stderr=""
    )
    with mock.patch("subprocess.run", return_value=completed):
        runner.run(["echo"])
    testbase.assert_equal(events, [])
    return


def test_aspect_before_raises_aborts_execution():
    """
        An exception raised from before_execute must abort subprocess invocation.
    """
    class RaisingAspect:
        enabled = True

        def before_execute(self, context: ExecutionContext) -> None:
            raise RuntimeError("abort")

        def after_execute(
            self,
            context: ExecutionContext,
            result: RunResult,
        ) -> RunResult:
            return result

    runner = LocalCommandRunner(LocalRunnerConfig())
    runner.add_aspect(RaisingAspect())
    raised = False
    with mock.patch("subprocess.run") as run_mock:
        try:
            runner.run(["echo"])
        except RuntimeError:
            raised = True
    testbase.assert_true(raised)
    run_mock.assert_not_called()
    return


def test_get_metrics_returns_none():
    """
        Local runners do not track reliability metrics.
    """
    runner = LocalCommandRunner(LocalRunnerConfig())
    testbase.assert_equal(runner.get_metrics(), None)
    return


def test_on_output_callback():
    """
        Stdout/stderr callbacks must receive the captured output chunks.
    """
    chunks_out: list = []
    chunks_err: list = []
    runner = LocalCommandRunner(LocalRunnerConfig())
    completed = subprocess.CompletedProcess(
        args=["echo"], returncode=0, stdout="hello", stderr="warn"
    )
    with mock.patch("subprocess.run", return_value=completed):
        runner.run(
            ["echo"],
            on_output=chunks_out.append,
            on_error=chunks_err.append,
        )
    testbase.assert_equal(chunks_out, ["hello"])
    testbase.assert_equal(chunks_err, ["warn"])
    return


def test_aspect_protocol_conformance():
    """
        A recording aspect satisfies the IExecutionAspect protocol.
    """
    events: list = []
    aspect = _RecordingAspect(name="A", events=events)
    testbase.assert_true(isinstance(aspect, IExecutionAspect))
    return
