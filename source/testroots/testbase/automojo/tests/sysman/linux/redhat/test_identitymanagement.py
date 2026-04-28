# Tests for the IdentityManager facade.

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from automojo import testbase

from automojo.sysman.linux.redhat.identitymanagement import (
    IdentityActionStatus,
    IdentityCredentialError,
    IdentityManager,
    IdentityManagerConfig,
    IdentityToolNotAvailableError,
    KrbTicket,
    RealmDiscovery,
    RealmMembership,
    SssdConfigCheckResult,
    SssdStatus,
)
from automojo.sysman.linux.redhat.runners.models import RunResult


# ----------------------------------------------------------------------
# Mock runner / sibling-manager stubs
# ----------------------------------------------------------------------


@dataclass
class _MockRunner:
    """
        Test runner stub.  ``results`` maps argv tuples to canned
        :class:`RunResult` values.  ``calls`` records every dispatch.
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
        key = tuple(args)
        if key in self.results:
            return self.results[key]
        if self.default_result is not None:
            return self.default_result
        rtnval = RunResult(
            exit_code=0, stdout="", stderr="", command=list(args),
        )
        return rtnval

    def add_aspect(self, aspect) -> None:
        return

    def remove_aspect(self, aspect) -> None:
        return

    def get_metrics(self):
        return None


class _SvcStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class _ServiceResult:
    status: _SvcStatus
    detail: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list] = None


@dataclass
class _MockServiceManager:
    """
        Stub for the injected :class:`ServiceManager`.  Records every
        ``restart`` call and returns the canned ``next_result``.
    """
    calls: list = field(default_factory=list)
    next_result: Optional[_ServiceResult] = None

    def restart(self, name: str) -> _ServiceResult:
        self.calls.append({"op": "restart", "name": name})
        if self.next_result is not None:
            return self.next_result
        rtnval = _ServiceResult(
            status=_SvcStatus.SUCCESS,
            detail=name,
            exit_code=0,
            stdout="",
            stderr="",
            command=["systemctl", "restart", name],
        )
        return rtnval


class _AuthStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class _AuthResult:
    status: _AuthStatus
    detail: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list] = None


@dataclass
class _MockAuthenticationManager:
    """
        Stub for the injected :class:`AuthenticationManager`.
    """
    calls: list = field(default_factory=list)
    next_result: Optional[_AuthResult] = None

    def set_nss_sources(self, db: str, sources: list) -> _AuthResult:
        self.calls.append({"db": db, "sources": list(sources)})
        if self.next_result is not None:
            return self.next_result
        rtnval = _AuthResult(
            status=_AuthStatus.SUCCESS,
            detail="{}: {}".format(db, " ".join(list(sources))),
            exit_code=0,
            stdout="",
            stderr="",
            command=["tee", "/etc/nsswitch.conf"],
        )
        return rtnval


@dataclass
class _Cred:
    """
        Lightweight credential-shaped object for tests.
    """
    username: str
    password: str


# ----------------------------------------------------------------------
# Sample command outputs
# ----------------------------------------------------------------------


_REALM_DISCOVER_OUTPUT = (
    "example.com\n"
    "  type: kerberos\n"
    "  realm-name: EXAMPLE.COM\n"
    "  domain-name: example.com\n"
    "  configured: kerberos-member\n"
    "  server-software: ipa\n"
    "  client-software: sssd\n"
    "  required-package: ipa-client\n"
)


_REALM_LIST_TWO_REALMS = (
    "example.com\n"
    "  type: kerberos\n"
    "  realm-name: EXAMPLE.COM\n"
    "  configured: kerberos-member\n"
    "  server-software: ipa\n"
    "  client-software: sssd\n"
    "  required-package: ipa-client\n"
    "\n"
    "ad.example.com\n"
    "  type: kerberos\n"
    "  realm-name: AD.EXAMPLE.COM\n"
    "  configured: kerberos-member\n"
    "  server-software: active-directory\n"
    "  client-software: sssd\n"
    "  required-package: adcli\n"
)


_KLIST_OUTPUT = (
    "Ticket cache: KEYRING:persistent:1000:1000\n"
    "Default principal: alice@EXAMPLE.COM\n"
    "\n"
    "Valid starting       Expires              Service principal\n"
    "04/27/26 09:00:01    04/27/26 19:00:01    krbtgt/EXAMPLE.COM@EXAMPLE.COM\n"
    "    renew until 05/04/26 09:00:01\n"
)


_SSSD_DOMAIN_STATUS_OUTPUT = (
    "Online status: Online\n"
    "Last request: 2026-04-27 12:34:56\n"
    "Servers:\n"
    "  - ipa01.example.com (online)\n"
)


_KRB5_CONF_TEXT = (
    "# Comment line\n"
    "[libdefaults]\n"
    "    default_realm = OLD.EXAMPLE.COM\n"
    "    dns_lookup_realm = false\n"
    "\n"
    "[realms]\n"
    "EXAMPLE.COM = {\n"
    "    kdc = kdc1.example.com\n"
    "    admin_server = kdc1.example.com\n"
    "}\n"
    "\n"
    "[domain_realm]\n"
    "    .example.com = EXAMPLE.COM\n"
    "    example.com = EXAMPLE.COM\n"
)


# ----------------------------------------------------------------------
# discover / list / join / leave
# ----------------------------------------------------------------------


def test_discover_parses_realm_output():
    """
        discover() parses ``realm discover`` into a RealmDiscovery.
    """
    runner = _MockRunner()
    runner.results[("realm", "discover", "example.com")] = RunResult(
        exit_code=0, stdout=_REALM_DISCOVER_OUTPUT, stderr="",
        command=["realm"],
    )
    im = IdentityManager(runner, IdentityManagerConfig())
    discovery = im.discover("example.com")
    testbase.assert_type(discovery, RealmDiscovery)
    testbase.assert_equal(discovery.domain, "example.com")
    testbase.assert_equal(discovery.realm_type, "kerberos")
    testbase.assert_equal(discovery.realm_name, "EXAMPLE.COM")
    return


def test_discover_raises_when_realm_not_installed():
    """
        discover() raises IdentityToolNotAvailableError when the
        runner reports a "command not found" failure.
    """
    runner = _MockRunner()
    runner.results[("realm", "discover", "example.com")] = RunResult(
        exit_code=127, stdout="", stderr="realm: command not found\n",
        command=["realm"],
    )
    im = IdentityManager(runner, IdentityManagerConfig())
    raised = False
    try:
        im.discover("example.com")
    except IdentityToolNotAvailableError:
        raised = True
    testbase.assert_true(raised)
    return


def test_list_realms_parses_two_stanzas():
    """
        list_realms() returns a RealmMembership per parsed stanza.
    """
    runner = _MockRunner()
    runner.results[("realm", "list")] = RunResult(
        exit_code=0, stdout=_REALM_LIST_TWO_REALMS, stderr="",
        command=["realm"],
    )
    im = IdentityManager(runner, IdentityManagerConfig())
    memberships = im.list_realms()
    testbase.assert_equal(len(memberships), 2)
    testbase.assert_type(memberships[0], RealmMembership)
    testbase.assert_equal(memberships[0].domain, "example.com")
    testbase.assert_equal(memberships[0].realm_name, "EXAMPLE.COM")
    testbase.assert_true(memberships[0].configured)
    testbase.assert_equal(memberships[1].domain, "ad.example.com")
    testbase.assert_equal(memberships[1].server_software, "active-directory")
    return


def test_list_realms_returns_empty_when_unconfigured():
    """
        list_realms() returns an empty list when ``realm list`` exits 0
        with no output.
    """
    runner = _MockRunner()
    runner.results[("realm", "list")] = RunResult(
        exit_code=0, stdout="", stderr="", command=["realm"],
    )
    im = IdentityManager(runner, IdentityManagerConfig())
    memberships = im.list_realms()
    testbase.assert_equal(len(memberships), 0)
    return


def test_join_password_via_stdin_never_on_argv():
    """
        join() routes the password through runner.stdin and never
        places it on argv; argv carries -U <user> only.
    """
    secret_password = "p@ssw0rd"
    runner = _MockRunner()
    runner.results[("realm", "list")] = RunResult(
        exit_code=0, stdout="", stderr="", command=["realm"],
    )
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=["realm"],
    )

    im = IdentityManager(runner, IdentityManagerConfig(elevated=True))
    cred = _Cred(username="admin", password=secret_password)
    result = im.join("example.com", credential=cred)

    testbase.assert_equal(result.status, IdentityActionStatus.SUCCESS)

    join_calls = [c for c in runner.calls if c["args"][:2] == ["realm", "join"]]
    testbase.assert_equal(len(join_calls), 1)
    join_call = join_calls[0]
    testbase.assert_true(join_call["elevated"])
    testbase.assert_equal(join_call["stdin"], secret_password)
    testbase.assert_false(secret_password in join_call["args"])
    testbase.assert_true("-U" in join_call["args"])
    testbase.assert_true("admin" in join_call["args"])
    # Result.command must also not carry the secret.
    testbase.assert_false(secret_password in (result.command or []))
    return


def test_join_otp_rides_on_stdin():
    """
        join() with a one-time password puts the OTP on stdin and the
        bare ``--one-time-password`` flag on argv (no value).
    """
    otp_value = "TopSecretOTP"
    runner = _MockRunner()
    runner.results[("realm", "list")] = RunResult(
        exit_code=0, stdout="", stderr="", command=["realm"],
    )
    im = IdentityManager(runner, IdentityManagerConfig())
    result = im.join("example.com", one_time_password=otp_value)
    testbase.assert_equal(result.status, IdentityActionStatus.SUCCESS)

    join_calls = [c for c in runner.calls if c["args"][:2] == ["realm", "join"]]
    testbase.assert_equal(len(join_calls), 1)
    testbase.assert_equal(join_calls[0]["stdin"], otp_value)
    testbase.assert_false(otp_value in join_calls[0]["args"])
    testbase.assert_true("--one-time-password" in join_calls[0]["args"])
    return


def test_join_skipped_when_already_joined():
    """
        join() returns SKIPPED and does NOT dispatch realm join when
        the host is already joined to the requested domain.
    """
    runner = _MockRunner()
    runner.results[("realm", "list")] = RunResult(
        exit_code=0, stdout=_REALM_LIST_TWO_REALMS, stderr="",
        command=["realm"],
    )
    im = IdentityManager(runner, IdentityManagerConfig())
    cred = _Cred(username="admin", password="x")
    result = im.join("example.com", credential=cred)
    testbase.assert_equal(result.status, IdentityActionStatus.SKIPPED)

    join_calls = [c for c in runner.calls if c["args"][:2] == ["realm", "join"]]
    testbase.assert_equal(len(join_calls), 0)
    return


def test_join_dry_run_short_circuits():
    """
        join() returns DRY_RUN and dispatches no command when
        config.dry_run=True.
    """
    runner = _MockRunner()
    im = IdentityManager(runner, IdentityManagerConfig(dry_run=True))
    cred = _Cred(username="admin", password="x")
    result = im.join("example.com", credential=cred)
    testbase.assert_equal(result.status, IdentityActionStatus.DRY_RUN)
    testbase.assert_equal(len(runner.calls), 0)
    return


def test_join_failed_maps_runner_nonzero():
    """
        join() returns FAILED and surfaces exit_code / stderr when
        realm join exits non-zero.
    """
    runner = _MockRunner()
    runner.results[("realm", "list")] = RunResult(
        exit_code=0, stdout="", stderr="", command=["realm"],
    )
    runner.default_result = RunResult(
        exit_code=2, stdout="", stderr="bind failed\n",
        command=["realm"],
    )
    im = IdentityManager(runner, IdentityManagerConfig())
    cred = _Cred(username="admin", password="x")
    result = im.join("example.com", credential=cred)
    testbase.assert_equal(result.status, IdentityActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 2)
    testbase.assert_equal(result.stderr, "bind failed\n")
    return


def test_join_raises_when_credential_absent():
    """
        join() raises IdentityCredentialError when no credential and
        no OTP are supplied.
    """
    runner = _MockRunner()
    im = IdentityManager(runner, IdentityManagerConfig())
    raised = False
    try:
        im.join("example.com")
    except IdentityCredentialError:
        raised = True
    testbase.assert_true(raised)
    return


def test_leave_skipped_when_not_joined():
    """
        leave() returns SKIPPED when the host is not joined to the
        target domain.
    """
    runner = _MockRunner()
    runner.results[("realm", "list")] = RunResult(
        exit_code=0, stdout="", stderr="", command=["realm"],
    )
    im = IdentityManager(runner, IdentityManagerConfig())
    result = im.leave("example.com")
    testbase.assert_equal(result.status, IdentityActionStatus.SKIPPED)
    leave_calls = [c for c in runner.calls if c["args"][:2] == ["realm", "leave"]]
    testbase.assert_equal(len(leave_calls), 0)
    return


def test_leave_dispatches_when_joined():
    """
        leave() dispatches ``realm leave <domain>`` when the host is
        joined.
    """
    runner = _MockRunner()
    runner.results[("realm", "list")] = RunResult(
        exit_code=0, stdout=_REALM_LIST_TWO_REALMS, stderr="",
        command=["realm"],
    )
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=["realm"],
    )
    im = IdentityManager(runner, IdentityManagerConfig(elevated=True))
    result = im.leave("example.com")
    testbase.assert_equal(result.status, IdentityActionStatus.SUCCESS)
    leave_calls = [c for c in runner.calls if c["args"][:2] == ["realm", "leave"]]
    testbase.assert_equal(len(leave_calls), 1)
    testbase.assert_equal(leave_calls[0]["args"], ["realm", "leave", "example.com"])
    testbase.assert_true(leave_calls[0]["elevated"])
    return


# ----------------------------------------------------------------------
# SSSD config
# ----------------------------------------------------------------------


_SSSD_SAMPLE = (
    "[sssd]\n"
    "config_file_version = 2\n"
    "domains = example.com\n"
    "\n"
    "[domain/example.com]\n"
    "id_provider = ipa\n"
    "ipa_server = ipa01.example.com\n"
)


def _runner_with_sssd_conf(text: str) -> _MockRunner:
    runner = _MockRunner()
    runner.results[("cat", "/etc/sssd/sssd.conf")] = RunResult(
        exit_code=0, stdout=text, stderr="", command=["cat"],
    )
    return runner


def test_read_sssd_conf_parses_sections():
    """
        read_sssd_conf() returns a section/key dict from cat output.
    """
    runner = _runner_with_sssd_conf(_SSSD_SAMPLE)
    im = IdentityManager(runner, IdentityManagerConfig())
    parsed = im.read_sssd_conf()
    testbase.assert_true("sssd" in parsed)
    testbase.assert_equal(parsed["sssd"]["domains"], "example.com")
    testbase.assert_equal(parsed["domain/example.com"]["id_provider"], "ipa")
    return


def test_read_sssd_conf_empty_when_absent():
    """
        read_sssd_conf() returns an empty dict when cat exits non-zero.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/sssd/sssd.conf")] = RunResult(
        exit_code=1, stdout="", stderr="No such file\n", command=["cat"],
    )
    im = IdentityManager(runner, IdentityManagerConfig())
    parsed = im.read_sssd_conf()
    testbase.assert_equal(len(parsed), 0)
    return


