# Tests for the FilesystemQuotaManager facade.

from dataclasses import dataclass, field
from typing import Callable, Optional

from automojo import testbase

from automojo.sysman.linux.redhat.filesystemquotas import (
    FilesystemQuotaActionResult,
    FilesystemQuotaManager,
    FilesystemQuotaManagerConfig,
    GracePeriod,
    ProjectDefinition,
    QuotaActionStatus,
    QuotaEntry,
    QuotaEnforcement,
    QuotaFilesystemNotMountedError,
    QuotaLimits,
    QuotaQueryError,
    QuotaTarget,
    QuotaUnsupportedOperationError,
    _parse_quotaon_status,
    _parse_repquota_output,
    _parse_xfs_state,
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


# ----------------------------------------------------------------------
# Seed helpers
# ----------------------------------------------------------------------


def _seed_findmnt_mounted(
    runner: _MockRunner,
    *,
    mount: str,
    fstype: str = "ext4",
) -> None:
    """
        Seed ``findmnt -n -o FSTYPE <mount>`` to return ``fstype``.
    """
    key = ("findmnt", "-n", "-o", "FSTYPE", mount)
    runner.results[key] = RunResult(
        exit_code=0,
        stdout=fstype + "\n",
        stderr="",
        command=list(key),
    )
    return


def _seed_findmnt_unmounted(
    runner: _MockRunner,
    *,
    mount: str,
) -> None:
    """
        Seed ``findmnt`` to fail for ``mount``.
    """
    key = ("findmnt", "-n", "-o", "FSTYPE", mount)
    runner.results[key] = RunResult(
        exit_code=1,
        stdout="",
        stderr="",
        command=list(key),
    )
    return


def _seed_quotaon_status(
    runner: _MockRunner,
    *,
    mount: str,
    user_on: bool,
    group_on: bool,
) -> None:
    """
        Seed ``quotaon -p <mount>`` output.
    """
    user_state = "on" if user_on else "off"
    group_state = "on" if group_on else "off"
    body = "".join([
        "user quota on {} (/dev/sda1) is {}\n".format(mount, user_state),
        "group quota on {} (/dev/sda1) is {}\n".format(mount, group_state),
    ])
    key = ("quotaon", "-p", mount)
    runner.results[key] = RunResult(
        exit_code=0,
        stdout=body,
        stderr="",
        command=list(key),
    )
    return


def _seed_quota_v_user(
    runner: _MockRunner,
    *,
    user: str,
    mount: str,
    blocks_used: int,
    block_soft: int,
    block_hard: int,
    inodes_used: int = 0,
    inode_soft: int = 0,
    inode_hard: int = 0,
) -> None:
    """
        Seed ``quota -v -u <user>`` output for one row on ``mount``.
    """
    body_lines = [
        "Disk quotas for user {} (uid 1001):".format(user),
        "     Filesystem   blocks   quota   limit   grace   files   quota   limit   grace",
        "       {}   {}  {}  {}        {}       {}       {}".format(
            mount, blocks_used, block_soft, block_hard,
            inodes_used, inode_soft, inode_hard,
        ),
    ]
    body = "\n".join(body_lines) + "\n"
    key = ("quota", "-v", "-u", user)
    runner.results[key] = RunResult(
        exit_code=0,
        stdout=body,
        stderr="",
        command=list(key),
    )
    return


def _seed_quota_v_no_record(
    runner: _MockRunner,
    *,
    user: str,
) -> None:
    """
        Seed ``quota -v -u`` with empty body and exit code 2.
    """
    key = ("quota", "-v", "-u", user)
    runner.results[key] = RunResult(
        exit_code=2,
        stdout="",
        stderr="",
        command=list(key),
    )
    return


# ----------------------------------------------------------------------
# _fstype_of / mount validation
# ----------------------------------------------------------------------


def test_fstype_of_returns_findmnt_value():
    """
        ``_fstype_of`` returns the trimmed ``findmnt`` body.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="xfs")
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    fstype = manager._fstype_of(mount="/mnt/data")
    testbase.assert_equal(fstype, "xfs")
    return


def test_fstype_of_raises_when_not_mounted():
    """
        ``_fstype_of`` raises :class:`QuotaFilesystemNotMountedError`
        when ``findmnt`` returns non-zero.
    """
    runner = _MockRunner()
    _seed_findmnt_unmounted(runner, mount="/mnt/missing")
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    raised = False
    try:
        manager._fstype_of(mount="/mnt/missing")
    except QuotaFilesystemNotMountedError:
        raised = True
    testbase.assert_equal(raised, True)
    return


def test_enable_raises_when_unmounted():
    """
        ``enable`` raises before invoking any quota tool when the
        mountpoint is not mounted.
    """
    runner = _MockRunner()
    _seed_findmnt_unmounted(runner, mount="/mnt/missing")
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    raised = False
    try:
        manager.enable(mount="/mnt/missing")
    except QuotaFilesystemNotMountedError:
        raised = True
    testbase.assert_equal(raised, True)
    quotaon_calls = [c for c in runner.calls if c["args"][0] == "quotaon"]
    testbase.assert_equal(len(quotaon_calls), 0)
    return


# ----------------------------------------------------------------------
# query_enforcement
# ----------------------------------------------------------------------


def test_query_enforcement_ext4_parses_quotaon():
    """
        ``query_enforcement`` on ext4 dispatches ``quotaon -p`` and
        parses the output.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="ext4")
    _seed_quotaon_status(
        runner, mount="/mnt/data", user_on=True, group_on=False,
    )
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    state = manager.query_enforcement(mount="/mnt/data")
    testbase.assert_equal(state.fstype, "ext4")
    testbase.assert_equal(state.user_enforced, True)
    testbase.assert_equal(state.group_enforced, False)
    testbase.assert_equal(state.project_enforced, False)
    return


