# Tests for the AclManager facade.

from dataclasses import dataclass, field
from typing import Callable, Optional

from automojo import testbase

from automojo.sysman.linux.redhat.aclmanagement import (
    AclActionStatus,
    AclEntry,
    AclManager,
    AclManagerConfig,
    FileAttributes,
    FileCapabilities,
)
from automojo.sysman.linux.redhat.runners.models import RunResult


# ----------------------------------------------------------------------
# Mock runner
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
        self.calls.append({"args": list(args), "elevated": elevated})
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


# ----------------------------------------------------------------------
# Sample tool outputs
# ----------------------------------------------------------------------


_GETFACL_OUTPUT_FILE = (
    "user::rwx\n"
    "user:alice:r-x\n"
    "group::r-x\n"
    "group:devs:rwx\n"
    "mask::rwx\n"
    "other::r--\n"
)


_GETFACL_OUTPUT_DIR = (
    "user::rwx\n"
    "user:alice:r-x\n"
    "group::r-x\n"
    "mask::rwx\n"
    "other::r--\n"
    "default:user::rwx\n"
    "default:user:alice:r-x\n"
    "default:group::r-x\n"
    "default:mask::rwx\n"
    "default:other::r--\n"
)


_GETFACL_RECURSIVE_OUTPUT = (
    "# file: /srv/data\n"
    "user::rwx\n"
    "group::r-x\n"
    "other::r--\n"
    "\n"
    "# file: /srv/data/inner.txt\n"
    "user::rw-\n"
    "user:alice:r--\n"
    "group::r--\n"
    "mask::r--\n"
    "other::---\n"
)


_LSATTR_OUTPUT_IMMUTABLE = (
    "----i-------------- /etc/resolv.conf\n"
)


_LSATTR_OUTPUT_NONE = (
    "------------------- /tmp/somefile\n"
)


_LSATTR_RECURSIVE_OUTPUT = (
    "----i-------------- /srv/data/locked.cfg\n"
    "-----a------------- /srv/data/append.log\n"
    "------------------- /srv/data/normal.txt\n"
)


_GETCAP_OUTPUT_PING = (
    "/usr/bin/ping cap_net_raw=ep\n"
)