def test_write_sssd_dropin_uses_tee_and_chmod_chown():
    """
        write_sssd_dropin() runs tee + chmod 0600 + chown root:root
        with content delivered via stdin.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=["tee"],
    )
    im = IdentityManager(runner, IdentityManagerConfig(elevated=True))
    sections = {
        "domain/example.com": {
            "id_provider": "ipa",
            "ipa_server": "ipa01.example.com",
        },
    }
    result = im.write_sssd_dropin("example", sections)
    testbase.assert_equal(result.status, IdentityActionStatus.SUCCESS)

    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_equal(tee_calls[0]["args"], ["tee", "/etc/sssd/conf.d/example.conf"])
    testbase.assert_true("[domain/example.com]" in (tee_calls[0]["stdin"] or ""))

    chmod_calls = [c for c in runner.calls if c["args"][0] == "chmod"]
    testbase.assert_equal(len(chmod_calls), 1)
    testbase.assert_true("0600" in chmod_calls[0]["args"])

    chown_calls = [c for c in runner.calls if c["args"][0] == "chown"]
    testbase.assert_equal(len(chown_calls), 1)
    testbase.assert_true("root:root" in chown_calls[0]["args"])
    return


def test_set_sssd_directive_skipped_when_unchanged():
    """
        set_sssd_directive() returns SKIPPED when the requested value
        already matches the file.
    """
    runner = _runner_with_sssd_conf(_SSSD_SAMPLE)
    im = IdentityManager(runner, IdentityManagerConfig())
    result = im.set_sssd_directive(
        section="domain/example.com",
        key="id_provider",
        value="ipa",
    )
    testbase.assert_equal(result.status, IdentityActionStatus.SKIPPED)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 0)
    return


def test_set_sssd_directive_preserves_other_sections():
    """
        set_sssd_directive() rewrites the file via tee preserving
        every other section's keys.
    """
    runner = _runner_with_sssd_conf(_SSSD_SAMPLE)
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=["tee"],
    )
    im = IdentityManager(runner, IdentityManagerConfig())
    result = im.set_sssd_directive(
        section="domain/example.com",
        key="id_provider",
        value="ldap",
    )
    testbase.assert_equal(result.status, IdentityActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    new_text = tee_calls[0]["stdin"] or ""
    testbase.assert_true("id_provider = ldap" in new_text)
    testbase.assert_true("config_file_version = 2" in new_text)
    testbase.assert_true("domains = example.com" in new_text)
    return


def test_remove_sssd_directive_skipped_when_absent():
    """
        remove_sssd_directive() returns SKIPPED when the directive
        does not exist.
    """
    runner = _runner_with_sssd_conf(_SSSD_SAMPLE)
    im = IdentityManager(runner, IdentityManagerConfig())
    result = im.remove_sssd_directive(
        section="domain/example.com",
        key="missing_key",
    )
    testbase.assert_equal(result.status, IdentityActionStatus.SKIPPED)
    return


def test_remove_sssd_directive_strips_line():
    """
        remove_sssd_directive() rewrites the file without the
        targeted directive.
    """
    runner = _runner_with_sssd_conf(_SSSD_SAMPLE)
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=["tee"],
    )
    im = IdentityManager(runner, IdentityManagerConfig())
    result = im.remove_sssd_directive(
        section="domain/example.com",
        key="ipa_server",
    )
    testbase.assert_equal(result.status, IdentityActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_true(len(tee_calls) >= 1)
    testbase.assert_false("ipa_server" in (tee_calls[0]["stdin"] or ""))
    return


def test_config_check_maps_sssctl_result():
    """
        config_check() returns SssdConfigCheckResult mapping ok and
        warnings.
    """
    runner = _MockRunner()
    runner.results[("sssctl", "config-check")] = RunResult(
        exit_code=0,
        stdout="Issue: deprecated option in /etc/sssd/sssd.conf\n",
        stderr="",
        command=["sssctl"],
    )
    im = IdentityManager(runner, IdentityManagerConfig())
    result = im.config_check()
    testbase.assert_type(result, SssdConfigCheckResult)
    testbase.assert_true(result.ok)
    testbase.assert_equal(result.exit_code, 0)
    testbase.assert_equal(len(result.warnings), 1)
    return


# ----------------------------------------------------------------------
# SSSD runtime
# ----------------------------------------------------------------------


def test_restart_sssd_delegates_to_service_manager():
    """
        restart_sssd() delegates to the injected ServiceManager.
    """
    runner = _MockRunner()
    svc = _MockServiceManager()
    im = IdentityManager(runner, IdentityManagerConfig(), service_manager=svc)
    result = im.restart_sssd()
    testbase.assert_equal(result.status, IdentityActionStatus.SUCCESS)
    testbase.assert_equal(len(svc.calls), 1)
    testbase.assert_equal(svc.calls[0]["name"], "sssd")
    return


def test_restart_sssd_falls_back_to_runner_when_no_service_manager():
    """
        restart_sssd() dispatches systemctl restart sssd when no
        ServiceManager is injected.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=["systemctl"],
    )
    im = IdentityManager(runner, IdentityManagerConfig(elevated=True))
    result = im.restart_sssd()
    testbase.assert_equal(result.status, IdentityActionStatus.SUCCESS)
    restart_calls = [
        c for c in runner.calls
        if c["args"] == ["systemctl", "restart", "sssd"]
    ]
    testbase.assert_equal(len(restart_calls), 1)
    return