def test_query_enforcement_xfs_parses_state():
    """
        ``query_enforcement`` on XFS dispatches ``xfs_quota state``
        and parses the output.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/xfs", fstype="xfs")
    state_body = "\n".join([
        "User quota state on /mnt/xfs (/dev/sdb1)",
        "  Accounting: ON",
        "  Enforcement: ON",
        "Group quota state on /mnt/xfs (/dev/sdb1)",
        "  Accounting: ON",
        "  Enforcement: OFF",
        "Project quota state on /mnt/xfs (/dev/sdb1)",
        "  Accounting: ON",
        "  Enforcement: ON",
    ]) + "\n"
    runner.results[("xfs_quota", "-x", "-c", "state", "/mnt/xfs")] = RunResult(
        exit_code=0, stdout=state_body, stderr="", command=["xfs_quota"],
    )
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    state = manager.query_enforcement(mount="/mnt/xfs")
    testbase.assert_equal(state.fstype, "xfs")
    testbase.assert_equal(state.user_enforced, True)
    testbase.assert_equal(state.group_enforced, False)
    testbase.assert_equal(state.project_enforced, True)
    return


# ----------------------------------------------------------------------
# enable / disable
# ----------------------------------------------------------------------


def test_enable_ext4_dispatches_quotaon():
    """
        ``enable`` on ext4 dispatches ``quotaon -u -g <mount>`` when
        quotas are currently off.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="ext4")
    _seed_quotaon_status(
        runner, mount="/mnt/data", user_on=False, group_on=False,
    )
    manager = FilesystemQuotaManager(
        runner,
        FilesystemQuotaManagerConfig(elevated=True),
    )
    result = manager.enable(mount="/mnt/data")
    testbase.assert_equal(result.status, QuotaActionStatus.SUCCESS)
    quotaon_writes = [
        c for c in runner.calls
        if c["args"][0] == "quotaon" and "-p" not in c["args"]
    ]
    testbase.assert_equal(len(quotaon_writes), 1)
    testbase.assert_equal(
        quotaon_writes[0]["args"],
        ["quotaon", "-u", "-g", "/mnt/data"],
    )
    testbase.assert_equal(quotaon_writes[0]["elevated"], True)
    return


def test_enable_ext4_skipped_when_already_on():
    """
        ``enable`` returns SKIPPED when both user and group quotas
        are already on.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="ext4")
    _seed_quotaon_status(
        runner, mount="/mnt/data", user_on=True, group_on=True,
    )
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    result = manager.enable(mount="/mnt/data")
    testbase.assert_equal(result.status, QuotaActionStatus.SKIPPED)
    quotaon_writes = [
        c for c in runner.calls
        if c["args"][0] == "quotaon" and "-p" not in c["args"]
    ]
    testbase.assert_equal(len(quotaon_writes), 0)
    return


def test_disable_ext4_dispatches_quotaoff():
    """
        ``disable`` on ext4 dispatches ``quotaoff -u -g <mount>``.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="ext4")
    _seed_quotaon_status(
        runner, mount="/mnt/data", user_on=True, group_on=True,
    )
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    result = manager.disable(mount="/mnt/data")
    testbase.assert_equal(result.status, QuotaActionStatus.SUCCESS)
    off_calls = [c for c in runner.calls if c["args"][0] == "quotaoff"]
    testbase.assert_equal(len(off_calls), 1)
    testbase.assert_equal(
        off_calls[0]["args"],
        ["quotaoff", "-u", "-g", "/mnt/data"],
    )
    return


