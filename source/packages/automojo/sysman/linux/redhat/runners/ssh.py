# automojo.sysman.linux.redhat.runners.ssh
#
# Command runner that executes commands over SSH with reliability tracking.

import shlex
import socket
import time

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional

from automojo.sysman.linux.redhat.runners.errors import SSHConnectionError
from automojo.sysman.linux.redhat.runners.models import (
    ExecutionContext,
    RunResult,
    SSHErrorType,
    SSHReliabilityMetrics,
)

if TYPE_CHECKING:
    from automojo.sysman.linux.redhat.aspects.base import IExecutionAspect


@dataclass
class SSHRunnerConfig:
    """
        Configuration for :class:`SSHCommandRunner`.

        :param host: Target hostname or IP address.
        :param username: Username for authentication.
        :param port: TCP port for SSH (default 22).
        :param password: Password credential (optional).
        :param key_filename: Path to a private key file (optional).
        :param key_data: Raw private key contents (optional).
        :param timeout: Socket timeout used during connection.
        :param keepalive_interval: Keepalive transport interval in seconds.
        :param max_connection_retries: Number of connection attempts before failure.
        :param retry_backoff_base: Base of the exponential backoff between retries.
        :param dry_run: When True, no connection is opened.
        :param sudo_path: Path to sudo used for elevation on the remote host.
        :param auto_add_host_key: When True, host keys are auto-accepted (use only in trusted environments).
    """

    host: str
    username: str
    port: int = 22
    password: Optional[str] = None
    key_filename: Optional[str] = None
    key_data: Optional[str] = None
    timeout: float = 30.0
    keepalive_interval: float = 60.0
    max_connection_retries: int = 3
    retry_backoff_base: float = 2.0
    dry_run: bool = False
    sudo_path: str = "/usr/bin/sudo"
    auto_add_host_key: bool = True


def _import_paramiko() -> Any:
    """
        Import paramiko lazily so that the package may be imported without
        the SSH dependency installed.  Raising here only affects callers that
        actually use SSH functionality.
    """
    try:
        import paramiko  # type: ignore
    except ImportError as ie:
        err_msg_lines = [
            "The 'paramiko' package is required for SSH execution."
        ]
        err_msg_lines.append("    INSTALL: pip install paramiko")
        err_msg = "\n".join(err_msg_lines)
        raise ImportError(err_msg) from ie

    return paramiko


