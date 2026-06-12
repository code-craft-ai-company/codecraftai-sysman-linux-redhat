# Tests for HardwareInventoryManager.

import json

from dataclasses import dataclass, field
from typing import Callable, Optional

from ccai import testbase

from ccai.sysman.linux.redhat.hardwareinventory import (
    HardwareInventoryConfig,
    HardwareInventoryManager,
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
        self.calls.append({"args": list(args)})
        key = tuple(args)
        if key in self.results:
            return self.results[key]
        if self.default_result is not None:
            return self.default_result
        return RunResult(exit_code=1, stdout="", stderr="not implemented",
                         command=list(args))

    def add_aspect(self, aspect) -> None:
        return

    def remove_aspect(self, aspect) -> None:
        return

    def get_metrics(self):
        return None


def test_get_dmi_identity_prefers_dmidecode():
    """
        get_dmi_identity() returns dmidecode output when available.
    """
    dmi_output = (
        "Handle 0x0001\n"
        "System Information\n"
        "\tManufacturer: ACME\n"
        "\tProduct Name: Widget 3000\n"
        "\tSerial Number: SN12345\n"
        "\nBIOS Information\n"
        "\tVendor: ACME BIOS\n"
        "\tVersion: 1.2\n"
    )
    runner = _MockRunner()
    runner.results[("dmidecode",)] = RunResult(
        exit_code=0, stdout=dmi_output, stderr="", command=["dmidecode"],
    )
    mgr = HardwareInventoryManager(runner, HardwareInventoryConfig(elevated=True))
    identity = mgr.get_dmi_identity()
    testbase.assert_equal(identity.system_manufacturer, "ACME")
    testbase.assert_equal(identity.system_product, "Widget 3000")
    testbase.assert_equal(identity.bios_vendor, "ACME BIOS")
    testbase.assert_equal(identity.source, "dmidecode")
    return


def test_get_cpu_info_parses_lscpu_json():
    """
        get_cpu_info() parses ``lscpu -J`` output.
    """
    lscpu_output = json.dumps({
        "lscpu": [
            {"field": "Architecture:", "data": "x86_64"},
            {"field": "CPU(s):", "data": "8"},
            {"field": "Vendor ID:", "data": "GenuineIntel"},
            {"field": "Model name:", "data": "Intel Xeon"},
            {"field": "Flags:", "data": "fpu vme sse"},
        ]
    })
    runner = _MockRunner()
    runner.results[("lscpu", "-J")] = RunResult(
        exit_code=0, stdout=lscpu_output, stderr="", command=["lscpu"],
    )
    mgr = HardwareInventoryManager(runner, HardwareInventoryConfig())
    info = mgr.get_cpu_info()
    testbase.assert_equal(info.arch, "x86_64")
    testbase.assert_equal(info.cpus, 8)
    testbase.assert_equal(info.model_name, "Intel Xeon")
    testbase.assert_equal(info.flags, ["fpu", "vme", "sse"])
    return


def test_get_memory_info_parses_meminfo():
    """
        get_memory_info() parses /proc/meminfo output.
    """
    meminfo = (
        "MemTotal: 16384000 kB\n"
        "MemAvailable: 12288000 kB\n"
        "SwapTotal: 2048000 kB\n"
    )
    runner = _MockRunner()
    runner.results[("cat", "/proc/meminfo")] = RunResult(
        exit_code=0, stdout=meminfo, stderr="", command=["cat"],
    )
    mgr = HardwareInventoryManager(runner, HardwareInventoryConfig())
    info = mgr.get_memory_info()
    testbase.assert_equal(info.total_online_bytes, 16384000 * 1024)
    testbase.assert_equal(info.available_bytes, 12288000 * 1024)
    testbase.assert_equal(info.source, "proc_meminfo")
    return


def test_list_block_devices_parses_lsblk_json():
    """
        list_block_devices() flattens lsblk json into device records.
    """
    body = json.dumps({
        "blockdevices": [
            {
                "name": "sda",
                "size": 107374182400,
                "type": "disk",
                "mountpoint": None,
                "model": "MODEL1",
                "children": [
                    {
                        "name": "sda1",
                        "size": 107374182400,
                        "type": "part",
                        "mountpoint": "/",
                        "model": None,
                    }
                ],
            }
        ]
    })
    runner = _MockRunner()
    runner.results[(
        "lsblk", "-J", "-b", "-o", "NAME,SIZE,TYPE,MOUNTPOINT,MODEL",
    )] = RunResult(
        exit_code=0, stdout=body, stderr="", command=["lsblk"],
    )
    mgr = HardwareInventoryManager(runner, HardwareInventoryConfig())
    devices = mgr.list_block_devices()
    testbase.assert_true(len(devices) >= 2)
    names = [d.name for d in devices]
    testbase.assert_true("sda" in names)
    testbase.assert_true("sda1" in names)
    return


def test_get_os_release_reports_kernel_and_distro():
    """
        get_os_release() combines uname, /etc/os-release, hostname, machine-id.
    """
    runner = _MockRunner()
    runner.results[("uname", "-srvmo")] = RunResult(
        exit_code=0, stdout="Linux 5.14 #1 SMP x86_64 GNU/Linux\n",
        stderr="", command=["uname"],
    )
    runner.results[("cat", "/etc/os-release")] = RunResult(
        exit_code=0,
        stdout='ID="rhel"\nVERSION_ID="9.2"\n',
        stderr="", command=["cat"],
    )
    runner.results[("cat", "/etc/hostname")] = RunResult(
        exit_code=0, stdout="host01\n", stderr="", command=["cat"],
    )
    runner.results[("cat", "/etc/machine-id")] = RunResult(
        exit_code=0, stdout="abcdef\n", stderr="", command=["cat"],
    )
    mgr = HardwareInventoryManager(runner, HardwareInventoryConfig())
    release = mgr.get_os_release()
    testbase.assert_equal(release.os_id, "rhel")
    testbase.assert_equal(release.os_version, "9.2")
    testbase.assert_equal(release.hostname, "host01")
    testbase.assert_equal(release.machine_id, "abcdef")
    return


def test_get_firmware_info_detects_uefi():
    """
        get_firmware_info() reports is_uefi=True when ``ls /sys/firmware/efi`` succeeds.
    """
    runner = _MockRunner()
    runner.results[("cat", "/sys/class/dmi/id/bios_vendor")] = RunResult(
        exit_code=0, stdout="American Megatrends Inc.\n", stderr="",
        command=["cat"],
    )
    runner.results[("cat", "/sys/class/dmi/id/bios_version")] = RunResult(
        exit_code=0, stdout="5.17\n", stderr="", command=["cat"],
    )
    runner.results[("cat", "/sys/class/dmi/id/bios_date")] = RunResult(
        exit_code=0, stdout="05/10/2023\n", stderr="", command=["cat"],
    )
    runner.results[("ls", "/sys/firmware/efi")] = RunResult(
        exit_code=0, stdout="efivars\n", stderr="", command=["ls"],
    )
    mgr = HardwareInventoryManager(runner, HardwareInventoryConfig())
    firmware = mgr.get_firmware_info()
    testbase.assert_equal(firmware.vendor, "American Megatrends Inc.")
    testbase.assert_true(firmware.is_uefi)
    return
