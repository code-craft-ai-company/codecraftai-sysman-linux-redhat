# Tests for :class:`CertificateTrustManager`.
#
# All tests use a `_MockRunner` that records every ``run`` call and
# returns pre-seeded :class:`RunResult` values keyed by argv tuple.
# No live filesystem is touched; ``cat`` / ``ls`` / ``tee`` / ``rm`` /
# ``openssl`` / ``update-ca-trust`` are all faked.

import base64
import hashlib

from dataclasses import dataclass, field
from typing import Callable, Optional

from ccai import testbase

from ccai.sysman.linux.redhat.certificatetrustmanagement import (
    CertificateTrustActionResult,
    CertificateTrustManager,
    CertificateTrustManagerConfig,
    CertMetadata,
    CertTrustActionStatus,
    CertTrustPathNotFoundError,
    CertTrustQueryError,
    CertVerifyResult,
    TrustAnchor,
    TrustStoreState,
)
from ccai.sysman.linux.redhat.runners.models import RunResult


# ----------------------------------------------------------------------
# Test helpers
# ----------------------------------------------------------------------


@dataclass
class _MockRunner:
    """
        Minimal ``ICommandRunner`` stand-in that records every call and
        returns pre-seeded :class:`RunResult` values keyed by argv
        tuple.
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
        stdin: Optional[str] = None,
        timeout: Optional[float] = None,
        on_output: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        capture_output: bool = True,
    ) -> RunResult:
        call_record = {
            "args": list(args),
            "elevated": elevated,
            "stdin": stdin,
            "env": env,
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
            exit_code=0,
            stdout="",
            stderr="",
            command=list(args),
        )
        return rtnval

    def add_aspect(self, aspect) -> None:
        return

    def remove_aspect(self, aspect) -> None:
        return

    def get_metrics(self):
        return None


def _make_pem(*, der_bytes: bytes) -> str:
    """
        Wrap raw DER bytes into a PEM CERTIFICATE envelope.
    """
    encoded = base64.b64encode(der_bytes).decode("ascii")
    body_lines: list = []
    cursor = 0
    while cursor < len(encoded):
        body_lines.append(encoded[cursor:cursor + 64])
        cursor += 64
    rtnval_lines = ["-----BEGIN CERTIFICATE-----"]
    rtnval_lines.extend(body_lines)
    rtnval_lines.append("-----END CERTIFICATE-----")
    rtnval = "\n".join(rtnval_lines) + "\n"
    return rtnval


def _expected_fingerprint(*, der_bytes: bytes) -> str:
    """
        Compute the canonical uppercase-with-colons SHA-256 fingerprint
        for the supplied DER bytes.
    """
    digest = hashlib.sha256(der_bytes).hexdigest().upper()
    chunks: list = []
    cursor = 0
    while cursor < len(digest):
        chunks.append(digest[cursor:cursor + 2])
        cursor += 2
    rtnval = ":".join(chunks)
    return rtnval


_DER_FOO = b"DER-FOO-BYTES-1234567890"
_DER_BAR = b"DER-BAR-BYTES-ABCDEFGHIJ"

_ANCHORS_DIR = "/etc/pki/ca-trust/source/anchors"
_BLOCKLIST_DIR = "/etc/pki/ca-trust/source/blacklist"


def _foo_pem() -> str:
    rtnval = _make_pem(der_bytes=_DER_FOO)
    return rtnval


def _bar_pem() -> str:
    rtnval = _make_pem(der_bytes=_DER_BAR)
    return rtnval


def _foo_fp() -> str:
    rtnval = _expected_fingerprint(der_bytes=_DER_FOO)
    return rtnval


def _bar_fp() -> str:
    rtnval = _expected_fingerprint(der_bytes=_DER_BAR)
    return rtnval


def _seed_openssl_validate_ok(
    *,
    runner: _MockRunner,
    path: str,
) -> None:
    """
        Pre-seed the runner so that ``openssl x509 -noout -in <path>``
        returns success.
    """
    key = ("openssl", "x509", "-noout", "-in", path)
    runner.results[key] = RunResult(
        exit_code=0,
        stdout="",
        stderr="",
        command=list(key),
    )
    return


def _seed_openssl_anchor_text(
    *,
    runner: _MockRunner,
    path: str,
) -> None:
    """
        Pre-seed a canned response for the anchor metadata read.
    """
    canned_stdout = "\n".join([
        "subject=CN = Foo CA, O = Foo Inc",
        "issuer=CN = Foo Root, O = Foo Inc",
        "notBefore=Jan 01 00:00:00 2024 GMT",
        "notAfter=Jan 01 00:00:00 2034 GMT",
        "sha256 Fingerprint=AB:CD:EF",
    ])
    key = (
        "openssl", "x509", "-in", path, "-noout",
        "-subject", "-issuer", "-startdate", "-enddate",
        "-fingerprint", "-sha256",
    )
    runner.results[key] = RunResult(
        exit_code=0,
        stdout=canned_stdout,
        stderr="",
        command=list(key),
    )
    return


def _seed_openssl_metadata(
    *,
    runner: _MockRunner,
    path: str,
) -> None:
    """
        Pre-seed a canned response for the ``get_certificate_metadata``
        openssl invocation.
    """
    canned_stdout = "\n".join([
        "subject=CN = Foo CA, O = Foo Inc",
        "issuer=CN = Foo Root, O = Foo Inc",
        "serial=01:23:45",
        "notBefore=Jan 01 00:00:00 2024 GMT",
        "notAfter=Jan 01 00:00:00 2034 GMT",
        "sha256 Fingerprint=AB:CD:EF",
        "X509v3 Subject Alternative Name:",
        "    DNS:foo.example.com, DNS:bar.example.com",
    ])
    key = (
        "openssl", "x509", "-in", path, "-noout",
        "-subject", "-issuer", "-serial", "-startdate", "-enddate",
        "-fingerprint", "-sha256", "-ext", "subjectAltName",
    )
    runner.results[key] = RunResult(
        exit_code=0,
        stdout=canned_stdout,
        stderr="",
        command=list(key),
    )
    return


# ----------------------------------------------------------------------
# install_anchor
# ----------------------------------------------------------------------


def test_install_anchor_writes_via_tee():
    """
        install_anchor() validates the source PEM and writes the
        destination via ``tee``.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    runner.results[(
        "cat", "/tmp/foo.pem",
    )] = RunResult(
        exit_code=0, stdout=_foo_pem(), stderr="", command=["cat"],
    )
    runner.results[(
        "cat", "{}/foo.pem".format(_ANCHORS_DIR),
    )] = RunResult(
        exit_code=1, stdout="", stderr="", command=["cat"],
    )
    _seed_openssl_validate_ok(runner=runner, path="/tmp/foo.pem")

    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(elevated=True),
    )
    result = manager.install_anchor(
        name="foo", source_path="/tmp/foo.pem",
    )

    testbase.assert_equal(result.status, CertTrustActionStatus.SUCCESS)
    testbase.assert_equal(result.sha256_fingerprint, _foo_fp())
    testbase.assert_equal(
        result.path, "{}/foo.pem".format(_ANCHORS_DIR),
    )
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_equal(
        tee_calls[0]["args"],
        ["tee", "{}/foo.pem".format(_ANCHORS_DIR)],
    )
    testbase.assert_equal(tee_calls[0]["stdin"], _foo_pem())
    testbase.assert_equal(tee_calls[0]["elevated"], True)
    testbase.assert_true(result.note is not None)
    return