def test_invalidate_cache_default_is_full_expiry():
    """
        invalidate_cache() with no target dispatches sss_cache -E.
    """
    runner = _MockRunner()
    im = IdentityManager(runner, IdentityManagerConfig())
    result = im.invalidate_cache()
    testbase.assert_equal(result.status, IdentityActionStatus.SUCCESS)
    testbase.assert_equal(runner.calls[-1]["args"], ["sss_cache", "-E"])
    return


def test_invalidate_cache_user_variant():
    """
        invalidate_cache(user=alice) dispatches sss_cache -u alice.
    """
    runner = _MockRunner()
    im = IdentityManager(runner, IdentityManagerConfig())
    result = im.invalidate_cache(user="alice")
    testbase.assert_equal(result.status, IdentityActionStatus.SUCCESS)
    testbase.assert_equal(runner.calls[-1]["args"], ["sss_cache", "-u", "alice"])
    return


def test_invalidate_cache_group_variant():
    """
        invalidate_cache(group=admins) dispatches sss_cache -g admins.
    """
    runner = _MockRunner()
    im = IdentityManager(runner, IdentityManagerConfig())
    result = im.invalidate_cache(group="admins")
    testbase.assert_equal(result.status, IdentityActionStatus.SUCCESS)
    testbase.assert_equal(runner.calls[-1]["args"], ["sss_cache", "-g", "admins"])
    return