def test_disable_ext4_skipped_when_already_off():
    """
        ``disable`` returns SKIPPED when both user and group quotas
        are already off.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="ext4")
    _seed_quotaon_status(
        runner, mount="/mnt/data", user_on=False, group_on=False,
    )
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    result = manager.disable(mount="/mnt/data")
    testbase.assert_equal(result.status, QuotaActionStatus.SKIPPED)
    return


def test_enable_xfs_dispatches_xfs_quota():
    """
        ``enable`` on XFS dispatches ``xfs_quota -x -c "enable -ug"``.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/xfs", fstype="xfs")
    state_body = "\n".join([
        "User quota state on /mnt/xfs (/dev/sdb1)",
        "  Accounting: OFF",
        "  Enforcement: OFF",
        "Group quota state on /mnt/xfs (/dev/sdb1)",
        "  Accounting: OFF",
        "  Enforcement: OFF",
        "Project quota state on /mnt/xfs (/dev/sdb1)",
        "  Accounting: OFF",
        "  Enforcement: OFF",
    ]) + "\n"
    runner.results[("xfs_quota", "-x", "-c", "state", "/mnt/xfs")] = RunResult(
        exit_code=0, stdout=state_body, stderr="", command=["xfs_quota"],
    )
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    result = manager.enable(mount="/mnt/xfs")
    testbase.assert_equal(result.status, QuotaActionStatus.SUCCESS)
    enable_calls = [
        c for c in runner.calls
        if c["args"][0] == "xfs_quota" and "enable" in " ".join(c["args"])
    ]
    testbase.assert_equal(len(enable_calls), 1)
    testbase.assert_equal(
        enable_calls[0]["args"],
        ["xfs_quota", "-x", "-c", "enable -ug", "/mnt/xfs"],
    )
    return


# ----------------------------------------------------------------------
# quotacheck
# ----------------------------------------------------------------------


def test_quotacheck_ext4_dispatches():
    """
        ``quotacheck`` on ext4 dispatches ``quotacheck -ug <mount>``.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="ext4")
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    result = manager.quotacheck(mount="/mnt/data")
    testbase.assert_equal(result.status, QuotaActionStatus.SUCCESS)
    check_calls = [c for c in runner.calls if c["args"][0] == "quotacheck"]
    testbase.assert_equal(len(check_calls), 1)
    testbase.assert_equal(check_calls[0]["args"], ["quotacheck", "-ug", "/mnt/data"])
    return


def test_quotacheck_ext4_with_remount_ro_false_uses_ugm():
    """
        ``quotacheck(remount_ro=False)`` adds ``-m`` to the flags.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="ext4")
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    manager.quotacheck(mount="/mnt/data", remount_ro=False)
    check_calls = [c for c in runner.calls if c["args"][0] == "quotacheck"]
    testbase.assert_equal(check_calls[0]["args"], ["quotacheck", "-ugm", "/mnt/data"])
    return


def test_quotacheck_xfs_raises_unsupported():
    """
        ``quotacheck`` on XFS raises
        :class:`QuotaUnsupportedOperationError` and does not
        dispatch the command.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/xfs", fstype="xfs")
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    raised = False
    try:
        manager.quotacheck(mount="/mnt/xfs")
    except QuotaUnsupportedOperationError:
        raised = True
    testbase.assert_equal(raised, True)
    check_calls = [c for c in runner.calls if c["args"][0] == "quotacheck"]
    testbase.assert_equal(len(check_calls), 0)
    return


# ----------------------------------------------------------------------
# set_user_quota / set_group_quota / set_project_quota
# ----------------------------------------------------------------------


def test_set_user_quota_ext4_dispatches_setquota():
    """
        ``set_user_quota`` on ext4 dispatches the canonical
        ``setquota -u <user> ...`` argv.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="ext4")
    _seed_quota_v_no_record(runner, user="alice")
    manager = FilesystemQuotaManager(
        runner,
        FilesystemQuotaManagerConfig(elevated=True),
    )
    limits = QuotaLimits(
        block_soft=204800, block_hard=409600,
        inode_soft=1000, inode_hard=2000,
    )
    result = manager.set_user_quota(
        mount="/mnt/data", username="alice", limits=limits,
    )
    testbase.assert_equal(result.status, QuotaActionStatus.SUCCESS)
    set_calls = [c for c in runner.calls if c["args"][0] == "setquota"]
    testbase.assert_equal(len(set_calls), 1)
    testbase.assert_equal(
        set_calls[0]["args"],
        [
            "setquota", "-u", "alice",
            "204800", "409600", "1000", "2000", "/mnt/data",
        ],
    )
    testbase.assert_equal(set_calls[0]["elevated"], True)
    return


