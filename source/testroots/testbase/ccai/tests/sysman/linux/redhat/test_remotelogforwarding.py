# Tests for the RemoteLogForwardingManager facade.

from dataclasses import dataclass, field
from typing import Callable, Optional

from ccai import testbase

from ccai.sysman.linux.redhat.featurebase import (
    ActionStatus,
    FeatureActionResult,
)
from ccai.sysman.linux.redhat.remotelogforwarding import (
    ForwarderQueryError,
    ForwardingTarget,
    LogForwardingActionStatus,
    LogForwardingHealth,
    RemoteLogForwardingConfig,
    RemoteLogForwardingManager,
    RsyslogConfigState,
    RsyslogForwardRule,
    _parse_action_kv,
    _parse_forward_body,
    _parse_section,
    _render_forward_rule,
    _upsert_section,
)
from ccai.sysman.linux.redhat.runners.models import RunResult


# ----------------------------------------------------------------------
# Mock runner and service manager
# ----------------------------------------------------------------------


@dataclass
class _MockRunner:

    results: dict = field(default_factory=dict)
    default_result: Optional[RunResult] = None
    calls: list = field(default_factory=list)

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
        self.calls.append({
            "args": list(args),
            "elevated": elevated,
            "stdin": stdin,
        })
        key = tuple(args)
        if key in self.results:
            return self.results[key]
        if self.default_result is not None:
            return self.default_result
        rtnval = RunResult(
            exit_code=0, stdout="", stderr="", command=list(args),
        )
        return rtnval

    def add_aspect(self, aspect) -> None:
        return

    def remove_aspect(self, aspect) -> None:
        return

    def get_metrics(self):
        return None


@dataclass
class _MockServiceManager:
    """
        Minimal stand-in for :class:`ServiceManager`.

        Each method records its call and returns a configurable
        :class:`FeatureActionResult`.  The default outcome is
        :class:`ActionStatus.SUCCESS` so tests that do not care
        about service-lifecycle plumbing can ignore it.
    """

    enable_result: FeatureActionResult = field(
        default_factory=lambda: FeatureActionResult(
            status=ActionStatus.SUCCESS,
            command=["systemctl", "enable", ""],
        ),
    )
    disable_result: FeatureActionResult = field(
        default_factory=lambda: FeatureActionResult(
            status=ActionStatus.SUCCESS,
            command=["systemctl", "disable", ""],
        ),
    )
    start_result: FeatureActionResult = field(
        default_factory=lambda: FeatureActionResult(
            status=ActionStatus.SUCCESS,
            command=["systemctl", "start", ""],
        ),
    )
    stop_result: FeatureActionResult = field(
        default_factory=lambda: FeatureActionResult(
            status=ActionStatus.SUCCESS,
            command=["systemctl", "stop", ""],
        ),
    )
    reload_result: FeatureActionResult = field(
        default_factory=lambda: FeatureActionResult(
            status=ActionStatus.SUCCESS,
            command=["systemctl", "reload", ""],
        ),
    )
    restart_result: FeatureActionResult = field(
        default_factory=lambda: FeatureActionResult(
            status=ActionStatus.SUCCESS,
            command=["systemctl", "restart", ""],
        ),
    )

    calls: list = field(default_factory=list)

    def enable(self, name: str) -> FeatureActionResult:
        self.calls.append({"op": "enable", "name": name})
        return self.enable_result

    def disable(self, name: str) -> FeatureActionResult:
        self.calls.append({"op": "disable", "name": name})
        return self.disable_result

    def start(self, name: str) -> FeatureActionResult:
        self.calls.append({"op": "start", "name": name})
        return self.start_result

    def stop(self, name: str) -> FeatureActionResult:
        self.calls.append({"op": "stop", "name": name})
        return self.stop_result

    def reload(self, name: str) -> FeatureActionResult:
        self.calls.append({"op": "reload", "name": name})
        return self.reload_result

    def restart(self, name: str) -> FeatureActionResult:
        self.calls.append({"op": "restart", "name": name})
        return self.restart_result


# ----------------------------------------------------------------------
# Seed helpers
# ----------------------------------------------------------------------


def _seed_test_dash_e(
    runner: _MockRunner,
    *,
    path: str,
    exists: bool,
) -> None:
    """
        Seed ``test -e <path>`` to return present or absent.
    """
    if exists:
        result = RunResult(exit_code=0, stdout="", stderr="", command=[])
    else:
        result = RunResult(exit_code=1, stdout="", stderr="", command=[])
    runner.results[("test", "-e", path)] = result
    return


def _seed_cat(
    runner: _MockRunner,
    *,
    path: str,
    body: Optional[str],
) -> None:
    """
        Seed ``cat <path>`` to return ``body`` on success or a
        non-zero exit when ``body`` is ``None``.
    """
    if body is None:
        result = RunResult(
            exit_code=1, stdout="",
            stderr="No such file", command=[],
        )
    else:
        result = RunResult(
            exit_code=0, stdout=body, stderr="", command=[],
        )
    runner.results[("cat", path)] = result
    return


