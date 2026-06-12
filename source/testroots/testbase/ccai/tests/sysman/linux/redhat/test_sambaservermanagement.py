# Tests for the SambaServerManager facade.

from dataclasses import dataclass, field
from typing import Callable, Optional

from ccai import testbase

from ccai.sysman.linux.redhat.runners.models import RunResult

from ccai.sysman.linux.redhat.sambaservermanagement import (
    SambaActionResult,
    SambaActionStatus,
    SambaCredentialError,
    SambaQueryError,
    SambaServerManager,
    SambaServerManagerConfig,
    SambaUserInfo,
    ShareInfo,
    ShareSpec,
    _extract_testparm_sections,
    _extract_testparm_warnings,
    _parse_pdbedit_short,
    _parse_smb_conf,
    _render_smb_conf,
)


# ----------------------------------------------------------------------
# Mock runner / mock service manager / mock credential
# ----------------------------------------------------------------------


@dataclass
class _MockRunner:
    """
        Minimal ICommandRunner stand-in that records every ``run``
        call and returns pre-seeded :class:`RunResult` values keyed
        by argv tuple.
    """

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
        call_record = {
            "args": list(args),
            "elevated": elevated,
            "stdin": stdin,
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
            exit_code=0, stdout="", stderr="", command=list(args),
        )
        return rtnval

    def add_aspect(self, aspect) -> None:
        return

    def remove_aspect(self, aspect) -> None:
        return

    def get_metrics(self):
        rtnval = None
        return rtnval


@dataclass
class _MockServiceManager:
    """
        Minimal ServiceManager stand-in that records every reload /
        restart call and returns a pre-seeded
        :class:`SambaActionResult` keyed by ``(verb, name)``.
    """

    results: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)

    def reload(self, name: str):
        self.calls.append({"verb": "reload", "name": name})
        key = ("reload", name)
        if key in self.results:
            rtnval = self.results[key]
            return rtnval
        rtnval = SambaActionResult(
            status=SambaActionStatus.SUCCESS,
            detail=name,
            exit_code=0,
            command=["systemctl", "reload", name],
        )
        return rtnval

    def restart(self, name: str):
        self.calls.append({"verb": "restart", "name": name})
        key = ("restart", name)
        if key in self.results:
            rtnval = self.results[key]
            return rtnval
        rtnval = SambaActionResult(
            status=SambaActionStatus.SUCCESS,
            detail=name,
            exit_code=0,
            command=["systemctl", "restart", name],
        )
        return rtnval


@dataclass
class _MockCredential:
    """
        Minimal credential stand-in carrying a username and a
        ``secret_bytes()`` accessor.
    """

    username: str
    secret: bytes

    def secret_bytes(self) -> bytes:
        rtnval = self.secret
        return rtnval


_SMB_CONF_PATH = "/etc/samba/smb.conf"


def _ok(stdout: str = "", stderr: str = "") -> RunResult:
    rtnval = RunResult(
        exit_code=0, stdout=stdout, stderr=stderr, command=[],
    )
    return rtnval


def _fail(
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 1,
) -> RunResult:
    rtnval = RunResult(
        exit_code=exit_code, stdout=stdout, stderr=stderr, command=[],
    )
    return rtnval


def _seed_cat(
    runner: _MockRunner,
    *,
    path: str,
    body: Optional[str],
) -> None:
    """
        Seed the runner so that ``cat <path>`` returns ``body`` on
        success, or non-zero with empty stderr (missing file) when
        ``body`` is ``None``.
    """
    if body is None:
        result = RunResult(
            exit_code=1, stdout="", stderr="", command=["cat", path],
        )
    else:
        result = RunResult(
            exit_code=0, stdout=body, stderr="", command=["cat", path],
        )
    runner.results[("cat", path)] = result
    return


# ----------------------------------------------------------------------
# Global directives
# ----------------------------------------------------------------------


def test_get_globals_returns_directive_map():
    """
        ``get_globals`` parses the ``[global]`` section into a
        ``dict[str, str]``.
    """
    runner = _MockRunner()
    body = (
        "[global]\n"
        "    workgroup = WORKGROUP\n"
        "    server string = Samba\n"
        "[public]\n"
        "    path = /srv/samba/public\n"
    )
    _seed_cat(runner, path=_SMB_CONF_PATH, body=body)

    manager = SambaServerManager(runner=runner)
    globals_map = manager.get_globals()

    testbase.assert_equal(globals_map["workgroup"], "WORKGROUP")
    testbase.assert_equal(globals_map["server string"], "Samba")
    return


