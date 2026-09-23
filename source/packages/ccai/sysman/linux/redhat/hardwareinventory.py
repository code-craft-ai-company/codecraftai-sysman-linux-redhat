# ccai.sysman.linux.redhat.hardwareinventory
#
# HardwareInventoryManager collects read-only system hardware information
# through the ICommandRunner abstraction.  The module never mutates system
# state.

import json

from dataclasses import dataclass, field
from typing import Optional

from ccai.sysman.linux.redhat.featurebase import (
    BaseFeatureManager,
    FeatureConfig,
)


@dataclass
class HardwareInventoryConfig(FeatureConfig):
    """
        Configuration for :class:`HardwareInventoryManager`.  Inherits
        ``dry_run`` (unused here, this feature is read-only) and
        ``elevated`` from :class:`FeatureConfig`.
    """
    pass


@dataclass
class DmiIdentity:
    """
        DMI / SMBIOS identity fields.
    """
    system_manufacturer: Optional[str] = None
    system_product: Optional[str] = None
    system_version: Optional[str] = None
    system_serial: Optional[str] = None
    system_uuid: Optional[str] = None
    baseboard_manufacturer: Optional[str] = None
    baseboard_product: Optional[str] = None
    baseboard_serial: Optional[str] = None
    chassis_type: Optional[str] = None
    bios_vendor: Optional[str] = None
    bios_version: Optional[str] = None
    bios_release_date: Optional[str] = None
    source: str = "unknown"


@dataclass
class CpuInfo:
    """
        Simplified CPU summary.
    """
    arch: Optional[str] = None
    cpus: Optional[int] = None
    cores_per_socket: Optional[int] = None
    sockets: Optional[int] = None
    vendor: Optional[str] = None
    model_name: Optional[str] = None
    mhz: Optional[float] = None
    flags: list[str] = field(default_factory=list)
    source: str = "unknown"


@dataclass
class MemoryInfo:
    """
        Aggregate memory information.
    """
    total_online_bytes: Optional[int] = None
    available_bytes: Optional[int] = None
    free_bytes: Optional[int] = None
    swap_total_bytes: Optional[int] = None
    swap_free_bytes: Optional[int] = None
    source: str = "unknown"


@dataclass
class BlockDeviceInventory:
    """
        Summary for a block device.
    """
    name: str
    size_bytes: int
    type: str
    mountpoint: Optional[str] = None
    model: Optional[str] = None


@dataclass
class PciDevice:
    """
        Summary for a PCI device.
    """
    slot: str
    class_desc: str
    vendor: str
    device: str


@dataclass
class UsbDevice:
    """
        Summary for a USB device.
    """
    bus: str
    device: str
    description: str


@dataclass
class NetInterfaceHardware:
    """
        Link-layer information for a network interface.
    """
    name: str
    mac_address: Optional[str]
    mtu: Optional[int]
    state: Optional[str]


@dataclass
class OsRelease:
    """
        Kernel and distribution identity.
    """
    kernel: str
    os_id: Optional[str]
    os_version: Optional[str]
    hostname: Optional[str]
    machine_id: Optional[str]


@dataclass
class FirmwareInfo:
    """
        Firmware vendor, version, release date, and UEFI flag.
    """
    vendor: Optional[str]
    version: Optional[str]
    release_date: Optional[str]
    is_uefi: bool


@dataclass
class HardwareSnapshot:
    """
        Aggregate snapshot of every hardware sub-query.
    """
    dmi: Optional[DmiIdentity] = None
    cpu: Optional[CpuInfo] = None
    memory: Optional[MemoryInfo] = None
    block_devices: list[BlockDeviceInventory] = field(default_factory=list)
    pci_devices: list[PciDevice] = field(default_factory=list)
    usb_devices: list[UsbDevice] = field(default_factory=list)
    net_interfaces: list[NetInterfaceHardware] = field(default_factory=list)
    os_release: Optional[OsRelease] = None
    firmware: Optional[FirmwareInfo] = None
    warnings: list[str] = field(default_factory=list)