def test_set_user_quota_skipped_when_already_matching():
    """
        ``set_user_quota`` returns SKIPPED when the existing
        record already carries the requested limits.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="ext4")
    _seed_quota_v_user(
        runner,
        user="alice",
        mount="/mnt/data",
        blocks_used=100,
        block_soft=204800,
        block_hard=409600,
        inodes_used=10,
        inode_soft=1000,
        inode_hard=2000,
    )
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    limits = QuotaLimits(
        block_soft=204800, block_hard=409600,
        inode_soft=1000, inode_hard=2000,
    )
    result = manager.set_user_quota(
        mount="/mnt/data", username="alice", limits=limits,
    )
    testbase.assert_equal(result.status, QuotaActionStatus.SKIPPED)
    set_calls = [c for c in runner.calls if c["args"][0] == "setquota"]
    testbase.assert_equal(len(set_calls), 0)
    return


def test_set_user_quota_dry_run_does_not_dispatch():
    """
        ``set_user_quota`` with ``dry_run=True`` returns DRY_RUN
        and does not dispatch the runner.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="ext4")
    manager = FilesystemQuotaManager(
        runner,
        FilesystemQuotaManagerConfig(dry_run=True),
    )
    limits = QuotaLimits(
        block_soft=1, block_hard=2, inode_soft=3, inode_hard=4,
    )
    result = manager.set_user_quota(
        mount="/mnt/data", username="bob", limits=limits,
    )
    testbase.assert_equal(result.status, QuotaActionStatus.DRY_RUN)
    testbase.assert_equal(
        result.command,
        ["setquota", "-u", "bob", "1", "2", "3", "4", "/mnt/data"],
    )
    set_calls = [c for c in runner.calls if c["args"][0] == "setquota"]
    testbase.assert_equal(len(set_calls), 0)
    return


def test_set_group_quota_ext4_dispatches_setquota():
    """
        ``set_group_quota`` on ext4 dispatches ``setquota -g``.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="ext4")
    runner.results[("quota", "-v", "-g", "staff")] = RunResult(
        exit_code=2, stdout="", stderr="", command=["quota"],
    )
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    limits = QuotaLimits(
        block_soft=10, block_hard=20, inode_soft=30, inode_hard=40,
    )
    result = manager.set_group_quota(
        mount="/mnt/data", groupname="staff", limits=limits,
    )
    testbase.assert_equal(result.status, QuotaActionStatus.SUCCESS)
    set_calls = [c for c in runner.calls if c["args"][0] == "setquota"]
    testbase.assert_equal(
        set_calls[0]["args"],
        ["setquota", "-g", "staff", "10", "20", "30", "40", "/mnt/data"],
    )
    return


def test_set_user_quota_xfs_dispatches_xfs_quota():
    """
        ``set_user_quota`` on XFS dispatches ``xfs_quota -x -c
        "limit -u ..."``.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/xfs", fstype="xfs")
    # Empty xfs_quota report -u so the SKIPPED guard doesn't fire.
    runner.results[("xfs_quota", "-x", "-c", "report -u", "/mnt/xfs")] = RunResult(
        exit_code=0, stdout="", stderr="", command=["xfs_quota"],
    )
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    limits = QuotaLimits(
        block_soft=100, block_hard=200, inode_soft=300, inode_hard=400,
    )
    result = manager.set_user_quota(
        mount="/mnt/xfs", username="alice", limits=limits,
    )
    testbase.assert_equal(result.status, QuotaActionStatus.SUCCESS)
    limit_calls = [
        c for c in runner.calls
        if c["args"][0] == "xfs_quota"
        and len(c["args"]) >= 4
        and c["args"][3].startswith("limit ")
    ]
    testbase.assert_equal(len(limit_calls), 1)
    inner = limit_calls[0]["args"][3]
    testbase.assert_true("limit -u" in inner)
    testbase.assert_true("bsoft=100" in inner)
    testbase.assert_true("bhard=200" in inner)
    testbase.assert_true("isoft=300" in inner)
    testbase.assert_true("ihard=400" in inner)
    testbase.assert_true(inner.endswith("alice"))
    return


