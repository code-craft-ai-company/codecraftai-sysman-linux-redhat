# Tests for the StorageManager facade.

import json

from dataclasses import dataclass, field
from typing import Callable, Optional

from automojo import testbase

from automojo.sysman.linux.redhat.runners.models import RunResult
from automojo.sysman.linux.redhat.storagemanagement import (
    StorageActionStatus,
    StorageManager,
    StorageManagerConfig,
)


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
        self.calls.append({"args": list(args)})
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


def test_list_block_devices_parses_lsblk_json():
    """
        list_block_devices() parses lsblk output into BlockDevice records.
    """
    body = json.dumps({
        "blockdevices": [
            {"name": "sda", "size": 100, "type": "disk", "mountpoint": None},
        ],
    })
    runner = _MockRunner()
    runner.results[(
        "lsblk", "-J", "-b", "-o", "NAME,SIZE,TYPE,MOUNTPOINT",
    )] = RunResult(exit_code=0, stdout=body, stderr="", command=["lsblk"])
    sm = StorageManager(runner, StorageManagerConfig())
    devices = sm.list_block_devices()
    testbase.assert_equal(len(devices), 1)
    testbase.assert_equal(devices[0].name, "sda")
    return


def test_create_filesystem_dispatches_mkfs():
    """
        create_filesystem() dispatches ``mkfs.<type> -L label <device>``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["mkfs.ext4"],
    ))
    sm = StorageManager(runner, StorageManagerConfig(elevated=True))
    result = sm.create_filesystem("/dev/sdb1", "ext4", label="data")
    testbase.assert_equal(result.status, StorageActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"],
        ["mkfs.ext4", "-L", "data", "/dev/sdb1"],
    )
    return


def test_create_pv_and_vg_dispatch():
    """
        create_pv() and create_vg() build the expected commands.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["pvcreate"],
    ))
    sm = StorageManager(runner, StorageManagerConfig(elevated=True))
    sm.create_pv("/dev/sdb1")
    sm.create_vg("datavg", ["/dev/sdb1", "/dev/sdc1"])
    testbase.assert_equal(runner.calls[0]["args"], ["pvcreate", "/dev/sdb1"])
    testbase.assert_equal(
        runner.calls[1]["args"],
        ["vgcreate", "datavg", "/dev/sdb1", "/dev/sdc1"],
    )
    return


def test_create_lv_requires_size_or_extents():
    """
        create_lv() fails when neither size nor extents is supplied.
    """
    runner = _MockRunner()
    sm = StorageManager(runner, StorageManagerConfig())
    result = sm.create_lv("datavg", "data")
    testbase.assert_equal(result.status, StorageActionStatus.FAILED)
    return


def test_create_lv_with_size_dispatches_lvcreate():
    """
        create_lv() dispatches ``lvcreate -n name -L size vg``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["lvcreate"],
    ))
    sm = StorageManager(runner, StorageManagerConfig(elevated=True))
    result = sm.create_lv("datavg", "data", size="100G")
    testbase.assert_equal(result.status, StorageActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"],
        ["lvcreate", "-n", "data", "-L", "100G", "datavg"],
    )
    return


def test_list_pvs_parses_json_report():
    """
        list_pvs() parses the JSON report from ``pvs``.
    """
    body = json.dumps({
        "report": [
            {"pv": [
                {
                    "pv_name": "/dev/sdb1",
                    "vg_name": "datavg",
                    "pv_size": "10000B",
                    "pv_free": "5000B",
                },
            ]},
        ],
    })
    runner = _MockRunner()
    runner.results[("pvs", "--units", "b", "--reportformat", "json")] = RunResult(
        exit_code=0, stdout=body, stderr="", command=["pvs"],
    )
    sm = StorageManager(runner, StorageManagerConfig())
    pvs = sm.list_pvs()
    testbase.assert_equal(len(pvs), 1)
    testbase.assert_equal(pvs[0].vg_name, "datavg")
    testbase.assert_equal(pvs[0].size_bytes, 10000)
    testbase.assert_equal(pvs[0].free_bytes, 5000)
    return
