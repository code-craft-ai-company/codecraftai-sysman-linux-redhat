# Requirements

## Introduction

The storage management feature provides the `codecraftai-sysman-linux-redhat` package with
the ability to manage block-level storage on Red Hat Linux systems: block-device
discovery, partitioning, filesystem creation, LVM (physical volumes, volume groups,
logical volumes), and software RAID (`mdadm`). It complements — and does not duplicate
— the mount and `/etc/fstab` management already provided by the `filesystem-management`
feature.

A maintenance coordinator uses this feature to bring a system's storage stack into a
declared desired state. All commands (`lsblk`, `blkid`, `parted`, `sfdisk`, `wipefs`,
`mkfs.*`, `pvcreate`, `pvs`, `vgcreate`, `vgextend`, `vgs`, `lvcreate`, `lvextend`,
`lvreduce`, `lvremove`, `lvs`, `mdadm`, `cat /proc/mdstat`) are executed through the
`codecraftai-interfaces` command-runner abstraction. All writes to LVM configuration and
`mdadm.conf` use the `codecraftai-interfaces` file-operator abstraction. All mutating
operations respect dry-run mode.

---

## Requirements

### Requirement 1: Enumerate Block Devices

**User Story:** As a maintenance coordinator, I want to enumerate all block devices on
the system with their type, size, parent, filesystem, and mount point, so that I can
make decisions about partitioning and LVM without blindly issuing commands.

#### Acceptance Criteria

1. WHEN the coordinator requests the device list THE SYSTEM SHALL invoke
   `lsblk -J -b -o NAME,KNAME,PKNAME,TYPE,SIZE,FSTYPE,FSUSE%,UUID,MOUNTPOINT,RO,RM,MODEL,SERIAL`.
2. THE SYSTEM SHALL return a list of `BlockDevice` records, preserving the parent/child
   relationship via the `parent` field populated from `PKNAME`.
3. WHEN the coordinator filters by device type (e.g. `disk`, `part`, `lvm`, `raid1`)
   THE SYSTEM SHALL return only matching entries.
4. WHEN the command fails THE SYSTEM SHALL raise a `StorageQueryError` with the exit
   code and captured stderr.

---

### Requirement 2: Query Disk Partition Table

**User Story:** As a maintenance coordinator, I want to query the partition table of a
disk, so that I can verify the layout before making changes.

#### Acceptance Criteria

1. WHEN the coordinator queries a disk's partition table THE SYSTEM SHALL invoke
   `parted -s -m <device> unit B print`.
2. THE SYSTEM SHALL return a `PartitionTable` record containing the table type
   (`msdos`, `gpt`, `loop`, `unknown`), the disk size in bytes, and a list of
   `Partition` records (number, start, end, size, filesystem type, flags).
3. WHEN the device has no recognized partition table THE SYSTEM SHALL return a
   `PartitionTable` record with type `unknown` and an empty partition list.
4. WHEN the command fails for any other reason THE SYSTEM SHALL raise a
   `StorageQueryError`.

---

### Requirement 3: Create a Partition Table

**User Story:** As a maintenance coordinator, I want to create a new partition table
(GPT or MSDOS) on a disk, so that the disk can be used for partitioning.

#### Acceptance Criteria

1. WHEN the coordinator requests creation of a table THE SYSTEM SHALL invoke
   `parted -s <device> mklabel <gpt|msdos>` with elevated privileges.
2. WHEN the disk already has a partition table of the requested type AND the
   coordinator did not supply `force=True` THE SYSTEM SHALL skip execution and return
   a `StorageActionResult` with status `SKIPPED`.
3. WHEN the disk has mounted partitions THE SYSTEM SHALL NOT execute the command and
   SHALL return a `StorageActionResult` with status `FAILED` and an error naming the
   mounted paths.
4. WHEN the operation succeeds THE SYSTEM SHALL return a `StorageActionResult` with
   status `SUCCESS`.
5. WHEN the operation fails THE SYSTEM SHALL return a `StorageActionResult` with
   status `FAILED`, exit code, and captured stderr.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 4: Create a Partition