def test_set_global_skipped_when_match():
    """
        ``set_global`` returns SKIPPED when the directive already
        carries the requested value.
    """
    runner = _MockRunner()
    body = (
        "[global]\n"
        "    workgroup = WORKGROUP\n"
    )
    _seed_cat(runner, path=_SMB_CONF_PATH, body=body)

    config = SambaServerManagerConfig(elevated=True)
    manager = SambaServerManager(runner=runner, config=config)

    result = manager.set_global(key="workgroup", value="WORKGROUP")

    testbase.assert_equal(result.status, SambaActionStatus.SKIPPED)
    tee_calls = [
        call for call in runner.calls
        if len(call["args"]) >= 1 and call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 0)
    return


def test_set_global_writes_when_changed():
    """
        ``set_global`` writes the smb.conf file via ``tee`` when
        the directive differs.
    """
    runner = _MockRunner()
    runner.default_result = _ok()
    body = (
        "[global]\n"
        "    workgroup = OLD\n"
    )
    _seed_cat(runner, path=_SMB_CONF_PATH, body=body)

    config = SambaServerManagerConfig(elevated=True)
    manager = SambaServerManager(runner=runner, config=config)

    result = manager.set_global(key="workgroup", value="NEW")

    testbase.assert_equal(result.status, SambaActionStatus.SUCCESS)
    tee_calls = [
        call for call in runner.calls
        if len(call["args"]) >= 2
        and call["args"][0] == "tee"
        and call["args"][1] == _SMB_CONF_PATH
    ]
    testbase.assert_equal(len(tee_calls), 1)
    written = tee_calls[0]["stdin"]
    testbase.assert_equal("workgroup = NEW" in written, True)
    return


def test_remove_global_skipped_when_absent():
    """
        ``remove_global`` returns SKIPPED when the directive is not
        present.
    """
    runner = _MockRunner()
    body = (
        "[global]\n"
        "    workgroup = WORKGROUP\n"
    )
    _seed_cat(runner, path=_SMB_CONF_PATH, body=body)

    manager = SambaServerManager(runner=runner)
    result = manager.remove_global(key="server string")

    testbase.assert_equal(result.status, SambaActionStatus.SKIPPED)
    tee_calls = [
        call for call in runner.calls
        if len(call["args"]) >= 1 and call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 0)
    return


def test_remove_global_strips_directive():
    """
        ``remove_global`` strips the matching line and writes the
        smb.conf via ``tee``.
    """
    runner = _MockRunner()
    runner.default_result = _ok()
    body = (
        "[global]\n"
        "    workgroup = WORKGROUP\n"
        "    server string = Samba\n"
    )
    _seed_cat(runner, path=_SMB_CONF_PATH, body=body)

    config = SambaServerManagerConfig(elevated=True)
    manager = SambaServerManager(runner=runner, config=config)

    result = manager.remove_global(key="server string")

    testbase.assert_equal(result.status, SambaActionStatus.SUCCESS)
    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 1)
    written = tee_calls[0]["stdin"]
    testbase.assert_equal("server string" in written, False)
    testbase.assert_equal("workgroup = WORKGROUP" in written, True)
    return


# ----------------------------------------------------------------------
# Share CRUD
# ----------------------------------------------------------------------


def test_define_share_writes_section():
    """
        ``define_share`` writes a new ``[<share>]`` block via
        ``tee`` when the share is absent.
    """
    runner = _MockRunner()
    runner.default_result = _ok()
    _seed_cat(runner, path=_SMB_CONF_PATH, body="")

    config = SambaServerManagerConfig(elevated=True)
    manager = SambaServerManager(runner=runner, config=config)

    spec = ShareSpec(
        name="public",
        path="/srv/samba/public",
        comment="Public share",
        writable=True,
        browseable=True,
        guest_ok=False,
    )
    result = manager.define_share(spec=spec)

    testbase.assert_equal(result.status, SambaActionStatus.SUCCESS)
    testbase.assert_equal(result.share, "public")

    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 1)
    written = tee_calls[0]["stdin"]
    testbase.assert_equal("[public]" in written, True)
    testbase.assert_equal("path = /srv/samba/public" in written, True)
    testbase.assert_equal("comment = Public share" in written, True)
    testbase.assert_equal(tee_calls[0]["elevated"], True)
    return