class HardwareInventoryManager(BaseFeatureManager):
    """
        Read-only facade for hardware inventory queries.  Has no
        ActionResult at all since it never mutates state; inherits
        the standard ``__init__`` / ``config`` / ``runner`` surface
        for uniformity with the rest of the package.
    """

    feature_name = "hardware-inventory"

    def get_dmi_identity(self) -> DmiIdentity:
        """
            Return DMI/SMBIOS identity, preferring dmidecode over sysfs.
        """
        run_result = self._runner.run(
            ["dmidecode"], elevated=self._config.elevated,
        )
        if run_result.exit_code == 0 and len(run_result.stdout) > 0:
            identity = _parse_dmidecode(stdout=run_result.stdout)
            identity.source = "dmidecode"
            return identity

        identity = DmiIdentity(source="sysfs")
        identity.system_manufacturer = self._read_sysfs_line(
            "/sys/class/dmi/id/sys_vendor",
        )
        identity.system_product = self._read_sysfs_line(
            "/sys/class/dmi/id/product_name",
        )
        identity.system_version = self._read_sysfs_line(
            "/sys/class/dmi/id/product_version",
        )
        identity.system_serial = self._read_sysfs_line(
            "/sys/class/dmi/id/product_serial",
        )
        identity.system_uuid = self._read_sysfs_line(
            "/sys/class/dmi/id/product_uuid",
        )
        identity.bios_vendor = self._read_sysfs_line(
            "/sys/class/dmi/id/bios_vendor",
        )
        identity.bios_version = self._read_sysfs_line(
            "/sys/class/dmi/id/bios_version",
        )
        identity.bios_release_date = self._read_sysfs_line(
            "/sys/class/dmi/id/bios_date",
        )
        return identity

    def get_cpu_info(self) -> CpuInfo:
        """
            Return CPU topology via ``lscpu -J``.
        """
        run_result = self._runner.run(["lscpu", "-J"], elevated=False)
        if run_result.exit_code != 0:
            proc_result = self._runner.run(
                ["cat", "/proc/cpuinfo"], elevated=False,
            )
            if proc_result.exit_code != 0:
                return CpuInfo(source="unknown")
            info = _parse_proc_cpuinfo(stdout=proc_result.stdout)
            info.source = "proc_cpuinfo"
            return info
        info = _parse_lscpu_json(stdout=run_result.stdout)
        info.source = "lscpu"
        return info

    def get_memory_info(self) -> MemoryInfo:
        """
            Return memory summary from ``/proc/meminfo`` or ``lsmem`` output.
        """
        proc_result = self._runner.run(
            ["cat", "/proc/meminfo"], elevated=False,
        )
        if proc_result.exit_code != 0:
            return MemoryInfo(source="unknown")
        info = _parse_proc_meminfo(stdout=proc_result.stdout)
        info.source = "proc_meminfo"
        return info

    def list_block_devices(self) -> list[BlockDeviceInventory]:
        """
            Enumerate block devices via ``lsblk -J -b``.
        """
        run_result = self._runner.run(
            ["lsblk", "-J", "-b", "-o", "NAME,SIZE,TYPE,MOUNTPOINT,MODEL"],
            elevated=False,
        )
        if run_result.exit_code != 0:
            return []
        devices = _parse_lsblk_json(stdout=run_result.stdout)
        return devices

    def list_pci_devices(self) -> list[PciDevice]:
        """
            Enumerate PCI devices via ``lspci -vmm -nnk``.
        """
        run_result = self._runner.run(
            ["lspci", "-vmm", "-nnk"], elevated=False,
        )
        if run_result.exit_code != 0:
            return []
        devices = _parse_lspci_output(stdout=run_result.stdout)
        return devices

    def list_usb_devices(self) -> list[UsbDevice]:
        """
            Enumerate USB devices via ``lsusb``.
        """
        run_result = self._runner.run(["lsusb"], elevated=False)
        if run_result.exit_code != 0:
            return []
        devices = _parse_lsusb_output(stdout=run_result.stdout)
        return devices

    def list_net_hardware(self) -> list[NetInterfaceHardware]:
        """
            Enumerate link-layer info via ``ip -j -d link show``.
        """
        run_result = self._runner.run(
            ["ip", "-j", "-d", "link", "show"], elevated=False,
        )
        if run_result.exit_code != 0:
            return []
        interfaces = _parse_ip_link(stdout=run_result.stdout)
        return interfaces

    def get_os_release(self) -> OsRelease:
        """
            Return kernel and distribution identity.
        """
        kernel = ""
        uname_result = self._runner.run(
            ["uname", "-srvmo"], elevated=False,
        )
        if uname_result.exit_code == 0:
            kernel = uname_result.stdout.strip()

        release_result = self._runner.run(
            ["cat", "/etc/os-release"], elevated=False,
        )
        os_id: Optional[str] = None
        os_version: Optional[str] = None
        if release_result.exit_code == 0:
            release_fields = _parse_os_release(stdout=release_result.stdout)
            os_id = release_fields.get("ID")
            os_version = release_fields.get("VERSION_ID")

        hostname = None
        hn_result = self._runner.run(["cat", "/etc/hostname"], elevated=False)
        if hn_result.exit_code == 0:
            hostname = hn_result.stdout.strip()

        machine_id = None
        mid_result = self._runner.run(["cat", "/etc/machine-id"], elevated=False)
        if mid_result.exit_code == 0:
            machine_id = mid_result.stdout.strip()

        release = OsRelease(
            kernel=kernel,
            os_id=os_id,
            os_version=os_version,
            hostname=hostname,
            machine_id=machine_id,
        )
        return release

    def get_firmware_info(self) -> FirmwareInfo:
        """
            Report BIOS/UEFI firmware vendor, version, and UEFI status.
        """
        vendor = self._read_sysfs_line("/sys/class/dmi/id/bios_vendor")
        version = self._read_sysfs_line("/sys/class/dmi/id/bios_version")
        release_date = self._read_sysfs_line("/sys/class/dmi/id/bios_date")
        efi_check = self._runner.run(
            ["ls", "/sys/firmware/efi"], elevated=False,
        )
        is_uefi = efi_check.exit_code == 0
        firmware = FirmwareInfo(
            vendor=vendor,
            version=version,
            release_date=release_date,
            is_uefi=is_uefi,
        )
        return firmware

    def collect_snapshot(self) -> HardwareSnapshot:
        """
            Aggregate every sub-query into a :class:`HardwareSnapshot`.
        """
        snapshot = HardwareSnapshot()
        try:
            snapshot.dmi = self.get_dmi_identity()
        except Exception as ex:
            snapshot.warnings.append("dmi: {}".format(ex))
        try:
            snapshot.cpu = self.get_cpu_info()
        except Exception as ex:
            snapshot.warnings.append("cpu: {}".format(ex))
        try:
            snapshot.memory = self.get_memory_info()
        except Exception as ex:
            snapshot.warnings.append("memory: {}".format(ex))
        try:
            snapshot.block_devices = self.list_block_devices()
        except Exception as ex:
            snapshot.warnings.append("block_devices: {}".format(ex))
        try:
            snapshot.pci_devices = self.list_pci_devices()
        except Exception as ex:
            snapshot.warnings.append("pci_devices: {}".format(ex))
        try:
            snapshot.usb_devices = self.list_usb_devices()
        except Exception as ex:
            snapshot.warnings.append("usb_devices: {}".format(ex))
        try:
            snapshot.net_interfaces = self.list_net_hardware()
        except Exception as ex:
            snapshot.warnings.append("net: {}".format(ex))
        try:
            snapshot.os_release = self.get_os_release()
        except Exception as ex:
            snapshot.warnings.append("os_release: {}".format(ex))
        try:
            snapshot.firmware = self.get_firmware_info()
        except Exception as ex:
            snapshot.warnings.append("firmware: {}".format(ex))
        return snapshot

    def _read_sysfs_line(self, path: str) -> Optional[str]:
        """
            Read a single line from a sysfs entry via ``cat``.
        """
        run_result = self._runner.run(["cat", path], elevated=False)
        if run_result.exit_code != 0:
            return None
        value = run_result.stdout.strip()
        if len(value) == 0:
            return None
        return value


