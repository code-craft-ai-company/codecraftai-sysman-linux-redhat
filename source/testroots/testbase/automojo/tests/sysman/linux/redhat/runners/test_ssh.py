# Tests for SSHCommandRunner.  Uses a fake paramiko module so the tests do
# not require an actual SSH server.

import socket
import sys
import types

from typing import Any, Optional
from unittest import mock

from automojo import testbase


class _FakeAuthException(Exception):
    pass


class _FakeBadHostKeyException(Exception):
    pass


class _FakeSSHException(Exception):
    pass


class _FakeAutoAddPolicy:
    pass


class _FakeTransport:

    def __init__(self) -> None:
        self._active: bool = True
        self._session: _FakeChannel = _FakeChannel()
        self.keepalive_seconds: Optional[int] = None
        return

    def is_active(self) -> bool:
        return self._active

    def set_keepalive(self, seconds: int) -> None:
        self.keepalive_seconds = seconds
        return

    def open_session(self) -> "_FakeChannel":
        return self._session

    def set_inactive(self) -> None:
        self._active = False
        return


class _FakeChannel:

    def __init__(self) -> None:
        self.stdout_data: bytes = b""
        self.stderr_data: bytes = b""
        self.exit_status: int = 0
        self.executed_command: Optional[str] = None
        self.timeout_seconds: Optional[float] = None
        self.should_timeout: bool = False
        return

    def settimeout(self, seconds: float) -> None:
        self.timeout_seconds = seconds
        return

    def exec_command(self, command: str) -> None:
        self.executed_command = command
        return

    def makefile(self, mode: str, bufsize: int):
        if self.should_timeout:
            raise socket.timeout("read timed out")

        class _Buf:
            def __init__(self, data: bytes) -> None:
                self._data = data
                return

            def read(self) -> bytes:
                return self._data

        buf = _Buf(self.stdout_data)
        return buf

    def makefile_stderr(self, mode: str, bufsize: int):
        class _Buf:
            def __init__(self, data: bytes) -> None:
                self._data = data
                return

            def read(self) -> bytes:
                return self._data

        buf = _Buf(self.stderr_data)
        return buf

    def recv_exit_status(self) -> int:
        return self.exit_status

    def close(self) -> None:
        return


class _FakeSSHClient:

    def __init__(self) -> None:
        self.connect_calls: list[dict] = []
        self.closed: bool = False
        self.transport: _FakeTransport = _FakeTransport()
        self.policy: Any = None
        self.raise_on_connect: Optional[Exception] = None
        return

    def set_missing_host_key_policy(self, policy: Any) -> None:
        self.policy = policy
        return

    def connect(self, **kwargs: Any) -> None:
        self.connect_calls.append(kwargs)
        if self.raise_on_connect is not None:
            raise self.raise_on_connect
        return

    def get_transport(self) -> Optional[_FakeTransport]:
        if self.closed:
            return None
        return self.transport

    def close(self) -> None:
        self.closed = True
        return


class _ClientSequence:
    """
        Returns a different :class:`_FakeSSHClient` for each invocation so we
        can simulate multiple connect attempts.
    """

    def __init__(self, clients: list[_FakeSSHClient]) -> None:
        self._clients: list[_FakeSSHClient] = clients
        self._index: int = 0
        return

    def __call__(self) -> _FakeSSHClient:
        client = self._clients[self._index]
        self._index = self._index + 1
        return client


def _make_fake_paramiko_module(client_factory: Any) -> types.ModuleType:
    """
        Build a throwaway paramiko module containing the symbols
        :class:`SSHCommandRunner` expects.
    """
    module = types.ModuleType("paramiko")
    module.AuthenticationException = _FakeAuthException
    module.BadHostKeyException = _FakeBadHostKeyException
    module.SSHException = _FakeSSHException
    module.AutoAddPolicy = _FakeAutoAddPolicy
    module.SSHClient = client_factory
    return module


def _install_paramiko(module: types.ModuleType) -> Optional[types.ModuleType]:
    """
        Replace sys.modules['paramiko'] and return the previous module (if any).
    """
    previous = sys.modules.get("paramiko")
    sys.modules["paramiko"] = module
    return previous


def _restore_paramiko(previous: Optional[types.ModuleType]) -> None:
    """
        Restore a previously installed paramiko module, or remove if none.
    """
    if previous is None:
        sys.modules.pop("paramiko", None)
    else:
        sys.modules["paramiko"] = previous
    return