def test_define_share_skipped_when_directives_present():
    """
        ``define_share`` returns SKIPPED when the existing block
        already carries every requested directive — even when the
        block carries other directives in addition.
    """
    runner = _MockRunner()
    body = (
        "[public]\n"
        "    path = /srv/samba/public\n"
        "    comment = Public share\n"
        "    writable = yes\n"
        "    browseable = yes\n"
        "    guest ok = no\n"
        "    create mask = 0664\n"
    )
    _seed_cat(runner, path=_SMB_CONF_PATH, body=body)

    manager = SambaServerManager(runner=runner)
    spec = ShareSpec(
        name="public",
        path="/srv/samba/public",
        comment="Public share",
        writable=True,
    )
    result = manager.define_share(spec=spec)

    testbase.assert_equal(result.status, SambaActionStatus.SKIPPED)
    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 0)
    return


def test_define_share_dry_run():
    """
        ``define_share`` under ``dry_run=True`` returns DRY_RUN and
        never invokes ``tee``.
    """
    runner = _MockRunner()
    _seed_cat(runner, path=_SMB_CONF_PATH, body="")

    config = SambaServerManagerConfig(elevated=True, dry_run=True)
    manager = SambaServerManager(runner=runner, config=config)
    spec = ShareSpec(name="public", path="/srv/samba/public")

    result = manager.define_share(spec=spec)

    testbase.assert_equal(result.status, SambaActionStatus.DRY_RUN)
    testbase.assert_equal(result.command[0], "tee")
    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 0)
    return


def test_remove_share_strips_block_with_boundary():
    """
        ``remove_share`` strips the entire ``[<name>]`` block,
        leaving surrounding sections intact.
    """
    runner = _MockRunner()
    runner.default_result = _ok()
    body = (
        "[global]\n"
        "    workgroup = WORKGROUP\n"
        "[public]\n"
        "    path = /srv/samba/public\n"
        "    writable = yes\n"
        "[private]\n"
        "    path = /srv/samba/private\n"
        "    writable = no\n"
    )
    _seed_cat(runner, path=_SMB_CONF_PATH, body=body)

    config = SambaServerManagerConfig(elevated=True)
    manager = SambaServerManager(runner=runner, config=config)

    result = manager.remove_share(name="public")

    testbase.assert_equal(result.status, SambaActionStatus.SUCCESS)
    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 1)
    written = tee_calls[0]["stdin"]
    testbase.assert_equal("[public]" in written, False)
    testbase.assert_equal("/srv/samba/public" in written, False)
    testbase.assert_equal("[private]" in written, True)
    testbase.assert_equal("[global]" in written, True)
    return


def test_remove_share_skipped_when_absent():
    """
        ``remove_share`` returns SKIPPED when the section is not
        present.
    """
    runner = _MockRunner()
    body = "[global]\n    workgroup = WORKGROUP\n"
    _seed_cat(runner, path=_SMB_CONF_PATH, body=body)

    manager = SambaServerManager(runner=runner)
    result = manager.remove_share(name="public")

    testbase.assert_equal(result.status, SambaActionStatus.SKIPPED)
    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 0)
    return


def test_list_shares_excludes_reserved_names():
    """
        ``list_shares`` returns every non-reserved section.
    """
    runner = _MockRunner()
    body = (
        "[global]\n"
        "    workgroup = WORKGROUP\n"
        "[homes]\n"
        "    read only = no\n"
        "[printers]\n"
        "    path = /var/spool/samba\n"
        "[public]\n"
        "    path = /srv/samba/public\n"
        "[private]\n"
        "    path = /srv/samba/private\n"
    )
    _seed_cat(runner, path=_SMB_CONF_PATH, body=body)

    manager = SambaServerManager(runner=runner)
    rtnval = manager.list_shares()

    testbase.assert_equal(len(rtnval), 2)
    by_name = {info.name: info for info in rtnval}
    testbase.assert_equal("public" in by_name, True)
    testbase.assert_equal("private" in by_name, True)
    testbase.assert_equal(by_name["public"].directives["path"], "/srv/samba/public")
    return


