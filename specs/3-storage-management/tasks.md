# Implementation Plan — Storage Management

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

Complements `3-filesystem-management` (mounts + `/etc/fstab`).  This
feature owns block-level concerns: partitioning, filesystem creation,
LVM, and RAID.

## Module and Models

- [x] 1. Create the `storagemanagement` module

- [x] 2. Define `StorageActionStatus` enum (SUCCESS / FAILED /
  SKIPPED / DRY_RUN)

- [x] 3. Define `StorageManagerConfig` (dry_run, elevated)

- [x] 4. Define `StorageActionResult` dataclass

- [x] 5. Define `BlockDevice`, `PhysicalVolume`, `VolumeGroup`,
  `LogicalVolume` query dataclasses

## StorageManager — Implemented

- [x] 6. Implement `__init__` accepting runner + optional config

- [x] 7. Implement `list_block_devices(include_fs=True)` — `lsblk
  --json [-f]` parsed into `list[BlockDevice]`

- [x] 8. Implement `wipe_signatures(device)` — `wipefs -a <device>`

- [x] 9. Implement `create_filesystem(device, fstype, label=None,
  force=False)` — `mkfs.<fstype> [-f] [-L <label>] <device>`

- [x] 10. Implement `create_pv(device, force=False)` — `pvcreate
  [--force] <device>`

- [x] 11. Implement `create_vg(name, pvs)` — `vgcreate <name>
  <pvs...>`

- [x] 12. Implement `extend_vg(name, pvs)` — `vgextend <name>
  <pvs...>`

- [x] 13. Implement `create_lv(name, vg, size=None, extents=None)`
  — `lvcreate -n <name> [-L <size>|-l <extents>] <vg>`; raises when
  neither size nor extents supplied

- [x] 14. Implement `remove_lv(name, vg, force=False)` — `lvremove
  [-f] /dev/<vg>/<name>`

- [x] 15. Implement `list_pvs()` / `list_vgs()` / `list_lvs()` —
  `pvs/vgs/lvs --reportformat json` parsed into dataclasses

- [x] 16. Implement `_dispatch` helper and `_parse_lvm_size`

## StorageManager — Gaps (specified but not implemented)

- [ ] 17. Partition management — `parted` / `sfdisk` wrappers for
  table creation, partition add/remove, partition type change

- [ ] 18. Software-RAID via `mdadm` — create array, inspect
  `/proc/mdstat`, manage `mdadm.conf` (entire RAID surface from the
  requirements is absent)

- [ ] 19. `extend_lv(name, vg, size)` and `reduce_lv(name, vg,
  size)` — currently only `create_lv` / `remove_lv`

- [ ] 20. `remove_vg(name)` / `remove_pv(device)` counterparts

- [ ] 21. `blkid` / `blkid -o export` parser returning UUIDs, labels,
  fstype per device

- [ ] 22. Filesystem resize — `resize2fs`, `xfs_growfs`

## Tests — Implemented

- [x] 23. `test_list_block_devices_parses_lsblk_json`
- [x] 24. `test_create_filesystem_dispatches_mkfs`
- [x] 25. `test_create_pv_and_vg_dispatch`
- [x] 26. `test_create_lv_requires_size_or_extents`
- [x] 27. `test_create_lv_with_size_dispatches_lvcreate`
- [x] 28. `test_list_pvs_parses_json_report`

## Tests — Gaps

- [ ] 29. Test `wipe_signatures` dispatch

- [ ] 30. Test `extend_vg` dispatch

- [ ] 31. Test `remove_lv` — with and without `force=True`

- [ ] 32. Test `list_vgs` / `list_lvs` parsers

- [ ] 33. Test dry-run short-circuits for every mutating method

- [ ] 34. Test `create_lv` with `extents` instead of `size`

## Follow-ups

- [ ] 35. **codecraftai-* reuse check**: no obvious sibling-package
  reuse for raw LVM; verify before building out tasks 17–22.