_GETCAP_RECURSIVE_OUTPUT = (
    "/usr/bin/ping cap_net_raw=ep\n"
    "/usr/bin/foo cap_net_admin,cap_net_bind_service=eip\n"
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _stat_dir_result() -> RunResult:
    return RunResult(
        exit_code=0, stdout="directory\n", stderr="",
        command=["stat"],
    )


def _stat_file_result() -> RunResult:
    return RunResult(
        exit_code=0, stdout="regular file\n", stderr="",
        command=["stat"],
    )


def _make_runner_with_acl_query(
    *,
    path: str,
    output: str,
    recursive: bool = False,
) -> _MockRunner:
    runner = _MockRunner()
    if recursive:
        key = (
            "getfacl", "-R", "--absolute-names", "--omit-header", path,
        )
    else:
        key = (
            "getfacl", "--absolute-names", "--omit-header", path,
        )
    runner.results[key] = RunResult(
        exit_code=0, stdout=output, stderr="", command=list(key),
    )
    return runner


# ----------------------------------------------------------------------
# Parser tests: getfacl
# ----------------------------------------------------------------------


def test_query_acl_parses_file_output():
    """
        query_acl() parses a non-default ACL block into AclSet entries.
    """
    runner = _make_runner_with_acl_query(
        path="/etc/passwd", output=_GETFACL_OUTPUT_FILE,
    )
    mgr = AclManager(runner, AclManagerConfig())
    acl = mgr.query_acl("/etc/passwd")
    testbase.assert_true(acl is not None)
    testbase.assert_equal(acl.path, "/etc/passwd")
    testbase.assert_equal(len(acl.entries), 6)
    testbase.assert_equal(len(acl.default_entries), 0)
    testbase.assert_true(acl.has_mask)

    alice_entries = [
        e for e in acl.entries
        if e.tag == "user" and e.qualifier == "alice"
    ]
    testbase.assert_equal(len(alice_entries), 1)
    testbase.assert_equal(alice_entries[0].perms, "r-x")
    return


def test_query_acl_parses_default_entries():
    """
        query_acl() pulls default entries into the dedicated bucket.
    """
    runner = _make_runner_with_acl_query(
        path="/srv/data", output=_GETFACL_OUTPUT_DIR,
    )
    mgr = AclManager(runner, AclManagerConfig())
    acl = mgr.query_acl("/srv/data")
    testbase.assert_true(acl is not None)
    testbase.assert_equal(len(acl.entries), 5)
    testbase.assert_equal(len(acl.default_entries), 5)
    testbase.assert_true(acl.has_mask)

    default_alice = [
        e for e in acl.default_entries
        if e.tag == "user" and e.qualifier == "alice"
    ]
    testbase.assert_equal(len(default_alice), 1)
    testbase.assert_true(default_alice[0].is_default)
    return


def test_query_acl_returns_none_for_missing_path():
    """
        query_acl() returns None when the underlying getfacl fails.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=1, stdout="", stderr="No such file or directory",
        command=["getfacl"],
    ))
    mgr = AclManager(runner, AclManagerConfig())
    acl = mgr.query_acl("/nope")
    testbase.assert_true(acl is None)
    return


def test_query_acl_recursive_returns_dict():
    """
        query_acl(recursive=True) returns a dict keyed by path.
    """
    runner = _make_runner_with_acl_query(
        path="/srv/data",
        output=_GETFACL_RECURSIVE_OUTPUT,
        recursive=True,
    )
    mgr = AclManager(runner, AclManagerConfig())
    acl_map = mgr.query_acl("/srv/data", recursive=True)
    testbase.assert_type(acl_map, dict)
    testbase.assert_true("/srv/data" in acl_map)
    testbase.assert_true("/srv/data/inner.txt" in acl_map)
    inner = acl_map["/srv/data/inner.txt"]
    testbase.assert_true(inner.has_mask)
    return


def test_enumerate_acls_dispatches_recursive_command():
    """
        enumerate_acls() dispatches ``getfacl -R --absolute-names
        --omit-header``.
    """
    runner = _make_runner_with_acl_query(
        path="/srv/data",
        output=_GETFACL_RECURSIVE_OUTPUT,
        recursive=True,
    )
    mgr = AclManager(runner, AclManagerConfig())
    acl_map = mgr.enumerate_acls("/srv/data")
    testbase.assert_equal(len(acl_map), 2)
    return


# ----------------------------------------------------------------------
# set_acl
# ----------------------------------------------------------------------


def test_set_acl_dispatches_setfacl_modify():
    """
        set_acl() dispatches ``setfacl -m`` with the rendered spec.
    """
    runner = _make_runner_with_acl_query(
        path="/etc/passwd", output=_GETFACL_OUTPUT_FILE,
    )
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=["setfacl"],
    )
    mgr = AclManager(runner, AclManagerConfig(elevated=True))
    new_entries = [
        AclEntry(tag="user", qualifier="bob", perms="rwx"),
    ]
    result = mgr.set_acl("/etc/passwd", new_entries)
    testbase.assert_equal(result.status, AclActionStatus.SUCCESS)

    setfacl_calls = [
        c for c in runner.calls
        if len(c["args"]) > 0 and c["args"][0] == "setfacl"
    ]
    testbase.assert_equal(len(setfacl_calls), 1)
    testbase.assert_true("-m" in setfacl_calls[0]["args"])
    testbase.assert_true("u:bob:rwx" in setfacl_calls[0]["args"])
    testbase.assert_true(setfacl_calls[0]["elevated"])
    return


def test_set_acl_skipped_when_entries_already_present():
    """
        set_acl() returns SKIPPED when the requested entries are
        already present with matching perms.
    """
    runner = _make_runner_with_acl_query(
        path="/etc/passwd", output=_GETFACL_OUTPUT_FILE,
    )
    mgr = AclManager(runner, AclManagerConfig())
    existing = [
        AclEntry(tag="user", qualifier="alice", perms="r-x"),
        AclEntry(tag="group", qualifier="devs", perms="rwx"),
    ]
    result = mgr.set_acl("/etc/passwd", existing)
    testbase.assert_equal(result.status, AclActionStatus.SKIPPED)

    setfacl_calls = [
        c for c in runner.calls
        if len(c["args"]) > 0 and c["args"][0] == "setfacl"
    ]
    testbase.assert_equal(len(setfacl_calls), 0)
    return


def test_set_acl_recursive_passes_dash_r():
    """
        set_acl(recursive=True) passes ``-R`` to ``setfacl``.
    """
    runner = _make_runner_with_acl_query(
        path="/srv/data", output=_GETFACL_OUTPUT_DIR,
    )
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=["setfacl"],
    )
    mgr = AclManager(runner, AclManagerConfig())
    new_entries = [
        AclEntry(tag="user", qualifier="bob", perms="rwx"),
    ]
    result = mgr.set_acl("/srv/data", new_entries, recursive=True)
    testbase.assert_equal(result.status, AclActionStatus.SUCCESS)

    setfacl_calls = [
        c for c in runner.calls
        if len(c["args"]) > 0 and c["args"][0] == "setfacl"
    ]
    testbase.assert_equal(len(setfacl_calls), 1)
    testbase.assert_true("-R" in setfacl_calls[0]["args"])
    return


def test_set_acl_dry_run_does_not_dispatch():
    """
        set_acl() with dry_run returns DRY_RUN and never invokes
        the ``setfacl`` runner call.
    """
    runner = _MockRunner()
    mgr = AclManager(runner, AclManagerConfig(dry_run=True))
    entries = [AclEntry(tag="user", qualifier="bob", perms="rwx")]
    result = mgr.set_acl("/etc/passwd", entries)
    testbase.assert_equal(result.status, AclActionStatus.DRY_RUN)

    setfacl_calls = [
        c for c in runner.calls
        if len(c["args"]) > 0 and c["args"][0] == "setfacl"
    ]
    testbase.assert_equal(len(setfacl_calls), 0)
    return


def test_set_acl_default_entry_renders_d_prefix():
    """
        set_acl() renders default entries with the ``d:`` prefix.
    """
    runner = _make_runner_with_acl_query(
        path="/srv/data", output=_GETFACL_OUTPUT_FILE,
    )
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=["setfacl"],
    )
    mgr = AclManager(runner, AclManagerConfig())
    new_entries = [
        AclEntry(
            tag="user", qualifier="bob", perms="rwx", is_default=True,
        ),
    ]
    result = mgr.set_acl("/srv/data", new_entries)
    testbase.assert_equal(result.status, AclActionStatus.SUCCESS)

    setfacl_calls = [
        c for c in runner.calls
        if len(c["args"]) > 0 and c["args"][0] == "setfacl"
    ]
    testbase.assert_equal(len(setfacl_calls), 1)
    testbase.assert_true("d:u:bob:rwx" in setfacl_calls[0]["args"])
    return


def test_set_acl_failure_propagates_runner_exit_code():
    """
        set_acl() returns FAILED with the runner's exit_code/stderr.
    """
    runner = _make_runner_with_acl_query(
        path="/etc/passwd", output=_GETFACL_OUTPUT_FILE,
    )
    runner.default_result = RunResult(
        exit_code=1, stdout="", stderr="setfacl: refused",
        command=["setfacl"],
    )
    mgr = AclManager(runner, AclManagerConfig())
    entries = [AclEntry(tag="user", qualifier="bob", perms="rwx")]
    result = mgr.set_acl("/etc/passwd", entries)
    testbase.assert_equal(result.status, AclActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 1)
    testbase.assert_equal(result.stderr, "setfacl: refused")
    return


# ----------------------------------------------------------------------
# remove_acl_entries / clear_acl / clear_default_acl
# ----------------------------------------------------------------------


def test_remove_acl_entries_dispatches_x_flag():
    """
        remove_acl_entries() dispatches ``setfacl -x`` with rendered
        principal-only tokens.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["setfacl"],
    ))
    mgr = AclManager(runner, AclManagerConfig(elevated=True))
    entries = [
        AclEntry(tag="user", qualifier="bob", perms=""),
    ]
    result = mgr.remove_acl_entries("/etc/passwd", entries)
    testbase.assert_equal(result.status, AclActionStatus.SUCCESS)

    setfacl_calls = [
        c for c in runner.calls
        if len(c["args"]) > 0 and c["args"][0] == "setfacl"
    ]
    testbase.assert_equal(len(setfacl_calls), 1)
    testbase.assert_true("-x" in setfacl_calls[0]["args"])
    testbase.assert_true("u:bob" in setfacl_calls[0]["args"])
    return


