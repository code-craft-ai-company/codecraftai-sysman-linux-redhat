# automojo.sysman.linux.redhat.sambaservermanagement
#
# SambaServerManager wraps the RHEL Samba toolchain — `testparm`,
# `smbpasswd`, `pdbedit`, `net ads`, `wbinfo`, plus the on-disk
# content of `/etc/samba/smb.conf` — through the ICommandRunner
# abstraction.  Service lifecycle (`smb`, `nmb`, `winbind`) is
# delegated to an injected ServiceManager.
#
# RHEL service-name caveat: the RHEL systemd units are named ``smb``,
# ``nmb``, and ``winbind`` (NOT ``smbd`` / ``nmbd`` as on Debian).

import os

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from automojo.errors.exceptions import SemanticError

from automojo.sysman.linux.redhat.featurebase import (
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
    SanitisedDispatchMixin,
)


# ----------------------------------------------------------------------
# Module-level command constants
# ----------------------------------------------------------------------

_CAT = "cat"
_TEE = "tee"
_TESTPARM = "testparm"
_SMBPASSWD = "smbpasswd"
_PDBEDIT = "pdbedit"
_NET = "net"
_WBINFO = "wbinfo"

_DEFAULT_SMB_CONF = "/etc/samba/smb.conf"
_DEFAULT_SMB_CONF_DIR = "/etc/samba/smb.conf.d"

# Sections excluded from share enumeration per requirement 2.4.
_RESERVED_SECTION_NAMES = ("global", "homes", "printers")

# RHEL systemd unit names (NOT smbd / nmbd as on Debian).
_SMB_UNIT = "smb"
_NMB_UNIT = "nmb"
_WINBIND_UNIT = "winbind"


# ----------------------------------------------------------------------
# Status enum
# ----------------------------------------------------------------------


class SambaActionStatus(Enum):
    """
        Outcome status for samba-server-management actions.
    """
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------


@dataclass
class SambaServerManagerConfig(FeatureConfig):
    """
        Configuration for :class:`SambaServerManager`.

        Inherits ``dry_run`` and ``elevated`` from
        :class:`FeatureConfig`.

        :param smb_conf: Absolute path to the primary Samba
                         configuration file.  Defaults to the RHEL
                         stock location ``/etc/samba/smb.conf``.
        :param smb_conf_dir: Directory containing drop-in
                             configuration fragments.  Reserved for
                             future drop-in authoring; currently
                             recorded for callers' reference only.
    """
    smb_conf: str = _DEFAULT_SMB_CONF
    smb_conf_dir: str = _DEFAULT_SMB_CONF_DIR


# ----------------------------------------------------------------------
# Result dataclass
# ----------------------------------------------------------------------


@dataclass
class SambaActionResult(FeatureActionResult):
    """
        Outcome of a samba-server-management action.

        :param share: The share name the action targeted, or ``None``
                      for verbs that do not target a single share.
        :param username: The Samba user the action targeted, or
                         ``None`` for share-scoped verbs.
    """
    share: Optional[str] = None
    username: Optional[str] = None


# ----------------------------------------------------------------------
# Domain dataclasses
# ----------------------------------------------------------------------


@dataclass
class ShareSpec:
    """
        Declarative spec for a single Samba share.

        :param name: The SMB share name as clients see it.
        :param path: The filesystem path exposed by the share.
        :param comment: Optional free-form description rendered as
                        the ``comment`` directive.
        :param writable: Optional ``writable`` directive.
        :param browseable: Optional ``browseable`` directive.
        :param guest_ok: Optional ``guest ok`` directive.
        :param valid_users: Optional list of users allowed access.
        :param read_list: Optional list of users granted read-only
                          access.
        :param write_list: Optional list of users granted read-write
                           access.
        :param create_mask: Optional ``create mask`` directive.
        :param directory_mask: Optional ``directory mask`` directive.
        :param extra_directives: Free-form extra ``key = value``
                                 directives.
    """
    name: str
    path: str
    comment: Optional[str] = None
    writable: Optional[bool] = None
    browseable: Optional[bool] = None
    guest_ok: Optional[bool] = None
    valid_users: Optional[list[str]] = None
    read_list: Optional[list[str]] = None
    write_list: Optional[list[str]] = None
    create_mask: Optional[str] = None
    directory_mask: Optional[str] = None
    extra_directives: dict[str, str] = field(default_factory=dict)


@dataclass
class ShareInfo:
    """
        One parsed share record.

        :param name: The share name.
        :param directives: The full, parsed directive map for the
                           share (normalised key → value).
    """
    name: str
    directives: dict[str, str] = field(default_factory=dict)


@dataclass
class SambaValidateResult:
    """
        Result of ``testparm -s --suppress-prompt``.

        :param ok: ``True`` when the validator returned exit code 0.
        :param exit_code: Validator's exit code.
        :param stderr: Validator's captured stderr.
        :param warnings: Lines from stderr that look like warnings
                         (``WARNING:`` / ``Bad value`` / ``Unknown
                         parameter`` lines).
        :param parsed_sections: Section names that the validator
                                successfully parsed (from stdout
                                section headers).
    """
    ok: bool
    exit_code: int
    stderr: str
    warnings: list[str] = field(default_factory=list)
    parsed_sections: list[str] = field(default_factory=list)


@dataclass
class SambaUserInfo:
    """
        One parsed Samba user record from ``pdbedit -L``.

        :param username: The user's login name.
        :param uid: Numeric uid, or ``None`` when not present.
        :param flags: The account flag string from ``pdbedit -v``
                      (e.g. ``[U          ]``).  Empty when the
                      short ``pdbedit -L`` form was parsed.
        :param last_password_change: Free-form text of the last
                                     password change as reported by
                                     ``pdbedit -v``, or ``None`` when
                                     absent.
    """
    username: str
    uid: Optional[int]
    flags: str
    last_password_change: Optional[str]


# ----------------------------------------------------------------------
# Exceptions
# ----------------------------------------------------------------------


class SambaCredentialError(SemanticError):
    """
        Raised when a Samba password / AD-admin credential is
        required but not supplied.  Bubbles unconditionally — never
        caught by the manager.
    """
    pass


