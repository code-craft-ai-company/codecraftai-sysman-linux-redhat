# automojo.sysman.linux.redhat.aspects.retry
#
# Execution aspect that decides whether to retry a failed command.

import re
import time

from dataclasses import dataclass, field
from typing import Optional

from automojo.sysman.linux.redhat.runners.models import ExecutionContext, RunResult


_METADATA_RETRY_DECISION_KEY = "retry.decision"
_METADATA_RETRY_ATTEMPT_KEY = "retry.attempt"


@dataclass
class RetryDecision:
    """
        Signal returned by :meth:`RetryAspect.should_retry` describing whether
        a retry is warranted and how long to wait before attempting it.

        :param should_retry: True when the command should be retried.
        :param backoff_seconds: Delay to wait before the retry.
        :param reason: Human-readable reason for the decision.
    """

    should_retry: bool
    backoff_seconds: float = 0.0
    reason: str = ""


@dataclass
class RetryAspectConfig:
    """
        Configuration for :class:`RetryAspect`.

        :param max_retries: Maximum number of retry attempts after an initial failure.
        :param backoff_base: Base of the exponential backoff.
        :param retryable_exit_codes: Non-zero exit codes that trigger a retry.
        :param retryable_error_patterns: Regex patterns matched against stderr
                                         to trigger retries.
    """

    max_retries: int = 3
    backoff_base: float = 2.0
    retryable_exit_codes: list[int] = field(default_factory=list)
    retryable_error_patterns: list[str] = field(default_factory=list)


class RetryAspect:
    """
        Execution aspect that evaluates whether a command should be retried
        after a failure.  The runner is responsible for invoking the retry
        loop when :meth:`should_retry` returns True; the aspect itself does
        not re-enter the runner.

        Callers that want a complete retry-loop wrapper should use
        :meth:`RetryAspect.run_with_retries` which takes a runner and arguments
        and handles the retry bookkeeping end-to-end.
    """

    def __init__(self, config: RetryAspectConfig) -> None:
        """
            :param config: Retry configuration.
        """
        self._config: RetryAspectConfig = config
        self._enabled: bool = True
        self._compiled_patterns: list[re.Pattern[str]] = [
            re.compile(pattern) for pattern in config.retryable_error_patterns
        ]
        return

    @property
    def enabled(self) -> bool:
        """
            Whether this aspect is active.
        """
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value
        return

    @property
    def config(self) -> RetryAspectConfig:
        """
            The active :class:`RetryAspectConfig`.
        """
        return self._config

    def before_execute(self, context: ExecutionContext) -> None:
        """
            Initialise retry metadata for the upcoming execution.
        """
        if _METADATA_RETRY_ATTEMPT_KEY not in context.metadata:
            context.metadata[_METADATA_RETRY_ATTEMPT_KEY] = 0
        return

    def after_execute(
        self,
        context: ExecutionContext,
        result: RunResult,
    ) -> RunResult:
        """
            Evaluate the result and record a retry decision in context metadata.
            The decision can be consulted by callers that drive the retry loop.
        """
        decision = self.should_retry(result=result, attempt=context.metadata.get(_METADATA_RETRY_ATTEMPT_KEY, 0))
        context.metadata[_METADATA_RETRY_DECISION_KEY] = decision
        return result

    def should_retry(self, *, result: RunResult, attempt: int) -> RetryDecision:
        """
            Decide whether a command should be retried given its current result.

            :param result: The result of the most recent attempt.
            :param attempt: Zero-based attempt number that just completed.

            :returns: A :class:`RetryDecision` describing the outcome.
        """
        if attempt >= self._config.max_retries:
            terminal_decision = RetryDecision(
                should_retry=False,
                reason="max retries reached",
            )
            return terminal_decision

        if result.success:
            success_decision = RetryDecision(
                should_retry=False,
                reason="command succeeded",
            )
            return success_decision

        matched_exit = result.exit_code in self._config.retryable_exit_codes
        matched_pattern = False
        if not matched_exit:
            for pattern in self._compiled_patterns:
                if pattern.search(result.stderr) is not None:
                    matched_pattern = True
                    break

        if not matched_exit and not matched_pattern:
            non_retryable_decision = RetryDecision(
                should_retry=False,
                reason="error not retryable",
            )
            return non_retryable_decision

        backoff = self._config.backoff_base ** attempt
        reason = (
            "exit code {} is retryable".format(result.exit_code)
            if matched_exit
            else "stderr matched retryable pattern"
        )
        retry_decision = RetryDecision(
            should_retry=True,
            backoff_seconds=backoff,
            reason=reason,
        )
        return retry_decision

    def run_with_retries(
        self,
        runner: object,
        args: list[str],
        *,
        elevated: bool = False,
        cwd: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> RunResult:
        """
            Execute a command through the given runner, retrying according to
            the configured policy.  The runner must expose a callable 'run'
            with a signature compatible with :class:`ICommandRunner`.

            :param runner: Runner used to execute the command.
            :param args: Argument vector.
            :param elevated: Whether to run with elevated privileges.
            :param cwd: Working directory for the command.
            :param env: Environment overrides for the command.
            :param timeout: Per-command timeout.

            :returns: The :class:`RunResult` from the final attempt.
        """
        current_attempt = 0
        last_result: Optional[RunResult] = None
        while True:
            result = runner.run(  # type: ignore[attr-defined]
                args,
                elevated=elevated,
                cwd=cwd,
                env=env,
                timeout=timeout,
            )
            result.retry_count = current_attempt
            last_result = result

            decision = self.should_retry(result=result, attempt=current_attempt)
            if not decision.should_retry:
                return result

            if decision.backoff_seconds > 0.0:
                time.sleep(decision.backoff_seconds)
            current_attempt = current_attempt + 1