def test_clear_acl_dispatches_b_flag():
    """
        clear_acl() dispatches ``setfacl -b``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["setfacl"],
    ))
    mgr = AclManager(runner, AclManagerConfig())
    result = mgr.clear_acl("/etc/passwd")
    testbase.assert_equal(result.status, AclActionStatus.SUCCESS)

    setfacl_calls = [
        c for c in runner.calls
        if len(c["args"]) > 0 and c["args"][0] == "setfacl"
    ]
    testbase.assert_equal(len(setfacl_calls), 1)
    testbase.assert_true("-b" in setfacl_calls[0]["args"])
    return


def test_clear_default_acl_succeeds_for_directory():
    """
        clear_default_acl() dispatches ``setfacl -k`` on a directory.
    """
    runner = _MockRunner()
    runner.results[("stat", "-c", "%F", "/srv/data")] = _stat_dir_result()
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=["setfacl"],
    )
    mgr = AclManager(runner, AclManagerConfig())
    result = mgr.clear_default_acl("/srv/data")
    testbase.assert_equal(result.status, AclActionStatus.SUCCESS)

    setfacl_calls = [
        c for c in runner.calls
        if len(c["args"]) > 0 and c["args"][0] == "setfacl"
    ]
    testbase.assert_equal(len(setfacl_calls), 1)
    testbase.assert_true("-k" in setfacl_calls[0]["args"])
    return


def test_clear_default_acl_fails_for_non_directory():
    """
        clear_default_acl() returns FAILED when the path is not a
        directory and does not dispatch ``setfacl``.
    """
    runner = _MockRunner()
    runner.results[("stat", "-c", "%F", "/etc/passwd")] = _stat_file_result()
    mgr = AclManager(runner, AclManagerConfig())
    result = mgr.clear_default_acl("/etc/passwd")
    testbase.assert_equal(result.status, AclActionStatus.FAILED)

    setfacl_calls = [
        c for c in runner.calls
        if len(c["args"]) > 0 and c["args"][0] == "setfacl"
    ]
    testbase.assert_equal(len(setfacl_calls), 0)
    return


# ----------------------------------------------------------------------
# Extended attributes
# ----------------------------------------------------------------------


def test_query_attrs_parses_lsattr_output():
    """
        query_attrs() parses ``lsattr -d`` into FileAttributes.
    """
    runner = _MockRunner()
    runner.results[("lsattr", "-d", "/etc/resolv.conf")] = RunResult(
        exit_code=0, stdout=_LSATTR_OUTPUT_IMMUTABLE,
        stderr="", command=["lsattr"],
    )
    mgr = AclManager(runner, AclManagerConfig())
    attrs = mgr.query_attrs("/etc/resolv.conf")
    testbase.assert_true(attrs is not None)
    testbase.assert_true(attrs.immutable)
    testbase.assert_false(attrs.append_only)
    testbase.assert_equal(attrs.path, "/etc/resolv.conf")
    return


def test_query_attrs_returns_none_on_runner_failure():
    """
        query_attrs() returns None when the runner exits non-zero.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=1, stdout="", stderr="No such file",
        command=["lsattr"],
    ))
    mgr = AclManager(runner, AclManagerConfig())
    attrs = mgr.query_attrs("/nope")
    testbase.assert_true(attrs is None)
    return


