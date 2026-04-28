# Tests for the MailTransportManager facade.

from dataclasses import dataclass, field
from typing import Callable, Optional

from automojo import testbase

from automojo.sysman.linux.redhat.featurebase import (
    ActionStatus,
    FeatureActionResult,
)
from automojo.sysman.linux.redhat.mailtransportmanagement import (
    MailActionResult,
    MailActionStatus,
    MailTransportManager,
    MailTransportManagerConfig,
    MasterCfService,
    _parse_aliases_text,
    _parse_postconf_output,
    _parse_postqueue_output,
)
from automojo.sysman.linux.redhat.runners.models import RunResult


# ----------------------------------------------------------------------
# Mock collaborators
# ----------------------------------------------------------------------


@dataclass
class _MockRunner:

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
        self.calls.append(
            {"args": list(args), "elevated": elevated, "stdin": stdin},
        )
        key = tuple(args)
        if key in self.results:
            return self.results[key]
        if self.default_result is not None:
            return self.default_result
        return RunResult(exit_code=0, stdout="", stderr="", command=list(args))

    def add_aspect(self, aspect) -> None:
        return

    def remove_aspect(self, aspect) -> None:
        return

    def get_metrics(self):
        return None


@dataclass
class _MockServiceManager:
    """
        Stand-in for the injected ServiceManager.  Records every
        reload/restart call and returns a canned FeatureActionResult.
    """

    reload_calls: list = field(default_factory=list)
    restart_calls: list = field(default_factory=list)
    reload_result: Optional[FeatureActionResult] = None
    restart_result: Optional[FeatureActionResult] = None

    def reload(self, name: str) -> FeatureActionResult:
        self.reload_calls.append(name)
        if self.reload_result is not None:
            return self.reload_result
        return FeatureActionResult(status=ActionStatus.SUCCESS)

    def restart(self, name: str) -> FeatureActionResult:
        self.restart_calls.append(name)
        if self.restart_result is not None:
            return self.restart_result
        return FeatureActionResult(status=ActionStatus.SUCCESS)


def _ok(stdout: str = "", stderr: str = "") -> RunResult:
    """
        Build a successful RunResult.
    """
    return RunResult(
        exit_code=0, stdout=stdout, stderr=stderr, command=[],
    )


def _fail(
    stdout: str = "", stderr: str = "", exit_code: int = 1,
) -> RunResult:
    """
        Build a failure RunResult.
    """
    return RunResult(
        exit_code=exit_code, stdout=stdout, stderr=stderr, command=[],
    )


# ----------------------------------------------------------------------
# postconf parser tests
# ----------------------------------------------------------------------


def test_parse_postconf_output_extracts_directives():
    """
        _parse_postconf_output() splits each ``key = value`` line.
    """
    text = (
        "myhostname = mail.example.com\n"
        "mydomain = example.com\n"
        "# a comment\n"
        "\n"
        "relayhost = [smtp.relay.example.com]:587\n"
    )
    parsed = _parse_postconf_output(text=text)
    testbase.assert_equal(parsed["myhostname"], "mail.example.com")
    testbase.assert_equal(parsed["mydomain"], "example.com")
    testbase.assert_equal(parsed["relayhost"], "[smtp.relay.example.com]:587")
    testbase.assert_equal(len(parsed), 3)
    return


def test_parse_aliases_text_handles_continuations():
    """
        _parse_aliases_text() handles continuation lines and
        comma-separated targets.
    """
    text = (
        "# alias defaults\n"
        "postmaster: root\n"
        "root: alice, bob,\n"
        "    carol\n"
    )
    parsed = _parse_aliases_text(content=text)
    testbase.assert_equal(parsed["postmaster"], ["root"])
    testbase.assert_equal(parsed["root"], ["alice", "bob", "carol"])
    return


