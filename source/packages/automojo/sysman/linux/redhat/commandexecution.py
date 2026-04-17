# automojo.sysman.linux.redhat.commandexecution
#
# CommandExecutor implementation that delegates to an ICommandRunner for
# execution.  Provides higher-level conveniences such as command sequences
# and script execution without embedding any subprocess logic.

import os
import tempfile

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from automojo.sysman.linux.redhat.runners.base import ICommandRunner


@dataclass
class CommandExecutorConfig:
    """
        Configuration for :class:`CommandExecutor`.

        :param dry_run: When True, commands are not dispatched to the runner.
    """

    dry_run: bool = False


@dataclass
class CommandSpec:
    """
        Specification for a single command within a sequence.

        :param args: Argument vector for the command.
        :param elevated: Whether to run the command with elevated privileges.
        :param working_dir: Working directory for the command.
        :param env: Environment overrides for the command.
        :param timeout_seconds: Per-command timeout in seconds.
        :param on_output: Callback invoked with captured stdout.
        :param on_error: Callback invoked with captured stderr.
        :param label: Human-readable label for reporting purposes.
    """

    args: list[str]
    elevated: bool = False
    working_dir: Optional[str] = None
    env: Optional[dict[str, str]] = None
    timeout_seconds: Optional[float] = None
    on_output: Optional[Callable[[str], None]] = None
    on_error: Optional[Callable[[str], None]] = None
    label: Optional[str] = None


@dataclass
class CommandResult:
    """
        Outcome of a single command execution as returned by
        :class:`CommandExecutor`.

        :param success: True when the command completed with exit code 0.
        :param exit_code: Process exit code, or None for synthetic results.
        :param stdout: Captured standard output.
        :param stderr: Captured standard error.
        :param args: Argument vector that was executed.
        :param timed_out: True if the command exceeded its timeout.
        :param dry_run: True if the result was produced in dry-run mode.
    """

    success: bool
    exit_code: Optional[int]
    stdout: str
    stderr: str
    args: list[str]
    timed_out: bool = False
    dry_run: bool = False


@dataclass
class SequenceResult:
    """
        Outcome of a sequence of command executions.

        :param all_succeeded: True when every command succeeded.
        :param results: Per-command results in execution order.
        :param failed_at: Index of the first failed command, or None.
        :param dry_run: True if the sequence ran in dry-run mode.
    """

    all_succeeded: bool
    results: list[CommandResult] = field(default_factory=list)
    failed_at: Optional[int] = None
    dry_run: bool = False


_TIMEOUT_MESSAGE = "Command execution timed out"


