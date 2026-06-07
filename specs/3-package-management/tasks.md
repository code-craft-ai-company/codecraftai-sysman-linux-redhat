# Implementation Plan — Package Management

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `packagemanagement` module

- [x] 2. Define `PackageActionStatus` enum (SUCCESS / FAILED / SKIPPED
  / DRY_RUN)

- [x] 3. Define `PackageManagerConfig` (dry_run, elevated)

- [x] 4. Define `PackageInfo`, `PackageUpdateInfo`,
  `PackageActionResult` dataclasses

- [x] 5. Define `PackageQueryError` and `PackageFileNotFoundError`
  exceptions

## PackageManager — Implemented

- [x] 6. Implement `__init__` accepting runner + optional config;
  expose `runner` + `config` properties

- [x] 7. Implement `install(name, version=None)` — `dnf install -y
  <name>[-version]`; SKIPPED when already installed at requested
  version; honours dry-run

- [x] 8. Implement `remove(name)` — `dnf remove -y <name>`; SKIPPED
  when not installed

- [x] 9. Implement `update(name, version=None)` — `dnf update -y
  <name>[-version]`

- [x] 10. Implement `query(name)` — `rpm -q --queryformat <fmt>
  <name>`; returns `Optional[PackageInfo]`; raises
  `PackageQueryError` on unexpected rpm exit codes

- [x] 11. Implement `list_updates()` — `dnf check-update`; exit
  code 0 → no updates; 100 → parse list; other → raise
  `PackageQueryError`

- [x] 12. Implement `install_local(filepath)` — raises
  `PackageFileNotFoundError` when file absent; otherwise `dnf install
  -y <filepath>`

- [x] 13. Implement `hold(name)` / `release_hold(name)` via
  `dnf versionlock add/delete`; idempotent via `_is_held` check

- [x] 14. Implement `group_install(group_name)` — `dnf group install
  -y <group>`

- [x] 15. Implement `group_remove(group_name)` — `dnf group remove
  -y <group>`

- [x] 16. Implement `_is_held`, `_parse_query_output`,
  `_parse_check_update_output`, `_result_from_run` helpers

## PackageManager — Gaps (specified but not implemented)

- [ ] 17. `list_installed(filter=None)` — enumerate installed RPMs
  (potentially filtered by name glob)

- [ ] 18. `verify(name)` — `rpm --verify <name>` with structured
  output (which files differ, what changed)

- [ ] 19. `list_files(name)` — `rpm -ql <name>` into a list

- [ ] 20. `owner_of_file(path)` — `rpm -qf <path>` for reverse
  lookup

- [ ] 21. `clean_cache()` — `dnf clean all` wrapper

- [ ] 22. Transaction history — `dnf history list/info` / `undo`

## Tests — Implemented

- [x] 23. `test_install_when_not_present_invokes_dnf`
- [x] 24. `test_install_skipped_when_already_installed`
- [x] 25. `test_install_at_specific_version`
- [x] 26. `test_install_dry_run_does_not_write`
- [x] 27. `test_remove_when_installed`
- [x] 28. `test_remove_skipped_when_not_installed`
- [x] 29. `test_query_returns_info_when_installed`
- [x] 30. `test_query_returns_none_when_not_installed`
- [x] 31. `test_query_raises_on_unexpected_error`
- [x] 32. `test_list_updates_no_updates_when_exit_code_zero`
- [x] 33. `test_list_updates_parsed_from_exit_code_100`
- [x] 34. `test_list_updates_unexpected_exit_code_raises`
- [x] 35. `test_install_local_missing_file_raises`
- [x] 36. `test_install_local_invokes_dnf_install_with_filepath`
- [x] 37. `test_hold_when_none_applies`
- [x] 38. `test_hold_skipped_when_already_held`
- [x] 39. `test_release_hold_skipped_when_not_held`
- [x] 40. `test_group_install`
- [x] 41. `test_group_remove_dry_run`

## Tests — Gaps

- [ ] 42. Test `update(name, version=None)` dispatch + version-pinned
  variant

- [ ] 43. Test `release_hold` dispatch when held — currently only
  covers SKIPPED path

- [ ] 44. Test failure mapping for each mutating method (runner
  non-zero exit → status FAILED with exit_code/stderr preserved)

## Follow-ups

- [ ] 45. **codecraftai-* reuse check**: `3-repository-management` manages
  the repo definitions; ensure this feature's `install_local` and
  group ops do not accidentally duplicate that surface.
