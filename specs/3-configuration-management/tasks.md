# Implementation Plan — Configuration Management

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `configurationmanagement` module
  - `source/packages/automojo/sysman/linux/redhat/configurationmanagement.py`
    exists

- [x] 2. Define `ConfigActionStatus` enum (SUCCESS / FAILED / SKIPPED /
  DRY_RUN)

- [x] 3. Define `ConfigurationManagerConfig` dataclass
  - Fields: `dry_run`, `elevated`

- [x] 4. Define `ConfigActionResult` dataclass
  - Fields: `status`, `path`, `backup_path`, `diff`, `exit_code`, `stdout`,
    `stderr`, `command`

- [x] 5. Define `ConfigDiffResult` dataclass (path / has_diff / diff)

- [x] 6. Define `ConfigValidationResult` dataclass
  - Fields: `path`, `validator_id`, `valid`, `exit_code`, `stdout`, `stderr`

- [x] 7. Define `ConfigTemplateError` (raised on unresolved template
  identifiers) and `ConfigValidatorNotRegisteredError`

- [x] 8. Define default validator registry (nginx, sshd, httpd, named,
  rsyslog, sudoers)

## ConfigurationManager — Implemented

- [x] 9. Implement `__init__` accepting runner + optional config;
  seed `_validators` with `_DEFAULT_VALIDATORS`

- [x] 10. Implement `register_validator(id, command_builder)` for
  extending the validator registry

- [x] 11. Implement `write(path, content, mode, owner, group)`
  - Reads current via `cat`; computes unified diff
  - Empty diff → `_ensure_metadata` with `SKIPPED` fall-through
  - Dry-run: return DRY_RUN with diff
  - Otherwise `tee <path>` with stdin, then `_apply_mode_and_owner`

- [x] 12. Implement `write_template(path, template, variables, ...)`
  - `string.Template.safe_substitute`; raises `ConfigTemplateError`
    with the list of missing identifiers; delegates to `write`

- [x] 13. Implement `backup(path)` — `cp -p <path> <path>.bak.<UTC>`;
  timestamp format `%Y%m%dT%H%M%S`

- [x] 14. Implement `restore(backup_path)` — strips trailing
  `.bak.<timestamp>` suffix and runs `cp -p <backup> <original>`

- [x] 15. Implement `diff(path, desired_content)` — returns
  `ConfigDiffResult` with unified diff text

- [x] 16. Implement `validate(path, validator_id)` — raises
  `ConfigValidatorNotRegisteredError` on unknown id; dispatches registered
  builder and maps exit_code to `valid`

- [x] 17. Implement `set_permissions(path, mode)` — `chmod <mode> <path>`

- [x] 18. Implement `set_ownership(path, user, group)` —
  `chown <spec> <path>`; SKIPPED when both `user` and `group` are None;
  spec is `user:group`, `user`, or `:group`

- [x] 19. Implement `set_ini_key(path, section, key, value)` using
  `configparser`; idempotent (SKIPPED when serialised content matches)

- [x] 20. Implement `remove_ini_key(path, section, key)` — SKIPPED when
  section or key already absent

- [x] 21. Implement `_read_file` helper (cat-via-runner; empty on
  non-zero exit) and `_apply_mode_and_owner` chmod+chown helper

## ConfigurationManager — Gaps (specified but not implemented)

- [ ] 22. JSON helpers (`set_json_key`, `remove_json_key`) matching the
  INI counterparts

- [ ] 23. YAML helpers — requires a YAML dependency (PyYAML); currently
  no YAML-format support beyond opaque `write`

- [ ] 24. Key=value/plain-text helpers (`set_kv_line`, `remove_kv_line`)
  for conf files that are not strict INI (e.g. `/etc/sysctl.conf` style)

- [ ] 25. Bulk-validate a directory of configs (iterate + register
  format per file) with consolidated result

- [ ] 26. `list_backups(path)` / `prune_backups(path, keep_last_n)` —
  retention policy for the `.bak.*` files created by `backup`

## Tests — Implemented

- [x] 27. `test_write_skipped_when_content_matches`
- [x] 28. `test_write_dispatches_tee_with_stdin`
- [x] 29. `test_write_dry_run_produces_diff`
- [x] 30. `test_write_template_renders_and_writes`
- [x] 31. `test_write_template_raises_on_missing_variable`
- [x] 32. `test_backup_produces_cp_command`
- [x] 33. `test_restore_strips_backup_suffix`
- [x] 34. `test_diff_reports_changes`
- [x] 35. `test_validate_unknown_validator_raises`
- [x] 36. `test_validate_runs_registered_command`
- [x] 37. `test_set_permissions_dispatches_chmod`
- [x] 38. `test_set_ownership_dispatches_chown`
- [x] 39. `test_set_ini_key_writes_when_changed`
- [x] 40. `test_set_ini_key_skipped_when_value_matches`
- [x] 41. `test_remove_ini_key_removes_existing_key`

## Tests — Gaps

- [ ] 42. Test `remove_ini_key` SKIPPED paths (missing section, missing
  key)

- [ ] 43. Test `_apply_mode_and_owner` is invoked after successful write
  (verify `chmod` / `chown` runner calls follow the `tee`)

- [ ] 44. Test `set_ownership` with user-only and group-only specs
  produces the correct `user`, `:group` chown spec

- [ ] 45. Test dry-run short-circuits for `backup`, `restore`,
  `set_permissions`, `set_ownership`

- [ ] 46. Test `register_validator` extends the registry and is used
  by a subsequent `validate` call

## Known Issues

- [x] 47. Replace deprecated `datetime.utcnow()` in `backup` with
  `datetime.now(timezone.utc)` — migrated as part of the Part B.5 sweep

## Follow-ups

- [ ] 48. **automojo-* reuse check**: `automojo-configuration` exists
  as a sibling package — evaluate whether its config parsing / diffing
  helpers should be used here rather than re-implementing INI/JSON/YAML
  handling.  Similarly check `automojo-dataprofiles` for config-shape
  definitions.
