# Tests for the PrecisionTimeManager facade.

from dataclasses import dataclass, field
from typing import Callable, Optional

from ccai import testbase

from ccai.sysman.linux.redhat.featurebase import ActionStatus
from ccai.sysman.linux.redhat.precisiontimemanagement import (
    PrecisionTimeManager,
    PrecisionTimeManagerConfig,
    PtpActionResult,
    PtpActionStatus,
    PtpCurrentDataSet,
    PtpParentDataSet,
    PtpPortDataSet,
    _parse_ptp4l_conf,
    _render_ptp4l_conf,
    _parse_sysconfig_options,
    _render_sysconfig_options,
)
from ccai.sysman.linux.redhat.runners.models import RunResult


# ----------------------------------------------------------------------
# Mock collaborators
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
        env: Optional[dict] = None,
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
        return RunResult(
            exit_code=0, stdout="", stderr="", command=list(args),
        )

    def add_aspect(self, aspect) -> None:
        return

    def remove_aspect(self, aspect) -> None:
        return

    def get_metrics(self):
        return None


@dataclass
class _SmInfo:
    active_state: str = "inactive"
    enabled_state: str = "disabled"


@dataclass
class _SmResult:
    status: ActionStatus = ActionStatus.SUCCESS
    detail: Optional[str] = None
    exit_code: Optional[int] = 0
    stdout: Optional[str] = ""
    stderr: Optional[str] = ""
    command: Optional[list] = None


@dataclass
class _MockServiceManager:

    info_by_service: dict = field(default_factory=dict)
    op_results: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)

    def _record(self, op: str, service: str) -> _SmResult:
        self.calls.append({"op": op, "service": service})
        key = (op, service)
        if key in self.op_results:
            return self.op_results[key]
        return _SmResult(status=ActionStatus.SUCCESS)

    def start(self, service: str) -> _SmResult:
        return self._record(op="start", service=service)

    def stop(self, service: str) -> _SmResult:
        return self._record(op="stop", service=service)

    def enable(self, service: str) -> _SmResult:
        return self._record(op="enable", service=service)

    def disable(self, service: str) -> _SmResult:
        return self._record(op="disable", service=service)

    def query(self, service: str) -> _SmInfo:
        if service in self.info_by_service:
            return self.info_by_service[service]
        return _SmInfo()


# ----------------------------------------------------------------------
# Sample fixtures
# ----------------------------------------------------------------------


_PTP4L_CONF_BODY = (
    "# Sample ptp4l.conf\n"
    "[global]\n"
    "domainNumber 0\n"
    "network_transport L2\n"
    "delay_mechanism E2E\n"
    "\n"
    "[eth0]\n"
    "logAnnounceInterval 1\n"
)

_PMC_CURRENT_DATA_SET = (
    "sending: GET CURRENT_DATA_SET\n"
    "\t507eaf.fffe.31a4d8-0 seq 0 RESPONSE MANAGEMENT CURRENT_DATA_SET\n"
    "\tstepsRemoved     1\n"
    "\toffsetFromMaster 1234\n"
    "\tmeanPathDelay    5678\n"
)

_PMC_PARENT_DATA_SET = (
    "sending: GET PARENT_DATA_SET\n"
    "\t507eaf.fffe.31a4d8-0 seq 0 RESPONSE MANAGEMENT PARENT_DATA_SET\n"
    "\tparentPortIdentity                    507eaf.fffe.000000-1\n"
    "\tgrandmasterIdentity                   507eaf.fffe.0000aa\n"
    "\tgrandmaster.priority1                 128\n"
    "\tgrandmaster.priority2                 127\n"
)