def test_parse_postqueue_output_parses_records():
    """
        _parse_postqueue_output() parses head + recipient lines into
        QueuedMessage records.
    """
    text = (
        "-Queue ID-  --Size-- ----Arrival Time---- -Sender/Recipient-------\n"
        "AAAA1111      1234 Mon May  6 12:34:56  sender@example.com\n"
        "                                         (host smtp.example.com refused)\n"
        "                                         user1@example.com\n"
        "\n"
        "BBBB2222*     5678 Tue May  7 11:22:33  other@example.com\n"
        "                                         user2@example.com\n"
        "\n"
        "-- 6 Kbytes in 2 Requests.\n"
    )
    parsed = _parse_postqueue_output(text=text)
    testbase.assert_equal(len(parsed), 2)
    testbase.assert_equal(parsed[0].queue_id, "AAAA1111")
    testbase.assert_equal(parsed[0].size_bytes, 1234)
    testbase.assert_equal(parsed[0].sender, "sender@example.com")
    testbase.assert_equal(
        parsed[0].last_error, "host smtp.example.com refused",
    )
    testbase.assert_equal(parsed[0].recipients, ["user1@example.com"])
    testbase.assert_equal(parsed[1].queue_id, "BBBB2222")
    testbase.assert_equal(parsed[1].state, "*")
    return


# ----------------------------------------------------------------------
# main.cf get/set/remove
# ----------------------------------------------------------------------


def test_get_main_directive_runs_postconf_h():
    """
        get_main_directive() invokes postconf -h and returns stripped
        stdout.
    """
    runner = _MockRunner()
    runner.results[("postconf", "-h", "myhostname")] = _ok(
        stdout="mail.example.com\n",
    )
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(),
        service_manager=_MockServiceManager(),
    )
    value = mgr.get_main_directive(key="myhostname")
    testbase.assert_equal(value, "mail.example.com")
    testbase.assert_equal(runner.calls[-1]["elevated"], False)
    return


def test_get_all_main_directives_returns_dict():
    """
        get_all_main_directives() returns a dict[str, str] from
        ``postconf`` (no arg) output.
    """
    runner = _MockRunner()
    runner.results[("postconf",)] = _ok(
        stdout="myhostname = mail.example.com\nmydomain = example.com\n",
    )
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(),
        service_manager=_MockServiceManager(),
    )
    parsed = mgr.get_all_main_directives()
    testbase.assert_equal(parsed["myhostname"], "mail.example.com")
    testbase.assert_equal(parsed["mydomain"], "example.com")
    return


def test_set_main_directive_dispatches_postconf_e():
    """
        set_main_directive() dispatches ``postconf -e key=value`` when
        the live value differs.
    """
    runner = _MockRunner(default_result=_ok())
    runner.results[("postconf", "-h", "myhostname")] = _ok(
        stdout="old.example.com\n",
    )
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(elevated=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.set_main_directive(key="myhostname", value="new.example.com")
    testbase.assert_equal(result.status, MailActionStatus.SUCCESS)
    set_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["postconf", "-e"]
    ]
    testbase.assert_equal(len(set_calls), 1)
    testbase.assert_equal(
        set_calls[0]["args"], ["postconf", "-e", "myhostname=new.example.com"],
    )
    testbase.assert_equal(set_calls[0]["elevated"], True)
    return


def test_set_main_directive_skipped_when_unchanged():
    """
        set_main_directive() returns SKIPPED when the live value
        already matches.
    """
    runner = _MockRunner()
    runner.results[("postconf", "-h", "myhostname")] = _ok(
        stdout="mail.example.com\n",
    )
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(),
        service_manager=_MockServiceManager(),
    )
    result = mgr.set_main_directive(
        key="myhostname", value="mail.example.com",
    )
    testbase.assert_equal(result.status, MailActionStatus.SKIPPED)
    set_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["postconf", "-e"]
    ]
    testbase.assert_equal(len(set_calls), 0)
    return


def test_set_main_directive_dry_run_does_not_dispatch():
    """
        set_main_directive() with dry_run=True returns DRY_RUN and
        does NOT invoke postconf -e.
    """
    runner = _MockRunner()
    runner.results[("postconf", "-h", "myhostname")] = _ok(
        stdout="old.example.com\n",
    )
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(dry_run=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.set_main_directive(key="myhostname", value="new.example.com")
    testbase.assert_equal(result.status, MailActionStatus.DRY_RUN)
    set_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["postconf", "-e"]
    ]
    testbase.assert_equal(len(set_calls), 0)
    return