def _seed_validate_ok(runner: _MockRunner) -> None:
    """
        Seed ``rsyslogd -N 1`` to return exit code zero.
    """
    runner.results[("rsyslogd", "-N", "1")] = RunResult(
        exit_code=0, stdout="config OK", stderr="", command=[],
    )
    return


def _build_manager(
    *,
    runner: _MockRunner,
    config: Optional[RemoteLogForwardingConfig] = None,
    service_manager: Optional[_MockServiceManager] = None,
) -> RemoteLogForwardingManager:
    """
        Construct a :class:`RemoteLogForwardingManager` with
        sensible defaults for the tests.
    """
    if config is None:
        config = RemoteLogForwardingConfig()
    if service_manager is None:
        service_manager = _MockServiceManager()
    rtnval = RemoteLogForwardingManager(
        runner=runner,
        config=config,
        service_manager=service_manager,
    )
    return rtnval


# ----------------------------------------------------------------------
# Renderer tests
# ----------------------------------------------------------------------


def test_render_plain_tcp_rule_emits_omfwd_action():
    """
        A plain TCP rule renders the canonical
        ``action(type="omfwd" target="..." port="..."
        protocol="tcp")`` line.
    """
    rule = RsyslogForwardRule(
        name="collector1",
        target_host="loghost.example.com",
        target_port=514,
        protocol="tcp",
        selector="*.*",
    )
    body = _render_forward_rule(rule=rule)
    testbase.assert_true('type="omfwd"' in body)
    testbase.assert_true('target="loghost.example.com"' in body)
    testbase.assert_true('port="514"' in body)
    testbase.assert_true('protocol="tcp"' in body)
    testbase.assert_true("*.* action(" in body)
    return


def test_render_tls_rule_emits_gtls_block():
    """
        A TLS rule renders the gtls StreamDriver block plus the
        global CA-file directive.
    """
    rule = RsyslogForwardRule(
        name="tls1",
        target_host="secure.example.com",
        target_port=6514,
        protocol="tcp",
        selector="*.*",
        tls=True,
        tls_ca_file="/etc/pki/tls/certs/ca.pem",
        tls_cert_file="/etc/pki/tls/certs/client.pem",
        tls_key_file="/etc/pki/tls/private/client.key",
        tls_auth_mode="x509/name",
        tls_permitted_peer="loghost.example.com",
    )
    body = _render_forward_rule(rule=rule)
    testbase.assert_true('StreamDriver="gtls"' in body)
    testbase.assert_true('StreamDriverMode="1"' in body)
    testbase.assert_true('StreamDriverAuthMode="x509/name"' in body)
    testbase.assert_true('permittedPeer="loghost.example.com"' in body)
    testbase.assert_true(
        'DefaultNetstreamDriverCAFile="/etc/pki/tls/certs/ca.pem"' in body,
    )
    return


def test_render_queue_rule_emits_queue_block():
    """
        A queue-enabled rule renders the queue.* directives.
    """
    rule = RsyslogForwardRule(
        name="queued1",
        target_host="logs.example.com",
        target_port=514,
        protocol="tcp",
        queue_filename="fwd-queued1",
        queue_spool_dir="/var/spool/rsyslog",
        queue_max_disk_bytes=104857600,
        queue_save_on_shutdown=True,
    )
    body = _render_forward_rule(rule=rule)
    testbase.assert_true('queue.type="LinkedList"' in body)
    testbase.assert_true('queue.filename="fwd-queued1"' in body)
    testbase.assert_true('queue.spoolDirectory="/var/spool/rsyslog"' in body)
    testbase.assert_true('queue.maxDiskSpace="104857600"' in body)
    testbase.assert_true('queue.saveOnShutdown="on"' in body)
    return


# ----------------------------------------------------------------------
# add_forward_rule
# ----------------------------------------------------------------------