def _parse_dmidecode(*, stdout: str) -> DmiIdentity:
    """
        Very small dmidecode parser that maps a few fields of interest.
    """
    identity = DmiIdentity()
    current_section: Optional[str] = None
    for raw_line in stdout.splitlines():
        line = raw_line.rstrip()
        if line.startswith("Handle ") or len(line.strip()) == 0:
            continue
        if not line.startswith("\t"):
            current_section = line.strip()
            continue
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if current_section == "System Information":
            if key == "Manufacturer":
                identity.system_manufacturer = value
            elif key == "Product Name":
                identity.system_product = value
            elif key == "Version":
                identity.system_version = value
            elif key == "Serial Number":
                identity.system_serial = value
            elif key == "UUID":
                identity.system_uuid = value
        elif current_section == "Base Board Information":
            if key == "Manufacturer":
                identity.baseboard_manufacturer = value
            elif key == "Product Name":
                identity.baseboard_product = value
            elif key == "Serial Number":
                identity.baseboard_serial = value
        elif current_section == "Chassis Information":
            if key == "Type":
                identity.chassis_type = value
        elif current_section == "BIOS Information":
            if key == "Vendor":
                identity.bios_vendor = value
            elif key == "Version":
                identity.bios_version = value
            elif key == "Release Date":
                identity.bios_release_date = value
    return identity