def test_set_main_directive_failure_propagates():
    """
        set_main_directive() returns FAILED with the captured exit
        code when ``postconf -e`` exits non-zero.
    """
    runner = _MockRunner()
    runner.results[("postconf", "-h", "myhostname")] = _ok(
        stdout="old.example.com\n",
    )
    runner.results[(
        "postconf", "-e", "myhostname=new.example.com",
    )] = _fail(stderr="boom", exit_code=2)
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(elevated=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.set_main_directive(key="myhostname", value="new.example.com")
    testbase.assert_equal(result.status, MailActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 2)
    testbase.assert_equal(result.stderr, "boom")
    return


def test_set_main_directive_autoreload_chains_service_manager():
    """
        set_main_directive() with autoreload=True calls
        service_manager.reload(...) on success.
    """
    runner = _MockRunner(default_result=_ok())
    runner.results[("postconf", "-h", "myhostname")] = _ok(
        stdout="old.example.com\n",
    )
    sm = _MockServiceManager()
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(elevated=True, autoreload=True),
        service_manager=sm,
    )
    result = mgr.set_main_directive(key="myhostname", value="new.example.com")
    testbase.assert_equal(result.status, MailActionStatus.SUCCESS)
    testbase.assert_equal(sm.reload_calls, ["postfix"])
    return


def test_remove_main_directive_skipped_when_at_default():
    """
        remove_main_directive() returns SKIPPED when the live value
        equals the postconf default.
    """
    runner = _MockRunner()
    runner.results[("postconf", "-h", "myorigin")] = _ok(
        stdout="$myhostname\n",
    )
    runner.results[("postconf", "-d", "-h", "myorigin")] = _ok(
        stdout="$myhostname\n",
    )
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(),
        service_manager=_MockServiceManager(),
    )
    result = mgr.remove_main_directive(key="myorigin")
    testbase.assert_equal(result.status, MailActionStatus.SKIPPED)
    return


def test_remove_main_directive_dispatches_postconf_x():
    """
        remove_main_directive() dispatches ``postconf -X`` when the
        live value differs from the default.
    """
    runner = _MockRunner(default_result=_ok())
    runner.results[("postconf", "-h", "myorigin")] = _ok(
        stdout="example.com\n",
    )
    runner.results[("postconf", "-d", "-h", "myorigin")] = _ok(
        stdout="$myhostname\n",
    )
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(elevated=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.remove_main_directive(key="myorigin")
    testbase.assert_equal(result.status, MailActionStatus.SUCCESS)
    rm_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["postconf", "-X"]
    ]
    testbase.assert_equal(len(rm_calls), 1)
    testbase.assert_equal(rm_calls[0]["args"], ["postconf", "-X", "myorigin"])
    return


# ----------------------------------------------------------------------
# master.cf
# ----------------------------------------------------------------------


def test_set_master_service_dispatches_postconf_m():
    """
        set_master_service() builds the ``postconf -M`` argument.
    """
    runner = _MockRunner(default_result=_ok())
    runner.results[("postconf", "-Mh", "submission/inet")] = _ok(stdout="")
    spec = MasterCfService(
        service="submission",
        type="inet",
        private="n",
        unpriv="-",
        chroot="y",
        wakeup="-",
        maxproc="-",
        command="smtpd",
    )
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(elevated=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.set_master_service(spec=spec)
    testbase.assert_equal(result.status, MailActionStatus.SUCCESS)
    m_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["postconf", "-M"]
    ]
    testbase.assert_equal(len(m_calls), 1)
    expected = (
        "submission/inet="
        "submission inet n - y - - smtpd"
    )
    testbase.assert_equal(m_calls[0]["args"][2], expected)
    return


def test_set_master_service_skipped_when_unchanged():
    """
        set_master_service() returns SKIPPED when the live line
        already matches the rendered spec.
    """
    runner = _MockRunner()
    runner.results[("postconf", "-Mh", "submission/inet")] = _ok(
        stdout="submission inet n - y - - smtpd\n",
    )
    spec = MasterCfService(
        service="submission",
        type="inet",
        private="n",
        unpriv="-",
        chroot="y",
        wakeup="-",
        maxproc="-",
        command="smtpd",
    )
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(),
        service_manager=_MockServiceManager(),
    )
    result = mgr.set_master_service(spec=spec)
    testbase.assert_equal(result.status, MailActionStatus.SKIPPED)
    return


