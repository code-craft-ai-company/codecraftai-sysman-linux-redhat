# Tests for the NfsServerManager facade.
#
# All tests use a ``_MockRunner`` that records every ``run`` call and
# returns pre-seeded :class:`RunResult` values keyed by argv tuple.
# A ``_MockServiceManager`` records ``restart`` calls for the
# delegation tests.  No live ``exportfs`` / ``cat`` / ``tee`` is
# invoked.

from dataclasses import dataclass, field
from typing import Callable, Optional

from ccai import testbase

from ccai.sysman.linux.redhat.nfsservermanagement import (
    ClientSpec,
    ExportEntry,
    NfsActionResult,
    NfsActionStatus,
    NfsQueryError,
    NfsServerManager,
    NfsServerManagerConfig,
    NfsServerStats,
    _ini_get_value,
    _ini_parse_all,
    _ini_strip,
    _ini_upsert,
    _parse_client_token,
    _parse_exportfs_v,
    _parse_exports_file_body,
    _parse_nfsstat_s,
    _parse_showmount_e,
    _render_export_line,
)
from ccai.sysman.linux.redhat.runners.models import RunResult


# ----------------------------------------------------------------------
# Mock runner + service manager
# ----------------------------------------------------------------------


@dataclass
class _MockRunner:
    """
        Minimal ``ICommandRunner`` stand-in that records every
        ``run`` call and returns pre-seeded :class:`RunResult`
        values keyed by argv tuple.
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
        return None


@dataclass
class _ServiceCallRecord:
    """
        One recorded restart call against the mock service manager.
    """
    name: str


@dataclass
class _MockServiceManager:
    """
        Records ``restart`` invocations and returns a stub result.
    """

    calls: list = field(default_factory=list)
    next_status: NfsActionStatus = NfsActionStatus.SUCCESS
    next_exit_code: int = 0
    next_stderr: str = ""

    def restart(self, name: str) -> NfsActionResult:
        record = _ServiceCallRecord(name=name)
        self.calls.append(record)
        rtnval = NfsActionResult(
            status=self.next_status,
            detail=name,
            exit_code=self.next_exit_code,
            stdout="",
            stderr=self.next_stderr,
            command=["systemctl", "restart", name],
            path=None,
        )
        return rtnval


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _ok(stdout: str = "", stderr: str = "") -> RunResult:
    """
        Build a successful :class:`RunResult`.
    """
    rtnval = RunResult(
        exit_code=0, stdout=stdout, stderr=stderr, command=[],
    )
    return rtnval


def _fail(
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 1,
) -> RunResult:
    """
        Build a failing :class:`RunResult`.
    """
    rtnval = RunResult(
        exit_code=exit_code, stdout=stdout, stderr=stderr, command=[],
    )
    return rtnval


_DROPIN_PATH = "/etc/exports.d/ccai.exports"
_EXPORTS_MAIN = "/etc/exports"
_EXPORTS_DIR = "/etc/exports.d"
_IDMAPD_CONF = "/etc/idmapd.conf"
_NFS_CONF = "/etc/nfs.conf"


def _seed_cat(
    runner: _MockRunner,
    *,
    path: str,
    body: Optional[str],
) -> None:
    """
        Seed the runner so ``cat <path>`` returns ``body`` on success
        or non-zero when ``body`` is ``None``.
    """
    if body is None:
        result = RunResult(
            exit_code=1, stdout="", stderr="No such file", command=["cat", path],
        )
    else:
        result = RunResult(
            exit_code=0, stdout=body, stderr="", command=["cat", path],
        )
    runner.results[("cat", path)] = result
    return


def _seed_ls(
    runner: _MockRunner,
    *,
    directory: str,
    names: Optional[list[str]],
) -> None:
    """
        Seed the runner so ``ls -1 <directory>`` returns ``names``.
    """
    if names is None:
        result = RunResult(
            exit_code=1, stdout="", stderr="No such directory",
            command=["ls", "-1", directory],
        )
    else:
        body = "\n".join(names)
        if len(body) > 0:
            body = body + "\n"
        result = RunResult(
            exit_code=0, stdout=body, stderr="",
            command=["ls", "-1", directory],
        )
    runner.results[("ls", "-1", directory)] = result
    return


def _make_entry(
    *,
    path: str = "/srv/share",
    client: str = "*",
    options: Optional[list[str]] = None,
) -> ExportEntry:
    """
        Build a single-client :class:`ExportEntry`.
    """
    if options is None:
        opts = ["rw", "sync"]
    else:
        opts = list(options)
    rtnval = ExportEntry(
        path=path,
        clients=[ClientSpec(client=client, options=opts)],
    )
    return rtnval


# ----------------------------------------------------------------------
# add_export
# ----------------------------------------------------------------------


def test_add_export_writes_dropin_and_reloads():
    """
        add_export writes the rendered line to the drop-in via tee
        and then dispatches ``exportfs -r``.
    """
    runner = _MockRunner()
    runner.default_result = _ok()
    _seed_cat(runner, path=_DROPIN_PATH, body=None)

    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(runner=runner, config=config)
    entry = _make_entry(path="/srv/share", client="*", options=["rw", "sync"])

    result = manager.add_export(entry=entry)

    testbase.assert_equal(result.status, NfsActionStatus.SUCCESS)
    testbase.assert_equal(result.path, "/srv/share")

    tee_calls = [
        call for call in runner.calls
        if len(call["args"]) >= 2
        and call["args"][0] == "tee"
        and call["args"][1] == _DROPIN_PATH
    ]
    testbase.assert_equal(len(tee_calls), 1)
    written = tee_calls[0]["stdin"]
    testbase.assert_equal("/srv/share *(rw,sync)" in written, True)
    testbase.assert_equal(tee_calls[0]["elevated"], True)

    reload_calls = [
        call for call in runner.calls
        if call["args"] == ["exportfs", "-r"]
    ]
    testbase.assert_equal(len(reload_calls), 1)
    testbase.assert_equal(reload_calls[0]["elevated"], True)
    return


def test_add_export_skipped_when_line_present():
    """
        add_export returns SKIPPED when the rendered line already
        appears in the drop-in.
    """
    runner = _MockRunner()
    body = "/srv/share *(rw,sync)\n"
    _seed_cat(runner, path=_DROPIN_PATH, body=body)

    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(runner=runner, config=config)
    entry = _make_entry(path="/srv/share", client="*", options=["rw", "sync"])

    result = manager.add_export(entry=entry)

    testbase.assert_equal(result.status, NfsActionStatus.SKIPPED)

    tee_calls = [
        call for call in runner.calls
        if len(call["args"]) >= 1 and call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 0)
    reload_calls = [
        call for call in runner.calls
        if call["args"] == ["exportfs", "-r"]
    ]
    testbase.assert_equal(len(reload_calls), 0)
    return


def test_add_export_dry_run_short_circuits():
    """
        add_export under ``dry_run=True`` returns DRY_RUN without
        invoking tee or exportfs.
    """
    runner = _MockRunner()
    _seed_cat(runner, path=_DROPIN_PATH, body=None)

    config = NfsServerManagerConfig(elevated=True, dry_run=True)
    manager = NfsServerManager(runner=runner, config=config)
    entry = _make_entry(path="/srv/share")

    result = manager.add_export(entry=entry)

    testbase.assert_equal(result.status, NfsActionStatus.DRY_RUN)
    testbase.assert_equal(result.command[0], "tee")

    tee_calls = [
        call for call in runner.calls
        if len(call["args"]) >= 1 and call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 0)
    reload_calls = [
        call for call in runner.calls
        if call["args"] == ["exportfs", "-r"]
    ]
    testbase.assert_equal(len(reload_calls), 0)
    return


def test_add_export_failure_does_not_reload():
    """
        A failing tee surfaces FAILED with stderr populated and
        does not dispatch ``exportfs -r``.
    """
    runner = _MockRunner()
    _seed_cat(runner, path=_DROPIN_PATH, body=None)
    runner.results[("tee", _DROPIN_PATH)] = _fail(
        stderr="tee: cannot open", exit_code=1,
    )

    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(runner=runner, config=config)
    entry = _make_entry(path="/srv/share")

    result = manager.add_export(entry=entry)

    testbase.assert_equal(result.status, NfsActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 1)
    testbase.assert_equal("tee: cannot open" in result.stderr, True)
    testbase.assert_equal(result.command[0], "tee")

    reload_calls = [
        call for call in runner.calls
        if call["args"] == ["exportfs", "-r"]
    ]
    testbase.assert_equal(len(reload_calls), 0)
    return


def test_add_export_reload_failure_propagates():
    """
        When ``exportfs -r`` fails after a successful tee, the verb
        maps to FAILED and surfaces the reload's command + stderr.
    """
    runner = _MockRunner()
    _seed_cat(runner, path=_DROPIN_PATH, body=None)
    runner.results[("tee", _DROPIN_PATH)] = _ok()
    runner.results[("exportfs", "-r")] = _fail(
        stderr="exportfs: invalid", exit_code=2,
    )

    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(runner=runner, config=config)
    entry = _make_entry(path="/srv/share")

    result = manager.add_export(entry=entry)

    testbase.assert_equal(result.status, NfsActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 2)
    testbase.assert_equal(result.command, ["exportfs", "-r"])
    testbase.assert_equal("exportfs: invalid" in result.stderr, True)
    return


# ----------------------------------------------------------------------
# update_export
# ----------------------------------------------------------------------


def test_update_export_replaces_existing_line():
    """
        update_export rewrites the path's line when clients differ
        and dispatches ``exportfs -r``.
    """
    runner = _MockRunner()
    runner.default_result = _ok()
    body = "/srv/share 10.0.0.0/24(ro,sync)\n"
    _seed_cat(runner, path=_DROPIN_PATH, body=body)

    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(runner=runner, config=config)
    entry = _make_entry(path="/srv/share", client="*", options=["rw", "sync"])

    result = manager.update_export(entry=entry)

    testbase.assert_equal(result.status, NfsActionStatus.SUCCESS)
    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 1)
    written = tee_calls[0]["stdin"]
    testbase.assert_equal("*(rw,sync)" in written, True)
    testbase.assert_equal("10.0.0.0/24(ro,sync)" in written, False)

    reload_calls = [
        call for call in runner.calls
        if call["args"] == ["exportfs", "-r"]
    ]
    testbase.assert_equal(len(reload_calls), 1)
    return


def test_update_export_skipped_when_unchanged():
    """
        update_export returns SKIPPED when the rendered line already
        matches the existing content.
    """
    runner = _MockRunner()
    body = "/srv/share *(rw,sync)\n"
    _seed_cat(runner, path=_DROPIN_PATH, body=body)

    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(runner=runner, config=config)
    entry = _make_entry(path="/srv/share", client="*", options=["rw", "sync"])

    result = manager.update_export(entry=entry)

    testbase.assert_equal(result.status, NfsActionStatus.SKIPPED)
    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 0)
    return


# ----------------------------------------------------------------------
# remove_export
# ----------------------------------------------------------------------


def test_remove_export_strips_line_and_reloads():
    """
        remove_export strips the matching line, rewrites the
        drop-in, and dispatches ``exportfs -r``.
    """
    runner = _MockRunner()
    runner.default_result = _ok()
    body = (
        "/srv/share *(rw,sync)\n"
        "/srv/other *(ro,sync)\n"
    )
    _seed_cat(runner, path=_DROPIN_PATH, body=body)

    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(runner=runner, config=config)

    result = manager.remove_export(path="/srv/share")

    testbase.assert_equal(result.status, NfsActionStatus.SUCCESS)
    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 1)
    written = tee_calls[0]["stdin"]
    testbase.assert_equal("/srv/share" in written, False)
    testbase.assert_equal("/srv/other" in written, True)

    reload_calls = [
        call for call in runner.calls
        if call["args"] == ["exportfs", "-r"]
    ]
    testbase.assert_equal(len(reload_calls), 1)
    return


def test_remove_export_skipped_when_absent():
    """
        remove_export returns SKIPPED when no line matches the path.
    """
    runner = _MockRunner()
    body = "/srv/other *(ro,sync)\n"
    _seed_cat(runner, path=_DROPIN_PATH, body=body)

    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(runner=runner, config=config)

    result = manager.remove_export(path="/srv/share")

    testbase.assert_equal(result.status, NfsActionStatus.SKIPPED)
    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 0)
    return


def test_remove_export_skipped_when_dropin_missing():
    """
        remove_export returns SKIPPED when the drop-in itself is
        missing.
    """
    runner = _MockRunner()
    _seed_cat(runner, path=_DROPIN_PATH, body=None)

    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(runner=runner, config=config)

    result = manager.remove_export(path="/srv/share")

    testbase.assert_equal(result.status, NfsActionStatus.SKIPPED)
    return


def test_remove_export_rm_fs_when_empty():
    """
        remove_export rm -f's the drop-in when stripping the last
        line leaves no exports behind, then reloads.
    """
    runner = _MockRunner()
    runner.default_result = _ok()
    body = "/srv/share *(rw,sync)\n"
    _seed_cat(runner, path=_DROPIN_PATH, body=body)

    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(runner=runner, config=config)

    result = manager.remove_export(path="/srv/share")

    testbase.assert_equal(result.status, NfsActionStatus.SUCCESS)

    rm_calls = [
        call for call in runner.calls
        if call["args"][0] == "rm"
    ]
    testbase.assert_equal(len(rm_calls), 1)
    testbase.assert_equal(rm_calls[0]["args"], ["rm", "-f", _DROPIN_PATH])
    testbase.assert_equal(rm_calls[0]["elevated"], True)

    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 0)

    reload_calls = [
        call for call in runner.calls
        if call["args"] == ["exportfs", "-r"]
    ]
    testbase.assert_equal(len(reload_calls), 1)
    return


# ----------------------------------------------------------------------
# reload / unexport_all
# ----------------------------------------------------------------------


def test_reload_dispatches_exportfs_r():
    """
        reload() dispatches ``exportfs -r``.
    """
    runner = _MockRunner()
    runner.default_result = _ok()

    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(runner=runner, config=config)

    result = manager.reload()

    testbase.assert_equal(result.status, NfsActionStatus.SUCCESS)
    testbase.assert_equal(runner.calls[-1]["args"], ["exportfs", "-r"])
    testbase.assert_equal(runner.calls[-1]["elevated"], True)
    return


def test_reload_dry_run():
    """
        reload() under ``dry_run=True`` returns DRY_RUN without
        invoking the runner.
    """
    runner = _MockRunner()
    config = NfsServerManagerConfig(elevated=True, dry_run=True)
    manager = NfsServerManager(runner=runner, config=config)

    result = manager.reload()

    testbase.assert_equal(result.status, NfsActionStatus.DRY_RUN)
    testbase.assert_equal(len(runner.calls), 0)
    return


def test_unexport_all_dispatches_exportfs_ua():
    """
        unexport_all() dispatches ``exportfs -ua``.
    """
    runner = _MockRunner()
    runner.default_result = _ok()

    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(runner=runner, config=config)

    result = manager.unexport_all()

    testbase.assert_equal(result.status, NfsActionStatus.SUCCESS)
    testbase.assert_equal(runner.calls[-1]["args"], ["exportfs", "-ua"])
    return


# ----------------------------------------------------------------------
# restart — service-manager delegation
# ----------------------------------------------------------------------


def test_restart_delegates_to_service_manager():
    """
        restart() delegates to the injected ServiceManager and never
        spells systemctl directly.
    """
    runner = _MockRunner()
    runner.default_result = _ok()
    sm = _MockServiceManager()

    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(
        runner=runner, config=config, service_manager=sm,
    )

    result = manager.restart()

    testbase.assert_equal(result.status, NfsActionStatus.SUCCESS)
    testbase.assert_equal(len(sm.calls), 1)
    testbase.assert_equal(sm.calls[0].name, "nfs-server")

    systemctl_calls = [
        call for call in runner.calls
        if len(call["args"]) >= 1 and call["args"][0] == "systemctl"
    ]
    testbase.assert_equal(len(systemctl_calls), 0)
    return


def test_restart_without_service_manager_fails():
    """
        restart() without an injected ServiceManager returns FAILED
        rather than spelling ``systemctl`` itself.
    """
    runner = _MockRunner()
    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(runner=runner, config=config)

    result = manager.restart()

    testbase.assert_equal(result.status, NfsActionStatus.FAILED)

    systemctl_calls = [
        call for call in runner.calls
        if len(call["args"]) >= 1 and call["args"][0] == "systemctl"
    ]
    testbase.assert_equal(len(systemctl_calls), 0)
    return


def test_restart_idmapd_delegates_to_service_manager():
    """
        restart_idmapd() delegates to the injected ServiceManager
        with the ``nfs-idmapd`` unit name.
    """
    runner = _MockRunner()
    sm = _MockServiceManager()

    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(
        runner=runner, config=config, service_manager=sm,
    )

    result = manager.restart_idmapd()

    testbase.assert_equal(result.status, NfsActionStatus.SUCCESS)
    testbase.assert_equal(len(sm.calls), 1)
    testbase.assert_equal(sm.calls[0].name, "nfs-idmapd")
    return


# ----------------------------------------------------------------------
# list_active / list_configured / showmount
# ----------------------------------------------------------------------


def test_list_active_parses_exportfs_v():
    """
        list_active() parses ``exportfs -v`` output into
        :class:`ExportEntry` records.
    """
    runner = _MockRunner()
    stdout_text = (
        "/srv/share \t<world>(sync,wdelay,hide,no_subtree_check,sec=sys,rw)\n"
        "/srv/other\n"
        "\t10.0.0.0/24(sync,wdelay,hide,sec=sys,ro)\n"
    )
    runner.results[("exportfs", "-v")] = _ok(stdout=stdout_text)

    config = NfsServerManagerConfig()
    manager = NfsServerManager(runner=runner, config=config)

    entries = manager.list_active()

    testbase.assert_equal(len(entries), 2)
    by_path = {entry.path: entry for entry in entries}
    testbase.assert_equal(by_path["/srv/share"].clients[0].client, "<world>")
    testbase.assert_equal(
        "rw" in by_path["/srv/share"].clients[0].options, True,
    )
    testbase.assert_equal(by_path["/srv/other"].clients[0].client, "10.0.0.0/24")
    testbase.assert_equal(
        "ro" in by_path["/srv/other"].clients[0].options, True,
    )
    return


def test_list_active_raises_on_failure_with_stderr():
    """
        list_active() raises NfsQueryError when ``exportfs -v``
        exits non-zero with non-empty stderr.
    """
    runner = _MockRunner()
    runner.results[("exportfs", "-v")] = _fail(
        stderr="exportfs: not found", exit_code=127,
    )

    config = NfsServerManagerConfig()
    manager = NfsServerManager(runner=runner, config=config)

    raised: Optional[NfsQueryError] = None
    try:
        manager.list_active()
    except NfsQueryError as exc:
        raised = exc

    testbase.assert_equal(raised is not None, True)
    testbase.assert_equal(raised.exit_code, 127)
    return


def test_list_configured_merges_main_and_dropins():
    """
        list_configured() merges /etc/exports with every drop-in and
        annotates each entry with its source_file.
    """
    runner = _MockRunner()

    main_body = "/srv/main *(rw,sync)\n"
    _seed_cat(runner, path=_EXPORTS_MAIN, body=main_body)
    _seed_ls(
        runner, directory=_EXPORTS_DIR,
        names=["ccai.exports", "README.txt"],
    )
    dropin_body = "/srv/dropin 10.0.0.0/24(ro,sync)\n"
    _seed_cat(
        runner,
        path=_EXPORTS_DIR + "/ccai.exports",
        body=dropin_body,
    )

    config = NfsServerManagerConfig()
    manager = NfsServerManager(runner=runner, config=config)

    entries = manager.list_configured()

    testbase.assert_equal(len(entries), 2)
    by_path = {entry.path: entry for entry in entries}
    testbase.assert_equal(by_path["/srv/main"].source_file, _EXPORTS_MAIN)
    testbase.assert_equal(
        by_path["/srv/dropin"].source_file,
        _EXPORTS_DIR + "/ccai.exports",
    )
    return


def test_showmount_parses_export_list():
    """
        showmount() parses ``showmount -e`` output into a path→clients
        map.
    """
    runner = _MockRunner()
    stdout_text = (
        "Export list for localhost:\n"
        "/srv/share *\n"
        "/srv/other 10.0.0.0/24,192.168.1.0/24\n"
    )
    runner.results[("showmount", "-e", "localhost")] = _ok(stdout=stdout_text)

    config = NfsServerManagerConfig()
    manager = NfsServerManager(runner=runner, config=config)

    rtnval = manager.showmount()

    testbase.assert_equal("/srv/share" in rtnval, True)
    testbase.assert_equal(rtnval["/srv/share"], ["*"])
    testbase.assert_equal(len(rtnval["/srv/other"]), 2)
    testbase.assert_equal("10.0.0.0/24" in rtnval["/srv/other"], True)
    return


# ----------------------------------------------------------------------
# idmapd — get / set
# ----------------------------------------------------------------------


def test_get_idmap_domain_returns_none_when_absent():
    """
        get_idmap_domain() returns None when /etc/idmapd.conf is
        missing.
    """
    runner = _MockRunner()
    _seed_cat(runner, path=_IDMAPD_CONF, body=None)

    config = NfsServerManagerConfig()
    manager = NfsServerManager(runner=runner, config=config)

    rtnval = manager.get_idmap_domain()

    testbase.assert_equal(rtnval, None)
    return


def test_get_idmap_domain_returns_current_value():
    """
        get_idmap_domain() returns the current ``Domain`` directive.
    """
    runner = _MockRunner()
    body = (
        "[General]\n"
        "Verbosity = 0\n"
        "Domain = example.com\n"
    )
    _seed_cat(runner, path=_IDMAPD_CONF, body=body)

    config = NfsServerManagerConfig()
    manager = NfsServerManager(runner=runner, config=config)

    rtnval = manager.get_idmap_domain()

    testbase.assert_equal(rtnval, "example.com")
    return


def test_set_idmap_domain_writes_directive():
    """
        set_idmap_domain() upserts the Domain directive and records
        the restart-required hint on the result detail.
    """
    runner = _MockRunner()
    runner.default_result = _ok()
    body = "[General]\nVerbosity = 0\n"
    _seed_cat(runner, path=_IDMAPD_CONF, body=body)

    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(runner=runner, config=config)

    result = manager.set_idmap_domain("example.com")

    testbase.assert_equal(result.status, NfsActionStatus.SUCCESS)
    testbase.assert_equal(
        "nfs-idmapd.service restart required" in result.detail, True,
    )
    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee" and call["args"][1] == _IDMAPD_CONF
    ]
    testbase.assert_equal(len(tee_calls), 1)
    written = tee_calls[0]["stdin"]
    testbase.assert_equal("Domain = example.com" in written, True)
    return


def test_set_idmap_domain_skipped_when_unchanged():
    """
        set_idmap_domain() returns SKIPPED when the directive
        already matches.
    """
    runner = _MockRunner()
    body = "[General]\nDomain = example.com\n"
    _seed_cat(runner, path=_IDMAPD_CONF, body=body)

    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(runner=runner, config=config)

    result = manager.set_idmap_domain("example.com")

    testbase.assert_equal(result.status, NfsActionStatus.SKIPPED)
    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 0)
    return


# ----------------------------------------------------------------------
# /etc/nfs.conf directives
# ----------------------------------------------------------------------


def test_get_directives_parses_multi_section_ini():
    """
        get_directives() returns a section→key→value mapping for
        every known directive.
    """
    runner = _MockRunner()
    body = (
        "# header comment\n"
        "[general]\n"
        "pipefs-directory=/var/lib/nfs/rpc_pipefs\n"
        "\n"
        "[nfsd]\n"
        "threads=16\n"
        "udp=n\n"
        "tcp=y\n"
    )
    _seed_cat(runner, path=_NFS_CONF, body=body)

    config = NfsServerManagerConfig()
    manager = NfsServerManager(runner=runner, config=config)

    rtnval = manager.get_directives()

    testbase.assert_equal("general" in rtnval, True)
    testbase.assert_equal("nfsd" in rtnval, True)
    testbase.assert_equal(rtnval["nfsd"]["threads"], "16")
    testbase.assert_equal(rtnval["nfsd"]["udp"], "n")
    testbase.assert_equal(
        rtnval["general"]["pipefs-directory"],
        "/var/lib/nfs/rpc_pipefs",
    )
    return


def test_set_directive_upserts_value():
    """
        set_directive() upserts a directive in an existing section.
    """
    runner = _MockRunner()
    runner.default_result = _ok()
    body = (
        "[nfsd]\n"
        "threads=8\n"
    )
    _seed_cat(runner, path=_NFS_CONF, body=body)

    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(runner=runner, config=config)

    result = manager.set_directive(section="nfsd", key="threads", value="32")

    testbase.assert_equal(result.status, NfsActionStatus.SUCCESS)
    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee" and call["args"][1] == _NFS_CONF
    ]
    testbase.assert_equal(len(tee_calls), 1)
    written = tee_calls[0]["stdin"]
    testbase.assert_equal("threads = 32" in written, True)
    testbase.assert_equal("threads=8" in written, False)
    return


def test_set_directive_skipped_when_unchanged():
    """
        set_directive() returns SKIPPED when the directive already
        matches.
    """
    runner = _MockRunner()
    body = (
        "[nfsd]\n"
        "threads = 16\n"
    )
    _seed_cat(runner, path=_NFS_CONF, body=body)

    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(runner=runner, config=config)

    result = manager.set_directive(section="nfsd", key="threads", value="16")

    testbase.assert_equal(result.status, NfsActionStatus.SKIPPED)
    return


def test_remove_directive_strips_line():
    """
        remove_directive() strips the matching line and SKIPS when
        absent.
    """
    runner = _MockRunner()
    runner.default_result = _ok()
    body = (
        "[nfsd]\n"
        "threads = 16\n"
        "udp = n\n"
    )
    _seed_cat(runner, path=_NFS_CONF, body=body)

    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(runner=runner, config=config)

    result = manager.remove_directive(section="nfsd", key="threads")

    testbase.assert_equal(result.status, NfsActionStatus.SUCCESS)
    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee" and call["args"][1] == _NFS_CONF
    ]
    written = tee_calls[0]["stdin"]
    testbase.assert_equal("threads" in written, False)
    testbase.assert_equal("udp = n" in written, True)
    return


def test_remove_directive_skipped_when_absent():
    """
        remove_directive() returns SKIPPED when the key is not
        present in the section.
    """
    runner = _MockRunner()
    body = "[nfsd]\nudp = n\n"
    _seed_cat(runner, path=_NFS_CONF, body=body)

    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(runner=runner, config=config)

    result = manager.remove_directive(section="nfsd", key="threads")

    testbase.assert_equal(result.status, NfsActionStatus.SKIPPED)
    return


# ----------------------------------------------------------------------
# server_stats
# ----------------------------------------------------------------------


def test_server_stats_parses_nfsstat_output():
    """
        server_stats() parses ``nfsstat -s`` into NfsServerStats.
    """
    runner = _MockRunner()
    stdout_text = (
        "Server rpc stats:\n"
        "calls      badcalls   badfmt     badauth    badclnt\n"
        "1234       2          0          0          0\n"
        "\n"
        "Server nfs v3:\n"
        "null       getattr    setattr\n"
        "10         200        5\n"
        "\n"
        "Server nfs v4:\n"
        "null       compound\n"
        "1          7\n"
    )
    runner.results[("nfsstat", "-s")] = _ok(stdout=stdout_text)

    config = NfsServerManagerConfig()
    manager = NfsServerManager(runner=runner, config=config)

    stats = manager.server_stats()

    testbase.assert_equal(stats.rpc_calls, 1234)
    testbase.assert_equal(stats.rpc_badcalls, 2)
    testbase.assert_equal(stats.nfs_v3_op_counts["getattr"], 200)
    testbase.assert_equal(stats.nfs_v4_op_counts["compound"], 7)
    return


# ----------------------------------------------------------------------
# Elevation policy
# ----------------------------------------------------------------------


def test_writes_elevate():
    """
        Mutating verbs pass ``elevated=True`` to the runner when
        ``config.elevated=True``.
    """
    runner = _MockRunner()
    runner.default_result = _ok()
    _seed_cat(runner, path=_DROPIN_PATH, body=None)

    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(runner=runner, config=config)

    entry = _make_entry(path="/srv/share")
    manager.add_export(entry=entry)

    tee_calls = [
        call for call in runner.calls
        if call["args"][0] == "tee"
    ]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_equal(tee_calls[0]["elevated"], True)
    reload_calls = [
        call for call in runner.calls
        if call["args"] == ["exportfs", "-r"]
    ]
    testbase.assert_equal(reload_calls[0]["elevated"], True)
    return


def test_reads_never_elevate():
    """
        Read probes (``cat``, ``exportfs -v``, ``ls``,
        ``showmount``, ``nfsstat``) never pass ``elevated=True``,
        even when ``config.elevated=True``.
    """
    runner = _MockRunner()
    runner.default_result = _ok()
    _seed_cat(runner, path=_EXPORTS_MAIN, body="")
    _seed_ls(runner, directory=_EXPORTS_DIR, names=[])
    runner.results[("exportfs", "-v")] = _ok()
    runner.results[("showmount", "-e", "localhost")] = _ok()
    runner.results[("nfsstat", "-s")] = _ok()

    config = NfsServerManagerConfig(elevated=True)
    manager = NfsServerManager(runner=runner, config=config)

    manager.list_configured()
    manager.list_active()
    manager.showmount()
    manager.server_stats()

    for call in runner.calls:
        if call["args"][0] == "cat":
            testbase.assert_equal(call["elevated"], False)
        if call["args"][0] == "ls":
            testbase.assert_equal(call["elevated"], False)
        if call["args"] == ["exportfs", "-v"]:
            testbase.assert_equal(call["elevated"], False)
        if call["args"][0] == "showmount":
            testbase.assert_equal(call["elevated"], False)
        if call["args"][0] == "nfsstat":
            testbase.assert_equal(call["elevated"], False)
    return


# ----------------------------------------------------------------------
# Renderer / parser unit tests
# ----------------------------------------------------------------------


def test_render_export_line_with_multiple_clients():
    """
        _render_export_line preserves per-client option scoping.
    """
    entry = ExportEntry(
        path="/srv/share",
        clients=[
            ClientSpec(client="10.0.0.0/24", options=["rw", "sync"]),
            ClientSpec(client="*", options=["ro"]),
        ],
    )
    rendered = _render_export_line(entry=entry)
    expected = "/srv/share 10.0.0.0/24(rw,sync) *(ro)"
    testbase.assert_equal(rendered, expected)
    return


def test_parse_exports_file_skips_comments_and_blanks():
    """
        _parse_exports_file_body skips comments and blank lines.
    """
    body = (
        "# header\n"
        "\n"
        "/srv/share *(rw,sync)\n"
        "# trailing\n"
    )
    parsed = _parse_exports_file_body(content=body)
    testbase.assert_equal(len(parsed), 1)
    testbase.assert_equal(parsed[0].path, "/srv/share")
    testbase.assert_equal(parsed[0].clients[0].client, "*")
    return


def test_parse_client_token_handles_no_options():
    """
        A bare client token without parens gets an empty options list.
    """
    parsed = _parse_client_token(token="*")
    testbase.assert_equal(parsed.client, "*")
    testbase.assert_equal(parsed.options, [])
    return


def test_parse_exportfs_v_handles_wrapped_lines():
    """
        _parse_exportfs_v handles single-line and wrapped output.
    """
    text = (
        "/srv/share \t<world>(sync,wdelay,hide,sec=sys,rw)\n"
        "/srv/wrapped\n"
        "\t10.0.0.0/24(sync,sec=sys,ro)\n"
    )
    parsed = _parse_exportfs_v(text=text)
    by_path = {entry.path: entry for entry in parsed}
    testbase.assert_equal(by_path["/srv/share"].clients[0].client, "<world>")
    testbase.assert_equal(by_path["/srv/wrapped"].clients[0].client, "10.0.0.0/24")
    return


def test_parse_showmount_e_skips_header():
    """
        _parse_showmount_e drops the ``Export list for ...`` header.
    """
    text = (
        "Export list for localhost:\n"
        "/srv/share *\n"
    )
    parsed = _parse_showmount_e(text=text)
    testbase.assert_equal("/srv/share" in parsed, True)
    testbase.assert_equal(parsed["/srv/share"], ["*"])
    return


def test_ini_parse_all_groups_by_section():
    """
        _ini_parse_all groups directives by section.
    """
    body = "[a]\nx=1\n[b]\ny=2\n"
    parsed = _ini_parse_all(content=body)
    testbase.assert_equal(parsed["a"]["x"], "1")
    testbase.assert_equal(parsed["b"]["y"], "2")
    return


def test_ini_upsert_inserts_new_section():
    """
        _ini_upsert appends a new section when the target section is
        absent.
    """
    body = "[a]\nx=1\n"
    rewritten = _ini_upsert(
        content=body, section="b", key="y", value="2",
    )
    testbase.assert_equal("[b]" in rewritten, True)
    testbase.assert_equal("y = 2" in rewritten, True)
    return


def test_ini_upsert_replaces_existing_key():
    """
        _ini_upsert replaces an existing key in place.
    """
    body = "[a]\nx = 1\ny = 2\n"
    rewritten = _ini_upsert(
        content=body, section="a", key="x", value="9",
    )
    testbase.assert_equal("x = 9" in rewritten, True)
    testbase.assert_equal("x = 1" in rewritten, False)
    testbase.assert_equal("y = 2" in rewritten, True)
    return


def test_ini_strip_removes_matching_line():
    """
        _ini_strip drops the matching key from the named section.
    """
    body = "[a]\nx = 1\ny = 2\n"
    rewritten = _ini_strip(content=body, section="a", key="x")
    testbase.assert_equal("x" in rewritten, False)
    testbase.assert_equal("y = 2" in rewritten, True)
    return


def test_ini_get_value_returns_none_when_absent():
    """
        _ini_get_value returns None when the key is absent.
    """
    body = "[a]\nx = 1\n"
    rtnval = _ini_get_value(content=body, section="a", key="missing")
    testbase.assert_equal(rtnval, None)
    return


def test_parse_nfsstat_s_picks_up_section_counters():
    """
        _parse_nfsstat_s populates rpc + v3 + v4 sections.
    """
    text = (
        "Server rpc stats:\n"
        "calls      badcalls\n"
        "100        3\n"
        "Server nfs v3:\n"
        "null  getattr\n"
        "1     50\n"
    )
    stats = _parse_nfsstat_s(text=text)
    testbase.assert_equal(stats.rpc_calls, 100)
    testbase.assert_equal(stats.rpc_badcalls, 3)
    testbase.assert_equal(stats.nfs_v3_op_counts["getattr"], 50)
    return


# ----------------------------------------------------------------------
# Result-type sanity
# ----------------------------------------------------------------------


def test_action_result_dataclass_carries_path_field():
    """
        :class:`NfsActionResult` carries a ``path`` field.
    """
    result = NfsActionResult(
        status=NfsActionStatus.SUCCESS,
        path="/srv/share",
    )
    testbase.assert_equal(result.path, "/srv/share")
    return


def test_server_stats_dataclass_shape():
    """
        :class:`NfsServerStats` exposes counters and raw text.
    """
    stats = NfsServerStats(
        rpc_calls=0,
        rpc_badcalls=0,
        nfs_v3_op_counts={},
        nfs_v4_op_counts={},
        raw="",
    )
    testbase.assert_equal(stats.rpc_calls, 0)
    testbase.assert_equal(stats.nfs_v3_op_counts, {})
    return
