# automojo.sysman.linux.redhat.certificatetrustmanagement
#
# CertificateTrustManager wraps the Red Hat CA-trust toolchain
# (``update-ca-trust``, ``openssl``, ``trust``) plus filesystem operations
# under ``/etc/pki/ca-trust/source/anchors/`` and
# ``/etc/pki/ca-trust/source/blacklist/`` through the injected
# :class:`ICommandRunner`.
#
# All filesystem reads and writes are routed through the runner using
# ``cat`` / ``ls`` / ``tee`` / ``rm`` so the manager works identically
# under local and SSH execution.  See
# specs/3-certificate-trust-management/design.md for the full surface
# and the cross-OS contract in
# automojo-sysman-common/specs/contracts/certificate-trust-management/.

import base64
import hashlib
import os

from dataclasses import dataclass, field
from typing import Optional

from automojo.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


# ----------------------------------------------------------------------
# Module-level constants
# ----------------------------------------------------------------------

_CAT = "cat"
_TEE = "tee"
_LS = "ls"
_RM = "rm"
_OPENSSL = "openssl"
_UPDATE_CA_TRUST = "update-ca-trust"

_DEFAULT_ANCHORS_DIR = "/etc/pki/ca-trust/source/anchors"
_DEFAULT_BLOCKLIST_DIR = "/etc/pki/ca-trust/source/blacklist"

_PEM_BEGIN = "-----BEGIN CERTIFICATE-----"
_PEM_END = "-----END CERTIFICATE-----"


CertTrustActionStatus = ActionStatus


# ----------------------------------------------------------------------
# Config / data models
# ----------------------------------------------------------------------


@dataclass
class CertificateTrustManagerConfig(FeatureConfig):
    """
        Configuration for :class:`CertificateTrustManager`.  Inherits
        ``dry_run`` and ``elevated`` from :class:`FeatureConfig`.

        :param anchors_dir: Filesystem directory used for trust anchors.
                            Defaults to
                            ``/etc/pki/ca-trust/source/anchors``.
        :param blocklist_dir: Filesystem directory used for blocklist
                              (distrust) anchors.  Defaults to
                              ``/etc/pki/ca-trust/source/blacklist``.
        :param openssl_executable: Path to the ``openssl`` executable.
                                   Defaults to ``"openssl"`` (resolved
                                   through the runner's PATH).
        :param update_executable: Path to ``update-ca-trust``.  Defaults
                                  to ``"update-ca-trust"``.
    """
    anchors_dir: str = _DEFAULT_ANCHORS_DIR
    blocklist_dir: str = _DEFAULT_BLOCKLIST_DIR
    openssl_executable: str = _OPENSSL
    update_executable: str = _UPDATE_CA_TRUST


@dataclass
class CertificateTrustActionResult(FeatureActionResult):
    """
        Outcome of a certificate-trust-management write operation.

        :param name: The anchor short-name used by the caller.
        :param path: The on-disk path that was acted upon.
        :param subject: Subject DN of the certificate, when known.
        :param sha256_fingerprint: SHA-256 fingerprint (uppercase
                                   colon-delimited bytes) of the
                                   certificate, when known.
        :param error: Free-form error string for FAILED results that did
                      not originate from the runner (e.g. PEM-validation
                      failures).
        :param note: Human-readable hint such as
                     ``"call refresh_trust_store() for changes to take
                     effect"``.
    """
    name: Optional[str] = None
    path: Optional[str] = None
    subject: Optional[str] = None
    sha256_fingerprint: Optional[str] = None
    error: Optional[str] = None
    note: Optional[str] = None


@dataclass
class TrustAnchor:
    """
        Structured metadata for a single installed trust anchor.

        :param name: The on-disk filename (no directory component).
        :param path: The absolute on-disk path.
        :param subject: Subject DN as emitted by ``openssl x509
                        -subject``.  Empty when not parseable.
        :param issuer: Issuer DN as emitted by ``openssl x509 -issuer``.
                       Empty when not parseable.
        :param not_before: Validity start as emitted by
                           ``openssl x509 -startdate``.  Empty when not
                           parseable.
        :param not_after: Validity end as emitted by
                          ``openssl x509 -enddate``.  Empty when not
                          parseable.
        :param sha256_fingerprint: SHA-256 fingerprint in the canonical
                                   uppercase colon-delimited form.
        :param valid: True when the file parsed as a PEM CERTIFICATE
                      and a fingerprint was computed; False otherwise.
        :param error: Captured error text when ``valid`` is False;
                      empty string otherwise.
    """
    name: str
    path: str
    subject: str
    issuer: str
    not_before: str
    not_after: str
    sha256_fingerprint: str
    valid: bool
    error: str


