# Tests for CertificateTrustManager.

from dataclasses import dataclass, field
from typing import Optional

from automojo import testbase

from automojo.sysman.linux.redhat.certificatetrust import (
    CertificateTrustConfig,
    CertificateTrustManager,
    TrustActionStatus,
)
from automojo.sysman.linux.redhat.runners.models import RunResult


@dataclass
class _MockRunner:
    results: dict = field(default_factory=dict)
    default_result: Optional[RunResult] = None
    calls: list = field(default_factory=list)

    def run(self, args, *, elevated=False, cwd=None, env=None, timeout=None,
            on_output=None, on_error=None, capture_output=True, stdin=None):
        self.calls.append({"args": list(args), "stdin": stdin})
        key = tuple(args)
        if key in self.results:
            return self.results[key]
        if self.default_result is not None:
            return self.default_result
        return RunResult(exit_code=0, stdout="", stderr="", command=list(args))

    def add_aspect(self, aspect): return
    def remove_aspect(self, aspect): return
    def get_metrics(self): return None


def test_add_anchor_writes_and_refreshes():
    """
        add_anchor() writes the PEM via tee and runs update-ca-trust.
    """
    runner = _MockRunner()
    runner.results[(
        "cat", "/etc/pki/ca-trust/source/anchors/corp.pem",
    )] = RunResult(exit_code=1, stdout="", stderr="", command=["cat"])
    mgr = CertificateTrustManager(runner, CertificateTrustConfig(elevated=True))
    result = mgr.add_anchor("corp", "-----BEGIN CERTIFICATE-----\nPEM\n")
    testbase.assert_equal(result.status, TrustActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    refresh_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["update-ca-trust", "extract"]
    ]
    testbase.assert_equal(len(refresh_calls), 1)
    return


def test_add_anchor_skipped_when_content_matches():
    """
        add_anchor() returns SKIPPED when the file already has identical content.
    """
    body = "PEM\n"
    runner = _MockRunner()
    runner.results[(
        "cat", "/etc/pki/ca-trust/source/anchors/corp.pem",
    )] = RunResult(exit_code=0, stdout=body, stderr="", command=["cat"])
    mgr = CertificateTrustManager(runner, CertificateTrustConfig())
    result = mgr.add_anchor("corp", body)
    testbase.assert_equal(result.status, TrustActionStatus.SKIPPED)
    return


def test_refresh_dispatches_update_ca_trust():
    """
        refresh() dispatches ``update-ca-trust extract``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["update-ca-trust"],
    ))
    mgr = CertificateTrustManager(runner, CertificateTrustConfig(elevated=True))
    result = mgr.refresh()
    testbase.assert_equal(result.status, TrustActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"],
        ["update-ca-trust", "extract"],
    )
    return
