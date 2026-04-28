# Tests for the CertificateIssuanceManager facade.
#
# All tests use a `_MockRunner` that records every `run` call and
# returns pre-seeded :class:`RunResult` values keyed by argv tuple.
# No live filesystem is touched; every `cat` / `tee` / `rm` /
# `chmod` / `test` / `openssl` / `getcert` / `certbot` invocation
# is faked.

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from automojo import testbase

from automojo.sysman.linux.redhat.certificateissuance import (
    AcmeIssuanceSpec,
    AcmeRequiredArgumentError,
    CertificateActionStatus,
    CertificateCredentialError,
    CertificateInfo,
    CertificateIssuanceConfig,
    CertificateIssuanceManager,
    CertmongerTracking,
    CsrInfo,
    CsrSpec,
    SubjectSpec,
    _parse_getcert_list,
    _parse_openssl_x509_text,
    _parse_openssl_req_text,
    _parse_fingerprint_line,
    _render_openssl_config,
)
from automojo.sysman.linux.redhat.runners.models import RunResult


# ----------------------------------------------------------------------
# _MockRunner
# ----------------------------------------------------------------------


@dataclass
class _MockRunner:
    """
        Minimal command-runner stand-in.  Records every ``run`` call
        and returns pre-seeded :class:`RunResult` values keyed by
        argv tuple.
    """

    results: dict = field(default_factory=dict)
    default_result: Optional[RunResult] = None
    calls: list = field(default_factory=list)
    test_path_existence: dict = field(default_factory=dict)

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
        self.calls.append({
            "args": list(args),
            "elevated": elevated,
            "stdin": stdin,
        })

        # `test -e <path>` shortcut: respect the per-path map first.
        if len(args) == 3 and args[0] == "test" and args[1] == "-e":
            target = args[2]
            if target in self.test_path_existence:
                if self.test_path_existence[target]:
                    rtnval = RunResult(
                        exit_code=0, stdout="", stderr="",
                        command=list(args),
                    )
                    return rtnval
                rtnval = RunResult(
                    exit_code=1, stdout="", stderr="",
                    command=list(args),
                )
                return rtnval

        key = tuple(args)
        if key in self.results:
            rtnval = self.results[key]
            return rtnval

        if self.default_result is not None:
            rtnval = self.default_result
            return rtnval

        rtnval = RunResult(
            exit_code=0, stdout="", stderr="",
            command=list(args),
        )
        return rtnval

    def add_aspect(self, aspect) -> None:
        return

    def remove_aspect(self, aspect) -> None:
        return

    def get_metrics(self):
        return None


# ----------------------------------------------------------------------
# Test helpers
# ----------------------------------------------------------------------


@dataclass
class _MockCredential:
    """
        Minimal credential stand-in.  Exposes ``secret_bytes()`` per
        the `automojo-credentials` shape.
    """

    payload: bytes

    def secret_bytes(self) -> bytes:
        return self.payload


def _ok(stdout: str = "") -> RunResult:
    rtnval = RunResult(
        exit_code=0, stdout=stdout, stderr="",
        command=[],
    )
    return rtnval


def _fail(stderr: str = "boom", exit_code: int = 1) -> RunResult:
    rtnval = RunResult(
        exit_code=exit_code, stdout="", stderr=stderr,
        command=[],
    )
    return rtnval


def _basic_subject() -> SubjectSpec:
    rtnval = SubjectSpec(
        common_name="foo.example.com",
        country="US",
        organization="Example Inc",
    )
    return rtnval


def _basic_csr_spec() -> CsrSpec:
    rtnval = CsrSpec(
        key_path="/etc/pki/tls/private/foo.key",
        csr_path="/etc/pki/tls/certs/foo.csr",
        subject=_basic_subject(),
        sans=["foo.example.com", "bar.example.com"],
    )
    return rtnval


def _far_future() -> str:
    """
        notAfter ~ 10 years out so the renew threshold guard skips.
    """
    target = datetime.now(tz=timezone.utc) + timedelta(days=3650)
    rtnval = target.strftime("%b %d %H:%M:%S %Y GMT")
    return rtnval


def _near_expiry() -> str:
    """
        notAfter ~ 5 days out so the renew threshold guard does NOT
        skip.
    """
    target = datetime.now(tz=timezone.utc) + timedelta(days=5)
    rtnval = target.strftime("%b %d %H:%M:%S %Y GMT")
    return rtnval


def _x509_text_with_dates(*, not_after: str) -> str:
    """
        Build a canned ``openssl x509 -noout -text`` body covering
        every field the manager parses.
    """
    body = "\n".join([
        "Certificate:",
        "    Data:",
        "        Version: 3 (0x2)",
        "        Serial Number: 1234",
        "        Signature Algorithm: sha256WithRSAEncryption",
        "        Issuer: CN=Foo CA",
        "        Validity",
        "            Not Before: Jan  1 00:00:00 2024 GMT",
        "            Not After : {}".format(not_after),
        "        Subject: CN=foo.example.com",
        "        Subject Public Key Info:",
        "            Public Key Algorithm: rsaEncryption",
        "                Public-Key: (3072 bit)",
        "        X509v3 extensions:",
        "            X509v3 Subject Alternative Name:",
        "                DNS:foo.example.com, DNS:bar.example.com",
        "            X509v3 Key Usage:",
        "                Digital Signature, Key Encipherment",
        "            X509v3 Extended Key Usage:",
        "                TLS Web Server Authentication",
        "    Signature Algorithm: sha256WithRSAEncryption",
    ])
    return body


