# Tests for the CommandExecutor high-level API.

import os

from dataclasses import dataclass, field
from typing import Callable, Optional
from unittest import mock

from ccai import testbase

from ccai.sysman.linux.redhat.commandexecution import (
    CommandExecutor,
    CommandExecutorConfig,
    CommandSpec,
)
from ccai.sysman.linux.redhat.runners.models import RunResult


@dataclass
class _MockRunner:
    """
        Minimal runner stub that returns pre-configured results and records
        the arguments it receives for each call.
    """

    queued_results: list[RunResult] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

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
    ) -> RunResult:
        self.calls.append({
            "args": list(args),
            "elevated": elevated,
            "cwd": cwd,
            "env": env,
            "timeout": timeout,
        })
        if len(self.queued_results) > 0:
            next_result = self.queued_results.pop(0)
        else:
            next_result = RunResult(
                exit_code=0, stdout="", stderr="", command=list(args)
            )
        return next_result

    def add_aspect(self, aspect) -> None:
        return

    def remove_aspect(self, aspect) -> None:
        return

    def get_metrics(self):
        return None


def test_dry_run_does_not_invoke_runner():
    """
        With dry_run=True the runner must not be called.
    """
    runner = _MockRunner()
    executor = CommandExecutor(runner, CommandExecutorConfig(dry_run=True))
    result = executor.run(["ls"])
    testbase.assert_true(result.success)
    testbase.assert_true(result.dry_run)
    testbase.assert_equal(runner.calls, [])
    return


def test_successful_run_maps_result():
    """
        A successful RunResult must be surfaced as a SUCCESS CommandResult.
    """
    runner = _MockRunner(queued_results=[
        RunResult(exit_code=0, stdout="hi\n", stderr="", command=["ls"])
    ])
    executor = CommandExecutor(runner, CommandExecutorConfig())
    result = executor.run(["ls"], elevated=True)
    testbase.assert_true(result.success)
    testbase.assert_equal(result.exit_code, 0)
    testbase.assert_equal(result.stdout, "hi\n")
    testbase.assert_true(runner.calls[0]["elevated"])
    return


def test_failure_run_maps_result():
    """
        A failed RunResult must be surfaced as a FAILED CommandResult.
    """
    runner = _MockRunner(queued_results=[
        RunResult(exit_code=1, stdout="", stderr="boom", command=["x"])
    ])
    executor = CommandExecutor(runner, CommandExecutorConfig())
    result = executor.run(["x"])
    testbase.assert_false(result.success)
    testbase.assert_equal(result.exit_code, 1)
    testbase.assert_equal(result.stderr, "boom")
    return


def test_timeout_populates_default_message():
    """
        A timed-out RunResult without stderr must get a default message.
    """
    runner = _MockRunner(queued_results=[
        RunResult(
            exit_code=-1, stdout="", stderr="", command=["x"], timed_out=True,
        )
    ])
    executor = CommandExecutor(runner, CommandExecutorConfig())
    result = executor.run(["x"])
    testbase.assert_true(result.timed_out)
    testbase.assert_equal(result.stderr, "Command execution timed out")
    testbase.assert_false(result.success)
    return


def test_working_dir_missing_short_circuits():
    """
        A missing working_dir must yield a FAILED result before the runner is called.
    """
    runner = _MockRunner()
    executor = CommandExecutor(runner, CommandExecutorConfig())
    result = executor.run(["ls"], working_dir="/no/such/dir")
    testbase.assert_false(result.success)
    testbase.assert_true("does not exist" in result.stderr)
    testbase.assert_equal(runner.calls, [])
    return


def test_env_merged_with_os_environ():
    """
        Caller env must be merged with os.environ before being passed to the runner.
    """
    runner = _MockRunner()
    executor = CommandExecutor(runner, CommandExecutorConfig())
    with mock.patch.dict(os.environ, {"EXISTING": "1"}, clear=False):
        executor.run(["env"], env={"OVERRIDE": "value"})
    env_passed = runner.calls[0]["env"]
    testbase.assert_true("EXISTING" in env_passed)
    testbase.assert_equal(env_passed["OVERRIDE"], "value")
    return


