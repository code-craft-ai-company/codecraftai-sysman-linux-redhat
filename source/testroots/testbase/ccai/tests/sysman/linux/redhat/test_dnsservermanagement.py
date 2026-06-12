# Tests for the DnsServerManager facade.
#
# All tests use a ``_MockRunner`` that records every ``run`` call
# and returns pre-seeded :class:`RunResult` values keyed by argv
# tuple.  No live BIND / Unbound / dnsmasq tooling is invoked.

from dataclasses import dataclass, field
from typing import Callable, Optional

from ccai import testbase

from ccai.errors.exceptions import SemanticError

from ccai.sysman.linux.redhat.dnsservermanagement import (
    DnsActionStatus,
    DnsBackendMismatchError,
    DnsBackendNotAvailableError,
    DnsServerBackend,
    DnsServerManager,
    DnsServerManagerConfig,
    DnsServerStatus,
    DnsValidateResult,
    ZoneEntry,
    _parse_bind_zone_block,
    _parse_unbound_blocks,
    _parse_version_from_rndc_status,
    _render_bind_zone_block,
    _render_unbound_block,
    _strip_dnsmasq_lines,
    _upsert_dnsmasq_lines,
    _upsert_unbound_block_text,
)
from ccai.sysman.linux.redhat.runners.models import RunResult


@dataclass
class _MockRunner:
    """
        Minimal ``ICommandRunner`` stand-in that records every
        ``run`` call and returns pre-seeded :class:`RunResult`
        values keyed by argv tuple.
    """

    results: dict = field(default_factory=dict)
    default_result: Optional[RunResult] = None
    calls: list = field(default_factory=list)

    def run(
        self,
        args: list,
        *,
        elevated: bool = False,
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        timeout: Optional[float] = None,
        on_output: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        capture_output: bool = True,
        stdin: Optional[str] = None,
    ) -> RunResult:
        call_record = {
            "args": list(args),
            "elevated": elevated,
            "stdin": stdin,
        }
        self.calls.append(call_record)

        key = tuple(args)
        if key in self.results:
            rtnval = self.results[key]
            return rtnval

        if self.default_result is not None:
            rtnval = self.default_result
            return rtnval

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
class _MockServiceManagerResult:
    """
        Mirrors the shape of a :class:`ServiceActionResult`-like
        object.  Used by :class:`_MockServiceManager`.
    """
    status: DnsActionStatus = DnsActionStatus.SUCCESS
    exit_code: Optional[int] = 0
    stdout: Optional[str] = ""
    stderr: Optional[str] = ""
    command: Optional[list] = None


@dataclass
class _MockServiceManager:
    """
        Records ``restart`` and ``is_active`` calls and returns
        pre-seeded answers.
    """

    restart_calls: list = field(default_factory=list)
    is_active_calls: list = field(default_factory=list)
    is_active_value: bool = True
    restart_status: DnsActionStatus = DnsActionStatus.SUCCESS

    def restart(self, name: str) -> _MockServiceManagerResult:
        self.restart_calls.append(name)
        rtnval = _MockServiceManagerResult(
            status=self.restart_status,
            exit_code=0,
            stdout="",
            stderr="",
            command=["systemctl", "restart", name],
        )
        return rtnval

    def is_active(self, name: str) -> bool:
        self.is_active_calls.append(name)
        rtnval = self.is_active_value
        return rtnval


# ----------------------------------------------------------------------
# Seeders
# ----------------------------------------------------------------------


def _seed_command_v(
    runner: _MockRunner,
    *,
    binary: str,
    found: bool,
) -> None:
    """
        Seed the runner so that ``command -v <binary>`` reports
        presence according to ``found``.
    """
    if found:
        result = RunResult(
            exit_code=0,
            stdout="/usr/sbin/{}\n".format(binary),
            stderr="",
            command=["command", "-v", binary],
        )
    else:
        result = RunResult(
            exit_code=1,
            stdout="",
            stderr="",
            command=["command", "-v", binary],
        )
    runner.results[("command", "-v", binary)] = result
    return


def _seed_test_dash_e(
    runner: _MockRunner,
    *,
    path: str,
    exists: bool,
) -> None:
    """
        Seed ``test -e <path>`` results.
    """
    if exists:
        result = RunResult(
            exit_code=0, stdout="", stderr="", command=["test", "-e", path],
        )
    else:
        result = RunResult(
            exit_code=1, stdout="", stderr="", command=["test", "-e", path],
        )
    runner.results[("test", "-e", path)] = result
    return


def _seed_cat(
    runner: _MockRunner,
    *,
    path: str,
    body: Optional[str],
) -> None:
    """
        Seed ``cat <path>`` results.  ``body=None`` means the
        file is missing.
    """
    if body is None:
        result = RunResult(
            exit_code=1, stdout="", stderr="No such file",
            command=["cat", path],
        )
    else:
        result = RunResult(
            exit_code=0, stdout=body, stderr="",
            command=["cat", path],
        )
    runner.results[("cat", path)] = result
    return


# ----------------------------------------------------------------------
# Backend detection
# ----------------------------------------------------------------------


def test_backend_autodetect_finds_bind_first():
    """
        With ``named`` present, auto-detect resolves to BIND
        without probing further.
    """
    runner = _MockRunner()
    _seed_command_v(runner, binary="named", found=True)
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    )
    manager = DnsServerManager(runner=runner)
    backend = manager._ensure_backend()
    testbase.assert_equal(backend, DnsServerBackend.BIND)
    return


