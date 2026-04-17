# Implementation Plan

- [ ] 1. Create the `storagemanagement` module file
  - Create `source/packages/automojo/sysman/linux/redhat/storagemanagement.py`
  - Add module-level docstring describing purpose and coordinator-driven design
  - Note in the docstring that this feature complements — does not duplicate — the
    mount / `/etc/fstab` behaviour of `filesystem-management`
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15_

- [ ] 2. Define status enum and core result dataclass
  - Implement `StorageActionStatus` enum with values: `SUCCESS`, `FAILED`, `SKIPPED`,
    `DRY_RUN`
  - Implement `StorageActionResult` dataclass with fields: `status`, `target`,
    `changed`, `exit_code`, `stdout`, `stderr`, `command`, `detail`
  - _Requirements: 3, 4, 5, 6, 7, 8, 9, 10, 12, 14_

- [ ] 3. Define block and partition dataclasses
  - Implement `BlockDevice` dataclass with fields: `name`, `kname`, `pkname`, `type`,
    `size`, `fstype`, `fsuse_pct`, `uuid`, `mountpoint`, `ro`, `rm`, `model`,
    `serial`, `parent`
  - Implement `PartitionTable` dataclass with fields: `type`, `size_bytes`,
    `partitions`
  - Implement `Partition` dataclass with fields: `number`, `start`, `end`, `size`,
    `fs_type`, `flags`
  - _Requirements: 1, 2_

- [ ] 4. Define LVM dataclasses
  - Implement `PhysicalVolume` dataclass with fields: `pv_name`, `vg_name`,
    `pv_uuid`, `pv_size`, `pv_free`
  - Implement `VolumeGroup` dataclass with fields: `vg_name`, `vg_uuid`, `pv_count`,
    `lv_count`, `vg_size`, `vg_free`
  - Implement `LogicalVolume` dataclass with fields: `lv_name`, `vg_name`, `lv_uuid`,
    `lv_path`, `lv_size`, `seg_type`, `origin`, `pool_lv`
  - _Requirements: 11_

- [ ] 5. Define RAID dataclass
  - Implement `RaidArray` dataclass with fields: `device`, `level`, `state`,
    `members`, `active`, `working`, `failed`, `spare`
  - _Requirements: 12, 13_

- [ ] 6. Define typed exceptions
  - Implement `StorageQueryError` with `exit_code` and `stderr` attributes
  - _Requirements: 1, 2, 11, 13_

- [ ] 7. Implement `StorageManagerConfig` dataclass
  - Add `dry_run: bool = False` field
  - Add `elevated: bool = False` field
  - _Requirements: 3.6, 4.6, 5.5, 6.4, 7.8, 8.5, 9.6, 10.7, 12.5, 14.4, 15_

- [ ] 8. Implement `StorageManager.__init__` and `list_block_devices()`
  - Accept `ICommandRunner`, `IFileOperator`, and `StorageManagerConfig` as
    constructor arguments
  - Initialize all instance variables in `__init__`
  - Implement `list_block_devices(type_filter=None)` using
    `lsblk -J -b -o NAME,KNAME,PKNAME,TYPE,SIZE,FSTYPE,FSUSE%,UUID,MOUNTPOINT,RO,RM,MODEL,SERIAL`
  - Parse the lsblk JSON output into a list of `BlockDevice` records, populating the
    `parent` field from `PKNAME`
  - Apply the optional `type_filter` to return only matching entries
  - Raise `StorageQueryError` on non-zero exit
  - _Requirements: 1_

- [ ] 9. Implement `query_partition_table()`
  - Build `parted -s -m <device> unit B print` command
  - Parse parted machine-readable output into `PartitionTable` with `type`,
    `size_bytes`, and a list of `Partition` records
  - Return a table with `type="unknown"` and empty partition list when parted reports
    an unrecognized label
  - Raise `StorageQueryError` on any other failure
  - _Requirements: 2_