def test_install_anchor_skipped_when_identical_content():
    """
        install_anchor() returns SKIPPED when the destination file
        exists with byte-identical content.
    """
    body = _foo_pem()
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    runner.results[(
        "cat", "/tmp/foo.pem",
    )] = RunResult(exit_code=0, stdout=body, stderr="", command=["cat"])
    runner.results[(
        "cat", "{}/foo.pem".format(_ANCHORS_DIR),
    )] = RunResult(exit_code=0, stdout=body, stderr="", command=["cat"])
    _seed_openssl_validate_ok(runner=runner, path="/tmp/foo.pem")

    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(),
    )
    result = manager.install_anchor(
        name="foo", source_path="/tmp/foo.pem",
    )

    testbase.assert_equal(result.status, CertTrustActionStatus.SKIPPED)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(tee_calls, [])
    testbase.assert_equal(result.sha256_fingerprint, _foo_fp())
    return


def test_install_anchor_dry_run_short_circuits():
    """
        install_anchor() with dry_run=True returns DRY_RUN and never
        invokes ``tee``.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    runner.results[(
        "cat", "/tmp/foo.pem",
    )] = RunResult(
        exit_code=0, stdout=_foo_pem(), stderr="", command=["cat"],
    )
    runner.results[(
        "cat", "{}/foo.pem".format(_ANCHORS_DIR),
    )] = RunResult(exit_code=1, stdout="", stderr="", command=["cat"])
    _seed_openssl_validate_ok(runner=runner, path="/tmp/foo.pem")

    manager = CertificateTrustManager(
        runner,
        CertificateTrustManagerConfig(dry_run=True, elevated=True),
    )
    result = manager.install_anchor(
        name="foo", source_path="/tmp/foo.pem",
    )

    testbase.assert_equal(result.status, CertTrustActionStatus.DRY_RUN)
    testbase.assert_equal(
        result.command,
        ["tee", "{}/foo.pem".format(_ANCHORS_DIR)],
    )
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(tee_calls, [])
    return


def test_install_anchor_invalid_pem_returns_failed():
    """
        A source whose openssl validation fails returns FAILED and no
        ``tee`` is dispatched.
    """
    runner = _MockRunner()
    runner.results[(
        "cat", "/tmp/bad.pem",
    )] = RunResult(
        exit_code=0, stdout="not a cert", stderr="", command=["cat"],
    )
    runner.results[(
        "openssl", "x509", "-noout", "-in", "/tmp/bad.pem",
    )] = RunResult(
        exit_code=1, stdout="",
        stderr="unable to load certificate", command=["openssl"],
    )

    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(elevated=True),
    )
    result = manager.install_anchor(
        name="bad", source_path="/tmp/bad.pem",
    )

    testbase.assert_equal(result.status, CertTrustActionStatus.FAILED)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(tee_calls, [])
    testbase.assert_true(result.error is not None)
    return


def test_install_anchor_missing_source_returns_failed():
    """
        install_anchor() returns FAILED when the source file does not
        exist (cat returns non-zero) and never invokes openssl/tee.
    """
    runner = _MockRunner()
    runner.results[(
        "cat", "/tmp/missing.pem",
    )] = RunResult(
        exit_code=1, stdout="", stderr="No such file",
        command=["cat"],
    )

    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(elevated=True),
    )
    result = manager.install_anchor(
        name="missing", source_path="/tmp/missing.pem",
    )

    testbase.assert_equal(result.status, CertTrustActionStatus.FAILED)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(tee_calls, [])
    openssl_calls = [c for c in runner.calls if c["args"][0] == "openssl"]
    testbase.assert_equal(openssl_calls, [])
    return


def test_install_anchor_failed_tee_surfaces_stderr():
    """
        When ``tee`` exits non-zero the result is FAILED with stderr
        and exit_code populated.
    """
    runner = _MockRunner()
    runner.results[(
        "cat", "/tmp/foo.pem",
    )] = RunResult(
        exit_code=0, stdout=_foo_pem(), stderr="", command=["cat"],
    )
    runner.results[(
        "cat", "{}/foo.pem".format(_ANCHORS_DIR),
    )] = RunResult(exit_code=1, stdout="", stderr="", command=["cat"])
    _seed_openssl_validate_ok(runner=runner, path="/tmp/foo.pem")
    runner.results[(
        "tee", "{}/foo.pem".format(_ANCHORS_DIR),
    )] = RunResult(
        exit_code=1, stdout="",
        stderr="permission denied", command=["tee"],
    )

    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(elevated=True),
    )
    result = manager.install_anchor(
        name="foo", source_path="/tmp/foo.pem",
    )

    testbase.assert_equal(result.status, CertTrustActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 1)
    testbase.assert_equal(result.stderr, "permission denied")
    return


# ----------------------------------------------------------------------
# remove_anchor
# ----------------------------------------------------------------------


def test_remove_anchor_invokes_rm():
    """
        remove_anchor() dispatches ``rm -f`` against the destination
        path when the file exists.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    runner.results[(
        "cat", "{}/foo.pem".format(_ANCHORS_DIR),
    )] = RunResult(
        exit_code=0, stdout=_foo_pem(), stderr="", command=["cat"],
    )

    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(elevated=True),
    )
    result = manager.remove_anchor(name="foo")

    testbase.assert_equal(result.status, CertTrustActionStatus.SUCCESS)
    rm_calls = [c for c in runner.calls if c["args"][0] == "rm"]
    testbase.assert_equal(len(rm_calls), 1)
    testbase.assert_equal(
        rm_calls[0]["args"],
        ["rm", "-f", "{}/foo.pem".format(_ANCHORS_DIR)],
    )
    testbase.assert_equal(rm_calls[0]["elevated"], True)
    testbase.assert_true(result.note is not None)
    return