def test_backend_autodetect_falls_back_to_unbound():
    """
        With ``named`` missing but ``unbound`` present,
        auto-detect resolves to UNBOUND.
    """
    runner = _MockRunner()
    _seed_command_v(runner, binary="named", found=False)
    _seed_command_v(runner, binary="unbound", found=True)
    manager = DnsServerManager(runner=runner)
    backend = manager._ensure_backend()
    testbase.assert_equal(backend, DnsServerBackend.UNBOUND)
    return


def test_backend_autodetect_falls_back_to_dnsmasq():
    """
        With only ``dnsmasq`` present, auto-detect resolves to
        DNSMASQ.
    """
    runner = _MockRunner()
    _seed_command_v(runner, binary="named", found=False)
    _seed_command_v(runner, binary="unbound", found=False)
    _seed_command_v(runner, binary="dnsmasq", found=True)
    manager = DnsServerManager(runner=runner)
    backend = manager._ensure_backend()
    testbase.assert_equal(backend, DnsServerBackend.DNSMASQ)
    return


def test_backend_autodetect_raises_when_none_present():
    """
        With no DNS-server binary on PATH, auto-detect raises
        :class:`DnsBackendNotAvailableError`.
    """
    runner = _MockRunner()
    _seed_command_v(runner, binary="named", found=False)
    _seed_command_v(runner, binary="unbound", found=False)
    _seed_command_v(runner, binary="dnsmasq", found=False)
    manager = DnsServerManager(runner=runner)
    raised = False
    try:
        manager._ensure_backend()
    except DnsBackendNotAvailableError:
        raised = True
    testbase.assert_equal(raised, True)
    return


def test_backend_not_available_error_is_semantic():
    """
        ``DnsBackendNotAvailableError`` derives from
        ``SemanticError`` so callers can rely on the contract.
    """
    testbase.assert_true(issubclass(DnsBackendNotAvailableError, SemanticError))
    testbase.assert_true(issubclass(DnsBackendMismatchError, SemanticError))
    return


def test_backend_mismatch_raised_for_bind_only_method_under_unbound():
    """
        Calling :meth:`define_zone` against an UNBOUND backend
        raises :class:`DnsBackendMismatchError`.
    """
    runner = _MockRunner()
    config = DnsServerManagerConfig(backend=DnsServerBackend.UNBOUND)
    manager = DnsServerManager(runner=runner, config=config)
    entry = ZoneEntry(
        name="example.com", type="master",
        file_path="/var/named/example.com.db",
    )
    raised = False
    try:
        manager.define_zone(entry=entry)
    except DnsBackendMismatchError:
        raised = True
    testbase.assert_equal(raised, True)
    return


def test_backend_mismatch_raised_for_unbound_method_under_bind():
    """
        Calling :meth:`add_forward_zone` against a BIND backend
        raises :class:`DnsBackendMismatchError`.
    """
    runner = _MockRunner()
    config = DnsServerManagerConfig(backend=DnsServerBackend.BIND)
    manager = DnsServerManager(runner=runner, config=config)
    raised = False
    try:
        manager.add_forward_zone(
            name="corp.example.com", forwarders=["10.0.0.1"],
        )
    except DnsBackendMismatchError:
        raised = True
    testbase.assert_equal(raised, True)
    return


# ----------------------------------------------------------------------
# validate()
# ----------------------------------------------------------------------


def test_validate_bind_happy_path():
    """
        validate() with BIND dispatches ``named-checkconf`` and
        the per-zone ``named-checkzone`` invocations.
    """
    runner = _MockRunner()
    runner.results[("named-checkconf",)] = RunResult(
        exit_code=0, stdout="", stderr="",
        command=["named-checkconf"],
    )
    runner.results[(
        "named-checkzone", "example.com", "/var/named/example.com.db",
    )] = RunResult(
        exit_code=0, stdout="OK", stderr="",
        command=["named-checkzone"],
    )
    config = DnsServerManagerConfig(backend=DnsServerBackend.BIND)
    manager = DnsServerManager(runner=runner, config=config)
    result = manager.validate(
        zone_files=[("example.com", "/var/named/example.com.db")],
    )
    testbase.assert_true(isinstance(result, DnsValidateResult))
    testbase.assert_equal(result.backend, DnsServerBackend.BIND)
    testbase.assert_true(result.ok)
    testbase.assert_equal(len(result.zone_check_results), 1)
    testbase.assert_equal(result.zone_check_results[0][0], "example.com")
    testbase.assert_true(result.zone_check_results[0][1])
    return


def test_validate_bind_failure_path():
    """
        validate() with a non-zero ``named-checkconf`` returns
        ok=False with the captured stderr.
    """
    runner = _MockRunner()
    runner.results[("named-checkconf",)] = RunResult(
        exit_code=1, stdout="", stderr="syntax error in named.conf",
        command=["named-checkconf"],
    )
    config = DnsServerManagerConfig(backend=DnsServerBackend.BIND)
    manager = DnsServerManager(runner=runner, config=config)
    result = manager.validate()
    testbase.assert_equal(result.ok, False)
    testbase.assert_equal(result.exit_code, 1)
    testbase.assert_true("syntax error" in result.stderr)
    return