def test_set_project_quota_on_non_xfs_raises_unsupported():
    """
        ``set_project_quota`` on ext4 raises
        :class:`QuotaUnsupportedOperationError`.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="ext4")
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    limits = QuotaLimits(
        block_soft=1, block_hard=2, inode_soft=3, inode_hard=4,
    )
    raised = False
    try:
        manager.set_project_quota(
            mount="/mnt/data", project_name="proj1", limits=limits,
        )
    except QuotaUnsupportedOperationError:
        raised = True
    testbase.assert_equal(raised, True)
    return


# ----------------------------------------------------------------------
# query_user / query_group / list_all
# ----------------------------------------------------------------------


def test_query_user_parses_quota_v_output():
    """
        ``query_user`` parses ``quota -v -u <user>`` output into a
        :class:`QuotaEntry`.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="ext4")
    _seed_quota_v_user(
        runner,
        user="alice",
        mount="/mnt/data",
        blocks_used=1024,
        block_soft=2048,
        block_hard=4096,
        inodes_used=10,
        inode_soft=20,
        inode_hard=40,
    )
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    entry = manager.query_user(mount="/mnt/data", username="alice")
    testbase.assert_not_equal(entry, None)
    testbase.assert_equal(entry.name, "alice")
    testbase.assert_equal(entry.target, QuotaTarget.USER)
    testbase.assert_equal(entry.blocks_used, 1024)
    testbase.assert_equal(entry.block_soft, 2048)
    testbase.assert_equal(entry.block_hard, 4096)
    testbase.assert_equal(entry.inodes_used, 10)
    testbase.assert_equal(entry.inode_soft, 20)
    testbase.assert_equal(entry.inode_hard, 40)
    return


def test_query_user_unknown_returns_none():
    """
        ``query_user`` returns ``None`` when ``quota`` reports no
        record for the principal.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="ext4")
    _seed_quota_v_no_record(runner, user="ghost")
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    entry = manager.query_user(mount="/mnt/data", username="ghost")
    testbase.assert_equal(entry, None)
    return


def test_list_all_ext4_parses_repquota_output():
    """
        ``list_all(target=USER)`` on ext4 dispatches ``repquota -u``
        and parses the rows.
    """
    body = "\n".join([
        "*** Report for user quotas on device /dev/sda1",
        "Block grace time: 7days; Inode grace time: 7days",
        "                        Block limits                File limits",
        "User            used    soft    hard  grace    used  soft  hard  grace",
        "----------------------------------------------------------------------",
        "root      --       8       0       0              2     0     0",
        "alice     --   102400  204800  409600           1234     0     0",
    ]) + "\n"
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="ext4")
    runner.results[("repquota", "-u", "/mnt/data")] = RunResult(
        exit_code=0, stdout=body, stderr="", command=["repquota"],
    )
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    entries = manager.list_all(mount="/mnt/data", target=QuotaTarget.USER)
    testbase.assert_equal(len(entries), 2)
    alice = entries[1]
    testbase.assert_equal(alice.name, "alice")
    testbase.assert_equal(alice.blocks_used, 102400)
    testbase.assert_equal(alice.block_soft, 204800)
    testbase.assert_equal(alice.block_hard, 409600)
    return


def test_list_all_xfs_parses_xfs_report():
    """
        ``list_all`` on XFS dispatches ``xfs_quota report -u`` and
        parses the rows.
    """
    body = "\n".join([
        "User quota on /mnt/xfs (/dev/sdb1)",
        "                        Blocks",
        "User ID      Used   Soft   Hard Warn/Grace",
        "---------- --------------------------------",
        "root          512      0      0  00 [------]",
        "alice      102400 204800 409600  00 [------]",
    ]) + "\n"
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/xfs", fstype="xfs")
    runner.results[("xfs_quota", "-x", "-c", "report -u", "/mnt/xfs")] = RunResult(
        exit_code=0, stdout=body, stderr="", command=["xfs_quota"],
    )
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    entries = manager.list_all(mount="/mnt/xfs", target=QuotaTarget.USER)
    testbase.assert_equal(len(entries), 2)
    testbase.assert_equal(entries[0].name, "root")
    testbase.assert_equal(entries[1].name, "alice")
    testbase.assert_equal(entries[1].block_soft, 204800)
    testbase.assert_equal(entries[1].block_hard, 409600)
    return


def test_list_all_failure_raises_query_error():
    """
        ``list_all`` raises :class:`QuotaQueryError` when the
        underlying tool returns non-zero.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="ext4")
    runner.results[("repquota", "-u", "/mnt/data")] = RunResult(
        exit_code=1, stdout="", stderr="boom",
        command=["repquota"],
    )
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    raised = False
    try:
        manager.list_all(mount="/mnt/data", target=QuotaTarget.USER)
    except QuotaQueryError:
        raised = True
    testbase.assert_equal(raised, True)
    return