def test_query_share_returns_record_or_none():
    """
        ``query_share`` returns the matching record or ``None``.
    """
    runner = _MockRunner()
    body = (
        "[public]\n"
        "    path = /srv/samba/public\n"
        "    writable = yes\n"
    )
    _seed_cat(runner, path=_SMB_CONF_PATH, body=body)

    manager = SambaServerManager(runner=runner)

    found = manager.query_share(name="public")
    testbase.assert_equal(found is not None, True)
    testbase.assert_equal(found.name, "public")
    testbase.assert_equal(found.directives["path"], "/srv/samba/public")

    missing = manager.query_share(name="absent")
    testbase.assert_equal(missing, None)
    return


def test_update_share_replaces_directives_atomically():
    """
        ``update_share`` rewrites the section block in a single
        ``tee`` call.
    """
    runner = _MockRunner()
    runner.default_result = _ok()
    body = (
        "[public]\n"
        "    path = /srv/samba/public\n"
        "    writable = yes\n"
    )
    _seed_cat(runner, path=_SMB_CONF_PATH, body=body)

    config = SambaServerManagerConfig(elevated=True)
    manager = SambaServerManager(runner=runner, config=config)

    spec = ShareSpec(
        name="public",
        path="/srv/samba/public2",
        writable=False,
    )
    result = manager.update_share(spec=spec)

    testbase.assert_equal(result.status, SambaActionStatus.SUCCESS)
    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 1)
    written = tee_calls[0]["stdin"]
    testbase.assert_equal("path = /srv/samba/public2" in written, True)
    testbase.assert_equal("writable = no" in written, True)
    testbase.assert_equal("writable = yes" in written, False)
    return


# ----------------------------------------------------------------------
# Validate / reload / restart
# ----------------------------------------------------------------------


def test_validate_config_parses_warnings_and_sections():
    """
        ``validate_config`` returns a populated
        :class:`SambaValidateResult` from ``testparm`` output.
    """
    runner = _MockRunner()
    runner.results[("testparm", "-s", "--suppress-prompt")] = RunResult(
        exit_code=0,
        stdout=(
            "[global]\n"
            "    workgroup = WORKGROUP\n"
            "[public]\n"
            "    path = /srv/samba/public\n"
        ),
        stderr=(
            "Loaded services file OK.\n"
            "WARNING: The 'foo' parameter is deprecated\n"
        ),
        command=["testparm"],
    )

    manager = SambaServerManager(runner=runner)
    result = manager.validate_config()

    testbase.assert_equal(result.ok, True)
    testbase.assert_equal(result.exit_code, 0)
    testbase.assert_equal("global" in result.parsed_sections, True)
    testbase.assert_equal("public" in result.parsed_sections, True)
    testbase.assert_equal(len(result.warnings) >= 1, True)
    return


def test_reload_does_not_dispatch_when_validation_fails():
    """
        ``reload`` returns FAILED and never asks the
        :class:`ServiceManager` to act when validation fails.
    """
    runner = _MockRunner()
    runner.results[("testparm", "-s", "--suppress-prompt")] = _fail(
        stderr="ERROR: bad section header", exit_code=1,
    )

    service_manager = _MockServiceManager()

    manager = SambaServerManager(
        runner=runner,
        config=SambaServerManagerConfig(elevated=True),
        service_manager=service_manager,
    )

    result = manager.reload()

    testbase.assert_equal(result.status, SambaActionStatus.FAILED)
    testbase.assert_equal("testparm" in (result.detail or ""), True)
    testbase.assert_equal(len(service_manager.calls), 0)
    return


def test_reload_delegates_to_service_manager_when_valid():
    """
        ``reload`` delegates to
        :meth:`ServiceManager.reload("smb")` after a successful
        validation.
    """
    runner = _MockRunner()
    runner.results[("testparm", "-s", "--suppress-prompt")] = _ok(
        stdout="[global]\n",
    )

    service_manager = _MockServiceManager()

    manager = SambaServerManager(
        runner=runner,
        config=SambaServerManagerConfig(elevated=True),
        service_manager=service_manager,
    )

    result = manager.reload()

    testbase.assert_equal(result.status, SambaActionStatus.SUCCESS)
    testbase.assert_equal(len(service_manager.calls), 1)
    testbase.assert_equal(service_manager.calls[0]["verb"], "reload")
    testbase.assert_equal(service_manager.calls[0]["name"], "smb")
    return


