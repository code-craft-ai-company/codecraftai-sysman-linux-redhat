# Implementation Plan — Filesystem Quotas

All tasks are unchecked — this is a new feature with no existing
implementation.

## Module and Models

- [ ] 1. Create `source/packages/ccai/sysman/linux/redhat/filesystemquotas.py`
  - _Requirements: All_

- [ ] 2. Define `QuotaActionStatus` enum
  - _Requirements: 8_

- [ ] 3. Define `FilesystemQuotasConfig` dataclass (`dry_run`,
  `elevated`, `projects_file`, `projid_file`)

- [ ] 4. Define `QuotaActionResult`, `QuotaEnforcement`,
  `QuotaTarget` enum, `QuotaLimits`, `QuotaEntry`, `GracePeriod`,
  `ProjectDefinition` dataclasses

- [ ] 5. Define `QuotaFilesystemNotMountedError` and
  `QuotaUnsupportedOperationError` as `SemanticError` subtypes
  - _Requirements: 1.4, 3.2_

## Helpers

- [ ] 6. Implement `__init__` accepting runner + optional config

- [ ] 7. Implement `_fstype_of(mount)` — `findmnt -n -o FSTYPE
  <mount>`; raises `QuotaFilesystemNotMountedError` on non-zero
  exit
  - _Requirements: 1.4_

- [ ] 8. Implement `_read_file(path)` / `_write_file(path,
  content)` via `cat` / `tee`

- [ ] 9. Implement `_dispatch(command, detail=None, mount=None)`
  with dry-run short-circuit and result mapping

## Enforcement State

- [ ] 10. Implement `query_enforcement(mount)` — XFS branch via
  `xfs_quota -x -c "state"`; ext* branch via `quotaon -p`
  - _Requirements: 1_

- [ ] 11. Implement `enable(mount, user=True, group=True,
  project=False)` / `disable(...)` with fstype dispatch and SKIPPED
  semantics
  - _Requirements: 2_

- [ ] 12. Implement `quotacheck(mount, remount_ro=True)` — ext* only;
  raises `QuotaUnsupportedOperationError` on XFS
  - _Requirements: 3_

## Limits

- [ ] 13. Implement `set_user_quota(mount, username, limits)` with
  fstype dispatch and idempotency via `query_user`
  - _Requirements: 4.1, 4.4_

- [ ] 14. Implement `set_group_quota(mount, groupname, limits)`
  - _Requirements: 4.2, 4.4_

- [ ] 15. Implement `set_project_quota(mount, project_name,
  limits)` — XFS only
  - _Requirements: 4.3, 4.4_

## Queries

- [ ] 16. Implement `query_user(mount, username)` / `query_group(
  mount, groupname)` — `quota -v -u|-g` parsed into `QuotaEntry`
  - _Requirements: 5.1_

- [ ] 17. Implement `query_project(mount, project_name)` — XFS,
  via `xfs_quota report -p`
  - _Requirements: 5.3_

- [ ] 18. Implement `list_all(mount, target=QuotaTarget.USER)` —
  `repquota -u|-g` parsed into `list[QuotaEntry]`; XFS branch via
  `xfs_quota report`
  - _Requirements: 5.2, 5.3_

## Grace Periods

- [ ] 19. Implement `get_grace_period(mount, target)` —
  `repquota -t` / `xfs_quota timer` parsed into `GracePeriod`
  - _Requirements: 7.3_

- [ ] 20. Implement `set_grace_period(mount, target,
  block_seconds, inode_seconds)` with fstype dispatch
  - _Requirements: 7.1, 7.2_

## XFS Project Plumbing

- [ ] 21. Implement `define_project(project_id, project_name,
  paths)` — upsert both `/etc/projects` and `/etc/projid`
  atomically; SKIPPED when both files already carry the matching
  entries
  - _Requirements: 6.1_

- [ ] 22. Implement `activate_project(mount, project_name)` —
  `xfs_quota -x -c "project -s <name>"`
  - _Requirements: 6.2_

- [ ] 23. Implement `remove_project(project_name)` — strip from
  both files; SKIPPED when already absent
  - _Requirements: 6.3_

- [ ] 24. Implement `list_projects()` — parse both files into
  `list[ProjectDefinition]`
  - _Requirements: 6.4_

## Tests

- [ ] 25. Create `test_filesystemquotas.py` with `_MockRunner`

- [ ] 26. Tests for `_fstype_of` including
  `QuotaFilesystemNotMountedError`

- [ ] 27. Parser tests for `quotaon -p`, `quota -v`, `repquota`,
  `xfs_quota state`, `xfs_quota report -u/-g/-p`

- [ ] 28. Tests for `enable` / `disable` / SKIPPED semantics on
  both fstype branches

- [ ] 29. Test `quotacheck` on XFS raises
  `QuotaUnsupportedOperationError` without dispatching

- [ ] 30. Tests for `set_user_quota` / `set_group_quota` /
  `set_project_quota` including SKIPPED when values already match

- [ ] 31. Tests for project plumbing:
  - `define_project` writes both files atomically
  - `remove_project` strips both files
  - `list_projects` merges the two files correctly

- [ ] 32. Grace-period round-trip tests on both fstype branches

- [ ] 33. Dry-run sweep across every mutating method

- [ ] 34. Failure-mapping sweep

## Follow-ups

- [ ] 35. **codecraftai-* reuse check**: nothing obvious in sibling
  packages for quota plumbing; verify before building.