def test_connect_on_init():
    """
        Constructing an SSHCommandRunner must establish the connection.
    """
    client = _FakeSSHClient()
    fake_paramiko = _make_fake_paramiko_module(client_factory=lambda: client)
    previous = _install_paramiko(fake_paramiko)
    try:
        from automojo.sysman.linux.redhat.runners.ssh import (
            SSHCommandRunner,
            SSHRunnerConfig,
        )
        runner = SSHCommandRunner(SSHRunnerConfig(
            host="example.test",
            username="root",
        ))
        testbase.assert_equal(len(client.connect_calls), 1)
        testbase.assert_equal(client.connect_calls[0]["hostname"], "example.test")
        testbase.assert_equal(client.connect_calls[0]["username"], "root")
        runner.disconnect()
    finally:
        _restore_paramiko(previous)
    return


def test_connect_retries_then_succeeds():
    """
        The connect retry loop must retry after a transient failure.
    """
    bad_client = _FakeSSHClient()
    bad_client.raise_on_connect = _FakeSSHException("boom")
    good_client = _FakeSSHClient()
    sequence = _ClientSequence([bad_client, good_client])
    fake_paramiko = _make_fake_paramiko_module(client_factory=sequence)
    previous = _install_paramiko(fake_paramiko)
    try:
        from automojo.sysman.linux.redhat.runners.ssh import (
            SSHCommandRunner,
            SSHRunnerConfig,
        )
        with mock.patch("time.sleep"):
            runner = SSHCommandRunner(SSHRunnerConfig(
                host="h",
                username="u",
                max_connection_retries=2,
                retry_backoff_base=1.0,
            ))
        metrics = runner.get_metrics()
        testbase.assert_true(metrics is not None)
        testbase.assert_equal(metrics.connection_error_count, 1)
        testbase.assert_equal(metrics.retry_count, 1)
        runner.disconnect()
    finally:
        _restore_paramiko(previous)
    return


def test_connect_all_retries_fail_raises():
    """
        When retries are exhausted an SSHConnectionError must be raised.
    """
    clients = [_FakeSSHClient(), _FakeSSHClient(), _FakeSSHClient()]
    for c in clients:
        c.raise_on_connect = _FakeAuthException("bad creds")
    sequence = _ClientSequence(clients)
    fake_paramiko = _make_fake_paramiko_module(client_factory=sequence)
    previous = _install_paramiko(fake_paramiko)
    try:
        from automojo.sysman.linux.redhat.runners.ssh import (
            SSHCommandRunner,
            SSHRunnerConfig,
        )
        from automojo.sysman.linux.redhat.runners.errors import SSHConnectionError
        from automojo.sysman.linux.redhat.runners.models import SSHErrorType

        raised: Optional[SSHConnectionError] = None
        with mock.patch("time.sleep"):
            try:
                SSHCommandRunner(SSHRunnerConfig(
                    host="h",
                    username="u",
                    max_connection_retries=3,
                    retry_backoff_base=1.0,
                ))
            except SSHConnectionError as ex:
                raised = ex

        testbase.assert_true(raised is not None)
        testbase.assert_equal(raised.error_type, SSHErrorType.AUTH_ERROR)
        testbase.assert_equal(raised.attempts, 3)
    finally:
        _restore_paramiko(previous)
    return


def test_dry_run_skips_connect():
    """
        dry_run must prevent any connection attempts at construction or run time.
    """
    client = _FakeSSHClient()
    fake_paramiko = _make_fake_paramiko_module(client_factory=lambda: client)
    previous = _install_paramiko(fake_paramiko)
    try:
        from automojo.sysman.linux.redhat.runners.ssh import (
            SSHCommandRunner,
            SSHRunnerConfig,
        )
        runner = SSHCommandRunner(SSHRunnerConfig(
            host="h", username="u", dry_run=True,
        ))
        testbase.assert_equal(len(client.connect_calls), 0)
        result = runner.run(["ls"])
        testbase.assert_true(result.dry_run)
        testbase.assert_equal(result.exit_code, 0)
        testbase.assert_equal(result.transport, "ssh")
    finally:
        _restore_paramiko(previous)
    return


def _make_runner_with_client(client: _FakeSSHClient):
    """
        Install a fake paramiko backed by the given client and build a runner.
        Returns a tuple ``(runner, restore_callback)``.
    """
    fake_paramiko = _make_fake_paramiko_module(client_factory=lambda: client)
    previous = _install_paramiko(fake_paramiko)
    from automojo.sysman.linux.redhat.runners.ssh import (
        SSHCommandRunner,
        SSHRunnerConfig,
    )
    runner = SSHCommandRunner(SSHRunnerConfig(host="h", username="u"))

    def _restore():
        _restore_paramiko(previous)
        return

    return runner, _restore