@dataclass
class CertMetadata:
    """
        Structured metadata for an arbitrary certificate file.

        :param subject: Subject DN.
        :param issuer: Issuer DN.
        :param serial: Hex serial.
        :param not_before: Validity start (openssl format).
        :param not_after: Validity end (openssl format).
        :param sha256_fingerprint: SHA-256 fingerprint
                                   (uppercase colon-delimited).
        :param sans: List of subject alternative-name entries.
    """
    subject: str
    issuer: str
    serial: str
    not_before: str
    not_after: str
    sha256_fingerprint: str
    sans: list = field(default_factory=list)


@dataclass
class CertVerifyResult:
    """
        Outcome of an ``openssl verify`` invocation.

        :param ok: True when the verifier exited zero.
        :param output: Captured combined stdout/stderr output.
    """
    ok: bool
    output: str


@dataclass
class TrustStoreState:
    """
        Outcome of an ``update-ca-trust check`` invocation.

        :param enabled: True when the consolidated store is enabled.
        :param raw_status: The raw captured stdout, for diagnostics.
    """
    enabled: bool
    raw_status: str


# ----------------------------------------------------------------------
# Exceptions
# ----------------------------------------------------------------------


class CertTrustQueryError(Exception):
    """
        Raised when a certificate-trust read operation fails for a
        reason other than a missing file.
    """

    def __init__(
        self,
        *,
        operation: str,
        message: str,
    ) -> None:
        """
            :param operation: The verb that triggered the failure.
            :param message: Human-readable detail.
        """
        self.operation = operation
        self.message = message
        err_msg_lines = ["Certificate trust query command failed."]
        err_msg_lines.append("    OPERATION: {}".format(operation))
        err_msg_lines.append("    DETAIL: {}".format(message))
        err_msg = os.linesep.join(err_msg_lines)
        super().__init__(err_msg)
        return


class CertTrustPathNotFoundError(Exception):
    """
        Raised when a read operation is invoked with a path that does
        not exist on disk.
    """

    def __init__(
        self,
        *,
        operation: str,
        path: str,
    ) -> None:
        """
            :param operation: The verb that triggered the failure.
            :param path: The path that was requested.
        """
        self.operation = operation
        self.path = path
        err_msg_lines = ["Certificate trust path not found."]
        err_msg_lines.append("    OPERATION: {}".format(operation))
        err_msg_lines.append("    PATH: {}".format(path))
        err_msg = os.linesep.join(err_msg_lines)
        super().__init__(err_msg)
        return


# ----------------------------------------------------------------------
# Manager
# ----------------------------------------------------------------------


