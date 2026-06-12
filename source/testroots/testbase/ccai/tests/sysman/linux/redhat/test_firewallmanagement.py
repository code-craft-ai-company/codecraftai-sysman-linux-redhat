# Tests for the FirewallManager facade.

from dataclasses import dataclass, field
from typing import Callable, Optional

from ccai import testbase

from ccai.sysman.linux.redhat.firewallmanagement import (
    FirewallActionStatus,
    FirewallManager,
    FirewallManagerConfig,
)
from ccai.sysman.linux.redhat.runners.models import RunResult


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
        self.calls.append({"args": list(args), "elevated": elevated})
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


_PUBLIC_ZONE_OUTPUT = (
    "public (active)\n"
    "  target: default\n"
    "  interfaces: eth0\n"
    "  sources: \n"
    "  services: ssh dhcpv6-client\n"
    "  ports: 8080/tcp\n"
    "  rich rules: \n"
    '\trule family="ipv4" source address="10.0.0.0/8" accept\n'
)


def _state_runner(default_zone: str = "public") -> _MockRunner:
    """
        Build a runner stub with typical firewalld state responses.
    """
    runner = _MockRunner()
    runner.results[("firewall-cmd", "--state")] = RunResult(
        exit_code=0, stdout="running\n", stderr="", command=["firewall-cmd"],
    )
    runner.results[("firewall-cmd", "--get-default-zone")] = RunResult(
        exit_code=0, stdout=default_zone + "\n", stderr="", command=["firewall-cmd"],
    )
    runner.results[(
        "firewall-cmd", "--zone=public", "--list-all",
    )] = RunResult(
        exit_code=0, stdout=_PUBLIC_ZONE_OUTPUT, stderr="", command=["firewall-cmd"],
    )
    return runner


def test_query_state_reports_running_and_default():
    """
        query_state() reports running=True and the configured default zone.
    """
    runner = _state_runner()
    fm = FirewallManager(runner, FirewallManagerConfig())
    state = fm.query_state()
    testbase.assert_true(state.running)
    testbase.assert_equal(state.default_zone, "public")
    return


def test_query_zone_parses_list_all():
    """
        query_zone() parses firewall-cmd --list-all output.
    """
    runner = _state_runner()
    fm = FirewallManager(runner, FirewallManagerConfig())
    info = fm.query_zone("public")
    testbase.assert_true("ssh" in info.services)
    testbase.assert_true("8080/tcp" in info.ports)
    testbase.assert_equal(len(info.rich_rules), 1)
    testbase.assert_true(info.is_default)
    return


def test_add_service_skipped_when_present():
    """
        add_service() returns SKIPPED when the service is already in the zone.
    """
    runner = _state_runner()
    fm = FirewallManager(runner, FirewallManagerConfig())
    result = fm.add_service("ssh", "public")
    testbase.assert_equal(result.status, FirewallActionStatus.SKIPPED)
    return


def test_add_service_runs_firewall_cmd():
    """
        add_service() dispatches firewall-cmd --add-service with --permanent.
    """
    runner = _state_runner()
    fm = FirewallManager(runner, FirewallManagerConfig(elevated=True))
    result = fm.add_service("https", "public")
    testbase.assert_equal(result.status, FirewallActionStatus.SUCCESS)
    add_calls = [c for c in runner.calls if "--add-service=https" in c["args"]]
    testbase.assert_equal(len(add_calls), 1)
    testbase.assert_true("--permanent" in add_calls[0]["args"])
    testbase.assert_true(add_calls[0]["elevated"])
    return


def test_add_port_runs_firewall_cmd():
    """
        add_port() builds the port/proto argument correctly.
    """
    runner = _state_runner()
    fm = FirewallManager(runner, FirewallManagerConfig(elevated=True))
    result = fm.add_port(9090, "tcp", "public")
    testbase.assert_equal(result.status, FirewallActionStatus.SUCCESS)
    add_calls = [c for c in runner.calls if "--add-port=9090/tcp" in c["args"]]
    testbase.assert_equal(len(add_calls), 1)
    return


def test_set_default_zone_skipped_when_same():
    """
        set_default_zone() returns SKIPPED when already matching.
    """
    runner = _state_runner()
    fm = FirewallManager(runner, FirewallManagerConfig())
    result = fm.set_default_zone("public")
    testbase.assert_equal(result.status, FirewallActionStatus.SKIPPED)
    return


def test_reload_dispatches_command():
    """
        reload() dispatches ``firewall-cmd --reload``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["firewall-cmd"],
    ))
    fm = FirewallManager(runner, FirewallManagerConfig(elevated=True))
    result = fm.reload()
    testbase.assert_equal(result.status, FirewallActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"], ["firewall-cmd", "--reload"],
    )
    return