# ----------------------------------------------------------------------
# Grace periods
# ----------------------------------------------------------------------


def test_set_grace_period_ext4_dispatches_setquota_t():
    """
        ``set_grace_period`` on ext4 dispatches
        ``setquota -u -t <bs> <is> <mount>``.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="ext4")
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    result = manager.set_grace_period(
        mount="/mnt/data",
        target=QuotaTarget.USER,
        block_seconds=604800,
        inode_seconds=604800,
    )
    testbase.assert_equal(result.status, QuotaActionStatus.SUCCESS)
    set_calls = [c for c in runner.calls if c["args"][0] == "setquota"]
    testbase.assert_equal(
        set_calls[0]["args"],
        ["setquota", "-u", "-t", "604800", "604800", "/mnt/data"],
    )
    return


def test_get_grace_period_ext4_parses_repquota_t():
    """
        ``get_grace_period`` on ext4 parses
        ``repquota -u -t`` block / inode times.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="ext4")
    body = "Block grace time: 7days; Inode grace time: 7days\n"
    runner.results[("repquota", "-u", "-t", "/mnt/data")] = RunResult(
        exit_code=0, stdout=body, stderr="", command=["repquota"],
    )
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    grace = manager.get_grace_period(
        mount="/mnt/data",
        target=QuotaTarget.USER,
    )
    testbase.assert_equal(grace.block_seconds, 7 * 86400)
    testbase.assert_equal(grace.inode_seconds, 7 * 86400)
    return


def test_get_grace_period_xfs_parses_timer():
    """
        ``get_grace_period`` on XFS parses ``xfs_quota timer``.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/xfs", fstype="xfs")
    body = "\n".join([
        "Blocks grace time: 7days",
        "Inodes grace time: 1day",
    ]) + "\n"
    runner.results[("xfs_quota", "-x", "-c", "timer -u", "/mnt/xfs")] = RunResult(
        exit_code=0, stdout=body, stderr="", command=["xfs_quota"],
    )
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    grace = manager.get_grace_period(
        mount="/mnt/xfs",
        target=QuotaTarget.USER,
    )
    testbase.assert_equal(grace.block_seconds, 7 * 86400)
    testbase.assert_equal(grace.inode_seconds, 1 * 86400)
    return


# ----------------------------------------------------------------------
# XFS project plumbing
# ----------------------------------------------------------------------


def test_define_project_writes_both_files():
    """
        ``define_project`` writes both ``/etc/projects`` and
        ``/etc/projid`` when neither already carries the entry.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/projects")] = RunResult(
        exit_code=0, stdout="", stderr="", command=["cat"],
    )
    runner.results[("cat", "/etc/projid")] = RunResult(
        exit_code=0, stdout="", stderr="", command=["cat"],
    )
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    result = manager.define_project(
        project_id=42,
        project_name="archive",
        paths=["/mnt/xfs/archive"],
    )
    testbase.assert_equal(result.status, QuotaActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 2)
    projects_writes = [c for c in tee_calls if c["args"][1] == "/etc/projects"]
    projid_writes = [c for c in tee_calls if c["args"][1] == "/etc/projid"]
    testbase.assert_equal(len(projects_writes), 1)
    testbase.assert_equal(len(projid_writes), 1)
    testbase.assert_true("42:/mnt/xfs/archive" in projects_writes[0]["stdin"])
    testbase.assert_true("archive:42" in projid_writes[0]["stdin"])
    return


def test_define_project_skipped_when_already_defined():
    """
        ``define_project`` returns SKIPPED when both files already
        carry the matching entry.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/projects")] = RunResult(
        exit_code=0,
        stdout="42:/mnt/xfs/archive\n",
        stderr="",
        command=["cat"],
    )
    runner.results[("cat", "/etc/projid")] = RunResult(
        exit_code=0,
        stdout="archive:42\n",
        stderr="",
        command=["cat"],
    )
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    result = manager.define_project(
        project_id=42,
        project_name="archive",
        paths=["/mnt/xfs/archive"],
    )
    testbase.assert_equal(result.status, QuotaActionStatus.SKIPPED)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 0)
    return


def test_remove_project_strips_both_files():
    """
        ``remove_project`` removes the matching id-line from
        ``/etc/projects`` and the matching name-line from
        ``/etc/projid``.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/projects")] = RunResult(
        exit_code=0,
        stdout="42:/mnt/xfs/archive\n7:/mnt/xfs/other\n",
        stderr="",
        command=["cat"],
    )
    runner.results[("cat", "/etc/projid")] = RunResult(
        exit_code=0,
        stdout="archive:42\nother:7\n",
        stderr="",
        command=["cat"],
    )
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    result = manager.remove_project(project_name="archive")
    testbase.assert_equal(result.status, QuotaActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 2)
    projects_writes = [c for c in tee_calls if c["args"][1] == "/etc/projects"]
    projid_writes = [c for c in tee_calls if c["args"][1] == "/etc/projid"]
    testbase.assert_true("42:/mnt/xfs/archive" not in projects_writes[0]["stdin"])
    testbase.assert_true("7:/mnt/xfs/other" in projects_writes[0]["stdin"])
    testbase.assert_true("archive:42" not in projid_writes[0]["stdin"])
    testbase.assert_true("other:7" in projid_writes[0]["stdin"])
    return