def test_validate_unbound_dispatches_unbound_checkconf():
    """
        validate() with UNBOUND dispatches ``unbound-checkconf``.
    """
    runner = _MockRunner()
    runner.results[("unbound-checkconf",)] = RunResult(
        exit_code=0, stdout="", stderr="",
        command=["unbound-checkconf"],
    )
    config = DnsServerManagerConfig(backend=DnsServerBackend.UNBOUND)
    manager = DnsServerManager(runner=runner, config=config)
    result = manager.validate()
    testbase.assert_equal(result.backend, DnsServerBackend.UNBOUND)
    testbase.assert_true(result.ok)
    return


def test_validate_dnsmasq_dispatches_dnsmasq_test():
    """
        validate() with DNSMASQ dispatches ``dnsmasq --test``.
    """
    runner = _MockRunner()
    runner.results[("dnsmasq", "--test")] = RunResult(
        exit_code=0, stdout="", stderr="syntax check OK",
        command=["dnsmasq", "--test"],
    )
    config = DnsServerManagerConfig(backend=DnsServerBackend.DNSMASQ)
    manager = DnsServerManager(runner=runner, config=config)
    result = manager.validate()
    testbase.assert_equal(result.backend, DnsServerBackend.DNSMASQ)
    testbase.assert_true(result.ok)
    return


# ----------------------------------------------------------------------
# reload()
# ----------------------------------------------------------------------


def test_reload_bind_runs_validate_then_rndc_reload():
    """
        reload() with BIND runs ``named-checkconf`` first, then
        ``rndc reload`` only when validation passed.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    )
    config = DnsServerManagerConfig(
        backend=DnsServerBackend.BIND, elevated=True,
    )
    manager = DnsServerManager(runner=runner, config=config)
    result = manager.reload()
    testbase.assert_equal(result.status, DnsActionStatus.SUCCESS)
    rndc_calls = [
        call for call in runner.calls
        if call["args"] == ["rndc", "reload"]
    ]
    testbase.assert_equal(len(rndc_calls), 1)
    testbase.assert_true(rndc_calls[0]["elevated"])

    checkconf_calls = [
        call for call in runner.calls
        if call["args"] == ["named-checkconf"]
    ]
    testbase.assert_equal(len(checkconf_calls), 1)
    return


def test_reload_does_not_dispatch_when_validation_fails():
    """
        reload() does not dispatch ``rndc reload`` when
        ``named-checkconf`` exits non-zero.
    """
    runner = _MockRunner()
    runner.results[("named-checkconf",)] = RunResult(
        exit_code=1, stdout="", stderr="bad config",
        command=["named-checkconf"],
    )
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    )
    config = DnsServerManagerConfig(backend=DnsServerBackend.BIND)
    manager = DnsServerManager(runner=runner, config=config)
    result = manager.reload()
    testbase.assert_equal(result.status, DnsActionStatus.FAILED)
    rndc_calls = [
        call for call in runner.calls
        if call["args"] == ["rndc", "reload"]
    ]
    testbase.assert_equal(len(rndc_calls), 0)
    return


def test_reload_unbound_dispatches_unbound_control_reload():
    """
        reload() with UNBOUND runs ``unbound-control reload``
        after a successful ``unbound-checkconf``.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    )
    config = DnsServerManagerConfig(backend=DnsServerBackend.UNBOUND)
    manager = DnsServerManager(runner=runner, config=config)
    result = manager.reload()
    testbase.assert_equal(result.status, DnsActionStatus.SUCCESS)
    reload_calls = [
        call for call in runner.calls
        if call["args"] == ["unbound-control", "reload"]
    ]
    testbase.assert_equal(len(reload_calls), 1)
    return