class SSHCommandRunner:
    """
        :class:`ICommandRunner` implementation that executes commands on a
        remote host over SSH using paramiko.
    """

    def __init__(self, config: SSHRunnerConfig) -> None:
        """
            :param config: SSH runner configuration.
        """
        self._config: SSHRunnerConfig = config
        self._aspects: list["IExecutionAspect"] = []
        self._metrics: SSHReliabilityMetrics = SSHReliabilityMetrics()
        self._client: Any = None
        self._paramiko: Any = None
        self._last_connected: Optional[float] = None

        if not config.dry_run:
            self.connect()
        return

    @property
    def config(self) -> SSHRunnerConfig:
        """
            The active :class:`SSHRunnerConfig`.
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
            Unregister a previously registered aspect.
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

    def connect(self) -> None:
        """
            Establish an SSH connection with retry and exponential backoff.

            :raises SSHConnectionError: When all configured retries are exhausted.
        """
        paramiko = _import_paramiko()
        self._paramiko = paramiko

        last_error: Optional[Exception] = None
        last_error_type: SSHErrorType = SSHErrorType.UNKNOWN_ERROR

        for attempt in range(self._config.max_connection_retries):
            client = paramiko.SSHClient()
            if self._config.auto_add_host_key:
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(
                    hostname=self._config.host,
                    port=self._config.port,
                    username=self._config.username,
                    password=self._config.password,
                    key_filename=self._config.key_filename,
                    timeout=self._config.timeout,
                    allow_agent=True,
                    look_for_keys=True,
                )
                self._client = client
                self._last_connected = time.monotonic()
                self._start_keepalive()
                return
            except Exception as ex:
                last_error = ex
                last_error_type = self._classify_error(ex)
                self._metrics.connection_error_count += 1
                self._metrics.last_error = str(ex)
                self._metrics.last_error_type = last_error_type
                try:
                    client.close()
                except Exception:
                    pass

                if attempt < self._config.max_connection_retries - 1:
                    self._metrics.retry_count += 1
                    backoff = self._config.retry_backoff_base ** attempt
                    time.sleep(backoff)

        attempts = self._config.max_connection_retries
        last_msg = str(last_error) if last_error is not None else "unknown"
        raise SSHConnectionError(
            host=self._config.host,
            port=self._config.port,
            error_type=last_error_type,
            attempts=attempts,
            last_error=last_msg,
        )

    def disconnect(self) -> None:
        """
            Close the SSH connection if one is open.
        """
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        return

    def _start_keepalive(self) -> None:
        """
            Enable transport-level keepalive packets.
        """
        if self._client is None:
            return
        transport = self._client.get_transport()
        if transport is not None:
            transport.set_keepalive(int(self._config.keepalive_interval))
        return

    def _check_connection(self) -> bool:
        """
            Return True when the SSH transport is still active.
        """
        if self._client is None:
            return False
        transport = self._client.get_transport()
        if transport is None:
            return False
        is_active = transport.is_active()
        return is_active

    def _classify_error(self, error: Exception) -> SSHErrorType:
        """
            Map a paramiko or socket error to a :class:`SSHErrorType`.
        """
        paramiko = self._paramiko
        if paramiko is None:
            try:
                paramiko = _import_paramiko()
            except Exception:
                paramiko = None

        if paramiko is not None:
            if isinstance(error, paramiko.AuthenticationException):
                return SSHErrorType.AUTH_ERROR
            if isinstance(error, paramiko.BadHostKeyException):
                return SSHErrorType.HOST_KEY_ERROR

        if isinstance(error, socket.timeout):
            return SSHErrorType.TIMEOUT_ERROR

        if isinstance(error, socket.error):
            return SSHErrorType.NETWORK_ERROR

        error_text = str(error).lower()
        if "host key" in error_text:
            return SSHErrorType.HOST_KEY_ERROR
        if "timed out" in error_text or "timeout" in error_text:
            return SSHErrorType.TIMEOUT_ERROR
        if paramiko is not None and isinstance(error, paramiko.SSHException):
            return SSHErrorType.NETWORK_ERROR

        return SSHErrorType.UNKNOWN_ERROR

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
            Execute a command on the remote host and return a :class:`RunResult`.
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

        if not self._check_connection():
            self.connect()

        command_line = self._build_command(args=context.args, context=context)
        start_monotonic = time.monotonic()
        result = self._invoke_remote(
            command_line=command_line,
            context=context,
            on_output=on_output,
            on_error=on_error,
            stdin=stdin,
        )
        end_monotonic = time.monotonic()

        result.duration_ms = (end_monotonic - start_monotonic) * 1000.0
        self._update_metrics(result=result, error=None)

        final_result = self._apply_after_aspects(context=context, result=result)
        return final_result

    def _build_command(
        self,
        *,
        args: list[str],
        context: ExecutionContext,
    ) -> str:
        """
            Build the shell command line to run over SSH, combining
            environment exports, working-directory change, and elevation.
        """
        parts: list[str] = []

        if context.env is not None:
            for key, value in context.env.items():
                parts.append("export {}={}".format(key, shlex.quote(value)))

        if context.cwd is not None:
            parts.append("cd {}".format(shlex.quote(context.cwd)))

        command_args = list(args)
        if context.elevated:
            command_args = [self._config.sudo_path, "-n"] + command_args

        quoted_cmd = " ".join(shlex.quote(arg) for arg in command_args)
        parts.append(quoted_cmd)

        full_command = " && ".join(parts)
        return full_command

    def _invoke_remote(
        self,
        *,
        command_line: str,
        context: ExecutionContext,
        on_output: Optional[Callable[[str], None]],
        on_error: Optional[Callable[[str], None]],
        stdin: Optional[str] = None,
    ) -> RunResult:
        """
            Open an SSH channel, execute the command, and capture its output.
        """
        if self._client is None:
            raise SSHConnectionError(
                host=self._config.host,
                port=self._config.port,
                error_type=SSHErrorType.CHANNEL_ERROR,
                attempts=0,
                last_error="SSH client not connected",
            )

        transport = self._client.get_transport()
        if transport is None:
            raise SSHConnectionError(
                host=self._config.host,
                port=self._config.port,
                error_type=SSHErrorType.CHANNEL_ERROR,
                attempts=0,
                last_error="No active SSH transport",
            )

        channel = transport.open_session()
        if context.timeout is not None:
            channel.settimeout(context.timeout)

        channel.exec_command(command_line)
        if stdin is not None:
            try:
                stdin_payload = stdin if isinstance(stdin, bytes) else stdin.encode("utf-8")
                channel.sendall(stdin_payload)
            except Exception:
                pass
            try:
                channel.shutdown_write()
            except Exception:
                pass

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        timed_out = False

        try:
            stdout_data = channel.makefile("r", -1).read()
            stderr_data = channel.makefile_stderr("r", -1).read()
            if isinstance(stdout_data, bytes):
                stdout_text = stdout_data.decode(errors="replace")
            else:
                stdout_text = stdout_data
            if isinstance(stderr_data, bytes):
                stderr_text = stderr_data.decode(errors="replace")
            else:
                stderr_text = stderr_data
            stdout_chunks.append(stdout_text)
            stderr_chunks.append(stderr_text)
            exit_code = channel.recv_exit_status()
        except socket.timeout:
            timed_out = True
            try:
                channel.close()
            except Exception:
                pass
            exit_code = -1
        finally:
            try:
                channel.close()
            except Exception:
                pass

        stdout_text = "".join(stdout_chunks)
        stderr_text = "".join(stderr_chunks)

        if on_output is not None and len(stdout_text) > 0:
            on_output(stdout_text)
        if on_error is not None and len(stderr_text) > 0:
            on_error(stderr_text)

        if timed_out:
            stderr_text = stderr_text + "Command timed out"

        remote_result = RunResult(
            exit_code=exit_code,
            stdout=stdout_text,
            stderr=stderr_text,
            command=list(context.args),
            timed_out=timed_out,
            transport="ssh",
        )
        return remote_result

    def _update_metrics(
        self,
        *,
        result: RunResult,
        error: Optional[Exception],
    ) -> None:
        """
            Update the reliability metrics with the outcome of a single
            execution.
        """
        if result.exit_code == 0 and not result.timed_out:
            self._metrics.success_count += 1
        else:
            self._metrics.failure_count += 1

        if result.timed_out:
            self._metrics.timeout_count += 1

        if error is not None:
            self._metrics.connection_error_count += 1
            self._metrics.last_error = str(error)
            self._metrics.last_error_type = self._classify_error(error)

        if result.duration_ms is not None:
            self._metrics.total_duration_ms += result.duration_ms
            if self._metrics.min_duration_ms is None:
                self._metrics.min_duration_ms = result.duration_ms
            else:
                if result.duration_ms < self._metrics.min_duration_ms:
                    self._metrics.min_duration_ms = result.duration_ms
            if self._metrics.max_duration_ms is None:
                self._metrics.max_duration_ms = result.duration_ms
            else:
                if result.duration_ms > self._metrics.max_duration_ms:
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

    def __enter__(self) -> "SSHCommandRunner":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.disconnect()
        return