def test_remove_anchor_skipped_when_absent():
    """
        remove_anchor() returns SKIPPED when the destination file does
        not exist.
    """
    runner = _MockRunner()
    runner.results[(
        "cat", "{}/foo.pem".format(_ANCHORS_DIR),
    )] = RunResult(exit_code=1, stdout="", stderr="", command=["cat"])

    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(),
    )
    result = manager.remove_anchor(name="foo")

    testbase.assert_equal(result.status, CertTrustActionStatus.SKIPPED)
    rm_calls = [c for c in runner.calls if c["args"][0] == "rm"]
    testbase.assert_equal(rm_calls, [])
    return


def test_remove_anchor_dry_run_short_circuits():
    """
        remove_anchor() with dry_run=True returns DRY_RUN and never
        invokes ``rm``.
    """
    runner = _MockRunner()
    runner.results[(
        "cat", "{}/foo.pem".format(_ANCHORS_DIR),
    )] = RunResult(
        exit_code=0, stdout=_foo_pem(), stderr="", command=["cat"],
    )

    manager = CertificateTrustManager(
        runner,
        CertificateTrustManagerConfig(dry_run=True, elevated=True),
    )
    result = manager.remove_anchor(name="foo")

    testbase.assert_equal(result.status, CertTrustActionStatus.DRY_RUN)
    rm_calls = [c for c in runner.calls if c["args"][0] == "rm"]
    testbase.assert_equal(rm_calls, [])
    testbase.assert_equal(
        result.command,
        ["rm", "-f", "{}/foo.pem".format(_ANCHORS_DIR)],
    )
    return