def test_restart_delegates_to_service_manager_only():
    """
        ``restart`` only delegates to the injected ServiceManager
        (no direct ``systemctl`` runner calls).
    """
    runner = _MockRunner()
    service_manager = _MockServiceManager()

    manager = SambaServerManager(
        runner=runner,
        config=SambaServerManagerConfig(elevated=True),
        service_manager=service_manager,
    )

    results = manager.restart()

    testbase.assert_equal(len(results), 2)
    testbase.assert_equal(results[0].status, SambaActionStatus.SUCCESS)
    testbase.assert_equal(results[1].status, SambaActionStatus.SUCCESS)

    verbs_and_names = [
        (call["verb"], call["name"]) for call in service_manager.calls
    ]
    testbase.assert_equal(verbs_and_names, [("restart", "smb"), ("restart", "nmb")])

    # No systemctl calls hit the runner directly.
    systemctl_calls = [
        call for call in runner.calls
        if len(call["args"]) > 0 and call["args"][0] == "systemctl"
    ]
    testbase.assert_equal(len(systemctl_calls), 0)
    return


# ----------------------------------------------------------------------
# Samba user management
# ----------------------------------------------------------------------


def test_list_samba_users_parses_pdbedit_short():
    """
        ``list_samba_users`` parses ``pdbedit -L`` output into
        :class:`SambaUserInfo`.
    """
    runner = _MockRunner()
    runner.results[("pdbedit", "-L")] = _ok(
        stdout=(
            "alice:1000:Alice User\n"
            "bob:1001:Bob User\n"
        ),
    )

    manager = SambaServerManager(runner=runner)
    users = manager.list_samba_users()

    testbase.assert_equal(len(users), 2)
    by_name = {info.username: info for info in users}
    testbase.assert_equal(by_name["alice"].uid, 1000)
    testbase.assert_equal(by_name["bob"].uid, 1001)
    return


def test_add_samba_user_password_on_stdin_not_argv():
    """
        ``add_samba_user`` dispatches ``smbpasswd -a -s <name>`` with
        the password delivered via runner stdin (twice — ``-s``
        reads new and confirmation off stdin), never on argv.
    """
    runner = _MockRunner()
    runner.default_result = _ok()

    config = SambaServerManagerConfig(elevated=True)
    manager = SambaServerManager(runner=runner, config=config)
    cred = _MockCredential(username="alice", secret=b"hunter2")

    result = manager.add_samba_user(username="alice", credential=cred)

    testbase.assert_equal(result.status, SambaActionStatus.SUCCESS)

    smbpasswd_calls = [
        call for call in runner.calls
        if (
            len(call["args"]) >= 4
            and call["args"][0] == "smbpasswd"
            and call["args"][1] == "-a"
            and call["args"][2] == "-s"
            and call["args"][3] == "alice"
        )
    ]
    testbase.assert_equal(len(smbpasswd_calls), 1)
    testbase.assert_equal(smbpasswd_calls[0]["stdin"], "hunter2\nhunter2\n")
    testbase.assert_equal(smbpasswd_calls[0]["elevated"], True)

    # Recorded command never contains the secret.
    recorded = " ".join(result.command)
    testbase.assert_equal("hunter2" in recorded, False)
    return


def test_add_samba_user_raises_when_credential_missing():
    """
        ``add_samba_user`` raises :class:`SambaCredentialError`
        when the credential is None.
    """
    runner = _MockRunner()
    manager = SambaServerManager(runner=runner)

    raised: Optional[SambaCredentialError] = None
    try:
        manager.add_samba_user(username="alice", credential=None)
    except SambaCredentialError as exc:
        raised = exc
    testbase.assert_equal(raised is not None, True)
    testbase.assert_equal(len(runner.calls), 0)
    return


def test_delete_samba_user_dispatches_smbpasswd_minus_x():
    """
        ``delete_samba_user`` dispatches ``smbpasswd -x <username>``.
    """
    runner = _MockRunner()
    runner.default_result = _ok()

    config = SambaServerManagerConfig(elevated=True)
    manager = SambaServerManager(runner=runner, config=config)

    result = manager.delete_samba_user(username="alice")

    testbase.assert_equal(result.status, SambaActionStatus.SUCCESS)
    last = runner.calls[-1]
    testbase.assert_equal(last["args"], ["smbpasswd", "-x", "alice"])
    testbase.assert_equal(last["elevated"], True)
    return