def _parse_lscpu_json(*, stdout: str) -> CpuInfo:
    """
        Parse ``lscpu -J`` output into a :class:`CpuInfo`.
    """
    info = CpuInfo()
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return info
    entries = data.get("lscpu", [])
    for entry in entries:
        field_name = entry.get("field", "").rstrip(":")
        value = entry.get("data", "")
        if field_name == "Architecture":
            info.arch = value
        elif field_name == "CPU(s)":
            try:
                info.cpus = int(value)
            except ValueError:
                # Best-effort parse: a non-numeric 'CPU(s)' value leaves the field at its default.
                pass
        elif field_name == "Core(s) per socket":
            try:
                info.cores_per_socket = int(value)
            except ValueError:
                # Best-effort parse: a non-numeric 'Core(s) per socket' value leaves the field at its default.
                pass
        elif field_name == "Socket(s)":
            try:
                info.sockets = int(value)
            except ValueError:
                # Best-effort parse: a non-numeric 'Socket(s)' value leaves the field at its default.
                pass
        elif field_name == "Vendor ID":
            info.vendor = value
        elif field_name == "Model name":
            info.model_name = value
        elif field_name == "CPU MHz":
            try:
                info.mhz = float(value)
            except ValueError:
                # Best-effort parse: a non-numeric 'CPU MHz' value leaves the field at its default.
                pass
        elif field_name == "Flags":
            info.flags = value.split()
    return info


def _parse_proc_cpuinfo(*, stdout: str) -> CpuInfo:
    """
        Parse a slice of ``/proc/cpuinfo`` into a :class:`CpuInfo`.
    """
    info = CpuInfo()
    for line in stdout.splitlines():
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if key == "vendor_id":
            info.vendor = value
        elif key == "model name":
            info.model_name = value
        elif key == "cpu MHz":
            try:
                info.mhz = float(value)
            except ValueError:
                # Best-effort parse: a non-numeric 'cpu MHz' value leaves the field at its default.
                pass
        elif key == "flags":
            info.flags = value.split()
    return info