# ----------------------------------------------------------------------
# Blocklist parity
# ----------------------------------------------------------------------


def test_install_blocklist_anchor_writes_to_blacklist_dir():
    """
        install_blocklist_anchor() writes under the configured blocklist
        directory.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    runner.results[(
        "cat", "/tmp/bar.pem",
    )] = RunResult(
        exit_code=0, stdout=_bar_pem(), stderr="", command=["cat"],
    )
    runner.results[(
        "cat", "{}/bar.pem".format(_BLOCKLIST_DIR),
    )] = RunResult(exit_code=1, stdout="", stderr="", command=["cat"])
    _seed_openssl_validate_ok(runner=runner, path="/tmp/bar.pem")

    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(elevated=True),
    )
    result = manager.install_blocklist_anchor(
        name="bar", source_path="/tmp/bar.pem",
    )

    testbase.assert_equal(result.status, CertTrustActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_equal(
        tee_calls[0]["args"],
        ["tee", "{}/bar.pem".format(_BLOCKLIST_DIR)],
    )
    return


def test_remove_blocklist_anchor_deletes_from_blacklist_dir():
    """
        remove_blocklist_anchor() dispatches ``rm -f`` against the
        blocklist directory.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    runner.results[(
        "cat", "{}/bar.pem".format(_BLOCKLIST_DIR),
    )] = RunResult(
        exit_code=0, stdout=_bar_pem(), stderr="", command=["cat"],
    )

    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(elevated=True),
    )
    result = manager.remove_blocklist_anchor(name="bar")

    testbase.assert_equal(result.status, CertTrustActionStatus.SUCCESS)
    rm_calls = [c for c in runner.calls if c["args"][0] == "rm"]
    testbase.assert_equal(len(rm_calls), 1)
    testbase.assert_equal(
        rm_calls[0]["args"],
        ["rm", "-f", "{}/bar.pem".format(_BLOCKLIST_DIR)],
    )
    return


# ----------------------------------------------------------------------
# refresh_trust_store
# ----------------------------------------------------------------------