def test_empty_sequence_returns_success():
    """
        An empty sequence must yield a success SequenceResult with no invocations.
    """
    runner = _MockRunner()
    executor = CommandExecutor(runner, CommandExecutorConfig())
    result = executor.run_sequence([])
    testbase.assert_true(result.all_succeeded)
    testbase.assert_equal(result.results, [])
    testbase.assert_equal(result.failed_at, None)
    testbase.assert_equal(runner.calls, [])
    return


def test_dry_run_sequence_emits_synthetic_results():
    """
        A dry-run sequence must synthesise one CommandResult per spec.
    """
    runner = _MockRunner()
    executor = CommandExecutor(runner, CommandExecutorConfig(dry_run=True))
    result = executor.run_sequence([
        CommandSpec(args=["a"]),
        CommandSpec(args=["b"]),
    ])
    testbase.assert_true(result.dry_run)
    testbase.assert_equal(len(result.results), 2)
    for r in result.results:
        testbase.assert_true(r.dry_run)
    testbase.assert_equal(runner.calls, [])
    return


def test_stop_on_failure_halts_sequence():
    """
        With stop_on_failure=True the sequence must halt at the first failure.
    """
    runner = _MockRunner(queued_results=[
        RunResult(exit_code=0, stdout="", stderr="", command=["a"]),
        RunResult(exit_code=2, stdout="", stderr="err", command=["b"]),
        RunResult(exit_code=0, stdout="", stderr="", command=["c"]),
    ])
    executor = CommandExecutor(runner, CommandExecutorConfig())
    result = executor.run_sequence([
        CommandSpec(args=["a"]),
        CommandSpec(args=["b"]),
        CommandSpec(args=["c"]),
    ], stop_on_failure=True)
    testbase.assert_false(result.all_succeeded)
    testbase.assert_equal(result.failed_at, 1)
    testbase.assert_equal(len(result.results), 2)
    testbase.assert_equal(len(runner.calls), 2)
    return


def test_continue_on_failure_runs_all():
    """
        With stop_on_failure=False the sequence must run all commands.
    """
    runner = _MockRunner(queued_results=[
        RunResult(exit_code=0, stdout="", stderr="", command=["a"]),
        RunResult(exit_code=2, stdout="", stderr="err", command=["b"]),
        RunResult(exit_code=0, stdout="", stderr="", command=["c"]),
    ])
    executor = CommandExecutor(runner, CommandExecutorConfig())
    result = executor.run_sequence([
        CommandSpec(args=["a"]),
        CommandSpec(args=["b"]),
        CommandSpec(args=["c"]),
    ], stop_on_failure=False)
    testbase.assert_false(result.all_succeeded)
    testbase.assert_equal(result.failed_at, 1)
    testbase.assert_equal(len(result.results), 3)
    testbase.assert_equal(len(runner.calls), 3)
    return


def test_dry_run_script_no_tempfile():
    """
        Dry-run run_script must not write any temp file.
    """
    runner = _MockRunner()
    executor = CommandExecutor(runner, CommandExecutorConfig(dry_run=True))
    result = executor.run_script("echo hi")
    testbase.assert_true(result.dry_run)
    testbase.assert_equal(runner.calls, [])
    return


def test_script_executed_via_bash_and_cleaned_up():
    """
        run_script must dispatch ``bash <temp>`` and remove the file afterwards.
    """
    runner = _MockRunner(queued_results=[
        RunResult(exit_code=0, stdout="hi\n", stderr="", command=["bash"])
    ])
    executor = CommandExecutor(runner, CommandExecutorConfig())
    result = executor.run_script("echo hi")
    testbase.assert_true(result.success)
    testbase.assert_equal(len(runner.calls), 1)
    call_args = runner.calls[0]["args"]
    testbase.assert_equal(call_args[0], "bash")
    testbase.assert_true(call_args[1].endswith(".sh"))
    testbase.assert_false(os.path.exists(call_args[1]))
    return


def test_script_tempfile_cleanup_on_failure():
    """
        The temp file for run_script must be removed even when the script fails.
    """
    runner = _MockRunner(queued_results=[
        RunResult(exit_code=7, stdout="", stderr="fail", command=["bash"])
    ])
    executor = CommandExecutor(runner, CommandExecutorConfig())
    result = executor.run_script("exit 7")
    testbase.assert_false(result.success)
    call_args = runner.calls[0]["args"]
    testbase.assert_false(os.path.exists(call_args[1]))
    return