def test_add_attrs_dispatches_chattr_plus():
    """
        add_attrs() dispatches ``chattr +<flags>`` when not already
        set.
    """
    runner = _MockRunner()
    runner.results[("lsattr", "-d", "/etc/resolv.conf")] = RunResult(
        exit_code=0, stdout=_LSATTR_OUTPUT_NONE,
        stderr="", command=["lsattr"],
    )
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=["chattr"],
    )
    mgr = AclManager(runner, AclManagerConfig(elevated=True))
    result = mgr.add_attrs("/etc/resolv.conf", "i")
    testbase.assert_equal(result.status, AclActionStatus.SUCCESS)

    chattr_calls = [
        c for c in runner.calls
        if len(c["args"]) > 0 and c["args"][0] == "chattr"
    ]
    testbase.assert_equal(len(chattr_calls), 1)
    testbase.assert_true("+i" in chattr_calls[0]["args"])
    return


def test_add_attrs_skipped_when_already_set():
    """
        add_attrs() returns SKIPPED when the requested flags are
        already set.
    """
    runner = _MockRunner()
    runner.results[("lsattr", "-d", "/etc/resolv.conf")] = RunResult(
        exit_code=0, stdout=_LSATTR_OUTPUT_IMMUTABLE,
        stderr="", command=["lsattr"],
    )
    mgr = AclManager(runner, AclManagerConfig())
    result = mgr.add_attrs("/etc/resolv.conf", "i")
    testbase.assert_equal(result.status, AclActionStatus.SKIPPED)
    chattr_calls = [
        c for c in runner.calls
        if len(c["args"]) > 0 and c["args"][0] == "chattr"
    ]
    testbase.assert_equal(len(chattr_calls), 0)
    return


