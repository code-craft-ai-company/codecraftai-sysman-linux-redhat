# ccai.sysman.linux.redhat.runners.local
#
# Command runner that executes commands via a local subprocess.

import os
import subprocess
import time

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

from ccai.sysman.linux.redhat.runners.models import (
    ExecutionContext,
    RunResult,
    SSHReliabilityMetrics,
)

if TYPE_CHECKING:
    from ccai.sysman.linux.redhat.aspects.base import IExecutionAspect


@dataclass
class LocalRunnerConfig:
    """
        Configuration for :class:`LocalCommandRunner`.

        :param dry_run: When True, no subprocess is spawned; a synthetic
                        success result is returned instead.
        :param sudo_path: Path to the sudo binary used for elevation.
    """

    dry_run: bool = False
    sudo_path: str = "/usr/bin/sudo"


class LocalCommandRunner:
    """
        :class:`ICommandRunner` implementation that executes commands through
        :func:`subprocess.run` on the local machine.
    """

    def __init__(self, config: Optional[LocalRunnerConfig] = None) -> None:
        """
            :param config: Runner configuration.  If None, defaults are used.
        """
        if config is None:
            config = LocalRunnerConfig()

        self._config: LocalRunnerConfig = config
        self._aspects: list["IExecutionAspect"] = []
        self._metrics: SSHReliabilityMetrics = SSHReliabilityMetrics()
        return

    @property
    def config(self) -> LocalRunnerConfig:
        """
            The active :class:`LocalRunnerConfig`.
        """
        return self._config

    @property
    def aspects(self) -> list["IExecutionAspect"]:
        """
            Snapshot of registered aspects in registration order.
        """
        rtnval = list(self._aspects)
        return rtnval

    def add_aspect(self, aspect: "IExecutionAspect") -> None:
        """
            Register an aspect to be applied to subsequent command executions.
        """
        self._aspects.append(aspect)
        return

    def remove_aspect(self, aspect: "IExecutionAspect") -> None:
        """
            Unregister a previously registered aspect.  No error is raised
            when the aspect was not present.
        """
        if aspect in self._aspects:
            self._aspects.remove(aspect)
        return

    def get_metrics(self) -> Optional[SSHReliabilityMetrics]:
        """
            Return the accumulated execution metrics for this runner.

            The ``SSHReliabilityMetrics`` shape is shared with
            :class:`SSHCommandRunner`; for a local runner, the
            ``connection_error_count`` and ``last_error_type`` fields
            remain at their defaults since they describe SSH-specific
            failure modes.  Every other field is populated from
            subprocess executions.
        """
        return self._metrics

    def reset_metrics(self) -> None:
        """
            Reset all execution counters and timing state.
        """
        self._metrics = SSHReliabilityMetrics()
        return

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
        """
            Execute a command locally and return its :class:`RunResult`.
        """
        context = ExecutionContext(
            args=list(args),
            elevated=elevated,
            cwd=cwd,
            env=env,
            timeout=timeout,
            transport="local",
            dry_run=self._config.dry_run,
        )

        self._apply_before_aspects(context=context)

        if context.dry_run:
            dry_result = RunResult(
                exit_code=0,
                stdout="",
                stderr="",
                command=list(context.args),
                dry_run=True,
                transport="local",
            )
            final_dry = self._apply_after_aspects(context=context, result=dry_result)
            return final_dry

        invocation_args = list(context.args)
        if context.elevated:
            invocation_args = [self._config.sudo_path, "-n"] + invocation_args

        merged_env: Optional[dict[str, str]] = None
        if context.env is not None:
            merged_env = dict(os.environ)
            merged_env.update(context.env)

        start_monotonic = time.monotonic()
        result = self._invoke_subprocess(
            args=invocation_args,
            cwd=context.cwd,
            env=merged_env,
            timeout=context.timeout,
            capture_output=capture_output,
            on_output=on_output,
            on_error=on_error,
            stdin=stdin,
        )
        end_monotonic = time.monotonic()

        result.duration_ms = (end_monotonic - start_monotonic) * 1000.0
        self._update_metrics(result=result)
        final_result = self._apply_after_aspects(context=context, result=result)
        return final_result

    def _update_metrics(self, *, result: RunResult) -> None:
        """
            Update execution metrics with the outcome of a single
            subprocess invocation.  Mirrors the SSH runner's metric
            semantics; the SSH-specific ``connection_error_count`` and
            ``last_error_type`` fields are not touched here.
        """
        if result.exit_code == 0 and not result.timed_out:
            self._metrics.success_count += 1
        else:
            self._metrics.failure_count += 1

        if result.timed_out:
            self._metrics.timeout_count += 1

        if len(result.stderr) > 0 and result.exit_code != 0:
            self._metrics.last_error = result.stderr

        if result.duration_ms is not None:
            self._metrics.total_duration_ms += result.duration_ms
            if self._metrics.min_duration_ms is None:
                self._metrics.min_duration_ms = result.duration_ms
            elif result.duration_ms < self._metrics.min_duration_ms:
                self._metrics.min_duration_ms = result.duration_ms
            if self._metrics.max_duration_ms is None:
                self._metrics.max_duration_ms = result.duration_ms
            elif result.duration_ms > self._metrics.max_duration_ms:
                self._metrics.max_duration_ms = result.duration_ms
            total_count = self._metrics.success_count + self._metrics.failure_count
            if total_count > 0:
                self._metrics.avg_duration_ms = (
                    self._metrics.total_duration_ms / total_count
                )
        return

    def _apply_before_aspects(self, *, context: ExecutionContext) -> None:
        """
            Invoke the 'before_execute' phase on all enabled aspects.
        """
        for aspect in self._aspects:
            if aspect.enabled:
                aspect.before_execute(context)
        return

    def _apply_after_aspects(
        self,
        *,
        context: ExecutionContext,
        result: RunResult,
    ) -> RunResult:
        """
            Invoke the 'after_execute' phase on all enabled aspects in reverse
            registration order, threading the :class:`RunResult` through each.
        """
        current = result
        for aspect in reversed(self._aspects):
            if aspect.enabled:
                current = aspect.after_execute(context, current)
        return current

    def _invoke_subprocess(
        self,
        *,
        args: list[str],
        cwd: Optional[str],
        env: Optional[dict[str, str]],
        timeout: Optional[float],
        capture_output: bool,
        on_output: Optional[Callable[[str], None]],
        on_error: Optional[Callable[[str], None]],
        stdin: Optional[str] = None,
    ) -> RunResult:
        """
            Invoke :func:`subprocess.run` with the provided parameters and map
            the outcome to a :class:`RunResult`.
        """
        try:
            proc = subprocess.run(
                args,
                cwd=cwd,
                env=env,
                timeout=timeout,
                capture_output=capture_output,
                text=True,
                input=stdin,
            )
        except subprocess.TimeoutExpired as te:
            partial_stdout = te.stdout if te.stdout is not None else ""
            partial_stderr = te.stderr if te.stderr is not None else "Command timed out"
            if isinstance(partial_stdout, bytes):
                partial_stdout = partial_stdout.decode(errors="replace")
            if isinstance(partial_stderr, bytes):
                partial_stderr = partial_stderr.decode(errors="replace")

            timeout_result = RunResult(
                exit_code=-1,
                stdout=partial_stdout,
                stderr=partial_stderr,
                command=list(args),
                timed_out=True,
                transport="local",
            )
            return timeout_result

        stdout_text = proc.stdout if proc.stdout is not None else ""
        stderr_text = proc.stderr if proc.stderr is not None else ""

        if on_output is not None and len(stdout_text) > 0:
            on_output(stdout_text)
        if on_error is not None and len(stderr_text) > 0:
            on_error(stderr_text)

        subprocess_result = RunResult(
            exit_code=proc.returncode,
            stdout=stdout_text,
            stderr=stderr_text,
            command=list(args),
            transport="local",
        )
        return subprocess_result
