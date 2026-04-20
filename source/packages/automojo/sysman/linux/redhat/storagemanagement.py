# automojo.sysman.linux.redhat.storagemanagement
#
# StorageManager wraps parted, wipefs, mkfs, LVM (pvcreate, vgcreate,
# lvcreate) and mdadm through the ICommandRunner abstraction.  This
# module focuses on the most common partition/filesystem/LVM operations.

import json

from dataclasses import dataclass
from typing import Optional

from automojo.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


StorageActionStatus = ActionStatus


@dataclass
class StorageManagerConfig(FeatureConfig):
    """
        Configuration for :class:`StorageManager`.  Inherits
        ``dry_run`` and ``elevated`` from :class:`FeatureConfig`.
    """
    pass


@dataclass
class StorageActionResult(FeatureActionResult):
    """
        Outcome of a storage management action.  No feature-specific
        extras beyond the base fields.
    """
    pass


@dataclass
class BlockDevice:
    """
        Block device summary from lsblk.
    """
    name: str
    size_bytes: int
    type: str
    mountpoint: Optional[str] = None


@dataclass
class PhysicalVolume:
    """
        LVM Physical Volume summary.
    """
    pv_name: str
    vg_name: str
    size_bytes: int
    free_bytes: int


@dataclass
class VolumeGroup:
    """
        LVM Volume Group summary.
    """
    vg_name: str
    size_bytes: int
    free_bytes: int


@dataclass
class LogicalVolume:
    """
        LVM Logical Volume summary.
    """
    lv_name: str
    vg_name: str
    size_bytes: int