class SambaQueryError(Exception):
    """
        Raised when a Samba read fails for a reason other than the
        target file simply being absent.
    """

    def __init__(
        self,
        *,
        command: list[str],
        exit_code: Optional[int],
        stderr: str,
    ) -> None:
        """
            :param command: The argv that failed.
            :param exit_code: The runner's exit code.
            :param stderr: The runner's captured stderr.
        """
        self.command = list(command)
        self.exit_code = exit_code
        self.stderr = stderr

        err_msg_lines = ["Samba server query command failed."]
        err_msg_lines.append("    COMMAND: {}".format(" ".join(command)))
        err_msg_lines.append("    EXIT_CODE: {}".format(exit_code))
        err_msg_lines.append("    STDERR: {}".format(stderr))
        err_msg = os.linesep.join(err_msg_lines)
        super().__init__(err_msg)
        return


# ----------------------------------------------------------------------
# Internal section model used by the parser / renderer
# ----------------------------------------------------------------------


@dataclass
class _SmbSection:
    """
        Internal model carrying a single parsed ``smb.conf`` section.

        Free-form preamble lines (comments / blanks before the first
        section header) are stored under a synthetic section with
        ``name=None``.

        :param name: Section name without the surrounding brackets,
                     or ``None`` for the synthetic preamble section.
        :param header_line: The verbatim ``[name]`` header line as
                            written in the file (preserves
                            whitespace), or ``None`` for the
                            preamble.
        :param body_lines: Verbatim body lines (every line after the
                           header up to the next header).  Comments
                           and blanks are preserved.
    """
    name: Optional[str]
    header_line: Optional[str]
    body_lines: list[str]


# ----------------------------------------------------------------------
# Manager
# ----------------------------------------------------------------------


