# Implementation Plan

- [ ] 1. Create the `backuprestore` module file
  - Create `source/packages/automojo/sysman/linux/redhat/backuprestore.py`
  - Add module-level docstring describing purpose, coordinator-driven design, and
    scope boundary vs `storage-management` (no block devices, no databases)
  - Declare module-level imports (`dataclasses`, `enum`, `json`, `os`, `pathlib`,
    `typing`, interface types)
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10_

- [ ] 2. Define status and compression enums
  - Implement `BackupActionStatus` enum with values: `SUCCESS`, `FAILED`, `SKIPPED`,
    `DRY_RUN`
  - Implement `BackupCompression` enum with values: `NONE`, `GZIP`, `ZSTD`, `XZ`
  - _Requirements: 1, 2, 3, 6, 8_

- [ ] 3. Define result and content dataclasses
  - Implement `BackupActionResult` dataclass with fields: `status`, `archive_path`,
    `manifest_path`, `sha256`, `exit_code`, `stdout`, `stderr`, `command`,
    `removed_entries`, `removed_bytes`, `planned_paths`, `error_message`
  - Implement `BackupEntry` dataclass with fields: `name`, `timestamp`,
    `archive_path`, `manifest_path`, `size_bytes`, `compression`, `sha256`
  - Implement `BackupVerifyResult` dataclass with fields: `ok`, `computed_sha`,
    `recorded_sha`, `tar_test_output`
  - Implement `BackupContents` dataclass with fields: `total_entries`, `entries_list`
  - Implement `BackupRetentionPolicy` dataclass with fields: `keep_last_n`,
    `keep_older_than_days`, `keep_min_total_bytes`
  - No mutable defaults; use `None` and assign inside `__post_init__` if required
  - _Requirements: 2, 3, 4, 5, 6, 7, 8_

- [ ] 4. Define typed exceptions
  - Implement `BackupNotFoundError` with `name` and `repository_root` attributes
  - Implement `BackupRepositoryNotFoundError` with `repository_root` attribute
  - Implement `BackupQueryError` with `exit_code` and `stderr` attributes
  - _Requirements: 4, 5, 7_

- [ ] 5. Implement `BackupRestoreManagerConfig` dataclass
  - Add `dry_run: bool = False` field
  - Add `elevated: bool = False` field
  - Add `default_compression: BackupCompression = BackupCompression.NONE` field
  - Add `repository_root: Path` field with a `Path("/var/lib/automojo/backups")`
    default (constructed inside `__post_init__` to avoid mutable default pitfalls)
  - _Requirements: 1.1, 1.5, 2.1, 2.7, 3.5, 6.7, 8.6, 9.5, 10.1, 10.2_

- [ ] 6. Implement `BackupRestoreManager.__init__`, `list_entries()`, and
      `init_repository()`
  - Accept `ICommandRunner`, `IFileOperator`, and `BackupRestoreManagerConfig` as
    constructor arguments; assign to instance attributes
  - Implement `list_entries(name_prefix=None)`:
    - Verify the repository root exists via the file operator; raise
      `BackupRepositoryNotFoundError` if not
    - Enumerate `manifests/*.json` via the file operator
    - Read each manifest and build a `BackupEntry`
    - Filter by `name_prefix` when supplied (explicit `is not None` check)
    - Return the resulting list (assigned to a local variable before `return`)
  - Implement `init_repository(root)`:
    - Check whether the root already exists and matches the expected layout; if so
      return `BackupActionResult(status=SKIPPED)` with no file operator writes
    - If the root exists but does not match, return
      `BackupActionResult(status=FAILED)` with an error message describing the
      mismatch; do not modify it
    - Honour `dry_run`; return `DRY_RUN` without any file operator writes
    - Create `root` (mode `0o750`), then `archives/`, `manifests/`, `tmp/` (all
      `0o750`) via the file operator with `elevated=True`
    - Write `repository.json` containing the schema version, creation timestamp,
      and default compression setting
    - Return `BackupActionResult(status=SUCCESS)`
  - _Requirements: 1, 5_