class StorageManager(BaseFeatureManager):
    """
        Facade for partition, filesystem, and LVM operations.
    """

    result_class = StorageActionResult
    feature_name = "storage-management"

    def list_block_devices(
        self,
        type_filter: Optional[str] = None,
    ) -> list[BlockDevice]:
        """
            List block devices via ``lsblk -J -b``.
        """
        run_result = self._runner.run(
            ["lsblk", "-J", "-b", "-o", "NAME,SIZE,TYPE,MOUNTPOINT"],
            elevated=False,
        )
        if run_result.exit_code != 0:
            return []
        devices: list[BlockDevice] = []
        try:
            data = json.loads(run_result.stdout)
        except json.JSONDecodeError:
            return devices
        stack: list[dict] = list(data.get("blockdevices", []))
        while len(stack) > 0:
            entry = stack.pop()
            try:
                size_bytes = int(entry.get("size", 0))
            except (TypeError, ValueError):
                size_bytes = 0
            device_type = entry.get("type", "")
            if type_filter is None or device_type == type_filter:
                devices.append(BlockDevice(
                    name=entry.get("name", ""),
                    size_bytes=size_bytes,
                    type=device_type,
                    mountpoint=entry.get("mountpoint"),
                ))
            for child in entry.get("children", []):
                stack.append(child)
        return devices

    def wipe_signatures(self, device: str) -> StorageActionResult:
        """
            Run ``wipefs -a <device>``.
        """
        command = ["wipefs", "-a", device]
        return self._dispatch(command=command, detail=device)

    def create_filesystem(
        self,
        device: str,
        fs_type: str,
        *,
        label: Optional[str] = None,
        force: bool = False,
    ) -> StorageActionResult:
        """
            Create a filesystem on ``device`` using ``mkfs.<fs_type>``.
        """
        command = ["mkfs.{}".format(fs_type)]
        if force:
            command.append("-F")
        if label is not None:
            command.extend(["-L", label])
        command.append(device)
        return self._dispatch(command=command, detail=device)

    def create_pv(self, device: str, *, force: bool = False) -> StorageActionResult:
        """
            Initialise an LVM physical volume via ``pvcreate``.
        """
        command = ["pvcreate"]
        if force:
            command.append("-ff")
        command.append(device)
        return self._dispatch(command=command, detail=device)

    def create_vg(
        self,
        vg_name: str,
        pv_list: list[str],
    ) -> StorageActionResult:
        """
            Create an LVM volume group via ``vgcreate``.
        """
        command = ["vgcreate", vg_name] + list(pv_list)
        return self._dispatch(command=command, detail=vg_name)

    def extend_vg(
        self,
        vg_name: str,
        pv_list: list[str],
    ) -> StorageActionResult:
        """
            Extend an LVM volume group via ``vgextend``.
        """
        command = ["vgextend", vg_name] + list(pv_list)
        return self._dispatch(command=command, detail=vg_name)

    def create_lv(
        self,
        vg_name: str,
        lv_name: str,
        *,
        size: Optional[str] = None,
        extents: Optional[str] = None,
    ) -> StorageActionResult:
        """
            Create an LVM logical volume via ``lvcreate``.
        """
        if size is None and extents is None:
            return StorageActionResult(
                status=StorageActionStatus.FAILED,
                detail=lv_name,
                stderr="size or extents is required",
            )
        command = ["lvcreate", "-n", lv_name]
        if size is not None:
            command.extend(["-L", size])
        else:
            command.extend(["-l", extents])
        command.append(vg_name)
        return self._dispatch(command=command, detail=lv_name)

    def remove_lv(
        self,
        vg_name: str,
        lv_name: str,
    ) -> StorageActionResult:
        """
            Remove an LV via ``lvremove -f``.
        """
        command = ["lvremove", "-f", "{}/{}".format(vg_name, lv_name)]
        return self._dispatch(command=command, detail=lv_name)

    def list_pvs(self) -> list[PhysicalVolume]:
        """
            Enumerate PVs via ``pvs --reportformat json``.
        """
        run_result = self._runner.run(
            ["pvs", "--units", "b", "--reportformat", "json"], elevated=False,
        )
        if run_result.exit_code != 0:
            return []
        pvs: list[PhysicalVolume] = []
        try:
            data = json.loads(run_result.stdout)
        except json.JSONDecodeError:
            return pvs
        reports = data.get("report", [])
        for report in reports:
            for entry in report.get("pv", []):
                pvs.append(PhysicalVolume(
                    pv_name=entry.get("pv_name", ""),
                    vg_name=entry.get("vg_name", ""),
                    size_bytes=_parse_lvm_size(entry.get("pv_size", "0")),
                    free_bytes=_parse_lvm_size(entry.get("pv_free", "0")),
                ))
        return pvs

    def list_vgs(self) -> list[VolumeGroup]:
        """
            Enumerate VGs via ``vgs --reportformat json``.
        """
        run_result = self._runner.run(
            ["vgs", "--units", "b", "--reportformat", "json"], elevated=False,
        )
        if run_result.exit_code != 0:
            return []
        vgs: list[VolumeGroup] = []
        try:
            data = json.loads(run_result.stdout)
        except json.JSONDecodeError:
            return vgs
        reports = data.get("report", [])
        for report in reports:
            for entry in report.get("vg", []):
                vgs.append(VolumeGroup(
                    vg_name=entry.get("vg_name", ""),
                    size_bytes=_parse_lvm_size(entry.get("vg_size", "0")),
                    free_bytes=_parse_lvm_size(entry.get("vg_free", "0")),
                ))
        return vgs

    def list_lvs(self) -> list[LogicalVolume]:
        """
            Enumerate LVs via ``lvs --reportformat json``.
        """
        run_result = self._runner.run(
            ["lvs", "--units", "b", "--reportformat", "json"], elevated=False,
        )
        if run_result.exit_code != 0:
            return []
        lvs: list[LogicalVolume] = []
        try:
            data = json.loads(run_result.stdout)
        except json.JSONDecodeError:
            return lvs
        reports = data.get("report", [])
        for report in reports:
            for entry in report.get("lv", []):
                lvs.append(LogicalVolume(
                    lv_name=entry.get("lv_name", ""),
                    vg_name=entry.get("vg_name", ""),
                    size_bytes=_parse_lvm_size(entry.get("lv_size", "0")),
                ))
        return lvs

    def _dispatch(
        self,
        *,
        command: list[str],
        detail: str,
    ) -> StorageActionResult:
        """
            Dispatch a storage command honouring dry-run.
        """
        if self._config.dry_run:
            return StorageActionResult(
                status=StorageActionStatus.DRY_RUN,
                detail=detail, command=command,
            )
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = StorageActionStatus.SUCCESS if run_result.exit_code == 0 else StorageActionStatus.FAILED
        return StorageActionResult(
            status=status, detail=detail,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )


def _parse_lvm_size(size_str: str) -> int:
    """
        Parse an LVM JSON size string (``"<number>B"``) into bytes.
    """
    token = size_str.strip().rstrip("B")
    try:
        return int(token)
    except ValueError:
        return 0