def test_remove_master_service_dispatches_postconf_mx():
    """
        remove_master_service() dispatches ``postconf -MX`` when the
        service is currently defined.
    """
    runner = _MockRunner(default_result=_ok())
    runner.results[("postconf", "-Mh", "smtps/inet")] = _ok(
        stdout="smtps inet n - y - - smtpd\n",
    )
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(elevated=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.remove_master_service(service="smtps", type_="inet")
    testbase.assert_equal(result.status, MailActionStatus.SUCCESS)
    rm_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["postconf", "-MX"]
    ]
    testbase.assert_equal(len(rm_calls), 1)
    testbase.assert_equal(
        rm_calls[0]["args"], ["postconf", "-MX", "smtps/inet"],
    )
    return


def test_remove_master_service_skipped_when_absent():
    """
        remove_master_service() returns SKIPPED when no such service
        is currently defined.
    """
    runner = _MockRunner()
    runner.results[("postconf", "-Mh", "smtps/inet")] = _fail()
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(),
        service_manager=_MockServiceManager(),
    )
    result = mgr.remove_master_service(service="smtps", type_="inet")
    testbase.assert_equal(result.status, MailActionStatus.SKIPPED)
    return


def test_set_master_directive_dispatches_postconf_p():
    """
        set_master_directive() dispatches ``postconf -P`` when the
        per-service value differs from the desired value.
    """
    runner = _MockRunner(default_result=_ok())
    runner.results[(
        "postconf", "-P", "-h", "submission/inet/smtpd_tls_security_level",
    )] = _ok(stdout="may\n")
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(elevated=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.set_master_directive(
        service="submission",
        type_="inet",
        key="smtpd_tls_security_level",
        value="encrypt",
    )
    testbase.assert_equal(result.status, MailActionStatus.SUCCESS)
    p_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["postconf", "-P"]
        and len(c["args"]) == 3
    ]
    testbase.assert_equal(len(p_calls), 1)
    testbase.assert_equal(
        p_calls[0]["args"],
        [
            "postconf",
            "-P",
            "submission/inet/smtpd_tls_security_level=encrypt",
        ],
    )
    return


# ----------------------------------------------------------------------
# Maps
# ----------------------------------------------------------------------


def test_write_map_writes_via_tee_and_compiles():
    """
        write_map() writes via tee and immediately invokes postmap.
    """
    runner = _MockRunner(default_result=_ok())
    runner.results[("cat", "/etc/postfix/transport")] = _fail()
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(elevated=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.write_map(name="transport", content="foo bar\n")
    testbase.assert_equal(result.status, MailActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_equal(tee_calls[0]["stdin"], "foo bar\n")
    postmap_calls = [
        c for c in runner.calls if c["args"][0] == "postmap"
    ]
    testbase.assert_equal(len(postmap_calls), 1)
    testbase.assert_equal(
        postmap_calls[0]["args"], ["postmap", "/etc/postfix/transport"],
    )
    return


def test_write_map_skipped_when_content_matches_and_db_fresh():
    """
        write_map() returns SKIPPED when content matches and the .db
        is at least as recent as the source.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/postfix/transport")] = _ok(
        stdout="foo bar\n",
    )
    runner.results[("stat", "-c", "%Y", "/etc/postfix/transport")] = _ok(
        stdout="100\n",
    )
    runner.results[("stat", "-c", "%Y", "/etc/postfix/transport.db")] = _ok(
        stdout="200\n",
    )
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(elevated=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.write_map(name="transport", content="foo bar\n")
    testbase.assert_equal(result.status, MailActionStatus.SKIPPED)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 0)
    postmap_calls = [
        c for c in runner.calls if c["args"][0] == "postmap"
    ]
    testbase.assert_equal(len(postmap_calls), 0)
    return


def test_write_map_compiles_when_content_matches_but_db_missing():
    """
        write_map() invokes postmap when the source matches but the
        .db is missing or stale.
    """
    runner = _MockRunner(default_result=_ok())
    runner.results[("cat", "/etc/postfix/transport")] = _ok(
        stdout="foo bar\n",
    )
    runner.results[("stat", "-c", "%Y", "/etc/postfix/transport")] = _ok(
        stdout="200\n",
    )
    runner.results[("stat", "-c", "%Y", "/etc/postfix/transport.db")] = _fail()
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(elevated=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.write_map(name="transport", content="foo bar\n")
    testbase.assert_equal(result.status, MailActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 0)
    postmap_calls = [
        c for c in runner.calls if c["args"][0] == "postmap"
    ]
    testbase.assert_equal(len(postmap_calls), 1)
    return


def test_write_map_dry_run_does_not_dispatch():
    """
        write_map() with dry_run=True does NOT invoke tee/postmap.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/postfix/transport")] = _fail()
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(dry_run=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.write_map(name="transport", content="foo bar\n")
    testbase.assert_equal(result.status, MailActionStatus.DRY_RUN)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 0)
    postmap_calls = [
        c for c in runner.calls if c["args"][0] == "postmap"
    ]
    testbase.assert_equal(len(postmap_calls), 0)
    return


def test_query_map_returns_value_or_none():
    """
        query_map() returns the stripped stdout on success and None
        on non-zero exit.
    """
    runner = _MockRunner()
    runner.results[(
        "postmap", "-q", "alice@old", "/etc/postfix/canonical",
    )] = _ok(stdout="alice@new\n")
    runner.results[(
        "postmap", "-q", "bogus@host", "/etc/postfix/canonical",
    )] = _fail()
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(),
        service_manager=_MockServiceManager(),
    )
    hit = mgr.query_map(name="canonical", key="alice@old")
    miss = mgr.query_map(name="canonical", key="bogus@host")
    testbase.assert_equal(hit, "alice@new")
    testbase.assert_equal(miss, None)
    return


def test_remove_map_skipped_when_both_absent():
    """
        remove_map() returns SKIPPED when both source and .db are
        already absent.
    """
    runner = _MockRunner()
    runner.results[("stat", "/etc/postfix/transport")] = _fail()
    runner.results[("stat", "/etc/postfix/transport.db")] = _fail()
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(),
        service_manager=_MockServiceManager(),
    )
    result = mgr.remove_map(name="transport")
    testbase.assert_equal(result.status, MailActionStatus.SKIPPED)
    return


