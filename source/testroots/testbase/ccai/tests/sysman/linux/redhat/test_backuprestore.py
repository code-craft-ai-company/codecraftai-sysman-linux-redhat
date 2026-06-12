# Tests for BackupRestoreManager.

from dataclasses import dataclass, field
from typing import Callable, Optional

from ccai import testbase

from ccai.sysman.linux.redhat.backuprestore import (
    BackupActionStatus,
    BackupManagerConfig,
    BackupRestoreManager,
    BackupSpec,
    Compression,
)
from ccai.sysman.linux.redhat.runners.models import RunResult


@dataclass
class _MockRunner:
    results: dict = field(default_factory=dict)
    default_result: Optional[RunResult] = None
    calls: list = field(default_factory=list)

    def run(self, args, *, elevated=False, cwd=None, env=None, timeout=None,
            on_output=None, on_error=None, capture_output=True, stdin=None):
        self.calls.append({"args": list(args)})
        key = tuple(args)
        if key in self.results:
            return self.results[key]
        if self.default_result is not None:
            return self.default_result
        return RunResult(exit_code=0, stdout="", stderr="", command=list(args))

    def add_aspect(self, aspect): return
    def remove_aspect(self, aspect): return
    def get_metrics(self): return None


def test_create_archive_dispatches_tar_with_compression():
    """
        create_archive() dispatches tar with the compression flag and excludes.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["tar"],
    ))
    mgr = BackupRestoreManager(runner, BackupManagerConfig(elevated=True))
    spec = BackupSpec(
        source_paths=["/etc"],
        archive_path="/backup/etc.tgz",
        compression=Compression.GZIP,
        exclude_patterns=["*.swp"],
    )
    result = mgr.create_archive(spec)
    testbase.assert_equal(result.status, BackupActionStatus.SUCCESS)
    args = runner.calls[-1]["args"]
    testbase.assert_true("-z" in args)
    testbase.assert_true("--exclude" in args)
    testbase.assert_true("/backup/etc.tgz" in args)
    return


def test_extract_archive_runs_tar_xf():
    """
        extract_archive() runs ``tar -xf archive -C destination``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["tar"],
    ))
    mgr = BackupRestoreManager(runner, BackupManagerConfig())
    result = mgr.extract_archive("/backup/etc.tgz", "/restore")
    testbase.assert_equal(result.status, BackupActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"],
        ["tar", "-xf", "/backup/etc.tgz", "-C", "/restore"],
    )
    return


def test_sync_paths_uses_rsync():
    """
        sync_paths() dispatches rsync with optional --delete.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["rsync"],
    ))
    mgr = BackupRestoreManager(runner, BackupManagerConfig())
    result = mgr.sync_paths("/src/", "/dst/", delete=True)
    testbase.assert_equal(result.status, BackupActionStatus.SUCCESS)
    testbase.assert_true("--delete" in runner.calls[-1]["args"])
    return
