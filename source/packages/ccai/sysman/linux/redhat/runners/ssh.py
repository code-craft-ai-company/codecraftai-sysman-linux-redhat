# ccai.sysman.linux.redhat.runners.ssh
#
# Command runner that adapts codecraftai-interop's SshAgent to the
# ICommandRunner protocol.  The SshAgent provides the transport,
# credential handling, jump-host support, multi-user exec, PTY, and
# file transfer; this module contributes only:
#   - ICommandRunner-shaped glue (argv + cwd + env + elevated -> shell line)
#   - Stdin delivery (base64 + pipe) for "tee"-style file writes
#   - Reliability metrics bookkeeping around each execution
#   - Aspect chain composition matching LocalCommandRunner's behaviour
#
# Callers use the same ICommandRunner surface regardless of transport.

import base64
import shlex
import socket
import time

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional

from ccai.sysman.linux.redhat.runners.models import (
    ExecutionContext,
    RunResult,
    SSHErrorType,
    SSHReliabilityMetrics,
)

if TYPE_CHECKING:
    from ccai.sysman.linux.redhat.aspects.base import IExecutionAspect


def _import_ssh_deps() -> tuple:
    """
        Lazily import codecraftai-interop and paramiko so the package may be
        imported without the SSH dependency installed.  Raising here only
        affects callers that actually use SSH functionality.
    """
    try:
        from ccai.interop.aspects import AspectsCmd
        from ccai.interop.protocols.ssh.sshagent import SshAgent
        from ccai.credentials.sshcredential import SshCredential
        import paramiko
    except ImportError as ie:
        err_msg_lines = [
            "SSH execution requires 'codecraftai-interop', 'codecraftai-credentials', and 'paramiko'."
        ]
        err_msg_lines.append("    INSTALL: add the packages to your environment")
        err_msg = "\n".join(err_msg_lines)
        raise ImportError(err_msg) from ie

    deps = (SshAgent, AspectsCmd, SshCredential, paramiko)
    return deps


@dataclass
class SSHRunnerConfig:
    """
        Configuration for :class:`SSHCommandRunner`.

        Callers may either pass an already-constructed ``agent`` or the
        individual fields needed to construct one.  When ``agent`` is
        provided, all credential/host fields are ignored.

        :param agent: Optional pre-built :class:`SshAgent` instance.
        :param host: Target host (required when ``agent`` is None).
        :param username: SSH username (required when ``agent`` is None and
                         no ``credential`` is supplied).
        :param port: TCP port for SSH (default 22).
        :param password: Password credential (optional).
        :param key_filename: Path to a private key file (optional).
        :param credential: Optional pre-built :class:`SshCredential`.
        :param credential_identifier: Identifier used when auto-building a
                                      credential (default ``"sshrunner"``).
        :param aspects: Default :class:`AspectsCmd` used for ``run_cmd``
                        calls.  When None a single-call, wide-expected-status
                        AspectsCmd is used so every exit code is surfaced
                        without raising an AssertionError.
        :param dry_run: When True, no connection is opened and no commands
                        are dispatched.
        :param sudo_path: Path to sudo used for elevation on the remote host.
        :param default_timeout: Default completion timeout (seconds) when
                                the caller does not supply one.
    """

    agent: Optional[Any] = None
    host: Optional[str] = None
    username: Optional[str] = None
    port: int = 22
    password: Optional[str] = None
    key_filename: Optional[str] = None
    credential: Optional[Any] = None
    credential_identifier: str = "sshrunner"
    aspects: Optional[Any] = None
    dry_run: bool = False
    sudo_path: str = "/usr/bin/sudo"
    default_timeout: float = 60.0