- [ ] 7. Implement `create_archive()`
  - Validate that at least one source path exists via the file operator; when all
    sources are missing, return `BackupActionResult(status=FAILED)` naming the
    missing paths and do not create any file
  - Build the temp archive path under `<repository_root>/tmp/<name>-<timestamp>.tar`
    (append `.gz`/`.zst`/`.xz` when compression is selected, append `.enc` when
    `encryption=True`)
  - Build the `tar --create` command: include `--acls --xattrs --selinux
    --numeric-owner --sparse`, append `--one-file-system` when requested, append
    each `--exclude=<pat>`, append the compression flag, end with `-- <sources>`
  - When `encryption=True`, delegate to the encryption helper (Task 14) to obtain a
    piped `openssl enc` argument vector and stdin-delivery binding
  - Honour `dry_run`; return `DRY_RUN` with the would-be archive path and no
    runner call
  - Invoke the runner with `elevated=True`
  - On runner failure, remove the incomplete temp file via the file operator and
    return `BackupActionResult(status=FAILED)` with `exit_code` and `stderr`
  - On success, compute `sha256sum <tmp-path>`, atomically rename the temp file to
    `<repository_root>/archives/<name>-<timestamp>.tar[.<ext>]` via the file
    operator, chmod the archive to `0o600`, chown to `root:root`
  - Write the manifest JSON to `<repository_root>/manifests/<name>-<timestamp>.json`
    containing archive size, SHA-256, compression, source list, exclude list,
    host fields, tool versions, and `encryption` field when applicable
  - Return `BackupActionResult(status=SUCCESS)` with the archive path, manifest
    path, and SHA-256
  - _Requirements: 2, 9_

- [ ] 8. Implement `create_rsync_mirror()`
  - Validate that the source exists via the file operator; when missing, return
    `BackupActionResult(status=FAILED)` naming the path
  - Compute the target path under `<repository_root>/archives/<name>-<timestamp>/`
  - Build the rsync command: `rsync -aAXH --numeric-ids --delete`
  - When a previous mirror path is supplied *and* exists, append
    `--link-dest=<previous>`; otherwise omit link-dest entirely
  - Honour `dry_run` by appending `--dry-run` and capturing the planned file list
    into `planned_paths` without mutating the target
  - Invoke the runner with `elevated=True`
  - On success, write a sibling manifest capturing source, target, link-dest, and
    the `rsync` exit code
  - Map exit code to `BackupActionResult.status`
  - _Requirements: 3_

- [ ] 9. Implement `verify_archive()`
  - Read the manifest via the file operator; raise `BackupNotFoundError` when the
    manifest or archive file is missing
  - Invoke `sha256sum <archive-path>` through the runner; raise `BackupQueryError`
    on unexpected non-zero exit
  - Compare the computed checksum against the manifest-recorded checksum
  - When `include_tar_test=True`, also invoke `tar --test-label --file <archive>`;
    capture stdout/stderr into `tar_test_output`; a non-zero exit sets `ok=False`
  - When the archive is encrypted, delegate decryption piping to the encryption
    helper (Task 14); the passphrase is delivered via stdin only
  - Return a `BackupVerifyResult` with `ok`, `computed_sha`, `recorded_sha`, and
    `tar_test_output`
  - _Requirements: 4, 9_

- [ ] 10. Implement `restore_archive()`
  - Invoke `verify_archive()` first; if verification fails, return
    `BackupActionResult(status=FAILED)` and do not invoke the extract command
  - Check the target root: when missing and `require_existing=True`, return
    `BackupActionResult(status=FAILED)`; when missing and
    `require_existing=False`, create it (mode `0o755`) via the file operator
  - Build the `tar --extract` command with `--acls --xattrs --selinux
    --numeric-owner -C <target-root>` plus the compression flag; append restricted
    paths when supplied
  - When the archive is encrypted, pipe through the decryption helper (Task 14)
  - Honour `dry_run`: compute `tar --list --file <archive>` to return the planned
    paths in `planned_paths`; do not extract
  - Invoke the runner with `elevated=True`
  - Map the runner result to `BackupActionResult`; on failure include `exit_code`
    and captured `stderr` with no rollback attempt
  - _Requirements: 6, 9_

- [ ] 11. Implement `query_contents()`
  - Read the manifest via the file operator; raise `BackupNotFoundError` when the
    archive is missing
  - Build the `tar --list --file <archive> [compression]` command
  - When the archive is encrypted, pipe through the decryption helper (Task 14)
  - Invoke the runner; on unexpected failure, raise `BackupQueryError`
  - Parse stdout lines into `entries_list`, compute `total_entries`, build a
    `BackupContents` (respecting `include_paths=False` by returning an empty
    `entries_list` while still setting `total_entries`)
  - _Requirements: 7, 9_