def test_remove_map_dispatches_rm():
    """
        remove_map() dispatches rm -f for both source and .db when
        either is present.
    """
    runner = _MockRunner(default_result=_ok())
    runner.results[("stat", "/etc/postfix/transport")] = _ok()
    runner.results[("stat", "/etc/postfix/transport.db")] = _ok()
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(elevated=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.remove_map(name="transport")
    testbase.assert_equal(result.status, MailActionStatus.SUCCESS)
    rm_calls = [c for c in runner.calls if c["args"][0] == "rm"]
    testbase.assert_equal(len(rm_calls), 1)
    testbase.assert_equal(
        rm_calls[0]["args"],
        ["rm", "-f", "/etc/postfix/transport", "/etc/postfix/transport.db"],
    )
    return


# ----------------------------------------------------------------------
# /etc/aliases
# ----------------------------------------------------------------------


def test_list_aliases_parses_etc_aliases():
    """
        list_aliases() returns dict[str, list[str]] from the file.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/aliases")] = _ok(
        stdout="postmaster: root\nroot: alice, bob\n",
    )
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(),
        service_manager=_MockServiceManager(),
    )
    parsed = mgr.list_aliases()
    testbase.assert_equal(parsed["postmaster"], ["root"])
    testbase.assert_equal(parsed["root"], ["alice", "bob"])
    return


def test_set_alias_writes_via_tee_and_chains_newaliases():
    """
        set_alias() writes via tee and chains newaliases.
    """
    runner = _MockRunner(default_result=_ok())
    runner.results[("cat", "/etc/aliases")] = _ok(
        stdout="postmaster: root\n",
    )
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(elevated=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.set_alias(name="root", targets=["alice", "bob"])
    testbase.assert_equal(result.status, MailActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true("root: alice, bob" in tee_calls[0]["stdin"])
    newalias_calls = [
        c for c in runner.calls if c["args"][0] == "newaliases"
    ]
    testbase.assert_equal(len(newalias_calls), 1)
    return


def test_set_alias_skipped_when_unchanged():
    """
        set_alias() returns SKIPPED when targets match.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/aliases")] = _ok(
        stdout="root: alice, bob\n",
    )
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(),
        service_manager=_MockServiceManager(),
    )
    result = mgr.set_alias(name="root", targets=["alice", "bob"])
    testbase.assert_equal(result.status, MailActionStatus.SKIPPED)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 0)
    return


