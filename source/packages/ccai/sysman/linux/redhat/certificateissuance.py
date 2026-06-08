# ccai.sysman.linux.redhat.certificateissuance
#
# CertificateIssuanceManager wraps the RHEL X.509 issuance toolchain —
# ``openssl`` for local CSR / self-signed / inspection flows,
# ``getcert`` (certmonger) for tracked enrolment and renewal, and
# ``certbot`` for ACME issuance — through the injected
# :class:`ICommandRunner`.
#
# All filesystem reads and writes are routed through the runner using
# ``cat`` / ``tee`` / ``rm`` / ``chmod`` / ``test`` so the manager
# works identically under local and SSH execution.  Private-key
# passphrases and ACME-plugin credentials flow on runner ``stdin`` —
# never on argv.  The recorded ``command`` field on every returned
# :class:`CertificateActionResult` is sanitised: it carries the same
# argv that was sent to the runner, never the secret payload.
#
# See specs/3-certificate-issuance/{requirements,design,tasks}.md for
# the full surface.

import base64
import hashlib
import os

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from ccai.errors.exceptions import SemanticError

from ccai.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
    SanitisedDispatchMixin,
)


# ----------------------------------------------------------------------
# Module-level command constants
# ----------------------------------------------------------------------

_CAT     = "cat"
_TEE     = "tee"
_RM      = "rm"
_TEST    = "test"
_CHMOD   = "chmod"
_OPENSSL = "openssl"
_GETCERT = "getcert"
_CERTBOT = "certbot"

_PEM_BEGIN = "-----BEGIN CERTIFICATE-----"
_PEM_END   = "-----END CERTIFICATE-----"


CertificateActionStatus = ActionStatus


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------


@dataclass
class CertificateIssuanceConfig(FeatureConfig):
    """
        Configuration for :class:`CertificateIssuanceManager`.
        Inherits ``dry_run`` and ``elevated`` from
        :class:`FeatureConfig`.

        :param openssl_bin: Command name for ``openssl``.  Defaults to
                            ``"openssl"`` (PATH-resolved).
        :param certbot_bin: Command name for ``certbot``.  Defaults to
                            ``"certbot"``.
        :param getcert_bin: Command name for ``getcert``.  Defaults to
                            ``"getcert"``.
        :param default_rsa_bits: Default RSA key size (bits) used when
                                 ``generate_key`` is invoked with
                                 ``algorithm="RSA"`` and
                                 ``bits_or_curve=None``.
        :param default_ec_curve: Default EC curve name used when
                                 ``generate_key`` is invoked with
                                 ``algorithm="EC"`` and
                                 ``bits_or_curve=None``.
        :param key_mode: POSIX mode bits for newly generated private
                         keys.  Defaults to ``0o600``.
        :param cert_mode: POSIX mode bits for newly generated
                          certificates.  Defaults to ``0o644``.
        :param renew_after_days_default: Default renewal-threshold
                                         used by :meth:`request_acme`
                                         when the
                                         :class:`AcmeIssuanceSpec`
                                         leaves ``renew_after_days``
                                         unset.  Defaults to ``30``.
        :param tmp_dir: Directory used for temporary OpenSSL
                        configuration files.  Defaults to ``"/tmp"``.
    """
    openssl_bin: str = _OPENSSL
    certbot_bin: str = _CERTBOT
    getcert_bin: str = _GETCERT
    default_rsa_bits: int = 3072
    default_ec_curve: str = "P-256"
    key_mode: int = 0o600
    cert_mode: int = 0o644
    renew_after_days_default: int = 30
    tmp_dir: str = "/tmp"


# ----------------------------------------------------------------------
# Result dataclass
# ----------------------------------------------------------------------


@dataclass
class CertIssuanceActionResult(FeatureActionResult):
    """
        Outcome of a certificate-issuance write operation.

        :param path: The on-disk path acted upon (key / CSR / cert /
                     certmonger ``-f`` target), when applicable.
    """
    path: Optional[str] = None


# ----------------------------------------------------------------------
# Domain dataclasses
# ----------------------------------------------------------------------


@dataclass
class SubjectSpec:
    """
        Distinguished-name fields used to render an OpenSSL
        ``-subj`` value or an OpenSSL config ``[req_distinguished_name]``
        block.

        :param common_name: The CN.
        :param country: Two-letter country code.
        :param state: State / province.
        :param locality: Locality / city.
        :param organization: Organization.
        :param organizational_unit: Organizational unit.
        :param email: Email address.
    """
    common_name: str
    country: Optional[str] = None
    state: Optional[str] = None
    locality: Optional[str] = None
    organization: Optional[str] = None
    organizational_unit: Optional[str] = None
    email: Optional[str] = None


@dataclass
class CsrSpec:
    """
        Caller-supplied spec for :meth:`generate_csr` and
        :meth:`generate_self_signed`.

        :param key_path: Path to an existing private-key file.
        :param csr_path: Destination path for the CSR (or, when used
                         from :meth:`generate_self_signed`, the cert
                         output path).
        :param subject: :class:`SubjectSpec` describing the DN.
        :param sans: Subject-alternative-names list.  DNS entries are
                     bare hostnames; IP entries should be prefixed with
                     ``"IP:"``; URI / email entries with ``"URI:"`` /
                     ``"email:"``.
        :param key_usages: Optional KU short names (e.g.
                           ``"digitalSignature"``).
        :param extended_key_usages: Optional EKU short names (e.g.
                                    ``"serverAuth"``).
    """
    key_path: str
    csr_path: str
    subject: SubjectSpec
    sans: list[str] = field(default_factory=list)
    key_usages: list[str] = field(default_factory=list)
    extended_key_usages: list[str] = field(default_factory=list)


@dataclass
class CsrInfo:
    """
        Parsed result of ``openssl req -noout -text``.

        :param path: The CSR file path.
        :param subject: Subject DN string.
        :param public_key_algorithm: ``"RSA"`` / ``"EC"`` / etc.
        :param sans: Subject-alternative-name entries.
    """
    path: str
    subject: str
    public_key_algorithm: str
    sans: list[str] = field(default_factory=list)


@dataclass
class CertificateInfo:
    """
        Parsed result of ``openssl x509 -noout -text`` plus
        ``-fingerprint -sha256``.

        :param path: The certificate file path.
        :param subject: Subject DN string.
        :param issuer: Issuer DN string.
        :param serial_hex: Hex serial number.
        :param not_before: ``notBefore`` value (raw openssl form).
        :param not_after: ``notAfter`` value (raw openssl form).
        :param signature_algorithm: Signature algorithm OID / name.
        :param public_key_algorithm: ``"RSA"`` / ``"EC"`` / etc.
        :param public_key_size: Public-key size in bits, or ``None``
                                when not exposed by parsing.
        :param sans: Subject-alternative-name entries.
        :param key_usages: Key-usage entries.
        :param extended_key_usages: Extended-key-usage entries.
        :param fingerprint_sha256: SHA-256 fingerprint
                                   (uppercase colon-delimited).
    """
    path: str
    subject: str
    issuer: str
    serial_hex: str
    not_before: str
    not_after: str
    signature_algorithm: str
    public_key_algorithm: str
    public_key_size: Optional[int]
    sans: list[str] = field(default_factory=list)
    key_usages: list[str] = field(default_factory=list)
    extended_key_usages: list[str] = field(default_factory=list)
    fingerprint_sha256: str = ""


