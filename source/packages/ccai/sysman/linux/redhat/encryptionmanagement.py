# ccai.sysman.linux.redhat.encryptionmanagement
#
# EncryptionManager wraps the RHEL LUKS / TPM2 / Clevis toolchain —
# ``cryptsetup``, ``systemd-cryptenroll``, ``clevis luks ...`` — and
# direct edits to ``/etc/crypttab`` through the ICommandRunner
# abstraction.
#
# Credential safety:
#   Passphrase bytes ALWAYS flow through the runner ``stdin`` parameter.
#   They are NEVER placed on argv.  The :class:`SanitisedDispatchMixin`
#   is mixed in so that the recorded ``command`` field on every result
#   reflects the caller-supplied argv (no secret material) regardless
#   of how the runner records its dispatch.

import json
import os

from dataclasses import dataclass, field
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
# Module-level command tokens
# ----------------------------------------------------------------------


_CRYPTSETUP = "cryptsetup"
_SYSTEMD_CRYPTENROLL = "systemd-cryptenroll"
_CLEVIS = "clevis"
_CAT = "cat"
_TEE = "tee"
_TEST = "test"


# Default crypttab path; overridable via :class:`EncryptionManagerConfig`.
_DEFAULT_CRYPTTAB_PATH = "/etc/crypttab"

# Default LUKS2 ``luksFormat`` knobs.
_DEFAULT_CIPHER = "aes-xts-plain64"
_DEFAULT_KEY_SIZE = 512
_DEFAULT_HASH = "sha256"
_DEFAULT_PBKDF = "argon2id"
_DEFAULT_LUKS_VERSION = "luks2"


# ----------------------------------------------------------------------
# Aliases
# ----------------------------------------------------------------------


EncryptionActionStatus = ActionStatus


# ----------------------------------------------------------------------
# Config / result dataclasses
# ----------------------------------------------------------------------


@dataclass
class EncryptionManagerConfig(FeatureConfig):
    """
        Configuration for :class:`EncryptionManager`.  Inherits
        ``dry_run`` and ``elevated`` from :class:`FeatureConfig` and
        adds LUKS-format defaults plus the crypttab-path override.

        :param crypttab_path: Path to the on-disk crypttab.  Defaults
                              to :data:`_DEFAULT_CRYPTTAB_PATH`
                              (``/etc/crypttab``).
        :param default_cipher: Default ``--cipher`` value passed to
                               ``cryptsetup luksFormat`` when a
                               :class:`CryptFormatSpec` does not
                               override.
        :param default_key_size: Default ``--key-size`` value (BITS).
        :param default_hash: Default ``--hash`` value.
        :param default_pbkdf: Default ``--pbkdf`` value.
    """
    elevated: bool = True
    crypttab_path: str = _DEFAULT_CRYPTTAB_PATH
    default_cipher: str = _DEFAULT_CIPHER
    default_key_size: int = _DEFAULT_KEY_SIZE
    default_hash: str = _DEFAULT_HASH
    default_pbkdf: str = _DEFAULT_PBKDF


@dataclass
class EncryptionActionResult(FeatureActionResult):
    """
        Outcome of an encryption-management operation.

        Adds a few RHEL-specific narrative fields to the canonical
        :class:`FeatureActionResult`.  The ``command`` field NEVER
        contains passphrase bytes; the
        :class:`SanitisedDispatchMixin` enforces this invariant.

        :param device: Underlying block-device path the operation
                       targeted, when applicable.
        :param name: Mapping name (``/dev/mapper/<name>``), when
                     applicable.
    """
    device: Optional[str] = None
    name: Optional[str] = None


# ----------------------------------------------------------------------
# Parsed-data dataclasses
# ----------------------------------------------------------------------


@dataclass
class KeySlotInfo:
    """
        Per-slot metadata extracted from ``cryptsetup luksDump``.

        :param slot: The slot index.
        :param state: ``"ENABLED"`` or ``"DISABLED"`` — empty slots
                      are reported as ``DISABLED`` on LUKS1 and are
                      simply absent from LUKS2 output.
        :param key_size_bits: Slot key size in bits when reported by
                              the LUKS header, else ``None``.
        :param token: LUKS2 token id bound to the slot when reported,
                      else ``None``.
    """
    slot: int
    state: str
    key_size_bits: Optional[int] = None
    token: Optional[int] = None


@dataclass
class LuksHeader:
    """
        Decoded LUKS header for a single device.

        :param device: Block-device path.
        :param version: ``"1"`` or ``"2"``.
        :param uuid: Header UUID.
        :param cipher: Cipher token (``aes-xts-plain64`` etc).
        :param hash: Hash token (LUKS1) or PBKDF hash (LUKS2).
        :param key_slots: Per-slot info.
        :param tokens: LUKS2 tokens parsed as plain dicts.
        :param raw: The verbatim ``luksDump`` stdout for callers that
                    need to consult fields the parser does not
                    surface.
    """
    device: str
    version: str
    uuid: Optional[str]
    cipher: str
    hash: str
    key_slots: list
    tokens: list
    raw: str


@dataclass
class CryptFormatSpec:
    """
        Spec for :meth:`EncryptionManager.format_device`.

        Every field is optional; ``None`` falls back to the matching
        :class:`EncryptionManagerConfig` default.

        :param cipher: ``--cipher`` token.
        :param key_size: ``--key-size`` value in BITS.
        :param hash: ``--hash`` token.
        :param pbkdf: ``--pbkdf`` token.
        :param sector_size: ``--sector-size`` value (bytes).
        :param luks_version: ``--type`` token (default ``luks2``).
        :param extra_args: Raw extra arguments appended to the
                           ``luksFormat`` argv after the standard
                           knobs and before the device path.
    """
    cipher: Optional[str] = None
    key_size: Optional[int] = None
    hash: Optional[str] = None
    pbkdf: Optional[str] = None
    sector_size: Optional[int] = None
    luks_version: str = _DEFAULT_LUKS_VERSION
    extra_args: Optional[list] = None


@dataclass
class CryptBinding:
    """
        A single TPM2 / Clevis / passphrase binding on a LUKS device.

        :param slot: The LUKS keyslot index.
        :param backend: ``"tpm2"`` / ``"clevis"`` / ``"passphrase"``.
        :param pin: Clevis pin name (``"tpm2"``, ``"tang"``, ...) or
                    ``None`` when the backend is not Clevis.
        :param config: Parsed JSON configuration for Clevis bindings,
                       else ``None``.
        :param raw: Verbatim source line (or JSON fragment) for
                    callers that need the unparsed form.
    """
    slot: int
    backend: str
    pin: Optional[str] = None
    config: Optional[dict] = None
    raw: str = ""