class CommandExecutor:
    """
        Executes commands, sequences, and scripts through an
        :class:`ICommandRunner` without performing any subprocess work itself.
    """

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[CommandExecutorConfig] = None,
    ) -> None:
        """
            :param runner: Runner used to dispatch commands.
            :param config: Executor configuration.  Defaults are used if None.
        """
        if config is None:
            config = CommandExecutorConfig()

        self._runner: ICommandRunner = runner
        self._config: CommandExecutorConfig = config
        return

    @property
    def config(self) -> CommandExecutorConfig:
        """
            The active :class:`CommandExecutorConfig`.
        """
        return self._config

    @property
    def runner(self) -> ICommandRunner:
        """
            The injected :class:`ICommandRunner`.
        """
        return self._runner

    def run(
        self,
        args: list[str],
        *,
        elevated: bool = False,
        working_dir: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
        timeout_seconds: Optional[float] = None,
        on_output: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> CommandResult:
        """
            Execute a single command and return its :class:`CommandResult`.
        """
        if self._config.dry_run:
            dry_result = CommandResult(
                success=True,
                exit_code=0,
                stdout="",
                stderr="",
                args=list(args),
                dry_run=True,
            )
            return dry_result

        if working_dir is not None:
            path = Path(working_dir)
            if not path.is_dir():
                invalid_dir_result = CommandResult(
                    success=False,
                    exit_code=1,
                    stdout="",
                    stderr="working_dir does not exist: {}".format(working_dir),
                    args=list(args),
                )
                return invalid_dir_result

        merged_env: Optional[dict[str, str]] = None
        if env is not None:
            merged_env = dict(os.environ)
            merged_env.update(env)

        run_result = self._runner.run(
            list(args),
            elevated=elevated,
            cwd=working_dir,
            env=merged_env,
            timeout=timeout_seconds,
            on_output=on_output,
            on_error=on_error,
        )

        stderr_text = run_result.stderr
        if run_result.timed_out and len(stderr_text) == 0:
            stderr_text = _TIMEOUT_MESSAGE

        command_result = CommandResult(
            success=(run_result.exit_code == 0) and (not run_result.timed_out),
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=stderr_text,
            args=list(args),
            timed_out=run_result.timed_out,
            dry_run=run_result.dry_run,
        )
        return command_result

    def run_sequence(
        self,
        commands: list[CommandSpec],
        *,
        stop_on_failure: bool = True,
    ) -> SequenceResult:
        """
            Execute an ordered sequence of commands.

            :param commands: The commands to execute.
            :param stop_on_failure: When True, the sequence halts at the first
                                    failing command.

            :returns: The :class:`SequenceResult` describing the run.
        """
        if len(commands) == 0:
            empty_result = SequenceResult(all_succeeded=True)
            return empty_result

        if self._config.dry_run:
            dry_results: list[CommandResult] = []
            for spec in commands:
                dry_results.append(CommandResult(
                    success=True,
                    exit_code=0,
                    stdout="",
                    stderr="",
                    args=list(spec.args),
                    dry_run=True,
                ))
            dry_sequence_result = SequenceResult(
                all_succeeded=True,
                results=dry_results,
                failed_at=None,
                dry_run=True,
            )
            return dry_sequence_result

        results: list[CommandResult] = []
        failed_at: Optional[int] = None

        for index, spec in enumerate(commands):
            one_result = self.run(
                spec.args,
                elevated=spec.elevated,
                working_dir=spec.working_dir,
                env=spec.env,
                timeout_seconds=spec.timeout_seconds,
                on_output=spec.on_output,
                on_error=spec.on_error,
            )
            results.append(one_result)

            if not one_result.success and failed_at is None:
                failed_at = index
                if stop_on_failure:
                    stop_result = SequenceResult(
                        all_succeeded=False,
                        results=results,
                        failed_at=failed_at,
                    )
                    return stop_result

        all_succeeded = True
        for r in results:
            if not r.success:
                all_succeeded = False
                break

        sequence_result = SequenceResult(
            all_succeeded=all_succeeded,
            results=results,
            failed_at=failed_at,
        )
        return sequence_result

    def run_script(
        self,
        script: str,
        *,
        elevated: bool = False,
        working_dir: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
        timeout_seconds: Optional[float] = None,
        on_output: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> CommandResult:
        """
            Write a shell script to a temporary file and execute it with bash.
        """
        if self._config.dry_run:
            dry_result = CommandResult(
                success=True,
                exit_code=0,
                stdout="",
                stderr="",
                args=["bash", "<dry-run>"],
                dry_run=True,
            )
            return dry_result

        tmp = tempfile.NamedTemporaryFile(
            prefix="automojo_script_",
            suffix=".sh",
            delete=False,
            mode="w",
            encoding="utf-8",
        )
        script_path = tmp.name
        try:
            tmp.write(script)
            tmp.close()
            os.chmod(script_path, 0o755)

            invocation_args = ["bash", script_path]
            run_outcome = self.run(
                invocation_args,
                elevated=elevated,
                working_dir=working_dir,
                env=env,
                timeout_seconds=timeout_seconds,
                on_output=on_output,
                on_error=on_error,
            )
        finally:
            Path(script_path).unlink(missing_ok=True)

        return run_outcome