def test_remove_project_skipped_when_absent():
    """
        ``remove_project`` returns SKIPPED when the project name is
        not defined.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/projects")] = RunResult(
        exit_code=0, stdout="", stderr="", command=["cat"],
    )
    runner.results[("cat", "/etc/projid")] = RunResult(
        exit_code=0, stdout="", stderr="", command=["cat"],
    )
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    result = manager.remove_project(project_name="archive")
    testbase.assert_equal(result.status, QuotaActionStatus.SKIPPED)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 0)
    return


def test_list_projects_merges_files():
    """
        ``list_projects`` merges ``/etc/projects`` and
        ``/etc/projid`` into :class:`ProjectDefinition` entries.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/projects")] = RunResult(
        exit_code=0,
        stdout="42:/mnt/xfs/archive\n42:/mnt/xfs/archive2\n7:/mnt/xfs/other\n",
        stderr="",
        command=["cat"],
    )
    runner.results[("cat", "/etc/projid")] = RunResult(
        exit_code=0,
        stdout="archive:42\nother:7\n",
        stderr="",
        command=["cat"],
    )
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    projects = manager.list_projects()
    testbase.assert_equal(len(projects), 2)
    archive = [p for p in projects if p.project_name == "archive"][0]
    other = [p for p in projects if p.project_name == "other"][0]
    testbase.assert_equal(archive.project_id, 42)
    testbase.assert_equal(len(archive.paths), 2)
    testbase.assert_true("/mnt/xfs/archive" in archive.paths)
    testbase.assert_true("/mnt/xfs/archive2" in archive.paths)
    testbase.assert_equal(other.project_id, 7)
    testbase.assert_equal(other.paths, ["/mnt/xfs/other"])
    return


def test_activate_project_xfs_dispatches_xfs_quota():
    """
        ``activate_project`` on XFS dispatches
        ``xfs_quota -x -c "project -s <name>" <mount>``.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/xfs", fstype="xfs")
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    result = manager.activate_project(
        mount="/mnt/xfs", project_name="archive",
    )
    testbase.assert_equal(result.status, QuotaActionStatus.SUCCESS)
    activate_calls = [c for c in runner.calls if c["args"][0] == "xfs_quota"]
    testbase.assert_equal(len(activate_calls), 1)
    testbase.assert_equal(
        activate_calls[0]["args"],
        ["xfs_quota", "-x", "-c", "project -s archive", "/mnt/xfs"],
    )
    return


def test_activate_project_non_xfs_raises_unsupported():
    """
        ``activate_project`` on ext4 raises
        :class:`QuotaUnsupportedOperationError`.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="ext4")
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    raised = False
    try:
        manager.activate_project(
            mount="/mnt/data", project_name="archive",
        )
    except QuotaUnsupportedOperationError:
        raised = True
    testbase.assert_equal(raised, True)
    return


# ----------------------------------------------------------------------
# Dry-run sweep across mutating verbs
# ----------------------------------------------------------------------


def test_enable_dry_run_does_not_dispatch():
    """
        ``enable`` with ``dry_run=True`` returns DRY_RUN and does
        not dispatch ``quotaon``.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="ext4")
    manager = FilesystemQuotaManager(
        runner,
        FilesystemQuotaManagerConfig(dry_run=True),
    )
    result = manager.enable(mount="/mnt/data")
    testbase.assert_equal(result.status, QuotaActionStatus.DRY_RUN)
    quotaon_writes = [
        c for c in runner.calls
        if c["args"][0] == "quotaon" and "-p" not in c["args"]
    ]
    testbase.assert_equal(len(quotaon_writes), 0)
    return


def test_quotacheck_dry_run_does_not_dispatch():
    """
        ``quotacheck`` with ``dry_run=True`` returns DRY_RUN.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="ext4")
    manager = FilesystemQuotaManager(
        runner,
        FilesystemQuotaManagerConfig(dry_run=True),
    )
    result = manager.quotacheck(mount="/mnt/data")
    testbase.assert_equal(result.status, QuotaActionStatus.DRY_RUN)
    check_calls = [c for c in runner.calls if c["args"][0] == "quotacheck"]
    testbase.assert_equal(len(check_calls), 0)
    return