def test_domain_status_parses_online_and_last_request():
    """
        domain_status() parses the Online status / Last request lines.
    """
    runner = _MockRunner()
    runner.results[("sssctl", "domain-status", "example.com")] = RunResult(
        exit_code=0, stdout=_SSSD_DOMAIN_STATUS_OUTPUT, stderr="",
        command=["sssctl"],
    )
    im = IdentityManager(runner, IdentityManagerConfig())
    status = im.domain_status("example.com")
    testbase.assert_type(status, SssdStatus)
    testbase.assert_true(status.online)
    testbase.assert_equal(status.last_request, "2026-04-27 12:34:56")
    return


# ----------------------------------------------------------------------
# Kerberos config
# ----------------------------------------------------------------------


def _runner_with_krb5(text: str) -> _MockRunner:
    runner = _MockRunner()
    runner.results[("cat", "/etc/krb5.conf")] = RunResult(
        exit_code=0, stdout=text, stderr="", command=["cat"],
    )
    runner.results[("ls", "-1", "/etc/krb5.conf.d")] = RunResult(
        exit_code=1, stdout="", stderr="No such directory\n",
        command=["ls"],
    )
    return runner


def test_read_krb5_conf_parses_default_realm_and_kdcs():
    """
        read_krb5_conf() merges the primary file and exposes
        default_realm + realm-to-KDC map.
    """
    runner = _runner_with_krb5(_KRB5_CONF_TEXT)
    im = IdentityManager(runner, IdentityManagerConfig())
    cfg = im.read_krb5_conf()
    testbase.assert_equal(cfg.default_realm, "OLD.EXAMPLE.COM")
    testbase.assert_true("EXAMPLE.COM" in cfg.realms)
    testbase.assert_true("kdc1.example.com" in cfg.realms["EXAMPLE.COM"])
    testbase.assert_equal(cfg.domain_realm[".example.com"], "EXAMPLE.COM")
    return