def _seed_inspect_certificate(
    *,
    runner: _MockRunner,
    path: str,
    not_after: str,
    fingerprint: str = "AB:CD:EF",
) -> None:
    """
        Pre-seed the runner with canned ``openssl x509`` outputs for
        ``inspect_certificate``.
    """
    text_key = ("openssl", "x509", "-in", path, "-noout", "-text")
    runner.results[text_key] = _ok(
        stdout=_x509_text_with_dates(not_after=not_after),
    )
    fp_key = (
        "openssl", "x509", "-in", path, "-noout",
        "-fingerprint", "-sha256",
    )
    runner.results[fp_key] = _ok(
        stdout="sha256 Fingerprint={}\n".format(fingerprint),
    )
    return


# ----------------------------------------------------------------------
# generate_key
# ----------------------------------------------------------------------


def test_generate_key_rsa_dispatches_openssl_genpkey():
    """
        ``generate_key`` builds the ``openssl genpkey -algorithm RSA``
        command, dispatches it, and chmods 0600 post-write.
    """
    runner = _MockRunner()
    runner.test_path_existence["/etc/pki/tls/private/foo.key"] = False
    cfg = CertificateIssuanceConfig(elevated=True)
    mgr = CertificateIssuanceManager(runner, cfg)

    result = mgr.generate_key(
        path="/etc/pki/tls/private/foo.key",
        algorithm="RSA",
        bits_or_curve="2048",
    )

    testbase.assert_equal(result.status, CertificateActionStatus.SUCCESS)
    testbase.assert_equal(result.path, "/etc/pki/tls/private/foo.key")

    genpkey_calls = [c for c in runner.calls if "genpkey" in c["args"]]
    testbase.assert_equal(len(genpkey_calls), 1)
    testbase.assert_true("-algorithm" in genpkey_calls[0]["args"])
    testbase.assert_true("RSA" in genpkey_calls[0]["args"])
    testbase.assert_true("rsa_keygen_bits:2048" in genpkey_calls[0]["args"])
    testbase.assert_equal(genpkey_calls[0]["elevated"], True)

    chmod_calls = [c for c in runner.calls if c["args"][0] == "chmod"]
    testbase.assert_equal(len(chmod_calls), 1)
    testbase.assert_equal(chmod_calls[0]["args"][1], "600")
    return


def test_generate_key_ec_uses_curve():
    """
        ``generate_key`` with ``algorithm='EC'`` routes through the
        ``ec_paramgen_curve`` pkeyopt.
    """
    runner = _MockRunner()
    runner.test_path_existence["/srv/k.pem"] = False
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())

    result = mgr.generate_key(
        path="/srv/k.pem",
        algorithm="EC",
        bits_or_curve="P-384",
    )

    testbase.assert_equal(result.status, CertificateActionStatus.SUCCESS)
    genpkey_calls = [c for c in runner.calls if "genpkey" in c["args"]]
    testbase.assert_equal(len(genpkey_calls), 1)
    testbase.assert_true("EC" in genpkey_calls[0]["args"])
    testbase.assert_true("ec_paramgen_curve:P-384" in genpkey_calls[0]["args"])
    return


def test_generate_key_skipped_when_exists():
    """
        ``generate_key`` returns SKIPPED when the file already exists
        and overwrite=False.
    """
    runner = _MockRunner()
    runner.test_path_existence["/srv/k.pem"] = True
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())

    result = mgr.generate_key(
        path="/srv/k.pem",
        algorithm="RSA",
        bits_or_curve="3072",
    )

    testbase.assert_equal(result.status, CertificateActionStatus.SKIPPED)
    genpkey_calls = [c for c in runner.calls if "genpkey" in c["args"]]
    testbase.assert_equal(len(genpkey_calls), 0)
    return


def test_generate_key_dry_run_does_not_dispatch():
    """
        ``generate_key`` returns DRY_RUN and does NOT dispatch
        ``openssl genpkey`` under dry-run.
    """
    runner = _MockRunner()
    runner.test_path_existence["/srv/k.pem"] = False
    mgr = CertificateIssuanceManager(
        runner, CertificateIssuanceConfig(dry_run=True),
    )

    result = mgr.generate_key(
        path="/srv/k.pem",
        algorithm="RSA",
        bits_or_curve="3072",
    )

    testbase.assert_equal(result.status, CertificateActionStatus.DRY_RUN)
    genpkey_calls = [c for c in runner.calls if "genpkey" in c["args"]]
    testbase.assert_equal(len(genpkey_calls), 0)
    chmod_calls = [c for c in runner.calls if c["args"][0] == "chmod"]
    testbase.assert_equal(len(chmod_calls), 0)
    return