def test_set_alias_dry_run_does_not_dispatch():
    """
        set_alias() with dry_run=True returns DRY_RUN without
        dispatching tee/newaliases.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/aliases")] = _ok(stdout="")
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(dry_run=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.set_alias(name="root", targets=["alice"])
    testbase.assert_equal(result.status, MailActionStatus.DRY_RUN)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 0)
    newalias_calls = [
        c for c in runner.calls if c["args"][0] == "newaliases"
    ]
    testbase.assert_equal(len(newalias_calls), 0)
    return


def test_remove_alias_chains_newaliases():
    """
        remove_alias() strips the matching line and runs newaliases.
    """
    runner = _MockRunner(default_result=_ok())
    runner.results[("cat", "/etc/aliases")] = _ok(
        stdout="postmaster: root\nroot: alice\n",
    )
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(elevated=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.remove_alias(name="root")
    testbase.assert_equal(result.status, MailActionStatus.SUCCESS)
    newalias_calls = [
        c for c in runner.calls if c["args"][0] == "newaliases"
    ]
    testbase.assert_equal(len(newalias_calls), 1)
    return


def test_remove_alias_skipped_when_absent():
    """
        remove_alias() returns SKIPPED when no entry matches.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/aliases")] = _ok(
        stdout="postmaster: root\n",
    )
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(),
        service_manager=_MockServiceManager(),
    )
    result = mgr.remove_alias(name="root")
    testbase.assert_equal(result.status, MailActionStatus.SKIPPED)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 0)
    return