def test_refresh_trust_store_invokes_update_ca_trust_extract():
    """
        refresh_trust_store() dispatches ``update-ca-trust extract``
        with elevated=True when configured.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["update-ca-trust"],
    ))
    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(elevated=True),
    )
    result = manager.refresh_trust_store()

    testbase.assert_equal(result.status, CertTrustActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"],
        ["update-ca-trust", "extract"],
    )
    testbase.assert_equal(runner.calls[-1]["elevated"], True)
    return


def test_refresh_trust_store_bare_when_extract_false():
    """
        refresh_trust_store(extract=False) dispatches the bare
        ``update-ca-trust`` form.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["update-ca-trust"],
    ))
    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(),
    )
    result = manager.refresh_trust_store(extract=False)

    testbase.assert_equal(result.status, CertTrustActionStatus.SUCCESS)
    testbase.assert_equal(runner.calls[-1]["args"], ["update-ca-trust"])
    return


def test_refresh_trust_store_dry_run_short_circuits():
    """
        refresh_trust_store() with dry_run=True returns DRY_RUN and
        never dispatches the runner.
    """
    runner = _MockRunner()
    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(dry_run=True),
    )
    result = manager.refresh_trust_store()

    testbase.assert_equal(result.status, CertTrustActionStatus.DRY_RUN)
    testbase.assert_equal(runner.calls, [])
    return


def test_refresh_trust_store_failure_surfaces_stderr():
    """
        A non-zero exit from ``update-ca-trust extract`` returns FAILED
        with the captured stderr.
    """
    runner = _MockRunner()
    runner.results[(
        "update-ca-trust", "extract",
    )] = RunResult(
        exit_code=2, stdout="",
        stderr="trust store corrupt",
        command=["update-ca-trust", "extract"],
    )
    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(elevated=True),
    )
    result = manager.refresh_trust_store()

    testbase.assert_equal(result.status, CertTrustActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 2)
    testbase.assert_equal(result.stderr, "trust store corrupt")
    return


# ----------------------------------------------------------------------
# enable_trust_store / disable_trust_store / query_trust_state
# ----------------------------------------------------------------------


def _seed_trust_check(
    *,
    runner: _MockRunner,
    enabled: bool,
) -> None:
    """
        Seed ``update-ca-trust check`` with an enabled or disabled
        response.
    """
    if enabled:
        text = "enabled\n"
    else:
        text = "DISABLED\n"
    runner.results[(
        "update-ca-trust", "check",
    )] = RunResult(
        exit_code=0, stdout=text, stderr="",
        command=["update-ca-trust", "check"],
    )
    return


def test_query_trust_state_returns_enabled_state():
    """
        query_trust_state() reports enabled=True when ``check`` does not
        emit ``DISABLED``.
    """
    runner = _MockRunner()
    _seed_trust_check(runner=runner, enabled=True)
    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(),
    )
    state = manager.query_trust_state()

    testbase.assert_equal(state.enabled, True)
    testbase.assert_true("enabled" in state.raw_status.lower())
    return


def test_query_trust_state_returns_disabled_state():
    """
        query_trust_state() reports enabled=False when ``check``
        contains ``DISABLED``.
    """
    runner = _MockRunner()
    _seed_trust_check(runner=runner, enabled=False)
    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(),
    )
    state = manager.query_trust_state()

    testbase.assert_equal(state.enabled, False)
    return


def test_query_trust_state_failure_raises():
    """
        query_trust_state() raises CertTrustQueryError when ``check``
        exits non-zero.
    """
    runner = _MockRunner()
    runner.results[(
        "update-ca-trust", "check",
    )] = RunResult(
        exit_code=1, stdout="", stderr="boom",
        command=["update-ca-trust", "check"],
    )
    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(),
    )

    raised = False
    try:
        manager.query_trust_state()
    except CertTrustQueryError:
        raised = True
    testbase.assert_equal(raised, True)
    return