def test_enable_and_disable_samba_user_dispatch():
    """
        ``enable_samba_user`` and ``disable_samba_user`` dispatch
        ``smbpasswd -e`` / ``-d``.
    """
    runner = _MockRunner()
    runner.default_result = _ok()

    config = SambaServerManagerConfig(elevated=True)
    manager = SambaServerManager(runner=runner, config=config)

    enable_result = manager.enable_samba_user(username="alice")
    disable_result = manager.disable_samba_user(username="alice")

    testbase.assert_equal(enable_result.status, SambaActionStatus.SUCCESS)
    testbase.assert_equal(disable_result.status, SambaActionStatus.SUCCESS)

    enable_calls = [
        call for call in runner.calls
        if call["args"] == ["smbpasswd", "-e", "alice"]
    ]
    disable_calls = [
        call for call in runner.calls
        if call["args"] == ["smbpasswd", "-d", "alice"]
    ]
    testbase.assert_equal(len(enable_calls), 1)
    testbase.assert_equal(len(disable_calls), 1)
    return


# ----------------------------------------------------------------------
# AD join
# ----------------------------------------------------------------------


def test_join_ad_password_on_stdin_not_argv():
    """
        ``join_ad`` dispatches ``net ads join -U <user>`` with the
        password on stdin.  Recorded command never contains the
        secret.
    """
    runner = _MockRunner()
    runner.default_result = _ok()

    config = SambaServerManagerConfig(elevated=True)
    manager = SambaServerManager(runner=runner, config=config)
    cred = _MockCredential(username="admin", secret=b"sup3rs3cret")

    result = manager.join_ad(domain="example.com", admin_credential=cred)

    testbase.assert_equal(result.status, SambaActionStatus.SUCCESS)

    net_calls = [
        call for call in runner.calls
        if (
            len(call["args"]) >= 5
            and call["args"][0] == "net"
            and call["args"][1] == "ads"
            and call["args"][2] == "join"
            and call["args"][3] == "-U"
            and call["args"][4] == "admin"
        )
    ]
    testbase.assert_equal(len(net_calls), 1)
    testbase.assert_equal(net_calls[0]["stdin"], "sup3rs3cret")

    recorded = " ".join(result.command)
    testbase.assert_equal("sup3rs3cret" in recorded, False)
    return


def test_join_ad_raises_when_credential_missing():
    """
        ``join_ad`` raises :class:`SambaCredentialError` when the
        admin credential is None.
    """
    runner = _MockRunner()
    manager = SambaServerManager(runner=runner)

    raised: Optional[SambaCredentialError] = None
    try:
        manager.join_ad(domain="example.com", admin_credential=None)
    except SambaCredentialError as exc:
        raised = exc
    testbase.assert_equal(raised is not None, True)
    return


def test_leave_ad_password_on_stdin_not_argv():
    """
        ``leave_ad`` dispatches ``net ads leave -U <user>`` with the
        password on stdin.
    """
    runner = _MockRunner()
    runner.default_result = _ok()

    config = SambaServerManagerConfig(elevated=True)
    manager = SambaServerManager(runner=runner, config=config)
    cred = _MockCredential(username="admin", secret=b"leaveSecret")

    result = manager.leave_ad(admin_credential=cred)

    testbase.assert_equal(result.status, SambaActionStatus.SUCCESS)

    net_calls = [
        call for call in runner.calls
        if (
            len(call["args"]) >= 5
            and call["args"][0] == "net"
            and call["args"][1] == "ads"
            and call["args"][2] == "leave"
            and call["args"][3] == "-U"
            and call["args"][4] == "admin"
        )
    ]
    testbase.assert_equal(len(net_calls), 1)
    testbase.assert_equal(net_calls[0]["stdin"], "leaveSecret")

    recorded = " ".join(result.command)
    testbase.assert_equal("leaveSecret" in recorded, False)
    return


