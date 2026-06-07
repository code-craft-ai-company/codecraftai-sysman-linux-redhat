# Implementation Plan — Repository Management

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `repositorymanagement` module

- [x] 2. Define `RepoActionStatus` enum (SUCCESS / FAILED / SKIPPED /
  DRY_RUN)

- [x] 3. Define `RepositoryManagerConfig` (dry_run, elevated)

- [x] 4. Define `RepoDefinition` dataclass (repo_id, name, baseurl,
  enabled, gpgcheck, gpgkey, priority, etc.)

- [x] 5. Define `RepoInfo` query dataclass

- [x] 6. Define `RepoActionResult` dataclass

## RepositoryManager — Implemented

- [x] 7. Implement `__init__` accepting runner + optional config

- [x] 8. Implement `add(repo_def)` — renders via `_render_repo_file`,
  writes `/etc/yum.repos.d/<repo_id>.repo` via `tee`; idempotent via
  pre-read compare

- [x] 9. Implement `remove(repo_id)` — `rm -f
  /etc/yum.repos.d/<repo_id>.repo`; SKIPPED when file absent

- [x] 10. Implement `enable(repo_id)` — `dnf config-manager
  --set-enabled <repo_id>`

- [x] 11. Implement `disable(repo_id)` — `dnf config-manager
  --set-disabled <repo_id>`

- [x] 12. Implement `refresh(repo_id=None)` — `dnf clean metadata`
  + `dnf makecache` (optionally scoped to a repo)

- [x] 13. Implement `list_repos()` — `dnf repolist -v` parsed via
  `_parse_repolist_output` into `RepoInfo` records

- [x] 14. Implement `set_priority(repo_id, priority)` — writes
  `priority=<N>` into the repo's `.repo` file via a dnf-config-manager
  call

- [x] 15. Implement `import_gpg_key(key_source)` — `rpm --import
  <key_source>`

- [x] 16. Implement `_repo_file_path`, `_read_file`, `_write_file`,
  `_run_config_manager` helpers

## RepositoryManager — Gaps (specified but not implemented)

- [ ] 17. `update_definition(repo_id, **fields)` — partial update of
  an existing `.repo` file (e.g., rotate `gpgkey`, change `baseurl`)
  without full rewrite

- [ ] 18. `query_definition(repo_id)` — read the `.repo` file and
  return the parsed `RepoDefinition`

- [ ] 19. `subscription-manager repos --enable/--disable` pass-through
  for RHSM-managed repos, or explicit delegation to
  `3-subscription-management`

- [ ] 20. `verify_gpg_imported(keyid)` — check that a key id is
  present in the rpmdb

## Tests — Implemented

- [x] 21. `test_add_writes_new_repo_file`
- [x] 22. `test_add_skipped_when_content_matches`
- [x] 23. `test_remove_skipped_when_file_absent`
- [x] 24. `test_enable_dispatches_config_manager`
- [x] 25. `test_refresh_cleans_and_makecaches`
- [x] 26. `test_list_repos_parses_repolist_output`
- [x] 27. `test_import_gpg_key_runs_rpm_import`

## Tests — Gaps

- [ ] 28. Test `disable(repo_id)` dispatch

- [ ] 29. Test `set_priority` writes via config-manager

- [ ] 30. Test `refresh(repo_id="myrepo")` scoped variant (with
  `--repo=<id>`)

- [ ] 31. Test `remove(repo_id)` when the file exists — verify `rm
  -f` dispatch

- [ ] 32. Test dry-run short-circuits for every mutating method

## Follow-ups

- [ ] 33. **codecraftai-* reuse check**: `3-package-management` /
  `3-subscription-management` interact here; confirm the seams remain
  clean when RHSM-managed repos are involved.