def test_add_attrs_recursive_on_non_directory_returns_failed():
    """
        add_attrs(recursive=True) on a non-directory returns FAILED
        and does not dispatch ``chattr``.
    """
    runner = _MockRunner()
    runner.results[("stat", "-c", "%F", "/etc/resolv.conf")] = (
        _stat_file_result()
    )
    mgr = AclManager(runner, AclManagerConfig())
    result = mgr.add_attrs("/etc/resolv.conf", "i", recursive=True)
    testbase.assert_equal(result.status, AclActionStatus.FAILED)
    chattr_calls = [
        c for c in runner.calls
        if len(c["args"]) > 0 and c["args"][0] == "chattr"
    ]
    testbase.assert_equal(len(chattr_calls), 0)
    return


def test_add_attrs_recursive_on_directory_dispatches_dash_r():
    """
        add_attrs(recursive=True) on a directory passes ``-R``.
    """
    runner = _MockRunner()
    runner.results[("stat", "-c", "%F", "/srv/data")] = _stat_dir_result()
    runner.results[("lsattr", "-d", "/srv/data")] = RunResult(
        exit_code=0, stdout="------------------- /srv/data\n",
        stderr="", command=["lsattr"],
    )
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=["chattr"],
    )
    mgr = AclManager(runner, AclManagerConfig())
    result = mgr.add_attrs("/srv/data", "i", recursive=True)
    testbase.assert_equal(result.status, AclActionStatus.SUCCESS)

    chattr_calls = [
        c for c in runner.calls
        if len(c["args"]) > 0 and c["args"][0] == "chattr"
    ]
    testbase.assert_equal(len(chattr_calls), 1)
    testbase.assert_true("-R" in chattr_calls[0]["args"])
    return


def test_remove_attrs_dispatches_chattr_minus():
    """
        remove_attrs() dispatches ``chattr -<flags>``.
    """
    runner = _MockRunner()
    runner.results[("lsattr", "-d", "/etc/resolv.conf")] = RunResult(
        exit_code=0, stdout=_LSATTR_OUTPUT_IMMUTABLE,
        stderr="", command=["lsattr"],
    )
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=["chattr"],
    )
    mgr = AclManager(runner, AclManagerConfig())
    result = mgr.remove_attrs("/etc/resolv.conf", "i")
    testbase.assert_equal(result.status, AclActionStatus.SUCCESS)

    chattr_calls = [
        c for c in runner.calls
        if len(c["args"]) > 0 and c["args"][0] == "chattr"
    ]
    testbase.assert_equal(len(chattr_calls), 1)
    testbase.assert_true("-i" in chattr_calls[0]["args"])
    return


def test_set_attrs_dispatches_chattr_equal():
    """
        set_attrs() dispatches ``chattr =<flags>``.
    """
    runner = _MockRunner()
    runner.results[("lsattr", "-d", "/etc/resolv.conf")] = RunResult(
        exit_code=0, stdout=_LSATTR_OUTPUT_NONE,
        stderr="", command=["lsattr"],
    )
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=["chattr"],
    )
    mgr = AclManager(runner, AclManagerConfig())
    result = mgr.set_attrs("/etc/resolv.conf", "ia")
    testbase.assert_equal(result.status, AclActionStatus.SUCCESS)

    chattr_calls = [
        c for c in runner.calls
        if len(c["args"]) > 0 and c["args"][0] == "chattr"
    ]
    testbase.assert_equal(len(chattr_calls), 1)
    testbase.assert_true("=ia" in chattr_calls[0]["args"])
    return