def test_generate_key_passphrase_routes_via_stdin_not_argv():
    """
        Passphrase-encrypted key generation routes the secret bytes
        on stdin and never lets them onto the recorded argv.
    """
    runner = _MockRunner()
    runner.test_path_existence["/srv/k.pem"] = False
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())

    cred = _MockCredential(payload=b"sup3r-secret-pass")
    result = mgr.generate_key(
        path="/srv/k.pem",
        algorithm="RSA",
        bits_or_curve="3072",
        credential=cred,
    )

    testbase.assert_equal(result.status, CertificateActionStatus.SUCCESS)

    genpkey_calls = [c for c in runner.calls if "genpkey" in c["args"]]
    testbase.assert_equal(len(genpkey_calls), 1)
    call = genpkey_calls[0]

    # Passphrase MUST NOT be on argv.
    for token in call["args"]:
        testbase.assert_true("sup3r-secret-pass" not in token)
    # Passphrase MUST NOT be on the recorded result command.
    for token in result.command:
        testbase.assert_true("sup3r-secret-pass" not in token)
    # Passphrase MUST be on stdin.
    testbase.assert_equal(call["stdin"], "sup3r-secret-pass")
    # Sanitised argv carries -aes256 and -pass stdin sentinels.
    testbase.assert_true("-aes256" in call["args"])
    testbase.assert_true("-pass" in call["args"])
    testbase.assert_true("stdin" in call["args"])
    return


def test_generate_key_unsupported_algorithm_returns_failed():
    """
        An unsupported algorithm name returns FAILED without
        dispatching.
    """
    runner = _MockRunner()
    runner.test_path_existence["/srv/k.pem"] = False
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())
    result = mgr.generate_key(
        path="/srv/k.pem",
        algorithm="DSA",
        bits_or_curve="3072",
    )
    testbase.assert_equal(result.status, CertificateActionStatus.FAILED)
    genpkey_calls = [c for c in runner.calls if "genpkey" in c["args"]]
    testbase.assert_equal(len(genpkey_calls), 0)
    return


# ----------------------------------------------------------------------
# generate_csr
# ----------------------------------------------------------------------


def test_generate_csr_writes_temp_config_and_cleans_up():
    """
        ``generate_csr`` writes a temp openssl config via tee, runs
        ``openssl req -new -config <tmp>`` and removes the temp config
        in a finally block — even on success.
    """
    runner = _MockRunner()
    runner.test_path_existence["/etc/pki/tls/certs/foo.csr"] = False
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())

    spec = _basic_csr_spec()
    result = mgr.generate_csr(spec=spec)

    testbase.assert_equal(result.status, CertificateActionStatus.SUCCESS)
    testbase.assert_equal(result.path, "/etc/pki/tls/certs/foo.csr")

    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    config_payload = tee_calls[0]["stdin"]
    testbase.assert_true("[req]" in config_payload)
    testbase.assert_true("CN = foo.example.com" in config_payload)
    testbase.assert_true("DNS.1 = foo.example.com" in config_payload)

    req_calls = [
        c for c in runner.calls
        if c["args"][0] == "openssl" and "req" in c["args"]
    ]
    testbase.assert_equal(len(req_calls), 1)
    testbase.assert_true("-new" in req_calls[0]["args"])
    testbase.assert_true("-x509" not in req_calls[0]["args"])

    rm_calls = [c for c in runner.calls if c["args"][0] == "rm"]
    testbase.assert_equal(len(rm_calls), 1)
    return


def test_generate_csr_cleans_up_temp_config_on_failure():
    """
        ``generate_csr`` removes the temp openssl config in the
        finally block even when ``openssl req`` returns non-zero.
    """
    runner = _MockRunner()
    runner.test_path_existence["/etc/pki/tls/certs/foo.csr"] = False
    spec = _basic_csr_spec()

    # Default-success seed for everything except the openssl req
    # invocation, which we make fail.
    runner.default_result = _ok()

    def _seed_req_failure() -> None:
        # We don't know the temp path up front; intercept any openssl
        # req call by relying on the fact that there is exactly one
        # such argv shape in the call list.
        return

    _seed_req_failure()

    # Replace runner.run wrapper to fail the openssl req call.
    real_run = runner.run

    def _patched_run(args, **kwargs):
        recorded = real_run(args, **kwargs)
        if (
            len(args) >= 2
            and args[0] == "openssl"
            and args[1] == "req"
            and "-new" in args
        ):
            failed = RunResult(
                exit_code=2, stdout="", stderr="bad config",
                command=list(args),
            )
            return failed
        return recorded

    runner.run = _patched_run  # type: ignore[assignment]

    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())
    result = mgr.generate_csr(spec=spec)

    testbase.assert_equal(result.status, CertificateActionStatus.FAILED)
    rm_calls = [c for c in runner.calls if c["args"][0] == "rm"]
    testbase.assert_equal(len(rm_calls), 1)
    return


