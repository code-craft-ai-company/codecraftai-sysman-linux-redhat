# Tests for the FilesystemManager facade.

import json

from dataclasses import dataclass, field
from typing import Callable, Optional

from ccai import testbase

from ccai.sysman.linux.redhat.filesystemmanagement import (
    FilesystemActionStatus,
    FilesystemManager,
    FilesystemManagerConfig,
    FstabEntry,
    MountSpec,
)
from ccai.sysman.linux.redhat.runners.models import RunResult


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
        self.calls.append({"args": list(args), "stdin": stdin})
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


def test_create_directory_runs_mkdir():
    """
        create_directory() runs mkdir -p and optional chmod/chown.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["mkdir"],
    ))
    fm = FilesystemManager(runner, FilesystemManagerConfig(elevated=True))
    result = fm.create_directory(
        "/srv/app", mode="0755", owner="app", group="app",
    )
    testbase.assert_equal(result.status, FilesystemActionStatus.SUCCESS)
    commands = [c["args"][0] for c in runner.calls]
    testbase.assert_true("mkdir" in commands)
    testbase.assert_true("chmod" in commands)
    testbase.assert_true("chown" in commands)
    return


def test_remove_directory_recursive_uses_rm_rf():
    """
        remove_directory(recursive=True) uses ``rm -rf``.
    """
    runner = _MockRunner()
    fm = FilesystemManager(runner, FilesystemManagerConfig())
    fm.remove_directory("/tmp/old", recursive=True)
    testbase.assert_equal(runner.calls[-1]["args"], ["rm", "-rf", "/tmp/old"])
    return


def test_create_symlink_skipped_when_target_matches():
    """
        create_symlink() returns SKIPPED when the existing target matches.
    """
    runner = _MockRunner()
    runner.results[("readlink", "/opt/app/current")] = RunResult(
        exit_code=0, stdout="/opt/app/v2\n", stderr="", command=["readlink"],
    )
    fm = FilesystemManager(runner, FilesystemManagerConfig())
    result = fm.create_symlink("/opt/app/current", "/opt/app/v2")
    testbase.assert_equal(result.status, FilesystemActionStatus.SKIPPED)
    return


def test_create_symlink_runs_ln_when_missing():
    """
        create_symlink() dispatches ``ln -s`` when the link is missing.
    """
    runner = _MockRunner()
    runner.results[("readlink", "/opt/app/current")] = RunResult(
        exit_code=1, stdout="", stderr="", command=["readlink"],
    )
    fm = FilesystemManager(runner, FilesystemManagerConfig(elevated=True))
    result = fm.create_symlink("/opt/app/current", "/opt/app/v3")
    testbase.assert_equal(result.status, FilesystemActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"], ["ln", "-s", "/opt/app/v3", "/opt/app/current"],
    )
    return


def test_query_path_parses_stat_output():
    """
        query_path() parses stat output into PathInfo.
    """
    runner = _MockRunner()
    runner.results[("stat", "-c", "%F|%a|%U|%G|%s", "/etc/app.conf")] = RunResult(
        exit_code=0, stdout="regular file|644|app|app|1024", stderr="",
        command=["stat"],
    )
    fm = FilesystemManager(runner, FilesystemManagerConfig())
    info = fm.query_path("/etc/app.conf")
    testbase.assert_true(info is not None)
    testbase.assert_equal(info.path_type, "regular file")
    testbase.assert_equal(info.mode, "644")
    testbase.assert_equal(info.size_bytes, 1024)
    return


def test_query_disk_usage_parses_df_output():
    """
        query_disk_usage() parses df output into DiskUsageInfo.
    """
    stdout = (
        "1B-blocks      Used     Avail  Use%\n"
        "107374182400  53687091200  53687091200  50%\n"
    )
    runner = _MockRunner()
    runner.results[(
        "df", "-B1", "--output=size,used,avail,pcent", "/",
    )] = RunResult(
        exit_code=0, stdout=stdout, stderr="", command=["df"],
    )
    fm = FilesystemManager(runner, FilesystemManagerConfig())
    info = fm.query_disk_usage("/")
    testbase.assert_equal(info.total_bytes, 107374182400)
    testbase.assert_equal(info.use_percent, 50)
    return


def test_mount_skipped_when_already_mounted():
    """
        mount() returns SKIPPED when the mountpoint is already mounted.
    """
    runner = _MockRunner()
    findmnt_body = json.dumps({
        "filesystems": [{
            "source": "/dev/sdb1",
            "target": "/data",
            "fstype": "ext4",
            "options": "rw",
            "children": [],
        }],
    })
    runner.results[("findmnt", "--json")] = RunResult(
        exit_code=0, stdout=findmnt_body, stderr="", command=["findmnt"],
    )
    fm = FilesystemManager(runner, FilesystemManagerConfig())
    result = fm.mount(MountSpec(device="/dev/sdb1", mountpoint="/data"))
    testbase.assert_equal(result.status, FilesystemActionStatus.SKIPPED)
    return


def test_mount_runs_mount_command():
    """
        mount() builds the mount command with optional flags.
    """
    runner = _MockRunner()
    runner.results[("findmnt", "--json")] = RunResult(
        exit_code=0, stdout='{"filesystems": []}', stderr="",
        command=["findmnt"],
    )
    fm = FilesystemManager(runner, FilesystemManagerConfig(elevated=True))
    fm.mount(MountSpec(
        device="/dev/sdb1",
        mountpoint="/data",
        fstype="ext4",
        options="rw",
    ))
    mount_call = runner.calls[-1]
    testbase.assert_equal(
        mount_call["args"],
        ["mount", "-t", "ext4", "-o", "rw", "/dev/sdb1", "/data"],
    )
    return


def test_unmount_skipped_when_not_mounted():
    """
        unmount() returns SKIPPED when no entry matches the mountpoint.
    """
    runner = _MockRunner()
    runner.results[("findmnt", "--json")] = RunResult(
        exit_code=0, stdout='{"filesystems": []}', stderr="",
        command=["findmnt"],
    )
    fm = FilesystemManager(runner, FilesystemManagerConfig())
    result = fm.unmount("/data")
    testbase.assert_equal(result.status, FilesystemActionStatus.SKIPPED)
    return


def test_add_fstab_entry_writes_tee_when_absent():
    """
        add_fstab_entry() appends a new line via tee.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/fstab")] = RunResult(
        exit_code=0, stdout="# header\n", stderr="", command=["cat"],
    )
    fm = FilesystemManager(runner, FilesystemManagerConfig(elevated=True))
    result = fm.add_fstab_entry(FstabEntry(
        device="/dev/sdb1", mountpoint="/data", fstype="ext4",
    ))
    testbase.assert_equal(result.status, FilesystemActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true("/dev/sdb1" in tee_calls[0]["stdin"])
    testbase.assert_true("/data" in tee_calls[0]["stdin"])
    return


def test_add_fstab_entry_skipped_when_present():
    """
        add_fstab_entry() returns SKIPPED when the entry already exists.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/fstab")] = RunResult(
        exit_code=0,
        stdout="/dev/sdb1 /data ext4 defaults 0 0\n",
        stderr="",
        command=["cat"],
    )
    fm = FilesystemManager(runner, FilesystemManagerConfig())
    result = fm.add_fstab_entry(FstabEntry(
        device="/dev/sdb1", mountpoint="/data", fstype="ext4",
    ))
    testbase.assert_equal(result.status, FilesystemActionStatus.SKIPPED)
    return


def test_remove_fstab_entry_strips_matching_line():
    """
        remove_fstab_entry() strips the line matching the device or mountpoint.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/fstab")] = RunResult(
        exit_code=0,
        stdout="/dev/sda1 / ext4 defaults 0 0\n/dev/sdb1 /data ext4 defaults 0 0\n",
        stderr="",
        command=["cat"],
    )
    fm = FilesystemManager(runner, FilesystemManagerConfig())
    result = fm.remove_fstab_entry("/data")
    testbase.assert_equal(result.status, FilesystemActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_false("/dev/sdb1" in tee_calls[0]["stdin"])
    return