class CertificateTrustManager(BaseFeatureManager):
    """
        Facade for the RHEL system-wide CA trust store.  Wraps the
        ``update-ca-trust`` toolchain plus ``openssl`` parsing and
        ``cat`` / ``ls`` / ``tee`` / ``rm`` file-ops dispatched through
        the injected :class:`ICommandRunner`.

        This manager does not implicitly invoke
        ``update-ca-trust extract`` after :meth:`install_anchor` or
        :meth:`remove_anchor` — callers must call
        :meth:`refresh_trust_store` explicitly.  This matches the
        cross-OS contract's "Refresh coupling" invariant.
    """

    result_class = CertificateTrustActionResult
    feature_name = "certificate-trust-management"

    # ------------------------------------------------------------------
    # Anchor write verbs
    # ------------------------------------------------------------------

    def install_anchor(
        self,
        name: str,
        source_path: str,
    ) -> CertificateTrustActionResult:
        """
            Install a PEM-encoded certificate as a trust anchor.

            Reads ``source_path`` via ``cat``, validates it as a PEM
            certificate via ``openssl x509 -noout -in <path>``, computes
            the SHA-256 fingerprint in pure Python, and writes the
            payload to ``<anchors_dir>/<name>.pem`` via ``tee``.

            Idempotency: when the destination already exists with
            byte-identical content, returns ``SKIPPED`` without
            invoking ``tee``.

            :param name: The anchor short-name.  ``.pem`` is appended
                         when not already present.
            :param source_path: Absolute path to the source PEM file.

            :returns: The :class:`CertificateTrustActionResult`.
        """
        rtnval = self._install_to_directory(
            name=name,
            source_path=source_path,
            store_dir=self._typed_config().anchors_dir,
        )
        return rtnval

    def remove_anchor(self, name: str) -> CertificateTrustActionResult:
        """
            Remove an installed trust anchor by name.

            Idempotency: returns ``SKIPPED`` when the destination file
            does not exist on disk.

            :param name: The anchor short-name.

            :returns: The :class:`CertificateTrustActionResult`.
        """
        rtnval = self._remove_from_directory(
            name=name,
            store_dir=self._typed_config().anchors_dir,
        )
        return rtnval

    def install_blocklist_anchor(
        self,
        name: str,
        source_path: str,
    ) -> CertificateTrustActionResult:
        """
            Install a PEM certificate into the system blocklist store.

            Mirrors :meth:`install_anchor` but writes under
            ``<blocklist_dir>/<name>.pem``.

            :param name: The anchor short-name.
            :param source_path: Absolute path to the source PEM file.

            :returns: The :class:`CertificateTrustActionResult`.
        """
        rtnval = self._install_to_directory(
            name=name,
            source_path=source_path,
            store_dir=self._typed_config().blocklist_dir,
        )
        return rtnval

    def remove_blocklist_anchor(
        self,
        name: str,
    ) -> CertificateTrustActionResult:
        """
            Remove a blocklist anchor by name.  Mirrors
            :meth:`remove_anchor`.

            :param name: The anchor short-name.

            :returns: The :class:`CertificateTrustActionResult`.
        """
        rtnval = self._remove_from_directory(
            name=name,
            store_dir=self._typed_config().blocklist_dir,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Trust-store-level write verbs
    # ------------------------------------------------------------------

    def refresh_trust_store(
        self,
        extract: bool = True,
    ) -> CertificateTrustActionResult:
        """
            Rebuild the consolidated trust bundle.

            Dispatches ``update-ca-trust extract`` by default, or the
            bare ``update-ca-trust`` form when ``extract=False``.

            :param extract: When True, append the ``extract``
                            sub-command.  When False, dispatch
                            ``update-ca-trust`` without arguments.

            :returns: The :class:`CertificateTrustActionResult`.
        """
        executable = self._typed_config().update_executable
        if extract:
            command = [executable, "extract"]
        else:
            command = [executable]
        rtnval = self._dispatch(
            command=command,
            operation="refresh_trust_store",
        )
        return rtnval

    def enable_trust_store(self) -> CertificateTrustActionResult:
        """
            Enable the consolidated trust store via
            ``update-ca-trust enable``.

            Idempotency: queries the current state via
            :meth:`query_trust_state`; returns ``SKIPPED`` when already
            enabled.

            :returns: The :class:`CertificateTrustActionResult`.
        """
        rtnval = self._toggle_trust_store(
            desired_enabled=True,
            sub_command="enable",
            operation="enable_trust_store",
        )
        return rtnval

    def disable_trust_store(self) -> CertificateTrustActionResult:
        """
            Disable the consolidated trust store via
            ``update-ca-trust disable``.

            Idempotency: returns ``SKIPPED`` when already disabled.

            :returns: The :class:`CertificateTrustActionResult`.
        """
        rtnval = self._toggle_trust_store(
            desired_enabled=False,
            sub_command="disable",
            operation="disable_trust_store",
        )
        return rtnval

    # ------------------------------------------------------------------
    # Read verbs
    # ------------------------------------------------------------------

    def list_anchors(self) -> list:
        """
            Enumerate every installed trust anchor.

            Lists ``<anchors_dir>`` via ``ls -1``, reads each entry via
            ``cat``, and parses metadata via
            ``openssl x509 -noout -subject -issuer -dates``.

            Read-only: never elevates.

            :returns: A ``list[TrustAnchor]``.  Empty when the directory
                      does not exist.
        """
        anchors_dir = self._typed_config().anchors_dir
        rtnval: list = []
        entries = self._list_dir(path=anchors_dir)
        for entry in entries:
            full_path = "{}/{}".format(anchors_dir, entry)
            anchor = self._read_anchor(name=entry, path=full_path)
            rtnval.append(anchor)
        return rtnval

    def query_trust_state(self) -> TrustStoreState:
        """
            Query whether the consolidated trust store is enabled.

            Dispatches ``update-ca-trust check`` and parses the captured
            stdout.  Per Property 5, the runner is invoked on every
            call (no caching).

            :returns: The :class:`TrustStoreState`.

            :raises CertTrustQueryError: When the underlying command
                                          returns an unexpected exit
                                          code.
        """
        executable = self._typed_config().update_executable
        command = [executable, "check"]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            raise CertTrustQueryError(
                operation="query_trust_state",
                message=run_result.stderr,
            )
        enabled = _parse_trust_check_output(stdout=run_result.stdout)
        rtnval = TrustStoreState(
            enabled=enabled,
            raw_status=run_result.stdout,
        )
        return rtnval

    def verify_certificate(
        self,
        cert_path: str,
        ca_file: Optional[str] = None,
        untrusted: Optional[str] = None,
    ) -> CertVerifyResult:
        """
            Verify a certificate chain against the system trust store
            or an explicit CA file.

            Per Property 6, a non-zero verifier exit code returns
            ``ok=False`` rather than raising.  Only a missing input
            file raises :class:`CertTrustPathNotFoundError`.

            :param cert_path: Path to the certificate to verify.
            :param ca_file: Optional explicit CA file; passed via
                            ``-CAfile``.
            :param untrusted: Optional explicit untrusted-chain file;
                              passed via ``-untrusted``.

            :returns: The :class:`CertVerifyResult`.

            :raises CertTrustPathNotFoundError: When ``cert_path`` does
                                                 not exist.
        """
        exists = self._path_exists(path=cert_path)
        if not exists:
            raise CertTrustPathNotFoundError(
                operation="verify_certificate",
                path=cert_path,
            )

        executable = self._typed_config().openssl_executable
        command: list = [executable, "verify"]
        if ca_file is not None:
            command.append("-CAfile")
            command.append(ca_file)
        if untrusted is not None:
            command.append("-untrusted")
            command.append(untrusted)
        command.append(cert_path)

        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code == 0:
            ok = True
        else:
            ok = False
        combined_lines: list = []
        if len(run_result.stdout) > 0:
            combined_lines.append(run_result.stdout)
        if len(run_result.stderr) > 0:
            combined_lines.append(run_result.stderr)
        output_text = "".join(combined_lines)
        rtnval = CertVerifyResult(ok=ok, output=output_text)
        return rtnval

    def get_certificate_metadata(self, cert_path: str) -> CertMetadata:
        """
            Read structured metadata for a certificate file.

            Dispatches ``openssl x509 -noout -subject -issuer -serial
            -startdate -enddate -fingerprint -sha256 -ext
            subjectAltName -in <path>`` and parses the captured output.

            :param cert_path: Path to the certificate file.

            :returns: The :class:`CertMetadata`.

            :raises CertTrustPathNotFoundError: When ``cert_path`` does
                                                 not exist.
            :raises CertTrustQueryError: When ``openssl`` exits with a
                                          non-zero exit code.
        """
        exists = self._path_exists(path=cert_path)
        if not exists:
            raise CertTrustPathNotFoundError(
                operation="get_certificate_metadata",
                path=cert_path,
            )

        executable = self._typed_config().openssl_executable
        command = [
            executable, "x509",
            "-in", cert_path,
            "-noout",
            "-subject",
            "-issuer",
            "-serial",
            "-startdate",
            "-enddate",
            "-fingerprint",
            "-sha256",
            "-ext", "subjectAltName",
        ]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            raise CertTrustQueryError(
                operation="get_certificate_metadata",
                message=run_result.stderr,
            )

        parsed = _parse_openssl_metadata(stdout=run_result.stdout)
        rtnval = CertMetadata(
            subject=parsed["subject"],
            issuer=parsed["issuer"],
            serial=parsed["serial"],
            not_before=parsed["not_before"],
            not_after=parsed["not_after"],
            sha256_fingerprint=parsed["sha256_fingerprint"],
            sans=parsed["sans"],
        )
        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers — install / remove
    # ------------------------------------------------------------------

    def _install_to_directory(
        self,
        *,
        name: str,
        source_path: str,
        store_dir: str,
    ) -> CertificateTrustActionResult:
        """
            Install a PEM file into ``store_dir`` under ``<name>.pem``.

            Common implementation behind :meth:`install_anchor` and
            :meth:`install_blocklist_anchor`.

            :param name: Anchor short-name.
            :param source_path: Source PEM path.
            :param store_dir: Destination directory.

            :returns: The :class:`CertificateTrustActionResult`.
        """
        # Read the source content via the runner.
        source_content = self._read_file(path=source_path)
        if source_content is None:
            failed = self._build_result(
                status=CertTrustActionStatus.FAILED,
                name=name,
                path=source_path,
                command=[_CAT, source_path],
                detail="source path does not exist",
                error="source path does not exist",
            )
            return failed

        # Validate the PEM body via openssl x509 -noout (Property 1).
        validation_result = self._validate_pem_source(path=source_path)
        if not validation_result.ok:
            failed = self._build_result(
                status=CertTrustActionStatus.FAILED,
                name=name,
                path=source_path,
                command=validation_result.command,
                detail="source file is not a valid PEM certificate",
                error=validation_result.error,
            )
            return failed

        # Compute fingerprint in pure Python.
        cert_blocks = _parse_pem_blocks(content=source_content)
        if len(cert_blocks) == 0:
            failed = self._build_result(
                status=CertTrustActionStatus.FAILED,
                name=name,
                path=source_path,
                command=None,
                detail="source file does not contain a CERTIFICATE block",
                error="source file does not contain a CERTIFICATE block",
            )
            return failed

        fingerprint = _compute_fingerprint(pem=cert_blocks[0])
        if fingerprint is None:
            failed = self._build_result(
                status=CertTrustActionStatus.FAILED,
                name=name,
                path=source_path,
                command=None,
                detail="failed to compute fingerprint from PEM body",
                error="failed to compute fingerprint from PEM body",
            )
            return failed

        dest_filename = _normalize_anchor_filename(name=name)
        dest_path = "{}/{}".format(store_dir, dest_filename)

        # Idempotency: byte-identical content already in place.
        existing_content = self._read_file(path=dest_path)
        if existing_content is not None and existing_content == source_content:
            skipped = self._build_result(
                status=CertTrustActionStatus.SKIPPED,
                name=name,
                path=dest_path,
                command=None,
                detail="anchor already installed with identical content",
                sha256_fingerprint=fingerprint,
            )
            return skipped

        write_command = [_TEE, dest_path]

        if self._config.dry_run:
            dry = self._build_result(
                status=CertTrustActionStatus.DRY_RUN,
                name=name,
                path=dest_path,
                command=write_command,
                sha256_fingerprint=fingerprint,
            )
            return dry

        run_result = self._runner.run(
            list(write_command),
            elevated=self._config.elevated,
            stdin=source_content,
        )

        if run_result.exit_code == 0:
            mapped_status = CertTrustActionStatus.SUCCESS
        else:
            mapped_status = CertTrustActionStatus.FAILED

        if mapped_status == CertTrustActionStatus.SUCCESS:
            note_text = "call refresh_trust_store() for changes to take effect"
        else:
            note_text = None

        rtnval = CertificateTrustActionResult(
            status=mapped_status,
            detail=None,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(write_command),
            name=name,
            path=dest_path,
            sha256_fingerprint=fingerprint,
            note=note_text,
        )
        return rtnval

    def _remove_from_directory(
        self,
        *,
        name: str,
        store_dir: str,
    ) -> CertificateTrustActionResult:
        """
            Remove ``<name>.pem`` from ``store_dir``.  Common
            implementation behind :meth:`remove_anchor` and
            :meth:`remove_blocklist_anchor`.

            :param name: Anchor short-name.
            :param store_dir: Store directory.

            :returns: The :class:`CertificateTrustActionResult`.
        """
        dest_filename = _normalize_anchor_filename(name=name)
        dest_path = "{}/{}".format(store_dir, dest_filename)

        existing_content = self._read_file(path=dest_path)
        if existing_content is None:
            skipped = self._build_result(
                status=CertTrustActionStatus.SKIPPED,
                name=name,
                path=dest_path,
                command=None,
                detail="anchor file does not exist",
            )
            return skipped

        delete_command = [_RM, "-f", dest_path]

        if self._config.dry_run:
            dry = self._build_result(
                status=CertTrustActionStatus.DRY_RUN,
                name=name,
                path=dest_path,
                command=delete_command,
            )
            return dry

        run_result = self._runner.run(
            list(delete_command),
            elevated=self._config.elevated,
        )

        if run_result.exit_code == 0:
            mapped_status = CertTrustActionStatus.SUCCESS
        else:
            mapped_status = CertTrustActionStatus.FAILED

        if mapped_status == CertTrustActionStatus.SUCCESS:
            note_text = "call refresh_trust_store() for changes to take effect"
        else:
            note_text = None

        rtnval = CertificateTrustActionResult(
            status=mapped_status,
            detail=None,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(delete_command),
            name=name,
            path=dest_path,
            note=note_text,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers — enable / disable
    # ------------------------------------------------------------------

    def _toggle_trust_store(
        self,
        *,
        desired_enabled: bool,
        sub_command: str,
        operation: str,
    ) -> CertificateTrustActionResult:
        """
            Enable or disable the consolidated trust store, with an
            idempotency guard via :meth:`query_trust_state`.

            :param desired_enabled: The state the caller wants.
            :param sub_command: ``"enable"`` or ``"disable"``.
            :param operation: Operation name for record emission.

            :returns: The :class:`CertificateTrustActionResult`.
        """
        try:
            current = self.query_trust_state()
        except CertTrustQueryError:
            current = None

        executable = self._typed_config().update_executable
        command = [executable, sub_command]

        if current is not None and current.enabled == desired_enabled:
            skipped = self._build_result(
                status=CertTrustActionStatus.SKIPPED,
                name=None,
                path=None,
                command=command,
                detail="trust store already in desired state",
            )
            return skipped

        rtnval = self._dispatch(
            command=command,
            operation=operation,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers — read / parse
    # ------------------------------------------------------------------

    def _typed_config(self) -> CertificateTrustManagerConfig:
        """
            Narrow the base config to the typed subclass.

            :returns: The typed
                      :class:`CertificateTrustManagerConfig`.
        """
        rtnval = self._config
        return rtnval

    def _read_file(self, *, path: str) -> Optional[str]:
        """
            Read a file via ``cat`` (no elevation).

            :param path: The absolute path.

            :returns: The file contents on success, ``None`` when
                      ``cat`` reports a non-zero exit code.
        """
        command = [_CAT, path]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: Optional[str] = None
            return rtnval
        rtnval = run_result.stdout
        return rtnval

    def _list_dir(self, *, path: str) -> list:
        """
            Enumerate a directory's entries via ``ls -1`` (no
            elevation).

            :param path: The directory path.

            :returns: A list of filenames.  Empty when the directory
                      does not exist.
        """
        command = [_LS, "-1", path]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: list = []
            return rtnval
        rtnval = []
        for raw in run_result.stdout.splitlines():
            entry = raw.strip()
            if len(entry) == 0:
                continue
            rtnval.append(entry)
        return rtnval

    def _path_exists(self, *, path: str) -> bool:
        """
            Test whether a path exists via ``ls -1 <path>``.

            :param path: The path to test.

            :returns: True when ``ls`` exits zero, False otherwise.
        """
        command = [_LS, "-1", path]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval = False
            return rtnval
        rtnval = True
        return rtnval

    def _validate_pem_source(
        self,
        *,
        path: str,
    ) -> "_ValidationOutcome":
        """
            Validate that ``path`` parses as a PEM certificate via
            ``openssl x509 -noout -in <path>``.

            :param path: The source PEM path.

            :returns: A :class:`_ValidationOutcome`.
        """
        executable = self._typed_config().openssl_executable
        command = [executable, "x509", "-noout", "-in", path]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code == 0:
            rtnval = _ValidationOutcome(
                ok=True,
                command=list(command),
                error="",
            )
            return rtnval
        rtnval = _ValidationOutcome(
            ok=False,
            command=list(command),
            error=run_result.stderr,
        )
        return rtnval

    def _read_anchor(
        self,
        *,
        name: str,
        path: str,
    ) -> TrustAnchor:
        """
            Read a single anchor file and produce a
            :class:`TrustAnchor` record.

            On failure to parse, returns a record with ``valid=False``
            and a captured error string.  Read-only: never elevates.

            :param name: The on-disk filename.
            :param path: The absolute on-disk path.

            :returns: The :class:`TrustAnchor`.
        """
        content = self._read_file(path=path)
        if content is None:
            rtnval = TrustAnchor(
                name=name,
                path=path,
                subject="",
                issuer="",
                not_before="",
                not_after="",
                sha256_fingerprint="",
                valid=False,
                error="failed to read anchor file",
            )
            return rtnval

        blocks = _parse_pem_blocks(content=content)
        if len(blocks) == 0:
            rtnval = TrustAnchor(
                name=name,
                path=path,
                subject="",
                issuer="",
                not_before="",
                not_after="",
                sha256_fingerprint="",
                valid=False,
                error="file does not contain a CERTIFICATE block",
            )
            return rtnval

        fingerprint = _compute_fingerprint(pem=blocks[0])
        if fingerprint is None:
            rtnval = TrustAnchor(
                name=name,
                path=path,
                subject="",
                issuer="",
                not_before="",
                not_after="",
                sha256_fingerprint="",
                valid=False,
                error="failed to decode PEM body",
            )
            return rtnval

        executable = self._typed_config().openssl_executable
        text_command = [
            executable, "x509",
            "-in", path,
            "-noout",
            "-subject",
            "-issuer",
            "-startdate",
            "-enddate",
            "-fingerprint",
            "-sha256",
        ]
        run_result = self._runner.run(list(text_command), elevated=False)
        if run_result.exit_code != 0:
            rtnval = TrustAnchor(
                name=name,
                path=path,
                subject="",
                issuer="",
                not_before="",
                not_after="",
                sha256_fingerprint=fingerprint,
                valid=False,
                error=run_result.stderr,
            )
            return rtnval

        parsed = _parse_openssl_anchor_text(stdout=run_result.stdout)
        rtnval = TrustAnchor(
            name=name,
            path=path,
            subject=parsed["subject"],
            issuer=parsed["issuer"],
            not_before=parsed["not_before"],
            not_after=parsed["not_after"],
            sha256_fingerprint=fingerprint,
            valid=True,
            error="",
        )
        return rtnval

    def _build_result(
        self,
        *,
        status: ActionStatus,
        name: Optional[str],
        path: Optional[str],
        command: Optional[list],
        detail: Optional[str] = None,
        sha256_fingerprint: Optional[str] = None,
        subject: Optional[str] = None,
        error: Optional[str] = None,
    ) -> CertificateTrustActionResult:
        """
            Construct a :class:`CertificateTrustActionResult` without
            invoking the runner.
        """
        if command is None:
            command_copy: Optional[list] = None
        else:
            command_copy = list(command)
        rtnval = CertificateTrustActionResult(
            status=status,
            detail=detail,
            command=command_copy,
            name=name,
            path=path,
            subject=subject,
            sha256_fingerprint=sha256_fingerprint,
            error=error,
        )
        return rtnval


# ----------------------------------------------------------------------
# Validation outcome (internal)
# ----------------------------------------------------------------------


@dataclass
class _ValidationOutcome:
    """
        Internal carrier for the result of
        :meth:`CertificateTrustManager._validate_pem_source`.
    """
    ok: bool
    command: list
    error: str


# ----------------------------------------------------------------------
# Module-level helpers — PEM / fingerprint / openssl text parsing
# ----------------------------------------------------------------------


def _normalize_anchor_filename(*, name: str) -> str:
    """
        Append ``.pem`` to an anchor name when the caller did not
        already supply an extension.

        :param name: The anchor short-name.

        :returns: The destination filename (no directory component).
    """
    if name.endswith(".pem") or name.endswith(".crt"):
        rtnval = name
        return rtnval
    rtnval = "{}.pem".format(name)
    return rtnval


def _parse_pem_blocks(*, content: str) -> list:
    """
        Split a PEM-bearing string into a list of CERTIFICATE blocks.

        Non-CERTIFICATE blocks (PRIVATE KEY, CERTIFICATE REQUEST, etc.)
        are skipped.

        :param content: Raw file content.

        :returns: A list of full PEM envelopes (one string per block).
    """
    rtnval: list = []
    in_block = False
    current_lines: list = []

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

        Decodes the base64 body, hashes the resulting DER bytes, and
        formats the digest as uppercase hex bytes joined by colons
        (e.g. ``"AB:CD:EF:..."``).  Pure Python; does not invoke
        ``openssl``.

        :param pem: The full PEM envelope.

        :returns: The fingerprint string, or ``None`` when the PEM body
                  fails to decode.
    """
    body_lines: list = []
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

    chunks: list = []
    cursor = 0
    while cursor < len(hex_text):
        chunks.append(hex_text[cursor:cursor + 2])
        cursor += 2
    rtnval = ":".join(chunks)
    return rtnval


def _parse_trust_check_output(*, stdout: str) -> bool:
    """
        Parse the captured ``update-ca-trust check`` stdout to extract
        the enabled/disabled state.

        Heuristic: ``update-ca-trust check`` emits a line containing
        ``DISABLED`` when the consolidated bundle is disabled.  An
        ``ENABLED`` token (case-insensitive) marks the enabled state.
        When neither token is present we default to enabled, matching
        the real tool's behaviour on a freshly-installed system.

        :param stdout: The captured stdout.

        :returns: True when enabled, False when disabled.
    """
    upper = stdout.upper()
    if "DISABLED" in upper:
        rtnval = False
        return rtnval
    rtnval = True
    return rtnval


def _parse_openssl_anchor_text(*, stdout: str) -> dict:
    """
        Parse the combined output of ``openssl x509 -subject -issuer
        -startdate -enddate -fingerprint -sha256``.

        :param stdout: The captured stdout.

        :returns: A ``dict`` with keys ``subject``, ``issuer``,
                  ``not_before``, ``not_after``,
                  ``sha256_fingerprint``.
    """
    rtnval: dict = {
        "subject": "",
        "issuer": "",
        "not_before": "",
        "not_after": "",
        "sha256_fingerprint": "",
    }

    for raw in stdout.splitlines():
        stripped = raw.strip()
        if stripped.startswith("subject="):
            rtnval["subject"] = stripped[len("subject="):].strip()
            continue
        if stripped.startswith("issuer="):
            rtnval["issuer"] = stripped[len("issuer="):].strip()
            continue
        if stripped.startswith("notBefore="):
            rtnval["not_before"] = stripped[len("notBefore="):].strip()
            continue
        if stripped.startswith("notAfter="):
            rtnval["not_after"] = stripped[len("notAfter="):].strip()
            continue
        lower = stripped.lower()
        if lower.startswith("sha256 fingerprint="):
            rtnval["sha256_fingerprint"] = stripped.split("=", 1)[1].strip()
            continue

    return rtnval


def _parse_openssl_metadata(*, stdout: str) -> dict:
    """
        Parse the combined output of ``openssl x509 -subject -issuer
        -serial -startdate -enddate -fingerprint -sha256 -ext
        subjectAltName``.

        :param stdout: The captured stdout.

        :returns: A ``dict`` with keys ``subject``, ``issuer``,
                  ``serial``, ``not_before``, ``not_after``,
                  ``sha256_fingerprint``, ``sans``.
    """
    rtnval: dict = {
        "subject": "",
        "issuer": "",
        "serial": "",
        "not_before": "",
        "not_after": "",
        "sha256_fingerprint": "",
        "sans": [],
    }

    in_san_block = False

    for raw in stdout.splitlines():
        stripped = raw.strip()

        if stripped.startswith("subject="):
            rtnval["subject"] = stripped[len("subject="):].strip()
            in_san_block = False
            continue
        if stripped.startswith("issuer="):
            rtnval["issuer"] = stripped[len("issuer="):].strip()
            in_san_block = False
            continue
        if stripped.startswith("serial="):
            rtnval["serial"] = stripped[len("serial="):].strip()
            in_san_block = False
            continue
        if stripped.startswith("notBefore="):
            rtnval["not_before"] = stripped[len("notBefore="):].strip()
            in_san_block = False
            continue
        if stripped.startswith("notAfter="):
            rtnval["not_after"] = stripped[len("notAfter="):].strip()
            in_san_block = False
            continue
        lower = stripped.lower()
        if lower.startswith("sha256 fingerprint="):
            rtnval["sha256_fingerprint"] = stripped.split("=", 1)[1].strip()
            in_san_block = False
            continue
        if "X509v3 Subject Alternative Name" in stripped:
            in_san_block = True
            continue
        if in_san_block:
            if len(stripped) == 0:
                in_san_block = False
                continue
            for chunk in stripped.split(","):
                clean = chunk.strip()
                if len(clean) == 0:
                    continue
                rtnval["sans"].append(clean)
            in_san_block = False
            continue

    return rtnval