def test_reload_dnsmasq_delegates_restart():
    """
        reload() with DNSMASQ delegates restart through the
        injected service manager rather than dispatching a reload
        command.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    )
    service_mgr = _MockServiceManager()
    config = DnsServerManagerConfig(backend=DnsServerBackend.DNSMASQ)
    manager = DnsServerManager(
        runner=runner, config=config, service_manager=service_mgr,
    )
    result = manager.reload()
    testbase.assert_equal(result.status, DnsActionStatus.SUCCESS)
    testbase.assert_equal(service_mgr.restart_calls, ["dnsmasq"])
    return


def test_reload_dry_run_skips_dispatch():
    """
        reload() in dry-run mode returns DRY_RUN without
        validating or dispatching the reload command.
    """
    runner = _MockRunner()
    config = DnsServerManagerConfig(
        backend=DnsServerBackend.BIND, dry_run=True,
    )
    manager = DnsServerManager(runner=runner, config=config)
    result = manager.reload()
    testbase.assert_equal(result.status, DnsActionStatus.DRY_RUN)
    testbase.assert_equal(len(runner.calls), 0)
    return


# ----------------------------------------------------------------------
# restart()
# ----------------------------------------------------------------------


def test_restart_delegates_to_service_manager():
    """
        restart() delegates a ``restart`` call through the
        injected service manager keyed by the per-backend unit
        name.
    """
    runner = _MockRunner()
    service_mgr = _MockServiceManager()
    config = DnsServerManagerConfig(backend=DnsServerBackend.BIND)
    manager = DnsServerManager(
        runner=runner, config=config, service_manager=service_mgr,
    )
    result = manager.restart()
    testbase.assert_equal(result.status, DnsActionStatus.SUCCESS)
    testbase.assert_equal(service_mgr.restart_calls, ["named"])
    return


def test_restart_returns_failed_when_service_manager_missing():
    """
        restart() returns FAILED when no service manager was
        injected.
    """
    runner = _MockRunner()
    config = DnsServerManagerConfig(backend=DnsServerBackend.BIND)
    manager = DnsServerManager(runner=runner, config=config)
    result = manager.restart()
    testbase.assert_equal(result.status, DnsActionStatus.FAILED)
    return


# ----------------------------------------------------------------------
# status()
# ----------------------------------------------------------------------


def test_status_bind_parses_rndc_status():
    """
        status() with BIND dispatches ``rndc status`` and
        extracts the version token.
    """
    runner = _MockRunner()
    runner.results[("rndc", "status")] = RunResult(
        exit_code=0,
        stdout=(
            "version: BIND 9.16.23-RH\n"
            "running on host: foo\n"
            "number of zones: 4\n"
        ),
        stderr="",
        command=["rndc", "status"],
    )
    config = DnsServerManagerConfig(backend=DnsServerBackend.BIND)
    manager = DnsServerManager(runner=runner, config=config)
    status = manager.status()
    testbase.assert_true(isinstance(status, DnsServerStatus))
    testbase.assert_true(status.running)
    testbase.assert_equal(status.version, "BIND 9.16.23-RH")
    return


def test_status_bind_running_false_when_rndc_fails():
    """
        status() reports ``running=False`` when ``rndc status``
        exits non-zero.
    """
    runner = _MockRunner()
    runner.results[("rndc", "status")] = RunResult(
        exit_code=1, stdout="", stderr="connection refused",
        command=["rndc", "status"],
    )
    config = DnsServerManagerConfig(backend=DnsServerBackend.BIND)
    manager = DnsServerManager(runner=runner, config=config)
    status = manager.status()
    testbase.assert_equal(status.running, False)
    return


def test_status_dnsmasq_uses_service_manager_is_active():
    """
        status() with DNSMASQ delegates to
        ``service_manager.is_active``.
    """
    runner = _MockRunner()
    service_mgr = _MockServiceManager(is_active_value=True)
    config = DnsServerManagerConfig(backend=DnsServerBackend.DNSMASQ)
    manager = DnsServerManager(
        runner=runner, config=config, service_manager=service_mgr,
    )
    status = manager.status()
    testbase.assert_true(status.running)
    testbase.assert_equal(service_mgr.is_active_calls, ["dnsmasq"])
    return


# ----------------------------------------------------------------------
# BIND zone management
# ----------------------------------------------------------------------


def test_define_zone_writes_drop_in_and_reloads():
    """
        define_zone() writes the rendered zone block to a drop-in
        file and dispatches ``rndc reload`` after validation.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    )
    _seed_cat(runner, path="/etc/named/zones.d/example.com.conf", body=None)
    config = DnsServerManagerConfig(
        backend=DnsServerBackend.BIND, elevated=True,
    )
    manager = DnsServerManager(runner=runner, config=config)
    entry = ZoneEntry(
        name="example.com",
        type="master",
        file_path="/var/named/example.com.db",
    )
    result = manager.define_zone(entry=entry)
    testbase.assert_equal(result.status, DnsActionStatus.SUCCESS)

    tee_calls = [
        call for call in runner.calls
        if len(call["args"]) >= 2
        and call["args"][0] == "tee"
        and call["args"][1] == "/etc/named/zones.d/example.com.conf"
    ]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true("zone \"example.com\"" in tee_calls[0]["stdin"])
    testbase.assert_true("type master" in tee_calls[0]["stdin"])
    testbase.assert_true(tee_calls[0]["elevated"])

    rndc_calls = [
        call for call in runner.calls
        if call["args"] == ["rndc", "reload"]
    ]
    testbase.assert_equal(len(rndc_calls), 1)
    return


def test_define_zone_skipped_when_drop_in_matches():
    """
        define_zone() returns SKIPPED when the drop-in already
        contains the canonical block.
    """
    runner = _MockRunner()
    entry = ZoneEntry(
        name="example.com",
        type="master",
        file_path="/var/named/example.com.db",
    )
    rendered = _render_bind_zone_block(entry=entry)
    _seed_cat(
        runner,
        path="/etc/named/zones.d/example.com.conf",
        body=rendered,
    )
    config = DnsServerManagerConfig(backend=DnsServerBackend.BIND)
    manager = DnsServerManager(runner=runner, config=config)
    result = manager.define_zone(entry=entry)
    testbase.assert_equal(result.status, DnsActionStatus.SKIPPED)
    tee_calls = [
        call for call in runner.calls
        if len(call["args"]) >= 1 and call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 0)
    return


def test_define_zone_dry_run_skips_dispatch():
    """
        define_zone() with dry_run=True returns DRY_RUN and never
        invokes the runner.
    """
    runner = _MockRunner()
    config = DnsServerManagerConfig(
        backend=DnsServerBackend.BIND, dry_run=True,
    )
    manager = DnsServerManager(runner=runner, config=config)
    entry = ZoneEntry(
        name="example.com",
        type="master",
        file_path="/var/named/example.com.db",
    )
    result = manager.define_zone(entry=entry)
    testbase.assert_equal(result.status, DnsActionStatus.DRY_RUN)
    testbase.assert_equal(len(runner.calls), 0)
    return