def test_set_attrs_skipped_when_mask_matches_exactly():
    """
        set_attrs() returns SKIPPED when the live mask exactly equals
        the requested flag set.
    """
    runner = _MockRunner()
    runner.results[("lsattr", "-d", "/etc/resolv.conf")] = RunResult(
        exit_code=0, stdout=_LSATTR_OUTPUT_IMMUTABLE,
        stderr="", command=["lsattr"],
    )
    mgr = AclManager(runner, AclManagerConfig())
    result = mgr.set_attrs("/etc/resolv.conf", "i")
    testbase.assert_equal(result.status, AclActionStatus.SKIPPED)
    chattr_calls = [
        c for c in runner.calls
        if len(c["args"]) > 0 and c["args"][0] == "chattr"
    ]
    testbase.assert_equal(len(chattr_calls), 0)
    return


def test_enumerate_attrs_parses_recursive_output():
    """
        enumerate_attrs() builds a dict from ``lsattr -R`` output.
    """
    runner = _MockRunner()
    runner.results[("lsattr", "-R", "/srv/data")] = RunResult(
        exit_code=0, stdout=_LSATTR_RECURSIVE_OUTPUT,
        stderr="", command=["lsattr"],
    )
    mgr = AclManager(runner, AclManagerConfig())
    attrs_map = mgr.enumerate_attrs("/srv/data")
    testbase.assert_type(attrs_map, dict)
    testbase.assert_equal(len(attrs_map), 3)
    testbase.assert_true("/srv/data/locked.cfg" in attrs_map)
    testbase.assert_true(attrs_map["/srv/data/locked.cfg"].immutable)
    testbase.assert_true(attrs_map["/srv/data/append.log"].append_only)
    testbase.assert_false(attrs_map["/srv/data/normal.txt"].immutable)
    return


# ----------------------------------------------------------------------
# File capabilities
# ----------------------------------------------------------------------


def test_query_caps_parses_getcap_output():
    """
        query_caps() parses ``getcap`` into a FileCapabilities.
    """
    runner = _MockRunner()
    runner.results[("getcap", "/usr/bin/ping")] = RunResult(
        exit_code=0, stdout=_GETCAP_OUTPUT_PING,
        stderr="", command=["getcap"],
    )
    mgr = AclManager(runner, AclManagerConfig())
    caps = mgr.query_caps("/usr/bin/ping")
    testbase.assert_true(caps is not None)
    testbase.assert_equal(caps.path, "/usr/bin/ping")
    testbase.assert_true("cap_net_raw" in caps.effective)
    testbase.assert_true("cap_net_raw" in caps.permitted)
    testbase.assert_equal(len(caps.inheritable), 0)
    return


def test_query_caps_returns_empty_when_no_caps_set():
    """
        query_caps() returns an empty FileCapabilities when getcap
        succeeds but emits no output.
    """
    runner = _MockRunner()
    runner.results[("getcap", "/bin/ls")] = RunResult(
        exit_code=0, stdout="", stderr="",
        command=["getcap"],
    )
    mgr = AclManager(runner, AclManagerConfig())
    caps = mgr.query_caps("/bin/ls")
    testbase.assert_true(caps is not None)
    testbase.assert_equal(len(caps.effective), 0)
    testbase.assert_equal(len(caps.permitted), 0)
    return


def test_set_caps_dispatches_setcap():
    """
        set_caps() dispatches ``setcap '<spec>' <path>``.
    """
    runner = _MockRunner()
    runner.results[("getcap", "/usr/bin/foo")] = RunResult(
        exit_code=0, stdout="", stderr="", command=["getcap"],
    )
    runner.default_result = RunResult(
        exit_code=0, stdout="", stderr="", command=["setcap"],
    )
    mgr = AclManager(runner, AclManagerConfig(elevated=True))
    result = mgr.set_caps("/usr/bin/foo", "cap_net_raw=ep")
    testbase.assert_equal(result.status, AclActionStatus.SUCCESS)

    setcap_calls = [
        c for c in runner.calls
        if len(c["args"]) > 0 and c["args"][0] == "setcap"
    ]
    testbase.assert_equal(len(setcap_calls), 1)
    testbase.assert_true("cap_net_raw=ep" in setcap_calls[0]["args"])
    testbase.assert_true(setcap_calls[0]["elevated"])
    return