_PMC_PORT_DATA_SET = (
    "sending: GET PORT_DATA_SET\n"
    "\t507eaf.fffe.31a4d8-0 seq 0 RESPONSE MANAGEMENT PORT_DATA_SET\n"
    "\tportIdentity 507eaf.fffe.31a4d8-1\n"
    "\tportState SLAVE\n"
    "\tlogAnnounceInterval 1\n"
    "\tlogSyncInterval 0\n"
    "\tdelayMechanism E2E\n"
    "\tportIdentity 507eaf.fffe.31a4d8-2\n"
    "\tportState MASTER\n"
    "\tlogAnnounceInterval 1\n"
    "\tlogSyncInterval 0\n"
    "\tdelayMechanism P2P\n"
)


def _build_runner_with_conf(body: str) -> _MockRunner:
    runner = _MockRunner()
    runner.results[("cat", "/etc/linuxptp/ptp4l.conf")] = RunResult(
        exit_code=0, stdout=body, stderr="",
        command=["cat", "/etc/linuxptp/ptp4l.conf"],
    )
    return runner


# ----------------------------------------------------------------------
# Parser / renderer tests
# ----------------------------------------------------------------------


def test_parse_ptp4l_conf_separates_sections():
    """
        _parse_ptp4l_conf splits a config into [global] + per-iface
        sections and ignores comments / blank lines.
    """
    sections = _parse_ptp4l_conf(text=_PTP4L_CONF_BODY)
    testbase.assert_true("global" in sections)
    testbase.assert_true("eth0" in sections)
    testbase.assert_equal(sections["global"]["domainNumber"], "0")
    testbase.assert_equal(sections["global"]["network_transport"], "L2")
    testbase.assert_equal(sections["global"]["delay_mechanism"], "E2E")
    testbase.assert_equal(sections["eth0"]["logAnnounceInterval"], "1")
    return


def test_render_ptp4l_conf_uses_whitespace_syntax():
    """
        _render_ptp4l_conf emits ``key value`` (whitespace) lines —
        never ``key = value``.
    """
    sections = _parse_ptp4l_conf(text=_PTP4L_CONF_BODY)
    rendered = _render_ptp4l_conf(sections=sections)
    testbase.assert_true("[global]" in rendered)
    testbase.assert_true("[eth0]" in rendered)
    testbase.assert_true("domainNumber 0" in rendered)
    testbase.assert_true("network_transport L2" in rendered)
    # Negative — no equals-sign style.
    testbase.assert_true("domainNumber = 0" not in rendered)
    testbase.assert_true("network_transport = L2" not in rendered)
    testbase.assert_true("logAnnounceInterval = 1" not in rendered)
    return


def test_render_round_trip_preserves_directive_values():
    """
        Parser + renderer round-trip preserves every directive value.
    """
    sections = _parse_ptp4l_conf(text=_PTP4L_CONF_BODY)
    rendered = _render_ptp4l_conf(sections=sections)
    reparsed = _parse_ptp4l_conf(text=rendered)
    testbase.assert_equal(
        reparsed["global"]["domainNumber"],
        sections["global"]["domainNumber"],
    )
    testbase.assert_equal(
        reparsed["eth0"]["logAnnounceInterval"],
        sections["eth0"]["logAnnounceInterval"],
    )
    return


def test_parse_sysconfig_options_strips_quotes():
    """
        _parse_sysconfig_options strips surrounding double-quotes.
    """
    body = '# header\nOPTIONS="-a -r"\n'
    value = _parse_sysconfig_options(text=body)
    testbase.assert_equal(value, "-a -r")
    return


def test_parse_sysconfig_options_returns_none_when_absent():
    """
        _parse_sysconfig_options returns None when OPTIONS= is missing.
    """
    body = "# nothing here\nOTHER=foo\n"
    value = _parse_sysconfig_options(text=body)
    testbase.assert_true(value is None)
    return


def test_render_sysconfig_options_replaces_existing_line():
    """
        _render_sysconfig_options replaces an existing OPTIONS= line.
    """
    body = "# comment\nOPTIONS=\"-old\"\n"
    rendered = _render_sysconfig_options(existing_text=body, value="-new -opts")
    testbase.assert_true('OPTIONS="-new -opts"' in rendered)
    testbase.assert_true('OPTIONS="-old"' not in rendered)
    return