**User Story:** As a maintenance coordinator, I want to create a partition on a disk
with a start, end, and type, so that the space is allocated for a filesystem or LVM.

#### Acceptance Criteria

1. WHEN the coordinator supplies a device, type (`primary`, `extended`, `logical`),
   filesystem hint, start, and end THE SYSTEM SHALL invoke
   `parted -s <device> mkpart <type> <fs> <start> <end>` with elevated privileges.
2. WHEN the coordinator requests a GPT partition name THE SYSTEM SHALL follow with
   `parted -s <device> name <n> <label>`.
3. WHEN the coordinator requests flags (e.g. `lvm`, `boot`, `esp`) THE SYSTEM SHALL
   apply each via `parted -s <device> set <n> <flag> on`.
4. WHEN the requested partition already exists at the same boundaries THE SYSTEM SHALL
   skip execution and return a `StorageActionResult` with status `SKIPPED`.
5. WHEN the operation succeeds THE SYSTEM SHALL return a `StorageActionResult` with
   status `SUCCESS` and the assigned partition number.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 5: Remove a Partition

**User Story:** As a maintenance coordinator, I want to remove a partition by number,
so that disk space can be reallocated.

#### Acceptance Criteria

1. WHEN the coordinator requests removal THE SYSTEM SHALL invoke
   `parted -s <device> rm <n>` with elevated privileges.
2. WHEN the partition is currently mounted THE SYSTEM SHALL NOT execute the command
   and SHALL return a `StorageActionResult` with status `FAILED` and an error naming
   the mount point.
3. WHEN the partition number does not exist THE SYSTEM SHALL skip execution and return
   a `StorageActionResult` with status `SKIPPED`.
4. WHEN the operation succeeds THE SYSTEM SHALL return a `StorageActionResult` with
   status `SUCCESS`.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 6: Wipe Filesystem Signatures

**User Story:** As a maintenance coordinator, I want to wipe existing filesystem and
partition-table signatures on a device, so that the device is cleanly prepared for
reuse.

#### Acceptance Criteria

1. WHEN the coordinator requests a wipe THE SYSTEM SHALL invoke
   `wipefs -a <device>` with elevated privileges.
2. WHEN the target device or any child is mounted THE SYSTEM SHALL NOT execute the
   command and SHALL return a `StorageActionResult` with status `FAILED`.
3. WHEN no signatures are present THE SYSTEM SHALL return a `StorageActionResult` with
   status `SUCCESS` and `changed=False`.
4. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 7: Create a Filesystem

**User Story:** As a maintenance coordinator, I want to create a filesystem (`xfs`,
`ext4`, `ext3`, `vfat`, `swap`) on a block device, so that it can be mounted or
activated.

#### Acceptance Criteria

1. WHEN the coordinator supplies a filesystem type and device THE SYSTEM SHALL invoke
   the appropriate tool (`mkfs.xfs`, `mkfs.ext4`, `mkfs.ext3`, `mkfs.vfat`,
   `mkswap`) with elevated privileges.
2. WHEN the coordinator supplies a label THE SYSTEM SHALL pass the tool's label flag
   (`-L` for xfs/ext, `-n` for vfat).
3. WHEN the coordinator supplies a UUID THE SYSTEM SHALL pass the tool's UUID flag
   where supported (`-U` for ext; xfs UUIDs are set post-format via `xfs_admin -U`).
4. WHEN the device already carries a filesystem of the same type AND
   `force=False` THE SYSTEM SHALL skip execution and return a `StorageActionResult`
   with status `SKIPPED`.
5. WHEN `force=True` THE SYSTEM SHALL pass the tool's force flag (`-f` for xfs, `-F`
   for ext, `-I` for vfat).
6. WHEN the device is currently mounted THE SYSTEM SHALL NOT execute the command and
   SHALL return a `StorageActionResult` with status `FAILED`.
7. WHEN the operation succeeds THE SYSTEM SHALL return a `StorageActionResult` with
   status `SUCCESS` and the resulting UUID captured via `blkid`.
8. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 8: Create a Physical Volume (LVM)

**User Story:** As a maintenance coordinator, I want to initialize a device as an LVM
physical volume, so that it can be added to a volume group.

#### Acceptance Criteria

1. WHEN the coordinator supplies a device THE SYSTEM SHALL invoke
   `pvcreate <device>` with elevated privileges.
2. WHEN the device already has a PV signature AND `force=False` THE SYSTEM SHALL skip
   execution and return a `StorageActionResult` with status `SKIPPED`.
3. WHEN `force=True` THE SYSTEM SHALL pass `-ff -y` to `pvcreate`.
4. WHEN the operation succeeds THE SYSTEM SHALL return a `StorageActionResult` with
   status `SUCCESS`.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 9: Manage Volume Groups

**User Story:** As a maintenance coordinator, I want to create, extend, and remove LVM
volume groups, so that the pool of LVM capacity can be shaped.

#### Acceptance Criteria

1. WHEN the coordinator requests VG creation THE SYSTEM SHALL invoke
   `vgcreate <vg-name> <pv-list>` with elevated privileges.
2. WHEN the coordinator requests VG extension THE SYSTEM SHALL invoke
   `vgextend <vg-name> <pv-list>` with elevated privileges.
3. WHEN the coordinator requests VG removal THE SYSTEM SHALL invoke
   `vgremove -f <vg-name>` with elevated privileges.
4. WHEN the VG already exists with the exact requested PV set AND the action is
   `create` THE SYSTEM SHALL skip execution and return a `StorageActionResult` with
   status `SKIPPED`.
5. WHEN the VG contains active LVs AND the action is `remove` THE SYSTEM SHALL NOT
   execute the command and SHALL return a `StorageActionResult` with status `FAILED`
   and an explanatory error.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 10: Manage Logical Volumes

**User Story:** As a maintenance coordinator, I want to create, extend, reduce, and
remove logical volumes, so that LVs can be shaped to match workload requirements.

#### Acceptance Criteria

1. WHEN the coordinator requests LV creation THE SYSTEM SHALL invoke
   `lvcreate -n <lv-name> -L <size>|-l <extents> <vg-name>` with elevated privileges.
2. WHEN the coordinator requests LV extension THE SYSTEM SHALL invoke
   `lvextend -L <size>|-l <extents> <vg>/<lv>` with elevated privileges, and SHALL
   pass `-r` when the coordinator requests resizing of the contained filesystem.
3. WHEN the coordinator requests LV reduction THE SYSTEM SHALL invoke
   `lvreduce -L <size>|-l <extents> <vg>/<lv>` with elevated privileges, and SHALL
   refuse to proceed unless the coordinator passes `confirm=True`.
4. WHEN the coordinator requests LV removal THE SYSTEM SHALL invoke
   `lvremove -f <vg>/<lv>` with elevated privileges.
5. WHEN the requested LV is currently mounted AND the action is `reduce` or `remove`
   THE SYSTEM SHALL NOT execute the command and SHALL return a `StorageActionResult`
   with status `FAILED`.
6. WHEN the operation succeeds THE SYSTEM SHALL return a `StorageActionResult` with
   status `SUCCESS`.
7. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 11: Query LVM State

**User Story:** As a maintenance coordinator, I want to query PVs, VGs, and LVs in a
machine-parseable form, so that I can compare actual LVM state against the declared
desired state.

#### Acceptance Criteria

1. WHEN the coordinator requests PV state THE SYSTEM SHALL invoke
   `pvs --reportformat json --units b -o +vg_name,pv_uuid,pv_size,pv_free`.
2. WHEN the coordinator requests VG state THE SYSTEM SHALL invoke
   `vgs --reportformat json --units b -o +vg_uuid,pv_count,lv_count,vg_size,vg_free`.
3. WHEN the coordinator requests LV state THE SYSTEM SHALL invoke
   `lvs --reportformat json --units b -o +lv_uuid,lv_path,lv_size,seg_type,origin,pool_lv`.
