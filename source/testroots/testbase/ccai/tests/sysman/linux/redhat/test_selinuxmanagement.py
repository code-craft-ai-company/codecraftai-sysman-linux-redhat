# Tests for the SELinuxManager.

from dataclasses import dataclass, field
from typing import Callable, Optional

from ccai import testbase

from ccai.sysman.linux.redhat.runners.models import RunResult
from ccai.sysman.linux.redhat.selinuxmanagement import (
    SELinuxActionStatus,
    SELinuxManager,
    SELinuxManagerConfig,
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
        self.calls.append({"args": list(args)})
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


def test_query_context_parses_ls_output():
    """
        query_context() parses ``ls -Zd`` into a context record.
    """
    runner = _MockRunner()
    runner.results[("ls", "-Zd", "/etc/passwd")] = RunResult(
        exit_code=0,
        stdout="system_u:object_r:passwd_file_t:s0 /etc/passwd\n",
        stderr="",
        command=["ls"],
    )
    mgr = SELinuxManager(runner, SELinuxManagerConfig())
    ctx = mgr.query_context("/etc/passwd")
    testbase.assert_true(ctx is not None)
    testbase.assert_equal(ctx.user, "system_u")
    testbase.assert_equal(ctx.type, "passwd_file_t")
    return


def test_set_context_dispatches_chcon():
    """
        set_context() dispatches ``chcon -t type path``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["chcon"],
    ))
    mgr = SELinuxManager(runner, SELinuxManagerConfig(elevated=True))
    result = mgr.set_context("/srv/app", "httpd_sys_content_t", recursive=True)
    testbase.assert_equal(result.status, SELinuxActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"],
        ["chcon", "-R", "-t", "httpd_sys_content_t", "/srv/app"],
    )
    return


def test_set_boolean_skipped_when_current_matches():
    """
        set_boolean() returns SKIPPED when the current value matches.
    """
    runner = _MockRunner()
    runner.results[("getsebool", "httpd_can_network_connect")] = RunResult(
        exit_code=0,
        stdout="httpd_can_network_connect --> on\n",
        stderr="",
        command=["getsebool"],
    )
    mgr = SELinuxManager(runner, SELinuxManagerConfig())
    result = mgr.set_boolean("httpd_can_network_connect", True)
    testbase.assert_equal(result.status, SELinuxActionStatus.SKIPPED)
    return


def test_set_boolean_dispatches_setsebool():
    """
        set_boolean() dispatches ``setsebool -P name on|off``.
    """
    runner = _MockRunner()
    runner.results[("getsebool", "httpd_can_network_connect")] = RunResult(
        exit_code=0,
        stdout="httpd_can_network_connect --> off\n",
        stderr="",
        command=["getsebool"],
    )
    mgr = SELinuxManager(runner, SELinuxManagerConfig(elevated=True))
    result = mgr.set_boolean("httpd_can_network_connect", True, persistent=True)
    testbase.assert_equal(result.status, SELinuxActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"],
        ["setsebool", "-P", "httpd_can_network_connect", "on"],
    )
    return


def test_add_port_context_dispatches_semanage():
    """
        add_port_context() dispatches ``semanage port -a -t type``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["semanage"],
    ))
    mgr = SELinuxManager(runner, SELinuxManagerConfig(elevated=True))
    result = mgr.add_port_context(8443, "tcp", "http_port_t")
    testbase.assert_equal(result.status, SELinuxActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"],
        ["semanage", "port", "-a", "-t", "http_port_t", "-p", "tcp", "8443"],
    )
    return