- [ ] 12. Implement `apply_retention()`
  - Invoke `list_entries()` to obtain the current archive set
  - For each supplied rule (`keep_last_n`, `keep_older_than_days`,
    `keep_min_total_bytes`), compute the *permit-removal* set
  - Compute the eligible removal set as the intersection of all permit-removal
    sets across supplied rules (explicit `is not None` checks; no truthy shortcuts)
  - When the eligible set is empty, return `BackupActionResult(status=SKIPPED)`
  - Honour `dry_run`: return `DRY_RUN` with the planned `removed_entries` and the
    sum of `removed_bytes` without issuing file operator deletes
  - For each eligible entry, remove the archive file and the manifest file via
    the file operator with `elevated=True`; continue on per-entry failure
  - On any per-entry failure, return `BackupActionResult(status=FAILED)` with both
    the successfully-removed entry list and a per-entry error map in
    `error_message`
  - On full success, return `BackupActionResult(status=SUCCESS)` with
    `removed_entries` and `removed_bytes`
  - _Requirements: 8_

- [ ] 13. Implement the passphrase-handling helper
  - Define an internal helper `_build_encryption_pipeline()` that, given an action
    (`encrypt` or `decrypt`) and a passphrase, returns:
    1. The `openssl enc` argument vector using `-pass stdin` (never `-k` or
       `-pass pass:`)
    2. The stdin bytes to deliver to the runner
  - Ensure the passphrase value appears only in the returned stdin bytes, never
    in the returned argv
  - Define `_redact_command_for_audit()` which returns a command list with the
    passphrase-bearing stdin channel already abstracted to `-pass stdin`
  - Clear the local passphrase reference before the helper returns
  - _Requirements: 9.1, 9.3, 9.4_

- [ ] 14. Implement the encryption helper entry points on `BackupRestoreManager`
  - Wire `create_archive()`, `verify_archive()`, `restore_archive()`, and
    `query_contents()` to the passphrase-handling helper from Task 13
  - For `create_archive()`, ensure the final on-disk name ends with `.enc` and the
    manifest records `encryption="aes-256-cbc/pbkdf2"`
  - When `dry_run=True`, skip the encryption command construction entirely and
    do not touch the passphrase
  - _Requirements: 9.2, 9.5_

- [ ] 15. Create test module and mock infrastructure
  - Create
    `source/testroots/testbase/automojo/tests/sysman/linux/redhat/test_backuprestore.py`
  - Implement `MockCommandRunner` that returns configurable `RunResult` values
    and records every invocation (args, elevated flag, stdin bytes)
  - Implement `MockFileOperator` that simulates an in-memory filesystem with
    path existence, mode, content, and a recorded operation log
  - Set up hypothesis testing framework configuration with a min of 100 iters
  - Create test fixtures: default manager, pre-populated repository, sample
    manifest set, sample retention scenarios
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10_

- [ ] 16. Write property-based test for verify-after-create matching checksum
  - **Property 1: Verify-After-Create Matches Recorded Checksum**
  - Generate random archive names, source lists, and compression choices
  - After each `create_archive()` stub the runner to return the known SHA; assert
    `verify_archive()` returns `ok=True` with matching SHAs
  - **Validates: Requirements 2.3, 2.5, 4.1, 4.2**
  - _Requirements: 2, 4_

- [ ] 17. Write property-based test for restore verifying before extracting
  - **Property 2: Restore Verifies Before Extracting**
  - Generate random manifest names and random verify outcomes
  - Assert verify is always called before extract; when verify fails, extract is
    never invoked
  - **Validates: Requirements 6.1, 6.2**
  - _Requirements: 6_

- [ ] 18. Write property-based test for retention rule intersection
  - **Property 3: Retention Rule Intersection**
  - Generate random entry sets and random combinations of the three retention
    rules
  - Assert that the removed set equals the intersection of per-rule
    permit-removal sets
  - **Validates: Requirements 8.1**
  - _Requirements: 8_

- [ ] 19. Write property-based test for retention safety without rules
  - **Property 4: Retention Safety Without Rules**
  - Generate random entry sets and rule combinations that permit no removal
  - Assert the result is `SKIPPED` and no file operator delete is issued
  - **Validates: Requirements 8.2**
  - _Requirements: 8_