def test_remove_zone_strips_drop_in_and_zone_file():
    """
        remove_zone() removes the drop-in declaration and the
        zone-data file, then triggers a reload.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    )
    _seed_test_dash_e(
        runner, path="/etc/named/zones.d/example.com.conf", exists=True,
    )
    _seed_test_dash_e(
        runner, path="/var/named/example.com.db", exists=True,
    )
    config = DnsServerManagerConfig(
        backend=DnsServerBackend.BIND, elevated=True,
    )
    manager = DnsServerManager(runner=runner, config=config)
    result = manager.remove_zone(name="example.com")
    testbase.assert_equal(result.status, DnsActionStatus.SUCCESS)

    rm_calls = [
        call for call in runner.calls
        if call["args"][0] == "rm"
    ]
    testbase.assert_equal(len(rm_calls), 2)
    rm_targets = [call["args"][2] for call in rm_calls]
    testbase.assert_true(
        "/etc/named/zones.d/example.com.conf" in rm_targets,
    )
    testbase.assert_true("/var/named/example.com.db" in rm_targets)

    rndc_calls = [
        call for call in runner.calls
        if call["args"] == ["rndc", "reload"]
    ]
    testbase.assert_equal(len(rndc_calls), 1)
    return


def test_remove_zone_skipped_when_absent():
    """
        remove_zone() returns SKIPPED when neither the drop-in
        nor the zone file is present.
    """
    runner = _MockRunner()
    _seed_test_dash_e(
        runner, path="/etc/named/zones.d/ghost.conf", exists=False,
    )
    _seed_test_dash_e(
        runner, path="/var/named/ghost.db", exists=False,
    )
    config = DnsServerManagerConfig(backend=DnsServerBackend.BIND)
    manager = DnsServerManager(runner=runner, config=config)
    result = manager.remove_zone(name="ghost")
    testbase.assert_equal(result.status, DnsActionStatus.SKIPPED)
    rm_calls = [
        call for call in runner.calls
        if call["args"][0] == "rm"
    ]
    testbase.assert_equal(len(rm_calls), 0)
    return


def test_query_zone_returns_zone_entry():
    """
        query_zone() returns a populated ZoneEntry with parsed
        type / file_path and the zone-data file content when
        readable.
    """
    runner = _MockRunner()
    entry = ZoneEntry(
        name="example.com",
        type="master",
        file_path="/var/named/example.com.db",
    )
    rendered_dropin = _render_bind_zone_block(entry=entry)
    _seed_cat(
        runner,
        path="/etc/named/zones.d/example.com.conf",
        body=rendered_dropin,
    )
    _seed_cat(
        runner,
        path="/var/named/example.com.db",
        body="; zone body\n",
    )
    config = DnsServerManagerConfig(backend=DnsServerBackend.BIND)
    manager = DnsServerManager(runner=runner, config=config)
    result = manager.query_zone(name="example.com")
    testbase.assert_true(isinstance(result, ZoneEntry))
    testbase.assert_equal(result.name, "example.com")
    testbase.assert_equal(result.type, "master")
    testbase.assert_equal(result.file_path, "/var/named/example.com.db")
    testbase.assert_equal(
        result.source_conf, "/etc/named/zones.d/example.com.conf",
    )
    testbase.assert_true("zone body" in (result.file_content or ""))
    return


def test_query_zone_returns_none_when_dropin_missing():
    """
        query_zone() returns ``None`` when the drop-in is absent.
    """
    runner = _MockRunner()
    _seed_cat(runner, path="/etc/named/zones.d/ghost.conf", body=None)
    config = DnsServerManagerConfig(backend=DnsServerBackend.BIND)
    manager = DnsServerManager(runner=runner, config=config)
    result = manager.query_zone(name="ghost")
    testbase.assert_true(result is None)
    return


def test_write_zone_file_idempotent():
    """
        write_zone_file() returns SKIPPED when the on-disk content
        already matches the requested body.
    """
    runner = _MockRunner()
    body = "$TTL 3600\n@ IN NS ns1.example.com.\n"
    _seed_cat(
        runner, path="/var/named/example.com.db", body=body,
    )
    config = DnsServerManagerConfig(backend=DnsServerBackend.BIND)
    manager = DnsServerManager(runner=runner, config=config)
    result = manager.write_zone_file(zone="example.com", content=body)
    testbase.assert_equal(result.status, DnsActionStatus.SKIPPED)
    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 0)
    return


def test_write_zone_file_writes_when_changed():
    """
        write_zone_file() dispatches ``tee`` when the on-disk
        content differs from the requested body.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    )
    _seed_cat(runner, path="/var/named/example.com.db", body="old\n")
    config = DnsServerManagerConfig(
        backend=DnsServerBackend.BIND, elevated=True,
    )
    manager = DnsServerManager(runner=runner, config=config)
    result = manager.write_zone_file(
        zone="example.com", content="new\n",
    )
    testbase.assert_equal(result.status, DnsActionStatus.SUCCESS)
    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_equal(
        tee_calls[0]["args"], ["tee", "/var/named/example.com.db"],
    )
    testbase.assert_equal(tee_calls[0]["stdin"], "new\n")
    testbase.assert_true(tee_calls[0]["elevated"])
    return