- [ ] 10. Implement `create_partition_table()`
  - Query the current table via `query_partition_table()`
  - If the existing table type matches and `force=False` return `SKIPPED`
  - Consult `list_block_devices()` for mounted children; return `FAILED` naming
    the mount points when any child is mounted
  - Honour `dry_run`; return `DRY_RUN` result without calling runner
  - Build `parted -s <device> mklabel <gpt|msdos>` and call runner with
    `elevated=config.elevated`
  - Map runner result to `StorageActionResult`
  - _Requirements: 3_

- [ ] 11. Implement `create_partition()` and `remove_partition()`
  - For `create_partition()`: query current table; return `SKIPPED` when a partition
    with the same start, end, and type already exists
  - Build `parted -s <device> mkpart <type> <fs> <start> <end>`, then follow with
    `parted ... name <n> <label>` and `parted ... set <n> <flag> on` for each flag
  - For `remove_partition()`: consult `list_block_devices()` for a mount on the
    target partition; return `FAILED` naming the mount when mounted
  - Return `SKIPPED` when the partition number does not exist
  - Build `parted -s <device> rm <n>`
  - Honour `dry_run` on both methods; pass `elevated=config.elevated` on the runner
  - _Requirements: 4, 5_

- [ ] 12. Implement `wipe_signatures()`
  - Consult `list_block_devices()` for the target and every child; return `FAILED`
    naming the mounted path when any is mounted
  - Query `blkid` or `list_block_devices()` to determine whether any signatures are
    present; when none, return `SUCCESS` with `changed=False` without invoking the
    runner
  - Honour `dry_run`
  - Build `wipefs -a <device>` and call runner with `elevated=config.elevated`
  - _Requirements: 6_

- [ ] 13. Implement `create_filesystem()`
  - Consult `list_block_devices()` for current `fstype`; when it matches the
    requested type and `force=False` return `SKIPPED`
  - Consult `list_block_devices()` for an active mount on the device; return
    `FAILED` when mounted
  - Dispatch to the correct tool by fs_type: `mkfs.xfs`, `mkfs.ext4`, `mkfs.ext3`,
    `mkfs.vfat`, or `mkswap`
  - Apply the label flag (`-L` for xfs/ext, `-n` for vfat) when supplied
  - Apply the UUID flag (`-U` for ext; `mkswap -U`) when supplied; for xfs, invoke
    `xfs_admin -U <uuid>` after mkfs
  - Apply the force flag when `force=True` (`-f` xfs, `-F` ext, `-I` vfat)
  - Honour `dry_run`; pass `elevated=config.elevated`
  - Capture the resulting UUID by invoking `blkid -s UUID -o value <device>` on
    success and attach it to `StorageActionResult.detail`
  - _Requirements: 7_

- [ ] 14. Implement `create_pv()`
  - Consult `list_pvs()`; when the device is already a PV and `force=False` return
    `SKIPPED`
  - Honour `dry_run`
  - Build `pvcreate <device>`, adding `-ff -y` when `force=True`
  - Call runner with `elevated=config.elevated`; map result to `StorageActionResult`
  - _Requirements: 8_

- [ ] 15. Implement `create_vg()`, `extend_vg()`, and `remove_vg()`
  - For `create_vg()`: consult `list_vgs()` and compare PV set; return `SKIPPED`
    when a VG with the same name and exact PV set already exists
  - Build `vgcreate <vg-name> <pv-list>`
  - For `extend_vg()`: build `vgextend <vg-name> <pv-list>`
  - For `remove_vg()`: consult `list_lvs()` for active LVs in the VG; return
    `FAILED` with an explanatory error when present
  - Build `vgremove -f <vg-name>`
  - Honour `dry_run` on all three; pass `elevated=config.elevated`
  - _Requirements: 9_

- [ ] 16. Implement `create_lv()`, `extend_lv()`, `reduce_lv()`, and `remove_lv()`
  - For `create_lv()`: build `lvcreate -n <lv-name> -L <size>` or
    `lvcreate -n <lv-name> -l <extents> <vg-name>`
  - For `extend_lv()`: build `lvextend -L <size>|-l <extents> <vg>/<lv>`, adding
    `-r` when `resize_fs=True`
  - For `reduce_lv()`: when `confirm=False` return `FAILED` with an explanatory
    error without invoking the runner; otherwise build
    `lvreduce -L <size>|-l <extents> <vg>/<lv>`
  - For `remove_lv()`: build `lvremove -f <vg>/<lv>`
  - Consult `list_block_devices()` for an active mount; for `reduce_lv()` and
    `remove_lv()` return `FAILED` when mounted
  - Honour `dry_run` on all four; pass `elevated=config.elevated`
  - _Requirements: 10_