def test_generate_csr_passphrase_uses_passin_stdin():
    """
        ``generate_csr`` with a credential adds ``-passin stdin`` and
        forwards the secret on stdin, not argv.
    """
    runner = _MockRunner()
    runner.test_path_existence["/etc/pki/tls/certs/foo.csr"] = False
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())

    cred = _MockCredential(payload=b"sup3r-secret-pass")
    result = mgr.generate_csr(spec=_basic_csr_spec(), credential=cred)

    testbase.assert_equal(result.status, CertificateActionStatus.SUCCESS)

    req_calls = [
        c for c in runner.calls
        if c["args"][0] == "openssl" and "req" in c["args"]
    ]
    testbase.assert_equal(len(req_calls), 1)
    testbase.assert_true("-passin" in req_calls[0]["args"])
    testbase.assert_true("stdin" in req_calls[0]["args"])
    testbase.assert_equal(req_calls[0]["stdin"], "sup3r-secret-pass")
    for token in req_calls[0]["args"]:
        testbase.assert_true("sup3r-secret-pass" not in token)
    for token in result.command:
        testbase.assert_true("sup3r-secret-pass" not in token)
    return


def test_generate_csr_skipped_when_exists():
    """
        ``generate_csr`` returns SKIPPED when the destination CSR
        already exists.
    """
    runner = _MockRunner()
    runner.test_path_existence["/etc/pki/tls/certs/foo.csr"] = True
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())

    result = mgr.generate_csr(spec=_basic_csr_spec())
    testbase.assert_equal(result.status, CertificateActionStatus.SKIPPED)
    req_calls = [
        c for c in runner.calls
        if c["args"][0] == "openssl" and "req" in c["args"]
    ]
    testbase.assert_equal(len(req_calls), 0)
    return


def test_generate_csr_dry_run_does_not_write_or_dispatch():
    """
        ``generate_csr`` under dry-run returns DRY_RUN, never invokes
        ``tee`` / ``openssl req`` / ``rm``.
    """
    runner = _MockRunner()
    runner.test_path_existence["/etc/pki/tls/certs/foo.csr"] = False
    mgr = CertificateIssuanceManager(
        runner, CertificateIssuanceConfig(dry_run=True),
    )
    result = mgr.generate_csr(spec=_basic_csr_spec())
    testbase.assert_equal(result.status, CertificateActionStatus.DRY_RUN)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 0)
    rm_calls = [c for c in runner.calls if c["args"][0] == "rm"]
    testbase.assert_equal(len(rm_calls), 0)
    req_calls = [
        c for c in runner.calls
        if c["args"][0] == "openssl" and "req" in c["args"]
    ]
    testbase.assert_equal(len(req_calls), 0)
    return


# ----------------------------------------------------------------------
# inspect_csr
# ----------------------------------------------------------------------


def test_inspect_csr_parses_subject_and_sans():
    """
        ``inspect_csr`` parses subject and SANs from
        ``openssl req -noout -text`` output.
    """
    runner = _MockRunner()
    canned = "\n".join([
        "Certificate Request:",
        "    Data:",
        "        Subject: CN=foo.example.com, O=Example",
        "        Subject Public Key Info:",
        "            Public Key Algorithm: rsaEncryption",
        "        Requested Extensions:",
        "            X509v3 Subject Alternative Name:",
        "                DNS:foo.example.com, DNS:bar.example.com",
    ])
    runner.results[(
        "openssl", "req", "-in", "/tmp/x.csr", "-noout", "-text",
    )] = _ok(stdout=canned)

    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())
    info = mgr.inspect_csr(path="/tmp/x.csr")
    testbase.assert_true(info is not None)
    testbase.assert_equal(info.subject, "CN=foo.example.com, O=Example")
    testbase.assert_equal(info.public_key_algorithm, "RSA")
    testbase.assert_true("foo.example.com" in info.sans)
    testbase.assert_true("bar.example.com" in info.sans)
    return


def test_inspect_csr_returns_none_when_missing():
    """
        ``inspect_csr`` returns ``None`` when openssl exits non-zero.
    """
    runner = _MockRunner()
    runner.results[(
        "openssl", "req", "-in", "/missing.csr", "-noout", "-text",
    )] = _fail(stderr="No such file")

    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())
    info = mgr.inspect_csr(path="/missing.csr")
    testbase.assert_true(info is None)
    return


# ----------------------------------------------------------------------
# generate_self_signed
# ----------------------------------------------------------------------


def test_generate_self_signed_uses_x509_and_chmods_0644():
    """
        ``generate_self_signed`` emits ``openssl req -new -x509 -days``
        and chmods 0644 post-write.
    """
    runner = _MockRunner()
    runner.test_path_existence["/etc/pki/tls/certs/foo.csr"] = False
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())

    spec = _basic_csr_spec()
    result = mgr.generate_self_signed(spec=spec, days=730)

    testbase.assert_equal(result.status, CertificateActionStatus.SUCCESS)

    req_calls = [
        c for c in runner.calls
        if c["args"][0] == "openssl" and "req" in c["args"]
    ]
    testbase.assert_equal(len(req_calls), 1)
    testbase.assert_true("-x509" in req_calls[0]["args"])
    testbase.assert_true("-days" in req_calls[0]["args"])
    testbase.assert_true("730" in req_calls[0]["args"])

    chmod_calls = [c for c in runner.calls if c["args"][0] == "chmod"]
    testbase.assert_equal(len(chmod_calls), 1)
    testbase.assert_equal(chmod_calls[0]["args"][1], "644")
    return