def test_define_project_dry_run_does_not_write():
    """
        ``define_project`` with ``dry_run=True`` returns DRY_RUN
        and does not invoke ``tee``.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/projects")] = RunResult(
        exit_code=0, stdout="", stderr="", command=["cat"],
    )
    runner.results[("cat", "/etc/projid")] = RunResult(
        exit_code=0, stdout="", stderr="", command=["cat"],
    )
    manager = FilesystemQuotaManager(
        runner,
        FilesystemQuotaManagerConfig(dry_run=True),
    )
    result = manager.define_project(
        project_id=99, project_name="dryproj", paths=["/mnt/x"],
    )
    testbase.assert_equal(result.status, QuotaActionStatus.DRY_RUN)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 0)
    return


# ----------------------------------------------------------------------
# Failure surface
# ----------------------------------------------------------------------


def test_failed_command_surfaces_exit_code_and_stderr():
    """
        A failing mutation surfaces FAILED with the runner's
        exit_code and stderr captured.
    """
    runner = _MockRunner()
    _seed_findmnt_mounted(runner, mount="/mnt/data", fstype="ext4")
    _seed_quotaon_status(
        runner, mount="/mnt/data", user_on=False, group_on=False,
    )
    runner.results[("quotaon", "-u", "-g", "/mnt/data")] = RunResult(
        exit_code=2,
        stdout="",
        stderr="quotaon: cannot find quota file",
        command=["quotaon"],
    )
    manager = FilesystemQuotaManager(runner, FilesystemQuotaManagerConfig())
    result = manager.enable(mount="/mnt/data")
    testbase.assert_equal(result.status, QuotaActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 2)
    testbase.assert_true("quotaon:" in result.stderr)
    return


# ----------------------------------------------------------------------
# Pure-parser tests
# ----------------------------------------------------------------------


def test_parse_quotaon_status_handles_mixed():
    """
        ``_parse_quotaon_status`` produces correct booleans for
        mixed output.
    """
    body = "\n".join([
        "user quota on /mnt/data (/dev/sda1) is on",
        "group quota on /mnt/data (/dev/sda1) is off",
    ]) + "\n"
    user_active, group_active = _parse_quotaon_status(stdout=body)
    testbase.assert_equal(user_active, True)
    testbase.assert_equal(group_active, False)
    return


def test_parse_xfs_state_requires_accounting_and_enforcement():
    """
        ``_parse_xfs_state`` reports ON only when both Accounting
        AND Enforcement are ON.
    """
    body = "\n".join([
        "User quota state on /mnt/xfs (/dev/sdb1)",
        "  Accounting: ON",
        "  Enforcement: OFF",
        "Group quota state on /mnt/xfs (/dev/sdb1)",
        "  Accounting: ON",
        "  Enforcement: ON",
        "Project quota state on /mnt/xfs (/dev/sdb1)",
        "  Accounting: OFF",
        "  Enforcement: OFF",
    ]) + "\n"
    user_on, group_on, project_on = _parse_xfs_state(stdout=body)
    testbase.assert_equal(user_on, False)
    testbase.assert_equal(group_on, True)
    testbase.assert_equal(project_on, False)
    return


def test_parse_repquota_output_user_section_only():
    """
        ``_parse_repquota_output`` builds entries from the data
        rows of a single-target repquota dump.
    """
    body = "\n".join([
        "*** Report for user quotas on device /dev/sda1",
        "Block grace time: 7days; Inode grace time: 7days",
        "                        Block limits                File limits",
        "User            used    soft    hard  grace    used  soft  hard  grace",
        "----------------------------------------------------------------------",
        "alice     --   102400  204800  409600           1234     0     0",
    ]) + "\n"
    entries = _parse_repquota_output(
        stdout=body,
        mount="/mnt/data",
        target=QuotaTarget.USER,
    )
    testbase.assert_equal(len(entries), 1)
    testbase.assert_equal(entries[0].name, "alice")
    testbase.assert_equal(entries[0].block_soft, 204800)
    testbase.assert_equal(entries[0].block_hard, 409600)
    testbase.assert_equal(entries[0].target, QuotaTarget.USER)
    return