- [ ] 17. Implement `list_pvs()`, `list_vgs()`, and `list_lvs()`
  - Build `pvs --reportformat json --units b -o +vg_name,pv_uuid,pv_size,pv_free`
  - Build `vgs --reportformat json --units b -o +vg_uuid,pv_count,lv_count,vg_size,vg_free`
  - Build `lvs --reportformat json --units b -o +lv_uuid,lv_path,lv_size,seg_type,origin,pool_lv`
  - Parse each JSON report into `PhysicalVolume` / `VolumeGroup` / `LogicalVolume`
    records
  - Raise `StorageQueryError` when any command exits non-zero
  - Pass `elevated=False` (read-only) regardless of `config.elevated`
  - _Requirements: 11_

- [ ] 18. Implement `create_raid_array()`
  - Consult `list_block_devices()` and `list_raid_arrays()` for each member; return
    `FAILED` with an error naming the conflicting use when any member is mounted
    or already part of another array
  - Honour `dry_run`
  - Build `mdadm --create /dev/<name> --level=<n> --raid-devices=<count>`, appending
    `--metadata=<v>`, `--chunk=<k>`, and `--bitmap=<b>` when supplied, and
    `--run --force` only when the coordinator asks for them
  - Call runner with `elevated=config.elevated`
  - On success, capture the array UUID via `mdadm --detail` and place it in
    `StorageActionResult.detail`
  - _Requirements: 12_

- [ ] 19. Implement `query_raid_array()` and `list_raid_arrays()`
  - For `query_raid_array()`: build `mdadm --detail /dev/<name>`; parse into a
    `RaidArray` record; raise `StorageQueryError` when the device is not a RAID
    array
  - For `list_raid_arrays()`: read `/proc/mdstat` via `cat /proc/mdstat`; parse each
    active-array stanza into a `RaidArray` record (device, level, state, members)
    and then supplement each record with counts from `mdadm --detail`
  - Pass `elevated=False` (read-only) regardless of `config.elevated`
  - _Requirements: 13_

- [ ] 20. Implement `refresh_mdadm_conf()`
  - Build `mdadm --detail --scan`; capture ARRAY lines from stdout
  - Read existing `/etc/mdadm.conf` via the file operator; preserve non-ARRAY lines
  - When the existing file already contains the exact captured ARRAY lines return
    `SKIPPED` without invoking the file operator's write method
  - Honour `dry_run`; return `DRY_RUN` without writing
  - Write the combined content back via the file operator with `mode=0o644`,
    `owner="root"`, `group="root"`, and `elevated=config.elevated`
  - _Requirements: 14_

- [ ] 21. Create test module and mock infrastructure
  - Create `source/testroots/testbase/automojo/tests/sysman/linux/redhat/test_storagemanagement.py`
  - Implement `MockCommandRunner` that returns configurable `RunResult` values by
    matching on the leading argv
  - Implement `MockFileOperator` that records reads and writes and returns
    configurable payloads
  - Set up hypothesis testing framework configuration
  - Create canned JSON / text fixtures for `lsblk`, `pvs`, `vgs`, `lvs`, `parted
    print`, `/proc/mdstat`, and `mdadm --detail`
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15_

- [ ] 22. Write property-based test for partition-table idempotency
  - **Property 1: Partition-Table Idempotency**
  - Generate random (existing table type, requested table type) pairs
  - Verify `create_partition_table()` with matching type and `force=False` returns
    `SKIPPED` and never invokes the runner
  - **Validates: Requirements 3.2**
  - _Requirements: 3_