def test_add_forward_rule_writes_validates_and_reloads():
    """
        add_forward_rule writes the drop-in via tee, validates
        with ``rsyslogd -N 1``, and delegates the reload to the
        injected service manager.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    ))
    _seed_validate_ok(runner)
    service_manager = _MockServiceManager()

    config = RemoteLogForwardingConfig(elevated=True)
    manager = _build_manager(
        runner=runner, config=config,
        service_manager=service_manager,
    )
    rule = RsyslogForwardRule(
        name="collector1",
        target_host="loghost.example.com",
        target_port=514,
        protocol="tcp",
        selector="*.*",
    )
    result = manager.add_forward_rule(rule=rule)

    testbase.assert_equal(result.status, LogForwardingActionStatus.SUCCESS)

    tee_calls = [
        c for c in runner.calls
        if len(c["args"]) >= 1 and c["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_equal(
        tee_calls[0]["args"],
        ["tee", "/etc/rsyslog.d/collector1.conf"],
    )
    testbase.assert_true(
        'target="loghost.example.com"' in tee_calls[0]["stdin"],
    )
    testbase.assert_equal(tee_calls[0]["elevated"], True)

    validate_calls = [
        c for c in runner.calls
        if c["args"] == ["rsyslogd", "-N", "1"]
    ]
    testbase.assert_equal(len(validate_calls), 1)

    reload_calls = [
        c for c in service_manager.calls
        if c["op"] == "reload" and c["name"] == "rsyslog"
    ]
    testbase.assert_equal(len(reload_calls), 1)
    return


def test_add_forward_rule_skipped_when_content_matches():
    """
        add_forward_rule returns SKIPPED when the drop-in body
        already matches the rendered desired body byte-for-byte.
    """
    runner = _MockRunner()
    rule = RsyslogForwardRule(
        name="collector1",
        target_host="loghost.example.com",
        target_port=514,
        protocol="tcp",
        selector="*.*",
    )
    desired = _render_forward_rule(rule=rule)
    _seed_cat(
        runner, path="/etc/rsyslog.d/collector1.conf", body=desired,
    )

    manager = _build_manager(runner=runner)
    result = manager.add_forward_rule(rule=rule)
    testbase.assert_equal(result.status, LogForwardingActionStatus.SKIPPED)

    tee_calls = [
        c for c in runner.calls
        if len(c["args"]) >= 1 and c["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 0)
    return


def test_add_forward_rule_dry_run_short_circuits():
    """
        With ``dry_run=True``, add_forward_rule returns DRY_RUN
        and does not invoke ``tee``, ``rsyslogd``, or the
        service manager.
    """
    runner = _MockRunner()
    service_manager = _MockServiceManager()
    config = RemoteLogForwardingConfig(dry_run=True)
    manager = _build_manager(
        runner=runner, config=config, service_manager=service_manager,
    )
    rule = RsyslogForwardRule(
        name="collector1",
        target_host="loghost.example.com",
        target_port=514,
    )
    result = manager.add_forward_rule(rule=rule)
    testbase.assert_equal(result.status, LogForwardingActionStatus.DRY_RUN)

    tee_calls = [
        c for c in runner.calls
        if len(c["args"]) >= 1 and c["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 0)

    validate_calls = [
        c for c in runner.calls
        if c["args"] == ["rsyslogd", "-N", "1"]
    ]
    testbase.assert_equal(len(validate_calls), 0)

    reload_calls = [
        c for c in service_manager.calls if c["op"] == "reload"
    ]
    testbase.assert_equal(len(reload_calls), 0)
    return


def test_add_forward_rule_validate_failure_aborts_reload():
    """
        When ``rsyslogd -N 1`` returns non-zero the verb returns
        FAILED and does NOT delegate the reload.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    ))
    runner.results[("rsyslogd", "-N", "1")] = RunResult(
        exit_code=1, stdout="", stderr="config error", command=[],
    )
    service_manager = _MockServiceManager()

    manager = _build_manager(
        runner=runner,
        config=RemoteLogForwardingConfig(elevated=True),
        service_manager=service_manager,
    )
    rule = RsyslogForwardRule(
        name="collector1",
        target_host="loghost.example.com",
        target_port=514,
    )
    result = manager.add_forward_rule(rule=rule)
    testbase.assert_equal(result.status, LogForwardingActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 1)
    testbase.assert_equal(result.stderr, "config error")

    reload_calls = [
        c for c in service_manager.calls if c["op"] == "reload"
    ]
    testbase.assert_equal(len(reload_calls), 0)
    return