def test_run_executes_command_and_returns_output():
    """
        run() must execute the built command and produce a RunResult.
    """
    client = _FakeSSHClient()
    client.transport._session.stdout_data = b"hello\n"
    client.transport._session.stderr_data = b""
    client.transport._session.exit_status = 0
    runner, restore = _make_runner_with_client(client)
    try:
        result = runner.run(["echo", "hello"])
        testbase.assert_equal(result.exit_code, 0)
        testbase.assert_equal(result.stdout, "hello\n")
        testbase.assert_equal(result.transport, "ssh")
        testbase.assert_false(result.timed_out)
        metrics = runner.get_metrics()
        testbase.assert_equal(metrics.success_count, 1)
        testbase.assert_true(metrics.avg_duration_ms is not None)
    finally:
        restore()
    return


def test_run_elevation_and_cwd_and_env():
    """
        Elevation, cwd, and env options must all appear in the built command.
    """
    client = _FakeSSHClient()
    client.transport._session.stdout_data = b""
    client.transport._session.stderr_data = b""
    client.transport._session.exit_status = 0
    runner, restore = _make_runner_with_client(client)
    try:
        runner.run(
            ["ls"],
            elevated=True,
            cwd="/opt/app",
            env={"FOO": "bar baz"},
        )
        command_sent = client.transport._session.executed_command
        testbase.assert_true("export FOO='bar baz'" in command_sent)
        testbase.assert_true("cd /opt/app" in command_sent)
        testbase.assert_true("/usr/bin/sudo -n ls" in command_sent)
    finally:
        restore()
    return


def test_timeout_marks_result():
    """
        A socket timeout during read must mark the RunResult as timed_out.
    """
    client = _FakeSSHClient()
    client.transport._session.should_timeout = True
    runner, restore = _make_runner_with_client(client)
    try:
        result = runner.run(["sleep", "100"], timeout=1.0)
        testbase.assert_true(result.timed_out)
        testbase.assert_equal(result.exit_code, -1)
        metrics = runner.get_metrics()
        testbase.assert_equal(metrics.timeout_count, 1)
    finally:
        restore()
    return


def test_metrics_accumulate():
    """
        Successive executions must accumulate distinct success/failure counts.
    """
    client = _FakeSSHClient()
    client.transport._session.exit_status = 0
    runner, restore = _make_runner_with_client(client)
    try:
        runner.run(["true"])
        client.transport._session.exit_status = 1
        runner.run(["false"])
        metrics = runner.get_metrics()
        testbase.assert_equal(metrics.success_count, 1)
        testbase.assert_equal(metrics.failure_count, 1)
    finally:
        restore()
    return


def test_reset_metrics():
    """
        reset_metrics() must return all counters to zero.
    """
    client = _FakeSSHClient()
    client.transport._session.exit_status = 0
    runner, restore = _make_runner_with_client(client)
    try:
        runner.run(["true"])
        runner.reset_metrics()
        metrics = runner.get_metrics()
        testbase.assert_equal(metrics.success_count, 0)
        testbase.assert_equal(metrics.failure_count, 0)
    finally:
        restore()
    return


def test_reconnect_when_transport_inactive():
    """
        If the transport becomes inactive the runner must reconnect before run.
    """
    first_client = _FakeSSHClient()
    second_client = _FakeSSHClient()
    second_client.transport._session.stdout_data = b""
    second_client.transport._session.exit_status = 0
    sequence = _ClientSequence([first_client, second_client])
    fake_paramiko = _make_fake_paramiko_module(client_factory=sequence)
    previous = _install_paramiko(fake_paramiko)
    try:
        from automojo.sysman.linux.redhat.runners.ssh import (
            SSHCommandRunner,
            SSHRunnerConfig,
        )
        runner = SSHCommandRunner(SSHRunnerConfig(host="h", username="u"))
        first_client.transport.set_inactive()
        runner.run(["true"])
        testbase.assert_equal(len(second_client.connect_calls), 1)
    finally:
        _restore_paramiko(previous)
    return


def test_classifies_auth_error():
    """
        Paramiko AuthenticationException must be classified as AUTH_ERROR.
    """
    client = _FakeSSHClient()
    runner, restore = _make_runner_with_client(client)
    try:
        from automojo.sysman.linux.redhat.runners.models import SSHErrorType
        err_type = runner._classify_error(_FakeAuthException("bad"))
        testbase.assert_equal(err_type, SSHErrorType.AUTH_ERROR)
    finally:
        restore()
    return


def test_classifies_socket_timeout():
    """
        socket.timeout must be classified as TIMEOUT_ERROR.
    """
    client = _FakeSSHClient()
    runner, restore = _make_runner_with_client(client)
    try:
        from automojo.sysman.linux.redhat.runners.models import SSHErrorType
        err_type = runner._classify_error(socket.timeout())
        testbase.assert_equal(err_type, SSHErrorType.TIMEOUT_ERROR)
    finally:
        restore()
    return