def test_read_krb5_conf_merges_dropins():
    """
        read_krb5_conf() merges drop-ins with later-overrides-earlier
        semantics.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/krb5.conf")] = RunResult(
        exit_code=0, stdout=_KRB5_CONF_TEXT, stderr="", command=["cat"],
    )
    runner.results[("ls", "-1", "/etc/krb5.conf.d")] = RunResult(
        exit_code=0, stdout="zz_override.conf\n", stderr="",
        command=["ls"],
    )
    runner.results[("cat", "/etc/krb5.conf.d/zz_override.conf")] = RunResult(
        exit_code=0,
        stdout=(
            "[libdefaults]\n"
            "    default_realm = NEW.EXAMPLE.COM\n"
        ),
        stderr="",
        command=["cat"],
    )
    im = IdentityManager(runner, IdentityManagerConfig())
    cfg = im.read_krb5_conf()
    testbase.assert_equal(cfg.default_realm, "NEW.EXAMPLE.COM")
    return


def test_set_default_realm_skipped_when_unchanged():
    """
        set_default_realm() returns SKIPPED when the file already
        carries the requested value.
    """
    runner = _runner_with_krb5(_KRB5_CONF_TEXT)
    im = IdentityManager(runner, IdentityManagerConfig())
    result = im.set_default_realm("OLD.EXAMPLE.COM")
    testbase.assert_equal(result.status, IdentityActionStatus.SKIPPED)
    return


def test_set_default_realm_writes_when_changed():
    """
        set_default_realm() rewrites only the
        [libdefaults].default_realm directive.
    """
    runner = _runner_with_krb5(_KRB5_CONF_TEXT)
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=["tee"],
    )
    im = IdentityManager(runner, IdentityManagerConfig())
    result = im.set_default_realm("NEW.EXAMPLE.COM")
    testbase.assert_equal(result.status, IdentityActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    written = tee_calls[0]["stdin"] or ""
    testbase.assert_true("default_realm = NEW.EXAMPLE.COM" in written)
    testbase.assert_false("default_realm = OLD.EXAMPLE.COM" in written)
    # Other content preserved.
    testbase.assert_true("dns_lookup_realm = false" in written)
    testbase.assert_true("[domain_realm]" in written)
    return


def test_add_kdc_skipped_when_present():
    """
        add_kdc() returns SKIPPED when the KDC already exists in the
        realm block.
    """
    runner = _runner_with_krb5(_KRB5_CONF_TEXT)
    im = IdentityManager(runner, IdentityManagerConfig())
    result = im.add_kdc("EXAMPLE.COM", "kdc1.example.com")
    testbase.assert_equal(result.status, IdentityActionStatus.SKIPPED)
    return


def test_add_kdc_inserts_into_existing_block():
    """
        add_kdc() inserts a new ``kdc = <host>`` line inside the
        existing realm block.
    """
    runner = _runner_with_krb5(_KRB5_CONF_TEXT)
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=["tee"],
    )
    im = IdentityManager(runner, IdentityManagerConfig())
    result = im.add_kdc("EXAMPLE.COM", "kdc2.example.com")
    testbase.assert_equal(result.status, IdentityActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    written = tee_calls[0]["stdin"] or ""
    testbase.assert_true("kdc = kdc1.example.com" in written)
    testbase.assert_true("kdc = kdc2.example.com" in written)
    return


def test_remove_kdc_strips_line():
    """
        remove_kdc() removes the matching ``kdc = <host>`` line.
    """
    runner = _runner_with_krb5(_KRB5_CONF_TEXT)
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=["tee"],
    )
    im = IdentityManager(runner, IdentityManagerConfig())
    result = im.remove_kdc("EXAMPLE.COM", "kdc1.example.com")
    testbase.assert_equal(result.status, IdentityActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    written = tee_calls[0]["stdin"] or ""
    testbase.assert_false("kdc = kdc1.example.com" in written)
    return


def test_remove_kdc_skipped_when_absent():
    """
        remove_kdc() returns SKIPPED when the KDC is not listed.
    """
    runner = _runner_with_krb5(_KRB5_CONF_TEXT)
    im = IdentityManager(runner, IdentityManagerConfig())
    result = im.remove_kdc("EXAMPLE.COM", "kdc-unknown.example.com")
    testbase.assert_equal(result.status, IdentityActionStatus.SKIPPED)
    return


# ----------------------------------------------------------------------
# Kerberos tickets
# ----------------------------------------------------------------------


def test_kinit_password_via_stdin_never_on_argv():
    """
        kinit() with a credential routes the password on stdin and
        argv carries only the principal.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=["kinit"],
    )
    im = IdentityManager(runner, IdentityManagerConfig())
    cred = _Cred(username="alice", password="kerb-pass")
    result = im.kinit("alice@EXAMPLE.COM", credential=cred)
    testbase.assert_equal(result.status, IdentityActionStatus.SUCCESS)
    testbase.assert_equal(len(runner.calls), 1)
    testbase.assert_equal(runner.calls[0]["args"], ["kinit", "alice@EXAMPLE.COM"])
    testbase.assert_equal(runner.calls[0]["stdin"], "kerb-pass")
    testbase.assert_false("kerb-pass" in runner.calls[0]["args"])
    testbase.assert_false("kerb-pass" in (result.command or []))
    return