def test_generate_self_signed_skipped_when_exists():
    """
        ``generate_self_signed`` returns SKIPPED when the destination
        cert already exists and overwrite=False.
    """
    runner = _MockRunner()
    runner.test_path_existence["/etc/pki/tls/certs/foo.csr"] = True
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())

    result = mgr.generate_self_signed(spec=_basic_csr_spec())
    testbase.assert_equal(result.status, CertificateActionStatus.SKIPPED)
    req_calls = [
        c for c in runner.calls
        if c["args"][0] == "openssl" and "req" in c["args"]
    ]
    testbase.assert_equal(len(req_calls), 0)
    return


# ----------------------------------------------------------------------
# inspect_certificate / expires_in
# ----------------------------------------------------------------------


def test_inspect_certificate_parses_full_record():
    """
        ``inspect_certificate`` parses subject / issuer / sans / KU /
        EKU / public_key_size and the SHA-256 fingerprint.
    """
    runner = _MockRunner()
    _seed_inspect_certificate(
        runner=runner,
        path="/etc/pki/tls/certs/foo.pem",
        not_after=_far_future(),
        fingerprint="AA:BB:CC",
    )

    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())
    info = mgr.inspect_certificate(path="/etc/pki/tls/certs/foo.pem")

    testbase.assert_true(info is not None)
    testbase.assert_equal(info.subject, "CN=foo.example.com")
    testbase.assert_equal(info.issuer, "CN=Foo CA")
    testbase.assert_equal(info.public_key_algorithm, "RSA")
    testbase.assert_equal(info.public_key_size, 3072)
    testbase.assert_equal(info.fingerprint_sha256, "AA:BB:CC")
    testbase.assert_true("foo.example.com" in info.sans)
    testbase.assert_true("bar.example.com" in info.sans)
    testbase.assert_true(len(info.key_usages) > 0)
    testbase.assert_true(len(info.extended_key_usages) > 0)
    return


def test_inspect_certificate_returns_none_when_missing():
    """
        ``inspect_certificate`` returns ``None`` when ``openssl x509
        -text`` exits non-zero.
    """
    runner = _MockRunner()
    runner.results[(
        "openssl", "x509", "-in", "/missing.pem", "-noout", "-text",
    )] = _fail(stderr="No such file")
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())
    info = mgr.inspect_certificate(path="/missing.pem")
    testbase.assert_true(info is None)
    return


def test_expires_in_returns_positive_delta_for_future_cert():
    """
        ``expires_in`` returns a positive timedelta for a far-future
        cert.
    """
    runner = _MockRunner()
    _seed_inspect_certificate(
        runner=runner,
        path="/srv/c.pem",
        not_after=_far_future(),
    )
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())
    delta = mgr.expires_in(path="/srv/c.pem")
    testbase.assert_true(delta.days > 1000)
    return


def test_expires_in_returns_negative_delta_for_expired_cert():
    """
        ``expires_in`` returns a negative timedelta for an expired
        cert.
    """
    expired = (
        datetime.now(tz=timezone.utc) - timedelta(days=10)
    ).strftime("%b %d %H:%M:%S %Y GMT")
    runner = _MockRunner()
    _seed_inspect_certificate(
        runner=runner,
        path="/srv/c.pem",
        not_after=expired,
    )
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())
    delta = mgr.expires_in(path="/srv/c.pem")
    testbase.assert_true(delta.days < 0)
    return


def test_expires_in_raises_when_missing():
    """
        ``expires_in`` raises ``FileNotFoundError`` when the cert is
        absent.
    """
    runner = _MockRunner()
    runner.results[(
        "openssl", "x509", "-in", "/missing.pem", "-noout", "-text",
    )] = _fail(stderr="No such file")
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())
    raised = False
    try:
        mgr.expires_in(path="/missing.pem")
    except FileNotFoundError:
        raised = True
    testbase.assert_equal(raised, True)
    return


# ----------------------------------------------------------------------
# certmonger
# ----------------------------------------------------------------------


_GETCERT_LIST_OUTPUT = "\n".join([
    "Number of certificates and requests being tracked: 1.",
    "Request ID '20240101000000':",
    "    status: MONITORING",
    "    stuck: no",
    "    key pair storage: type=FILE,location='/etc/pki/tls/private/foo.key'",
    "    certificate: type=FILE,location='/etc/pki/tls/certs/foo.pem'",
    "    CA: local",
    "    issuer: CN=Local CA",
    "    subject: CN=foo.example.com",
    "    expires: 2030-01-01 00:00:00 UTC",
    "    dns: foo.example.com,bar.example.com",
])


def test_list_certmonger_parses_blocks():
    """
        ``list_certmonger`` parses one block per Request ID.
    """
    runner = _MockRunner()
    runner.results[("getcert", "list")] = _ok(stdout=_GETCERT_LIST_OUTPUT)
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())

    entries = mgr.list_certmonger()
    testbase.assert_equal(len(entries), 1)
    e = entries[0]
    testbase.assert_equal(e.request_id, "20240101000000")
    testbase.assert_equal(e.status, "MONITORING")
    testbase.assert_equal(e.ca, "local")
    testbase.assert_equal(e.cert_path, "/etc/pki/tls/certs/foo.pem")
    testbase.assert_equal(e.key_path, "/etc/pki/tls/private/foo.key")
    testbase.assert_true("foo.example.com" in e.sans)
    return