def test_set_caps_skipped_when_already_matching():
    """
        set_caps() returns SKIPPED when the canonical form already
        matches.  The runner's getcap output uses ``ep``; the request
        uses ``pe`` -- canonicalisation collapses both to ``ep``.
    """
    runner = _MockRunner()
    runner.results[("getcap", "/usr/bin/ping")] = RunResult(
        exit_code=0, stdout=_GETCAP_OUTPUT_PING,
        stderr="", command=["getcap"],
    )
    mgr = AclManager(runner, AclManagerConfig())
    result = mgr.set_caps("/usr/bin/ping", "cap_net_raw=pe")
    testbase.assert_equal(result.status, AclActionStatus.SKIPPED)
    setcap_calls = [
        c for c in runner.calls
        if len(c["args"]) > 0 and c["args"][0] == "setcap"
    ]
    testbase.assert_equal(len(setcap_calls), 0)
    return


def test_clear_caps_dispatches_setcap_dash_r():
    """
        clear_caps() dispatches ``setcap -r <path>``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["setcap"],
    ))
    mgr = AclManager(runner, AclManagerConfig())
    result = mgr.clear_caps("/usr/bin/ping")
    testbase.assert_equal(result.status, AclActionStatus.SUCCESS)
    setcap_calls = [
        c for c in runner.calls
        if len(c["args"]) > 0 and c["args"][0] == "setcap"
    ]
    testbase.assert_equal(len(setcap_calls), 1)
    testbase.assert_equal(
        setcap_calls[0]["args"], ["setcap", "-r", "/usr/bin/ping"],
    )
    return


def test_enumerate_caps_returns_list_of_capabilities():
    """
        enumerate_caps() parses ``getcap -r`` into a list.
    """
    runner = _MockRunner()
    runner.results[("getcap", "-r", "/usr")] = RunResult(
        exit_code=0, stdout=_GETCAP_RECURSIVE_OUTPUT,
        stderr="", command=["getcap"],
    )
    mgr = AclManager(runner, AclManagerConfig())
    caps_list = mgr.enumerate_caps("/usr")
    testbase.assert_type(caps_list, list)
    testbase.assert_equal(len(caps_list), 2)
    paths = [c.path for c in caps_list]
    testbase.assert_true("/usr/bin/ping" in paths)
    testbase.assert_true("/usr/bin/foo" in paths)
    return


# ----------------------------------------------------------------------
# Dry-run sweeps for write verbs
# ----------------------------------------------------------------------


def test_dry_run_sweep_no_mutating_dispatches():
    """
        Dry-run mode short-circuits every mutating verb; no chattr,
        setfacl, or setcap calls are made.
    """
    runner = _MockRunner()
    mgr = AclManager(runner, AclManagerConfig(dry_run=True))

    set_acl_result = mgr.set_acl(
        "/etc/passwd",
        [AclEntry(tag="user", qualifier="bob", perms="rwx")],
    )
    remove_result = mgr.remove_acl_entries(
        "/etc/passwd",
        [AclEntry(tag="user", qualifier="bob", perms="")],
    )
    clear_result = mgr.clear_acl("/etc/passwd")
    add_attr_result = mgr.add_attrs("/etc/resolv.conf", "i")
    remove_attr_result = mgr.remove_attrs("/etc/resolv.conf", "i")
    set_attr_result = mgr.set_attrs("/etc/resolv.conf", "i")
    set_caps_result = mgr.set_caps("/usr/bin/foo", "cap_net_raw=ep")
    clear_caps_result = mgr.clear_caps("/usr/bin/foo")

    statuses = [
        set_acl_result.status,
        remove_result.status,
        clear_result.status,
        add_attr_result.status,
        remove_attr_result.status,
        set_attr_result.status,
        set_caps_result.status,
        clear_caps_result.status,
    ]
    for status in statuses:
        testbase.assert_equal(status, AclActionStatus.DRY_RUN)

    mutating = [
        c for c in runner.calls
        if len(c["args"]) > 0 and c["args"][0] in (
            "setfacl", "chattr", "setcap",
        )
    ]
    testbase.assert_equal(len(mutating), 0)
    return


# ----------------------------------------------------------------------
# Failure-mapping sweep
# ----------------------------------------------------------------------


def test_failure_mapping_clear_caps():
    """
        Non-zero exit on a write surfaces as FAILED with exit_code,
        stdout, and stderr populated.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=2, stdout="boom", stderr="permission denied",
        command=["setcap"],
    ))
    mgr = AclManager(runner, AclManagerConfig())
    result = mgr.clear_caps("/usr/bin/ping")
    testbase.assert_equal(result.status, AclActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 2)
    testbase.assert_equal(result.stderr, "permission denied")
    return