- [ ] 23. Write property-based test for wipe refusal when mounted
  - **Property 2: Wipe Refusal When Mounted**
  - Generate random device trees with a mounted child injected at a random depth
  - Verify `wipe_signatures()` returns `FAILED` naming the mount and never invokes
    the runner
  - **Validates: Requirements 6.2**
  - _Requirements: 6_

- [ ] 24. Write property-based test for mkfs skip on same filesystem
  - **Property 3: mkfs Skip When Same Filesystem**
  - Generate random (existing fstype, requested fstype) pairs with `force=False`
  - Verify `create_filesystem()` returns `SKIPPED` on match and calls the runner
    otherwise
  - **Validates: Requirements 7.4**
  - _Requirements: 7_

- [ ] 25. Write property-based test for LVM create-then-remove symmetry
  - **Property 4: LVM Create-then-Remove Symmetry**
  - Generate random PV / VG / LV name sets
  - Drive `create_pv → create_vg → create_lv` followed by
    `remove_lv → remove_vg` against a stateful `MockCommandRunner`
  - Verify the mock's recorded state ends empty
  - **Validates: Requirements 8, 9, 10, 11**
  - _Requirements: 8, 9, 10, 11_

- [ ] 26. Write property-based test for `lvreduce` confirmation
  - **Property 5: lvreduce Confirmation Required**
  - Generate random (size, extents, confirm) triples
  - Verify `reduce_lv()` with `confirm=False` returns `FAILED` and never invokes the
    runner; `confirm=True` proceeds
  - **Validates: Requirements 10.3**
  - _Requirements: 10_

- [ ] 27. Write property-based test for RAID create busy-member refusal
  - **Property 6: RAID Create Refuses Busy Members**
  - Generate random member lists with one member randomly marked mounted or
    already-in-array
  - Verify `create_raid_array()` returns `FAILED` naming the conflict and never
    invokes the runner
  - **Validates: Requirements 12.3**
  - _Requirements: 12_

- [ ] 28. Write property-based test for LV/VG remove refusal on active mount
  - **Property 7: LV/VG Remove Refusal on Active Mount**
  - Generate random VG/LV hierarchies with a mount randomly injected on one LV
  - Verify `reduce_lv()`, `remove_lv()`, and `remove_vg()` return `FAILED`
  - **Validates: Requirements 9.5, 10.5**
  - _Requirements: 9, 10_

- [ ] 29. Write property-based test for LVM JSON parsing stability
  - **Property 8: JSON Parsing Stability for LVM Queries**
  - Generate random (but well-formed) `pvs`/`vgs`/`lvs` JSON reports
  - Verify the same input yields the same parsed record list across multiple calls
    and that every report entry appears in the output
  - **Validates: Requirements 11.4**
  - _Requirements: 11_

- [ ] 30. Write property-based test for dry-run safety
  - **Property 9: Dry-Run Safety**
  - Generate random mutating operations across the full StorageManager surface with
    `dry_run=True`
  - Verify the runner and file operator are never invoked with mutations and every
    result carries `DRY_RUN` status
  - **Validates: Requirements 3.6, 4.6, 5.5, 6.4, 7.8, 8.5, 9.6, 10.7, 12.5, 14.4**
  - _Requirements: 3, 4, 5, 6, 7, 8, 9, 10, 12, 14_

- [ ] 31. Write property-based test for elevation consistency
  - **Property 10: Elevation Consistency**
  - Generate random operations with `elevated=True/False`
  - Verify mutating operations pass `elevated` verbatim to runner/file operator
  - Verify read-only queries (`lsblk`, `blkid`, `parted print`, `pvs`, `vgs`, `lvs`,
    `mdadm --detail`, `cat /proc/mdstat`) are always invoked with `elevated=False`
  - **Validates: Requirements 15.1, 15.3**
  - _Requirements: 15_

- [ ] 32. Write property-based test for status mapping correctness
  - **Property 11: Status Mapping Correctness**
  - Generate random exit codes from the command runner for each mutating operation
  - Verify `0` maps to `SUCCESS` and non-zero maps to `FAILED`
  - **Validates: Requirements 3.4, 3.5, 4.5, 5.4, 7.7, 8.4, 10.6, 12.4**
  - _Requirements: 3, 4, 5, 7, 8, 10, 12_