def test_request_certmonger_dispatches_getcert_request():
    """
        ``request_certmonger`` dispatches ``getcert request -f -k -r
        -w -N`` plus ``-D`` per SAN.
    """
    runner = _MockRunner()
    runner.results[("getcert", "list")] = _ok(stdout="")
    mgr = CertificateIssuanceManager(
        runner, CertificateIssuanceConfig(elevated=True),
    )

    spec = _basic_csr_spec()
    result = mgr.request_certmonger(
        spec=spec,
        cert_path="/etc/pki/tls/certs/foo.pem",
        key_path="/etc/pki/tls/private/foo.key",
        ca="local",
    )

    testbase.assert_equal(result.status, CertificateActionStatus.SUCCESS)
    request_calls = [
        c for c in runner.calls
        if c["args"][0] == "getcert" and "request" in c["args"]
    ]
    testbase.assert_equal(len(request_calls), 1)
    args = request_calls[0]["args"]
    testbase.assert_true("-f" in args)
    testbase.assert_true("/etc/pki/tls/certs/foo.pem" in args)
    testbase.assert_true("-k" in args)
    testbase.assert_true("-r" in args)
    testbase.assert_true("-w" in args)
    testbase.assert_true("-N" in args)
    testbase.assert_true("foo.example.com" in args)
    testbase.assert_true("-D" in args)
    testbase.assert_true("bar.example.com" in args)
    testbase.assert_true("-c" in args)
    testbase.assert_true("local" in args)
    testbase.assert_equal(request_calls[0]["elevated"], True)
    return


def test_request_certmonger_skipped_when_already_tracked():
    """
        ``request_certmonger`` returns SKIPPED when an identical
        tracking entry already exists.
    """
    runner = _MockRunner()
    runner.results[("getcert", "list")] = _ok(stdout=_GETCERT_LIST_OUTPUT)
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())

    spec = _basic_csr_spec()
    result = mgr.request_certmonger(
        spec=spec,
        cert_path="/etc/pki/tls/certs/foo.pem",
        key_path="/etc/pki/tls/private/foo.key",
        ca="local",
    )
    testbase.assert_equal(result.status, CertificateActionStatus.SKIPPED)
    request_calls = [
        c for c in runner.calls
        if c["args"][0] == "getcert" and "request" in c["args"]
    ]
    testbase.assert_equal(len(request_calls), 0)
    return


def test_stop_certmonger_dispatches_when_match_present():
    """
        ``stop_certmonger`` dispatches ``getcert stop-tracking -f``
        when a tracking entry matches.
    """
    runner = _MockRunner()
    runner.results[("getcert", "list")] = _ok(stdout=_GETCERT_LIST_OUTPUT)
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())

    result = mgr.stop_certmonger(cert_path="/etc/pki/tls/certs/foo.pem")
    testbase.assert_equal(result.status, CertificateActionStatus.SUCCESS)
    stop_calls = [
        c for c in runner.calls
        if c["args"][0] == "getcert" and "stop-tracking" in c["args"]
    ]
    testbase.assert_equal(len(stop_calls), 1)
    return


def test_stop_certmonger_skipped_when_no_match():
    """
        ``stop_certmonger`` returns SKIPPED when no tracking entry
        matches.
    """
    runner = _MockRunner()
    runner.results[("getcert", "list")] = _ok(stdout="")
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())

    result = mgr.stop_certmonger(cert_path="/etc/pki/tls/certs/missing.pem")
    testbase.assert_equal(result.status, CertificateActionStatus.SKIPPED)
    stop_calls = [
        c for c in runner.calls
        if c["args"][0] == "getcert" and "stop-tracking" in c["args"]
    ]
    testbase.assert_equal(len(stop_calls), 0)
    return


def test_resubmit_certmonger_dispatches_when_match():
    """
        ``resubmit_certmonger`` dispatches ``getcert resubmit -f``
        when a tracking entry matches.
    """
    runner = _MockRunner()
    runner.results[("getcert", "list")] = _ok(stdout=_GETCERT_LIST_OUTPUT)
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())

    result = mgr.resubmit_certmonger(
        cert_path="/etc/pki/tls/certs/foo.pem",
    )
    testbase.assert_equal(result.status, CertificateActionStatus.SUCCESS)
    resubmit_calls = [
        c for c in runner.calls
        if c["args"][0] == "getcert" and "resubmit" in c["args"]
    ]
    testbase.assert_equal(len(resubmit_calls), 1)
    return


def test_resubmit_certmonger_skipped_when_no_match():
    """
        ``resubmit_certmonger`` returns SKIPPED when no tracking
        entry matches.
    """
    runner = _MockRunner()
    runner.results[("getcert", "list")] = _ok(stdout="")
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())

    result = mgr.resubmit_certmonger(
        cert_path="/etc/pki/tls/certs/missing.pem",
    )
    testbase.assert_equal(result.status, CertificateActionStatus.SKIPPED)
    return


# ----------------------------------------------------------------------
# ACME — request_acme / renew_acme
# ----------------------------------------------------------------------


