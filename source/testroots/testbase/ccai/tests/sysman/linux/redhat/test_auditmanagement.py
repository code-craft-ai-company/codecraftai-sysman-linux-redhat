# Tests for the AuditManager.

from dataclasses import dataclass, field
from typing import Callable, Optional

from ccai import testbase

from ccai.sysman.linux.redhat.auditmanagement import (
    AuditActionStatus,
    AuditManager,
    AuditManagerConfig,
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


def test_list_rules_returns_auditctl_output():
    """
        list_rules() returns the lines from auditctl -l.
    """
    runner = _MockRunner()
    runner.results[("auditctl", "-l")] = RunResult(
        exit_code=0,
        stdout="-w /etc/passwd -p wa -k passwd_changes\n",
        stderr="",
        command=["auditctl"],
    )
    mgr = AuditManager(runner, AuditManagerConfig(elevated=True))
    rules = mgr.list_rules()
    testbase.assert_equal(len(rules), 1)
    testbase.assert_true("passwd_changes" in rules[0])
    return


def test_add_rule_dispatches_auditctl():
    """
        add_rule() splits the rule string and dispatches auditctl.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["auditctl"],
    ))
    mgr = AuditManager(runner, AuditManagerConfig(elevated=True))
    result = mgr.add_rule("-w /etc/passwd -p wa -k passwd_changes")
    testbase.assert_equal(result.status, AuditActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"],
        ["auditctl", "-w", "/etc/passwd", "-p", "wa", "-k", "passwd_changes"],
    )
    return


def test_write_rules_file_writes_via_tee():
    """
        write_rules_file() writes the drop-in via tee when content differs.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/audit/rules.d/custom.rules")] = RunResult(
        exit_code=1, stdout="", stderr="", command=["cat"],
    )
    mgr = AuditManager(runner, AuditManagerConfig(elevated=True))
    result = mgr.write_rules_file("custom", ["-w /etc/passwd -p wa -k pw"])
    testbase.assert_equal(result.status, AuditActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true("-w /etc/passwd" in tee_calls[0]["stdin"])
    return


def test_search_returns_lines():
    """
        search() returns the raw lines from ausearch.
    """
    runner = _MockRunner()
    runner.results[("ausearch", "-k", "passwd_changes")] = RunResult(
        exit_code=0, stdout="line1\nline2\n", stderr="", command=["ausearch"],
    )
    mgr = AuditManager(runner, AuditManagerConfig(elevated=True))
    report = mgr.search(key="passwd_changes")
    testbase.assert_equal(report.lines, ["line1", "line2"])
    return