- [ ] 33. Write property-based test for mdadm.conf idempotent refresh
  - **Property 12: mdadm.conf Idempotent Refresh**
  - Generate random ARRAY-line sets and random permutations of matching file
    contents
  - Verify `refresh_mdadm_conf()` returns `SKIPPED` when the captured and existing
    ARRAY lines match and never invokes `IFileOperator.write_text()`
  - **Validates: Requirements 14.3**
  - _Requirements: 14_

- [ ] 34. Write property-based test for query error propagation
  - **Property 13: Query Error Propagation**
  - Generate random non-zero exit codes for read-only query commands
  - Verify each query method raises `StorageQueryError` carrying the exit code and
    stderr
  - **Validates: Requirements 1.4, 2.4, 11.5, 13.4**
  - _Requirements: 1, 2, 11, 13_

- [ ] 35. Write property-based test for partition boundary idempotency
  - **Property 14: Partition Boundary Idempotency**
  - Generate random (start, end, type) triples matching an existing partition
  - Verify `create_partition()` returns `SKIPPED` without invoking the runner
  - **Validates: Requirements 4.4**
  - _Requirements: 4_

- [ ] 36. Write unit tests for `list_block_devices()`
  - Test: parses canned `lsblk` JSON into populated `BlockDevice` list
  - Test: `parent` field is populated from `PKNAME`
  - Test: `type_filter="disk"` returns only disk entries
  - Test: non-zero exit raises `StorageQueryError`
  - _Requirements: 1_

- [ ] 37. Write unit tests for `query_partition_table()`
  - Test: parsed output produces a `PartitionTable` with correct type, size, and
    partition records
  - Test: unrecognized label returns `type="unknown"` with empty partition list
  - Test: non-zero exit raises `StorageQueryError`
  - _Requirements: 2_

- [ ] 38. Write unit tests for `create_partition_table()`
  - Test: matching existing type + `force=False` returns `SKIPPED`, runner not called
  - Test: mounted child returns `FAILED` naming the mount, runner not called
  - Test: happy path returns `SUCCESS` with correct `parted mklabel` argv
  - Test: dry-run mode returns `DRY_RUN`, runner not called
  - Test: elevated mode passes `elevated=True`
  - _Requirements: 3_

- [ ] 39. Write unit tests for `create_partition()` and `remove_partition()`
  - Test: existing partition at same boundaries returns `SKIPPED` on create
  - Test: create success returns `SUCCESS` with the assigned partition number
  - Test: name and flags result in the correct follow-up `parted name` and
    `parted set` calls
  - Test: remove of mounted partition returns `FAILED` naming the mount
  - Test: remove of nonexistent partition returns `SKIPPED`
  - Test: dry-run mode on both returns `DRY_RUN`, runner not called
  - _Requirements: 4, 5_

- [ ] 40. Write unit tests for `wipe_signatures()`
  - Test: target mounted returns `FAILED`, runner not called
  - Test: child mounted returns `FAILED`, runner not called
  - Test: no signatures present returns `SUCCESS` with `changed=False`
  - Test: dry-run mode returns `DRY_RUN`, runner not called
  - Test: happy path issues `wipefs -a <device>` with `elevated=True`
  - _Requirements: 6_

- [ ] 41. Write unit tests for `create_filesystem()`
  - Test: matching fstype + `force=False` returns `SKIPPED`, runner not called
  - Test: device mounted returns `FAILED`, runner not called
  - Test: xfs happy path issues `mkfs.xfs` with `-L` and follows with `xfs_admin -U`
    when UUID supplied
  - Test: ext4 happy path issues `mkfs.ext4` with `-L` and `-U`
  - Test: vfat happy path issues `mkfs.vfat` with `-n`
  - Test: swap happy path issues `mkswap`
  - Test: `force=True` adds `-f` / `-F` / `-I`
  - Test: result `detail` carries the UUID captured from `blkid`
  - Test: dry-run returns `DRY_RUN`
  - _Requirements: 7_