def test_enable_trust_store_invokes_enable_when_disabled():
    """
        enable_trust_store() dispatches ``update-ca-trust enable`` when
        the store is currently disabled.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["update-ca-trust"],
    ))
    _seed_trust_check(runner=runner, enabled=False)

    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(elevated=True),
    )
    result = manager.enable_trust_store()

    testbase.assert_equal(result.status, CertTrustActionStatus.SUCCESS)
    enable_calls = [
        c for c in runner.calls
        if c["args"] == ["update-ca-trust", "enable"]
    ]
    testbase.assert_equal(len(enable_calls), 1)
    testbase.assert_equal(enable_calls[0]["elevated"], True)
    return


def test_enable_trust_store_skipped_when_already_enabled():
    """
        enable_trust_store() returns SKIPPED when the store is already
        enabled.
    """
    runner = _MockRunner()
    _seed_trust_check(runner=runner, enabled=True)
    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(elevated=True),
    )
    result = manager.enable_trust_store()

    testbase.assert_equal(result.status, CertTrustActionStatus.SKIPPED)
    enable_calls = [
        c for c in runner.calls
        if c["args"] == ["update-ca-trust", "enable"]
    ]
    testbase.assert_equal(enable_calls, [])
    return


def test_disable_trust_store_invokes_disable_when_enabled():
    """
        disable_trust_store() dispatches ``update-ca-trust disable``
        when the store is currently enabled.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["update-ca-trust"],
    ))
    _seed_trust_check(runner=runner, enabled=True)

    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(elevated=True),
    )
    result = manager.disable_trust_store()

    testbase.assert_equal(result.status, CertTrustActionStatus.SUCCESS)
    disable_calls = [
        c for c in runner.calls
        if c["args"] == ["update-ca-trust", "disable"]
    ]
    testbase.assert_equal(len(disable_calls), 1)
    testbase.assert_equal(disable_calls[0]["elevated"], True)
    return


def test_disable_trust_store_skipped_when_already_disabled():
    """
        disable_trust_store() returns SKIPPED when the store is
        already disabled.
    """
    runner = _MockRunner()
    _seed_trust_check(runner=runner, enabled=False)
    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(),
    )
    result = manager.disable_trust_store()

    testbase.assert_equal(result.status, CertTrustActionStatus.SKIPPED)
    disable_calls = [
        c for c in runner.calls
        if c["args"] == ["update-ca-trust", "disable"]
    ]
    testbase.assert_equal(disable_calls, [])
    return


# ----------------------------------------------------------------------
# list_anchors
# ----------------------------------------------------------------------


def test_list_anchors_parses_directory():
    """
        list_anchors() lists the anchors directory and produces one
        TrustAnchor per readable PEM file.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    runner.results[(
        "ls", "-1", _ANCHORS_DIR,
    )] = RunResult(
        exit_code=0, stdout="foo.pem\nbar.pem\n", stderr="",
        command=["ls"],
    )
    foo_path = "{}/foo.pem".format(_ANCHORS_DIR)
    bar_path = "{}/bar.pem".format(_ANCHORS_DIR)
    runner.results[("cat", foo_path)] = RunResult(
        exit_code=0, stdout=_foo_pem(), stderr="", command=["cat"],
    )
    runner.results[("cat", bar_path)] = RunResult(
        exit_code=0, stdout=_bar_pem(), stderr="", command=["cat"],
    )
    _seed_openssl_anchor_text(runner=runner, path=foo_path)
    _seed_openssl_anchor_text(runner=runner, path=bar_path)

    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(),
    )
    anchors = manager.list_anchors()

    testbase.assert_equal(len(anchors), 2)
    fingerprints = sorted([a.sha256_fingerprint for a in anchors])
    expected = sorted([_foo_fp(), _bar_fp()])
    testbase.assert_equal(fingerprints, expected)
    testbase.assert_equal(anchors[0].subject, "CN = Foo CA, O = Foo Inc")
    testbase.assert_true(anchors[0].valid)
    return


def test_list_anchors_marks_invalid_files():
    """
        list_anchors() returns a record with valid=False when a file
        does not contain a CERTIFICATE block.
    """
    runner = _MockRunner()
    runner.results[(
        "ls", "-1", _ANCHORS_DIR,
    )] = RunResult(
        exit_code=0, stdout="garbage.pem\n", stderr="",
        command=["ls"],
    )
    runner.results[(
        "cat", "{}/garbage.pem".format(_ANCHORS_DIR),
    )] = RunResult(
        exit_code=0, stdout="not a cert", stderr="",
        command=["cat"],
    )

    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(),
    )
    anchors = manager.list_anchors()

    testbase.assert_equal(len(anchors), 1)
    testbase.assert_equal(anchors[0].valid, False)
    testbase.assert_true(len(anchors[0].error) > 0)
    return


def test_list_anchors_empty_when_directory_missing():
    """
        list_anchors() returns an empty list when ``ls`` reports the
        directory does not exist.
    """
    runner = _MockRunner()
    runner.results[(
        "ls", "-1", _ANCHORS_DIR,
    )] = RunResult(
        exit_code=2, stdout="", stderr="No such file",
        command=["ls"],
    )
    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(),
    )
    anchors = manager.list_anchors()

    testbase.assert_equal(anchors, [])
    return


def test_list_anchors_does_not_elevate():
    """
        list_anchors() never passes elevated=True even when the config
        requests elevation.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    runner.results[(
        "ls", "-1", _ANCHORS_DIR,
    )] = RunResult(
        exit_code=0, stdout="foo.pem\n", stderr="", command=["ls"],
    )
    foo_path = "{}/foo.pem".format(_ANCHORS_DIR)
    runner.results[("cat", foo_path)] = RunResult(
        exit_code=0, stdout=_foo_pem(), stderr="", command=["cat"],
    )
    _seed_openssl_anchor_text(runner=runner, path=foo_path)

    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(elevated=True),
    )
    manager.list_anchors()

    for call in runner.calls:
        testbase.assert_equal(call["elevated"], False)
    return


