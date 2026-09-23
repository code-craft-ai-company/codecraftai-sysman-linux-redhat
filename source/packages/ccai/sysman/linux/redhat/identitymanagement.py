# ccai.sysman.linux.redhat.identitymanagement
#
# IdentityManager wraps the RHEL identity-client toolchain — `realm`,
# `adcli`, `sssctl`, `sss_cache`, `kinit`, `klist`, `kdestroy` — and
# the on-disk content of `/etc/sssd/sssd.conf`, `/etc/sssd/conf.d/`,
# `/etc/krb5.conf`, and `/etc/krb5.conf.d/` through the
# ICommandRunner abstraction.  SSSD service lifecycle is delegated
# to an injected ServiceManager (3-service-management) and NSS
# source-order edits are delegated to an injected
# AuthenticationManager (3-authentication-management) so a single
# well-defined code path owns nsswitch.conf.
#
# Credential bytes (passwords, OTPs, keytab passphrases) are routed
# through runner ``stdin`` only — never on argv.  The
# :class:`SanitisedDispatchMixin` guarantees the recorded
# ``command`` field on a result equals the caller-supplied argv,
# never the runner's annotated command.

import os

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ccai.errors.exceptions import SemanticError

from ccai.sysman.linux.redhat.featurebase import (
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
    SanitisedDispatchMixin,
)
from ccai.sysman.linux.redhat.runners.base import ICommandRunner


# ----------------------------------------------------------------------
# Module-level command constants
# ----------------------------------------------------------------------

_CAT = "cat"
_TEE = "tee"
_CHMOD = "chmod"
_CHOWN = "chown"
_LS = "ls"

_REALM = "realm"
_SSSCTL = "sssctl"
_SSS_CACHE = "sss_cache"
_KINIT = "kinit"
_KLIST = "klist"
_KDESTROY = "kdestroy"


# ----------------------------------------------------------------------
# Status enum
# ----------------------------------------------------------------------


class IdentityActionStatus(Enum):
    """
        Outcome status for identity-management actions.
    """
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------


@dataclass
class IdentityManagerConfig(FeatureConfig):
    """
        Configuration for :class:`IdentityManager`.

        Inherits ``dry_run`` and ``elevated`` from
        :class:`FeatureConfig`.

        :param sssd_conf_path: Absolute path to the primary SSSD
                               configuration file.
        :param sssd_dropin_dir: Absolute path to the SSSD drop-in
                                directory.
        :param krb5_conf_path: Absolute path to the primary Kerberos
                               client configuration file.
        :param krb5_dropin_dir: Absolute path to the Kerberos client
                                drop-in directory.
        :param default_keytab: Absolute path to the default host
                               keytab.
    """
    elevated: bool = True
    sssd_conf_path: str = "/etc/sssd/sssd.conf"
    sssd_dropin_dir: str = "/etc/sssd/conf.d"
    krb5_conf_path: str = "/etc/krb5.conf"
    krb5_dropin_dir: str = "/etc/krb5.conf.d"
    default_keytab: str = "/etc/krb5.keytab"


# ----------------------------------------------------------------------
# Result and data models
# ----------------------------------------------------------------------


@dataclass
class IdentityActionResult(FeatureActionResult):
    """
        Outcome of an identity-management action.

        :param realm: Realm acted upon, when relevant.
    """
    realm: Optional[str] = None


@dataclass
class RealmDiscovery:
    """
        Result of ``realm discover <domain>``.

        :param domain: Queried domain.
        :param realm_type: ``configured`` value reported by ``realm
                           discover`` (``kerberos``, ``kerberos-member``,
                           ``ipa``, ...).
        :param realm_name: Canonical realm name, when present in the
                           output.
        :param domain_controllers: List of discovered DC / server
                                   strings.
        :param raw: Verbatim discovery stdout.
    """
    domain: str
    realm_type: str
    realm_name: Optional[str]
    domain_controllers: list[str] = field(default_factory=list)
    raw: str = ""


@dataclass
class RealmMembership:
    """
        Single ``realm list`` stanza describing a configured realm.

        :param domain: Realm / domain stanza header.
        :param realm_name: Canonical realm name (``realm-name`` line),
                           when present.
        :param configured: ``True`` when the stanza reports a
                           non-empty ``configured`` value.
        :param server_software: ``server-software`` line, or ``None``.
        :param client_software: ``client-software`` line, or ``None``.
        :param required_package: ``required-package`` line, or
                                 ``None``.
    """
    domain: str
    realm_name: Optional[str]
    configured: bool
    server_software: Optional[str]
    client_software: Optional[str]
    required_package: Optional[str]


@dataclass
class SssdConfigCheckResult:
    """
        Result of ``sssctl config-check``.

        :param ok: True when ``sssctl config-check`` exited 0.
        :param exit_code: The runner's exit code.
        :param stderr: Captured stderr.
        :param warnings: Parsed warning lines from stdout.
    """
    ok: bool
    exit_code: int
    stderr: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class SssdStatus:
    """
        Result of ``sssctl domain-status <domain>``.

        :param domain: The domain queried.
        :param online: True when the parsed status reports the
                       domain is online.
        :param last_request: Verbatim ``Last request:`` value, or
                             ``None``.
        :param raw: Verbatim stdout.
    """
    domain: str
    online: bool
    last_request: Optional[str]
    raw: str


@dataclass
class KrbClientConfig:
    """
        Parsed Kerberos client configuration.

        :param default_realm: The ``[libdefaults].default_realm``
                              value, or ``None`` when absent.
        :param realms: Mapping of realm → ordered list of KDC host
                       strings.
        :param domain_realm: Mapping of DNS suffix → realm.
    """
    default_realm: Optional[str]
    realms: dict = field(default_factory=dict)
    domain_realm: dict = field(default_factory=dict)


@dataclass
class KrbTicket:
    """
        Single ``klist`` ticket entry.

        :param principal: Owning principal (``Default principal``).
        :param service: Service principal, e.g.
                        ``krbtgt/EXAMPLE.COM@EXAMPLE.COM``.
        :param starts: ``Valid starting`` field, ISO-ish string.
        :param expires: ``Expires`` field.
        :param renewable_until: ``renew until`` value, or ``None``.
    """
    principal: str
    service: str
    starts: str
    expires: str
    renewable_until: Optional[str]


# ----------------------------------------------------------------------
# Exceptions
# ----------------------------------------------------------------------


class IdentityToolNotAvailableError(SemanticError):
    """
        Raised when a required identity-management tool (e.g.
        ``realm``) is not installed on the target host.  Bubbles
        unconditionally — the manager never catches it.
    """
    pass


class IdentityCredentialError(SemanticError):
    """
        Raised when an identity-management verb requires a credential
        and none was supplied.  Bubbles unconditionally.
    """
    pass


# ----------------------------------------------------------------------
# Manager
# ----------------------------------------------------------------------


