# Tests for CockpitManager.

from dataclasses import dataclass, field
from typing import Optional

from ccai import testbase

from ccai.sysman.linux.redhat.cockpitmanagement import (
    CockpitActionStatus,
    CockpitManager,
    CockpitManagerConfig,
)
from ccai.sysman.linux.redhat.runners.models import RunResult


@dataclass
class _MockRunner:
    results: dict = field(default_factory=dict)
    default_result: Optional[RunResult] = None
    calls: list = field(default_factory=list)

    def run(self, args, *, elevated=False, cwd=None, env=None, timeout=None,
            on_output=None, on_error=None, capture_output=True, stdin=None):
        self.calls.append({"args": list(args)})
        key = tuple(args)
        if key in self.results:
            return self.results[key]
        if self.default_result is not None:
            return self.default_result
        return RunResult(exit_code=0, stdout="", stderr="", command=list(args))

    def add_aspect(self, aspect): return
    def remove_aspect(self, aspect): return
    def get_metrics(self): return None


def test_enable_delegates_to_service_manager():
    """
        enable() delegates to ServiceManager: it runs
        ``systemctl enable cockpit.socket`` then
        ``systemctl start cockpit.socket`` (Part C seam — replaces the
        previous single ``systemctl enable --now`` call).
    """
    runner = _MockRunner()
    # ServiceManager.enable() / .start() query is-active / is-enabled
    # first; seed responses so SKIPPED is not triggered.
    runner.results[("systemctl", "is-active", "cockpit.socket")] = RunResult(
        exit_code=0, stdout="inactive\n", stderr="", command=["systemctl"],
    )
    runner.results[("systemctl", "is-enabled", "cockpit.socket")] = RunResult(
        exit_code=0, stdout="disabled\n", stderr="", command=["systemctl"],
    )
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=["systemctl"],
    )
    mgr = CockpitManager(runner, CockpitManagerConfig(elevated=True))
    result = mgr.enable()
    testbase.assert_equal(result.status, CockpitActionStatus.SUCCESS)
    mutation_args = [
        c["args"] for c in runner.calls
        if c["args"][:2] in (["systemctl", "enable"], ["systemctl", "start"])
    ]
    testbase.assert_equal(
        mutation_args[0], ["systemctl", "enable", "cockpit.socket"],
    )
    testbase.assert_equal(
        mutation_args[1], ["systemctl", "start", "cockpit.socket"],
    )
    return


def test_is_active_delegates_to_service_manager_query():
    """
        is_active() delegates to ServiceManager.query() and checks
        ``active_state``.  Under the hood ServiceManager runs both
        ``systemctl is-active`` AND ``systemctl is-enabled``.
    """
    runner = _MockRunner()
    runner.results[("systemctl", "is-active", "cockpit.socket")] = RunResult(
        exit_code=0, stdout="active\n", stderr="", command=["systemctl"],
    )
    runner.results[("systemctl", "is-enabled", "cockpit.socket")] = RunResult(
        exit_code=0, stdout="enabled\n", stderr="", command=["systemctl"],
    )
    mgr = CockpitManager(runner, CockpitManagerConfig())
    testbase.assert_true(mgr.is_active())
    return


def test_open_firewall_delegates_to_firewall_manager():
    """
        open_firewall() delegates to FirewallManager.add_service, which
        queries the zone first then dispatches a firewall-cmd
        ``--add-service=cockpit --permanent`` invocation.  (Part C seam.)
    """
    runner = _MockRunner()
    # FirewallManager.add_service queries the zone and the default-zone
    # first; seed minimal responses so it's not SKIPPED.
    runner.results[("firewall-cmd", "--zone=public", "--list-all")] = RunResult(
        exit_code=0,
        stdout="public (active)\n  services: ssh\n  ports:\n",
        stderr="", command=["firewall-cmd"],
    )
    runner.results[("firewall-cmd", "--state")] = RunResult(
        exit_code=0, stdout="running\n", stderr="", command=["firewall-cmd"],
    )
    runner.results[("firewall-cmd", "--get-default-zone")] = RunResult(
        exit_code=0, stdout="public\n", stderr="", command=["firewall-cmd"],
    )
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=["firewall-cmd"],
    )
    mgr = CockpitManager(runner, CockpitManagerConfig(elevated=True))
    result = mgr.open_firewall()
    testbase.assert_equal(result.status, CockpitActionStatus.SUCCESS)
    mutation_calls = [
        c for c in runner.calls if "--add-service=cockpit" in c["args"]
    ]
    testbase.assert_equal(len(mutation_calls), 1)
    testbase.assert_true("--permanent" in mutation_calls[0]["args"])
    return