4. THE SYSTEM SHALL return lists of `PhysicalVolume`, `VolumeGroup`, and
   `LogicalVolume` records derived from the parsed JSON.
5. WHEN any command fails THE SYSTEM SHALL raise a `StorageQueryError`.

---

### Requirement 12: Create a Software RAID Array

**User Story:** As a maintenance coordinator, I want to create a software RAID array
across block devices, so that redundancy or striping can be provided below the
filesystem layer.

#### Acceptance Criteria

1. WHEN the coordinator supplies a RAID level, device name, and member devices THE
   SYSTEM SHALL invoke
   `mdadm --create /dev/<name> --level=<n> --raid-devices=<count> <members>` with
   elevated privileges, passing `--run --force` only when the coordinator requests it.
2. WHEN the coordinator supplies a metadata version, chunk size, or bitmap THE SYSTEM
   SHALL pass the corresponding flags (`--metadata`, `--chunk`, `--bitmap`).
3. WHEN any member device is currently mounted or is part of another array THE SYSTEM
   SHALL NOT execute the command and SHALL return a `StorageActionResult` with status
   `FAILED` and an error naming the conflicting use.
4. WHEN the operation succeeds THE SYSTEM SHALL return a `StorageActionResult` with
   status `SUCCESS`, the array device path, and the captured UUID.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 13: Query Software RAID State

**User Story:** As a maintenance coordinator, I want to query the state of software
RAID arrays, so that degraded arrays or rebuilds can be detected.

#### Acceptance Criteria

1. WHEN the coordinator requests a single array's state THE SYSTEM SHALL invoke
   `mdadm --detail /dev/<name>`.
2. WHEN the coordinator requests all arrays THE SYSTEM SHALL read `/proc/mdstat` and
   enumerate each active array.
3. THE SYSTEM SHALL return a list of `RaidArray` records containing `device`,
   `level`, `state`, `members`, `active`, `working`, `failed`, and `spare` counts.
4. WHEN the device is not a RAID array THE SYSTEM SHALL raise a
   `StorageQueryError`.

---

### Requirement 14: Manage mdadm Configuration

**User Story:** As a maintenance coordinator, I want to write a known-good set of ARRAY
lines into `/etc/mdadm.conf`, so that arrays assemble correctly at boot.

#### Acceptance Criteria

1. WHEN the coordinator requests a refresh of `/etc/mdadm.conf` THE SYSTEM SHALL
   invoke `mdadm --detail --scan` and capture the ARRAY output.
2. THE SYSTEM SHALL write the captured ARRAY lines together with the preserved
   non-ARRAY lines back to `/etc/mdadm.conf` through the file operator with mode
   `0644`, owner `root`, group `root`, and elevated privileges.
3. WHEN the existing file already contains the captured ARRAY lines THE SYSTEM SHALL
   skip the write and return a `StorageActionResult` with status `SKIPPED`.
4. WHEN dry-run mode is active THE SYSTEM SHALL NOT write to the file.

---

### Requirement 15: Elevated Privilege Execution

**User Story:** As a maintenance coordinator, I want all mutating storage commands to
run with the privileges required by the storage tools, so that operations succeed on
standard systems where the process does not run as root.

#### Acceptance Criteria

1. WHEN the system is configured to use sudo elevation THE SYSTEM SHALL prefix all
   mutating commands (`parted ... mklabel|mkpart|rm|set|name`, `wipefs`, `mkfs.*`,
   `mkswap`, `pvcreate`, `vgcreate|vgextend|vgremove`, `lvcreate|lvextend|lvreduce|lvremove`,
   `mdadm --create`, writes under `/etc/`) with the command runner's elevation
   mechanism.
2. WHEN the system is running as root THE SYSTEM SHALL NOT prefix commands with
   `sudo`.
3. WHEN read-only query commands (`lsblk`, `blkid`, `parted print`, `pvs`, `vgs`,
   `lvs`, `mdadm --detail`, `cat /proc/mdstat`) are invoked THE SYSTEM SHALL NOT
   require elevation, though the command runner MAY use elevation if the user lacks
   read permission on device nodes.