class SambaServerManager(SanitisedDispatchMixin, BaseFeatureManager):
    """
        Facade for samba-server-management on RHEL.

        Reads, writes, and reloads ``/etc/samba/smb.conf`` through
        the injected :class:`ICommandRunner`; delegates SMB / NMB /
        winbind service lifecycle to the injected
        :class:`ServiceManager`.

        Mixes :class:`SanitisedDispatchMixin` in so any dispatch with
        passphrase bytes on stdin records the bare argv (no secret
        material) on the returned :class:`SambaActionResult`.
    """

    result_class = SambaActionResult
    status_class = SambaActionStatus
    feature_name = "samba-server-management"

    def __init__(
        self,
        runner,
        config: Optional[SambaServerManagerConfig] = None,
        service_manager=None,
    ) -> None:
        """
            :param runner: The :class:`ICommandRunner` used by every
                           dispatch.
            :param config: Manager configuration.  Defaults to a
                           fresh :class:`SambaServerManagerConfig`.
            :param service_manager: Optional injected
                                    :class:`ServiceManager` used by
                                    :meth:`reload` / :meth:`restart`
                                    to drive ``smb`` / ``nmb`` /
                                    ``winbind`` lifecycle.  When
                                    ``None`` and a lifecycle method
                                    is invoked, those methods return
                                    ``FAILED`` with an explanatory
                                    detail.
        """
        if config is None:
            config = SambaServerManagerConfig()
        super().__init__(runner=runner, config=config)
        self._service_manager = service_manager
        return

    # ------------------------------------------------------------------
    # Global directives
    # ------------------------------------------------------------------

    def get_globals(self) -> dict[str, str]:
        """
            Return the ``[global]`` directive map.

            :returns: ``dict[str, str]`` of normalised directives.
                      Empty when ``smb.conf`` is missing or has no
                      ``[global]`` section.
        """
        content = self._read_file(path=self._typed_config().smb_conf)
        if content is None:
            rtnval: dict[str, str] = {}
            return rtnval

        sections = _parse_smb_conf(content=content)
        for section in sections:
            if section.name is None:
                continue
            if section.name.strip().lower() == "global":
                rtnval = _parse_directives(body_lines=section.body_lines)
                return rtnval

        rtnval = {}
        return rtnval

    def set_global(self, key: str, value: str) -> SambaActionResult:
        """
            Upsert ``key = value`` in the ``[global]`` section.

            :param key: The directive key (case-insensitive).
            :param value: The directive value.

            :returns: A :class:`SambaActionResult`.  ``SKIPPED`` when
                      the directive already matches.
        """
        target_path = self._typed_config().smb_conf
        write_command = [_TEE, target_path]

        content = self._read_file(path=target_path)
        if content is None:
            content = ""
        sections = _parse_smb_conf(content=content)
        sections, ensured = _ensure_global_section(sections=sections)

        normalised_key = " ".join(key.lower().split())
        existing_directives = _parse_directives(
            body_lines=_get_section(sections=sections, name="global").body_lines,
        )
        if existing_directives.get(normalised_key) == value:
            skipped = SambaActionResult(
                status=SambaActionStatus.SKIPPED,
                detail="global:{}".format(normalised_key),
                command=list(write_command),
                share=None,
                username=None,
            )
            return skipped

        sections = _set_directive_in_section(
            sections=sections,
            section_name="global",
            key=normalised_key,
            value=value,
        )
        new_content = _render_smb_conf(sections=sections)

        rtnval = self._dispatch(
            command=write_command,
            detail="global:{}".format(normalised_key),
            stdin=new_content,
            operation="set_global",
            share=None,
            username=None,
        )
        return rtnval

    def remove_global(self, key: str) -> SambaActionResult:
        """
            Strip a directive from the ``[global]`` section.

            :param key: The directive key (case-insensitive).

            :returns: A :class:`SambaActionResult`.  ``SKIPPED`` when
                      the directive is already absent.
        """
        target_path = self._typed_config().smb_conf
        write_command = [_TEE, target_path]

        content = self._read_file(path=target_path)
        if content is None:
            skipped = SambaActionResult(
                status=SambaActionStatus.SKIPPED,
                detail="global:{}".format(key),
                command=list(write_command),
                share=None,
                username=None,
            )
            return skipped

        sections = _parse_smb_conf(content=content)
        normalised_key = " ".join(key.lower().split())
        global_section = _get_section_or_none(sections=sections, name="global")
        if global_section is None:
            skipped = SambaActionResult(
                status=SambaActionStatus.SKIPPED,
                detail="global:{}".format(normalised_key),
                command=list(write_command),
                share=None,
                username=None,
            )
            return skipped

        existing_directives = _parse_directives(
            body_lines=global_section.body_lines,
        )
        if normalised_key not in existing_directives:
            skipped = SambaActionResult(
                status=SambaActionStatus.SKIPPED,
                detail="global:{}".format(normalised_key),
                command=list(write_command),
                share=None,
                username=None,
            )
            return skipped

        sections = _remove_directive_from_section(
            sections=sections,
            section_name="global",
            key=normalised_key,
        )
        new_content = _render_smb_conf(sections=sections)

        rtnval = self._dispatch(
            command=write_command,
            detail="global:{}".format(normalised_key),
            stdin=new_content,
            operation="remove_global",
            share=None,
            username=None,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Share CRUD
    # ------------------------------------------------------------------

    def list_shares(self) -> list[ShareInfo]:
        """
            Enumerate every non-reserved share defined in the
            primary ``smb.conf`` file.

            :returns: A list of :class:`ShareInfo`.  Empty when the
                      file is missing.

            :raises SambaQueryError: When ``cat`` fails for a reason
                                     other than the file being
                                     absent.
        """
        content = self._read_file_or_raise(
            path=self._typed_config().smb_conf,
        )
        if content is None:
            rtnval: list[ShareInfo] = []
            return rtnval

        sections = _parse_smb_conf(content=content)

        rtnval = []
        for section in sections:
            if section.name is None:
                continue
            normalised = section.name.strip().lower()
            if normalised in _RESERVED_SECTION_NAMES:
                continue
            directives = _parse_directives(body_lines=section.body_lines)
            info = ShareInfo(name=section.name, directives=directives)
            rtnval.append(info)
        return rtnval

    def query_share(self, name: str) -> Optional[ShareInfo]:
        """
            Look up a single share by name.

            :param name: The share name.

            :returns: The :class:`ShareInfo`, or ``None`` when no
                      matching section exists or the file is missing.

            :raises SambaQueryError: When ``cat`` fails for a reason
                                     other than the file being
                                     absent.
        """
        content = self._read_file_or_raise(
            path=self._typed_config().smb_conf,
        )
        if content is None:
            rtnval: Optional[ShareInfo] = None
            return rtnval

        sections = _parse_smb_conf(content=content)
        for section in sections:
            if section.name is None:
                continue
            if section.name == name:
                directives = _parse_directives(body_lines=section.body_lines)
                rtnval = ShareInfo(name=section.name, directives=directives)
                return rtnval
        rtnval = None
        return rtnval

    def define_share(self, spec: ShareSpec) -> SambaActionResult:
        """
            Idempotently upsert ``[spec.name]`` carrying the
            directives implied by ``spec``.

            Returns ``SKIPPED`` when the existing block already
            carries every requested directive (other directives may
            also be present).  Honours dry-run.

            :param spec: The :class:`ShareSpec`.

            :returns: The :class:`SambaActionResult`.
        """
        target_path = self._typed_config().smb_conf
        write_command = [_TEE, target_path]

        existing = self._read_file(path=target_path)
        existing_content = "" if existing is None else existing
        sections = _parse_smb_conf(content=existing_content)

        requested_directives = _spec_to_directives(spec=spec)

        existing_section = _get_section_or_none(
            sections=sections, name=spec.name,
        )
        if existing_section is not None:
            existing_directives = _parse_directives(
                body_lines=existing_section.body_lines,
            )
            if _requested_directives_present(
                requested=requested_directives,
                existing=existing_directives,
            ):
                skipped = SambaActionResult(
                    status=SambaActionStatus.SKIPPED,
                    detail="define_share:{}".format(spec.name),
                    command=list(write_command),
                    share=spec.name,
                    username=None,
                )
                return skipped

        new_section = _build_share_section(
            name=spec.name, directives=requested_directives,
        )
        sections = _upsert_section(sections=sections, new_section=new_section)
        new_content = _render_smb_conf(sections=sections)

        rtnval = self._dispatch(
            command=write_command,
            detail="define_share:{}".format(spec.name),
            stdin=new_content,
            operation="define_share",
            share=spec.name,
            username=None,
        )
        return rtnval

    def update_share(self, spec: ShareSpec) -> SambaActionResult:
        """
            Replace the directive set of an existing
            ``[spec.name]`` block atomically (one file write).

            When the block does not exist, behaves like
            :meth:`define_share`.

            :param spec: The :class:`ShareSpec`.

            :returns: The :class:`SambaActionResult`.
        """
        target_path = self._typed_config().smb_conf
        write_command = [_TEE, target_path]

        existing = self._read_file(path=target_path)
        existing_content = "" if existing is None else existing
        sections = _parse_smb_conf(content=existing_content)

        requested_directives = _spec_to_directives(spec=spec)
        new_section = _build_share_section(
            name=spec.name, directives=requested_directives,
        )
        sections = _upsert_section(sections=sections, new_section=new_section)
        new_content = _render_smb_conf(sections=sections)

        rtnval = self._dispatch(
            command=write_command,
            detail="update_share:{}".format(spec.name),
            stdin=new_content,
            operation="update_share",
            share=spec.name,
            username=None,
        )
        return rtnval

    def remove_share(self, name: str) -> SambaActionResult:
        """
            Strip the entire ``[name]`` block from
            ``smb.conf`` (header + every directive until the next
            section header).

            Idempotent: ``SKIPPED`` when the file is missing or the
            block is already absent.  Honours dry-run.

            :param name: The share name.

            :returns: The :class:`SambaActionResult`.
        """
        target_path = self._typed_config().smb_conf
        write_command = [_TEE, target_path]

        existing = self._read_file(path=target_path)
        if existing is None:
            skipped = SambaActionResult(
                status=SambaActionStatus.SKIPPED,
                detail="remove_share:{}".format(name),
                command=list(write_command),
                share=name,
                username=None,
            )
            return skipped

        sections = _parse_smb_conf(content=existing)
        target_section = _get_section_or_none(sections=sections, name=name)
        if target_section is None:
            skipped = SambaActionResult(
                status=SambaActionStatus.SKIPPED,
                detail="remove_share:{}".format(name),
                command=list(write_command),
                share=name,
                username=None,
            )
            return skipped

        sections = _remove_section_by_name(sections=sections, name=name)
        new_content = _render_smb_conf(sections=sections)

        rtnval = self._dispatch(
            command=write_command,
            detail="remove_share:{}".format(name),
            stdin=new_content,
            operation="remove_share",
            share=name,
            username=None,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Validate, reload, restart
    # ------------------------------------------------------------------

    def validate_config(self) -> SambaValidateResult:
        """
            Run ``testparm -s --suppress-prompt`` and parse the
            output.

            :returns: A :class:`SambaValidateResult`.
        """
        command = [_TESTPARM, "-s", "--suppress-prompt"]
        run_result = self._runner.run(list(command), elevated=False)

        warnings = _extract_testparm_warnings(stderr=run_result.stderr)
        parsed_sections = _extract_testparm_sections(stdout=run_result.stdout)

        ok = run_result.exit_code == 0
        rtnval = SambaValidateResult(
            ok=ok,
            exit_code=run_result.exit_code,
            stderr=run_result.stderr,
            warnings=warnings,
            parsed_sections=parsed_sections,
        )
        return rtnval

    def reload(self) -> SambaActionResult:
        """
            Validate then delegate a reload of ``smb`` (and
            ``nmb``) to the injected :class:`ServiceManager`.

            When validation fails, returns ``FAILED`` without
            touching the service.  Honours dry-run.

            :returns: The :class:`SambaActionResult`.
        """
        if self._config.dry_run:
            dry = SambaActionResult(
                status=SambaActionStatus.DRY_RUN,
                detail="reload",
                command=["systemctl", "reload", _SMB_UNIT],
                share=None,
                username=None,
            )
            return dry

        validate = self.validate_config()
        if not validate.ok:
            failed = SambaActionResult(
                status=SambaActionStatus.FAILED,
                detail="testparm validation failed",
                exit_code=validate.exit_code,
                stderr=validate.stderr,
                command=[_TESTPARM, "-s", "--suppress-prompt"],
                share=None,
                username=None,
            )
            return failed

        if self._service_manager is None:
            failed = SambaActionResult(
                status=SambaActionStatus.FAILED,
                detail="no service manager injected",
                command=["systemctl", "reload", _SMB_UNIT],
                share=None,
                username=None,
            )
            return failed

        smb_result = self._service_manager.reload(_SMB_UNIT)
        rtnval = self._coerce_service_result(
            service_result=smb_result,
            detail="reload:{}".format(_SMB_UNIT),
            fallback_command=["systemctl", "reload", _SMB_UNIT],
        )
        return rtnval

    def restart(
        self,
        services: Optional[list[str]] = None,
    ) -> list[SambaActionResult]:
        """
            Delegate ``restart`` of one or more Samba units (default
            ``["smb", "nmb"]``) to the injected
            :class:`ServiceManager`.

            :param services: Unit names to restart; defaults to
                             ``["smb", "nmb"]``.

            :returns: One :class:`SambaActionResult` per requested
                      unit, in declaration order.
        """
        if services is None:
            services = [_SMB_UNIT, _NMB_UNIT]

        rtnval: list[SambaActionResult] = []

        if self._config.dry_run:
            for unit_name in services:
                dry = SambaActionResult(
                    status=SambaActionStatus.DRY_RUN,
                    detail="restart:{}".format(unit_name),
                    command=["systemctl", "restart", unit_name],
                    share=None,
                    username=None,
                )
                rtnval.append(dry)
            return rtnval

        if self._service_manager is None:
            for unit_name in services:
                failed = SambaActionResult(
                    status=SambaActionStatus.FAILED,
                    detail="no service manager injected",
                    command=["systemctl", "restart", unit_name],
                    share=None,
                    username=None,
                )
                rtnval.append(failed)
            return rtnval

        for unit_name in services:
            unit_result = self._service_manager.restart(unit_name)
            mapped = self._coerce_service_result(
                service_result=unit_result,
                detail="restart:{}".format(unit_name),
                fallback_command=["systemctl", "restart", unit_name],
            )
            rtnval.append(mapped)
        return rtnval

    # ------------------------------------------------------------------
    # Samba user management
    # ------------------------------------------------------------------

    def list_samba_users(self) -> list[SambaUserInfo]:
        """
            Enumerate Samba users via ``pdbedit -L``.

            :returns: A list of :class:`SambaUserInfo`.

            :raises SambaQueryError: When ``pdbedit`` exits non-zero
                                     with non-empty stderr.
        """
        command = [_PDBEDIT, "-L"]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0 and len(run_result.stderr.strip()) > 0:
            raise SambaQueryError(
                command=command,
                exit_code=run_result.exit_code,
                stderr=run_result.stderr,
            )
        rtnval = _parse_pdbedit_short(text=run_result.stdout)
        return rtnval

    def add_samba_user(
        self,
        username: str,
        credential,
    ) -> SambaActionResult:
        """
            Register a Samba user via ``smbpasswd -a -s <username>``.

            The password is delivered via runner ``stdin`` twice
            (``-s`` reads new and confirmation off stdin).  Never
            on argv.

            :param username: The Samba user's login name.
            :param credential: An object exposing
                               ``secret_bytes()`` (or a plain ``str``
                               / ``bytes`` payload — see
                               :func:`_resolve_credential_bytes`).
                               When ``None``, raises
                               :class:`SambaCredentialError`.

            :returns: The :class:`SambaActionResult`.

            :raises SambaCredentialError: When ``credential`` is
                                          ``None``.
        """
        if credential is None:
            err_msg = "add_samba_user requires a non-None credential"
            raise SambaCredentialError(err_msg)

        sanitised_command = [_SMBPASSWD, "-a", "-s", username]

        if self._config.dry_run:
            dry = SambaActionResult(
                status=SambaActionStatus.DRY_RUN,
                detail="add_samba_user:{}".format(username),
                command=list(sanitised_command),
                share=None,
                username=username,
            )
            return dry

        secret_bytes = _resolve_credential_bytes(credential=credential)
        stdin_payload = (
            secret_bytes.decode("utf-8") + "\n"
            + secret_bytes.decode("utf-8") + "\n"
        )

        rtnval = self._dispatch(
            command=sanitised_command,
            detail="add_samba_user:{}".format(username),
            stdin=stdin_payload,
            operation="add_samba_user",
            share=None,
            username=username,
        )
        return rtnval

    def delete_samba_user(self, username: str) -> SambaActionResult:
        """
            Remove a Samba user via ``smbpasswd -x <username>``.

            :param username: The Samba user's login name.

            :returns: The :class:`SambaActionResult`.
        """
        command = [_SMBPASSWD, "-x", username]
        rtnval = self._dispatch(
            command=command,
            detail="delete_samba_user:{}".format(username),
            operation="delete_samba_user",
            share=None,
            username=username,
        )
        return rtnval

    def enable_samba_user(self, username: str) -> SambaActionResult:
        """
            Enable a Samba user via ``smbpasswd -e <username>``.

            :param username: The Samba user's login name.

            :returns: The :class:`SambaActionResult`.
        """
        command = [_SMBPASSWD, "-e", username]
        rtnval = self._dispatch(
            command=command,
            detail="enable_samba_user:{}".format(username),
            operation="enable_samba_user",
            share=None,
            username=username,
        )
        return rtnval

    def disable_samba_user(self, username: str) -> SambaActionResult:
        """
            Disable a Samba user via ``smbpasswd -d <username>``.

            :param username: The Samba user's login name.

            :returns: The :class:`SambaActionResult`.
        """
        command = [_SMBPASSWD, "-d", username]
        rtnval = self._dispatch(
            command=command,
            detail="disable_samba_user:{}".format(username),
            operation="disable_samba_user",
            share=None,
            username=username,
        )
        return rtnval

    # ------------------------------------------------------------------
    # AD join
    # ------------------------------------------------------------------

    def join_ad(
        self,
        domain: str,
        admin_credential,
    ) -> SambaActionResult:
        """
            Join the Samba server to an Active Directory domain via
            ``net ads join -U <user>``.  The password is delivered
            via runner ``stdin``, never on argv.

            :param domain: The AD domain (recorded as detail; the
                           ``net ads join`` tool reads the joining
                           domain from the local Kerberos / smb.conf
                           configuration).
            :param admin_credential: Credential carrying ``username``
                                     and a ``secret_bytes()`` /
                                     ``password`` accessor.  When
                                     ``None``, raises
                                     :class:`SambaCredentialError`.

            :returns: The :class:`SambaActionResult`.

            :raises SambaCredentialError: When ``admin_credential``
                                          is ``None``.
        """
        if admin_credential is None:
            err_msg = "join_ad requires a non-None admin credential"
            raise SambaCredentialError(err_msg)

        admin_user = _resolve_credential_username(
            credential=admin_credential,
        )
        sanitised_command = [_NET, "ads", "join", "-U", admin_user]

        if self._config.dry_run:
            dry = SambaActionResult(
                status=SambaActionStatus.DRY_RUN,
                detail="join_ad:{}".format(domain),
                command=list(sanitised_command),
                share=None,
                username=admin_user,
            )
            return dry

        secret_bytes = _resolve_credential_bytes(credential=admin_credential)
        stdin_payload = secret_bytes.decode("utf-8")

        rtnval = self._dispatch(
            command=sanitised_command,
            detail="join_ad:{}".format(domain),
            stdin=stdin_payload,
            operation="join_ad",
            share=None,
            username=admin_user,
        )
        return rtnval

    def leave_ad(self, admin_credential) -> SambaActionResult:
        """
            Leave the AD domain via ``net ads leave -U <user>``.
            The password is delivered via runner ``stdin``, never on
            argv.

            :param admin_credential: Credential carrying ``username``
                                     and password.  When ``None``,
                                     raises
                                     :class:`SambaCredentialError`.

            :returns: The :class:`SambaActionResult`.

            :raises SambaCredentialError: When ``admin_credential``
                                          is ``None``.
        """
        if admin_credential is None:
            err_msg = "leave_ad requires a non-None admin credential"
            raise SambaCredentialError(err_msg)

        admin_user = _resolve_credential_username(
            credential=admin_credential,
        )
        sanitised_command = [_NET, "ads", "leave", "-U", admin_user]

        if self._config.dry_run:
            dry = SambaActionResult(
                status=SambaActionStatus.DRY_RUN,
                detail="leave_ad",
                command=list(sanitised_command),
                share=None,
                username=admin_user,
            )
            return dry

        secret_bytes = _resolve_credential_bytes(credential=admin_credential)
        stdin_payload = secret_bytes.decode("utf-8")

        rtnval = self._dispatch(
            command=sanitised_command,
            detail="leave_ad",
            stdin=stdin_payload,
            operation="leave_ad",
            share=None,
            username=admin_user,
        )
        return rtnval

    def test_join(self) -> bool:
        """
            Test current AD-domain membership via
            ``net ads testjoin``.

            :returns: ``True`` when ``net ads testjoin`` exits 0.
        """
        command = [_NET, "ads", "testjoin"]
        run_result = self._runner.run(
            list(command), elevated=self._config.elevated,
        )
        rtnval = run_result.exit_code == 0
        return rtnval

    def list_trusted_domains(self) -> list[str]:
        """
            Enumerate trusted domains via ``wbinfo
            --trusted-domains``.

            :returns: The list of trusted-domain names.

            :raises SambaQueryError: When ``wbinfo`` exits non-zero
                                     with non-empty stderr.
        """
        command = [_WBINFO, "--trusted-domains"]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0 and len(run_result.stderr.strip()) > 0:
            raise SambaQueryError(
                command=command,
                exit_code=run_result.exit_code,
                stderr=run_result.stderr,
            )

        rtnval: list[str] = []
        for raw in run_result.stdout.splitlines():
            stripped = raw.strip()
            if len(stripped) == 0:
                continue
            # ``wbinfo --trusted-domains`` emits one domain name per
            # line; some builds add a status suffix separated by
            # whitespace — keep the leading token.
            parts = stripped.split()
            if len(parts) > 0:
                rtnval.append(parts[0])
        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _typed_config(self) -> SambaServerManagerConfig:
        """
            Narrow the base config to the typed subclass.
        """
        rtnval = self._config
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

    def _read_file_or_raise(self, *, path: str) -> Optional[str]:
        """
            Read a file via ``cat`` without elevation.  Distinguishes
            'file absent' (return ``None``) from 'unexpected failure'
            (raise :class:`SambaQueryError`).

            :param path: The absolute path to read.

            :returns: The file contents on success; ``None`` when
                      ``cat`` exits non-zero with empty stderr.

            :raises SambaQueryError: When ``cat`` exits non-zero with
                                     non-empty stderr.
        """
        command = [_CAT, path]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code == 0:
            rtnval: Optional[str] = run_result.stdout
            return rtnval
        if len(run_result.stderr.strip()) > 0:
            raise SambaQueryError(
                command=command,
                exit_code=run_result.exit_code,
                stderr=run_result.stderr,
            )
        rtnval = None
        return rtnval

    def _coerce_service_result(
        self,
        *,
        service_result,
        detail: str,
        fallback_command: list[str],
    ) -> SambaActionResult:
        """
            Map a :class:`ServiceActionResult` (or any
            :class:`FeatureActionResult`-shaped object) into a
            :class:`SambaActionResult`.

            :param service_result: The injected ServiceManager's
                                   per-call result.
            :param detail: Detail string to record on the mapped
                           result.
            :param fallback_command: Argv to record when the
                                     ``service_result`` does not
                                     carry a ``command`` field.

            :returns: A :class:`SambaActionResult`.
        """
        # Status — translate by enum value name to be robust to the
        # ServiceManager's distinct enum class.
        status_value = getattr(
            getattr(service_result, "status", None), "value", None,
        )
        if status_value is None:
            mapped_status = SambaActionStatus.FAILED
        else:
            try:
                mapped_status = SambaActionStatus(status_value)
            except ValueError:
                mapped_status = SambaActionStatus.FAILED

        recorded_command = getattr(service_result, "command", None)
        if recorded_command is None:
            recorded_command = list(fallback_command)
        else:
            recorded_command = list(recorded_command)

        rtnval = SambaActionResult(
            status=mapped_status,
            detail=detail,
            exit_code=getattr(service_result, "exit_code", None),
            stdout=getattr(service_result, "stdout", None),
            stderr=getattr(service_result, "stderr", None),
            command=recorded_command,
            share=None,
            username=None,
        )
        return rtnval


# ----------------------------------------------------------------------
# Module-level helpers — credential resolution
# ----------------------------------------------------------------------


def _resolve_credential_bytes(*, credential) -> bytes:
    """
        Extract the secret bytes from a credential-shaped object.

        Accepts (in order):
        1. An object exposing ``secret_bytes()`` returning ``bytes``.
        2. An object exposing ``password`` (``str`` or ``bytes``).
        3. A bare ``bytes`` value.
        4. A bare ``str`` value.

        :param credential: The credential-shaped value.

        :returns: ``bytes`` carrying the secret payload.

        :raises SambaCredentialError: When no secret bytes can be
                                      resolved.
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
    raise SambaCredentialError(err_msg)


def _resolve_credential_username(*, credential) -> str:
    """
        Extract the principal username from a credential-shaped
        object.

        Accepts an object exposing ``username``, ``user``, or
        ``principal`` — first one wins.

        :param credential: The credential-shaped value.

        :returns: The username string.

        :raises SambaCredentialError: When no username field is
                                      exposed.
    """
    for attr_name in ("username", "user", "principal"):
        if hasattr(credential, attr_name):
            value = getattr(credential, attr_name)
            if isinstance(value, str) and len(value) > 0:
                rtnval = value
                return rtnval

    err_msg_lines = ["Unable to resolve username from credential."]
    err_msg_lines.append(
        "    CREDENTIAL_TYPE: {}".format(type(credential).__name__),
    )
    err_msg = os.linesep.join(err_msg_lines)
    raise SambaCredentialError(err_msg)


# ----------------------------------------------------------------------
# Module-level helpers — smb.conf parser / renderer
# ----------------------------------------------------------------------


def _parse_smb_conf(*, content: str) -> list[_SmbSection]:
    """
        Parse an ``smb.conf`` body into an ordered list of
        :class:`_SmbSection` records.

        Section headers are lines whose first non-whitespace
        character is ``[`` and which end with ``]``.  Any preamble
        (comments / blanks before the first header) is captured
        under a synthetic section with ``name=None``.  Comments and
        blank lines inside a section body are preserved verbatim.

        :param content: The raw file content.

        :returns: A list of :class:`_SmbSection` in declaration
                  order.
    """
    rtnval: list[_SmbSection] = []
    current = _SmbSection(name=None, header_line=None, body_lines=[])

    for raw in content.splitlines():
        stripped = raw.strip()
        is_header = (
            len(stripped) > 0
            and stripped.startswith("[")
            and stripped.endswith("]")
            and not stripped.startswith("#")
        )
        if is_header:
            if (
                current.name is not None
                or len(current.body_lines) > 0
            ):
                rtnval.append(current)
            section_name = stripped[1:-1].strip()
            current = _SmbSection(
                name=section_name,
                header_line=raw,
                body_lines=[],
            )
            continue
        current.body_lines.append(raw)

    if current.name is not None or len(current.body_lines) > 0:
        rtnval.append(current)

    return rtnval


def _render_smb_conf(*, sections: list[_SmbSection]) -> str:
    """
        Render an ordered :class:`_SmbSection` list back to a
        canonical INI body.

        :param sections: The sections to render.

        :returns: The rendered file content with a trailing newline.
    """
    parts: list[str] = []
    for section in sections:
        if section.name is None:
            for line in section.body_lines:
                parts.append(line)
            continue
        if section.header_line is not None:
            parts.append(section.header_line)
        else:
            parts.append("[{}]".format(section.name))
        for line in section.body_lines:
            parts.append(line)

    body = "\n".join(parts)
    if not body.endswith("\n"):
        body = body + "\n"
    return body


def _parse_directives(*, body_lines: list[str]) -> dict[str, str]:
    """
        Parse a section's body lines into an ordered dict of
        ``key → value`` directive strings.

        Comment and blank lines are skipped.  Keys are normalised
        to lower-case with embedded whitespace collapsed to single
        spaces (Samba treats ``read only``, ``Read Only``, and
        ``read  only`` as the same directive).  Values are stripped
        of leading / trailing whitespace.

        :param body_lines: The verbatim body lines of a parsed
                           section.

        :returns: A ``dict[str, str]`` of normalised directives.
                  Insertion order is preserved.
    """
    rtnval: dict[str, str] = {}
    for raw in body_lines:
        stripped = raw.strip()
        if len(stripped) == 0:
            continue
        if stripped.startswith("#") or stripped.startswith(";"):
            continue
        equal_index = stripped.find("=")
        if equal_index == -1:
            continue
        key_raw = stripped[:equal_index].strip()
        value_raw = stripped[equal_index + 1:].strip()
        key_normalised = " ".join(key_raw.lower().split())
        rtnval[key_normalised] = value_raw
    return rtnval


def _get_section(
    *,
    sections: list[_SmbSection],
    name: str,
) -> _SmbSection:
    """
        Return the section whose normalised name matches ``name``.
        Raises :class:`KeyError` when absent (use
        :func:`_get_section_or_none` for the optional variant).

        :param sections: The parsed sections.
        :param name: The case-insensitive section name.

        :returns: The matching :class:`_SmbSection`.

        :raises KeyError: When no section matches.
    """
    candidate = _get_section_or_none(sections=sections, name=name)
    if candidate is None:
        err_msg = "section [{}] not found".format(name)
        raise KeyError(err_msg)
    rtnval = candidate
    return rtnval


def _get_section_or_none(
    *,
    sections: list[_SmbSection],
    name: str,
) -> Optional[_SmbSection]:
    """
        Return the section whose name matches ``name`` (case-
        insensitive), or ``None`` when absent.

        :param sections: The parsed sections.
        :param name: The case-insensitive section name.

        :returns: The matching :class:`_SmbSection` or ``None``.
    """
    target_normalised = name.strip().lower()
    for section in sections:
        if section.name is None:
            continue
        if section.name.strip().lower() == target_normalised:
            rtnval: Optional[_SmbSection] = section
            return rtnval
    rtnval = None
    return rtnval


def _ensure_global_section(
    *,
    sections: list[_SmbSection],
) -> tuple[list[_SmbSection], bool]:
    """
        Return ``(sections, ensured)`` where ``sections`` is
        guaranteed to contain a ``[global]`` section and
        ``ensured`` is ``True`` when one had to be appended.

        :param sections: The parsed sections.

        :returns: The augmented section list and the ``ensured``
                  flag.
    """
    existing = _get_section_or_none(sections=sections, name="global")
    if existing is not None:
        rtnval = (sections, False)
        return rtnval
    appended = list(sections)
    appended.append(
        _SmbSection(
            name="global",
            header_line="[global]",
            body_lines=[],
        ),
    )
    rtnval = (appended, True)
    return rtnval


def _set_directive_in_section(
    *,
    sections: list[_SmbSection],
    section_name: str,
    key: str,
    value: str,
) -> list[_SmbSection]:
    """
        Set ``key = value`` inside the section ``section_name``.

        Replaces an existing matching line in place when present;
        otherwise appends a new line at the end of the section
        body.

        :param sections: The parsed section list.
        :param section_name: Name of the section to edit (case-
                             insensitive).
        :param key: Already-normalised directive key.
        :param value: Directive value.

        :returns: A new section list with the directive applied.
    """
    target_normalised = section_name.strip().lower()
    rtnval: list[_SmbSection] = []
    for section in sections:
        if (
            section.name is None
            or section.name.strip().lower() != target_normalised
        ):
            rtnval.append(section)
            continue

        new_body: list[str] = []
        replaced = False
        for raw in section.body_lines:
            stripped = raw.strip()
            if (
                len(stripped) == 0
                or stripped.startswith("#")
                or stripped.startswith(";")
            ):
                new_body.append(raw)
                continue
            equal_index = stripped.find("=")
            if equal_index == -1:
                new_body.append(raw)
                continue
            existing_key = " ".join(
                stripped[:equal_index].strip().lower().split(),
            )
            if existing_key == key and not replaced:
                new_body.append("    {} = {}".format(key, value))
                replaced = True
                continue
            new_body.append(raw)
        if not replaced:
            new_body.append("    {} = {}".format(key, value))

        replaced_section = _SmbSection(
            name=section.name,
            header_line=section.header_line,
            body_lines=new_body,
        )
        rtnval.append(replaced_section)
    return rtnval


def _remove_directive_from_section(
    *,
    sections: list[_SmbSection],
    section_name: str,
    key: str,
) -> list[_SmbSection]:
    """
        Strip the directive ``key`` from the section
        ``section_name``.

        :param sections: The parsed section list.
        :param section_name: Name of the section to edit.
        :param key: Already-normalised directive key.

        :returns: A new section list with the directive removed.
    """
    target_normalised = section_name.strip().lower()
    rtnval: list[_SmbSection] = []
    for section in sections:
        if (
            section.name is None
            or section.name.strip().lower() != target_normalised
        ):
            rtnval.append(section)
            continue

        new_body: list[str] = []
        for raw in section.body_lines:
            stripped = raw.strip()
            if (
                len(stripped) == 0
                or stripped.startswith("#")
                or stripped.startswith(";")
            ):
                new_body.append(raw)
                continue
            equal_index = stripped.find("=")
            if equal_index == -1:
                new_body.append(raw)
                continue
            existing_key = " ".join(
                stripped[:equal_index].strip().lower().split(),
            )
            if existing_key == key:
                continue
            new_body.append(raw)

        replaced_section = _SmbSection(
            name=section.name,
            header_line=section.header_line,
            body_lines=new_body,
        )
        rtnval.append(replaced_section)
    return rtnval


def _upsert_section(
    *,
    sections: list[_SmbSection],
    new_section: _SmbSection,
) -> list[_SmbSection]:
    """
        Return ``sections`` with ``new_section`` either replacing an
        existing section of the same name or appended at the end.

        :param sections: The original section list.
        :param new_section: The section to upsert.

        :returns: A new list with the upserted section.
    """
    target_name = new_section.name
    rtnval: list[_SmbSection] = []
    replaced = False
    for section in sections:
        if section.name == target_name and not replaced:
            rtnval.append(new_section)
            replaced = True
            continue
        if section.name == target_name and replaced:
            # Drop duplicate sections with the same name.
            continue
        rtnval.append(section)
    if not replaced:
        rtnval.append(new_section)
    return rtnval


def _remove_section_by_name(
    *,
    sections: list[_SmbSection],
    name: str,
) -> list[_SmbSection]:
    """
        Return ``sections`` with every section whose name equals
        ``name`` removed.

        :param sections: The original section list.
        :param name: The section name to drop.

        :returns: A new list with the section(s) removed.
    """
    rtnval: list[_SmbSection] = []
    for section in sections:
        if section.name == name:
            continue
        rtnval.append(section)
    return rtnval


# ----------------------------------------------------------------------
# Module-level helpers — share spec rendering / idempotency
# ----------------------------------------------------------------------


def _spec_to_directives(*, spec: ShareSpec) -> dict[str, str]:
    """
        Build the directive map implied by a :class:`ShareSpec`.

        Boolean fields are rendered as ``yes`` / ``no``; list-valued
        fields are rendered as space-separated principal lists.
        ``extra_directives`` are merged in last; their values
        override anything earlier in this map.

        :param spec: The share spec.

        :returns: A directive map (normalised keys).
    """
    rtnval: dict[str, str] = {}
    rtnval["path"] = spec.path
    if spec.comment is not None and len(spec.comment) > 0:
        rtnval["comment"] = spec.comment
    if spec.writable is not None:
        rtnval["writable"] = "yes" if spec.writable else "no"
    if spec.browseable is not None:
        rtnval["browseable"] = "yes" if spec.browseable else "no"
    if spec.guest_ok is not None:
        rtnval["guest ok"] = "yes" if spec.guest_ok else "no"
    if spec.valid_users is not None and len(spec.valid_users) > 0:
        rtnval["valid users"] = " ".join(spec.valid_users)
    if spec.read_list is not None and len(spec.read_list) > 0:
        rtnval["read list"] = " ".join(spec.read_list)
    if spec.write_list is not None and len(spec.write_list) > 0:
        rtnval["write list"] = " ".join(spec.write_list)
    if spec.create_mask is not None:
        rtnval["create mask"] = spec.create_mask
    if spec.directory_mask is not None:
        rtnval["directory mask"] = spec.directory_mask
    for raw_key, raw_value in spec.extra_directives.items():
        normalised_key = " ".join(raw_key.lower().split())
        rtnval[normalised_key] = raw_value
    return rtnval


def _build_share_section(
    *,
    name: str,
    directives: dict[str, str],
) -> _SmbSection:
    """
        Build a :class:`_SmbSection` for a share named ``name``
        whose body carries the ``directives`` map.

        :param name: The share name.
        :param directives: The directive map.

        :returns: The populated section.
    """
    body_lines: list[str] = []
    for key, value in directives.items():
        body_lines.append("    {} = {}".format(key, value))
    rtnval = _SmbSection(
        name=name,
        header_line="[{}]".format(name),
        body_lines=body_lines,
    )
    return rtnval


def _requested_directives_present(
    *,
    requested: dict[str, str],
    existing: dict[str, str],
) -> bool:
    """
        ``True`` when every ``key → value`` in ``requested`` is
        already present in ``existing``.

        Other directives may also be present in ``existing`` — see
        requirement 2.1 ("SKIPPED when the block already contains
        the requested directives, even if others are also present").

        :param requested: The desired directive set.
        :param existing: The current directive set.

        :returns: True when every desired directive is already at
                  the desired value.
    """
    for key, value in requested.items():
        if existing.get(key) != value:
            rtnval = False
            return rtnval
    rtnval = True
    return rtnval


# ----------------------------------------------------------------------
# Module-level helpers — testparm parsing
# ----------------------------------------------------------------------


def _extract_testparm_warnings(*, stderr: str) -> list[str]:
    """
        Extract warning-shaped lines from ``testparm`` stderr.

        :param stderr: Captured stderr.

        :returns: A list of warning lines.
    """
    rtnval: list[str] = []
    for raw in stderr.splitlines():
        stripped = raw.strip()
        if len(stripped) == 0:
            continue
        lowered = stripped.lower()
        if (
            lowered.startswith("warning")
            or lowered.startswith("bad value")
            or lowered.startswith("unknown parameter")
            or "warning:" in lowered
        ):
            rtnval.append(stripped)
    return rtnval


def _extract_testparm_sections(*, stdout: str) -> list[str]:
    """
        Extract every parsed section header from ``testparm``
        stdout.

        :param stdout: Captured stdout.

        :returns: The parsed section names in declaration order.
    """
    rtnval: list[str] = []
    for raw in stdout.splitlines():
        stripped = raw.strip()
        if len(stripped) == 0:
            continue
        if (
            stripped.startswith("[")
            and stripped.endswith("]")
            and not stripped.startswith("#")
        ):
            section_name = stripped[1:-1].strip()
            rtnval.append(section_name)
    return rtnval


# ----------------------------------------------------------------------
# Module-level helpers — pdbedit parsing
# ----------------------------------------------------------------------


def _parse_pdbedit_short(*, text: str) -> list[SambaUserInfo]:
    """
        Parse short ``pdbedit -L`` output (``user:uid:gecos`` per
        line) into a list of :class:`SambaUserInfo` records.

        Lines that do not contain a colon are still recorded with
        ``uid=None``.

        :param text: Captured stdout.

        :returns: A list of :class:`SambaUserInfo`.
    """
    rtnval: list[SambaUserInfo] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if len(stripped) == 0:
            continue
        parts = stripped.split(":")
        username = parts[0].strip()
        if len(username) == 0:
            continue

        uid_value: Optional[int] = None
        if len(parts) >= 2:
            uid_text = parts[1].strip()
            if len(uid_text) > 0:
                try:
                    uid_value = int(uid_text)
                except ValueError:
                    uid_value = None

        info = SambaUserInfo(
            username=username,
            uid=uid_value,
            flags="",
            last_password_change=None,
        )
        rtnval.append(info)
    return rtnval
