# Tests for the SecurityComplianceManager.

from dataclasses import dataclass, field
from typing import Callable, Optional

from automojo import testbase

from automojo.sysman.linux.redhat.runners.models import RunResult
from automojo.sysman.linux.redhat.securitycompliance import (
    FilePermissionCheck,
    SecurityComplianceConfig,
    SecurityComplianceManager,
    SELinuxMode,
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


def test_check_selinux_reports_enforcing():
    """
        check_selinux() parses getenforce + sestatus into SELinuxStatus.
    """
    runner = _MockRunner()
    runner.results[("getenforce",)] = RunResult(
        exit_code=0, stdout="Enforcing\n", stderr="", command=["getenforce"],
    )
    runner.results[("sestatus",)] = RunResult(
        exit_code=0,
        stdout="SELinux status: enabled\nLoaded policy name: targeted\n",
        stderr="",
        command=["sestatus"],
    )
    mgr = SecurityComplianceManager(runner, SecurityComplianceConfig())
    status = mgr.check_selinux()
    testbase.assert_equal(status.mode, SELinuxMode.ENFORCING)
    testbase.assert_equal(status.policy, "targeted")
    return


def test_check_file_permissions_detects_mismatch():
    """
        check_file_permissions() flags non-compliant files.
    """
    runner = _MockRunner()
    runner.results[("stat", "-c", "%a|%U|%G", "/etc/shadow")] = RunResult(
        exit_code=0, stdout="0|root|root\n", stderr="", command=["stat"],
    )
    mgr = SecurityComplianceManager(runner, SecurityComplianceConfig())
    results = mgr.check_file_permissions([FilePermissionCheck(
        path="/etc/shadow", expected_mode="600",
        expected_owner="root", expected_group="root",
    )])
    testbase.assert_equal(len(results), 1)
    testbase.assert_false(results[0].compliant)
    testbase.assert_equal(results[0].actual_mode, "0")
    return


def test_remediate_file_permissions_runs_chmod_and_chown():
    """
        remediate_file_permissions() runs chmod and chown for failing checks.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["chmod"],
    ))
    mgr = SecurityComplianceManager(runner, SecurityComplianceConfig(elevated=True))
    from automojo.sysman.linux.redhat.securitycompliance import FilePermissionResult
    failed = FilePermissionResult(
        path="/etc/shadow", compliant=False,
        expected_mode="600", expected_owner="root", expected_group="root",
        actual_mode="0644",
    )
    outcomes = mgr.remediate_file_permissions([failed])
    testbase.assert_equal(len(outcomes), 1)
    testbase.assert_true(outcomes[0].success)
    chmod_calls = [c for c in runner.calls if c["args"][0] == "chmod"]
    testbase.assert_equal(len(chmod_calls), 1)
    testbase.assert_equal(chmod_calls[0]["args"], ["chmod", "600", "/etc/shadow"])
    chown_calls = [c for c in runner.calls if c["args"][0] == "chown"]
    testbase.assert_equal(chown_calls[0]["args"], ["chown", "root:root", "/etc/shadow"])
    return


def test_check_ssh_config_flags_missing_setting():
    """
        check_ssh_config() flags absent settings as non-compliant.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/ssh/sshd_config")] = RunResult(
        exit_code=0,
        stdout="PasswordAuthentication no\n",
        stderr="",
        command=["cat"],
    )
    mgr = SecurityComplianceManager(runner, SecurityComplianceConfig())
    findings = mgr.check_ssh_config()
    setting_map = {f.setting: f for f in findings}
    testbase.assert_false(setting_map["PermitRootLogin"].compliant)
    testbase.assert_true(setting_map["PasswordAuthentication"].compliant)
    return


def test_apply_ssh_hardening_writes_and_restarts():
    """
        apply_ssh_hardening() patches sshd_config and restarts the service.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/ssh/sshd_config")] = RunResult(
        exit_code=0, stdout="PermitRootLogin yes\n", stderr="", command=["cat"],
    )
    mgr = SecurityComplianceManager(runner, SecurityComplianceConfig(elevated=True))
    result = mgr.apply_ssh_hardening({"PermitRootLogin": "no"})
    testbase.assert_true(result.success)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_true("PermitRootLogin no" in tee_calls[0]["stdin"])
    restart_calls = [c for c in runner.calls if c["args"][:3] == ["systemctl", "restart", "sshd"]]
    testbase.assert_equal(len(restart_calls), 1)
    return


def test_find_world_writable_files_parses_output():
    """
        find_world_writable_files() returns the reported file list.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0,
        stdout="/tmp/xxx\n/tmp/yyy\n",
        stderr="",
        command=["find"],
    ))
    mgr = SecurityComplianceManager(runner, SecurityComplianceConfig())
    files = mgr.find_world_writable_files(["/tmp"])
    testbase.assert_equal(files, ["/tmp/xxx", "/tmp/yyy"])
    return