@dataclass
class CrypttabEntry:
    """
        One line in ``/etc/crypttab``.

        :param name: Mapping name (column 1).
        :param device: Source device or ``UUID=...`` (column 2).
        :param keyfile: Keyfile path or ``None`` when column 3 is
                        ``-`` / ``none``.
        :param options: Comma-split options list (column 4).  An
                        empty list when the column is absent or
                        ``-``.
    """
    name: str
    device: str
    keyfile: Optional[str] = None
    options: list = field(default_factory=list)


# ----------------------------------------------------------------------
# Exceptions
# ----------------------------------------------------------------------


class EncryptionToolNotAvailableError(SemanticError):
    """
        Raised when a required encryption tool (``cryptsetup``,
        ``systemd-cryptenroll``, ``clevis``) is not installed on the
        target system.  Subclasses :class:`SemanticError` so it
        bubbles past the manager unconditionally.
    """
    pass


class EncryptionCredentialError(SemanticError):
    """
        Raised when an encryption verb requires a credential and none
        was supplied.  Subclasses :class:`SemanticError` so it
        bubbles unconditionally — never caught by the manager.
    """
    pass


class EncryptionVolumeNotFoundError(SemanticError):
    """
        Raised when a verb is given an unknown volume.  Subclasses
        :class:`SemanticError` so it bubbles unconditionally.
    """
    pass


class EncryptionQueryError(Exception):
    """
        Raised when an encryption read fails for a reason other than
        the target simply being absent from the encryption surface.
    """

    def __init__(
        self,
        *,
        command: list,
        exit_code: Optional[int],
        stderr: str,
    ) -> None:
        """
            :param command: The argv that failed.
            :param exit_code: The runner's exit code.
            :param stderr: The runner's captured stderr.
        """
        self.command: list = list(command)
        self.exit_code = exit_code
        self.stderr = stderr

        err_msg_lines = ["Encryption query command failed."]
        err_msg_lines.append("    COMMAND: {}".format(" ".join(command)))
        err_msg_lines.append("    EXIT_CODE: {}".format(exit_code))
        err_msg_lines.append("    STDERR: {}".format(stderr))
        err_msg = os.linesep.join(err_msg_lines)
        super().__init__(err_msg)
        return


# ----------------------------------------------------------------------
# EncryptionManager
# ----------------------------------------------------------------------