def test_render_sysconfig_options_appends_when_missing():
    """
        _render_sysconfig_options appends a fresh OPTIONS= line when
        the variable is absent.
    """
    body = "# only a comment\n"
    rendered = _render_sysconfig_options(existing_text=body, value="-a -r")
    testbase.assert_true('OPTIONS="-a -r"' in rendered)
    return


# ----------------------------------------------------------------------
# get_directives / set_directive / remove_directive
# ----------------------------------------------------------------------


def test_get_directives_returns_nested_dict():
    """
        get_directives parses the live config into the nested dict
        shape required by the contract.
    """
    runner = _build_runner_with_conf(body=_PTP4L_CONF_BODY)
    mgr = PrecisionTimeManager(runner=runner)
    directives = mgr.get_directives()
    testbase.assert_equal(directives["global"]["domainNumber"], "0")
    testbase.assert_equal(directives["eth0"]["logAnnounceInterval"], "1")
    return


def test_set_directive_skipped_when_unchanged():
    """
        set_directive returns SKIPPED when the existing value already
        matches.
    """
    runner = _build_runner_with_conf(body=_PTP4L_CONF_BODY)
    mgr = PrecisionTimeManager(runner=runner)
    result = mgr.set_directive(
        section="global", key="domainNumber", value="0",
    )
    testbase.assert_equal(result.status, PtpActionStatus.SKIPPED)
    # No tee call should have been attempted.
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 0)
    return


def test_set_directive_writes_new_value_via_tee():
    """
        set_directive dispatches a tee write with the new value when
        the directive changes.
    """
    runner = _build_runner_with_conf(body=_PTP4L_CONF_BODY)
    config = PrecisionTimeManagerConfig(elevated=True)
    mgr = PrecisionTimeManager(runner=runner, config=config)
    result = mgr.set_directive(
        section="global", key="domainNumber", value="24",
    )
    testbase.assert_equal(result.status, PtpActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    payload = tee_calls[0]["stdin"]
    testbase.assert_true("domainNumber 24" in payload)
    testbase.assert_true("domainNumber = 24" not in payload)
    testbase.assert_true(tee_calls[0]["elevated"])
    return


def test_set_directive_dry_run_does_not_invoke_tee():
    """
        set_directive returns DRY_RUN and does not invoke tee under
        dry-run mode.
    """
    runner = _build_runner_with_conf(body=_PTP4L_CONF_BODY)
    config = PrecisionTimeManagerConfig(dry_run=True)
    mgr = PrecisionTimeManager(runner=runner, config=config)
    result = mgr.set_directive(
        section="global", key="domainNumber", value="42",
    )
    testbase.assert_equal(result.status, PtpActionStatus.DRY_RUN)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 0)
    return


def test_remove_directive_skipped_when_absent():
    """
        remove_directive returns SKIPPED when the directive is not
        present.
    """
    runner = _build_runner_with_conf(body=_PTP4L_CONF_BODY)
    mgr = PrecisionTimeManager(runner=runner)
    result = mgr.remove_directive(section="global", key="bogusKey")
    testbase.assert_equal(result.status, PtpActionStatus.SKIPPED)
    return