@dataclass
class CertmongerTracking:
    """
        One parsed entry from ``getcert list``.

        :param request_id: certmonger's request identifier.
        :param status: certmonger status string (e.g.
                       ``"MONITORING"``).
        :param ca: certmonger CA name (e.g. ``"local"``, ``"IPA"``).
        :param cert_path: Tracked certificate file path.
        :param key_path: Tracked private-key file path.
        :param subject: Subject DN, when reported.
        :param sans: Subject-alternative names.
        :param expires: ``expires:`` field from ``getcert list``.
    """
    request_id: str
    status: str
    ca: str
    cert_path: str
    key_path: str
    subject: Optional[str] = None
    sans: list[str] = field(default_factory=list)
    expires: Optional[str] = None


@dataclass
class AcmeIssuanceSpec:
    """
        Caller-supplied spec for :meth:`request_acme`.

        :param domains: List of domain names; the first is the primary
                        certificate name.
        :param email: ACME registration email.
        :param method: Challenge method.  One of ``"webroot"``,
                       ``"standalone"``, or ``"dns-<plugin>"``.
        :param webroot_path: Filesystem path for the ``webroot`` method.
                             Required when ``method == "webroot"``.
        :param plugin: Certbot plugin short-name (e.g. ``"route53"``).
                       Required when ``method`` starts with ``"dns-"``.
        :param plugin_credentials_path: Path to a
                                        plugin-credentials file the
                                        caller has already written
                                        (mode ``0600``).  Used as
                                        ``--<plugin>-credentials``.
        :param server: Optional ACME directory URL (e.g. staging).
        :param renew_after_days: Override of
                                 ``config.renew_after_days_default``.
                                 When the existing cert has more than
                                 this number of days remaining, the
                                 verb returns ``SKIPPED``.
        :param cert_path: Path of the existing cert used by the
                          renewal-threshold guard, when the caller
                          knows it.  When ``None`` the guard is
                          skipped.
    """
    domains: list[str]
    email: str
    method: str
    webroot_path: Optional[str] = None
    plugin: Optional[str] = None
    plugin_credentials_path: Optional[str] = None
    server: Optional[str] = None
    renew_after_days: Optional[int] = None
    cert_path: Optional[str] = None


# ----------------------------------------------------------------------
# Exceptions
# ----------------------------------------------------------------------


class CertificateCredentialError(SemanticError):
    """
        Raised when a private-key passphrase or other credential is
        required by a verb but not supplied.  Subclass of
        :class:`SemanticError` — bubbles unconditionally; never caught
        by the manager.
    """
    pass


class AcmeRequiredArgumentError(SemanticError):
    """
        Raised when an :class:`AcmeIssuanceSpec` is missing a field
        required by its declared method (e.g. ``webroot_path`` for the
        ``"webroot"`` method, ``plugin`` / ``plugin_credentials_path``
        for a ``"dns-<plugin>"`` method).
    """
    pass


# ----------------------------------------------------------------------
# Manager
# ----------------------------------------------------------------------