- [ ] 20. Write property-based test for repository init idempotence
  - **Property 5: Repository Init Idempotence**
  - Generate random already-matching layouts
  - Assert the second `init_repository()` call returns `SKIPPED` and issues no
    file operator writes
  - **Validates: Requirements 1.2**
  - _Requirements: 1_

- [ ] 21. Write property-based test for passphrase never appearing in argv
  - **Property 6: Passphrase Never in argv**
  - Generate random passphrase strings
  - Assert the command list passed to the runner never contains the passphrase
    for any encrypted create/verify/restore/query
  - **Validates: Requirements 9.1, 9.3, 9.4**
  - _Requirements: 9_

- [ ] 22. Write property-based test for encryption extension and manifest record
  - **Property 7: Encryption Extension and Manifest Record**
  - Generate random archive names with `encryption=True`
  - Assert the archive path ends with `.enc` and the written manifest records
    `encryption="aes-256-cbc/pbkdf2"`
  - **Validates: Requirements 9.2**
  - _Requirements: 9_

- [ ] 23. Write property-based test for rsync link-dest presence
  - **Property 8: rsync link-dest Only When Previous Exists**
  - Generate random previous-mirror presence states
  - Assert `--link-dest=<path>` appears exactly once when the previous mirror
    exists and not at all otherwise
  - **Validates: Requirements 3.1, 3.2**
  - _Requirements: 3_

- [ ] 24. Write property-based test for dry-run safety
  - **Property 9: Dry-Run Safety**
  - Generate random mutating operations with `dry_run=True`
  - Assert no file operator write/rename/delete and no mutating runner command
    is issued; `tar --list` for restore planning is permitted
  - **Validates: Requirements 1.5, 2.7, 3.5, 6.7, 8.6, 9.5**
  - _Requirements: 1, 2, 3, 6, 8, 9_

- [ ] 25. Write property-based test for elevation consistency
  - **Property 10: Elevation Consistency**
  - Generate random `elevated=True/False` configurations
  - Assert mutating operations propagate `elevated=True` to both the runner and
    the file operator; read-only operations do not use elevation
  - **Validates: Requirements 10.1, 10.2, 10.3**
  - _Requirements: 10_

- [ ] 26. Write property-based test for status mapping correctness
  - **Property 11: Status Mapping Correctness**
  - Generate random exit codes from the mock runner
  - Assert `0` maps to `SUCCESS`, non-zero maps to `FAILED`, and skip/dry-run
    paths override as specified
  - **Validates: Requirements 2.5, 2.6, 3.4, 6.5, 6.6, 8.4, 8.5**
  - _Requirements: 2, 3, 6, 8_

- [ ] 27. Write property-based test for byte-identical repository init skip
  - **Property 12: Byte-Identical Repository Init Skip**
  - Generate random repository configurations; init once, then capture
    `repository.json` bytes; init again
  - Assert the second call is `SKIPPED`, issues no writes, and reports
    byte-identical metadata
  - **Validates: Requirements 1.2, 1.4**
  - _Requirements: 1_

- [ ] 28. Write property-based test for temp file cleanup on failure
  - **Property 13: Temp File Cleanup on Failure**
  - Generate random `create_archive()` scenarios with the mock runner configured
    to return non-zero
  - Assert the temp archive file under `tmp/` is removed via the file operator
    before the method returns
  - **Validates: Requirements 2.6**
  - _Requirements: 2_

- [ ] 29. Write unit tests for `init_repository()`
  - Test: root does not exist — creates all subdirectories and
    `repository.json`, returns `SUCCESS`
  - Test: root exists with matching layout — returns `SKIPPED`, no writes
  - Test: root exists with mismatched permissions — returns `FAILED`, no writes
  - Test: dry-run mode — returns `DRY_RUN`, no writes
  - Test: elevated=True — file operator calls pass `elevated=True`
  - _Requirements: 1_

- [ ] 30. Write unit tests for `create_archive()`
  - Test: happy path no compression — runner invoked with expected argv,
    manifest written, `SUCCESS` returned with correct paths and SHA
  - Test: gzip / zstd / xz compression — correct compression flag appended,
    correct extension
  - Test: all sources missing — returns `FAILED` naming missing paths, no runner
    call
  - Test: tar failure — runner invoked, temp file removed via file operator,
    returns `FAILED` with exit code and stderr
  - Test: dry-run — returns `DRY_RUN` with the would-be archive path, no runner
    call
  - Test: excludes and one-file-system — flags appear in argv in expected order
  - Test: encryption=True — `.enc` extension applied, manifest records
    `aes-256-cbc/pbkdf2`, passphrase never in runner argv
  - _Requirements: 2, 9_