# ----------------------------------------------------------------------
# Unbound forward / stub zones
# ----------------------------------------------------------------------


def test_add_forward_zone_writes_block_and_reloads():
    """
        add_forward_zone() upserts a ``forward-zone:`` block in
        the Unbound drop-in and reloads.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    )
    _seed_cat(runner, path="/etc/unbound/conf.d/ccai.conf", body=None)
    config = DnsServerManagerConfig(
        backend=DnsServerBackend.UNBOUND, elevated=True,
    )
    manager = DnsServerManager(runner=runner, config=config)
    result = manager.add_forward_zone(
        name="corp.example.com.",
        forwarders=["10.0.0.1", "10.0.0.2"],
    )
    testbase.assert_equal(result.status, DnsActionStatus.SUCCESS)

    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 1)
    body = tee_calls[0]["stdin"]
    testbase.assert_true("forward-zone:" in body)
    testbase.assert_true("name: \"corp.example.com.\"" in body)
    testbase.assert_true("forward-addr: 10.0.0.1" in body)
    testbase.assert_true("forward-addr: 10.0.0.2" in body)

    reload_calls = [
        call for call in runner.calls
        if call["args"] == ["unbound-control", "reload"]
    ]
    testbase.assert_equal(len(reload_calls), 1)
    return


def test_add_forward_zone_skipped_when_unchanged():
    """
        add_forward_zone() returns SKIPPED when the drop-in
        already contains the identical block.
    """
    runner = _MockRunner()
    rendered = _render_unbound_block(
        block_kind="forward-zone",
        name="corp.example.com.",
        addresses=["10.0.0.1"],
        address_directive="forward-addr",
    )
    _seed_cat(
        runner,
        path="/etc/unbound/conf.d/ccai.conf",
        body=rendered + "\n",
    )
    config = DnsServerManagerConfig(backend=DnsServerBackend.UNBOUND)
    manager = DnsServerManager(runner=runner, config=config)
    result = manager.add_forward_zone(
        name="corp.example.com.", forwarders=["10.0.0.1"],
    )
    testbase.assert_equal(result.status, DnsActionStatus.SKIPPED)
    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 0)
    return


def test_remove_forward_zone_strips_block():
    """
        remove_forward_zone() strips the matching block and
        reloads.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    )
    rendered = _render_unbound_block(
        block_kind="forward-zone",
        name="corp.example.com.",
        addresses=["10.0.0.1"],
        address_directive="forward-addr",
    )
    _seed_cat(
        runner,
        path="/etc/unbound/conf.d/ccai.conf",
        body=rendered + "\n",
    )
    config = DnsServerManagerConfig(backend=DnsServerBackend.UNBOUND)
    manager = DnsServerManager(runner=runner, config=config)
    result = manager.remove_forward_zone(name="corp.example.com.")
    testbase.assert_equal(result.status, DnsActionStatus.SUCCESS)
    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 1)
    new_body = tee_calls[0]["stdin"]
    testbase.assert_true("forward-zone:" not in new_body)
    return


def test_remove_forward_zone_skipped_when_absent():
    """
        remove_forward_zone() returns SKIPPED when the block is
        not present in the drop-in.
    """
    runner = _MockRunner()
    _seed_cat(
        runner,
        path="/etc/unbound/conf.d/ccai.conf",
        body="# nothing managed here\n",
    )
    config = DnsServerManagerConfig(backend=DnsServerBackend.UNBOUND)
    manager = DnsServerManager(runner=runner, config=config)
    result = manager.remove_forward_zone(name="corp.example.com.")
    testbase.assert_equal(result.status, DnsActionStatus.SKIPPED)
    return


def test_add_stub_zone_writes_stub_addr_lines():
    """
        add_stub_zone() emits a ``stub-zone:`` block whose
        per-address directives use ``stub-addr:``.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    )
    _seed_cat(runner, path="/etc/unbound/conf.d/ccai.conf", body=None)
    config = DnsServerManagerConfig(backend=DnsServerBackend.UNBOUND)
    manager = DnsServerManager(runner=runner, config=config)
    result = manager.add_stub_zone(
        name="lab.example.com.", addresses=["10.1.0.1"],
    )
    testbase.assert_equal(result.status, DnsActionStatus.SUCCESS)
    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 1)
    body = tee_calls[0]["stdin"]
    testbase.assert_true("stub-zone:" in body)
    testbase.assert_true("stub-addr: 10.1.0.1" in body)
    return


# ----------------------------------------------------------------------
# dnsmasq directives
# ----------------------------------------------------------------------


def test_set_static_host_writes_host_record_and_restarts():
    """
        set_static_host() upserts ``host-record=`` in the dnsmasq
        drop-in and delegates a restart to the service manager.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    )
    _seed_cat(runner, path="/etc/dnsmasq.d/ccai.conf", body=None)
    service_mgr = _MockServiceManager()
    config = DnsServerManagerConfig(
        backend=DnsServerBackend.DNSMASQ, elevated=True,
    )
    manager = DnsServerManager(
        runner=runner, config=config, service_manager=service_mgr,
    )
    result = manager.set_static_host(
        host="printer.lan", ip="192.168.1.10",
    )
    testbase.assert_equal(result.status, DnsActionStatus.SUCCESS)

    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 1)
    body = tee_calls[0]["stdin"]
    testbase.assert_true(
        "host-record=printer.lan,192.168.1.10" in body,
    )
    testbase.assert_true(tee_calls[0]["elevated"])

    testbase.assert_equal(service_mgr.restart_calls, ["dnsmasq"])
    return