def test_request_acme_webroot_builds_command():
    """
        ``request_acme`` with ``method='webroot'`` builds the certbot
        argv with ``--webroot --webroot-path`` and one ``-d`` per
        domain.
    """
    runner = _MockRunner()
    mgr = CertificateIssuanceManager(
        runner, CertificateIssuanceConfig(elevated=True),
    )
    spec = AcmeIssuanceSpec(
        domains=["foo.example.com", "bar.example.com"],
        email="ops@example.com",
        method="webroot",
        webroot_path="/var/www/html",
    )
    result = mgr.request_acme(spec=spec)
    testbase.assert_equal(result.status, CertificateActionStatus.SUCCESS)

    certbot_calls = [
        c for c in runner.calls if c["args"][0] == "certbot"
    ]
    testbase.assert_equal(len(certbot_calls), 1)
    args = certbot_calls[0]["args"]
    testbase.assert_true("certonly" in args)
    testbase.assert_true("--webroot" in args)
    testbase.assert_true("--webroot-path" in args)
    testbase.assert_true("/var/www/html" in args)
    testbase.assert_true("-d" in args)
    testbase.assert_true("foo.example.com" in args)
    testbase.assert_true("bar.example.com" in args)
    testbase.assert_true("--agree-tos" in args)
    testbase.assert_true("-m" in args)
    testbase.assert_true("ops@example.com" in args)
    testbase.assert_true("--non-interactive" in args)
    return


def test_request_acme_standalone_builds_command():
    """
        ``request_acme`` with ``method='standalone'`` builds the
        certbot argv with ``--standalone``.
    """
    runner = _MockRunner()
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())
    spec = AcmeIssuanceSpec(
        domains=["foo.example.com"],
        email="ops@example.com",
        method="standalone",
    )
    result = mgr.request_acme(spec=spec)
    testbase.assert_equal(result.status, CertificateActionStatus.SUCCESS)
    certbot_calls = [
        c for c in runner.calls if c["args"][0] == "certbot"
    ]
    testbase.assert_equal(len(certbot_calls), 1)
    testbase.assert_true("--standalone" in certbot_calls[0]["args"])
    return


def test_request_acme_dns_plugin_builds_command():
    """
        ``request_acme`` with ``method='dns-route53'`` builds the
        certbot argv with ``--route53 --route53-credentials <path>``.
    """
    runner = _MockRunner()
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())
    spec = AcmeIssuanceSpec(
        domains=["foo.example.com"],
        email="ops@example.com",
        method="dns-route53",
        plugin="route53",
        plugin_credentials_path="/etc/letsencrypt/route53.ini",
    )
    result = mgr.request_acme(spec=spec)
    testbase.assert_equal(result.status, CertificateActionStatus.SUCCESS)
    certbot_calls = [
        c for c in runner.calls if c["args"][0] == "certbot"
    ]
    testbase.assert_equal(len(certbot_calls), 1)
    testbase.assert_true("--route53" in certbot_calls[0]["args"])
    testbase.assert_true(
        "--route53-credentials" in certbot_calls[0]["args"],
    )
    testbase.assert_true(
        "/etc/letsencrypt/route53.ini" in certbot_calls[0]["args"],
    )
    return


def test_request_acme_webroot_missing_path_raises():
    """
        ``request_acme`` raises :class:`AcmeRequiredArgumentError`
        when ``method='webroot'`` and no ``webroot_path`` is set.
    """
    runner = _MockRunner()
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())
    spec = AcmeIssuanceSpec(
        domains=["foo.example.com"],
        email="ops@example.com",
        method="webroot",
    )
    raised = False
    try:
        mgr.request_acme(spec=spec)
    except AcmeRequiredArgumentError:
        raised = True
    testbase.assert_equal(raised, True)
    return


def test_request_acme_skipped_when_existing_far_future():
    """
        ``request_acme`` returns SKIPPED when ``spec.cert_path`` is
        set, the file exists, and the remaining validity exceeds
        the renew threshold.
    """
    runner = _MockRunner()
    _seed_inspect_certificate(
        runner=runner,
        path="/etc/letsencrypt/live/foo/fullchain.pem",
        not_after=_far_future(),
    )
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())
    spec = AcmeIssuanceSpec(
        domains=["foo.example.com"],
        email="ops@example.com",
        method="standalone",
        cert_path="/etc/letsencrypt/live/foo/fullchain.pem",
        renew_after_days=30,
    )
    result = mgr.request_acme(spec=spec)
    testbase.assert_equal(result.status, CertificateActionStatus.SKIPPED)
    certbot_calls = [
        c for c in runner.calls if c["args"][0] == "certbot"
    ]
    testbase.assert_equal(len(certbot_calls), 0)
    return


def test_request_acme_dispatches_when_existing_near_expiry():
    """
        ``request_acme`` dispatches when the existing cert is within
        the renew threshold.
    """
    runner = _MockRunner()
    _seed_inspect_certificate(
        runner=runner,
        path="/etc/letsencrypt/live/foo/fullchain.pem",
        not_after=_near_expiry(),
    )
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())
    spec = AcmeIssuanceSpec(
        domains=["foo.example.com"],
        email="ops@example.com",
        method="standalone",
        cert_path="/etc/letsencrypt/live/foo/fullchain.pem",
        renew_after_days=30,
    )
    result = mgr.request_acme(spec=spec)
    testbase.assert_equal(result.status, CertificateActionStatus.SUCCESS)
    return