- [ ] 31. Write unit tests for `create_rsync_mirror()`
  - Test: fresh mirror — argv contains no `--link-dest`
  - Test: with previous mirror — argv contains exactly one
    `--link-dest=<previous>`
  - Test: source missing — returns `FAILED`, no runner call
  - Test: dry-run — `--dry-run` appended, `planned_paths` populated, no
    mutations
  - Test: manifest records source, target, link-dest, and exit code
  - _Requirements: 3_

- [ ] 32. Write unit tests for `verify_archive()`
  - Test: checksums match — `ok=True`
  - Test: checksums differ — `ok=False` with both computed and recorded SHAs
  - Test: missing manifest — raises `BackupNotFoundError`
  - Test: missing archive — raises `BackupNotFoundError`
  - Test: `include_tar_test=True` and tar passes — `ok` reflects SHA only
  - Test: `include_tar_test=True` and tar fails — `ok=False` with
    `tar_test_output` captured
  - Test: encrypted archive — decryption helper invoked, passphrase via stdin
  - _Requirements: 4, 9_

- [ ] 33. Write unit tests for `list_entries()`
  - Test: populated repository — returns list of `BackupEntry` in timestamp
    order with correct fields
  - Test: empty repository — returns empty list
  - Test: repository does not exist — raises `BackupRepositoryNotFoundError`
  - Test: `name_prefix` filter — returns only matching entries
  - _Requirements: 5_

- [ ] 34. Write unit tests for `restore_archive()`
  - Test: verify passes — runner invoked with correct extract argv, returns
    `SUCCESS`
  - Test: verify fails — extract never invoked, returns `FAILED`
  - Test: target root missing + `require_existing=False` — target created
    (mode `0o755`) then extract runs
  - Test: target root missing + `require_existing=True` — returns `FAILED`, no
    extract, no directory created
  - Test: restricted paths supplied — argv includes each path after the tar
    options
  - Test: extract failure — returns `FAILED` with exit code and stderr, no
    rollback attempted
  - Test: dry-run — `tar --list` invoked, `planned_paths` populated, no extract
  - Test: encrypted archive — decryption helper invoked, passphrase via stdin
  - _Requirements: 6, 9_

- [ ] 35. Write unit tests for `query_contents()`
  - Test: happy path — returns `BackupContents` with correct `total_entries`
    and `entries_list`
  - Test: `include_paths=False` — `total_entries` populated, `entries_list`
    empty
  - Test: archive missing — raises `BackupNotFoundError`
  - Test: tar --list fails — raises `BackupQueryError`
  - Test: encrypted archive — decryption helper invoked, passphrase via stdin
  - _Requirements: 7, 9_

- [ ] 36. Write unit tests for `apply_retention()`
  - Test: single rule `keep_last_n=3` with five entries — removes oldest two
  - Test: single rule `keep_older_than_days=30` — removes entries older than
    30 days
  - Test: single rule `keep_min_total_bytes` — removes entries until total size
    reaches the minimum
  - Test: intersection of two rules — removes only entries permitted by both
  - Test: no eligible entries — returns `SKIPPED`, no file operator deletes
  - Test: per-entry removal failure — returns `FAILED` listing both the
    successful removals and the per-entry errors
  - Test: dry-run — returns `DRY_RUN` with planned `removed_entries` and
    `removed_bytes`, no file operator deletes
  - Test: elevated=True — file operator deletes pass `elevated=True`
  - _Requirements: 8, 10_

- [ ] 37. Write unit tests for passphrase handling
  - Test: `create_archive(encryption=True)` — runner `args` never contains the
    passphrase; runner receives passphrase via `stdin_data`
  - Test: audit record captured by a stub persistence aspect uses `-pass stdin`
    placeholder, not the actual passphrase
  - Test: dry-run with `encryption=True` — no openssl enc invocation, no
    passphrase consumed
  - Test: manifest contents never contain the passphrase
  - _Requirements: 9_

- [ ] 38. Checkpoint - Ensure all tests pass
  - Run all unit tests and property-based tests
  - Verify all 13 properties pass with 100+ iterations
  - Verify every requirement (1 through 10) is exercised by at least one task's
    tests
  - Ensure all tests pass, ask the user if questions arise