class IdentityManager(SanitisedDispatchMixin, BaseFeatureManager):
    """
        Facade for identity-management on RHEL.

        Wraps ``realm``, ``sssctl``, ``sss_cache``, ``kinit``,
        ``klist``, and ``kdestroy`` plus the on-disk content of
        ``/etc/sssd/`` and ``/etc/krb5.conf{.d}`` through the
        injected :class:`ICommandRunner`.  Mixes
        :class:`SanitisedDispatchMixin` in so that any dispatch
        carrying password / OTP bytes on ``stdin`` records the bare
        argv on the returned :class:`IdentityActionResult` rather
        than the runner's annotated command.

        SSSD service lifecycle is delegated to the injected
        :class:`ServiceManager` (``3-service-management``) and
        ``/etc/nsswitch.conf`` source-order edits are delegated to
        the injected :class:`AuthenticationManager`
        (``3-authentication-management``).
    """

    result_class = IdentityActionResult
    status_class = IdentityActionStatus
    feature_name = "identity-management"

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[IdentityManagerConfig] = None,
        *,
        service_manager: Optional[object] = None,
        authentication_manager: Optional[object] = None,
        report=None,
    ) -> None:
        """
            :param runner: The command runner used by every operation.
            :param config: Manager configuration.  Defaults to a fresh
                           :class:`IdentityManagerConfig` when omitted.
            :param service_manager: Optional sibling
                                    :class:`ServiceManager` used by
                                    :meth:`restart_sssd`.
            :param authentication_manager: Optional sibling
                                           :class:`AuthenticationManager`
                                           used by
                                           :meth:`ensure_nss_ordering`.
            :param report: Optional :class:`MaintenanceReport`
                           forwarded to the base manager.
        """
        if config is None:
            config = IdentityManagerConfig()

        super().__init__(runner=runner, config=config, report=report)

        self._service_manager = service_manager
        self._authentication_manager = authentication_manager
        return

    @property
    def service_manager(self) -> Optional[object]:
        """
            The injected :class:`ServiceManager`, or ``None``.
        """
        return self._service_manager

    @property
    def authentication_manager(self) -> Optional[object]:
        """
            The injected :class:`AuthenticationManager`, or ``None``.
        """
        return self._authentication_manager

    # ------------------------------------------------------------------
    # realm / adcli
    # ------------------------------------------------------------------

    def discover(self, domain: str) -> RealmDiscovery:
        """
            Run ``realm discover <domain>`` and parse the result.

            :param domain: The DNS domain to discover.

            :returns: The :class:`RealmDiscovery`.
            :raises IdentityToolNotAvailableError: When the ``realm``
                                                   tool is not
                                                   installed.
        """
        command = [_REALM, "discover", domain]
        run_result = self._runner.run(list(command), elevated=False)

        is_missing = _looks_like_command_not_found(
            exit_code=run_result.exit_code,
            stderr=run_result.stderr,
        )
        if is_missing:
            err_msg_lines = ["The 'realm' tool is not installed."]
            err_msg_lines.append("    HINT: install the 'realmd' RPM.")
            err_msg_lines.append("    EXIT_CODE: {}".format(run_result.exit_code))
            err_msg_lines.append("    STDERR: {}".format(run_result.stderr))
            err_msg = os.linesep.join(err_msg_lines)
            raise IdentityToolNotAvailableError(err_msg)

        rtnval = _parse_realm_discover_output(
            domain=domain, output=run_result.stdout,
        )
        return rtnval

    def list_realms(self) -> list[RealmMembership]:
        """
            Run ``realm list`` and return one :class:`RealmMembership`
            per parsed stanza.  Returns an empty list when no realms
            are configured.

            :returns: A list of :class:`RealmMembership`.
        """
        command = [_REALM, "list"]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: list[RealmMembership] = []
            return rtnval

        rtnval = _parse_realm_list_output(output=run_result.stdout)
        return rtnval

    def join(
        self,
        domain: str,
        credential: Optional[object] = None,
        *,
        one_time_password: Optional[str] = None,
        user: Optional[str] = None,
    ) -> IdentityActionResult:
        """
            Join the host to ``domain`` via ``realm join``.

            Idempotent: when ``realm list`` already reports a
            configured stanza for ``domain``, returns ``SKIPPED``
            without dispatching.  Dry-run short-circuits before the
            membership probe.

            Credential bytes ride on runner ``stdin`` — argv carries
            only the realm and a username flag.

            :param domain: The realm / domain to join.
            :param credential: A credential-shaped object exposing
                               ``username`` (or ``user``) and a
                               ``password`` / ``secret_bytes()``
                               accessor.
            :param one_time_password: Pre-shared one-time password.
                                      When supplied, used in place of
                                      a credential.  The password is
                                      delivered via stdin — argv
                                      carries only the
                                      ``--one-time-password`` flag, not
                                      its value.
            :param user: Override admin username when ``credential``
                         does not carry one.

            :returns: The :class:`IdentityActionResult`.

            :raises IdentityCredentialError: When neither ``credential``
                                             nor ``one_time_password``
                                             is supplied.
        """
        admin_user = _resolve_join_user(credential=credential, user=user)
        stdin_payload = _resolve_join_stdin(
            credential=credential,
            one_time_password=one_time_password,
        )

        sanitised_command = _build_join_argv(
            domain=domain,
            admin_user=admin_user,
            using_otp=one_time_password is not None,
        )

        if self._config.dry_run:
            dry = IdentityActionResult(
                status=IdentityActionStatus.DRY_RUN,
                detail=domain,
                command=list(sanitised_command),
                realm=domain,
            )
            return dry

        already_joined = self._is_joined(domain=domain)
        if already_joined:
            skipped = IdentityActionResult(
                status=IdentityActionStatus.SKIPPED,
                detail=domain,
                command=list(sanitised_command),
                realm=domain,
            )
            return skipped

        rtnval = self._dispatch(
            command=sanitised_command,
            detail=domain,
            stdin=stdin_payload,
            operation="join",
            realm=domain,
        )
        return rtnval

    def leave(
        self,
        domain: Optional[str] = None,
    ) -> IdentityActionResult:
        """
            Leave ``domain`` via ``realm leave``.

            Idempotent: when ``domain`` is supplied and the host is
            not joined to it, returns ``SKIPPED``.  When ``domain`` is
            ``None`` and ``realm list`` reports zero configured
            realms, returns ``SKIPPED``.

            :param domain: Realm to leave.  When ``None``, runs
                           ``realm leave`` with no positional argument
                           and realmd selects the host's primary realm.

            :returns: The :class:`IdentityActionResult`.
        """
        command: list[str] = [_REALM, "leave"]
        if domain is not None:
            command.append(domain)

        if self._config.dry_run:
            dry = IdentityActionResult(
                status=IdentityActionStatus.DRY_RUN,
                detail=domain,
                command=list(command),
                realm=domain,
            )
            return dry

        memberships = self.list_realms()
        if domain is not None:
            joined = self._membership_for(
                memberships=memberships, domain=domain,
            )
            if joined is None:
                skipped = IdentityActionResult(
                    status=IdentityActionStatus.SKIPPED,
                    detail=domain,
                    command=list(command),
                    realm=domain,
                )
                return skipped
        else:
            if len(memberships) == 0:
                skipped = IdentityActionResult(
                    status=IdentityActionStatus.SKIPPED,
                    detail=None,
                    command=list(command),
                    realm=None,
                )
                return skipped

        rtnval = self._dispatch(
            command=command,
            detail=domain,
            operation="leave",
            realm=domain,
        )
        return rtnval

    # ------------------------------------------------------------------
    # SSSD configuration
    # ------------------------------------------------------------------

    def read_sssd_conf(self, path: Optional[str] = None) -> dict:
        """
            Read and parse an SSSD configuration file.

            :param path: Optional override for the SSSD config path.
                         Defaults to ``config.sssd_conf_path``.

            :returns: A ``dict[str, dict[str, str]]`` keyed by section
                      name; empty dict when the file does not exist.
        """
        target_path = path if path is not None else self._typed_config().sssd_conf_path
        text = self._read_file(path=target_path)
        rtnval = _parse_ini_text(text=text)
        return rtnval

    def write_sssd_dropin(
        self,
        name: str,
        sections: dict,
    ) -> IdentityActionResult:
        """
            Write a drop-in to ``<sssd_dropin_dir>/<name>.conf`` with
            mode ``0600`` and owner ``root:root``.

            :param name: File-stem (no ``.conf`` extension).
            :param sections: ``dict[str, dict[str, str]]`` describing
                             the desired file body.

            :returns: The :class:`IdentityActionResult`.
        """
        target_path = self._dropin_path(
            base_dir=self._typed_config().sssd_dropin_dir, name=name,
        )
        body_text = _render_ini_text(sections=sections)
        rtnval = self._write_file(
            path=target_path,
            content=body_text,
            mode="0600",
            owner="root:root",
            operation="write_sssd_dropin",
        )
        return rtnval

    def set_sssd_directive(
        self,
        section: str,
        key: str,
        value: str,
        *,
        path: Optional[str] = None,
    ) -> IdentityActionResult:
        """
            Upsert ``[section] key = value`` in an SSSD-style INI
            file.  Returns ``SKIPPED`` when the directive already
            holds the requested value.

            :param section: INI section name.
            :param key: Directive key.
            :param value: Desired value.
            :param path: Optional override for the file path.
                         Defaults to ``config.sssd_conf_path``.

            :returns: The :class:`IdentityActionResult`.
        """
        target_path = path if path is not None else self._typed_config().sssd_conf_path
        existing_text = self._read_file(path=target_path)
        parsed = _parse_ini_text(text=existing_text)

        existing_section = parsed.get(section, {})
        if existing_section.get(key) == value:
            skipped = IdentityActionResult(
                status=IdentityActionStatus.SKIPPED,
                detail="{}:{}={}".format(section, key, value),
                command=[_TEE, target_path],
                realm=None,
            )
            return skipped

        new_text = _upsert_ini_directive(
            text=existing_text, section=section, key=key, value=value,
        )
        rtnval = self._write_file(
            path=target_path,
            content=new_text,
            mode="0600",
            owner="root:root",
            operation="set_sssd_directive",
            detail="{}:{}={}".format(section, key, value),
        )
        return rtnval

    def remove_sssd_directive(
        self,
        section: str,
        key: str,
        *,
        path: Optional[str] = None,
    ) -> IdentityActionResult:
        """
            Strip ``[section] key`` from an SSSD-style INI file.

            :param section: INI section name.
            :param key: Directive key.
            :param path: Optional override for the file path.

            :returns: The :class:`IdentityActionResult`.  ``SKIPPED``
                      when the directive is already absent.
        """
        target_path = path if path is not None else self._typed_config().sssd_conf_path
        existing_text = self._read_file(path=target_path)
        parsed = _parse_ini_text(text=existing_text)

        if section not in parsed:
            skipped = IdentityActionResult(
                status=IdentityActionStatus.SKIPPED,
                detail="{}:{}".format(section, key),
                command=[_TEE, target_path],
                realm=None,
            )
            return skipped
        if key not in parsed[section]:
            skipped = IdentityActionResult(
                status=IdentityActionStatus.SKIPPED,
                detail="{}:{}".format(section, key),
                command=[_TEE, target_path],
                realm=None,
            )
            return skipped

        new_text = _remove_ini_directive(
            text=existing_text, section=section, key=key,
        )
        rtnval = self._write_file(
            path=target_path,
            content=new_text,
            mode="0600",
            owner="root:root",
            operation="remove_sssd_directive",
            detail="{}:{}".format(section, key),
        )
        return rtnval

    def config_check(self) -> SssdConfigCheckResult:
        """
            Validate the SSSD configuration via ``sssctl
            config-check``.

            :returns: The :class:`SssdConfigCheckResult`.
        """
        if self._config.dry_run:
            rtnval = SssdConfigCheckResult(
                ok=True, exit_code=0, stderr="", warnings=[],
            )
            return rtnval

        command = [_SSSCTL, "config-check"]
        run_result = self._runner.run(
            list(command), elevated=self._config.elevated,
        )
        warnings = _parse_sssctl_warnings(text=run_result.stdout)
        rtnval = SssdConfigCheckResult(
            ok=run_result.exit_code == 0,
            exit_code=run_result.exit_code,
            stderr=run_result.stderr,
            warnings=warnings,
        )
        return rtnval

    # ------------------------------------------------------------------
    # SSSD runtime
    # ------------------------------------------------------------------

    def restart_sssd(self) -> IdentityActionResult:
        """
            Restart the ``sssd`` service.

            Delegates to the injected :class:`ServiceManager` when
            present; otherwise dispatches ``systemctl restart sssd``
            directly through the runner.

            :returns: The :class:`IdentityActionResult`.
        """
        if self._config.dry_run:
            dry = IdentityActionResult(
                status=IdentityActionStatus.DRY_RUN,
                detail="sssd",
                command=["systemctl", "restart", "sssd"],
                realm=None,
            )
            return dry

        if self._service_manager is not None:
            svc_result = self._service_manager.restart("sssd")
            mapped_status = _map_status_value(value=svc_result.status)
            rtnval = IdentityActionResult(
                status=mapped_status,
                detail="sssd",
                exit_code=svc_result.exit_code,
                stdout=svc_result.stdout,
                stderr=svc_result.stderr,
                command=list(svc_result.command) if svc_result.command is not None else None,
                realm=None,
            )
            return rtnval

        command = ["systemctl", "restart", "sssd"]
        rtnval = self._dispatch(
            command=command,
            detail="sssd",
            operation="restart_sssd",
            realm=None,
        )
        return rtnval

    def invalidate_cache(
        self,
        *,
        user: Optional[str] = None,
        group: Optional[str] = None,
    ) -> IdentityActionResult:
        """
            Invalidate the SSSD cache via ``sss_cache``.

            :param user: When set, expire only ``<user>``'s entry
                         (``-u <user>``).
            :param group: When set, expire only ``<group>``'s entry
                          (``-g <group>``).  Mutually exclusive with
                          ``user``; when both are ``None`` the entire
                          cache is expired (``-E``).

            :returns: The :class:`IdentityActionResult`.
        """
        command: list[str] = [_SSS_CACHE]
        detail_value: Optional[str]
        if user is not None:
            command.append("-u")
            command.append(user)
            detail_value = "user:{}".format(user)
        elif group is not None:
            command.append("-g")
            command.append(group)
            detail_value = "group:{}".format(group)
        else:
            command.append("-E")
            detail_value = "all"

        rtnval = self._dispatch(
            command=command,
            detail=detail_value,
            operation="invalidate_cache",
            realm=None,
        )
        return rtnval

    def domain_status(self, domain: str) -> SssdStatus:
        """
            Query the status of the SSSD domain via ``sssctl
            domain-status <domain>``.

            :param domain: The domain to query.

            :returns: The :class:`SssdStatus`.
        """
        command = [_SSSCTL, "domain-status", domain]
        run_result = self._runner.run(
            list(command), elevated=False,
        )
        rtnval = _parse_sssd_domain_status(
            domain=domain, text=run_result.stdout,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Kerberos client configuration
    # ------------------------------------------------------------------

    def read_krb5_conf(self) -> KrbClientConfig:
        """
            Read and merge ``/etc/krb5.conf`` plus every drop-in
            under ``/etc/krb5.conf.d/`` into a single
            :class:`KrbClientConfig`.

            Drop-ins are merged in lexicographic order so a later
            file wins on a key collision — matching MIT Kerberos
            ``includedir`` semantics.

            :returns: The :class:`KrbClientConfig`.
        """
        cfg = self._typed_config()
        primary_text = self._read_file(path=cfg.krb5_conf_path)
        merged = _parse_ini_text(text=primary_text)

        # Per-file source texts are retained so the multi-line
        # ``[realms] <REALM> = { kdc = ... }`` blocks (which the
        # flat INI parser cannot fully resolve) can be re-parsed
        # by :func:`_krb_realm_kdcs` per file.
        per_file_texts: list[str] = [primary_text]

        dropin_paths = self._list_dropin_files(directory=cfg.krb5_dropin_dir)
        for dropin_path in dropin_paths:
            dropin_text = self._read_file(path=dropin_path)
            per_file_texts.append(dropin_text)
            extra = _parse_ini_text(text=dropin_text)
            for sect_name, sect_body in extra.items():
                if sect_name not in merged:
                    merged[sect_name] = {}
                for k, v in sect_body.items():
                    merged[sect_name][k] = v

        rtnval = _build_krb_client_config(
            merged=merged, source_texts=per_file_texts,
        )
        return rtnval

    def set_default_realm(self, realm: str) -> IdentityActionResult:
        """
            Rewrite the ``[libdefaults].default_realm`` directive in
            ``/etc/krb5.conf``.  Every other directive, comment, and
            blank line is preserved verbatim.

            :param realm: Realm name to set.

            :returns: The :class:`IdentityActionResult`.  ``SKIPPED``
                      when the file already holds the requested
                      value.
        """
        rtnval = self.set_sssd_directive(
            section="libdefaults",
            key="default_realm",
            value=realm,
            path=self._typed_config().krb5_conf_path,
        )
        return rtnval

    def add_kdc(self, realm: str, host: str) -> IdentityActionResult:
        """
            Add ``kdc = <host>`` to the ``[realms]`` block for
            ``<realm>`` in ``/etc/krb5.conf``.  ``SKIPPED`` when the
            host is already listed.

            :param realm: Realm name.
            :param host: KDC host string.

            :returns: The :class:`IdentityActionResult`.
        """
        cfg = self._typed_config()
        existing_text = self._read_file(path=cfg.krb5_conf_path)
        existing_kdcs = _krb_realm_kdcs(text=existing_text, realm=realm)

        if host in existing_kdcs:
            skipped = IdentityActionResult(
                status=IdentityActionStatus.SKIPPED,
                detail="{}:kdc={}".format(realm, host),
                command=[_TEE, cfg.krb5_conf_path],
                realm=realm,
            )
            return skipped

        new_text = _add_krb_kdc(
            text=existing_text, realm=realm, host=host,
        )
        rtnval = self._write_file(
            path=cfg.krb5_conf_path,
            content=new_text,
            mode="0644",
            owner="root:root",
            operation="add_kdc",
            detail="{}:kdc={}".format(realm, host),
            realm=realm,
        )
        return rtnval

    def remove_kdc(self, realm: str, host: str) -> IdentityActionResult:
        """
            Remove ``kdc = <host>`` from the ``[realms]`` block for
            ``<realm>`` in ``/etc/krb5.conf``.  ``SKIPPED`` when the
            host is not listed.

            :param realm: Realm name.
            :param host: KDC host string.

            :returns: The :class:`IdentityActionResult`.
        """
        cfg = self._typed_config()
        existing_text = self._read_file(path=cfg.krb5_conf_path)
        existing_kdcs = _krb_realm_kdcs(text=existing_text, realm=realm)

        if host not in existing_kdcs:
            skipped = IdentityActionResult(
                status=IdentityActionStatus.SKIPPED,
                detail="{}:kdc={}".format(realm, host),
                command=[_TEE, cfg.krb5_conf_path],
                realm=realm,
            )
            return skipped

        new_text = _remove_krb_kdc(
            text=existing_text, realm=realm, host=host,
        )
        rtnval = self._write_file(
            path=cfg.krb5_conf_path,
            content=new_text,
            mode="0644",
            owner="root:root",
            operation="remove_kdc",
            detail="{}:kdc={}".format(realm, host),
            realm=realm,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Kerberos tickets
    # ------------------------------------------------------------------

    def kinit(
        self,
        principal: str,
        credential: Optional[object] = None,
        *,
        keytab: Optional[str] = None,
    ) -> IdentityActionResult:
        """
            Acquire a Kerberos ticket for ``principal``.

            When ``keytab`` is supplied, runs ``kinit -kt <keytab>
            <principal>`` with no stdin payload.  Otherwise the
            credential's password rides on runner ``stdin``.

            :param principal: The principal to acquire a ticket for.
            :param credential: A credential-shaped object exposing a
                               ``password`` / ``secret_bytes()``
                               accessor.
            :param keytab: Optional path to a keytab.  When set, the
                           credential is ignored.

            :returns: The :class:`IdentityActionResult`.

            :raises IdentityCredentialError: When neither
                                             ``credential`` nor
                                             ``keytab`` is supplied.
        """
        if keytab is not None:
            command = [_KINIT, "-kt", keytab, principal]
            stdin_payload: Optional[str] = None
        else:
            if credential is None:
                err_msg_lines = ["kinit requires a credential or a keytab."]
                err_msg_lines.append("    PRINCIPAL: {}".format(principal))
                err_msg = os.linesep.join(err_msg_lines)
                raise IdentityCredentialError(err_msg)

            secret_bytes = _resolve_credential_bytes(credential=credential)
            stdin_payload = secret_bytes.decode("utf-8")
            command = [_KINIT, principal]

        if self._config.dry_run:
            dry = IdentityActionResult(
                status=IdentityActionStatus.DRY_RUN,
                detail=principal,
                command=list(command),
                realm=None,
            )
            return dry

        rtnval = self._dispatch(
            command=command,
            detail=principal,
            stdin=stdin_payload,
            operation="kinit",
            realm=None,
        )
        return rtnval

    def klist(self) -> list[KrbTicket]:
        """
            Return every ticket parsed from ``klist``.

            Returns an empty list when the credential cache does not
            exist.

            :returns: A list of :class:`KrbTicket` records.
        """
        command = [_KLIST]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: list[KrbTicket] = []
            return rtnval

        rtnval = _parse_klist_output(text=run_result.stdout)
        return rtnval

    def kdestroy(self, *, all_caches: bool = False) -> IdentityActionResult:
        """
            Destroy the active Kerberos credential cache via
            ``kdestroy [-A]``.

            :param all_caches: When True, append ``-A`` to destroy
                               every cache in the cache collection.

            :returns: The :class:`IdentityActionResult`.
        """
        command: list[str] = [_KDESTROY]
        if all_caches:
            command.append("-A")

        rtnval = self._dispatch(
            command=command,
            detail=None,
            operation="kdestroy",
            realm=None,
        )
        return rtnval

    # ------------------------------------------------------------------
    # NSS ordering seam
    # ------------------------------------------------------------------

    def ensure_nss_ordering(
        self,
        databases: dict,
    ) -> IdentityActionResult:
        """
            Apply an NSS database → source-order map by delegating to
            the injected :class:`AuthenticationManager`.  Each
            database is dispatched separately; the aggregate
            :class:`IdentityActionResult` collapses the per-database
            outcomes.

            :param databases: ``dict[str, list[str]]`` keyed by NSS
                              database name (``passwd``, ``group``,
                              ``shadow``, ...).

            :returns: The aggregate :class:`IdentityActionResult`:
                      ``SUCCESS`` when every per-database call
                      succeeded or skipped; ``FAILED`` when any
                      delegated call failed; ``DRY_RUN`` when every
                      call was dry-run; ``SKIPPED`` when every call
                      was skipped.
        """
        if self._authentication_manager is None:
            err_msg_lines = ["ensure_nss_ordering requires an injected AuthenticationManager."]
            err_msg = os.linesep.join(err_msg_lines)
            raise IdentityCredentialError(err_msg)

        statuses: list[IdentityActionStatus] = []
        last_result = None
        detail_parts: list[str] = []

        for db_name, sources in databases.items():
            sub = self._authentication_manager.set_nss_sources(
                db_name, list(sources),
            )
            mapped_status = _map_status_value(value=sub.status)
            statuses.append(mapped_status)
            last_result = sub
            detail_parts.append(
                "{}={}".format(db_name, " ".join(list(sources))),
            )

        aggregate_status = _aggregate_statuses(statuses=statuses)
        if last_result is not None:
            exit_code_value = last_result.exit_code
            stdout_value = last_result.stdout
            stderr_value = last_result.stderr
            command_value = list(last_result.command) if last_result.command is not None else None
        else:
            exit_code_value = None
            stdout_value = None
            stderr_value = None
            command_value = None

        rtnval = IdentityActionResult(
            status=aggregate_status,
            detail="; ".join(detail_parts) if len(detail_parts) > 0 else None,
            exit_code=exit_code_value,
            stdout=stdout_value,
            stderr=stderr_value,
            command=command_value,
            realm=None,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _typed_config(self) -> IdentityManagerConfig:
        """
            Return ``self._config`` typed as
            :class:`IdentityManagerConfig`, falling back to a
            synthesised default when a plain :class:`FeatureConfig`
            was injected.
        """
        if isinstance(self._config, IdentityManagerConfig):
            rtnval = self._config
        else:
            rtnval = IdentityManagerConfig(
                dry_run=self._config.dry_run,
                elevated=self._config.elevated,
            )
        return rtnval

    def _is_joined(self, *, domain: str) -> bool:
        """
            Probe ``realm list`` and report whether the host is
            joined to ``domain``.  Query failures collapse to False
            so the caller's ``join`` attempt proceeds.
        """
        memberships = self.list_realms()
        match = self._membership_for(memberships=memberships, domain=domain)
        rtnval = match is not None
        return rtnval

    def _membership_for(
        self,
        *,
        memberships: list,
        domain: str,
    ) -> Optional[RealmMembership]:
        """
            Find a :class:`RealmMembership` matching ``domain`` (case
            insensitive on the domain string).
        """
        rtnval: Optional[RealmMembership] = None
        for entry in memberships:
            if entry.domain == domain or entry.domain.lower() == domain.lower():
                rtnval = entry
                return rtnval
            if entry.realm_name is not None:
                if entry.realm_name == domain or entry.realm_name.lower() == domain.lower():
                    rtnval = entry
                    return rtnval
        return rtnval

    def _read_file(self, *, path: str) -> str:
        """
            Read ``path`` through the runner via ``cat``.  Returns
            the empty string when the file does not exist or the read
            otherwise fails — the caller is expected to treat empty
            content as "absent".
        """
        run_result = self._runner.run([_CAT, path], elevated=False)
        if run_result.exit_code != 0:
            rtnval = ""
            return rtnval
        rtnval = run_result.stdout
        return rtnval

    def _list_dropin_files(self, *, directory: str) -> list[str]:
        """
            Enumerate regular files in ``directory`` via ``ls``,
            sorted lexicographically.  Returns an empty list when the
            directory does not exist.
        """
        run_result = self._runner.run([_LS, "-1", directory], elevated=False)
        if run_result.exit_code != 0:
            rtnval: list[str] = []
            return rtnval

        rtnval = []
        for raw_line in run_result.stdout.splitlines():
            cleaned = raw_line.strip()
            if len(cleaned) == 0:
                continue
            full_path = "{}/{}".format(directory.rstrip("/"), cleaned)
            rtnval.append(full_path)
        rtnval.sort()
        return rtnval

    def _dropin_path(self, *, base_dir: str, name: str) -> str:
        """
            Compose the absolute path of a drop-in file from
            ``base_dir`` and ``name``.  Adds the ``.conf`` extension
            when ``name`` does not already include one.
        """
        cleaned_name = name
        if not cleaned_name.endswith(".conf"):
            cleaned_name = "{}.conf".format(cleaned_name)
        rtnval = "{}/{}".format(base_dir.rstrip("/"), cleaned_name)
        return rtnval

    def _write_file(
        self,
        *,
        path: str,
        content: str,
        mode: str,
        owner: str,
        operation: str,
        detail: Optional[str] = None,
        realm: Optional[str] = None,
    ) -> IdentityActionResult:
        """
            Write ``content`` to ``path`` via ``tee`` and then chmod
            / chown to enforce ``mode`` / ``owner``.  Honours
            ``dry_run``: every dispatch short-circuits without
            contacting the runner.

            :param path: Absolute file path.
            :param content: File body to write.
            :param mode: chmod mode string (e.g. ``"0600"``).
            :param owner: chown owner string (e.g. ``"root:root"``).
            :param operation: Operation name for reporting.
            :param detail: Optional human-readable detail.
            :param realm: Optional realm acted upon.

            :returns: The :class:`IdentityActionResult` from the
                      ``tee`` dispatch.  chmod / chown happen only on
                      successful write outside dry-run.
        """
        actual_detail = detail if detail is not None else path
        write_result = self._dispatch(
            command=[_TEE, path],
            detail=actual_detail,
            stdin=content,
            operation=operation,
            realm=realm,
        )
        if write_result.status != IdentityActionStatus.SUCCESS:
            return write_result

        chmod_result = self._dispatch(
            command=[_CHMOD, mode, path],
            detail=actual_detail,
            operation="{}:chmod".format(operation),
            realm=realm,
        )
        if chmod_result.status != IdentityActionStatus.SUCCESS:
            return chmod_result

        chown_result = self._dispatch(
            command=[_CHOWN, owner, path],
            detail=actual_detail,
            operation="{}:chown".format(operation),
            realm=realm,
        )
        if chown_result.status != IdentityActionStatus.SUCCESS:
            return chown_result

        return write_result


# ----------------------------------------------------------------------
# Module-level helpers — credential / argv resolution
# ----------------------------------------------------------------------


def _resolve_join_user(
    *,
    credential: Optional[object],
    user: Optional[str],
) -> Optional[str]:
    """
        Resolve the admin username for a ``realm join`` command.

        Order: explicit ``user`` parameter, then a credential's
        ``username`` / ``user`` / ``principal`` attribute.  When
        neither is supplied the resolved user is ``None`` — the
        caller decides whether that is an error.
    """
    if user is not None and len(user) > 0:
        rtnval: Optional[str] = user
        return rtnval
    if credential is not None:
        for attr_name in ("username", "user", "principal"):
            if hasattr(credential, attr_name):
                value = getattr(credential, attr_name)
                if isinstance(value, str) and len(value) > 0:
                    rtnval = value
                    return rtnval
    rtnval = None
    return rtnval


def _resolve_join_stdin(
    *,
    credential: Optional[object],
    one_time_password: Optional[str],
) -> str:
    """
        Resolve the ``stdin`` payload for a ``realm join``.  An
        explicit OTP wins over a credential.

        :raises IdentityCredentialError: When neither input carries a
                                         secret payload.
    """
    if one_time_password is not None:
        rtnval = one_time_password
        return rtnval

    if credential is not None:
        secret_bytes = _resolve_credential_bytes(credential=credential)
        rtnval = secret_bytes.decode("utf-8")
        return rtnval

    err_msg_lines = ["join requires a credential or a one-time password."]
    err_msg = os.linesep.join(err_msg_lines)
    raise IdentityCredentialError(err_msg)


def _build_join_argv(
    *,
    domain: str,
    admin_user: Optional[str],
    using_otp: bool,
) -> list[str]:
    """
        Build the ``realm join`` argv.  The OTP / password value is
        NOT placed on argv — it rides on stdin.  When an OTP is in
        use, the bare ``--one-time-password`` flag is added (no
        value).

        :param domain: Realm / domain to join.
        :param admin_user: Admin user, or ``None``.
        :param using_otp: True when an OTP rides on stdin.

        :returns: The argv as a new list.
    """
    rtnval: list[str] = [_REALM, "join", domain]
    if using_otp:
        rtnval.append("--one-time-password")
    if admin_user is not None and len(admin_user) > 0:
        rtnval.append("-U")
        rtnval.append(admin_user)
    return rtnval


def _resolve_credential_bytes(*, credential: object) -> bytes:
    """
        Extract the secret bytes from a credential-shaped object.

        Accepts (in order): an object exposing ``secret_bytes()``
        returning ``bytes`` / ``str``; an object exposing
        ``password``; a bare ``bytes`` value; a bare ``str`` value.

        :raises IdentityCredentialError: When no secret bytes can be
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
    raise IdentityCredentialError(err_msg)


# ----------------------------------------------------------------------
# Module-level helpers — status mapping
# ----------------------------------------------------------------------


def _map_status_value(*, value: object) -> IdentityActionStatus:
    """
        Map a sibling manager's status enum value onto an
        :class:`IdentityActionStatus`.

        Falls back to :attr:`IdentityActionStatus.FAILED` when the
        value cannot be mapped.
    """
    candidate: Optional[str] = None
    if hasattr(value, "value"):
        raw = value.value
        if isinstance(raw, str):
            candidate = raw
    if isinstance(value, str):
        candidate = value

    if candidate is None:
        rtnval = IdentityActionStatus.FAILED
        return rtnval

    for member in IdentityActionStatus:
        if member.value == candidate:
            rtnval = member
            return rtnval

    rtnval = IdentityActionStatus.FAILED
    return rtnval


def _aggregate_statuses(
    *,
    statuses: list,
) -> IdentityActionStatus:
    """
        Aggregate a list of :class:`IdentityActionStatus` values
        into a single overall status.

        Rule:
        - any FAILED → FAILED
        - all DRY_RUN → DRY_RUN
        - all SKIPPED → SKIPPED
        - else SUCCESS
    """
    if len(statuses) == 0:
        rtnval = IdentityActionStatus.SUCCESS
        return rtnval

    has_failed = False
    all_dry = True
    all_skipped = True
    for st in statuses:
        if st == IdentityActionStatus.FAILED:
            has_failed = True
        if st != IdentityActionStatus.DRY_RUN:
            all_dry = False
        if st != IdentityActionStatus.SKIPPED:
            all_skipped = False

    if has_failed:
        rtnval = IdentityActionStatus.FAILED
        return rtnval
    if all_dry:
        rtnval = IdentityActionStatus.DRY_RUN
        return rtnval
    if all_skipped:
        rtnval = IdentityActionStatus.SKIPPED
        return rtnval

    rtnval = IdentityActionStatus.SUCCESS
    return rtnval


def _looks_like_command_not_found(
    *,
    exit_code: int,
    stderr: str,
) -> bool:
    """
        Heuristic: True when ``exit_code`` and ``stderr`` look like a
        "command not found" failure from the shell or runner.
    """
    if exit_code == 0:
        rtnval = False
        return rtnval
    lowered = stderr.lower()
    if "command not found" in lowered:
        rtnval = True
        return rtnval
    if "no such file or directory" in lowered and "realm" in lowered:
        rtnval = True
        return rtnval
    if exit_code == 127:
        rtnval = True
        return rtnval
    rtnval = False
    return rtnval


# ----------------------------------------------------------------------
# Module-level parsers — realm
# ----------------------------------------------------------------------


def _parse_realm_discover_output(
    *,
    domain: str,
    output: str,
) -> RealmDiscovery:
    """
        Parse ``realm discover`` output into a :class:`RealmDiscovery`.

        ``realm discover`` emits a single stanza:  the first
        non-indented line is the canonical domain, followed by
        indented ``key: value`` attributes.
    """
    lines = output.splitlines()
    realm_type_value: str = ""
    realm_name_value: Optional[str] = None
    server_software_value: str = ""

    for raw_line in lines:
        stripped_full = raw_line.rstrip("\r\n")
        if len(stripped_full.strip()) == 0:
            continue
        first_char = stripped_full[0]
        if first_char != " " and first_char != "\t":
            continue
        line_text = stripped_full.strip()
        if ":" not in line_text:
            continue
        key, _, value = line_text.partition(":")
        cleaned_key = key.strip()
        cleaned_value = value.strip()
        if cleaned_key == "type":
            realm_type_value = cleaned_value
        elif cleaned_key == "realm-name":
            realm_name_value = cleaned_value
        elif cleaned_key == "server-software":
            server_software_value = cleaned_value
        elif cleaned_key == "configured":
            if len(realm_type_value) == 0:
                realm_type_value = cleaned_value

    if len(realm_type_value) == 0 and len(server_software_value) > 0:
        realm_type_value = server_software_value

    domain_controllers: list[str] = []

    rtnval = RealmDiscovery(
        domain=domain,
        realm_type=realm_type_value,
        realm_name=realm_name_value,
        domain_controllers=domain_controllers,
        raw=output,
    )
    return rtnval


def _parse_realm_list_output(*, output: str) -> list[RealmMembership]:
    """
        Parse ``realm list`` into a list of :class:`RealmMembership`.

        Stanzas are separated by blank lines; the first non-indented
        line is the realm domain header.
    """
    rtnval: list[RealmMembership] = []
    current_domain: Optional[str] = None
    current_attrs: dict = {}

    def _flush() -> None:
        nonlocal current_domain, current_attrs
        if current_domain is None:
            return
        rtnval.append(_build_realm_membership(
            domain=current_domain, attributes=current_attrs,
        ))
        current_domain = None
        current_attrs = {}
        return

    for raw_line in output.splitlines():
        stripped_full = raw_line.rstrip("\r\n")
        if len(stripped_full.strip()) == 0:
            _flush()
            continue
        first_char = stripped_full[0]
        if first_char != " " and first_char != "\t":
            _flush()
            current_domain = stripped_full.strip()
            current_attrs = {}
            continue

        attribute_line = stripped_full.strip()
        if ":" not in attribute_line:
            continue
        key, _, value = attribute_line.partition(":")
        cleaned_key = key.strip()
        cleaned_value = value.strip()
        if len(cleaned_key) == 0:
            continue
        current_attrs[cleaned_key] = cleaned_value

    _flush()
    return rtnval


def _build_realm_membership(
    *,
    domain: str,
    attributes: dict,
) -> RealmMembership:
    """
        Build a :class:`RealmMembership` from a parsed stanza.
    """
    realm_name_value = attributes.get("realm-name")
    if realm_name_value is not None and len(realm_name_value) == 0:
        realm_name_value = None
    configured_value = attributes.get("configured", "").strip()
    is_configured = len(configured_value) > 0
    server_software = attributes.get("server-software")
    if server_software is not None and len(server_software) == 0:
        server_software = None
    client_software = attributes.get("client-software")
    if client_software is not None and len(client_software) == 0:
        client_software = None
    required_package = attributes.get("required-package")
    if required_package is not None and len(required_package) == 0:
        required_package = None

    rtnval = RealmMembership(
        domain=domain,
        realm_name=realm_name_value,
        configured=is_configured,
        server_software=server_software,
        client_software=client_software,
        required_package=required_package,
    )
    return rtnval


# ----------------------------------------------------------------------
# Module-level parsers — INI / sssd / krb5
# ----------------------------------------------------------------------


def _parse_ini_text(*, text: str) -> dict:
    """
        Parse INI-style ``[section] key = value`` content into
        ``dict[str, dict[str, str]]``.

        Comment lines (starting with ``#`` or ``;``) and blank lines
        are skipped.  ``key=value`` (no surrounding spaces) is
        accepted.  Values are stored verbatim with leading / trailing
        whitespace stripped.
    """
    rtnval: dict = {}
    current_section: Optional[str] = None

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if len(stripped) == 0:
            continue
        if stripped.startswith("#") or stripped.startswith(";"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section_name = stripped[1:-1].strip()
            if section_name not in rtnval:
                rtnval[section_name] = {}
            current_section = section_name
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        cleaned_key = key.strip()
        cleaned_value = value.strip()
        if len(cleaned_key) == 0:
            continue
        if current_section is None:
            continue
        rtnval[current_section][cleaned_key] = cleaned_value

    return rtnval


def _render_ini_text(*, sections: dict) -> str:
    """
        Render a ``dict[str, dict[str, str]]`` into INI-style text
        with mode-0600-friendly trailing newlines.
    """
    lines: list[str] = []
    for section_name, body in sections.items():
        lines.append("[{}]".format(section_name))
        for key, value in body.items():
            lines.append("{} = {}".format(key, value))
        lines.append("")
    rtnval = "\n".join(lines)
    if not rtnval.endswith("\n"):
        rtnval = rtnval + "\n"
    return rtnval


def _upsert_ini_directive(
    *,
    text: str,
    section: str,
    key: str,
    value: str,
) -> str:
    """
        Upsert ``[section] key = value`` into the verbatim INI
        ``text``.  Other sections, comments, blank lines and key
        order are preserved.

        When ``[section]`` does not exist a new section block is
        appended after a single trailing blank line.
    """
    lines = text.splitlines()
    new_lines: list[str] = []
    in_target_section = False
    found_target_section = False
    replaced = False
    section_end_index: Optional[int] = None

    idx = 0
    while idx < len(lines):
        raw_line = lines[idx]
        stripped = raw_line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current_name = stripped[1:-1].strip()
            if in_target_section and not replaced:
                # We are leaving the target section without finding the
                # key — insert a fresh directive line before the next
                # header.
                new_lines.append("{} = {}".format(key, value))
                replaced = True
            in_target_section = current_name == section
            if in_target_section:
                found_target_section = True
            new_lines.append(raw_line)
            idx += 1
            continue

        if in_target_section:
            if "=" in stripped and not stripped.startswith("#") and not stripped.startswith(";"):
                existing_key, _, _existing_value = stripped.partition("=")
                cleaned_existing = existing_key.strip()
                if cleaned_existing == key:
                    new_lines.append("{} = {}".format(key, value))
                    replaced = True
                    idx += 1
                    continue
            section_end_index = len(new_lines)

        new_lines.append(raw_line)
        idx += 1

    if in_target_section and not replaced:
        # Trailing target section — append the directive.
        new_lines.append("{} = {}".format(key, value))

    if not found_target_section:
        if len(new_lines) > 0 and len(new_lines[-1].strip()) > 0:
            new_lines.append("")
        new_lines.append("[{}]".format(section))
        new_lines.append("{} = {}".format(key, value))

    rtnval = "\n".join(new_lines)
    if text.endswith("\n") or not rtnval.endswith("\n"):
        rtnval = rtnval + "\n"
    # Suppress unused-name lint by referencing the variable.
    _ = section_end_index
    return rtnval


def _remove_ini_directive(
    *,
    text: str,
    section: str,
    key: str,
) -> str:
    """
        Strip ``[section] key = ...`` from the verbatim INI ``text``.
        Other sections, comments and blank lines are preserved.
    """
    lines = text.splitlines()
    new_lines: list[str] = []
    in_target_section = False

    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current_name = stripped[1:-1].strip()
            in_target_section = current_name == section
            new_lines.append(raw_line)
            continue

        if in_target_section and "=" in stripped:
            if not stripped.startswith("#") and not stripped.startswith(";"):
                existing_key, _, _existing_value = stripped.partition("=")
                cleaned_existing = existing_key.strip()
                if cleaned_existing == key:
                    continue

        new_lines.append(raw_line)

    rtnval = "\n".join(new_lines)
    if text.endswith("\n") and not rtnval.endswith("\n"):
        rtnval = rtnval + "\n"
    return rtnval


def _parse_sssctl_warnings(*, text: str) -> list[str]:
    """
        Pull ``Warning:`` / ``WARNING:`` lines out of the
        ``sssctl config-check`` stdout.
    """
    rtnval: list[str] = []
    for raw_line in text.splitlines():
        cleaned = raw_line.strip()
        if len(cleaned) == 0:
            continue
        lowered = cleaned.lower()
        if lowered.startswith("warning") or lowered.startswith("issue"):
            rtnval.append(cleaned)
    return rtnval


def _parse_sssd_domain_status(
    *,
    domain: str,
    text: str,
) -> SssdStatus:
    """
        Parse ``sssctl domain-status`` output into an
        :class:`SssdStatus`.
    """
    online_value = False
    last_request_value: Optional[str] = None

    for raw_line in text.splitlines():
        cleaned = raw_line.strip()
        if len(cleaned) == 0:
            continue
        lowered = cleaned.lower()
        if "online status:" in lowered:
            _, _, value = cleaned.partition(":")
            normalised = value.strip().lower()
            if normalised == "online":
                online_value = True
            else:
                online_value = False
        elif "last request:" in lowered:
            _, _, value = cleaned.partition(":")
            cleaned_value = value.strip()
            if len(cleaned_value) > 0:
                last_request_value = cleaned_value

    rtnval = SssdStatus(
        domain=domain,
        online=online_value,
        last_request=last_request_value,
        raw=text,
    )
    return rtnval


# ----------------------------------------------------------------------
# Module-level parsers — krb5
# ----------------------------------------------------------------------


def _build_krb_client_config(
    *,
    merged: dict,
    source_texts: list,
) -> KrbClientConfig:
    """
        Build a :class:`KrbClientConfig` from a parsed-and-merged
        krb5.conf dict and the per-file source texts.

        ``[libdefaults]`` and ``[domain_realm]`` come from the
        flat-INI parse of ``merged``; ``[realms]`` blocks have a
        non-flat shape (``REALM = { kdc = host ; ... }``) and are
        re-parsed from each source text via :func:`_krb_realm_kdcs`.
    """
    libdefaults = merged.get("libdefaults", {})
    default_realm_value = libdefaults.get("default_realm")
    if default_realm_value is not None and len(default_realm_value) == 0:
        default_realm_value = None

    realms_map: dict = {}
    realm_names: list[str] = []
    for source_text in source_texts:
        for name in _krb_realm_names(text=source_text):
            if name not in realm_names:
                realm_names.append(name)

    for realm_name in realm_names:
        merged_kdcs: list[str] = []
        for source_text in source_texts:
            text_kdcs = _krb_realm_kdcs(text=source_text, realm=realm_name)
            for host in text_kdcs:
                if host not in merged_kdcs:
                    merged_kdcs.append(host)
        if len(merged_kdcs) > 0:
            realms_map[realm_name] = merged_kdcs

    domain_block = merged.get("domain_realm", {})
    domain_realm_map: dict = {}
    for k, v in domain_block.items():
        domain_realm_map[k] = v

    rtnval = KrbClientConfig(
        default_realm=default_realm_value,
        realms=realms_map,
        domain_realm=domain_realm_map,
    )
    return rtnval


def _krb_realm_names(*, text: str) -> list[str]:
    """
        Return the realm names declared inside the ``[realms]``
        section of ``text``.  A realm header is a line of the form
        ``<NAME> = {``.
    """
    rtnval: list[str] = []
    in_realms_section = False

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section_name = stripped[1:-1].strip()
            in_realms_section = section_name == "realms"
            continue
        if not in_realms_section:
            continue
        if "=" in stripped and "{" in stripped:
            head, _, tail = stripped.partition("=")
            cleaned_head = head.strip()
            cleaned_tail = tail.strip()
            if cleaned_tail.startswith("{") and len(cleaned_head) > 0:
                if cleaned_head not in rtnval:
                    rtnval.append(cleaned_head)
    return rtnval


def _krb_realm_kdcs(*, text: str, realm: str) -> list[str]:
    """
        Return every ``kdc = <host>`` entry within the
        ``[realms] <realm> = { ... }`` block of ``text``.
    """
    rtnval: list[str] = []
    in_realms_section = False
    in_target_block = False

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section_name = stripped[1:-1].strip()
            in_realms_section = section_name == "realms"
            in_target_block = False
            continue

        if not in_realms_section:
            continue

        if "=" in stripped and "{" in stripped and not in_target_block:
            head, _, _tail = stripped.partition("=")
            if head.strip() == realm:
                in_target_block = True
                continue

        if in_target_block:
            if stripped.startswith("}"):
                in_target_block = False
                continue
            if stripped.startswith("kdc"):
                _, _, value = stripped.partition("=")
                cleaned_host = value.strip()
                if len(cleaned_host) > 0:
                    rtnval.append(cleaned_host)

    return rtnval


def _add_krb_kdc(
    *,
    text: str,
    realm: str,
    host: str,
) -> str:
    """
        Insert ``kdc = <host>`` inside the ``<realm> = { ... }``
        block of ``text``.  When the realm block does not exist, a
        fresh block is appended after the ``[realms]`` section
        header (creating the section if absent).
    """
    lines = text.splitlines()
    new_lines: list[str] = []
    in_realms_section = False
    realms_section_seen = False
    in_target_block = False
    inserted = False
    realm_block_seen = False

    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section_name = stripped[1:-1].strip()
            in_realms_section = section_name == "realms"
            if in_realms_section:
                realms_section_seen = True
            in_target_block = False
            new_lines.append(raw_line)
            continue

        if in_realms_section and ("=" in stripped) and ("{" in stripped) and (not in_target_block):
            head, _, _tail = stripped.partition("=")
            if head.strip() == realm:
                in_target_block = True
                realm_block_seen = True
                new_lines.append(raw_line)
                new_lines.append("    kdc = {}".format(host))
                inserted = True
                continue

        if in_target_block and stripped.startswith("}"):
            in_target_block = False

        new_lines.append(raw_line)

    if not realms_section_seen:
        if len(new_lines) > 0 and len(new_lines[-1].strip()) > 0:
            new_lines.append("")
        new_lines.append("[realms]")
        new_lines.append("{} = {{".format(realm))
        new_lines.append("    kdc = {}".format(host))
        new_lines.append("}")
        inserted = True
    elif not realm_block_seen:
        new_lines.append("{} = {{".format(realm))
        new_lines.append("    kdc = {}".format(host))
        new_lines.append("}")
        inserted = True

    rtnval = "\n".join(new_lines)
    if text.endswith("\n") or not rtnval.endswith("\n"):
        rtnval = rtnval + "\n"
    _ = inserted
    return rtnval


def _remove_krb_kdc(
    *,
    text: str,
    realm: str,
    host: str,
) -> str:
    """
        Strip a ``kdc = <host>`` line from the ``<realm> = { ... }``
        block of ``text``.
    """
    lines = text.splitlines()
    new_lines: list[str] = []
    in_realms_section = False
    in_target_block = False

    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section_name = stripped[1:-1].strip()
            in_realms_section = section_name == "realms"
            in_target_block = False
            new_lines.append(raw_line)
            continue

        if in_realms_section and ("=" in stripped) and ("{" in stripped) and (not in_target_block):
            head, _, _tail = stripped.partition("=")
            if head.strip() == realm:
                in_target_block = True
                new_lines.append(raw_line)
                continue

        if in_target_block:
            if stripped.startswith("}"):
                in_target_block = False
                new_lines.append(raw_line)
                continue
            if stripped.startswith("kdc"):
                _, _, value = stripped.partition("=")
                cleaned_host = value.strip()
                if cleaned_host == host:
                    continue

        new_lines.append(raw_line)

    rtnval = "\n".join(new_lines)
    if text.endswith("\n") and not rtnval.endswith("\n"):
        rtnval = rtnval + "\n"
    return rtnval


# ----------------------------------------------------------------------
# Module-level parsers — klist
# ----------------------------------------------------------------------


def _parse_klist_output(*, text: str) -> list[KrbTicket]:
    """
        Parse ``klist`` output into :class:`KrbTicket` records.

        Output shape (MIT Kerberos):

            Ticket cache: KEYRING:persistent:1000:1000
            Default principal: alice@EXAMPLE.COM

            Valid starting       Expires              Service principal
            04/27/26 09:00:01    04/27/26 19:00:01    krbtgt/EXAMPLE.COM@EXAMPLE.COM
                renew until 05/04/26 09:00:01

        Tickets without a renew line carry ``renewable_until=None``.
    """
    principal_value = ""
    rtnval: list[KrbTicket] = []
    in_table = False
    pending: Optional[KrbTicket] = None

    for raw_line in text.splitlines():
        stripped = raw_line.rstrip("\r\n")
        cleaned = stripped.strip()
        if len(cleaned) == 0:
            continue

        if cleaned.startswith("Default principal:"):
            _, _, value = cleaned.partition(":")
            principal_value = value.strip()
            continue

        if cleaned.startswith("Valid starting"):
            in_table = True
            continue

        if not in_table:
            continue

        # Renew-until continuation line.
        if cleaned.lower().startswith("renew until"):
            renew_text = cleaned[len("renew until"):].strip()
            if pending is not None:
                pending.renewable_until = renew_text
            continue

        # Standard ticket row: two timestamps + service principal.  A
        # MIT ``klist`` row uses two-space columns; tabs may appear.
        tokens = cleaned.split()
        if len(tokens) >= 5:
            starts_value = "{} {}".format(tokens[0], tokens[1])
            expires_value = "{} {}".format(tokens[2], tokens[3])
            service_value = " ".join(tokens[4:])
            new_ticket = KrbTicket(
                principal=principal_value,
                service=service_value,
                starts=starts_value,
                expires=expires_value,
                renewable_until=None,
            )
            rtnval.append(new_ticket)
            pending = new_ticket

    return rtnval
