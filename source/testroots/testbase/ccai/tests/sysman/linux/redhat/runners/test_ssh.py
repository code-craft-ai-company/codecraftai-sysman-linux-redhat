# Tests for the SSHCommandRunner adapter that wraps SshAgent.
#
# The adapter itself is transport-agnostic; these tests feed it a stub
# SshAgent that captures the shell command line and queued response.

import base64
import socket

from dataclasses import dataclass, field
from typing import Any, Optional

from ccai import testbase

from ccai.sysman.linux.redhat.runners.models import SSHErrorType
from ccai.sysman.linux.redhat.runners.ssh import SSHCommandRunner, SSHRunnerConfig


@dataclass
class _FakeSshAgent:
    """
        Drop-in replacement for :class:`ccai.interop.protocols.ssh.sshagent.SshAgent`
        that records each command and returns queued results.
    """
    queued_results: list = field(default_factory=list)
    raise_on_run: Optional[Exception] = None
    calls: list = field(default_factory=list)

    def run_cmd(
        self,
        command: str,
        *,
        exp_status: Any = None,
        user: Any = None,
        pty_params: Any = None,
        aspects: Any = None,
        ssh_client: Any = None,
    ) -> tuple:
        self.calls.append({"command": command, "aspects": aspects})
        if self.raise_on_run is not None:
            raise self.raise_on_run
        if len(self.queued_results) > 0:
            next_result = self.queued_results.pop(0)
            return next_result
        return (0, "", "")


def _make_runner(agent: _FakeSshAgent) -> SSHCommandRunner:
    """
        Build an :class:`SSHCommandRunner` that uses ``agent`` as its transport.
    """
    config = SSHRunnerConfig(agent=agent)
    runner = SSHCommandRunner(config)
    return runner


def test_dry_run_skips_agent_invocation():
    """
        dry_run must produce a synthetic result without touching the agent.
    """
    agent = _FakeSshAgent()
    runner = SSHCommandRunner(SSHRunnerConfig(agent=agent, dry_run=True))
    result = runner.run(["ls"])
    testbase.assert_true(result.dry_run)
    testbase.assert_equal(result.exit_code, 0)
    testbase.assert_equal(result.transport, "ssh")
    testbase.assert_equal(agent.calls, [])
    return


def test_run_dispatches_shell_command_to_agent():
    """
        run() must build a single shell command line and pass it to run_cmd.
    """
    agent = _FakeSshAgent(queued_results=[(0, "hello\n", "")])
    runner = _make_runner(agent)
    result = runner.run(["echo", "hello"])
    testbase.assert_equal(result.exit_code, 0)
    testbase.assert_equal(result.stdout, "hello\n")
    testbase.assert_equal(result.transport, "ssh")
    testbase.assert_false(result.timed_out)
    testbase.assert_equal(len(agent.calls), 1)
    testbase.assert_true("echo hello" in agent.calls[0]["command"])
    return


def test_elevation_prepends_sudo_in_command_line():
    """
        elevated=True must prepend ``sudo -n`` to the quoted argv.
    """
    agent = _FakeSshAgent(queued_results=[(0, "", "")])
    runner = _make_runner(agent)
    runner.run(["ls"], elevated=True)
    testbase.assert_true("/usr/bin/sudo -n ls" in agent.calls[-1]["command"])
    return


def test_cwd_and_env_are_chained_with_ampersand():
    """
        cwd and env must produce ``export`` and ``cd`` clauses chained with &&.
    """
    agent = _FakeSshAgent(queued_results=[(0, "", "")])
    runner = _make_runner(agent)
    runner.run(["ls"], cwd="/opt/app", env={"FOO": "bar baz"})
    sent = agent.calls[-1]["command"]
    testbase.assert_true("export FOO='bar baz'" in sent)
    testbase.assert_true("cd /opt/app" in sent)
    testbase.assert_true(" && " in sent)
    return


def test_stdin_is_piped_via_base64_decode():
    """
        Supplying stdin must wrap the command in a base64 pipe so the payload
        reaches the remote command's stdin verbatim.
    """
    agent = _FakeSshAgent(queued_results=[(0, "", "")])
    runner = _make_runner(agent)
    payload = "line1\nline2\n"
    runner.run(["tee", "/etc/app.conf"], stdin=payload)
    sent = agent.calls[-1]["command"]
    testbase.assert_true("base64 -d" in sent)
    testbase.assert_true("tee /etc/app.conf" in sent)
    expected_encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    testbase.assert_true(expected_encoded in sent)
    return