# ----------------------------------------------------------------------
# verify_certificate
# ----------------------------------------------------------------------


def test_verify_certificate_returns_ok_on_success():
    """
        verify_certificate() returns ok=True on zero exit.
    """
    runner = _MockRunner()
    runner.results[(
        "ls", "-1", "/tmp/foo.pem",
    )] = RunResult(
        exit_code=0, stdout="/tmp/foo.pem\n", stderr="",
        command=["ls"],
    )
    runner.results[(
        "openssl", "verify", "/tmp/foo.pem",
    )] = RunResult(
        exit_code=0, stdout="/tmp/foo.pem: OK\n", stderr="",
        command=["openssl"],
    )
    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(),
    )
    result = manager.verify_certificate(cert_path="/tmp/foo.pem")

    testbase.assert_equal(result.ok, True)
    testbase.assert_true("OK" in result.output)
    return


def test_verify_certificate_returns_not_ok_on_failure():
    """
        verify_certificate() returns ok=False on non-zero exit and does
        not raise.
    """
    runner = _MockRunner()
    runner.results[(
        "ls", "-1", "/tmp/foo.pem",
    )] = RunResult(
        exit_code=0, stdout="/tmp/foo.pem\n", stderr="",
        command=["ls"],
    )
    runner.results[(
        "openssl", "verify", "/tmp/foo.pem",
    )] = RunResult(
        exit_code=2, stdout="",
        stderr="error: untrusted certificate\n",
        command=["openssl"],
    )
    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(),
    )
    result = manager.verify_certificate(cert_path="/tmp/foo.pem")

    testbase.assert_equal(result.ok, False)
    testbase.assert_true("untrusted" in result.output)
    return