- [ ] 42. Write unit tests for `create_pv()`
  - Test: device already a PV + `force=False` returns `SKIPPED`
  - Test: happy path issues `pvcreate <device>` with `elevated=True`
  - Test: `force=True` adds `-ff -y`
  - Test: dry-run returns `DRY_RUN`
  - _Requirements: 8_

- [ ] 43. Write unit tests for `create_vg()`, `extend_vg()`, `remove_vg()`
  - Test: create when VG + exact PV set exists returns `SKIPPED`
  - Test: create happy path issues `vgcreate <vg-name> <pv-list>`
  - Test: extend happy path issues `vgextend <vg-name> <pv-list>`
  - Test: remove when VG has active LVs returns `FAILED` with explanation
  - Test: remove happy path issues `vgremove -f <vg-name>`
  - Test: dry-run on each returns `DRY_RUN`
  - _Requirements: 9_

- [ ] 44. Write unit tests for `create_lv()`, `extend_lv()`, `reduce_lv()`,
  `remove_lv()`
  - Test: create with size issues `lvcreate -n -L <size>`
  - Test: create with extents issues `lvcreate -n -l <extents>`
  - Test: extend with `resize_fs=True` adds `-r`
  - Test: reduce with `confirm=False` returns `FAILED`, runner not called
  - Test: reduce with `confirm=True` issues `lvreduce -L <size> <vg>/<lv>`
  - Test: remove of mounted LV returns `FAILED`
  - Test: remove happy path issues `lvremove -f <vg>/<lv>`
  - Test: dry-run on each returns `DRY_RUN`
  - _Requirements: 10_

- [ ] 45. Write unit tests for `list_pvs()`, `list_vgs()`, `list_lvs()`
  - Test: canned `pvs` JSON parses into populated `PhysicalVolume` list
  - Test: canned `vgs` JSON parses into populated `VolumeGroup` list
  - Test: canned `lvs` JSON parses into populated `LogicalVolume` list
  - Test: non-zero exit on any of the three raises `StorageQueryError`
  - Test: each query is invoked with `elevated=False`
  - _Requirements: 11_

- [ ] 46. Write unit tests for `create_raid_array()`
  - Test: busy member (mounted) returns `FAILED` naming the mount
  - Test: member already in array returns `FAILED` naming the array
  - Test: happy path issues `mdadm --create` with correct level, device count, and
    member list
  - Test: metadata / chunk / bitmap flags are appended when supplied
  - Test: `--run --force` only appear when requested
  - Test: result `detail` carries the array UUID captured from `mdadm --detail`
  - Test: dry-run returns `DRY_RUN`
  - _Requirements: 12_

- [ ] 47. Write unit tests for `query_raid_array()` and `list_raid_arrays()`
  - Test: canned `mdadm --detail` output parses into `RaidArray` with correct counts
  - Test: not-a-RAID-device raises `StorageQueryError`
  - Test: canned `/proc/mdstat` enumerates every active array
  - Test: each query is invoked with `elevated=False`
  - _Requirements: 13_

- [ ] 48. Write unit tests for `refresh_mdadm_conf()`
  - Test: scan matches existing ARRAY lines → returns `SKIPPED`,
    `write_text` not called
  - Test: scan differs from file → writes combined content preserving non-ARRAY
    lines, with `mode=0o644`, `owner="root"`, `group="root"`, and
    `elevated=config.elevated`
  - Test: dry-run → returns `DRY_RUN`, `write_text` not called
  - Test: scan command failure surfaces as `FAILED` (or `StorageQueryError` if the
    underlying contract is to raise) — mirror the runtime behaviour chosen in the
    implementation task
  - _Requirements: 14_

- [ ] 49. Write unit tests for elevation behaviour
  - Test: all mutating methods pass `elevated=True` when
    `StorageManagerConfig.elevated=True`
  - Test: all read-only query methods pass `elevated=False` regardless of config
  - _Requirements: 15_

- [ ] 50. Checkpoint - Ensure all tests pass
  - Run all unit tests and property-based tests
  - Verify all properties pass with 100+ iterations
  - Ensure all tests pass, ask the user if questions arise
