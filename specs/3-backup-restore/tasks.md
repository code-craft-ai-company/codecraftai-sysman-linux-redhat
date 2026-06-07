# Implementation Plan — Backup & Restore

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `backuprestore` module
  - `source/packages/ccai/sysman/linux/redhat/backuprestore.py` exists
  - _Requirements: All_

- [x] 2. Define `BackupActionStatus` enum (SUCCESS / FAILED / SKIPPED /
  DRY_RUN)

- [x] 3. Define `Compression` enum (NONE / GZIP / BZIP2 / XZ) and
  `_compression_flag` mapper (GZIP→-z, BZIP2→-j, XZ→-J, NONE→None)

- [x] 4. Define `BackupManagerConfig` dataclass
  - Fields: `dry_run: bool = False`, `elevated: bool = False`

- [x] 5. Define `BackupSpec` dataclass
  - Fields: `source_paths`, `archive_path`, `compression` (default GZIP),
    `exclude_patterns` (default [])

- [x] 6. Define `BackupActionResult` dataclass
  - Fields: `status`, `archive_path`, `exit_code`, `stdout`, `stderr`,
    `command`

## BackupRestoreManager — Implemented

- [x] 7. Implement `__init__` accepting runner + optional config

- [x] 8. Implement `create_archive(spec)` — `tar [-z|-j|-J] -cf <archive>
  [--exclude <pat> ...] <sources...>`

- [x] 9. Implement `extract_archive(archive_path, destination)` —
  `tar -xf <archive> -C <destination>`

- [x] 10. Implement `sync_paths(source, destination, delete=False)` —
  `rsync -a [--delete] <src> <dst>`

- [x] 11. Implement `_dispatch` helper — dry-run short-circuit and
  runner-result-to-BackupActionResult mapping

## BackupRestoreManager — Gaps (specified but not implemented)

- [ ] 12. Manifest and checksum generation for created archives
  (sha256 sidecars)

- [ ] 13. Archive verification (`tar -tf` listing, checksum compare)

- [ ] 14. Retention / pruning — delete archives older than a threshold
  or beyond a max-count

- [ ] 15. Archive content queries — list entries, search for path,
  report compression type

- [ ] 16. Encryption wrapper (`openssl enc`) for archives

- [ ] 17. `SKIPPED` semantics — return `SKIPPED` when target archive
  already exists and matches expected content hash

## Tests — Implemented

- [x] 18. `test_create_archive_dispatches_tar_with_compression` —
  verifies the tar flag and `-cf` args

- [x] 19. `test_extract_archive_runs_tar_xf` — verifies `tar -xf` and
  `-C` args

- [x] 20. `test_sync_paths_uses_rsync` — verifies `rsync -a` and
  `--delete` propagation

## Tests — Gaps

- [ ] 21. Test dry-run short-circuits for all three methods (returns
  DRY_RUN, runner NOT called)

- [ ] 22. Test `exclude_patterns` flag insertion in `create_archive`

- [ ] 23. Test failure mapping — runner non-zero exit → status FAILED
  with exit_code/stderr preserved

- [ ] 24. Test each `Compression` enum value produces the expected tar
  flag

## Follow-ups

- [ ] 25. **codecraftai-* reuse check**: investigate whether
  `codecraftai-interop` provides higher-level archive/sync helpers or
  whether the raw-`tar`/`rsync` approach is the right level for this
  package.