def test_add_forward_rule_installs_missing_spool_directory():
    """
        When ``queue_spool_dir`` is set and absent the manager
        runs ``install -d -o syslog -g syslog -m 0700`` before
        writing the drop-in.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    ))
    spool_dir = "/var/spool/rsyslog/queued1"
    _seed_test_dash_e(runner, path=spool_dir, exists=False)
    _seed_validate_ok(runner)

    config = RemoteLogForwardingConfig(elevated=True)
    manager = _build_manager(runner=runner, config=config)
    rule = RsyslogForwardRule(
        name="queued1",
        target_host="logs.example.com",
        target_port=514,
        protocol="tcp",
        queue_filename="fwd-queued1",
        queue_spool_dir=spool_dir,
        queue_max_disk_bytes=104857600,
    )
    result = manager.add_forward_rule(rule=rule)
    testbase.assert_equal(result.status, LogForwardingActionStatus.SUCCESS)

    install_calls = [
        c for c in runner.calls
        if len(c["args"]) >= 1 and c["args"][0] == "install"
    ]
    testbase.assert_equal(len(install_calls), 1)
    testbase.assert_equal(
        install_calls[0]["args"],
        [
            "install", "-d",
            "-o", "syslog",
            "-g", "syslog",
            "-m", "0700",
            spool_dir,
        ],
    )
    testbase.assert_equal(install_calls[0]["elevated"], True)
    return


def test_add_forward_rule_tls_missing_ca_returns_failed():
    """
        A TLS rule without ``tls_ca_file`` is rejected before the
        runner is invoked.
    """
    runner = _MockRunner()
    manager = _build_manager(runner=runner)
    rule = RsyslogForwardRule(
        name="tls1",
        target_host="secure.example.com",
        target_port=6514,
        protocol="tcp",
        tls=True,
        tls_ca_file=None,
    )
    result = manager.add_forward_rule(rule=rule)
    testbase.assert_equal(result.status, LogForwardingActionStatus.FAILED)
    testbase.assert_true("tls_ca_file" in (result.stderr or ""))

    tee_calls = [
        c for c in runner.calls
        if len(c["args"]) >= 1 and c["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 0)
    return


def test_add_forward_rule_reload_failure_propagates():
    """
        When the post-validate reload fails, the verb returns
        FAILED with the reload's exit code propagated.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    ))
    _seed_validate_ok(runner)
    service_manager = _MockServiceManager(
        reload_result=FeatureActionResult(
            status=ActionStatus.FAILED,
            exit_code=4,
            stderr="reload failed",
            command=["systemctl", "reload", "rsyslog"],
        ),
    )
    manager = _build_manager(
        runner=runner, service_manager=service_manager,
    )
    rule = RsyslogForwardRule(
        name="collector1",
        target_host="loghost.example.com",
        target_port=514,
    )
    result = manager.add_forward_rule(rule=rule)
    testbase.assert_equal(result.status, LogForwardingActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 4)
    return


# ----------------------------------------------------------------------
# remove_forward_rule
# ----------------------------------------------------------------------