class SSHCommandRunner:
    """
        :class:`ICommandRunner` implementation that delegates SSH execution
        to :class:`ccai.interop.protocols.ssh.sshagent.SshAgent`.
    """

    def __init__(self, config: SSHRunnerConfig) -> None:
        """
            :param config: SSH runner configuration.
        """
        self._config: SSHRunnerConfig = config
        self._aspects: list["IExecutionAspect"] = []
        self._metrics: SSHReliabilityMetrics = SSHReliabilityMetrics()
        self._agent: Any = None
        self._ssh_agent_cls: Any = None
        self._aspects_cmd_cls: Any = None
        self._ssh_credential_cls: Any = None
        self._paramiko_module: Any = None

        if not config.dry_run:
            self._ensure_agent()
        return

    @property
    def config(self) -> SSHRunnerConfig:
        """
            The active :class:`SSHRunnerConfig`.
        """
        return self._config

    @property
    def agent(self) -> Any:
        """
            The underlying :class:`SshAgent`, or None in dry-run mode.
        """
        return self._agent

    @property
    def aspects(self) -> list["IExecutionAspect"]:
        """
            Snapshot of registered execution aspects in registration order.
        """
        rtnval = list(self._aspects)
        return rtnval

    def add_aspect(self, aspect: "IExecutionAspect") -> None:
        """
            Register an execution aspect on this runner.
        """
        self._aspects.append(aspect)
        return

    def remove_aspect(self, aspect: "IExecutionAspect") -> None:
        """
            Unregister a previously registered execution aspect.
        """
        if aspect in self._aspects:
            self._aspects.remove(aspect)
        return

    def get_metrics(self) -> Optional[SSHReliabilityMetrics]:
        """
            Return the accumulated reliability metrics for this runner.
        """
        return self._metrics

    def reset_metrics(self) -> None:
        """
            Reset all reliability counters and timing state.
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
            Execute a command on the remote host through the wrapped
            :class:`SshAgent` and return a :class:`RunResult`.
        """
        context = ExecutionContext(
            args=list(args),
            elevated=elevated,
            cwd=cwd,
            env=env,
            timeout=timeout,
            transport="ssh",
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
                transport="ssh",
            )
            final_dry = self._apply_after_aspects(context=context, result=dry_result)
            return final_dry

        self._ensure_agent()

        command_line = _build_shell_command(
            args=context.args,
            context=context,
            sudo_path=self._config.sudo_path,
            stdin_payload=stdin,
        )

        aspects_cmd = self._aspects_for_run(timeout=context.timeout)

        start_monotonic = time.monotonic()
        error: Optional[Exception] = None
        timed_out = False
        try:
            status, stdout_text, stderr_text = self._agent.run_cmd(
                command_line, aspects=aspects_cmd,
            )
        except socket.timeout as ste:
            error = ste
            timed_out = True
            status, stdout_text, stderr_text = -1, "", "Command timed out"
        except TimeoutError as toe:
            error = toe
            timed_out = True
            status, stdout_text, stderr_text = -1, "", str(toe) or "Command timed out"
        except Exception as ex:
            error = ex
            status, stdout_text, stderr_text = -1, "", str(ex)
        end_monotonic = time.monotonic()

        if on_output is not None and len(stdout_text) > 0:
            on_output(stdout_text)
        if on_error is not None and len(stderr_text) > 0:
            on_error(stderr_text)

        result = RunResult(
            exit_code=status if status is not None else -1,
            stdout=stdout_text if stdout_text is not None else "",
            stderr=stderr_text if stderr_text is not None else "",
            command=list(context.args),
            timed_out=timed_out,
            transport="ssh",
            duration_ms=(end_monotonic - start_monotonic) * 1000.0,
        )

        self._update_metrics(result=result, error=error)
        final_result = self._apply_after_aspects(context=context, result=result)
        return final_result

    def _ensure_agent(self) -> None:
        """
            Import the SSH dependencies and build an :class:`SshAgent` on
            first use when one was not provided via config.
        """
        if self._agent is not None:
            return

        ssh_agent_cls, aspects_cmd_cls, ssh_credential_cls, paramiko_module = _import_ssh_deps()
        self._ssh_agent_cls = ssh_agent_cls
        self._aspects_cmd_cls = aspects_cmd_cls
        self._ssh_credential_cls = ssh_credential_cls
        self._paramiko_module = paramiko_module

        if self._config.agent is not None:
            self._agent = self._config.agent
            return

        if self._config.host is None:
            raise ValueError(
                "SSHRunnerConfig requires 'host' when 'agent' is not provided"
            )

        credential = self._config.credential
        if credential is None:
            if self._config.username is None:
                raise ValueError(
                    "SSHRunnerConfig requires 'username' when 'credential' is not provided"
                )
            credential = ssh_credential_cls(
                identifier=self._config.credential_identifier,
                categories=["ssh"],
                username=self._config.username,
                password=self._config.password,
                keyfile=self._config.key_filename,
                allow_agent=True,
            )

        self._agent = ssh_agent_cls(
            host=self._config.host,
            primary_credential=credential,
            port=self._config.port,
        )
        return

    def _aspects_for_run(
        self,
        *,
        timeout: Optional[float],
    ) -> Any:
        """
            Build an :class:`AspectsCmd` that permits any exit code and
            honours the caller-supplied timeout.
        """
        if self._aspects_cmd_cls is None:
            self._ensure_agent()

        base_aspects = self._config.aspects
        completion_timeout = timeout if timeout is not None else self._config.default_timeout

        permissive_expected = tuple(range(0, 256))

        if base_aspects is None:
            aspects_cmd = self._aspects_cmd_cls(
                expected_status=permissive_expected,
                completion_timeout=completion_timeout,
            )
            return aspects_cmd

        aspects_cmd = self._aspects_cmd_cls(
            expected_status=permissive_expected,
            user=getattr(base_aspects, "user", None),
            pty_params=getattr(base_aspects, "pty_params", None),
            action_pattern=getattr(base_aspects, "action_pattern"),
            completion_timeout=completion_timeout,
            completion_interval=getattr(base_aspects, "completion_interval"),
            connection_timeout=getattr(base_aspects, "connection_timeout"),
            connection_interval=getattr(base_aspects, "connection_interval"),
            inactivity_timeout=getattr(base_aspects, "inactivity_timeout"),
            inactivity_interval=getattr(base_aspects, "inactivity_interval"),
            monitor_delay=getattr(base_aspects, "monitor_delay"),
            logging_pattern=getattr(base_aspects, "logging_pattern"),
            retry_logging_interval=getattr(base_aspects, "retry_logging_interval"),
            allowed_error_codes=getattr(base_aspects, "allowed_error_codes"),
            allowed_connection_exceptions=getattr(base_aspects, "allowed_connection_exceptions"),
        )
        return aspects_cmd

    def _classify_error(self, error: Exception) -> SSHErrorType:
        """
            Map an exception surfaced by SshAgent into a :class:`SSHErrorType`.
        """
        paramiko_module = self._paramiko_module
        if paramiko_module is not None:
            auth_exc = getattr(paramiko_module, "AuthenticationException", None)
            bad_host_exc = getattr(paramiko_module, "BadHostKeyException", None)
            ssh_exc = getattr(paramiko_module, "SSHException", None)
            channel_exc = getattr(paramiko_module, "ChannelException", None)
            if auth_exc is not None and isinstance(error, auth_exc):
                return SSHErrorType.AUTH_ERROR
            if bad_host_exc is not None and isinstance(error, bad_host_exc):
                return SSHErrorType.HOST_KEY_ERROR
            if channel_exc is not None and isinstance(error, channel_exc):
                return SSHErrorType.CHANNEL_ERROR
            if ssh_exc is not None and isinstance(error, ssh_exc):
                return SSHErrorType.NETWORK_ERROR

        if isinstance(error, socket.timeout):
            return SSHErrorType.TIMEOUT_ERROR
        if isinstance(error, TimeoutError):
            return SSHErrorType.TIMEOUT_ERROR
        if isinstance(error, socket.error):
            return SSHErrorType.NETWORK_ERROR

        error_text = str(error).lower()
        if "host key" in error_text:
            return SSHErrorType.HOST_KEY_ERROR
        if "timed out" in error_text or "timeout" in error_text:
            return SSHErrorType.TIMEOUT_ERROR

        return SSHErrorType.UNKNOWN_ERROR

    def _update_metrics(
        self,
        *,
        result: RunResult,
        error: Optional[Exception],
    ) -> None:
        """
            Update reliability metrics with the outcome of a single execution.
        """
        if result.exit_code == 0 and not result.timed_out:
            self._metrics.success_count += 1
        else:
            self._metrics.failure_count += 1

        if result.timed_out:
            self._metrics.timeout_count += 1

        if error is not None:
            self._metrics.last_error = str(error)
            self._metrics.last_error_type = self._classify_error(error)
            if not isinstance(error, socket.timeout) and not isinstance(error, TimeoutError):
                self._metrics.connection_error_count += 1

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
            Invoke the 'after_execute' phase on all enabled aspects in
            reverse registration order.
        """
        current = result
        for aspect in reversed(self._aspects):
            if aspect.enabled:
                current = aspect.after_execute(context, current)
        return current


def _build_shell_command(
    *,
    args: list[str],
    context: ExecutionContext,
    sudo_path: str,
    stdin_payload: Optional[str],
) -> str:
    """
        Build the shell command line dispatched to :meth:`SshAgent.run_cmd`.

        Incorporates optional environment exports, working-directory change,
        elevation, and (when ``stdin_payload`` is non-None) a base64
        encoded stdin stream piped into the command.  Base64 avoids heredoc
        marker collisions and binary-safety issues for arbitrary payloads.
    """
    parts: list[str] = []

    if context.env is not None:
        for key, value in context.env.items():
            parts.append("export {}={}".format(key, shlex.quote(value)))

    if context.cwd is not None:
        parts.append("cd {}".format(shlex.quote(context.cwd)))

    command_args = list(args)
    if context.elevated:
        command_args = [sudo_path, "-n"] + command_args

    quoted_cmd = " ".join(shlex.quote(arg) for arg in command_args)

    if stdin_payload is None:
        parts.append(quoted_cmd)
    else:
        encoded = base64.b64encode(stdin_payload.encode("utf-8")).decode("ascii")
        piped = "printf '%s' {} | base64 -d | {}".format(
            shlex.quote(encoded), quoted_cmd,
        )
        parts.append(piped)

    full_command = " && ".join(parts)
    return full_command