def test_set_static_host_skipped_when_unchanged():
    """
        set_static_host() returns SKIPPED when the directive is
        already on disk.
    """
    runner = _MockRunner()
    body = "host-record=printer.lan,192.168.1.10\n"
    _seed_cat(
        runner, path="/etc/dnsmasq.d/ccai.conf", body=body,
    )
    service_mgr = _MockServiceManager()
    config = DnsServerManagerConfig(backend=DnsServerBackend.DNSMASQ)
    manager = DnsServerManager(
        runner=runner, config=config, service_manager=service_mgr,
    )
    result = manager.set_static_host(
        host="printer.lan", ip="192.168.1.10",
    )
    testbase.assert_equal(result.status, DnsActionStatus.SKIPPED)
    testbase.assert_equal(service_mgr.restart_calls, [])
    return


def test_remove_static_host_strips_directive_and_restarts():
    """
        remove_static_host() strips matching directives and
        delegates a restart.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    )
    body = (
        "host-record=other,10.0.0.5\n"
        "host-record=printer.lan,192.168.1.10\n"
    )
    _seed_cat(
        runner, path="/etc/dnsmasq.d/ccai.conf", body=body,
    )
    service_mgr = _MockServiceManager()
    config = DnsServerManagerConfig(backend=DnsServerBackend.DNSMASQ)
    manager = DnsServerManager(
        runner=runner, config=config, service_manager=service_mgr,
    )
    result = manager.remove_static_host(host="printer.lan")
    testbase.assert_equal(result.status, DnsActionStatus.SUCCESS)
    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 1)
    new_body = tee_calls[0]["stdin"]
    testbase.assert_true("printer.lan" not in new_body)
    testbase.assert_true("other" in new_body)
    testbase.assert_equal(service_mgr.restart_calls, ["dnsmasq"])
    return


def test_set_forwarder_domain_writes_server_lines():
    """
        set_forwarder_domain() emits one ``server=/<domain>/<ip>``
        line per upstream and triggers a restart.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    )
    _seed_cat(runner, path="/etc/dnsmasq.d/ccai.conf", body=None)
    service_mgr = _MockServiceManager()
    config = DnsServerManagerConfig(backend=DnsServerBackend.DNSMASQ)
    manager = DnsServerManager(
        runner=runner, config=config, service_manager=service_mgr,
    )
    result = manager.set_forwarder_domain(
        domain="corp.example.com",
        servers=["10.0.0.1", "10.0.0.2"],
    )
    testbase.assert_equal(result.status, DnsActionStatus.SUCCESS)
    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 1)
    body = tee_calls[0]["stdin"]
    testbase.assert_true("server=/corp.example.com/10.0.0.1" in body)
    testbase.assert_true("server=/corp.example.com/10.0.0.2" in body)
    testbase.assert_equal(service_mgr.restart_calls, ["dnsmasq"])
    return