def test_remove_forward_rule_invokes_rm_and_reload():
    """
        remove_forward_rule dispatches ``rm -f`` against the
        drop-in, validates, and delegates the reload.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    ))
    path = "/etc/rsyslog.d/collector1.conf"
    _seed_test_dash_e(runner, path=path, exists=True)
    _seed_validate_ok(runner)
    service_manager = _MockServiceManager()

    config = RemoteLogForwardingConfig(elevated=True)
    manager = _build_manager(
        runner=runner, config=config, service_manager=service_manager,
    )
    result = manager.remove_forward_rule(name="collector1")
    testbase.assert_equal(result.status, LogForwardingActionStatus.SUCCESS)

    rm_calls = [
        c for c in runner.calls
        if len(c["args"]) >= 1 and c["args"][0] == "rm"
    ]
    testbase.assert_equal(len(rm_calls), 1)
    testbase.assert_equal(rm_calls[0]["args"], ["rm", "-f", path])
    testbase.assert_equal(rm_calls[0]["elevated"], True)

    reload_calls = [
        c for c in service_manager.calls if c["op"] == "reload"
    ]
    testbase.assert_equal(len(reload_calls), 1)
    return


def test_remove_forward_rule_skipped_when_absent():
    """
        remove_forward_rule returns SKIPPED when the drop-in is
        already absent.
    """
    runner = _MockRunner()
    path = "/etc/rsyslog.d/ghost.conf"
    _seed_test_dash_e(runner, path=path, exists=False)
    service_manager = _MockServiceManager()

    manager = _build_manager(
        runner=runner, service_manager=service_manager,
    )
    result = manager.remove_forward_rule(name="ghost")
    testbase.assert_equal(result.status, LogForwardingActionStatus.SKIPPED)

    rm_calls = [
        c for c in runner.calls
        if len(c["args"]) >= 1 and c["args"][0] == "rm"
    ]
    testbase.assert_equal(len(rm_calls), 0)

    reload_calls = [
        c for c in service_manager.calls if c["op"] == "reload"
    ]
    testbase.assert_equal(len(reload_calls), 0)
    return


# ----------------------------------------------------------------------
# list_forward_rules
# ----------------------------------------------------------------------


def test_list_forward_rules_parses_modern_omfwd():
    """
        list_forward_rules parses a drop-in carrying the modern
        omfwd action() construct.
    """
    runner = _MockRunner()
    rsyslog_d = "/etc/rsyslog.d"
    runner.results[("ls", "-1", rsyslog_d)] = RunResult(
        exit_code=0,
        stdout=(
            "collector1.conf\n"
            "50-default.conf\n"
        ),
        stderr="", command=[],
    )

    rule = RsyslogForwardRule(
        name="collector1",
        target_host="loghost.example.com",
        target_port=514,
        protocol="tcp",
        selector="*.*",
    )
    body = _render_forward_rule(rule=rule)
    _seed_cat(runner, path="{}/collector1.conf".format(rsyslog_d), body=body)
    _seed_cat(
        runner, path="{}/50-default.conf".format(rsyslog_d),
        body="# unrelated drop-in\n",
    )

    manager = _build_manager(runner=runner)
    targets = manager.list_forward_rules()
    testbase.assert_equal(len(targets), 1)
    testbase.assert_true(isinstance(targets[0], ForwardingTarget))
    testbase.assert_equal(targets[0].name, "collector1")
    testbase.assert_equal(targets[0].host, "loghost.example.com")
    testbase.assert_equal(targets[0].port, 514)
    testbase.assert_equal(targets[0].protocol, "tcp")
    testbase.assert_equal(targets[0].tls, False)
    return


def test_list_forward_rules_parses_legacy_at_at_action():
    """
        list_forward_rules parses a legacy ``@@host:port``
        action line.
    """
    runner = _MockRunner()
    rsyslog_d = "/etc/rsyslog.d"
    runner.results[("ls", "-1", rsyslog_d)] = RunResult(
        exit_code=0,
        stdout="legacy.conf\n",
        stderr="", command=[],
    )
    legacy_body = (
        "# legacy drop-in\n"
        "*.* @@legacyhost.example.com:6514\n"
    )
    _seed_cat(
        runner, path="{}/legacy.conf".format(rsyslog_d), body=legacy_body,
    )

    manager = _build_manager(runner=runner)
    targets = manager.list_forward_rules()
    testbase.assert_equal(len(targets), 1)
    testbase.assert_equal(targets[0].name, "legacy")
    testbase.assert_equal(targets[0].host, "legacyhost.example.com")
    testbase.assert_equal(targets[0].port, 6514)
    testbase.assert_equal(targets[0].protocol, "tcp")
    testbase.assert_equal(targets[0].selector, "*.*")
    return


def test_list_forward_rules_raises_on_unexpected_failure():
    """
        list_forward_rules raises ForwarderQueryError when the
        ``ls`` runner reports a non-zero exit with a non-empty
        stderr.
    """
    runner = _MockRunner()
    rsyslog_d = "/etc/rsyslog.d"
    runner.results[("ls", "-1", rsyslog_d)] = RunResult(
        exit_code=2, stdout="", stderr="Permission denied", command=[],
    )

    manager = _build_manager(runner=runner)
    raised = False
    try:
        manager.list_forward_rules()
    except ForwarderQueryError:
        raised = True
    testbase.assert_equal(raised, True)
    return


# ----------------------------------------------------------------------
# validate_rsyslog
# ----------------------------------------------------------------------


def test_validate_rsyslog_returns_state():
    """
        validate_rsyslog dispatches ``rsyslogd -N 1`` and maps
        the runner result onto :class:`RsyslogConfigState`.
    """
    runner = _MockRunner()
    runner.results[("rsyslogd", "-N", "1")] = RunResult(
        exit_code=0, stdout="OK", stderr="", command=[],
    )
    manager = _build_manager(runner=runner)
    state = manager.validate_rsyslog()
    testbase.assert_true(isinstance(state, RsyslogConfigState))
    testbase.assert_true(state.ok)
    testbase.assert_equal(state.exit_code, 0)
    testbase.assert_equal(state.stdout, "OK")
    return


def test_validate_rsyslog_does_not_elevate():
    """
        validate_rsyslog never passes ``elevated=True`` to the
        runner.
    """
    runner = _MockRunner()
    config = RemoteLogForwardingConfig(elevated=True)
    manager = _build_manager(runner=runner, config=config)
    manager.validate_rsyslog()
    validate_calls = [
        c for c in runner.calls
        if c["args"] == ["rsyslogd", "-N", "1"]
    ]
    testbase.assert_equal(len(validate_calls), 1)
    testbase.assert_equal(validate_calls[0]["elevated"], False)
    return


# ----------------------------------------------------------------------
# systemd-journal-upload
# ----------------------------------------------------------------------


def test_set_upload_config_upserts_directives():
    """
        set_upload_config writes the merged file via tee when
        the requested directives differ from the on-disk values.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    ))
    existing = (
        "[Upload]\n"
        "URL=https://old.example.com:19532\n"
        "TrustedCertificateFile=/etc/pki/old.pem\n"
    )
    _seed_cat(
        runner,
        path="/etc/systemd/journal-upload.conf",
        body=existing,
    )

    config = RemoteLogForwardingConfig(elevated=True)
    manager = _build_manager(runner=runner, config=config)
    result = manager.set_upload_config(directives={
        "URL": "https://new.example.com:19532",
        "ServerKeyFile": "/etc/pki/new.key",
    })
    testbase.assert_equal(result.status, LogForwardingActionStatus.SUCCESS)

    tee_calls = [
        c for c in runner.calls
        if len(c["args"]) >= 1 and c["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 1)
    body = tee_calls[0]["stdin"]
    testbase.assert_true("URL=https://new.example.com:19532" in body)
    testbase.assert_true("ServerKeyFile=/etc/pki/new.key" in body)
    # Pre-existing unrelated key must be preserved.
    testbase.assert_true(
        "TrustedCertificateFile=/etc/pki/old.pem" in body,
    )
    testbase.assert_equal(tee_calls[0]["elevated"], True)
    return


def test_set_upload_config_skipped_when_unchanged():
    """
        set_upload_config returns SKIPPED when every requested
        directive already matches.
    """
    runner = _MockRunner()
    existing = (
        "[Upload]\n"
        "URL=https://collector.example.com:19532\n"
    )
    _seed_cat(
        runner,
        path="/etc/systemd/journal-upload.conf",
        body=existing,
    )
    manager = _build_manager(runner=runner)
    result = manager.set_upload_config(directives={
        "URL": "https://collector.example.com:19532",
    })
    testbase.assert_equal(result.status, LogForwardingActionStatus.SKIPPED)

    tee_calls = [
        c for c in runner.calls
        if len(c["args"]) >= 1 and c["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 0)
    return


def test_get_upload_config_parses_section():
    """
        get_upload_config returns a flat ``dict[str, str]`` of
        the ``[Upload]`` directives on disk.
    """
    runner = _MockRunner()
    body = (
        "[Upload]\n"
        "URL=https://collector.example.com:19532\n"
        "ServerKeyFile=/etc/pki/k.key\n"
        "[Other]\n"
        "Ignored=true\n"
    )
    _seed_cat(
        runner,
        path="/etc/systemd/journal-upload.conf",
        body=body,
    )
    manager = _build_manager(runner=runner)
    directives = manager.get_upload_config()
    testbase.assert_equal(
        directives.get("URL"),
        "https://collector.example.com:19532",
    )
    testbase.assert_equal(
        directives.get("ServerKeyFile"), "/etc/pki/k.key",
    )
    testbase.assert_true("Ignored" not in directives)
    return


def test_enable_upload_delegates_to_service_manager():
    """
        enable_upload delegates to
        ``service_manager.enable("systemd-journal-upload")`` and
        starts the service when ``start=True``.
    """
    runner = _MockRunner()
    service_manager = _MockServiceManager()
    manager = _build_manager(
        runner=runner, service_manager=service_manager,
    )
    result = manager.enable_upload(start=True)
    testbase.assert_equal(result.status, LogForwardingActionStatus.SUCCESS)

    enable_calls = [
        c for c in service_manager.calls if c["op"] == "enable"
    ]
    testbase.assert_equal(len(enable_calls), 1)
    testbase.assert_equal(enable_calls[0]["name"], "systemd-journal-upload")

    start_calls = [
        c for c in service_manager.calls if c["op"] == "start"
    ]
    testbase.assert_equal(len(start_calls), 1)
    return


def test_disable_upload_stops_then_disables():
    """
        disable_upload stops the service first and then
        delegates the disable.
    """
    runner = _MockRunner()
    service_manager = _MockServiceManager()
    manager = _build_manager(
        runner=runner, service_manager=service_manager,
    )
    result = manager.disable_upload()
    testbase.assert_equal(result.status, LogForwardingActionStatus.SUCCESS)

    ops = [c["op"] for c in service_manager.calls]
    testbase.assert_equal(ops, ["stop", "disable"])
    return


# ----------------------------------------------------------------------
# journald.conf ForwardTo* directives
# ----------------------------------------------------------------------


def test_set_journald_directive_emits_restart_hint():
    """
        set_journald_directive writes the merged journald.conf
        and populates ``result.detail`` with the journald-restart
        hint.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    ))
    body = (
        "[Journal]\n"
        "ForwardToSyslog=no\n"
    )
    _seed_cat(runner, path="/etc/systemd/journald.conf", body=body)

    manager = _build_manager(runner=runner)
    result = manager.set_journald_directive(
        key="ForwardToSyslog", value="yes",
    )
    testbase.assert_equal(result.status, LogForwardingActionStatus.SUCCESS)
    testbase.assert_true(
        "systemd-journald.service restart required" in (result.detail or ""),
    )

    tee_calls = [
        c for c in runner.calls
        if len(c["args"]) >= 1 and c["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true(
        "ForwardToSyslog=yes" in tee_calls[0]["stdin"],
    )
    return


def test_set_journald_directive_skipped_when_unchanged():
    """
        set_journald_directive returns SKIPPED when the
        directive already matches.
    """
    runner = _MockRunner()
    body = (
        "[Journal]\n"
        "ForwardToSyslog=yes\n"
    )
    _seed_cat(runner, path="/etc/systemd/journald.conf", body=body)

    manager = _build_manager(runner=runner)
    result = manager.set_journald_directive(
        key="ForwardToSyslog", value="yes",
    )
    testbase.assert_equal(result.status, LogForwardingActionStatus.SKIPPED)
    testbase.assert_true(
        "systemd-journald.service restart required" in (result.detail or ""),
    )
    return


def test_get_journald_directives_merges_dropins():
    """
        get_journald_directives merges the main journald.conf
        with drop-ins under
        ``/etc/systemd/journald.conf.d/`` in lexical order.
    """
    runner = _MockRunner()
    main_body = (
        "[Journal]\n"
        "ForwardToSyslog=no\n"
        "ForwardToWall=yes\n"
    )
    _seed_cat(runner, path="/etc/systemd/journald.conf", body=main_body)
    runner.results[("ls", "-1", "/etc/systemd/journald.conf.d")] = RunResult(
        exit_code=0,
        stdout="00-base.conf\n10-override.conf\n",
        stderr="", command=[],
    )
    _seed_cat(
        runner,
        path="/etc/systemd/journald.conf.d/00-base.conf",
        body="[Journal]\nForwardToConsole=no\n",
    )
    _seed_cat(
        runner,
        path="/etc/systemd/journald.conf.d/10-override.conf",
        body="[Journal]\nForwardToSyslog=yes\n",
    )

    manager = _build_manager(runner=runner)
    merged = manager.get_journald_directives()
    testbase.assert_equal(merged.get("ForwardToSyslog"), "yes")
    testbase.assert_equal(merged.get("ForwardToWall"), "yes")
    testbase.assert_equal(merged.get("ForwardToConsole"), "no")
    return


# ----------------------------------------------------------------------
# probe_collector
# ----------------------------------------------------------------------


def test_probe_collector_plain_reachable():
    """
        Plain TCP probe with exit code 0 surfaces as
        ``reachable=True`` and ``tls_verified=None``.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    )
    manager = _build_manager(runner=runner)
    health = manager.probe_collector(
        host="loghost.example.com", port=514,
    )
    testbase.assert_true(isinstance(health, LogForwardingHealth))
    testbase.assert_true(health.reachable)
    testbase.assert_equal(health.tls_verified, None)

    probe_calls = [
        c for c in runner.calls
        if len(c["args"]) >= 1 and c["args"][0] == "timeout"
    ]
    testbase.assert_equal(len(probe_calls), 1)
    testbase.assert_equal(probe_calls[0]["elevated"], False)
    return


def test_probe_collector_plain_unreachable_does_not_raise():
    """
        Plain TCP probe with a timeout-style exit (124) surfaces
        as ``reachable=False`` and does not raise.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=124, stdout="", stderr="timeout", command=[],
    )
    manager = _build_manager(runner=runner)
    health = manager.probe_collector(
        host="unreachable.example.com", port=514,
    )
    testbase.assert_equal(health.reachable, False)
    testbase.assert_equal(health.tls_verified, None)
    return


def test_probe_collector_tls_verified():
    """
        TLS probe with ``Verification: OK`` in the captured
        output surfaces as ``tls_verified=True``.
    """
    runner = _MockRunner()
    runner.results[(
        "timeout", "5",
        "openssl", "s_client",
        "-connect", "secure.example.com:6514",
        "-verify", "1", "-brief",
    )] = RunResult(
        exit_code=0,
        stdout="CONNECTED\nVerification: OK\n",
        stderr="", command=[],
    )
    manager = _build_manager(runner=runner)
    health = manager.probe_collector(
        host="secure.example.com", port=6514, tls=True,
    )
    testbase.assert_true(health.reachable)
    testbase.assert_equal(health.tls_verified, True)
    return


def test_probe_collector_tls_unverified():
    """
        TLS probe whose output does not contain
        ``Verification: OK`` surfaces as ``tls_verified=False``.
    """
    runner = _MockRunner()
    runner.results[(
        "timeout", "5",
        "openssl", "s_client",
        "-connect", "secure.example.com:6514",
        "-verify", "1", "-brief",
    )] = RunResult(
        exit_code=1,
        stdout="", stderr="verify error: self signed certificate",
        command=[],
    )
    manager = _build_manager(runner=runner)
    health = manager.probe_collector(
        host="secure.example.com", port=6514, tls=True,
    )
    testbase.assert_equal(health.reachable, False)
    testbase.assert_equal(health.tls_verified, False)
    return


# ----------------------------------------------------------------------
# Pure-helper round-trip tests
# ----------------------------------------------------------------------


def test_parse_action_kv_round_trip():
    """
        ``_parse_action_kv`` parses the body of an
        ``action(...)`` construct.
    """
    text = 'type="omfwd" target="h.example.com" port="514" protocol="tcp"'
    parsed = _parse_action_kv(text=text)
    testbase.assert_equal(parsed["type"], "omfwd")
    testbase.assert_equal(parsed["target"], "h.example.com")
    testbase.assert_equal(parsed["port"], "514")
    testbase.assert_equal(parsed["protocol"], "tcp")
    return


def test_parse_forward_body_round_trip_modern_and_tls():
    """
        ``_parse_forward_body`` recovers the host, port,
        protocol, tls flag, and selector from a rendered drop-in
        for both plain TCP and TLS rules.
    """
    plain_rule = RsyslogForwardRule(
        name="p1",
        target_host="h.example.com",
        target_port=514,
        protocol="tcp",
        selector="*.*",
    )
    plain_body = _render_forward_rule(rule=plain_rule)
    parsed_plain = _parse_forward_body(body=plain_body)
    testbase.assert_true(parsed_plain is not None)
    host_p, port_p, protocol_p, tls_p, selector_p = parsed_plain
    testbase.assert_equal(host_p, "h.example.com")
    testbase.assert_equal(port_p, 514)
    testbase.assert_equal(protocol_p, "tcp")
    testbase.assert_equal(tls_p, False)
    testbase.assert_equal(selector_p, "*.*")

    tls_rule = RsyslogForwardRule(
        name="t1",
        target_host="secure.example.com",
        target_port=6514,
        protocol="tcp",
        selector="*.*",
        tls=True,
        tls_ca_file="/etc/pki/tls/certs/ca.pem",
    )
    tls_body = _render_forward_rule(rule=tls_rule)
    parsed_tls = _parse_forward_body(body=tls_body)
    testbase.assert_true(parsed_tls is not None)
    host_t, port_t, protocol_t, tls_t, selector_t = parsed_tls
    testbase.assert_equal(host_t, "secure.example.com")
    testbase.assert_equal(port_t, 6514)
    testbase.assert_equal(tls_t, True)
    return


def test_upsert_section_preserves_unrelated_sections():
    """
        ``_upsert_section`` rewrites only the target section.
    """
    body = (
        "[Other]\n"
        "Keep=this\n"
        "[Upload]\n"
        "URL=old\n"
        "TrustedCertificateFile=/etc/pki/old.pem\n"
    )
    merged = _upsert_section(
        body=body, section="Upload",
        directives={"URL": "new"},
    )
    testbase.assert_true("[Other]" in merged)
    testbase.assert_true("Keep=this" in merged)
    testbase.assert_true("URL=new" in merged)
    testbase.assert_true(
        "TrustedCertificateFile=/etc/pki/old.pem" in merged,
    )
    testbase.assert_true("URL=old" not in merged)
    return


def test_parse_section_handles_comments_and_other_sections():
    """
        ``_parse_section`` ignores comments and entries outside
        the target section.
    """
    body = (
        "# header comment\n"
        "[Upload]\n"
        "; semicolon comment\n"
        "URL=https://x.example.com\n"
        "[Journal]\n"
        "ForwardToSyslog=yes\n"
    )
    parsed = _parse_section(body=body, section="Upload")
    testbase.assert_equal(parsed.get("URL"), "https://x.example.com")
    testbase.assert_true("ForwardToSyslog" not in parsed)
    return


# ----------------------------------------------------------------------
# Elevation, error propagation
# ----------------------------------------------------------------------


def test_reads_never_elevate_even_with_elevated_config():
    """
        Reads (cat, ls, test, rsyslogd -N 1) never elevate even
        when ``config.elevated=True``.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    )
    config = RemoteLogForwardingConfig(elevated=True)
    manager = _build_manager(runner=runner, config=config)
    manager.list_forward_rules()
    manager.validate_rsyslog()
    manager.get_upload_config()
    manager.probe_collector(host="x.example.com", port=514)

    read_argv0 = ("ls", "cat", "test", "rsyslogd", "openssl", "timeout")
    for call in runner.calls:
        argv0 = call["args"][0]
        if argv0 in read_argv0:
            testbase.assert_equal(call["elevated"], False)
    return


def test_failed_tee_surfaces_exit_code_and_stderr():
    """
        When ``tee`` returns non-zero on add_forward_rule, the
        manager surfaces the runner's exit code and stderr
        without raising and without delegating the reload.
    """
    runner = _MockRunner()
    path = "/etc/rsyslog.d/collector1.conf"
    runner.results[("tee", path)] = RunResult(
        exit_code=2, stdout="", stderr="permission denied", command=[],
    )
    service_manager = _MockServiceManager()
    manager = _build_manager(
        runner=runner, service_manager=service_manager,
    )
    rule = RsyslogForwardRule(
        name="collector1",
        target_host="loghost.example.com",
        target_port=514,
    )
    result = manager.add_forward_rule(rule=rule)
    testbase.assert_equal(result.status, LogForwardingActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 2)
    testbase.assert_equal(result.stderr, "permission denied")

    reload_calls = [
        c for c in service_manager.calls if c["op"] == "reload"
    ]
    testbase.assert_equal(len(reload_calls), 0)
    return