def _parse_proc_meminfo(*, stdout: str) -> MemoryInfo:
    """
        Parse ``/proc/meminfo`` into a :class:`MemoryInfo`.
    """
    info = MemoryInfo()
    for line in stdout.splitlines():
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        raw_value = value.strip()
        tokens = raw_value.split()
        if len(tokens) == 0:
            continue
        try:
            number = int(tokens[0])
        except ValueError:
            continue
        unit = tokens[1].lower() if len(tokens) > 1 else "b"
        multiplier = {"b": 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3}.get(unit, 1)
        bytes_value = number * multiplier
        if key == "MemTotal":
            info.total_online_bytes = bytes_value
        elif key == "MemAvailable":
            info.available_bytes = bytes_value
        elif key == "MemFree":
            info.free_bytes = bytes_value
        elif key == "SwapTotal":
            info.swap_total_bytes = bytes_value
        elif key == "SwapFree":
            info.swap_free_bytes = bytes_value
    return info


def _parse_lsblk_json(*, stdout: str) -> list[BlockDeviceInventory]:
    """
        Parse ``lsblk -J -b`` output into BlockDeviceInventory records.
    """
    devices: list[BlockDeviceInventory] = []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return devices
    stack: list[dict] = list(data.get("blockdevices", []))
    while len(stack) > 0:
        entry = stack.pop()
        try:
            size_bytes = int(entry.get("size", 0))
        except (TypeError, ValueError):
            size_bytes = 0
        devices.append(BlockDeviceInventory(
            name=entry.get("name", ""),
            size_bytes=size_bytes,
            type=entry.get("type", ""),
            mountpoint=entry.get("mountpoint"),
            model=entry.get("model"),
        ))
        for child in entry.get("children", []):
            stack.append(child)
    return devices


def _parse_lspci_output(*, stdout: str) -> list[PciDevice]:
    """
        Parse ``lspci -vmm -nnk`` records separated by blank lines.
    """
    devices: list[PciDevice] = []
    current: dict[str, str] = {}
    for raw in stdout.splitlines():
        line = raw.rstrip()
        if len(line) == 0:
            if len(current) > 0:
                devices.append(PciDevice(
                    slot=current.get("Slot", ""),
                    class_desc=current.get("Class", ""),
                    vendor=current.get("Vendor", ""),
                    device=current.get("Device", ""),
                ))
                current = {}
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            current[key.strip()] = value.strip()
    if len(current) > 0:
        devices.append(PciDevice(
            slot=current.get("Slot", ""),
            class_desc=current.get("Class", ""),
            vendor=current.get("Vendor", ""),
            device=current.get("Device", ""),
        ))
    return devices


def _parse_lsusb_output(*, stdout: str) -> list[UsbDevice]:
    """
        Parse ``lsusb`` output into UsbDevice records.
    """
    devices: list[UsbDevice] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if len(line) == 0:
            continue
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue
        bus = parts[1]
        device = parts[3].rstrip(":")
        description = parts[6]
        devices.append(UsbDevice(
            bus=bus, device=device, description=description,
        ))
    return devices


def _parse_ip_link(*, stdout: str) -> list[NetInterfaceHardware]:
    """
        Parse ``ip -j -d link show`` output into NetInterfaceHardware records.
    """
    interfaces: list[NetInterfaceHardware] = []
    try:
        entries = json.loads(stdout)
    except json.JSONDecodeError:
        return interfaces
    for entry in entries:
        mtu_value = entry.get("mtu")
        try:
            mtu_int = int(mtu_value) if mtu_value is not None else None
        except (TypeError, ValueError):
            mtu_int = None
        interfaces.append(NetInterfaceHardware(
            name=entry.get("ifname", ""),
            mac_address=entry.get("address"),
            mtu=mtu_int,
            state=entry.get("operstate"),
        ))
    return interfaces


def _parse_os_release(*, stdout: str) -> dict[str, str]:
    """
        Parse ``/etc/os-release`` lines into a dict, stripping quotes.
    """
    parsed: dict[str, str] = {}
    for raw in stdout.splitlines():
        line = raw.strip()
        if len(line) == 0 or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        parsed[key.strip()] = value.strip()
    return parsed