def test_remove_forwarder_domain_strips_directives():
    """
        remove_forwarder_domain() strips every matching
        ``server=/<domain>/*`` directive.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=[],
    )
    body = (
        "server=/corp.example.com/10.0.0.1\n"
        "server=/corp.example.com/10.0.0.2\n"
        "server=/other.example.com/10.9.9.9\n"
    )
    _seed_cat(
        runner, path="/etc/dnsmasq.d/ccai.conf", body=body,
    )
    service_mgr = _MockServiceManager()
    config = DnsServerManagerConfig(backend=DnsServerBackend.DNSMASQ)
    manager = DnsServerManager(
        runner=runner, config=config, service_manager=service_mgr,
    )
    result = manager.remove_forwarder_domain(domain="corp.example.com")
    testbase.assert_equal(result.status, DnsActionStatus.SUCCESS)
    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 1)
    new_body = tee_calls[0]["stdin"]
    testbase.assert_true("corp.example.com" not in new_body)
    testbase.assert_true("other.example.com" in new_body)
    return


# ----------------------------------------------------------------------
# Failure surfacing and dry-run sweeps
# ----------------------------------------------------------------------


def test_define_zone_failure_propagates_runner_error():
    """
        When ``tee`` fails to write the drop-in, define_zone()
        surfaces the runner's exit code and stderr.
    """
    runner = _MockRunner()
    _seed_cat(runner, path="/etc/named/zones.d/example.com.conf", body=None)
    runner.results[("tee", "/etc/named/zones.d/example.com.conf")] = RunResult(
        exit_code=2, stdout="", stderr="permission denied",
        command=["tee", "/etc/named/zones.d/example.com.conf"],
    )
    config = DnsServerManagerConfig(backend=DnsServerBackend.BIND)
    manager = DnsServerManager(runner=runner, config=config)
    entry = ZoneEntry(
        name="example.com",
        type="master",
        file_path="/var/named/example.com.db",
    )
    result = manager.define_zone(entry=entry)
    testbase.assert_equal(result.status, DnsActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 2)
    testbase.assert_true("permission denied" in (result.stderr or ""))

    rndc_calls = [
        call for call in runner.calls
        if call["args"] == ["rndc", "reload"]
    ]
    testbase.assert_equal(len(rndc_calls), 0)
    return


def test_dry_run_sweep_skips_every_write():
    """
        dry_run=True returns DRY_RUN for every write verb without
        invoking the runner.
    """
    runner = _MockRunner()
    bind_config = DnsServerManagerConfig(
        backend=DnsServerBackend.BIND, dry_run=True,
    )
    bind_manager = DnsServerManager(runner=runner, config=bind_config)
    entry = ZoneEntry(
        name="example.com",
        type="master",
        file_path="/var/named/example.com.db",
    )
    write_zone = bind_manager.write_zone_file(
        zone="example.com", content="body",
    )
    define_zone = bind_manager.define_zone(entry=entry)
    remove_zone = bind_manager.remove_zone(name="example.com")
    testbase.assert_equal(write_zone.status, DnsActionStatus.DRY_RUN)
    testbase.assert_equal(define_zone.status, DnsActionStatus.DRY_RUN)
    testbase.assert_equal(remove_zone.status, DnsActionStatus.DRY_RUN)

    unbound_config = DnsServerManagerConfig(
        backend=DnsServerBackend.UNBOUND, dry_run=True,
    )
    unbound_manager = DnsServerManager(
        runner=runner, config=unbound_config,
    )
    add_forward = unbound_manager.add_forward_zone(
        name="corp", forwarders=["10.0.0.1"],
    )
    remove_forward = unbound_manager.remove_forward_zone(name="corp")
    testbase.assert_equal(add_forward.status, DnsActionStatus.DRY_RUN)
    testbase.assert_equal(remove_forward.status, DnsActionStatus.DRY_RUN)

    dnsmasq_config = DnsServerManagerConfig(
        backend=DnsServerBackend.DNSMASQ, dry_run=True,
    )
    dnsmasq_manager = DnsServerManager(
        runner=runner, config=dnsmasq_config,
    )
    set_host = dnsmasq_manager.set_static_host(
        host="printer.lan", ip="192.168.1.10",
    )
    remove_host = dnsmasq_manager.remove_static_host(host="printer.lan")
    testbase.assert_equal(set_host.status, DnsActionStatus.DRY_RUN)
    testbase.assert_equal(remove_host.status, DnsActionStatus.DRY_RUN)

    testbase.assert_equal(len(runner.calls), 0)
    return


# ----------------------------------------------------------------------
# Module-level helpers
# ----------------------------------------------------------------------


def test_render_bind_zone_block_round_trips():
    """
        _render_bind_zone_block emits a block whose type and file
        survive a round-trip through _parse_bind_zone_block.
    """
    entry = ZoneEntry(
        name="example.com",
        type="master",
        file_path="/var/named/example.com.db",
    )
    rendered = _render_bind_zone_block(entry=entry)
    parsed_type, parsed_file = _parse_bind_zone_block(content=rendered)
    testbase.assert_equal(parsed_type, "master")
    testbase.assert_equal(parsed_file, "/var/named/example.com.db")
    return


def test_unbound_block_upsert_round_trip():
    """
        Re-upserting the same block yields identical content.
    """
    rendered = _render_unbound_block(
        block_kind="forward-zone",
        name="corp.example.com.",
        addresses=["10.0.0.1"],
        address_directive="forward-addr",
    )
    seeded = _upsert_unbound_block_text(
        content="",
        block_kind="forward-zone",
        name="corp.example.com.",
        block_body=rendered,
    )
    re_upserted = _upsert_unbound_block_text(
        content=seeded,
        block_kind="forward-zone",
        name="corp.example.com.",
        block_body=rendered,
    )
    testbase.assert_equal(seeded, re_upserted)

    parsed_blocks = _parse_unbound_blocks(content=seeded)
    matched = [
        record for record in parsed_blocks
        if record["kind"] == "forward-zone"
        and record["name"] == "corp.example.com."
    ]
    testbase.assert_equal(len(matched), 1)
    return


def test_dnsmasq_line_helpers():
    """
        ``_upsert_dnsmasq_lines`` strips matching lines before
        appending the replacement; ``_strip_dnsmasq_lines``
        strips them altogether.
    """
    body = (
        "host-record=alpha,10.0.0.1\n"
        "host-record=alpha,10.0.0.2\n"
        "host-record=beta,10.0.0.3\n"
    )
    upserted = _upsert_dnsmasq_lines(
        content=body,
        line_prefix="host-record=alpha,",
        replacement_block="host-record=alpha,10.0.0.99",
    )
    testbase.assert_true("10.0.0.99" in upserted)
    testbase.assert_true("10.0.0.1" not in upserted)
    testbase.assert_true("10.0.0.2" not in upserted)
    testbase.assert_true("beta" in upserted)

    stripped = _strip_dnsmasq_lines(
        content=body, line_prefix="host-record=alpha,",
    )
    testbase.assert_true("alpha" not in stripped)
    testbase.assert_true("beta" in stripped)
    return


def test_parse_version_from_rndc_status_handles_lowercase():
    """
        The version parser is case-insensitive on the key.
    """
    text = "Version: BIND 9.16.0\nrunning: yes\n"
    parsed = _parse_version_from_rndc_status(text=text)
    testbase.assert_equal(parsed, "BIND 9.16.0")

    parsed_missing = _parse_version_from_rndc_status(text="hello world\n")
    testbase.assert_true(parsed_missing is None)
    return