def test_metrics_accumulate_across_runs():
    """
        Successive runs accrue success and failure counts separately.
    """
    agent = _FakeSshAgent(queued_results=[
        (0, "", ""),
        (1, "", "fail"),
    ])
    runner = _make_runner(agent)
    runner.run(["true"])
    runner.run(["false"])
    metrics = runner.get_metrics()
    testbase.assert_equal(metrics.success_count, 1)
    testbase.assert_equal(metrics.failure_count, 1)
    testbase.assert_true(metrics.avg_duration_ms is not None)
    return


def test_reset_metrics_clears_counters():
    """
        reset_metrics() returns all counters to zero.
    """
    agent = _FakeSshAgent(queued_results=[(0, "", "")])
    runner = _make_runner(agent)
    runner.run(["true"])
    runner.reset_metrics()
    metrics = runner.get_metrics()
    testbase.assert_equal(metrics.success_count, 0)
    testbase.assert_equal(metrics.failure_count, 0)
    return


def test_socket_timeout_is_surfaced_as_timed_out_result():
    """
        A socket.timeout from run_cmd must yield a RunResult with timed_out=True.
    """
    agent = _FakeSshAgent(raise_on_run=socket.timeout("read timed out"))
    runner = _make_runner(agent)
    result = runner.run(["sleep", "100"], timeout=1.0)
    testbase.assert_true(result.timed_out)
    testbase.assert_equal(result.exit_code, -1)
    metrics = runner.get_metrics()
    testbase.assert_equal(metrics.timeout_count, 1)
    testbase.assert_equal(metrics.last_error_type, SSHErrorType.TIMEOUT_ERROR)
    return


def test_generic_exception_is_recorded_as_failure():
    """
        A non-timeout exception is surfaced as a failed RunResult and recorded
        in the reliability metrics.
    """
    agent = _FakeSshAgent(raise_on_run=RuntimeError("boom"))
    runner = _make_runner(agent)
    result = runner.run(["true"])
    testbase.assert_false(result.success)
    testbase.assert_equal(result.exit_code, -1)
    metrics = runner.get_metrics()
    testbase.assert_equal(metrics.failure_count, 1)
    testbase.assert_equal(metrics.connection_error_count, 1)
    testbase.assert_true(metrics.last_error is not None)
    return


def test_agent_is_lazy_built_when_not_supplied():
    """
        Omitting ``agent`` but supplying ``host``/``username`` should build an SshAgent.
    """
    from unittest import mock
    with mock.patch(
        "ccai.sysman.linux.redhat.runners.ssh._import_ssh_deps"
    ) as import_mock:
        captured = {}

        def _fake_agent_factory(*, host, primary_credential, port):
            captured["host"] = host
            captured["port"] = port
            captured["credential"] = primary_credential
            return _FakeSshAgent()

        class _FakeCredential:
            def __init__(self, *, identifier, categories, username,
                         password=None, keyfile=None, allow_agent=False):
                self.identifier = identifier
                self.categories = categories
                self.username = username
                self.password = password
                self.keyfile = keyfile
                self.allow_agent = allow_agent

        class _AspectsCmd:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        import_mock.return_value = (
            _fake_agent_factory, _AspectsCmd, _FakeCredential, None,
        )

        SSHCommandRunner(SSHRunnerConfig(
            host="host.example.com", username="root", port=2222,
        ))
    testbase.assert_equal(captured["host"], "host.example.com")
    testbase.assert_equal(captured["port"], 2222)
    testbase.assert_equal(captured["credential"].username, "root")
    return


def test_aspect_invocation_order_matches_local_runner():
    """
        before/after aspects run in registration and reverse-registration order.
    """
    from ccai.sysman.linux.redhat.runners.models import ExecutionContext, RunResult

    @dataclass
    class _RecordingAspect:
        name: str
        events: list
        enabled: bool = True

        def before_execute(self, context: ExecutionContext) -> None:
            self.events.append(("before", self.name))
            return

        def after_execute(
            self, context: ExecutionContext, result: RunResult,
        ) -> RunResult:
            self.events.append(("after", self.name))
            return result

    events: list = []
    agent = _FakeSshAgent(queued_results=[(0, "", "")])
    runner = _make_runner(agent)
    runner.add_aspect(_RecordingAspect(name="A", events=events))
    runner.add_aspect(_RecordingAspect(name="B", events=events))
    runner.run(["echo"])
    testbase.assert_equal(
        events,
        [("before", "A"), ("before", "B"), ("after", "B"), ("after", "A")],
    )
    return


def test_classify_error_maps_socket_timeout():
    """
        _classify_error() maps socket.timeout to SSHErrorType.TIMEOUT_ERROR.
    """
    agent = _FakeSshAgent()
    runner = _make_runner(agent)
    err_type = runner._classify_error(socket.timeout())
    testbase.assert_equal(err_type, SSHErrorType.TIMEOUT_ERROR)
    return
