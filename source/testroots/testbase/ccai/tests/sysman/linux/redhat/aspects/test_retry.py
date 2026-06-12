# Tests for the RetryAspect.

import time

from typing import Optional
from unittest import mock

from ccai import testbase

from ccai.sysman.linux.redhat.aspects.retry import RetryAspect, RetryAspectConfig
from ccai.sysman.linux.redhat.runners.models import RunResult


def _result(exit_code: int, stderr: str = "") -> RunResult:
    """
        Build a simple :class:`RunResult` with the given exit code.
    """
    result = RunResult(
        exit_code=exit_code,
        stdout="",
        stderr=stderr,
        command=["x"],
    )
    return result


def test_success_result_not_retried():
    """
        A success result should never trigger a retry decision.
    """
    aspect = RetryAspect(RetryAspectConfig(max_retries=3))
    decision = aspect.should_retry(result=_result(0), attempt=0)
    testbase.assert_false(decision.should_retry)
    return


def test_retryable_exit_code_triggers_retry():
    """
        A failure whose exit code is listed as retryable must request retry.
    """
    aspect = RetryAspect(RetryAspectConfig(max_retries=2, retryable_exit_codes=[1]))
    decision = aspect.should_retry(result=_result(1), attempt=0)
    testbase.assert_true(decision.should_retry)
    testbase.assert_greater(decision.backoff_seconds, 0.0)
    return


def test_non_retryable_exit_code_not_retried():
    """
        A failure whose exit code is not listed must not retry.
    """
    aspect = RetryAspect(RetryAspectConfig(max_retries=2, retryable_exit_codes=[1]))
    decision = aspect.should_retry(result=_result(42), attempt=0)
    testbase.assert_false(decision.should_retry)
    return


def test_retryable_stderr_pattern():
    """
        A retryable stderr pattern must trigger retry even when the exit code is not listed.
    """
    aspect = RetryAspect(RetryAspectConfig(
        max_retries=2,
        retryable_error_patterns=["Connection reset"],
    ))
    decision = aspect.should_retry(
        result=_result(5, "Connection reset by peer"),
        attempt=0,
    )
    testbase.assert_true(decision.should_retry)
    return


def test_max_retries_enforced():
    """
        Once max_retries is reached, further attempts must not retry.
    """
    aspect = RetryAspect(RetryAspectConfig(max_retries=2, retryable_exit_codes=[1]))
    decision = aspect.should_retry(result=_result(1), attempt=2)
    testbase.assert_false(decision.should_retry)
    return


class _ScriptedRunner:
    """
        Runner stub that replays a queue of RunResult values for the retry loop.
    """

    def __init__(self, queued_results: list[RunResult]) -> None:
        self._queued_results: list[RunResult] = list(queued_results)
        self._calls: int = 0
        return

    @property
    def calls(self) -> int:
        return self._calls

    def run(
        self,
        args,
        *,
        elevated: bool = False,
        cwd: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> RunResult:
        self._calls = self._calls + 1
        if len(self._queued_results) > 0:
            next_result = self._queued_results.pop(0)
        else:
            next_result = _result(0)
        return next_result


def test_returns_successful_result_without_retry():
    """
        When the first attempt succeeds, no retries should occur.
    """
    runner = _ScriptedRunner([_result(0)])
    aspect = RetryAspect(RetryAspectConfig(max_retries=3, retryable_exit_codes=[1]))
    result = aspect.run_with_retries(runner, ["ls"])
    testbase.assert_equal(result.exit_code, 0)
    testbase.assert_equal(result.retry_count, 0)
    testbase.assert_equal(runner.calls, 1)
    return


def test_retries_until_success():
    """
        The runner should be retried until a successful result is observed.
    """
    runner = _ScriptedRunner([_result(1), _result(1), _result(0)])
    aspect = RetryAspect(RetryAspectConfig(
        max_retries=3, retryable_exit_codes=[1], backoff_base=1.0,
    ))
    with mock.patch.object(time, "sleep"):
        result = aspect.run_with_retries(runner, ["ls"])
    testbase.assert_equal(result.exit_code, 0)
    testbase.assert_equal(result.retry_count, 2)
    testbase.assert_equal(runner.calls, 3)
    return


def test_exhausts_retries_and_returns_last_failure():
    """
        After max retries the last failure result must be returned.
    """
    runner = _ScriptedRunner([_result(1)] * 5)
    aspect = RetryAspect(RetryAspectConfig(
        max_retries=2, retryable_exit_codes=[1], backoff_base=1.0,
    ))
    with mock.patch.object(time, "sleep"):
        result = aspect.run_with_retries(runner, ["ls"])
    testbase.assert_equal(result.exit_code, 1)
    testbase.assert_equal(runner.calls, 3)
    return