class CertificateIssuanceManager(SanitisedDispatchMixin, BaseFeatureManager):
    """
        Facade for RHEL X.509 issuance through the command runner.

        Wraps three independent backends:

        * ``openssl`` — private-key generation, CSR generation,
          self-signed issuance, certificate inspection.
        * ``getcert`` (certmonger) — tracked enrolment plus renewal
          and stop-tracking.
        * ``certbot`` — ACME issuance and renewal.

        File I/O (private-key creation, temp OpenSSL config writes,
        chmod) routes through the runner via ``tee`` / ``rm`` /
        ``chmod`` / ``test``.  Private-key passphrases and ACME-plugin
        credentials flow on runner ``stdin`` only; the recorded
        ``command`` field on the returned
        :class:`CertIssuanceActionResult` is sanitised.

        Inherits :class:`SanitisedDispatchMixin` so the recorded
        ``command`` field is the caller-supplied argv rather than the
        runner-recorded argv (which some runners may augment).
    """

    result_class = CertIssuanceActionResult
    feature_name = "certificate-issuance"

    # ------------------------------------------------------------------
    # Keys
    # ------------------------------------------------------------------

    def generate_key(
        self,
        path: str,
        algorithm: str,
        bits_or_curve: Optional[str] = None,
        credential=None,
        overwrite: bool = False,
    ) -> CertIssuanceActionResult:
        """
            Generate a private key at ``path``.

            :param path: Destination path for the key (PEM).
            :param algorithm: ``"RSA"`` or ``"EC"`` (case-insensitive).
            :param bits_or_curve: For RSA, the bit size as a string.
                                  For EC, the curve name.  When
                                  ``None`` the manager falls back to
                                  ``config.default_rsa_bits`` /
                                  ``config.default_ec_curve``.
            :param credential: Optional passphrase carrier.  When
                               supplied, the key is encrypted with
                               AES-256 and the passphrase is fed via
                               runner ``stdin`` (never argv).
            :param overwrite: When ``False`` (the default) and the
                              file already exists, returns ``SKIPPED``
                              without dispatching.

            :returns: The :class:`CertIssuanceActionResult`.
        """
        cfg = self._typed_config()

        if not overwrite:
            if self._path_exists(path=path):
                skipped = self._build_result(
                    status=CertificateActionStatus.SKIPPED,
                    command=None,
                    detail="key already exists at path",
                    path=path,
                )
                return skipped

        algo_upper = algorithm.upper()
        if algo_upper == "RSA":
            if bits_or_curve is None:
                bits_text = str(cfg.default_rsa_bits)
            else:
                bits_text = str(bits_or_curve)
            command: list[str] = [
                cfg.openssl_bin,
                "genpkey",
                "-algorithm", "RSA",
                "-pkeyopt", "rsa_keygen_bits:{}".format(bits_text),
                "-out", path,
            ]
        elif algo_upper == "EC":
            if bits_or_curve is None:
                curve_text = cfg.default_ec_curve
            else:
                curve_text = str(bits_or_curve)
            command = [
                cfg.openssl_bin,
                "genpkey",
                "-algorithm", "EC",
                "-pkeyopt", "ec_paramgen_curve:{}".format(curve_text),
                "-out", path,
            ]
        else:
            failed = self._build_result(
                status=CertificateActionStatus.FAILED,
                command=None,
                detail="unsupported key algorithm: {}".format(algorithm),
                path=path,
            )
            return failed

        stdin_payload: Optional[str] = None
        if credential is not None:
            command.append("-aes256")
            command.append("-pass")
            command.append("stdin")
            secret_bytes = _resolve_credential_bytes(credential=credential)
            stdin_payload = secret_bytes.decode("utf-8")

        if self._config.dry_run:
            dry = self._build_result(
                status=CertificateActionStatus.DRY_RUN,
                command=list(command),
                detail="generate_key:{}".format(algo_upper),
                path=path,
            )
            return dry

        result = self._dispatch(
            command=command,
            detail="generate_key:{}".format(algo_upper),
            stdin=stdin_payload,
            operation="generate_key",
            path=path,
        )

        if result.status == CertificateActionStatus.SUCCESS:
            self._chmod(path=path, mode=cfg.key_mode)

        return result

    # ------------------------------------------------------------------
    # CSR
    # ------------------------------------------------------------------

    def generate_csr(
        self,
        spec: CsrSpec,
        credential=None,
        overwrite: bool = False,
    ) -> CertIssuanceActionResult:
        """
            Generate an X.509 certificate signing request from an
            existing key.

            Renders an OpenSSL config including
            ``[req_distinguished_name]``, ``[v3_req]``, and
            ``[alt_names]`` sections, writes it to a temp path via
            ``tee``, dispatches ``openssl req -new -key <key> -out
            <csr> -config <tmp>``, and removes the temp config in a
            ``finally`` block.

            :param spec: :class:`CsrSpec` describing the CSR.
            :param credential: Optional key-passphrase carrier.
                               When supplied, the manager appends
                               ``-passin stdin`` and forwards the
                               passphrase on stdin.
            :param overwrite: When ``False`` (the default) and
                              ``spec.csr_path`` exists, returns
                              ``SKIPPED``.

            :returns: The :class:`CertIssuanceActionResult`.
        """
        rtnval = self._openssl_req(
            spec=spec,
            credential=credential,
            overwrite=overwrite,
            self_signed=False,
            days=0,
            chmod_mode=None,
            output_path=spec.csr_path,
            operation="generate_csr",
        )
        return rtnval

    def inspect_csr(self, path: str) -> Optional[CsrInfo]:
        """
            Parse the CSR at ``path`` via ``openssl req -in <path>
            -noout -text``.

            :param path: Path to a PEM-encoded CSR.

            :returns: A :class:`CsrInfo` on success; ``None`` when the
                      file is absent or the command exits non-zero.
        """
        cfg = self._typed_config()
        command = [
            cfg.openssl_bin,
            "req",
            "-in", path,
            "-noout",
            "-text",
        ]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: Optional[CsrInfo] = None
            return rtnval

        parsed = _parse_openssl_req_text(stdout=run_result.stdout)
        rtnval = CsrInfo(
            path=path,
            subject=parsed["subject"],
            public_key_algorithm=parsed["public_key_algorithm"],
            sans=parsed["sans"],
        )
        return rtnval

    # ------------------------------------------------------------------
    # Self-signed
    # ------------------------------------------------------------------

    def generate_self_signed(
        self,
        spec: CsrSpec,
        credential=None,
        overwrite: bool = False,
        days: int = 365,
    ) -> CertIssuanceActionResult:
        """
            Issue a self-signed X.509 certificate.

            :param spec: :class:`CsrSpec`; ``spec.csr_path`` is
                         re-purposed as the output certificate path.
            :param credential: Optional key-passphrase carrier.
            :param overwrite: When ``False`` (the default) and the
                              destination certificate exists, returns
                              ``SKIPPED``.
            :param days: Validity in days.

            :returns: The :class:`CertIssuanceActionResult`.
        """
        cfg = self._typed_config()
        rtnval = self._openssl_req(
            spec=spec,
            credential=credential,
            overwrite=overwrite,
            self_signed=True,
            days=days,
            chmod_mode=cfg.cert_mode,
            output_path=spec.csr_path,
            operation="generate_self_signed",
        )
        return rtnval

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def inspect_certificate(self, path: str) -> Optional[CertificateInfo]:
        """
            Read structured metadata for a certificate file.

            Returns ``None`` when the file is absent.

            :param path: Path to the certificate file.

            :returns: The :class:`CertificateInfo` or ``None``.
        """
        cfg = self._typed_config()

        text_command = [
            cfg.openssl_bin,
            "x509",
            "-in", path,
            "-noout",
            "-text",
        ]
        text_result = self._runner.run(list(text_command), elevated=False)
        if text_result.exit_code != 0:
            rtnval: Optional[CertificateInfo] = None
            return rtnval

        fp_command = [
            cfg.openssl_bin,
            "x509",
            "-in", path,
            "-noout",
            "-fingerprint", "-sha256",
        ]
        fp_result = self._runner.run(list(fp_command), elevated=False)
        if fp_result.exit_code == 0:
            fingerprint = _parse_fingerprint_line(stdout=fp_result.stdout)
        else:
            fingerprint = ""

        parsed = _parse_openssl_x509_text(stdout=text_result.stdout)
        rtnval = CertificateInfo(
            path=path,
            subject=parsed["subject"],
            issuer=parsed["issuer"],
            serial_hex=parsed["serial_hex"],
            not_before=parsed["not_before"],
            not_after=parsed["not_after"],
            signature_algorithm=parsed["signature_algorithm"],
            public_key_algorithm=parsed["public_key_algorithm"],
            public_key_size=parsed["public_key_size"],
            sans=parsed["sans"],
            key_usages=parsed["key_usages"],
            extended_key_usages=parsed["extended_key_usages"],
            fingerprint_sha256=fingerprint,
        )
        return rtnval

    def expires_in(self, path: str) -> timedelta:
        """
            Return ``not_after - now()`` as a :class:`timedelta`.

            :param path: Path to the certificate file.

            :returns: A :class:`timedelta`; negative when the cert is
                      already expired.

            :raises FileNotFoundError: When the certificate file is
                                       absent or unparseable.
        """
        info = self.inspect_certificate(path=path)
        if info is None:
            err_msg_lines = ["Certificate file not found or unparseable."]
            err_msg_lines.append("    PATH: {}".format(path))
            err_msg = os.linesep.join(err_msg_lines)
            raise FileNotFoundError(err_msg)

        parsed_dt = _parse_openssl_date(text=info.not_after)
        if parsed_dt is None:
            err_msg_lines = ["Failed to parse certificate notAfter."]
            err_msg_lines.append("    PATH: {}".format(path))
            err_msg_lines.append("    NOT_AFTER: {}".format(info.not_after))
            err_msg = os.linesep.join(err_msg_lines)
            raise FileNotFoundError(err_msg)

        now = datetime.now(tz=timezone.utc)
        rtnval = parsed_dt - now
        return rtnval

    # ------------------------------------------------------------------
    # certmonger
    # ------------------------------------------------------------------

    def list_certmonger(self) -> list[CertmongerTracking]:
        """
            Enumerate certmonger tracking entries via
            ``getcert list``.

            :returns: A ``list[CertmongerTracking]``.  Empty when the
                      command exits non-zero.
        """
        cfg = self._typed_config()
        command = [cfg.getcert_bin, "list"]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: list[CertmongerTracking] = []
            return rtnval
        rtnval = _parse_getcert_list(stdout=run_result.stdout)
        return rtnval

    def request_certmonger(
        self,
        spec: CsrSpec,
        cert_path: str,
        key_path: str,
        ca: Optional[str] = None,
        profile: Optional[str] = None,
    ) -> CertIssuanceActionResult:
        """
            Enrol a certificate with certmonger via ``getcert
            request``.

            Returns ``SKIPPED`` when an existing tracking entry for
            ``cert_path`` matches the same subject + SANs + CA.

            :param spec: :class:`CsrSpec` describing the certificate.
            :param cert_path: Tracked certificate file path
                              (``-f`` argument).
            :param key_path: Tracked private-key file path
                             (``-k`` argument).
            :param ca: Optional certmonger CA short-name (e.g.
                       ``"IPA"`` or ``"local"``); passed via ``-c``.
            :param profile: Optional CA profile; passed via ``-T``.

            :returns: The :class:`CertIssuanceActionResult`.
        """
        cfg = self._typed_config()

        existing = self.list_certmonger()
        for entry in existing:
            if entry.cert_path != cert_path:
                continue
            same_subject = _subjects_match(
                tracked=entry.subject,
                requested=spec.subject.common_name,
            )
            same_sans = sorted(entry.sans) == sorted(spec.sans)
            same_ca = (ca is None) or (entry.ca == ca)
            if same_subject and same_sans and same_ca:
                skipped = self._build_result(
                    status=CertificateActionStatus.SKIPPED,
                    command=None,
                    detail="certmonger tracking already matches",
                    path=cert_path,
                )
                return skipped

        command: list[str] = [
            cfg.getcert_bin,
            "request",
            "-f", cert_path,
            "-k", key_path,
            "-r",
            "-w",
            "-N", spec.subject.common_name,
        ]
        for san in spec.sans:
            command.append("-D")
            command.append(san)
        if ca is not None:
            command.append("-c")
            command.append(ca)
        if profile is not None:
            command.append("-T")
            command.append(profile)

        rtnval = self._dispatch(
            command=command,
            detail="request_certmonger:{}".format(cert_path),
            operation="request_certmonger",
            path=cert_path,
        )
        return rtnval

    def stop_certmonger(self, cert_path: str) -> CertIssuanceActionResult:
        """
            Stop certmonger tracking for ``cert_path`` via
            ``getcert stop-tracking -f <cert_path>``.

            Returns ``SKIPPED`` when no tracking entry matches.

            :param cert_path: Tracked certificate file path.

            :returns: The :class:`CertIssuanceActionResult`.
        """
        cfg = self._typed_config()

        existing = self.list_certmonger()
        match_found = False
        for entry in existing:
            if entry.cert_path == cert_path:
                match_found = True
                break

        if not match_found:
            skipped = self._build_result(
                status=CertificateActionStatus.SKIPPED,
                command=None,
                detail="no certmonger tracking matches cert_path",
                path=cert_path,
            )
            return skipped

        command = [
            cfg.getcert_bin,
            "stop-tracking",
            "-f", cert_path,
        ]
        rtnval = self._dispatch(
            command=command,
            detail="stop_certmonger:{}".format(cert_path),
            operation="stop_certmonger",
            path=cert_path,
        )
        return rtnval

    def resubmit_certmonger(
        self,
        cert_path: str,
    ) -> CertIssuanceActionResult:
        """
            Resubmit (force-renew) a certmonger-tracked cert via
            ``getcert resubmit -f <cert_path>``.

            Returns ``SKIPPED`` when no tracking entry matches.

            :param cert_path: Tracked certificate file path.

            :returns: The :class:`CertIssuanceActionResult`.
        """
        cfg = self._typed_config()

        existing = self.list_certmonger()
        match_found = False
        for entry in existing:
            if entry.cert_path == cert_path:
                match_found = True
                break

        if not match_found:
            skipped = self._build_result(
                status=CertificateActionStatus.SKIPPED,
                command=None,
                detail="no certmonger tracking matches cert_path",
                path=cert_path,
            )
            return skipped

        command = [
            cfg.getcert_bin,
            "resubmit",
            "-f", cert_path,
        ]
        rtnval = self._dispatch(
            command=command,
            detail="resubmit_certmonger:{}".format(cert_path),
            operation="resubmit_certmonger",
            path=cert_path,
        )
        return rtnval

    # ------------------------------------------------------------------
    # ACME
    # ------------------------------------------------------------------

    def request_acme(
        self,
        spec: AcmeIssuanceSpec,
    ) -> CertIssuanceActionResult:
        """
            Request an ACME certificate via certbot.

            Builds the appropriate ``certbot certonly`` invocation
            based on ``spec.method``:

            * ``"webroot"``  → ``--webroot --webroot-path <path>``
            * ``"standalone"`` → ``--standalone``
            * ``"dns-<plugin>"`` → ``--<plugin> --<plugin>-credentials
              <path>``

            Returns ``SKIPPED`` when ``spec.cert_path`` is set, the
            existing certificate is present, and its remaining validity
            exceeds ``spec.renew_after_days`` (or
            ``config.renew_after_days_default``).

            :param spec: :class:`AcmeIssuanceSpec`.

            :returns: The :class:`CertIssuanceActionResult`.

            :raises AcmeRequiredArgumentError: When the spec is missing
                                                a method-required
                                                field.
        """
        cfg = self._typed_config()

        if spec.cert_path is not None:
            existing_info = self.inspect_certificate(path=spec.cert_path)
            if existing_info is not None:
                threshold = spec.renew_after_days
                if threshold is None:
                    threshold = cfg.renew_after_days_default
                parsed_dt = _parse_openssl_date(text=existing_info.not_after)
                if parsed_dt is not None:
                    delta = parsed_dt - datetime.now(tz=timezone.utc)
                    if delta.days > threshold:
                        skipped = self._build_result(
                            status=CertificateActionStatus.SKIPPED,
                            command=None,
                            detail=(
                                "existing cert valid for {} days "
                                "(> threshold {})".format(
                                    delta.days, threshold,
                                )
                            ),
                            path=spec.cert_path,
                        )
                        return skipped

        command: list[str] = [
            cfg.certbot_bin,
            "certonly",
        ]

        method = spec.method
        if method == "webroot":
            if spec.webroot_path is None:
                err_msg_lines = [
                    "ACME webroot method requires AcmeIssuanceSpec.webroot_path."
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise AcmeRequiredArgumentError(err_msg)
            command.append("--webroot")
            command.append("--webroot-path")
            command.append(spec.webroot_path)
        elif method == "standalone":
            command.append("--standalone")
        elif method.startswith("dns-"):
            if spec.plugin is None:
                err_msg_lines = [
                    "ACME dns-<plugin> method requires AcmeIssuanceSpec.plugin."
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise AcmeRequiredArgumentError(err_msg)
            command.append("--{}".format(spec.plugin))
            if spec.plugin_credentials_path is not None:
                command.append("--{}-credentials".format(spec.plugin))
                command.append(spec.plugin_credentials_path)
        else:
            err_msg_lines = [
                "Unsupported ACME method.",
            ]
            err_msg_lines.append("    METHOD: {}".format(method))
            err_msg = os.linesep.join(err_msg_lines)
            raise AcmeRequiredArgumentError(err_msg)

        for domain in spec.domains:
            command.append("-d")
            command.append(domain)

        command.append("--agree-tos")
        command.append("-m")
        command.append(spec.email)
        command.append("--non-interactive")

        if spec.server is not None:
            command.append("--server")
            command.append(spec.server)

        path_value: Optional[str] = spec.cert_path
        rtnval = self._dispatch(
            command=command,
            detail="request_acme:{}".format(spec.method),
            operation="request_acme",
            path=path_value,
        )
        return rtnval

    def renew_acme(
        self,
        cert_name: str,
        force: bool = False,
    ) -> CertIssuanceActionResult:
        """
            Renew an ACME certificate via ``certbot renew --cert-name
            <name>``.

            :param cert_name: Certbot ``--cert-name`` value.
            :param force: When ``True``, append ``--force-renewal``.

            :returns: The :class:`CertIssuanceActionResult`.
        """
        cfg = self._typed_config()
        command = [
            cfg.certbot_bin,
            "renew",
            "--cert-name", cert_name,
        ]
        if force:
            command.append("--force-renewal")

        rtnval = self._dispatch(
            command=command,
            detail="renew_acme:{}".format(cert_name),
            operation="renew_acme",
            path=None,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers — openssl req shared flow
    # ------------------------------------------------------------------

    def _openssl_req(
        self,
        *,
        spec: CsrSpec,
        credential,
        overwrite: bool,
        self_signed: bool,
        days: int,
        chmod_mode: Optional[int],
        output_path: str,
        operation: str,
    ) -> CertIssuanceActionResult:
        """
            Shared implementation for :meth:`generate_csr` and
            :meth:`generate_self_signed`.  Renders an OpenSSL config
            to a temp file, dispatches ``openssl req``, and removes
            the temp config in a ``finally`` block.

            :param spec: :class:`CsrSpec`.
            :param credential: Optional passphrase carrier.
            :param overwrite: Overwrite-policy flag.
            :param self_signed: When ``True`` add ``-x509`` and
                                ``-days``.
            :param days: Validity in days for self-signed certs.
            :param chmod_mode: Mode bits to chmod the output to on
                               success; ``None`` to skip.
            :param output_path: Destination path (CSR or cert).
            :param operation: Operation name for record emission.

            :returns: The :class:`CertIssuanceActionResult`.
        """
        cfg = self._typed_config()

        if not overwrite:
            if self._path_exists(path=output_path):
                skipped = self._build_result(
                    status=CertificateActionStatus.SKIPPED,
                    command=None,
                    detail="output already exists at path",
                    path=output_path,
                )
                return skipped

        if self._config.dry_run:
            sanitised_command = self._render_openssl_req_command(
                spec=spec,
                cfg=cfg,
                self_signed=self_signed,
                days=days,
                tmp_config_path="<dry-run-tmp>",
                with_passin=(credential is not None),
            )
            dry = self._build_result(
                status=CertificateActionStatus.DRY_RUN,
                command=list(sanitised_command),
                detail="{}:{}".format(operation, output_path),
                path=output_path,
            )
            return dry

        tmp_config_path = self._tmp_config_path(spec=spec)
        config_text = _render_openssl_config(spec=spec)

        write_command = [_TEE, tmp_config_path]
        write_result = self._runner.run(
            list(write_command),
            elevated=self._config.elevated,
            stdin=config_text,
        )
        if write_result.exit_code != 0:
            failed = self._build_result(
                status=CertificateActionStatus.FAILED,
                command=list(write_command),
                detail="failed to write temp openssl config",
                path=output_path,
                exit_code=write_result.exit_code,
                stdout=write_result.stdout,
                stderr=write_result.stderr,
            )
            return failed

        try:
            command = self._render_openssl_req_command(
                spec=spec,
                cfg=cfg,
                self_signed=self_signed,
                days=days,
                tmp_config_path=tmp_config_path,
                with_passin=(credential is not None),
            )

            stdin_payload: Optional[str] = None
            if credential is not None:
                secret_bytes = _resolve_credential_bytes(credential=credential)
                stdin_payload = secret_bytes.decode("utf-8")

            run_result = self._runner.run(
                list(command),
                elevated=self._config.elevated,
                stdin=stdin_payload,
            )

            if run_result.exit_code == 0:
                mapped_status = CertificateActionStatus.SUCCESS
            else:
                mapped_status = CertificateActionStatus.FAILED

            rtnval = self._wrap_result(
                status=mapped_status,
                detail="{}:{}".format(operation, output_path),
                exit_code=run_result.exit_code,
                stdout=run_result.stdout,
                stderr=run_result.stderr,
                command=list(command),
                path=output_path,
            )
            self._emit_record(result=rtnval, operation=operation)
        finally:
            cleanup_command = [_RM, "-f", tmp_config_path]
            self._runner.run(
                list(cleanup_command),
                elevated=self._config.elevated,
            )

        if rtnval.status == CertificateActionStatus.SUCCESS:
            if chmod_mode is not None:
                self._chmod(path=output_path, mode=chmod_mode)

        return rtnval

    def _render_openssl_req_command(
        self,
        *,
        spec: CsrSpec,
        cfg: CertificateIssuanceConfig,
        self_signed: bool,
        days: int,
        tmp_config_path: str,
        with_passin: bool,
    ) -> list[str]:
        """
            Build the ``openssl req`` argv (sanitised — never carries
            secret bytes).
        """
        rtnval: list[str] = [cfg.openssl_bin, "req", "-new"]
        if self_signed:
            rtnval.append("-x509")
            rtnval.append("-days")
            rtnval.append(str(days))
        rtnval.append("-key")
        rtnval.append(spec.key_path)
        rtnval.append("-out")
        rtnval.append(spec.csr_path)
        rtnval.append("-config")
        rtnval.append(tmp_config_path)
        if with_passin:
            rtnval.append("-passin")
            rtnval.append("stdin")
        return rtnval

    def _tmp_config_path(self, *, spec: CsrSpec) -> str:
        """
            Compute a stable per-spec temp-config path under
            ``config.tmp_dir``.
        """
        cfg = self._typed_config()
        slug_source = "{}::{}".format(spec.csr_path, spec.subject.common_name)
        slug = hashlib.sha256(slug_source.encode("utf-8")).hexdigest()[:16]
        rtnval = "{}/ccai_openssl_{}.cnf".format(cfg.tmp_dir, slug)
        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers — runner-routed file ops
    # ------------------------------------------------------------------

    def _typed_config(self) -> CertificateIssuanceConfig:
        """
            Narrow the base config to the typed subclass.
        """
        rtnval = self._config
        return rtnval

    def _path_exists(self, *, path: str) -> bool:
        """
            Test existence via ``test -e <path>``.

            :returns: ``True`` when ``test`` exits zero.
        """
        command = [_TEST, "-e", path]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code == 0:
            rtnval = True
            return rtnval
        rtnval = False
        return rtnval

    def _chmod(self, *, path: str, mode: int) -> None:
        """
            Apply mode bits via ``chmod <octal> <path>``.  No-op
            during dry-run; never raises.

            :param path: Target path.
            :param mode: Mode bits as an integer (e.g. ``0o600``).
        """
        if self._config.dry_run:
            return
        mode_text = format(mode, "o")
        command = [_CHMOD, mode_text, path]
        self._runner.run(
            list(command),
            elevated=self._config.elevated,
        )
        return

    # ------------------------------------------------------------------
    # Internal helpers — result construction
    # ------------------------------------------------------------------

    def _build_result(
        self,
        *,
        status: ActionStatus,
        command: Optional[list[str]],
        detail: Optional[str] = None,
        path: Optional[str] = None,
        exit_code: Optional[int] = None,
        stdout: Optional[str] = None,
        stderr: Optional[str] = None,
    ) -> CertIssuanceActionResult:
        """
            Construct a :class:`CertIssuanceActionResult` without
            invoking the runner.
        """
        if command is None:
            command_copy: Optional[list[str]] = None
        else:
            command_copy = list(command)
        rtnval = CertIssuanceActionResult(
            status=status,
            detail=detail,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            command=command_copy,
            path=path,
        )
        return rtnval


# ----------------------------------------------------------------------
# Module-level helpers — credential resolution
# ----------------------------------------------------------------------


def _resolve_credential_bytes(*, credential) -> bytes:
    """
        Extract secret bytes from a credential-shaped object.

        Accepts (in order):
        1. An object exposing ``secret_bytes()`` returning ``bytes``.
        2. An object exposing ``password`` (``str`` or ``bytes``).
        3. A bare ``bytes`` value.
        4. A bare ``str`` value.

        :param credential: The credential-shaped value.

        :returns: ``bytes`` carrying the secret payload.

        :raises CertificateCredentialError: When no secret bytes can
                                             be resolved.
    """
    if hasattr(credential, "secret_bytes"):
        accessor = credential.secret_bytes
        if callable(accessor):
            payload = accessor()
        else:
            payload = accessor
        if isinstance(payload, bytes):
            rtnval = payload
            return rtnval
        if isinstance(payload, str):
            rtnval = payload.encode("utf-8")
            return rtnval

    if hasattr(credential, "password"):
        password_value = credential.password
        if isinstance(password_value, bytes):
            rtnval = password_value
            return rtnval
        if isinstance(password_value, str):
            rtnval = password_value.encode("utf-8")
            return rtnval

    if isinstance(credential, bytes):
        rtnval = credential
        return rtnval
    if isinstance(credential, str):
        rtnval = credential.encode("utf-8")
        return rtnval

    err_msg_lines = ["Unable to resolve secret bytes from credential."]
    err_msg_lines.append(
        "    CREDENTIAL_TYPE: {}".format(type(credential).__name__),
    )
    err_msg = os.linesep.join(err_msg_lines)
    raise CertificateCredentialError(err_msg)


# ----------------------------------------------------------------------
# Module-level helpers — OpenSSL config rendering
# ----------------------------------------------------------------------


def _render_openssl_config(*, spec: CsrSpec) -> str:
    """
        Render a self-contained OpenSSL config covering ``[req]``,
        ``[req_distinguished_name]``, ``[v3_req]``, and ``[alt_names]``
        for the supplied :class:`CsrSpec`.

        :param spec: The :class:`CsrSpec`.

        :returns: Rendered config text.
    """
    lines: list[str] = []
    lines.append("[req]")
    lines.append("distinguished_name = req_distinguished_name")
    lines.append("req_extensions = v3_req")
    lines.append("prompt = no")
    lines.append("")

    lines.append("[req_distinguished_name]")
    subj = spec.subject
    if subj.country is not None:
        lines.append("C  = {}".format(subj.country))
    if subj.state is not None:
        lines.append("ST = {}".format(subj.state))
    if subj.locality is not None:
        lines.append("L  = {}".format(subj.locality))
    if subj.organization is not None:
        lines.append("O  = {}".format(subj.organization))
    if subj.organizational_unit is not None:
        lines.append("OU = {}".format(subj.organizational_unit))
    lines.append("CN = {}".format(subj.common_name))
    if subj.email is not None:
        lines.append("emailAddress = {}".format(subj.email))
    lines.append("")

    lines.append("[v3_req]")
    if len(spec.key_usages) > 0:
        lines.append("keyUsage = {}".format(", ".join(spec.key_usages)))
    if len(spec.extended_key_usages) > 0:
        lines.append(
            "extendedKeyUsage = {}".format(
                ", ".join(spec.extended_key_usages),
            ),
        )
    if len(spec.sans) > 0:
        lines.append("subjectAltName = @alt_names")
    lines.append("")

    if len(spec.sans) > 0:
        lines.append("[alt_names]")
        dns_index = 1
        ip_index = 1
        uri_index = 1
        email_index = 1
        for entry in spec.sans:
            if entry.startswith("IP:"):
                lines.append("IP.{} = {}".format(ip_index, entry[len("IP:"):]))
                ip_index += 1
            elif entry.startswith("URI:"):
                lines.append("URI.{} = {}".format(uri_index, entry[len("URI:"):]))
                uri_index += 1
            elif entry.startswith("email:"):
                lines.append(
                    "email.{} = {}".format(email_index, entry[len("email:"):]),
                )
                email_index += 1
            else:
                lines.append("DNS.{} = {}".format(dns_index, entry))
                dns_index += 1
        lines.append("")

    rtnval = "\n".join(lines)
    return rtnval


# ----------------------------------------------------------------------
# Module-level helpers — openssl x509 / req text parsing
# ----------------------------------------------------------------------


def _parse_openssl_x509_text(*, stdout: str) -> dict:
    """
        Parse the output of ``openssl x509 -noout -text``.

        :param stdout: Captured stdout.

        :returns: A ``dict`` with keys ``subject``, ``issuer``,
                  ``serial_hex``, ``not_before``, ``not_after``,
                  ``signature_algorithm``, ``public_key_algorithm``,
                  ``public_key_size``, ``sans``, ``key_usages``,
                  ``extended_key_usages``.
    """
    rtnval: dict = {
        "subject": "",
        "issuer": "",
        "serial_hex": "",
        "not_before": "",
        "not_after": "",
        "signature_algorithm": "",
        "public_key_algorithm": "",
        "public_key_size": None,
        "sans": [],
        "key_usages": [],
        "extended_key_usages": [],
    }

    in_san = False
    in_ku = False
    in_eku = False
    serial_pending = False
    sig_algo_seen = False

    for raw in stdout.splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if stripped.startswith("Issuer:"):
            rtnval["issuer"] = stripped[len("Issuer:"):].strip()
            in_san = False
            in_ku = False
            in_eku = False
            continue
        if stripped.startswith("Subject:"):
            if not stripped.startswith("Subject: "):
                # "Subject Public Key Info:" — different block
                if "Public Key Info" not in stripped:
                    rtnval["subject"] = stripped[len("Subject:"):].strip()
            else:
                rtnval["subject"] = stripped[len("Subject:"):].strip()
            in_san = False
            in_ku = False
            in_eku = False
            continue

        if stripped.startswith("Serial Number:"):
            tail = stripped[len("Serial Number:"):].strip()
            if len(tail) > 0:
                rtnval["serial_hex"] = tail
            else:
                serial_pending = True
            continue
        if serial_pending:
            if len(stripped) > 0:
                rtnval["serial_hex"] = stripped
                serial_pending = False
            continue

        if stripped.startswith("Not Before:"):
            rtnval["not_before"] = stripped[len("Not Before:"):].strip()
            continue
        if stripped.startswith("Not After :"):
            rtnval["not_after"] = stripped[len("Not After :"):].strip()
            continue
        if stripped.startswith("Not After:"):
            rtnval["not_after"] = stripped[len("Not After:"):].strip()
            continue

        if stripped.startswith("Signature Algorithm:"):
            algo_text = stripped[len("Signature Algorithm:"):].strip()
            if not sig_algo_seen:
                rtnval["signature_algorithm"] = algo_text
                sig_algo_seen = True
            continue

        if stripped.startswith("Public Key Algorithm:"):
            algo_text = stripped[len("Public Key Algorithm:"):].strip()
            if "rsaEncryption" in algo_text:
                rtnval["public_key_algorithm"] = "RSA"
            elif "id-ecPublicKey" in algo_text:
                rtnval["public_key_algorithm"] = "EC"
            else:
                rtnval["public_key_algorithm"] = algo_text
            continue

        if stripped.startswith("Public-Key:"):
            tail = stripped[len("Public-Key:"):].strip()
            digits: list[str] = []
            for ch in tail:
                if ch.isdigit():
                    digits.append(ch)
                elif len(digits) > 0:
                    break
            if len(digits) > 0:
                try:
                    rtnval["public_key_size"] = int("".join(digits))
                except (ValueError, Exception):
                    rtnval["public_key_size"] = None
            continue

        if "X509v3 Subject Alternative Name" in stripped:
            in_san = True
            in_ku = False
            in_eku = False
            continue
        if "X509v3 Key Usage" in stripped:
            in_ku = True
            in_san = False
            in_eku = False
            continue
        if "X509v3 Extended Key Usage" in stripped:
            in_eku = True
            in_san = False
            in_ku = False
            continue

        if in_san:
            if len(stripped) == 0:
                in_san = False
                continue
            for chunk in stripped.split(","):
                chunk_clean = chunk.strip()
                if len(chunk_clean) == 0:
                    continue
                if chunk_clean.startswith("DNS:"):
                    rtnval["sans"].append(chunk_clean[len("DNS:"):].strip())
                elif chunk_clean.startswith("IP Address:"):
                    rtnval["sans"].append(chunk_clean[len("IP Address:"):].strip())
                elif chunk_clean.startswith("URI:"):
                    rtnval["sans"].append(chunk_clean[len("URI:"):].strip())
                elif chunk_clean.startswith("email:"):
                    rtnval["sans"].append(chunk_clean[len("email:"):].strip())
                else:
                    rtnval["sans"].append(chunk_clean)
            in_san = False
            continue

        if in_ku:
            if len(stripped) == 0:
                in_ku = False
                continue
            for chunk in stripped.split(","):
                chunk_clean = chunk.strip()
                if len(chunk_clean) > 0:
                    rtnval["key_usages"].append(chunk_clean)
            in_ku = False
            continue

        if in_eku:
            if len(stripped) == 0:
                in_eku = False
                continue
            for chunk in stripped.split(","):
                chunk_clean = chunk.strip()
                if len(chunk_clean) > 0:
                    rtnval["extended_key_usages"].append(chunk_clean)
            in_eku = False
            continue

    return rtnval


def _parse_openssl_req_text(*, stdout: str) -> dict:
    """
        Parse the output of ``openssl req -in <path> -noout -text``.

        :param stdout: Captured stdout.

        :returns: A ``dict`` with keys ``subject``,
                  ``public_key_algorithm``, ``sans``.
    """
    rtnval: dict = {
        "subject": "",
        "public_key_algorithm": "",
        "sans": [],
    }

    in_san = False

    for raw in stdout.splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if stripped.startswith("Subject:"):
            if "Public Key" not in stripped:
                rtnval["subject"] = stripped[len("Subject:"):].strip()
            in_san = False
            continue

        if stripped.startswith("Public Key Algorithm:"):
            algo_text = stripped[len("Public Key Algorithm:"):].strip()
            if "rsaEncryption" in algo_text:
                rtnval["public_key_algorithm"] = "RSA"
            elif "id-ecPublicKey" in algo_text:
                rtnval["public_key_algorithm"] = "EC"
            else:
                rtnval["public_key_algorithm"] = algo_text
            continue

        if "X509v3 Subject Alternative Name" in stripped:
            in_san = True
            continue
        if "DNS:" in stripped and in_san is False:
            # Some openssl req -text builds emit "DNS:foo, DNS:bar"
            # directly under "Requested Extensions" without a heading
            # match line; tolerate that form.
            if "Subject Alternative Name" in stripped:
                in_san = True
                continue

        if in_san:
            if len(stripped) == 0:
                in_san = False
                continue
            for chunk in stripped.split(","):
                chunk_clean = chunk.strip()
                if len(chunk_clean) == 0:
                    continue
                if chunk_clean.startswith("DNS:"):
                    rtnval["sans"].append(chunk_clean[len("DNS:"):].strip())
                elif chunk_clean.startswith("IP Address:"):
                    rtnval["sans"].append(chunk_clean[len("IP Address:"):].strip())
                elif chunk_clean.startswith("URI:"):
                    rtnval["sans"].append(chunk_clean[len("URI:"):].strip())
                elif chunk_clean.startswith("email:"):
                    rtnval["sans"].append(chunk_clean[len("email:"):].strip())
                else:
                    rtnval["sans"].append(chunk_clean)
            in_san = False

    return rtnval


def _parse_fingerprint_line(*, stdout: str) -> str:
    """
        Parse the SHA-256 fingerprint line from
        ``openssl x509 -noout -fingerprint -sha256`` output.

        :param stdout: Captured stdout.

        :returns: The fingerprint string (uppercase colon-delimited),
                  or empty string when not found.
    """
    for raw in stdout.splitlines():
        stripped = raw.strip()
        lower = stripped.lower()
        if lower.startswith("sha256 fingerprint="):
            rtnval = stripped.split("=", 1)[1].strip()
            return rtnval
    rtnval = ""
    return rtnval


# ----------------------------------------------------------------------
# Module-level helpers — getcert list parsing
# ----------------------------------------------------------------------


def _parse_getcert_list(*, stdout: str) -> list[CertmongerTracking]:
    """
        Parse the output of ``getcert list`` into a list of
        :class:`CertmongerTracking`.

        ``getcert list`` emits one block per request, separated by
        blank lines.  Each block contains lines like::

            Request ID '20240101000000':
                status: MONITORING
                stuck: no
                key pair storage: type=FILE,location='/etc/pki/tls/private/foo.key'
                certificate: type=FILE,location='/etc/pki/tls/certs/foo.pem'
                CA: local
                issuer: CN=Local CA
                subject: CN=foo.example.com
                expires: 2025-01-01 00:00:00 UTC
                dns: foo.example.com,bar.example.com

        :param stdout: Captured stdout.

        :returns: A ``list[CertmongerTracking]``.
    """
    rtnval: list[CertmongerTracking] = []
    current: dict = _empty_certmonger_block()
    in_block = False

    for raw in stdout.splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if stripped.startswith("Request ID"):
            if in_block:
                flushed = _flush_certmonger_block(block=current)
                if flushed is not None:
                    rtnval.append(flushed)
            current = _empty_certmonger_block()
            in_block = True
            tail = stripped[len("Request ID"):].strip()
            if tail.startswith("'") and tail.endswith("':"):
                current["request_id"] = tail[1:-2]
            elif tail.startswith("'"):
                end = tail.find("'", 1)
                if end > 0:
                    current["request_id"] = tail[1:end]
            continue

        if not in_block:
            continue

        if stripped.startswith("status:"):
            current["status"] = stripped[len("status:"):].strip()
            continue
        if stripped.startswith("CA:"):
            current["ca"] = stripped[len("CA:"):].strip()
            continue
        if stripped.startswith("subject:"):
            current["subject"] = stripped[len("subject:"):].strip()
            continue
        if stripped.startswith("expires:"):
            current["expires"] = stripped[len("expires:"):].strip()
            continue
        if stripped.startswith("certificate:"):
            tail = stripped[len("certificate:"):].strip()
            current["cert_path"] = _extract_location(tail=tail)
            continue
        if stripped.startswith("key pair storage:"):
            tail = stripped[len("key pair storage:"):].strip()
            current["key_path"] = _extract_location(tail=tail)
            continue
        if stripped.startswith("dns:"):
            tail = stripped[len("dns:"):].strip()
            for chunk in tail.split(","):
                chunk_clean = chunk.strip()
                if len(chunk_clean) > 0:
                    current["sans"].append(chunk_clean)
            continue

    if in_block:
        flushed = _flush_certmonger_block(block=current)
        if flushed is not None:
            rtnval.append(flushed)

    return rtnval


def _empty_certmonger_block() -> dict:
    """
        Build an empty parser-state dict for a certmonger block.
    """
    rtnval: dict = {
        "request_id": "",
        "status": "",
        "ca": "",
        "cert_path": "",
        "key_path": "",
        "subject": None,
        "sans": [],
        "expires": None,
    }
    return rtnval


def _flush_certmonger_block(*, block: dict) -> Optional[CertmongerTracking]:
    """
        Convert a parser-state dict into a :class:`CertmongerTracking`,
        returning ``None`` when the block is empty.
    """
    if len(block.get("request_id", "")) == 0:
        rtnval: Optional[CertmongerTracking] = None
        return rtnval

    rtnval = CertmongerTracking(
        request_id=block["request_id"],
        status=block.get("status", ""),
        ca=block.get("ca", ""),
        cert_path=block.get("cert_path", ""),
        key_path=block.get("key_path", ""),
        subject=block.get("subject"),
        sans=list(block.get("sans", [])),
        expires=block.get("expires"),
    )
    return rtnval


def _extract_location(*, tail: str) -> str:
    """
        Extract the ``location='<path>'`` value from a getcert
        ``certificate:`` / ``key pair storage:`` line tail.
    """
    marker = "location="
    idx = tail.find(marker)
    if idx < 0:
        rtnval = ""
        return rtnval
    after = tail[idx + len(marker):]
    if after.startswith("'"):
        end = after.find("'", 1)
        if end > 0:
            rtnval = after[1:end]
            return rtnval
    end_comma = after.find(",")
    if end_comma > 0:
        rtnval = after[:end_comma].strip()
        return rtnval
    rtnval = after.strip()
    return rtnval


def _subjects_match(*, tracked: Optional[str], requested: str) -> bool:
    """
        Compare a tracked ``subject:`` string from ``getcert list``
        with a requested CN.  Tolerant of ``"CN=foo"`` vs ``"foo"``.
    """
    if tracked is None:
        rtnval = False
        return rtnval
    if tracked == requested:
        rtnval = True
        return rtnval
    cn_marker = "CN="
    idx = tracked.find(cn_marker)
    if idx >= 0:
        tail = tracked[idx + len(cn_marker):]
        comma = tail.find(",")
        if comma >= 0:
            cn_value = tail[:comma].strip()
        else:
            cn_value = tail.strip()
        if cn_value == requested:
            rtnval = True
            return rtnval
    rtnval = False
    return rtnval


# ----------------------------------------------------------------------
# Module-level helpers — date parsing / PEM utilities
# ----------------------------------------------------------------------


def _parse_openssl_date(*, text: str) -> Optional[datetime]:
    """
        Parse an OpenSSL ``Not After``-style date string into a
        UTC-aware :class:`datetime`.

        Accepted formats include::

            "Jan  1 00:00:00 2030 GMT"
            "Jan 01 00:00:00 2030 GMT"
            "2030-01-01 00:00:00 UTC"
            ISO-8601 ("2030-01-01T00:00:00Z")

        :param text: The raw date string.

        :returns: A :class:`datetime` (UTC), or ``None`` on parse
                  failure.
    """
    if len(text) == 0:
        rtnval: Optional[datetime] = None
        return rtnval

    formats = [
        "%b %d %H:%M:%S %Y %Z",
        "%b  %d %H:%M:%S %Y %Z",
        "%b %d %H:%M:%S %Y",
        "%Y-%m-%d %H:%M:%S %Z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
    ]
    parsed: Optional[datetime] = None
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            break
        except (ValueError, Exception):
            continue

    if parsed is None:
        rtnval = None
        return rtnval

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    rtnval = parsed
    return rtnval


def _parse_pem_blocks(*, content: str) -> list[str]:
    """
        Split a PEM-bearing string into a list of CERTIFICATE blocks.
        Helper retained for future bundling work.

        :param content: Raw file content.

        :returns: A list of full PEM envelopes.
    """
    rtnval: list[str] = []
    in_block = False
    current_lines: list[str] = []

    for raw in content.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped == _PEM_BEGIN:
            in_block = True
            current_lines = [line]
            continue
        if stripped == _PEM_END:
            if in_block:
                current_lines.append(line)
                block_text = "\n".join(current_lines) + "\n"
                rtnval.append(block_text)
            in_block = False
            current_lines = []
            continue
        if in_block:
            current_lines.append(line)

    return rtnval


def _compute_fingerprint(*, pem: str) -> Optional[str]:
    """
        Compute the SHA-256 fingerprint of a PEM CERTIFICATE block.

        :param pem: The full PEM envelope.

        :returns: Uppercase colon-delimited fingerprint, or ``None``
                  when the body fails to decode.
    """
    body_lines: list[str] = []
    in_body = False
    for raw in pem.splitlines():
        stripped = raw.strip()
        if stripped == _PEM_BEGIN:
            in_body = True
            continue
        if stripped == _PEM_END:
            in_body = False
            continue
        if in_body:
            body_lines.append(stripped)

    body = "".join(body_lines)
    if len(body) == 0:
        rtnval: Optional[str] = None
        return rtnval

    try:
        decoded = base64.b64decode(body, validate=False)
    except (ValueError, Exception):
        rtnval = None
        return rtnval

    if len(decoded) == 0:
        rtnval = None
        return rtnval

    digest = hashlib.sha256(decoded).digest()
    hex_text = digest.hex().upper()

    chunks: list[str] = []
    cursor = 0
    while cursor < len(hex_text):
        chunks.append(hex_text[cursor:cursor + 2])
        cursor += 2
    rtnval = ":".join(chunks)
    return rtnval