def test_verify_certificate_passes_ca_file_and_untrusted():
    """
        verify_certificate() wires -CAfile and -untrusted into the
        argv when supplied.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    runner.results[(
        "ls", "-1", "/tmp/foo.pem",
    )] = RunResult(
        exit_code=0, stdout="/tmp/foo.pem\n", stderr="",
        command=["ls"],
    )
    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(),
    )
    manager.verify_certificate(
        cert_path="/tmp/foo.pem",
        ca_file="/tmp/ca.pem",
        untrusted="/tmp/chain.pem",
    )

    verify_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["openssl", "verify"]
    ]
    testbase.assert_equal(len(verify_calls), 1)
    args = verify_calls[0]["args"]
    testbase.assert_true("-CAfile" in args)
    testbase.assert_true("/tmp/ca.pem" in args)
    testbase.assert_true("-untrusted" in args)
    testbase.assert_true("/tmp/chain.pem" in args)
    testbase.assert_equal(verify_calls[0]["elevated"], False)
    return


def test_verify_certificate_missing_path_raises():
    """
        verify_certificate() raises CertTrustPathNotFoundError when the
        certificate file does not exist.
    """
    runner = _MockRunner()
    runner.results[(
        "ls", "-1", "/tmp/missing.pem",
    )] = RunResult(
        exit_code=2, stdout="", stderr="No such file",
        command=["ls"],
    )
    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(),
    )

    raised = False
    try:
        manager.verify_certificate(cert_path="/tmp/missing.pem")
    except CertTrustPathNotFoundError:
        raised = True
    testbase.assert_equal(raised, True)
    return


# ----------------------------------------------------------------------
# get_certificate_metadata
# ----------------------------------------------------------------------


def test_get_certificate_metadata_returns_populated_record():
    """
        get_certificate_metadata() returns a CertMetadata populated
        from the canned openssl output.
    """
    runner = _MockRunner()
    runner.results[(
        "ls", "-1", "/tmp/foo.pem",
    )] = RunResult(
        exit_code=0, stdout="/tmp/foo.pem\n", stderr="",
        command=["ls"],
    )
    _seed_openssl_metadata(runner=runner, path="/tmp/foo.pem")

    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(),
    )
    meta = manager.get_certificate_metadata(cert_path="/tmp/foo.pem")

    testbase.assert_equal(meta.subject, "CN = Foo CA, O = Foo Inc")
    testbase.assert_equal(meta.serial, "01:23:45")
    testbase.assert_equal(meta.sha256_fingerprint, "AB:CD:EF")
    testbase.assert_true("DNS:foo.example.com" in meta.sans)
    testbase.assert_true("DNS:bar.example.com" in meta.sans)
    return


def test_get_certificate_metadata_missing_path_raises():
    """
        get_certificate_metadata() raises CertTrustPathNotFoundError
        when the file does not exist.
    """
    runner = _MockRunner()
    runner.results[(
        "ls", "-1", "/tmp/missing.pem",
    )] = RunResult(
        exit_code=2, stdout="", stderr="",
        command=["ls"],
    )
    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(),
    )

    raised = False
    try:
        manager.get_certificate_metadata(cert_path="/tmp/missing.pem")
    except CertTrustPathNotFoundError:
        raised = True
    testbase.assert_equal(raised, True)
    return


def test_get_certificate_metadata_invalid_cert_raises_query_error():
    """
        get_certificate_metadata() raises CertTrustQueryError when
        ``openssl x509`` exits non-zero.
    """
    runner = _MockRunner()
    runner.results[(
        "ls", "-1", "/tmp/bad.pem",
    )] = RunResult(
        exit_code=0, stdout="/tmp/bad.pem\n", stderr="",
        command=["ls"],
    )
    failing_key = (
        "openssl", "x509", "-in", "/tmp/bad.pem", "-noout",
        "-subject", "-issuer", "-serial", "-startdate", "-enddate",
        "-fingerprint", "-sha256", "-ext", "subjectAltName",
    )
    runner.results[failing_key] = RunResult(
        exit_code=1, stdout="",
        stderr="unable to load certificate",
        command=list(failing_key),
    )
    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(),
    )

    raised = False
    try:
        manager.get_certificate_metadata(cert_path="/tmp/bad.pem")
    except CertTrustQueryError:
        raised = True
    testbase.assert_equal(raised, True)
    return


# ----------------------------------------------------------------------
# Cross-cutting: elevation policy
# ----------------------------------------------------------------------


def test_writes_pass_elevated_true_when_configured():
    """
        With elevated=True every mutating runner call carries
        elevated=True.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    runner.results[(
        "cat", "/tmp/foo.pem",
    )] = RunResult(
        exit_code=0, stdout=_foo_pem(), stderr="", command=["cat"],
    )
    runner.results[(
        "cat", "{}/foo.pem".format(_ANCHORS_DIR),
    )] = RunResult(exit_code=1, stdout="", stderr="", command=["cat"])
    _seed_openssl_validate_ok(runner=runner, path="/tmp/foo.pem")

    manager = CertificateTrustManager(
        runner, CertificateTrustManagerConfig(elevated=True),
    )
    manager.install_anchor(name="foo", source_path="/tmp/foo.pem")

    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_equal(tee_calls[0]["elevated"], True)

    # Reads (cat / openssl) MUST not elevate.
    for call in runner.calls:
        if call["args"][0] in ("cat", "openssl", "ls"):
            testbase.assert_equal(call["elevated"], False)
    return