def test_remove_directive_strips_line_via_tee():
    """
        remove_directive rewrites the file with the matching line
        stripped.
    """
    runner = _build_runner_with_conf(body=_PTP4L_CONF_BODY)
    mgr = PrecisionTimeManager(runner=runner)
    result = mgr.remove_directive(section="global", key="delay_mechanism")
    testbase.assert_equal(result.status, PtpActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    payload = tee_calls[0]["stdin"]
    testbase.assert_true("delay_mechanism" not in payload)
    testbase.assert_true("domainNumber 0" in payload)
    return


# ----------------------------------------------------------------------
# Preset helpers
# ----------------------------------------------------------------------


def test_set_domain_writes_global_section():
    """
        set_domain upserts ``domainNumber`` in [global].
    """
    runner = _build_runner_with_conf(body=_PTP4L_CONF_BODY)
    mgr = PrecisionTimeManager(runner=runner)
    result = mgr.set_domain(domain_number=7)
    testbase.assert_equal(result.status, PtpActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true("domainNumber 7" in tee_calls[0]["stdin"])
    return


def test_set_transport_writes_global_section():
    """
        set_transport upserts ``network_transport`` in [global].
    """
    runner = _build_runner_with_conf(body=_PTP4L_CONF_BODY)
    mgr = PrecisionTimeManager(runner=runner)
    result = mgr.set_transport(mode="UDPv4")
    testbase.assert_equal(result.status, PtpActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_true("network_transport UDPv4" in tee_calls[0]["stdin"])
    return


def test_set_delay_mechanism_writes_global_section():
    """
        set_delay_mechanism upserts ``delay_mechanism`` in [global].
    """
    runner = _build_runner_with_conf(body=_PTP4L_CONF_BODY)
    mgr = PrecisionTimeManager(runner=runner)
    result = mgr.set_delay_mechanism(mode="P2P")
    testbase.assert_equal(result.status, PtpActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_true("delay_mechanism P2P" in tee_calls[0]["stdin"])
    return


# ----------------------------------------------------------------------
# set_interfaces
# ----------------------------------------------------------------------


def test_set_interfaces_adds_missing_interface_section():
    """
        set_interfaces adds a [<iface>] section for each new interface.
    """
    runner = _build_runner_with_conf(body=_PTP4L_CONF_BODY)
    mgr = PrecisionTimeManager(runner=runner)
    result = mgr.set_interfaces(interfaces=["eth0", "ens5f0"])
    testbase.assert_equal(result.status, PtpActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    payload = tee_calls[0]["stdin"]
    testbase.assert_true("[eth0]" in payload)
    testbase.assert_true("[ens5f0]" in payload)
    return


def test_set_interfaces_strips_stale_interface_section():
    """
        set_interfaces removes per-interface sections not in the
        requested list.
    """
    runner = _build_runner_with_conf(body=_PTP4L_CONF_BODY)
    mgr = PrecisionTimeManager(runner=runner)
    result = mgr.set_interfaces(interfaces=["ens5f0"])
    testbase.assert_equal(result.status, PtpActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    payload = tee_calls[0]["stdin"]
    testbase.assert_true("[eth0]" not in payload)
    testbase.assert_true("[ens5f0]" in payload)
    # [global] is always preserved.
    testbase.assert_true("[global]" in payload)
    return


def test_set_interfaces_skipped_when_already_matching():
    """
        set_interfaces returns SKIPPED when the requested list equals
        the current per-interface section set.
    """
    runner = _build_runner_with_conf(body=_PTP4L_CONF_BODY)
    mgr = PrecisionTimeManager(runner=runner)
    result = mgr.set_interfaces(interfaces=["eth0"])
    testbase.assert_equal(result.status, PtpActionStatus.SKIPPED)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 0)
    return


def test_set_interfaces_applies_per_interface_overrides():
    """
        set_interfaces honours caller-supplied per-interface overrides
        for newly-added sections.
    """
    runner = _build_runner_with_conf(body=_PTP4L_CONF_BODY)
    mgr = PrecisionTimeManager(runner=runner)
    overrides = {"ens5f0": {"logSyncInterval": "0"}}
    result = mgr.set_interfaces(
        interfaces=["eth0", "ens5f0"], per_interface=overrides,
    )
    testbase.assert_equal(result.status, PtpActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    payload = tee_calls[0]["stdin"]
    testbase.assert_true("[ens5f0]" in payload)
    testbase.assert_true("logSyncInterval 0" in payload)
    return


# ----------------------------------------------------------------------
# sysconfig OPTIONS
# ----------------------------------------------------------------------


def test_get_ptp4l_options_parses_value():
    """
        get_ptp4l_options strips quotes and returns the value.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/sysconfig/ptp4l")] = RunResult(
        exit_code=0,
        stdout='OPTIONS="-f /etc/linuxptp/ptp4l.conf -i eth0"\n',
        stderr="",
        command=["cat", "/etc/sysconfig/ptp4l"],
    )
    mgr = PrecisionTimeManager(runner=runner)
    value = mgr.get_ptp4l_options()
    testbase.assert_equal(value, "-f /etc/linuxptp/ptp4l.conf -i eth0")
    return


def test_set_ptp4l_options_skipped_when_unchanged():
    """
        set_ptp4l_options returns SKIPPED when the OPTIONS value is
        already correct.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/sysconfig/ptp4l")] = RunResult(
        exit_code=0, stdout='OPTIONS="-a -r"\n',
        stderr="", command=["cat", "/etc/sysconfig/ptp4l"],
    )
    mgr = PrecisionTimeManager(runner=runner)
    result = mgr.set_ptp4l_options(value="-a -r")
    testbase.assert_equal(result.status, PtpActionStatus.SKIPPED)
    return


def test_set_ptp4l_options_writes_and_attaches_restart_notice():
    """
        set_ptp4l_options writes via tee and surfaces the
        ``ptp4l.service restart required`` notice in detail.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/sysconfig/ptp4l")] = RunResult(
        exit_code=0, stdout="# header\n", stderr="",
        command=["cat", "/etc/sysconfig/ptp4l"],
    )
    mgr = PrecisionTimeManager(runner=runner)
    result = mgr.set_ptp4l_options(value="-a -r")
    testbase.assert_equal(result.status, PtpActionStatus.SUCCESS)
    testbase.assert_true(result.detail is not None)
    testbase.assert_true("ptp4l.service" in result.detail)
    testbase.assert_true("restart" in result.detail)
    tee_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["tee", "/etc/sysconfig/ptp4l"]
    ]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true('OPTIONS="-a -r"' in tee_calls[0]["stdin"])
    return


def test_set_phc2sys_options_writes_and_attaches_restart_notice():
    """
        set_phc2sys_options carries a phc2sys.service restart notice.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/sysconfig/phc2sys")] = RunResult(
        exit_code=0, stdout="", stderr="",
        command=["cat", "/etc/sysconfig/phc2sys"],
    )
    mgr = PrecisionTimeManager(runner=runner)
    result = mgr.set_phc2sys_options(value="-a -r")
    testbase.assert_equal(result.status, PtpActionStatus.SUCCESS)
    testbase.assert_true("phc2sys.service" in result.detail)
    return


# ----------------------------------------------------------------------
# pmc parsers
# ----------------------------------------------------------------------


def test_current_data_set_parses_pmc_output():
    """
        current_data_set extracts stepsRemoved / offset / mean delay.
    """
    runner = _MockRunner()
    runner.results[(
        "pmc", "-u", "-b", "0", "GET CURRENT_DATA_SET",
    )] = RunResult(
        exit_code=0, stdout=_PMC_CURRENT_DATA_SET, stderr="",
        command=["pmc"],
    )
    mgr = PrecisionTimeManager(runner=runner)
    snapshot = mgr.current_data_set()
    testbase.assert_equal(snapshot.steps_removed, 1)
    testbase.assert_equal(snapshot.offset_from_master_ns, 1234)
    testbase.assert_equal(snapshot.mean_path_delay_ns, 5678)
    return


def test_current_data_set_returns_stub_when_daemon_down():
    """
        current_data_set returns a stub record with steps_removed=-1
        and does NOT raise when ptp4l is not running.
    """
    runner = _MockRunner()
    runner.results[(
        "pmc", "-u", "-b", "0", "GET CURRENT_DATA_SET",
    )] = RunResult(
        exit_code=1, stdout="", stderr="failed to connect", command=["pmc"],
    )
    mgr = PrecisionTimeManager(runner=runner)
    snapshot = mgr.current_data_set()
    testbase.assert_equal(snapshot.steps_removed, -1)
    testbase.assert_true("failed to connect" in snapshot.raw)
    return


def test_parent_data_set_parses_pmc_output():
    """
        parent_data_set extracts identity strings and priorities.
    """
    runner = _MockRunner()
    runner.results[(
        "pmc", "-u", "-b", "0", "GET PARENT_DATA_SET",
    )] = RunResult(
        exit_code=0, stdout=_PMC_PARENT_DATA_SET, stderr="",
        command=["pmc"],
    )
    mgr = PrecisionTimeManager(runner=runner)
    snapshot = mgr.parent_data_set()
    testbase.assert_equal(
        snapshot.parent_port_identity, "507eaf.fffe.000000-1",
    )
    testbase.assert_equal(
        snapshot.grandmaster_identity, "507eaf.fffe.0000aa",
    )
    testbase.assert_equal(snapshot.grandmaster_priority_1, 128)
    testbase.assert_equal(snapshot.grandmaster_priority_2, 127)
    return


def test_port_data_sets_parses_two_port_blocks():
    """
        port_data_sets returns one PtpPortDataSet per port block.
    """
    runner = _MockRunner()
    runner.results[(
        "pmc", "-u", "-b", "0", "GET PORT_DATA_SET",
    )] = RunResult(
        exit_code=0, stdout=_PMC_PORT_DATA_SET, stderr="",
        command=["pmc"],
    )
    mgr = PrecisionTimeManager(runner=runner)
    ports = mgr.port_data_sets()
    testbase.assert_equal(len(ports), 2)
    testbase.assert_equal(ports[0].port_state, "SLAVE")
    testbase.assert_equal(ports[0].delay_mechanism, "E2E")
    testbase.assert_equal(ports[1].port_state, "MASTER")
    testbase.assert_equal(ports[1].delay_mechanism, "P2P")
    return


def test_port_data_sets_empty_when_daemon_down():
    """
        port_data_sets returns an empty list when pmc fails.
    """
    runner = _MockRunner()
    runner.results[(
        "pmc", "-u", "-b", "0", "GET PORT_DATA_SET",
    )] = RunResult(
        exit_code=1, stdout="", stderr="not running", command=["pmc"],
    )
    mgr = PrecisionTimeManager(runner=runner)
    ports = mgr.port_data_sets()
    testbase.assert_equal(len(ports), 0)
    return


# ----------------------------------------------------------------------
# Service-lifecycle delegation
# ----------------------------------------------------------------------


def test_start_ptp4l_delegates_to_service_manager():
    """
        start_ptp4l() calls service_manager.start("ptp4l") and never
        invokes systemctl directly through the runner.
    """
    runner = _MockRunner()
    sm = _MockServiceManager()
    mgr = PrecisionTimeManager(runner=runner, service_manager=sm)
    result = mgr.start_ptp4l()
    testbase.assert_equal(result.status, PtpActionStatus.SUCCESS)
    testbase.assert_equal(len(sm.calls), 1)
    testbase.assert_equal(sm.calls[0]["op"], "start")
    testbase.assert_equal(sm.calls[0]["service"], "ptp4l")
    # And the runner was never asked to spawn systemctl.
    systemctl_calls = [
        c for c in runner.calls if c["args"][0] == "systemctl"
    ]
    testbase.assert_equal(len(systemctl_calls), 0)
    return


def test_stop_enable_disable_delegate_to_service_manager():
    """
        stop_ptp4l / enable_ptp4l / disable_ptp4l each delegate to the
        injected service manager.
    """
    runner = _MockRunner()
    sm = _MockServiceManager()
    mgr = PrecisionTimeManager(runner=runner, service_manager=sm)
    mgr.stop_ptp4l()
    mgr.enable_ptp4l()
    mgr.disable_ptp4l()
    ops = [c["op"] for c in sm.calls]
    testbase.assert_true("stop" in ops)
    testbase.assert_true("enable" in ops)
    testbase.assert_true("disable" in ops)
    return


def test_start_phc2sys_blocks_when_ptp4l_inactive_and_disabled():
    """
        start_phc2sys returns FAILED with a descriptive detail when
        ptp4l.service is neither active nor enabled-on-boot.
    """
    runner = _MockRunner()
    sm = _MockServiceManager()
    sm.info_by_service["ptp4l"] = _SmInfo(
        active_state="inactive", enabled_state="disabled",
    )
    mgr = PrecisionTimeManager(runner=runner, service_manager=sm)
    result = mgr.start_phc2sys()
    testbase.assert_equal(result.status, PtpActionStatus.FAILED)
    testbase.assert_true("ptp4l" in result.detail)
    # Service manager must NOT have been asked to start phc2sys.
    start_calls = [
        c for c in sm.calls
        if c["op"] == "start" and c["service"] == "phc2sys"
    ]
    testbase.assert_equal(len(start_calls), 0)
    return


def test_start_phc2sys_delegates_when_ptp4l_active():
    """
        start_phc2sys delegates when ptp4l.service is active.
    """
    runner = _MockRunner()
    sm = _MockServiceManager()
    sm.info_by_service["ptp4l"] = _SmInfo(
        active_state="active", enabled_state="enabled",
    )
    mgr = PrecisionTimeManager(runner=runner, service_manager=sm)
    result = mgr.start_phc2sys()
    testbase.assert_equal(result.status, PtpActionStatus.SUCCESS)
    start_calls = [
        c for c in sm.calls
        if c["op"] == "start" and c["service"] == "phc2sys"
    ]
    testbase.assert_equal(len(start_calls), 1)
    return


def test_lifecycle_propagates_failed_status_from_service_manager():
    """
        When the service manager returns FAILED, the lifecycle verb
        propagates FAILED rather than SUCCESS.
    """
    runner = _MockRunner()
    sm = _MockServiceManager()
    sm.op_results[("start", "ptp4l")] = _SmResult(
        status=ActionStatus.FAILED, exit_code=5,
        stderr="unit not loaded",
    )
    mgr = PrecisionTimeManager(runner=runner, service_manager=sm)
    result = mgr.start_ptp4l()
    testbase.assert_equal(result.status, PtpActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 5)
    testbase.assert_equal(result.stderr, "unit not loaded")
    return


def test_lifecycle_without_service_manager_returns_failed():
    """
        Lifecycle verbs gracefully return FAILED with a descriptive
        detail when no ServiceManager was injected.
    """
    runner = _MockRunner()
    mgr = PrecisionTimeManager(runner=runner)
    result = mgr.start_ptp4l()
    testbase.assert_equal(result.status, PtpActionStatus.FAILED)
    testbase.assert_true("ServiceManager" in result.detail)
    return


# ----------------------------------------------------------------------
# Failure mapping for config writes
# ----------------------------------------------------------------------


def test_set_directive_failure_maps_to_failed_result():
    """
        When tee returns non-zero, set_directive surfaces FAILED with
        the captured exit code / stderr.
    """
    runner = _build_runner_with_conf(body=_PTP4L_CONF_BODY)
    runner.results[("tee", "/etc/linuxptp/ptp4l.conf")] = RunResult(
        exit_code=1, stdout="", stderr="permission denied",
        command=["tee", "/etc/linuxptp/ptp4l.conf"],
    )
    mgr = PrecisionTimeManager(runner=runner)
    result = mgr.set_directive(
        section="global", key="domainNumber", value="99",
    )
    testbase.assert_equal(result.status, PtpActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 1)
    testbase.assert_equal(result.stderr, "permission denied")
    return