def test_request_acme_dry_run_does_not_dispatch():
    """
        ``request_acme`` under dry-run never invokes certbot.
    """
    runner = _MockRunner()
    mgr = CertificateIssuanceManager(
        runner, CertificateIssuanceConfig(dry_run=True),
    )
    spec = AcmeIssuanceSpec(
        domains=["foo.example.com"],
        email="ops@example.com",
        method="standalone",
    )
    result = mgr.request_acme(spec=spec)
    testbase.assert_equal(result.status, CertificateActionStatus.DRY_RUN)
    certbot_calls = [
        c for c in runner.calls if c["args"][0] == "certbot"
    ]
    testbase.assert_equal(len(certbot_calls), 0)
    return


def test_renew_acme_builds_command():
    """
        ``renew_acme`` dispatches ``certbot renew --cert-name`` and
        appends ``--force-renewal`` only when ``force=True``.
    """
    runner = _MockRunner()
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())

    result = mgr.renew_acme(cert_name="foo.example.com", force=True)
    testbase.assert_equal(result.status, CertificateActionStatus.SUCCESS)
    certbot_calls = [
        c for c in runner.calls if c["args"][0] == "certbot"
    ]
    testbase.assert_equal(len(certbot_calls), 1)
    testbase.assert_true("renew" in certbot_calls[0]["args"])
    testbase.assert_true("--cert-name" in certbot_calls[0]["args"])
    testbase.assert_true("foo.example.com" in certbot_calls[0]["args"])
    testbase.assert_true("--force-renewal" in certbot_calls[0]["args"])
    return


# ----------------------------------------------------------------------
# Failure mapping
# ----------------------------------------------------------------------


def test_generate_key_failure_propagates_runner_exit_code():
    """
        A non-zero exit code from the runner becomes FAILED with
        propagated exit_code / stderr.
    """
    runner = _MockRunner()
    runner.test_path_existence["/srv/k.pem"] = False
    runner.default_result = _fail(stderr="permission denied", exit_code=13)
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())

    result = mgr.generate_key(
        path="/srv/k.pem",
        algorithm="RSA",
        bits_or_curve="3072",
    )
    testbase.assert_equal(result.status, CertificateActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 13)
    testbase.assert_equal(result.stderr, "permission denied")
    return


# ----------------------------------------------------------------------
# Module-level parser tests
# ----------------------------------------------------------------------


def test_parse_getcert_list_parses_minimal_block():
    """
        ``_parse_getcert_list`` produces one record per Request ID
        block.
    """
    parsed = _parse_getcert_list(stdout=_GETCERT_LIST_OUTPUT)
    testbase.assert_equal(len(parsed), 1)
    testbase.assert_equal(parsed[0].cert_path, "/etc/pki/tls/certs/foo.pem")
    testbase.assert_equal(parsed[0].subject, "CN=foo.example.com")
    return


def test_parse_openssl_x509_text_extracts_sans_and_size():
    """
        ``_parse_openssl_x509_text`` extracts subject, issuer, SANs,
        and public-key size.
    """
    body = _x509_text_with_dates(not_after="Jan  1 00:00:00 2030 GMT")
    parsed = _parse_openssl_x509_text(stdout=body)
    testbase.assert_equal(parsed["subject"], "CN=foo.example.com")
    testbase.assert_equal(parsed["issuer"], "CN=Foo CA")
    testbase.assert_equal(parsed["public_key_algorithm"], "RSA")
    testbase.assert_equal(parsed["public_key_size"], 3072)
    testbase.assert_true("foo.example.com" in parsed["sans"])
    testbase.assert_true("bar.example.com" in parsed["sans"])
    return


def test_parse_fingerprint_line_extracts_value():
    """
        ``_parse_fingerprint_line`` extracts the SHA-256 hex string
        from openssl ``-fingerprint -sha256`` output.
    """
    canned = "sha256 Fingerprint=AB:CD:EF:01:23\n"
    parsed = _parse_fingerprint_line(stdout=canned)
    testbase.assert_equal(parsed, "AB:CD:EF:01:23")
    return


def test_render_openssl_config_includes_alt_names():
    """
        ``_render_openssl_config`` emits ``[alt_names]`` with one
        ``DNS.N`` entry per SAN.
    """
    spec = _basic_csr_spec()
    text = _render_openssl_config(spec=spec)
    testbase.assert_true("[alt_names]" in text)
    testbase.assert_true("DNS.1 = foo.example.com" in text)
    testbase.assert_true("DNS.2 = bar.example.com" in text)
    return


# ----------------------------------------------------------------------
# Credential-resolution
# ----------------------------------------------------------------------


def test_credential_unsupported_raises_credential_error():
    """
        Passing an unrecognised credential type to ``generate_key``
        raises :class:`CertificateCredentialError`.
    """
    runner = _MockRunner()
    runner.test_path_existence["/srv/k.pem"] = False
    mgr = CertificateIssuanceManager(runner, CertificateIssuanceConfig())

    class _Bogus:
        pass

    raised = False
    try:
        mgr.generate_key(
            path="/srv/k.pem",
            algorithm="RSA",
            bits_or_curve="3072",
            credential=_Bogus(),
        )
    except CertificateCredentialError:
        raised = True
    testbase.assert_equal(raised, True)
    return
