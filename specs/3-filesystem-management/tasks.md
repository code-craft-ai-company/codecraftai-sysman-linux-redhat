# Implementation Plan — Filesystem Management

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `filesystemmanagement` module

- [x] 2. Define `FilesystemActionStatus` enum (SUCCESS / FAILED /
  SKIPPED / DRY_RUN)

- [x] 3. Define `FilesystemManagerConfig` dataclass (dry_run, elevated)

- [x] 4. Define `MountSpec` dataclass (device, mountpoint, fstype,
  options)

- [x] 5. Define `FstabEntry` dataclass (device, mountpoint, fstype,
  options, dump, pass_no)

- [x] 6. Define `FilesystemActionResult` dataclass

- [x] 7. Define `PathInfo`, `DiskUsageInfo`, `MountInfo` query-result
  dataclasses

## FilesystemManager — Implemented

- [x] 8. Implement `create_directory(path, mode, owner, group)` via
  `mkdir -p`, optional `chmod`, optional `chown`

- [x] 9. Implement `remove_directory(path, recursive=False)` via
  `rmdir` or `rm -rf`

- [x] 10. Implement `create_symlink(link, target)` — idempotent (reads
  existing target via `readlink`, SKIPPED when matching); `ln -sfn`
  otherwise

- [x] 11. Implement `remove_symlink(link)` via `rm` on existing symlink

- [x] 12. Implement `query_path(path)` — `stat -c` with parseable
  format; `_parse_stat_output` → `PathInfo`; `None` on missing

- [x] 13. Implement `query_disk_usage(path)` — `df -PB1 <path>`;
  `_parse_df_output` → `DiskUsageInfo`

- [x] 14. Implement `mount(spec)` — idempotent (SKIPPED when already
  mounted); `mount -t <fstype> -o <opts> <dev> <mp>`

- [x] 15. Implement `unmount(mountpoint, force=False)` — SKIPPED when
  not mounted; `umount [-f] <mp>`

- [x] 16. Implement `list_mounts()` — `findmnt -J` / equivalent parsed
  into `MountInfo` list

- [x] 17. Implement `add_fstab_entry(entry)` — read `/etc/fstab` via
  `cat`; SKIPPED when matching line exists; otherwise rewrite via
  `tee`

- [x] 18. Implement `remove_fstab_entry(device|mountpoint)` — strips
  matching line and rewrites via `tee`

## FilesystemManager — Gaps (specified but not implemented)

- [ ] 19. `verify_fstab()` — run `mount -a -n` in a fault-tolerant way
  to catch invalid fstab entries before reboot

- [ ] 20. `set_permissions(path, mode, recursive=False)` and
  `set_ownership(path, user, group, recursive=False)` as standalone
  methods (currently only bundled into `create_directory`)

- [ ] 21. `copy_file(src, dst, preserve_mode=True)` wrapper

- [ ] 22. `move_path(src, dst)` wrapper

## Tests — Implemented

- [x] 23. `test_create_directory_runs_mkdir`
- [x] 24. `test_remove_directory_recursive_uses_rm_rf`
- [x] 25. `test_create_symlink_skipped_when_target_matches`
- [x] 26. `test_create_symlink_runs_ln_when_missing`
- [x] 27. `test_query_path_parses_stat_output`
- [x] 28. `test_query_disk_usage_parses_df_output`
- [x] 29. `test_mount_skipped_when_already_mounted`
- [x] 30. `test_mount_runs_mount_command`
- [x] 31. `test_unmount_skipped_when_not_mounted`
- [x] 32. `test_add_fstab_entry_writes_tee_when_absent`
- [x] 33. `test_add_fstab_entry_skipped_when_present`
- [x] 34. `test_remove_fstab_entry_strips_matching_line`

## Tests — Gaps

- [ ] 35. Test `remove_symlink` dispatch

- [ ] 36. Test `list_mounts` parser

- [ ] 37. Test dry-run short-circuits for all mutating methods

- [ ] 38. Test `create_directory` with `mode` / `owner` / `group` —
  verify chmod/chown runner calls follow the `mkdir`

- [ ] 39. Test `unmount(force=True)` — verify `-f` flag propagation

## Follow-ups

- [ ] 40. **codecraftai-* reuse check**: filesystem helpers may exist in
  `codecraftai-interop` or `codecraftai-interfaces`; confirm the raw-shell
  approach is preferred before extending (esp. for `query_path` which
  could use `os.stat` locally if the runner exposed a file-operator
  surface).