def test_kinit_keytab_no_stdin():
    """
        kinit() with a keytab uses ``-kt <keytab> <principal>`` and
        no stdin payload.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=["kinit"],
    )
    im = IdentityManager(runner, IdentityManagerConfig())
    result = im.kinit(
        "host/box.example.com@EXAMPLE.COM",
        keytab="/etc/krb5.keytab",
    )
    testbase.assert_equal(result.status, IdentityActionStatus.SUCCESS)
    testbase.assert_equal(len(runner.calls), 1)
    testbase.assert_equal(
        runner.calls[0]["args"],
        ["kinit", "-kt", "/etc/krb5.keytab", "host/box.example.com@EXAMPLE.COM"],
    )
    testbase.assert_true(runner.calls[0]["stdin"] is None)
    return


def test_kinit_raises_when_credential_missing():
    """
        kinit() raises IdentityCredentialError when neither a
        credential nor a keytab is supplied.
    """
    runner = _MockRunner()
    im = IdentityManager(runner, IdentityManagerConfig())
    raised = False
    try:
        im.kinit("alice@EXAMPLE.COM")
    except IdentityCredentialError:
        raised = True
    testbase.assert_true(raised)
    return


def test_kinit_dry_run_short_circuits():
    """
        kinit() with dry_run=True returns DRY_RUN and dispatches
        nothing.
    """
    runner = _MockRunner()
    im = IdentityManager(runner, IdentityManagerConfig(dry_run=True))
    cred = _Cred(username="alice", password="kerb-pass")
    result = im.kinit("alice@EXAMPLE.COM", credential=cred)
    testbase.assert_equal(result.status, IdentityActionStatus.DRY_RUN)
    testbase.assert_equal(len(runner.calls), 0)
    return


def test_klist_parses_ticket_rows():
    """
        klist() parses the tabular output into KrbTicket records.
    """
    runner = _MockRunner()
    runner.results[("klist",)] = RunResult(
        exit_code=0, stdout=_KLIST_OUTPUT, stderr="",
        command=["klist"],
    )
    im = IdentityManager(runner, IdentityManagerConfig())
    tickets = im.klist()
    testbase.assert_equal(len(tickets), 1)
    testbase.assert_type(tickets[0], KrbTicket)
    testbase.assert_equal(tickets[0].principal, "alice@EXAMPLE.COM")
    testbase.assert_equal(tickets[0].service, "krbtgt/EXAMPLE.COM@EXAMPLE.COM")
    testbase.assert_equal(tickets[0].starts, "04/27/26 09:00:01")
    testbase.assert_equal(tickets[0].expires, "04/27/26 19:00:01")
    testbase.assert_equal(tickets[0].renewable_until, "05/04/26 09:00:01")
    return


def test_klist_returns_empty_when_no_cache():
    """
        klist() returns an empty list when ``klist`` exits non-zero
        (no credential cache).
    """
    runner = _MockRunner()
    runner.results[("klist",)] = RunResult(
        exit_code=1, stdout="", stderr="No credentials cache found\n",
        command=["klist"],
    )
    im = IdentityManager(runner, IdentityManagerConfig())
    tickets = im.klist()
    testbase.assert_equal(len(tickets), 0)
    return


def test_kdestroy_default_no_flag():
    """
        kdestroy() with default args dispatches just ``kdestroy``.
    """
    runner = _MockRunner()
    im = IdentityManager(runner, IdentityManagerConfig())
    result = im.kdestroy()
    testbase.assert_equal(result.status, IdentityActionStatus.SUCCESS)
    testbase.assert_equal(runner.calls[-1]["args"], ["kdestroy"])
    return


def test_kdestroy_all_caches_appends_dash_a():
    """
        kdestroy(all_caches=True) appends -A to the argv.
    """
    runner = _MockRunner()
    im = IdentityManager(runner, IdentityManagerConfig())
    result = im.kdestroy(all_caches=True)
    testbase.assert_equal(result.status, IdentityActionStatus.SUCCESS)
    testbase.assert_equal(runner.calls[-1]["args"], ["kdestroy", "-A"])
    return


# ----------------------------------------------------------------------
# NSS ordering seam
# ----------------------------------------------------------------------


def test_ensure_nss_ordering_delegates_to_authentication_manager():
    """
        ensure_nss_ordering() delegates each database to the injected
        AuthenticationManager.
    """
    runner = _MockRunner()
    auth = _MockAuthenticationManager()
    im = IdentityManager(
        runner,
        IdentityManagerConfig(),
        authentication_manager=auth,
    )
    result = im.ensure_nss_ordering({
        "passwd": ["files", "sss"],
        "group":  ["files", "sss"],
    })
    testbase.assert_equal(result.status, IdentityActionStatus.SUCCESS)
    testbase.assert_equal(len(auth.calls), 2)
    testbase.assert_equal(auth.calls[0]["db"], "passwd")
    testbase.assert_equal(auth.calls[0]["sources"], ["files", "sss"])
    testbase.assert_equal(auth.calls[1]["db"], "group")
    return


def test_ensure_nss_ordering_failed_propagates():
    """
        ensure_nss_ordering() returns FAILED when any delegated call
        fails.
    """
    runner = _MockRunner()
    auth = _MockAuthenticationManager(
        next_result=_AuthResult(
            status=_AuthStatus.FAILED,
            detail="boom",
            exit_code=2,
            stdout="",
            stderr="permission denied",
            command=["tee", "/etc/nsswitch.conf"],
        ),
    )
    im = IdentityManager(
        runner,
        IdentityManagerConfig(),
        authentication_manager=auth,
    )
    result = im.ensure_nss_ordering({"passwd": ["files", "sss"]})
    testbase.assert_equal(result.status, IdentityActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 2)
    testbase.assert_equal(result.stderr, "permission denied")
    return


# ----------------------------------------------------------------------
# Dry-run sweep across mutating verbs
# ----------------------------------------------------------------------


def test_dry_run_sweep_blocks_mutations():
    """
        With dry_run=True, no mutating verb dispatches a runner
        command for the realm join / leave / kinit / kdestroy /
        invalidate_cache code paths.
    """
    runner = _MockRunner()
    im = IdentityManager(runner, IdentityManagerConfig(dry_run=True))

    cred = _Cred(username="admin", password="x")
    join_result = im.join("example.com", credential=cred)
    leave_result = im.leave("example.com")
    kinit_result = im.kinit("alice@EXAMPLE.COM", credential=cred)
    kdestroy_result = im.kdestroy()
    cache_result = im.invalidate_cache()

    testbase.assert_equal(join_result.status, IdentityActionStatus.DRY_RUN)
    testbase.assert_equal(leave_result.status, IdentityActionStatus.DRY_RUN)
    testbase.assert_equal(kinit_result.status, IdentityActionStatus.DRY_RUN)
    testbase.assert_equal(kdestroy_result.status, IdentityActionStatus.DRY_RUN)
    testbase.assert_equal(cache_result.status, IdentityActionStatus.DRY_RUN)
    testbase.assert_equal(len(runner.calls), 0)
    return


# ----------------------------------------------------------------------
# Failure-mapping sweep
# ----------------------------------------------------------------------


def test_failed_dispatch_preserves_exit_code_and_stderr():
    """
        When the runner returns a non-zero exit code the result
        carries FAILED with the original exit_code and stderr.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=4, stdout="", stderr="kerberos error",
        command=["sss_cache"],
    )
    im = IdentityManager(runner, IdentityManagerConfig())
    result = im.invalidate_cache()
    testbase.assert_equal(result.status, IdentityActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 4)
    testbase.assert_equal(result.stderr, "kerberos error")
    return
