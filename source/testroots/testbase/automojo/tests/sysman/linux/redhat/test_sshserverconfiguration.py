# Tests for the SshServerConfigurator.

from dataclasses import dataclass, field
from typing import Callable, Optional

from automojo import testbase

from automojo.sysman.linux.redhat.runners.models import RunResult
from automojo.sysman.linux.redhat.sshserverconfiguration import (
    SshServerConfig,
    SshServerConfigurator,
    SshdActionStatus,
)


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


def test_get_effective_directive_parses_sshd_T():
    """
        get_effective_directive() parses ``sshd -T`` output into a map.
    """
    runner = _MockRunner()
    runner.results[("sshd", "-T")] = RunResult(
        exit_code=0,
        stdout="port 22\npermitrootlogin no\n",
        stderr="",
        command=["sshd"],
    )
    cfg = SshServerConfigurator(runner, SshServerConfig())
    testbase.assert_equal(cfg.get_effective_directive("Port"), "22")
    testbase.assert_equal(cfg.get_effective_directive("PermitRootLogin"), "no")
    return


def test_set_directive_dropin_writes_new_file():
    """
        set_directive_dropin() writes the drop-in via tee when empty.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/ssh/sshd_config.d/10-hardening.conf")] = RunResult(
        exit_code=1, stdout="", stderr="", command=["cat"],
    )
    cfg = SshServerConfigurator(runner, SshServerConfig(elevated=True))
    result = cfg.set_directive_dropin("10-hardening", "PermitRootLogin", "no")
    testbase.assert_equal(result.status, SshdActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true("PermitRootLogin no" in tee_calls[0]["stdin"])
    return


def test_set_directive_dropin_skipped_when_unchanged():
    """
        set_directive_dropin() returns SKIPPED when content matches.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/ssh/sshd_config.d/10-hardening.conf")] = RunResult(
        exit_code=0,
        stdout="PermitRootLogin no\n",
        stderr="",
        command=["cat"],
    )
    cfg = SshServerConfigurator(runner, SshServerConfig())
    result = cfg.set_directive_dropin("10-hardening", "PermitRootLogin", "no")
    testbase.assert_equal(result.status, SshdActionStatus.SKIPPED)
    return


def test_reload_config_fails_when_validation_fails():
    """
        reload_config() aborts with FAILED when sshd -t exits non-zero.
    """
    runner = _MockRunner()
    runner.results[("sshd", "-t")] = RunResult(
        exit_code=1, stdout="", stderr="bad config", command=["sshd"],
    )
    cfg = SshServerConfigurator(runner, SshServerConfig())
    result = cfg.reload_config()
    testbase.assert_equal(result.status, SshdActionStatus.FAILED)
    reload_calls = [c for c in runner.calls if c["args"][:3] == ["systemctl", "reload", "sshd"]]
    testbase.assert_equal(reload_calls, [])
    return


def test_reload_config_runs_systemctl_reload_when_valid():
    """
        reload_config() delegates to ServiceManager.reload("sshd")
        when validation passes.  ServiceManager first queries
        is-active/is-enabled then dispatches the reload; both end up
        going through the injected runner.  (Part C seam.)
    """
    runner = _MockRunner()
    runner.results[("sshd", "-t")] = RunResult(
        exit_code=0, stdout="", stderr="", command=["sshd"],
    )
    runner.results[("systemctl", "is-active", "sshd")] = RunResult(
        exit_code=0, stdout="active\n", stderr="", command=["systemctl"],
    )
    runner.results[("systemctl", "is-enabled", "sshd")] = RunResult(
        exit_code=0, stdout="enabled\n", stderr="", command=["systemctl"],
    )
    cfg = SshServerConfigurator(runner, SshServerConfig(elevated=True))
    result = cfg.reload_config()
    testbase.assert_equal(result.status, SshdActionStatus.SUCCESS)
    reload_calls = [
        c for c in runner.calls if c["args"][:3] == ["systemctl", "reload", "sshd"]
    ]
    testbase.assert_equal(len(reload_calls), 1)
    return