class EncryptionManager(SanitisedDispatchMixin, BaseFeatureManager):
    """
        Facade for LUKS / TPM2 / Clevis encryption management on
        RHEL.

        Wraps ``cryptsetup``, ``systemd-cryptenroll``, and
        ``clevis luks`` through the injected
        :class:`ICommandRunner`; owns reads and writes against
        ``/etc/crypttab`` via ``cat`` / ``tee``.

        Mixes :class:`SanitisedDispatchMixin` in so any dispatch with
        passphrase bytes on stdin records the bare argv (no secret
        material) on the returned :class:`EncryptionActionResult`.
    """

    result_class = EncryptionActionResult
    status_class = EncryptionActionStatus
    feature_name = "encryption-management"

    # ------------------------------------------------------------------
    # Read verbs
    # ------------------------------------------------------------------

    def query_header(self, device: str) -> Optional[LuksHeader]:
        """
            Parse the LUKS header of ``device`` via ``cryptsetup
            luksDump``.

            :param device: Block-device path.

            :returns: A :class:`LuksHeader` when the device is LUKS,
                      ``None`` when it is not.

            :raises EncryptionToolNotAvailableError: When the
                ``cryptsetup`` binary is absent on the target.
        """
        command = [_CRYPTSETUP, "luksDump", device]
        run_result = self._runner.run(list(command), elevated=False)

        if run_result.exit_code == 127:
            err_msg_lines = ["cryptsetup is not installed on the target."]
            err_msg_lines.append("    COMMAND: {}".format(" ".join(command)))
            err_msg_lines.append("    HINT: dnf install cryptsetup")
            err_msg = os.linesep.join(err_msg_lines)
            raise EncryptionToolNotAvailableError(err_msg)

        stderr_lower = run_result.stderr.lower()
        if "command not found" in stderr_lower or "no such file" in stderr_lower:
            err_msg_lines = ["cryptsetup is not installed on the target."]
            err_msg_lines.append("    COMMAND: {}".format(" ".join(command)))
            err_msg_lines.append("    STDERR: {}".format(run_result.stderr))
            err_msg_lines.append("    HINT: dnf install cryptsetup")
            err_msg = os.linesep.join(err_msg_lines)
            raise EncryptionToolNotAvailableError(err_msg)

        if run_result.exit_code != 0:
            rtnval: Optional[LuksHeader] = None
            return rtnval

        # Defensive: an empty / malformed payload should be treated as
        # "not LUKS" rather than an empty :class:`LuksHeader`.  Real
        # ``cryptsetup luksDump`` output always carries a ``Version:``
        # line; any payload missing it is not a usable header.
        if "Version:" not in run_result.stdout:
            rtnval = None
            return rtnval

        rtnval = _parse_luks_dump(device=device, output=run_result.stdout)
        return rtnval

    def list_bindings(self, device: str) -> list:
        """
            Merge ``systemd-cryptenroll <device>`` and
            ``clevis luks list -d <device>`` into a single list of
            :class:`CryptBinding` records.

            :param device: Block-device path.

            :returns: A list of :class:`CryptBinding`.  Empty when
                      neither tool reports any binding.
        """
        rtnval: list = []

        sce_command = [_SYSTEMD_CRYPTENROLL, device]
        sce_result = self._runner.run(list(sce_command), elevated=False)
        if sce_result.exit_code == 0:
            parsed_sce = _parse_systemd_cryptenroll(output=sce_result.stdout)
            rtnval.extend(parsed_sce)

        clevis_command = [_CLEVIS, "luks", "list", "-d", device]
        clevis_result = self._runner.run(list(clevis_command), elevated=False)
        if clevis_result.exit_code == 0:
            parsed_clevis = _parse_clevis_luks_list(output=clevis_result.stdout)
            rtnval.extend(parsed_clevis)

        return rtnval

    def list_crypttab(self) -> list:
        """
            Parse ``/etc/crypttab`` into a list of
            :class:`CrypttabEntry`.

            :returns: A list (possibly empty) of
                      :class:`CrypttabEntry` preserving file order.
        """
        target_path = self._typed_config().crypttab_path
        content = self._read_file(path=target_path)
        if content is None:
            rtnval: list = []
            return rtnval
        rtnval = _parse_crypttab(content=content)
        return rtnval

    # ------------------------------------------------------------------
    # LUKS — format / open / close / resize
    # ------------------------------------------------------------------

    def format_device(
        self,
        device: str,
        spec: CryptFormatSpec,
        credential,
        force: bool = False,
    ) -> EncryptionActionResult:
        """
            LUKS-format ``device`` per ``spec``.

            Idempotent: when :meth:`query_header` returns a non-None
            :class:`LuksHeader` and ``force`` is False, returns
            ``SKIPPED`` without dispatching ``luksFormat``.  When
            ``force`` is True, ``--batch-mode`` is appended to
            suppress interactive confirmations.

            :param device: Block-device path.
            :param spec: :class:`CryptFormatSpec`.
            :param credential: Credential-shaped object whose
                               ``secret_bytes()`` (or compatible
                               accessor) yields the bootstrap
                               passphrase.  Required.  Bytes flow on
                               runner ``stdin``; never on argv.
            :param force: When True, dispatch even if the device is
                          already LUKS.

            :returns: An :class:`EncryptionActionResult`.

            :raises EncryptionCredentialError: When ``credential`` is
                ``None``.
        """
        if credential is None:
            err_msg_lines = ["A credential is required for format_device."]
            err_msg_lines.append("    DEVICE: {}".format(device))
            err_msg = os.linesep.join(err_msg_lines)
            raise EncryptionCredentialError(err_msg)

        cfg = self._typed_config()
        cipher_value = spec.cipher if spec.cipher is not None else cfg.default_cipher
        key_size_value = (
            spec.key_size if spec.key_size is not None else cfg.default_key_size
        )
        hash_value = spec.hash if spec.hash is not None else cfg.default_hash
        pbkdf_value = spec.pbkdf if spec.pbkdf is not None else cfg.default_pbkdf

        command = [_CRYPTSETUP, "luksFormat"]
        command.append("--type")
        command.append(spec.luks_version)
        command.append("--cipher")
        command.append(cipher_value)
        command.append("--key-size")
        command.append(str(key_size_value))
        command.append("--hash")
        command.append(hash_value)
        command.append("--pbkdf")
        command.append(pbkdf_value)
        if spec.sector_size is not None:
            command.append("--sector-size")
            command.append(str(spec.sector_size))
        if spec.extra_args is not None and len(spec.extra_args) > 0:
            for arg in spec.extra_args:
                command.append(arg)
        if force:
            command.append("--batch-mode")
        command.append(device)

        # Dry-run short-circuits before the read-only header probe so
        # the runner is not consulted at all.  This matches the
        # principle that dry-run is a no-side-effect, no-query
        # rehearsal of the would-be argv.
        if self._config.dry_run:
            dry_result = EncryptionActionResult(
                status=EncryptionActionStatus.DRY_RUN,
                detail=device,
                command=list(command),
                device=device,
            )
            return dry_result

        if not force:
            existing = self.query_header(device=device)
            if existing is not None:
                skipped = self._build_skipped(
                    command=command,
                    detail="device {} is already LUKS".format(device),
                    device=device,
                )
                return skipped

        passphrase = _resolve_credential_str(credential=credential)
        rtnval = self._dispatch_secret_write(
            command=command,
            stdin=passphrase,
            device=device,
            detail=device,
        )
        return rtnval

    def open(
        self,
        device: str,
        name: str,
        credential=None,
        key_file: Optional[str] = None,
    ) -> EncryptionActionResult:
        """
            Unlock ``device`` as ``/dev/mapper/<name>``.

            Idempotent: when ``/dev/mapper/<name>`` already exists
            (probed via ``test -b``), returns ``SKIPPED``.  Exactly
            one of ``credential`` or ``key_file`` must be supplied;
            passphrase bytes flow on stdin and never on argv.

            :param device: Block-device path.
            :param name: Mapping name.
            :param credential: Credential-shaped object whose
                               ``secret_bytes()`` yields the
                               passphrase.  Mutually exclusive with
                               ``key_file``.
            :param key_file: Path to a key file passed as
                             ``--key-file``.  Mutually exclusive with
                             ``credential``.

            :returns: An :class:`EncryptionActionResult`.

            :raises EncryptionCredentialError: When neither
                ``credential`` nor ``key_file`` is supplied.
        """
        if credential is None and key_file is None:
            err_msg_lines = ["open() requires either a credential or key_file."]
            err_msg_lines.append("    DEVICE: {}".format(device))
            err_msg_lines.append("    NAME: {}".format(name))
            err_msg = os.linesep.join(err_msg_lines)
            raise EncryptionCredentialError(err_msg)

        if key_file is not None:
            command = [
                _CRYPTSETUP, "open",
                "--key-file", key_file,
                device, name,
            ]
            stdin_payload: Optional[str] = None
        else:
            command = [_CRYPTSETUP, "open", device, name]
            stdin_payload = _resolve_credential_str(credential=credential)

        if self._mapping_exists(name=name):
            skipped = self._build_skipped(
                command=command,
                detail="mapping /dev/mapper/{} already active".format(name),
                device=device,
                name=name,
            )
            return skipped

        rtnval = self._dispatch_secret_write(
            command=command,
            stdin=stdin_payload,
            device=device,
            name=name,
            detail=name,
        )
        return rtnval

    def open_with_clevis(
        self,
        device: str,
        name: str,
    ) -> EncryptionActionResult:
        """
            Unlock ``device`` as ``/dev/mapper/<name>`` using a Clevis
            (TPM2 / Tang) binding.  No passphrase is required; the
            command is dispatched without a stdin payload.

            :param device: Block-device path.
            :param name: Mapping name.

            :returns: An :class:`EncryptionActionResult`.
        """
        command = [_CLEVIS, "luks", "unlock", "-d", device, "-n", name]

        if self._mapping_exists(name=name):
            skipped = self._build_skipped(
                command=command,
                detail="mapping /dev/mapper/{} already active".format(name),
                device=device,
                name=name,
            )
            return skipped

        rtnval = self._dispatch_secret_write(
            command=command,
            stdin=None,
            device=device,
            name=name,
            detail=name,
        )
        return rtnval

    def close(self, name: str) -> EncryptionActionResult:
        """
            Close (lock) the mapping at ``/dev/mapper/<name>``.

            Idempotent: when the mapping does not exist, returns
            ``SKIPPED``.

            :param name: Mapping name.

            :returns: An :class:`EncryptionActionResult`.
        """
        command = [_CRYPTSETUP, "close", name]

        if not self._mapping_exists(name=name):
            skipped = self._build_skipped(
                command=command,
                detail="mapping /dev/mapper/{} not active".format(name),
                name=name,
            )
            return skipped

        rtnval = self._dispatch_secret_write(
            command=command,
            stdin=None,
            name=name,
            detail=name,
        )
        return rtnval

    def resize(
        self,
        name: str,
        sectors: Optional[int] = None,
    ) -> EncryptionActionResult:
        """
            Resize the LUKS mapping at ``/dev/mapper/<name>``.

            With no ``sectors`` value the call grows the mapping to
            the underlying device size.  When ``sectors`` is
            supplied, ``--size <sectors>`` is appended.  When the
            mapping is not currently active, the operation returns
            ``FAILED`` without dispatching.

            :param name: Mapping name.
            :param sectors: Optional explicit sector count.

            :returns: An :class:`EncryptionActionResult`.
        """
        command = [_CRYPTSETUP, "resize", name]
        if sectors is not None:
            command.append("--size")
            command.append(str(sectors))

        if not self._mapping_exists(name=name):
            failed = self._build_failed(
                command=command,
                detail="mapping /dev/mapper/{} not active".format(name),
                name=name,
                stderr="cannot resize: mapping is not active",
            )
            return failed

        rtnval = self._dispatch_secret_write(
            command=command,
            stdin=None,
            name=name,
            detail=name,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Key-slot lifecycle
    # ------------------------------------------------------------------

    def add_key(
        self,
        device: str,
        existing_credential,
        new_credential,
    ) -> EncryptionActionResult:
        """
            Add a new passphrase to a LUKS key slot.

            Both passphrases are delivered via runner ``stdin`` on a
            single combined payload (existing followed by newline
            followed by new); ``cryptsetup luksAddKey`` reads them in
            that order.  Neither value ever appears on argv.

            :param device: Block-device path.
            :param existing_credential: Credential-shaped object
                whose secret bytes yield an existing valid
                passphrase.
            :param new_credential: Credential-shaped object whose
                secret bytes yield the new passphrase.

            :returns: An :class:`EncryptionActionResult`.

            :raises EncryptionCredentialError: When either credential
                is ``None``.
        """
        if existing_credential is None or new_credential is None:
            err_msg_lines = ["add_key requires both an existing and a new credential."]
            err_msg_lines.append("    DEVICE: {}".format(device))
            err_msg = os.linesep.join(err_msg_lines)
            raise EncryptionCredentialError(err_msg)

        existing_value = _resolve_credential_str(credential=existing_credential)
        new_value = _resolve_credential_str(credential=new_credential)

        command = [_CRYPTSETUP, "luksAddKey", device]
        combined_stdin = "{}\n{}\n".format(existing_value, new_value)

        rtnval = self._dispatch_secret_write(
            command=command,
            stdin=combined_stdin,
            device=device,
            detail=device,
        )
        return rtnval

    def remove_key_by_slot(
        self,
        device: str,
        slot: int,
        credential,
    ) -> EncryptionActionResult:
        """
            Remove the LUKS key slot at ``slot`` via ``cryptsetup
            luksKillSlot``.

            Safety rail: when the device's enabled-slot count would
            drop to zero, returns ``FAILED`` without dispatching.
            This prevents leaving the device with no recoverable
            passphrase.  Callers that intend to permanently retire a
            device should use ``cryptsetup erase`` (out of scope for
            this verb).

            :param device: Block-device path.
            :param slot: Slot index.
            :param credential: Credential-shaped object whose secret
                               bytes yield an existing valid
                               passphrase.

            :returns: An :class:`EncryptionActionResult`.

            :raises EncryptionCredentialError: When ``credential`` is
                ``None``.
        """
        if credential is None:
            err_msg_lines = ["remove_key_by_slot requires a credential."]
            err_msg_lines.append("    DEVICE: {}".format(device))
            err_msg = os.linesep.join(err_msg_lines)
            raise EncryptionCredentialError(err_msg)

        command = [_CRYPTSETUP, "luksKillSlot", device, str(slot)]

        header = self.query_header(device=device)
        if header is not None:
            enabled_slots: list = []
            for slot_info in header.key_slots:
                if slot_info.state == "ENABLED":
                    enabled_slots.append(slot_info.slot)
            target_is_enabled = slot in enabled_slots
            remaining = len(enabled_slots)
            if target_is_enabled:
                remaining = remaining - 1
            if remaining <= 0:
                failed = self._build_failed(
                    command=command,
                    detail="refusing to remove last enabled key slot",
                    device=device,
                    stderr=(
                        "removal would leave the device with zero "
                        "active key slots"
                    ),
                )
                return failed

        passphrase = _resolve_credential_str(credential=credential)
        rtnval = self._dispatch_secret_write(
            command=command,
            stdin=passphrase,
            device=device,
            detail=str(slot),
        )
        return rtnval

    def remove_key_by_passphrase(
        self,
        device: str,
        credential,
    ) -> EncryptionActionResult:
        """
            Remove the LUKS key slot matching ``credential`` via
            ``cryptsetup luksRemoveKey``.

            :param device: Block-device path.
            :param credential: Credential-shaped object whose secret
                               bytes name the slot to remove.

            :returns: An :class:`EncryptionActionResult`.

            :raises EncryptionCredentialError: When ``credential`` is
                ``None``.
        """
        if credential is None:
            err_msg_lines = ["remove_key_by_passphrase requires a credential."]
            err_msg_lines.append("    DEVICE: {}".format(device))
            err_msg = os.linesep.join(err_msg_lines)
            raise EncryptionCredentialError(err_msg)

        command = [_CRYPTSETUP, "luksRemoveKey", device]
        passphrase = _resolve_credential_str(credential=credential)

        rtnval = self._dispatch_secret_write(
            command=command,
            stdin=passphrase,
            device=device,
            detail=device,
        )
        return rtnval

    def rotate_key(
        self,
        device: str,
        old_credential,
        new_credential,
    ) -> EncryptionActionResult:
        """
            Rotate a LUKS slot via ``cryptsetup luksChangeKey``.  Old
            and new passphrases are concatenated with a newline and
            sent on a single ``stdin`` payload.

            :param device: Block-device path.
            :param old_credential: Credential-shaped object whose
                                   secret bytes yield the existing
                                   passphrase.
            :param new_credential: Credential-shaped object whose
                                   secret bytes yield the
                                   replacement passphrase.

            :returns: An :class:`EncryptionActionResult`.

            :raises EncryptionCredentialError: When either credential
                is ``None``.
        """
        if old_credential is None or new_credential is None:
            err_msg_lines = ["rotate_key requires both old and new credentials."]
            err_msg_lines.append("    DEVICE: {}".format(device))
            err_msg = os.linesep.join(err_msg_lines)
            raise EncryptionCredentialError(err_msg)

        old_value = _resolve_credential_str(credential=old_credential)
        new_value = _resolve_credential_str(credential=new_credential)

        command = [_CRYPTSETUP, "luksChangeKey", device]
        combined_stdin = "{}\n{}\n".format(old_value, new_value)

        rtnval = self._dispatch_secret_write(
            command=command,
            stdin=combined_stdin,
            device=device,
            detail=device,
        )
        return rtnval

    # ------------------------------------------------------------------
    # TPM2 / Clevis enrolment
    # ------------------------------------------------------------------

    def enroll_tpm2(
        self,
        device: str,
        credential,
        pcrs: str = "0,7",
    ) -> EncryptionActionResult:
        """
            Enrol a TPM2 binding for ``device`` via
            ``systemd-cryptenroll``.

            :param device: Block-device path.
            :param credential: Credential-shaped object whose secret
                               bytes yield an existing valid
                               passphrase used to authenticate the
                               enrolment.
            :param pcrs: Comma-separated PCR list passed via
                         ``--tpm2-pcrs``.

            :returns: An :class:`EncryptionActionResult`.

            :raises EncryptionCredentialError: When ``credential`` is
                ``None``.
        """
        if credential is None:
            err_msg_lines = ["enroll_tpm2 requires a credential."]
            err_msg_lines.append("    DEVICE: {}".format(device))
            err_msg = os.linesep.join(err_msg_lines)
            raise EncryptionCredentialError(err_msg)

        command = [
            _SYSTEMD_CRYPTENROLL,
            "--tpm2-device=auto",
            "--tpm2-pcrs={}".format(pcrs),
            device,
        ]
        passphrase = _resolve_credential_str(credential=credential)

        rtnval = self._dispatch_secret_write(
            command=command,
            stdin=passphrase,
            device=device,
            detail=pcrs,
        )
        return rtnval

    def wipe_tpm2_slot(
        self,
        device: str,
        slot: int,
    ) -> EncryptionActionResult:
        """
            Wipe a slot via ``systemd-cryptenroll --wipe-slot=<slot>``.

            :param device: Block-device path.
            :param slot: Slot index to wipe.

            :returns: An :class:`EncryptionActionResult`.
        """
        command = [
            _SYSTEMD_CRYPTENROLL,
            "--wipe-slot={}".format(slot),
            device,
        ]
        rtnval = self._dispatch_secret_write(
            command=command,
            stdin=None,
            device=device,
            detail=str(slot),
        )
        return rtnval

    def enroll_clevis(
        self,
        device: str,
        pin: str,
        config: dict,
        credential,
    ) -> EncryptionActionResult:
        """
            Enrol a Clevis binding via ``clevis luks bind -d <device>
            <pin> <json>``.  The JSON config is rendered and passed
            on argv (no secret material); the existing passphrase
            flows on ``stdin``.

            :param device: Block-device path.
            :param pin: Clevis pin name (``"tpm2"``, ``"tang"``, ...).
            :param config: Clevis pin configuration; serialised to
                           JSON.
            :param credential: Credential-shaped object whose secret
                               bytes yield an existing valid
                               passphrase.

            :returns: An :class:`EncryptionActionResult`.

            :raises EncryptionCredentialError: When ``credential`` is
                ``None``.
        """
        if credential is None:
            err_msg_lines = ["enroll_clevis requires a credential."]
            err_msg_lines.append("    DEVICE: {}".format(device))
            err_msg = os.linesep.join(err_msg_lines)
            raise EncryptionCredentialError(err_msg)

        if config is None:
            config_payload: dict = {}
        else:
            config_payload = config
        config_json = json.dumps(config_payload, sort_keys=True)

        command = [
            _CLEVIS, "luks", "bind",
            "-d", device,
            pin,
            config_json,
        ]
        passphrase = _resolve_credential_str(credential=credential)

        rtnval = self._dispatch_secret_write(
            command=command,
            stdin=passphrase,
            device=device,
            detail=pin,
        )
        return rtnval

    def unbind_clevis(
        self,
        device: str,
        slot: int,
    ) -> EncryptionActionResult:
        """
            Remove a Clevis binding via ``clevis luks unbind -d
            <device> -s <slot>``.

            :param device: Block-device path.
            :param slot: Slot index to unbind.

            :returns: An :class:`EncryptionActionResult`.
        """
        command = [
            _CLEVIS, "luks", "unbind",
            "-d", device,
            "-s", str(slot),
        ]
        rtnval = self._dispatch_secret_write(
            command=command,
            stdin=None,
            device=device,
            detail=str(slot),
        )
        return rtnval

    # ------------------------------------------------------------------
    # /etc/crypttab edits
    # ------------------------------------------------------------------

    def add_crypttab_entry(
        self,
        entry: CrypttabEntry,
    ) -> EncryptionActionResult:
        """
            Add ``entry`` to the configured crypttab.  Idempotent:
            when an identical entry already exists, returns
            ``SKIPPED``.

            :param entry: The :class:`CrypttabEntry` to add.

            :returns: An :class:`EncryptionActionResult`.
        """
        target_path = self._typed_config().crypttab_path
        write_command = [_TEE, target_path]

        existing_content = self._read_file(path=target_path)
        if existing_content is None:
            existing_content = ""
        existing_entries = _parse_crypttab(content=existing_content)

        for current in existing_entries:
            if _crypttab_entries_equal(left=current, right=entry):
                skipped = self._build_skipped(
                    command=write_command,
                    detail="crypttab entry {} already present".format(entry.name),
                    name=entry.name,
                )
                return skipped

        new_entries = list(existing_entries)
        new_entries.append(entry)
        new_content = _render_crypttab(entries=new_entries)

        rtnval = self._dispatch_secret_write(
            command=write_command,
            stdin=new_content,
            name=entry.name,
            detail=entry.name,
        )
        return rtnval

    def remove_crypttab_entry(self, name: str) -> EncryptionActionResult:
        """
            Remove the crypttab entry whose mapping name is ``name``.
            Idempotent: when no matching entry exists, returns
            ``SKIPPED``.

            :param name: Mapping name (column 1).

            :returns: An :class:`EncryptionActionResult`.
        """
        target_path = self._typed_config().crypttab_path
        write_command = [_TEE, target_path]

        existing_content = self._read_file(path=target_path)
        if existing_content is None:
            existing_content = ""
        existing_entries = _parse_crypttab(content=existing_content)

        kept_entries: list = []
        found = False
        for current in existing_entries:
            if current.name == name:
                found = True
                continue
            kept_entries.append(current)

        if not found:
            skipped = self._build_skipped(
                command=write_command,
                detail="crypttab entry {} not present".format(name),
                name=name,
            )
            return skipped

        new_content = _render_crypttab(entries=kept_entries)
        rtnval = self._dispatch_secret_write(
            command=write_command,
            stdin=new_content,
            name=name,
            detail=name,
        )
        return rtnval

    def update_crypttab_entry(
        self,
        entry: CrypttabEntry,
    ) -> EncryptionActionResult:
        """
            Replace the crypttab entry whose mapping name matches
            ``entry.name`` with ``entry``.  Idempotent: when an
            identical entry already exists, returns ``SKIPPED``;
            when no entry of that name exists, the new entry is
            appended.

            :param entry: The replacement :class:`CrypttabEntry`.

            :returns: An :class:`EncryptionActionResult`.
        """
        target_path = self._typed_config().crypttab_path
        write_command = [_TEE, target_path]

        existing_content = self._read_file(path=target_path)
        if existing_content is None:
            existing_content = ""
        existing_entries = _parse_crypttab(content=existing_content)

        new_entries: list = []
        replaced = False
        for current in existing_entries:
            if current.name == entry.name:
                if _crypttab_entries_equal(left=current, right=entry):
                    skipped = self._build_skipped(
                        command=write_command,
                        detail="crypttab entry {} already matches".format(entry.name),
                        name=entry.name,
                    )
                    return skipped
                new_entries.append(entry)
                replaced = True
                continue
            new_entries.append(current)

        if not replaced:
            new_entries.append(entry)

        new_content = _render_crypttab(entries=new_entries)
        rtnval = self._dispatch_secret_write(
            command=write_command,
            stdin=new_content,
            name=entry.name,
            detail=entry.name,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _typed_config(self) -> EncryptionManagerConfig:
        """
            Return ``self._config`` typed as
            :class:`EncryptionManagerConfig`, falling back to a
            synthesised default when the caller passed a plain
            :class:`FeatureConfig`.
        """
        if isinstance(self._config, EncryptionManagerConfig):
            rtnval = self._config
            return rtnval
        rtnval = EncryptionManagerConfig(
            dry_run=self._config.dry_run,
            elevated=self._config.elevated,
        )
        return rtnval

    def _read_file(self, *, path: str) -> Optional[str]:
        """
            Read a file via ``cat`` without elevation, returning
            ``None`` when the file does not exist (write paths treat
            a missing file as 'no existing content').

            :param path: The absolute path to read.

            :returns: The file contents on success; ``None`` when
                      ``cat`` reports a non-zero exit code.
        """
        command = [_CAT, path]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: Optional[str] = None
            return rtnval
        rtnval = run_result.stdout
        return rtnval

    def _mapping_exists(self, *, name: str) -> bool:
        """
            Probe whether ``/dev/mapper/<name>`` exists by running
            ``test -b /dev/mapper/<name>`` through the runner.
        """
        target = "/dev/mapper/{}".format(name)
        command = [_TEST, "-b", target]
        run_result = self._runner.run(list(command), elevated=False)
        rtnval = run_result.exit_code == 0
        return rtnval

    def _dispatch_secret_write(
        self,
        *,
        command: list,
        stdin: Optional[str],
        device: Optional[str] = None,
        name: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> EncryptionActionResult:
        """
            Dispatch a mutating command through the
            :class:`SanitisedDispatchMixin` ``_dispatch`` hook.  The
            recorded ``command`` field on the resulting
            :class:`EncryptionActionResult` is the caller-supplied
            argv (no secret material).
        """
        rtnval = self._dispatch(
            command=command,
            detail=detail,
            stdin=stdin,
            operation="encryption_write",
            device=device,
            name=name,
        )
        return rtnval

    def _build_skipped(
        self,
        *,
        command: list,
        detail: str,
        device: Optional[str] = None,
        name: Optional[str] = None,
    ) -> EncryptionActionResult:
        """
            Construct a SKIPPED result without dispatching.
        """
        rtnval = EncryptionActionResult(
            status=EncryptionActionStatus.SKIPPED,
            detail=detail,
            command=list(command),
            device=device,
            name=name,
        )
        return rtnval

    def _build_failed(
        self,
        *,
        command: list,
        detail: str,
        device: Optional[str] = None,
        name: Optional[str] = None,
        stderr: Optional[str] = None,
    ) -> EncryptionActionResult:
        """
            Construct a FAILED result without dispatching.
        """
        rtnval = EncryptionActionResult(
            status=EncryptionActionStatus.FAILED,
            detail=detail,
            command=list(command),
            device=device,
            name=name,
            stderr=stderr,
        )
        return rtnval


# ----------------------------------------------------------------------
# Module-level helpers — credential resolution
# ----------------------------------------------------------------------


def _resolve_credential_str(*, credential) -> str:
    """
        Extract the secret bytes from a credential-shaped object and
        return them as a UTF-8 ``str`` suitable for the runner's
        ``stdin`` parameter.

        Accepts (in order):
        1. An object exposing ``secret_bytes()`` returning ``bytes``
           or ``str``.
        2. An object exposing ``password`` (``str`` or ``bytes``).
        3. A bare ``bytes`` value.
        4. A bare ``str`` value.

        :param credential: The credential-shaped value.

        :returns: The secret as a ``str``.

        :raises EncryptionCredentialError: When no secret can be
                                           resolved.
    """
    if hasattr(credential, "secret_bytes"):
        accessor = credential.secret_bytes
        if callable(accessor):
            payload = accessor()
        else:
            payload = accessor
        if isinstance(payload, bytes):
            rtnval = payload.decode("utf-8")
            return rtnval
        if isinstance(payload, str):
            rtnval = payload
            return rtnval

    if hasattr(credential, "password"):
        password_value = credential.password
        if isinstance(password_value, bytes):
            rtnval = password_value.decode("utf-8")
            return rtnval
        if isinstance(password_value, str):
            rtnval = password_value
            return rtnval

    if isinstance(credential, bytes):
        rtnval = credential.decode("utf-8")
        return rtnval
    if isinstance(credential, str):
        rtnval = credential
        return rtnval

    err_msg_lines = ["Unable to resolve secret from credential."]
    err_msg_lines.append(
        "    CREDENTIAL_TYPE: {}".format(type(credential).__name__),
    )
    err_msg = os.linesep.join(err_msg_lines)
    raise EncryptionCredentialError(err_msg)


# ----------------------------------------------------------------------
# Module-level parsers / renderers
# ----------------------------------------------------------------------


def _parse_luks_dump(*, device: str, output: str) -> LuksHeader:
    """
        Parse ``cryptsetup luksDump`` plain-text output into a
        :class:`LuksHeader`.

        Tolerates both LUKS1 and LUKS2 layouts; LUKS1 prints
        per-slot ``Key Slot N: ENABLED`` lines, LUKS2 prints a
        ``Keyslots:`` section followed by indented ``N: luks2``
        lines.

        :param device: Block-device path (recorded on the returned
                       header).
        :param output: Raw stdout text.

        :returns: A :class:`LuksHeader`.
    """
    version: str = "1"
    uuid_value: Optional[str] = None
    cipher_name: Optional[str] = None
    cipher_mode: Optional[str] = None
    cipher_full: Optional[str] = None
    hash_value: str = ""
    pbkdf_hash: Optional[str] = None
    key_slots: list = []
    tokens: list = []

    in_keyslots = False
    in_tokens = False
    current_slot: Optional[int] = None
    current_slot_key_size: Optional[int] = None
    current_token: Optional[dict] = None
    current_token_id: Optional[int] = None

    for raw_line in output.splitlines():
        line_text = raw_line.rstrip()
        stripped = line_text.strip()
        if len(stripped) == 0:
            continue

        if stripped.startswith("Version:"):
            version_text = stripped.split(":", 1)[1].strip()
            version = version_text
            in_keyslots = False
            in_tokens = False
            continue
        if stripped.startswith("UUID:"):
            uuid_value = stripped.split(":", 1)[1].strip()
            in_keyslots = False
            in_tokens = False
            continue
        if stripped.startswith("Cipher name:"):
            cipher_name = stripped.split(":", 1)[1].strip()
            continue
        if stripped.startswith("Cipher mode:"):
            cipher_mode = stripped.split(":", 1)[1].strip()
            continue
        if stripped.startswith("Cipher:"):
            cipher_full = stripped.split(":", 1)[1].strip()
            continue
        if stripped.startswith("Hash spec:"):
            hash_value = stripped.split(":", 1)[1].strip()
            continue
        if stripped.startswith("PBKDF:"):
            pbkdf_hash = stripped.split(":", 1)[1].strip()
            continue

        if stripped.startswith("Key Slot ") and "ENABLED" in stripped:
            slot_token = stripped.split(":", 1)[0].strip()
            slot_number_str = slot_token.replace("Key Slot ", "")
            try:
                slot_number = int(slot_number_str)
            except ValueError:
                slot_number = -1
            slot_info = KeySlotInfo(slot=slot_number, state="ENABLED")
            key_slots.append(slot_info)
            continue
        if stripped.startswith("Key Slot ") and "DISABLED" in stripped:
            slot_token = stripped.split(":", 1)[0].strip()
            slot_number_str = slot_token.replace("Key Slot ", "")
            try:
                slot_number = int(slot_number_str)
            except ValueError:
                slot_number = -1
            slot_info = KeySlotInfo(slot=slot_number, state="DISABLED")
            key_slots.append(slot_info)
            continue

        if stripped.startswith("Keyslots:"):
            in_keyslots = True
            in_tokens = False
            continue
        if stripped.startswith("Tokens:"):
            in_keyslots = False
            in_tokens = True
            continue
        if stripped.startswith("Digests:"):
            in_keyslots = False
            in_tokens = False
            current_slot = None
            current_token = None
            continue

        if in_keyslots:
            head, _, _tail = stripped.partition(":")
            head_clean = head.strip()
            if head_clean.isdigit():
                current_slot = int(head_clean)
                current_slot_key_size = None
                slot_info = KeySlotInfo(slot=current_slot, state="ENABLED")
                key_slots.append(slot_info)
                continue
            if current_slot is not None and stripped.lower().startswith("key:"):
                value = stripped.split(":", 1)[1].strip()
                bits_token = value.split()[0] if len(value.split()) > 0 else ""
                if bits_token.isdigit():
                    current_slot_key_size = int(bits_token)
                    if len(key_slots) > 0:
                        key_slots[-1].key_size_bits = current_slot_key_size
                continue

        if in_tokens:
            head, _, _tail = stripped.partition(":")
            head_clean = head.strip()
            if head_clean.isdigit():
                current_token_id = int(head_clean)
                current_token = {"id": current_token_id, "raw": stripped}
                tokens.append(current_token)
                continue
            if current_token is not None:
                current_token["raw"] = "{}\n{}".format(
                    current_token.get("raw", ""), stripped,
                )
                if stripped.lower().startswith("keyslot:"):
                    keyslot_text = stripped.split(":", 1)[1].strip()
                    if keyslot_text.isdigit():
                        token_slot_id = int(keyslot_text)
                        for slot_info in key_slots:
                            if slot_info.slot == token_slot_id:
                                slot_info.token = current_token_id
                continue

    if cipher_full is not None:
        cipher_value = cipher_full
    elif cipher_name is not None and cipher_mode is not None:
        cipher_value = "{}-{}".format(cipher_name, cipher_mode)
    elif cipher_name is not None:
        cipher_value = cipher_name
    else:
        cipher_value = ""

    if len(hash_value) == 0 and pbkdf_hash is not None:
        hash_value = pbkdf_hash

    rtnval = LuksHeader(
        device=device,
        version=version,
        uuid=uuid_value,
        cipher=cipher_value,
        hash=hash_value,
        key_slots=key_slots,
        tokens=tokens,
        raw=output,
    )
    return rtnval


def _parse_systemd_cryptenroll(*, output: str) -> list:
    """
        Parse ``systemd-cryptenroll <device>`` plain-text output into
        a list of :class:`CryptBinding`.

        ``systemd-cryptenroll`` (no flags) prints a small table of
        the form::

            SLOT TYPE
               0 password
               1 tpm2
               2 recovery

        Slots may also report ``empty`` — those rows are skipped.

        :param output: Raw stdout text.

        :returns: A list (possibly empty) of :class:`CryptBinding`.
    """
    rtnval: list = []
    for raw_line in output.splitlines():
        stripped = raw_line.strip()
        if len(stripped) == 0:
            continue
        upper = stripped.upper()
        if upper.startswith("SLOT") and "TYPE" in upper:
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        slot_token = parts[0]
        kind_token = parts[1].lower()
        if not slot_token.isdigit():
            continue
        if kind_token == "empty":
            continue
        if kind_token == "tpm2":
            backend_value = "tpm2"
        elif kind_token == "password":
            backend_value = "passphrase"
        else:
            backend_value = kind_token
        binding = CryptBinding(
            slot=int(slot_token),
            backend=backend_value,
            pin=None,
            config=None,
            raw=stripped,
        )
        rtnval.append(binding)
    return rtnval


def _parse_clevis_luks_list(*, output: str) -> list:
    """
        Parse ``clevis luks list -d <device>`` output into a list of
        :class:`CryptBinding` records.

        Each line looks like::

            1: tpm2 '{"hash":"sha256","key":"ecc"}'

        The slot index, pin name, and JSON config are extracted; any
        line that does not match this shape is skipped.

        :param output: Raw stdout text.

        :returns: A list (possibly empty) of :class:`CryptBinding`.
    """
    rtnval: list = []
    for raw_line in output.splitlines():
        stripped = raw_line.strip()
        if len(stripped) == 0:
            continue
        head, sep, tail = stripped.partition(":")
        if len(sep) == 0:
            continue
        slot_token = head.strip()
        if not slot_token.isdigit():
            continue
        body = tail.strip()
        body_parts = body.split(None, 1)
        if len(body_parts) == 0:
            continue
        pin_token = body_parts[0]
        config_value: Optional[dict] = None
        if len(body_parts) >= 2:
            json_text = body_parts[1].strip()
            if (
                len(json_text) >= 2
                and json_text.startswith("'")
                and json_text.endswith("'")
            ):
                json_text = json_text[1:-1]
            try:
                parsed_config = json.loads(json_text)
            except (ValueError, TypeError):
                parsed_config = None
            if isinstance(parsed_config, dict):
                config_value = parsed_config
        binding = CryptBinding(
            slot=int(slot_token),
            backend="clevis",
            pin=pin_token,
            config=config_value,
            raw=stripped,
        )
        rtnval.append(binding)
    return rtnval


def _parse_crypttab(*, content: str) -> list:
    """
        Parse ``/etc/crypttab`` content into a list of
        :class:`CrypttabEntry`.

        Comment lines (``#``) and blank lines are skipped.  Each
        non-comment line carries 2-4 whitespace-separated columns:
        ``<name> <device> [<keyfile> [<options>]]``.  A keyfile of
        ``-`` or ``none`` is normalised to ``None``.  Options are
        comma-split.

        :param content: Raw file content.

        :returns: A list (possibly empty) of :class:`CrypttabEntry`.
    """
    rtnval: list = []
    for raw_line in content.splitlines():
        line_text = raw_line.strip()
        if len(line_text) == 0:
            continue
        if line_text.startswith("#"):
            continue
        parts = line_text.split()
        if len(parts) < 2:
            continue
        name_value = parts[0]
        device_value = parts[1]
        keyfile_value: Optional[str] = None
        if len(parts) >= 3:
            keyfile_token = parts[2]
            if keyfile_token == "-" or keyfile_token.lower() == "none":
                keyfile_value = None
            else:
                keyfile_value = keyfile_token
        options_list: list = []
        if len(parts) >= 4:
            options_token = parts[3]
            if options_token != "-":
                pieces = options_token.split(",")
                for piece in pieces:
                    cleaned = piece.strip()
                    if len(cleaned) > 0:
                        options_list.append(cleaned)
        entry = CrypttabEntry(
            name=name_value,
            device=device_value,
            keyfile=keyfile_value,
            options=options_list,
        )
        rtnval.append(entry)
    return rtnval


def _render_crypttab(*, entries: list) -> str:
    """
        Render a list of :class:`CrypttabEntry` into the canonical
        ``/etc/crypttab`` whitespace layout.

        :param entries: The entries to render.

        :returns: A string ending with a newline.
    """
    lines: list = []
    for entry in entries:
        if entry.keyfile is None:
            keyfile_token = "-"
        else:
            keyfile_token = entry.keyfile
        if entry.options is None or len(entry.options) == 0:
            options_token = "-"
        else:
            options_token = ",".join(entry.options)
        line_text = "{} {} {} {}".format(
            entry.name,
            entry.device,
            keyfile_token,
            options_token,
        )
        lines.append(line_text)
    if len(lines) == 0:
        rtnval = ""
        return rtnval
    rtnval = "\n".join(lines) + "\n"
    return rtnval


def _crypttab_entries_equal(
    *,
    left: CrypttabEntry,
    right: CrypttabEntry,
) -> bool:
    """
        Compare two :class:`CrypttabEntry` records for full equality
        across name, device, keyfile, and options (order-sensitive).
    """
    if left.name != right.name:
        rtnval = False
        return rtnval
    if left.device != right.device:
        rtnval = False
        return rtnval
    if left.keyfile != right.keyfile:
        rtnval = False
        return rtnval
    if list(left.options) != list(right.options):
        rtnval = False
        return rtnval
    rtnval = True
    return rtnval