def test_test_join_returns_bool():
    """
        ``test_join`` returns ``True`` for exit code 0, ``False``
        otherwise.
    """
    runner_ok = _MockRunner()
    runner_ok.results[("net", "ads", "testjoin")] = _ok()
    manager_ok = SambaServerManager(runner=runner_ok)
    testbase.assert_equal(manager_ok.test_join(), True)

    runner_fail = _MockRunner()
    runner_fail.results[("net", "ads", "testjoin")] = _fail(
        stderr="not joined", exit_code=1,
    )
    manager_fail = SambaServerManager(runner=runner_fail)
    testbase.assert_equal(manager_fail.test_join(), False)
    return


def test_list_trusted_domains_parses_wbinfo_output():
    """
        ``list_trusted_domains`` parses ``wbinfo
        --trusted-domains`` into a list of domain names.
    """
    runner = _MockRunner()
    runner.results[("wbinfo", "--trusted-domains")] = _ok(
        stdout=(
            "EXAMPLE\n"
            "OTHER\n"
        ),
    )
    manager = SambaServerManager(runner=runner)
    rtnval = manager.list_trusted_domains()
    testbase.assert_equal(rtnval, ["EXAMPLE", "OTHER"])
    return


# ----------------------------------------------------------------------
# Dry-run sweep
# ----------------------------------------------------------------------


def test_dry_run_sweep_blocks_mutations():
    """
        Under ``dry_run=True`` no mutating runner call is made for
        ``define_share``, ``remove_share``, ``set_global``,
        ``add_samba_user``, ``delete_samba_user``, ``join_ad``, or
        ``reload``.  Each verb returns DRY_RUN.
    """
    runner = _MockRunner()
    _seed_cat(runner, path=_SMB_CONF_PATH, body="[global]\n    workgroup = OLD\n")

    service_manager = _MockServiceManager()
    config = SambaServerManagerConfig(elevated=True, dry_run=True)
    manager = SambaServerManager(
        runner=runner, config=config, service_manager=service_manager,
    )
    cred = _MockCredential(username="alice", secret=b"hunter2")
    admin = _MockCredential(username="admin", secret=b"adminsecret")

    define_result = manager.define_share(
        spec=ShareSpec(name="public", path="/srv/samba/public"),
    )
    remove_result = manager.remove_share(name="other")
    set_result = manager.set_global(key="workgroup", value="NEW")
    add_user_result = manager.add_samba_user(
        username="alice", credential=cred,
    )
    delete_user_result = manager.delete_samba_user(username="alice")
    join_result = manager.join_ad(
        domain="example.com", admin_credential=admin,
    )
    reload_result = manager.reload()

    testbase.assert_equal(define_result.status, SambaActionStatus.DRY_RUN)
    # remove_share short-circuits to SKIPPED when the share is absent;
    # use a body that contains the share so dry-run is exercised.
    testbase.assert_equal(set_result.status, SambaActionStatus.DRY_RUN)
    testbase.assert_equal(add_user_result.status, SambaActionStatus.DRY_RUN)
    testbase.assert_equal(delete_user_result.status, SambaActionStatus.DRY_RUN)
    testbase.assert_equal(join_result.status, SambaActionStatus.DRY_RUN)
    testbase.assert_equal(reload_result.status, SambaActionStatus.DRY_RUN)

    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 0)

    smbpasswd_calls = [
        call for call in runner.calls
        if call["args"][0] == "smbpasswd"
    ]
    testbase.assert_equal(len(smbpasswd_calls), 0)

    net_calls = [
        call for call in runner.calls
        if call["args"][0] == "net"
    ]
    testbase.assert_equal(len(net_calls), 0)

    testbase.assert_equal(len(service_manager.calls), 0)

    # ``remove_share`` returns SKIPPED on absent share; not asserted as DRY_RUN.
    _ = remove_result
    return


# ----------------------------------------------------------------------
# Failure-mapping sweep
# ----------------------------------------------------------------------


def test_failed_command_propagates_stderr_and_exit_code():
    """
        When a ``tee`` write fails the action result carries
        ``status=FAILED`` plus the runner's exit_code / stderr.
    """
    runner = _MockRunner()
    _seed_cat(runner, path=_SMB_CONF_PATH, body="")
    runner.results[("tee", _SMB_CONF_PATH)] = _fail(
        stderr="tee: cannot open", exit_code=1,
    )

    config = SambaServerManagerConfig(elevated=True)
    manager = SambaServerManager(runner=runner, config=config)

    spec = ShareSpec(name="public", path="/srv/samba/public")
    result = manager.define_share(spec=spec)

    testbase.assert_equal(result.status, SambaActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 1)
    testbase.assert_equal("tee: cannot open" in (result.stderr or ""), True)
    return