def test_set_alias_failure_when_newaliases_fails():
    """
        set_alias() reports FAILED when newaliases exits non-zero,
        even though the tee write succeeded.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/aliases")] = _ok(stdout="")
    runner.results[("tee", "/etc/aliases")] = _ok()
    runner.results[("newaliases",)] = _fail(stderr="cannot rebuild")
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(elevated=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.set_alias(name="root", targets=["alice"])
    testbase.assert_equal(result.status, MailActionStatus.FAILED)
    testbase.assert_equal(result.stderr, "cannot rebuild")
    return


# ----------------------------------------------------------------------
# Queue
# ----------------------------------------------------------------------


def test_list_queue_empty_when_postfix_says_empty():
    """
        list_queue() returns empty=True when Postfix says so.
    """
    runner = _MockRunner()
    runner.results[("postqueue", "-p")] = _ok(stdout="Mail queue is empty\n")
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(),
        service_manager=_MockServiceManager(),
    )
    state = mgr.list_queue()
    testbase.assert_true(state.empty)
    testbase.assert_equal(len(state.messages), 0)
    return


def test_list_queue_parses_records():
    """
        list_queue() parses the postqueue dump into messages.
    """
    runner = _MockRunner()
    runner.results[("postqueue", "-p")] = _ok(
        stdout=(
            "-Queue ID-  --Size-- ----Arrival Time---- -Sender/Recipient-------\n"
            "AAAA1111      1234 Mon May  6 12:34:56  sender@example.com\n"
            "                                         user1@example.com\n"
            "\n"
            "-- 1 Kbytes in 1 Request.\n"
        ),
    )
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(),
        service_manager=_MockServiceManager(),
    )
    state = mgr.list_queue()
    testbase.assert_true(state.empty is False)
    testbase.assert_equal(len(state.messages), 1)
    testbase.assert_equal(state.messages[0].queue_id, "AAAA1111")
    return


def test_flush_queue_dispatches_postqueue_f():
    """
        flush_queue() dispatches ``postqueue -f``.
    """
    runner = _MockRunner(default_result=_ok())
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(elevated=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.flush_queue()
    testbase.assert_equal(result.status, MailActionStatus.SUCCESS)
    testbase.assert_equal(runner.calls[-1]["args"], ["postqueue", "-f"])
    testbase.assert_equal(runner.calls[-1]["elevated"], True)
    return


def test_delete_message_skipped_when_id_absent():
    """
        delete_message() returns SKIPPED when no such queue id is
        currently present.
    """
    runner = _MockRunner()
    runner.results[("postqueue", "-p")] = _ok(stdout="Mail queue is empty\n")
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(),
        service_manager=_MockServiceManager(),
    )
    result = mgr.delete_message(queue_id="ZZZZ9999")
    testbase.assert_equal(result.status, MailActionStatus.SKIPPED)
    delete_calls = [
        c for c in runner.calls if c["args"][0] == "postsuper"
    ]
    testbase.assert_equal(len(delete_calls), 0)
    return


def test_delete_message_dispatches_postsuper_d():
    """
        delete_message() dispatches ``postsuper -d <id>`` when the id
        is present in the queue.
    """
    runner = _MockRunner(default_result=_ok())
    runner.results[("postqueue", "-p")] = _ok(
        stdout=(
            "-Queue ID-  --Size-- ----Arrival Time---- -Sender/Recipient-------\n"
            "AAAA1111      1234 Mon May  6 12:34:56  sender@example.com\n"
            "                                         user1@example.com\n"
        ),
    )
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(elevated=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.delete_message(queue_id="AAAA1111")
    testbase.assert_equal(result.status, MailActionStatus.SUCCESS)
    delete_calls = [
        c for c in runner.calls if c["args"][0] == "postsuper"
    ]
    testbase.assert_equal(len(delete_calls), 1)
    testbase.assert_equal(
        delete_calls[0]["args"], ["postsuper", "-d", "AAAA1111"],
    )
    return


def test_delete_all_queued_dispatches_postsuper_d_all():
    """
        delete_all_queued() dispatches ``postsuper -d ALL``.
    """
    runner = _MockRunner(default_result=_ok())
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(elevated=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.delete_all_queued()
    testbase.assert_equal(result.status, MailActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"], ["postsuper", "-d", "ALL"],
    )
    return


def test_requeue_message_dispatches_postsuper_r():
    """
        requeue_message() dispatches ``postsuper -r <id>``.
    """
    runner = _MockRunner(default_result=_ok())
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(elevated=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.requeue_message(queue_id="AAAA1111")
    testbase.assert_equal(result.status, MailActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"], ["postsuper", "-r", "AAAA1111"],
    )
    return


# ----------------------------------------------------------------------
# Service lifecycle delegation
# ----------------------------------------------------------------------


def test_reload_delegates_to_service_manager():
    """
        reload() delegates to service_manager.reload("postfix") and
        does NOT spell systemctl directly.
    """
    runner = _MockRunner(default_result=_ok())
    sm = _MockServiceManager()
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(),
        service_manager=sm,
    )
    result = mgr.reload()
    testbase.assert_equal(result.status, MailActionStatus.SUCCESS)
    testbase.assert_equal(sm.reload_calls, ["postfix"])
    systemctl_calls = [
        c for c in runner.calls if c["args"][0] == "systemctl"
    ]
    testbase.assert_equal(len(systemctl_calls), 0)
    return


def test_restart_delegates_to_service_manager():
    """
        restart() delegates to service_manager.restart("postfix").
    """
    runner = _MockRunner(default_result=_ok())
    sm = _MockServiceManager()
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(),
        service_manager=sm,
    )
    result = mgr.restart()
    testbase.assert_equal(result.status, MailActionStatus.SUCCESS)
    testbase.assert_equal(sm.restart_calls, ["postfix"])
    return


# ----------------------------------------------------------------------
# Failure mapping & dry-run sweep
# ----------------------------------------------------------------------


def test_flush_queue_failure_propagates_exit_code():
    """
        flush_queue() returns FAILED with the captured exit code.
    """
    runner = _MockRunner(default_result=_fail(stderr="kaboom", exit_code=3))
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(elevated=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.flush_queue()
    testbase.assert_equal(result.status, MailActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 3)
    testbase.assert_equal(result.stderr, "kaboom")
    return


def test_delete_all_queued_dry_run_does_not_dispatch():
    """
        delete_all_queued() with dry_run=True returns DRY_RUN and
        does NOT invoke postsuper.
    """
    runner = _MockRunner()
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(dry_run=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.delete_all_queued()
    testbase.assert_equal(result.status, MailActionStatus.DRY_RUN)
    delete_calls = [
        c for c in runner.calls if c["args"][0] == "postsuper"
    ]
    testbase.assert_equal(len(delete_calls), 0)
    return


def test_result_is_mail_action_result():
    """
        Every dispatched verb returns a MailActionResult instance so
        the feature-specific fields remain accessible.
    """
    runner = _MockRunner(default_result=_ok())
    mgr = MailTransportManager(
        runner=runner,
        config=MailTransportManagerConfig(elevated=True),
        service_manager=_MockServiceManager(),
    )
    result = mgr.flush_queue()
    testbase.assert_true(isinstance(result, MailActionResult))
    return
