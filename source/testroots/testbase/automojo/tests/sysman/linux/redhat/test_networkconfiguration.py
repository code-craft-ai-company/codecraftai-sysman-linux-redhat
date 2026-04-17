# Tests for the NetworkConfigurator.

from dataclasses import dataclass, field
from typing import Callable, Optional

from automojo import testbase

from automojo.sysman.linux.redhat.networkconfiguration import (
    NetworkActionStatus,
    NetworkConfigurator,
    NetworkManagerConfig,
    NmConnectionSpec,
)
from automojo.sysman.linux.redhat.runners.models import RunResult


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
        self.calls.append({"args": list(args), "stdin": stdin})
        key = tuple(args)
        if key in self.results:
            return self.results[key]
        if self.default_result is not None:
            return self.default_result
        return RunResult(exit_code=0, stdout="", stderr="", command=list(args))

    def add_aspect(self, aspect) -> None:
        return

    def remove_aspect(self, aspect) -> None:
        return

    def get_metrics(self):
        return None


def test_set_hostname_skipped_when_matching():
    """
        set_hostname() returns SKIPPED when the current static hostname matches.
    """
    runner = _MockRunner()
    runner.results[("hostnamectl", "status")] = RunResult(
        exit_code=0,
        stdout="   Static hostname: host01\n Transient hostname: host01\n  Pretty hostname: host01\n",
        stderr="",
        command=["hostnamectl"],
    )
    nc = NetworkConfigurator(runner, NetworkManagerConfig())
    result = nc.set_hostname("host01")
    testbase.assert_equal(result.status, NetworkActionStatus.SKIPPED)
    return


def test_set_hostname_dispatches_hostnamectl():
    """
        set_hostname() dispatches ``hostnamectl set-hostname`` when different.
    """
    runner = _MockRunner()
    runner.results[("hostnamectl", "status")] = RunResult(
        exit_code=0, stdout="   Static hostname: old\n", stderr="",
        command=["hostnamectl"],
    )
    nc = NetworkConfigurator(runner, NetworkManagerConfig(elevated=True))
    result = nc.set_hostname("new-host")
    testbase.assert_equal(result.status, NetworkActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"], ["hostnamectl", "set-hostname", "new-host"],
    )
    return


def test_add_hosts_entry_writes_tee():
    """
        add_hosts_entry() writes hosts file via tee with the new entry.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/hosts")] = RunResult(
        exit_code=0, stdout="127.0.0.1 localhost\n", stderr="", command=["cat"],
    )
    nc = NetworkConfigurator(runner, NetworkManagerConfig(elevated=True))
    result = nc.add_hosts_entry("10.0.0.5", ["app.example.com", "app"])
    testbase.assert_equal(result.status, NetworkActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true("10.0.0.5" in tee_calls[0]["stdin"])
    testbase.assert_true("app.example.com" in tee_calls[0]["stdin"])
    return


def test_remove_hosts_entry_strips_line():
    """
        remove_hosts_entry() strips any line for the given IP.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/hosts")] = RunResult(
        exit_code=0,
        stdout="127.0.0.1 localhost\n10.0.0.5 app\n",
        stderr="",
        command=["cat"],
    )
    nc = NetworkConfigurator(runner, NetworkManagerConfig())
    result = nc.remove_hosts_entry("10.0.0.5")
    testbase.assert_equal(result.status, NetworkActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_false("10.0.0.5" in tee_calls[0]["stdin"])
    return


def test_set_nameservers_replaces_existing_lines():
    """
        set_nameservers() replaces all ``nameserver`` lines.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/resolv.conf")] = RunResult(
        exit_code=0,
        stdout="nameserver 8.8.8.8\nsearch example.com\n",
        stderr="",
        command=["cat"],
    )
    nc = NetworkConfigurator(runner, NetworkManagerConfig())
    result = nc.set_nameservers(["1.1.1.1", "1.0.0.1"])
    testbase.assert_equal(result.status, NetworkActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_true("nameserver 1.1.1.1" in tee_calls[0]["stdin"])
    testbase.assert_true("nameserver 1.0.0.1" in tee_calls[0]["stdin"])
    testbase.assert_false("8.8.8.8" in tee_calls[0]["stdin"])
    return


def test_add_ntp_server_appends_line():
    """
        add_ntp_server() appends a new ``server`` line.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/chrony.conf")] = RunResult(
        exit_code=0, stdout="pool 2.rhel.pool.ntp.org iburst\n", stderr="",
        command=["cat"],
    )
    nc = NetworkConfigurator(runner, NetworkManagerConfig(elevated=True))
    result = nc.add_ntp_server("10.0.0.1")
    testbase.assert_equal(result.status, NetworkActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_true("server 10.0.0.1 iburst" in tee_calls[0]["stdin"])
    return


def test_add_connection_dispatches_nmcli():
    """
        add_connection() dispatches ``nmcli connection add`` with config args.
    """
    runner = _MockRunner()
    runner.results[(
        "nmcli", "-t", "-f", "NAME,TYPE,DEVICE,STATE", "connection", "show",
    )] = RunResult(exit_code=0, stdout="", stderr="", command=["nmcli"])
    nc = NetworkConfigurator(runner, NetworkManagerConfig(elevated=True))
    result = nc.add_connection(NmConnectionSpec(
        name="wired",
        conn_type="ethernet",
        device="eth0",
        ip4="10.0.0.10/24",
        gw4="10.0.0.1",
        dns4=["1.1.1.1"],
    ))
    testbase.assert_equal(result.status, NetworkActionStatus.SUCCESS)
    add_call = runner.calls[-1]
    testbase.assert_true("nmcli" in add_call["args"])
    testbase.assert_true("connection.autoconnect" in add_call["args"])
    testbase.assert_true("ipv4.addresses" in add_call["args"])
    return