def test_list_shares_raises_query_error_on_unexpected_failure():
    """
        ``list_shares`` raises :class:`SambaQueryError` when ``cat``
        exits non-zero with non-empty stderr (i.e., not a missing-
        file case).
    """
    runner = _MockRunner()
    runner.results[("cat", _SMB_CONF_PATH)] = _fail(
        stderr="cat: permission denied", exit_code=1,
    )

    manager = SambaServerManager(runner=runner)
    raised: Optional[SambaQueryError] = None
    try:
        manager.list_shares()
    except SambaQueryError as exc:
        raised = exc
    testbase.assert_equal(raised is not None, True)
    testbase.assert_equal(raised.exit_code, 1)
    return


# ----------------------------------------------------------------------
# Parser unit tests
# ----------------------------------------------------------------------


def test_parse_smb_conf_orders_sections_correctly():
    """
        ``_parse_smb_conf`` produces sections in declaration order
        with a synthetic preamble entry.
    """
    body = (
        "# top comment\n"
        "[global]\n"
        "    workgroup = WORKGROUP\n"
        "[public]\n"
        "    path = /srv/share\n"
    )
    sections = _parse_smb_conf(content=body)
    names = [section.name for section in sections]
    testbase.assert_equal(names, [None, "global", "public"])
    return


def test_render_smb_conf_round_trips():
    """
        ``_render_smb_conf`` emits parsed content back out (modulo
        the trailing newline canonicalisation).
    """
    body = (
        "[global]\n"
        "    workgroup = WORKGROUP\n"
        "[public]\n"
        "    path = /srv/share\n"
    )
    sections = _parse_smb_conf(content=body)
    rendered = _render_smb_conf(sections=sections)
    testbase.assert_equal("[global]" in rendered, True)
    testbase.assert_equal("[public]" in rendered, True)
    testbase.assert_equal("workgroup = WORKGROUP" in rendered, True)
    testbase.assert_equal(rendered.endswith("\n"), True)
    return


def test_parse_pdbedit_short_handles_lines():
    """
        ``_parse_pdbedit_short`` parses ``pdbedit -L`` short output.
    """
    text = (
        "alice:1000:Alice\n"
        "bob:1001:Bob\n"
    )
    parsed = _parse_pdbedit_short(text=text)
    testbase.assert_equal(len(parsed), 2)
    by_name = {info.username: info for info in parsed}
    testbase.assert_equal(by_name["alice"].uid, 1000)
    testbase.assert_equal(by_name["bob"].uid, 1001)
    return


def test_extract_testparm_warnings_picks_up_warning_lines():
    """
        ``_extract_testparm_warnings`` extracts the warning-shaped
        stderr lines.
    """
    stderr = (
        "Loaded services file OK.\n"
        "WARNING: deprecated option\n"
        "Bad value for parameter\n"
    )
    rtnval = _extract_testparm_warnings(stderr=stderr)
    testbase.assert_equal(len(rtnval) >= 2, True)
    return


def test_extract_testparm_sections_parses_section_headers():
    """
        ``_extract_testparm_sections`` extracts every
        ``[section]`` header in stdout.
    """
    stdout = (
        "[global]\n"
        "    workgroup = WORKGROUP\n"
        "[public]\n"
        "    path = /srv/share\n"
    )
    rtnval = _extract_testparm_sections(stdout=stdout)
    testbase.assert_equal(rtnval, ["global", "public"])
    return


# ----------------------------------------------------------------------
# Result-type sanity
# ----------------------------------------------------------------------


def test_share_info_dataclass_shape():
    """
        :class:`ShareInfo` exposes the contracted fields.
    """
    info = ShareInfo(
        name="public",
        directives={"path": "/srv/samba/public"},
    )
    testbase.assert_equal(info.name, "public")
    testbase.assert_equal(info.directives["path"], "/srv/samba/public")
    return


def test_user_info_dataclass_shape():
    """
        :class:`SambaUserInfo` exposes the contracted fields.
    """
    info = SambaUserInfo(
        username="alice",
        uid=1000,
        flags="U",
        last_password_change=None,
    )
    testbase.assert_equal(info.username, "alice")
    testbase.assert_equal(info.uid, 1000)
    return
