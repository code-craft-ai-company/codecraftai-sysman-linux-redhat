# Implementation Plan — Log Management

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `logmanagement` module

- [x] 2. Define `LogActionStatus` enum (SUCCESS / FAILED / SKIPPED /
  DRY_RUN)

- [x] 3. Define `LogManagerConfig` (dry_run, elevated)

- [x] 4. Define journal-query dataclasses: `JournalQueryParams`,
  `JournalQueryResult`, `JournalDiskUsage`

- [x] 5. Define `LogActionResult`, `LogFileResult` dataclasses

- [x] 6. Define logrotate dataclasses: `LogrotateConfig`,
  `LogrotateDropInState`

- [x] 7. Define `LogManagementError` exception

## LogManager — Implemented

- [x] 8. Implement `__init__` accepting runner + optional config

- [x] 9. Implement `query_journal(params)` — builds `journalctl`
  invocation from `JournalQueryParams` (unit, priority, since, until,
  lines, follow, output format)

- [x] 10. Implement `journal_disk_usage()` — `journalctl
  --disk-usage` parsed via `_parse_disk_usage`; raises
  `LogManagementError` on non-zero exit

- [x] 11. Implement `vacuum_by_size(size)` — `journalctl
  --vacuum-size=<size>`

- [x] 12. Implement `vacuum_by_time(period)` — `journalctl
  --vacuum-time=<period>`

- [x] 13. Implement `read_log_file(path, lines)` — `tail -n <lines>
  <path>` via runner

- [x] 14. Implement `search_log_file(path, pattern, ...)` — `grep`
  with options; empty-match still SUCCESS

- [x] 15. Implement `rotate_log(config_path)` — `logrotate -f
  <config_path>`

- [x] 16. Implement `write_logrotate_dropin(name, config)` — renders
  `LogrotateConfig` via `_render_logrotate`, writes
  `/etc/logrotate.d/<name>` via `tee`; idempotent via pre-read compare

- [x] 17. Implement `remove_logrotate_dropin(name)` — `rm -f
  /etc/logrotate.d/<name>`

- [x] 18. Implement `query_logrotate_dropin(name)` — returns
  `LogrotateDropInState` (exists + content)

- [x] 19. Implement `_dispatch_mutation`, `_read_file` helpers

## LogManager — Gaps (specified but not implemented)

- [ ] 20. `list_journal_units()` — enumerate services known to the
  journal (`journalctl --field=_SYSTEMD_UNIT`)

- [ ] 21. `export_journal(params, path, format)` — journal export
  helper (JSON, cursor, etc.)

- [ ] 22. `list_logrotate_dropins()` — enumerate entries under
  `/etc/logrotate.d/`

- [ ] 23. `verify_logrotate(config_path)` — `logrotate -d` dry-run
  test with parseable output

- [ ] 24. `query_journal_config()` — read
  `/etc/systemd/journald.conf` + drop-ins under
  `/etc/systemd/journald.conf.d/`

- [ ] 25. `set_journal_config_directive(key, value)` — mirror of the
  kdump / auditd config-directive pattern

## Tests — Implemented

- [x] 26. `test_query_journal_builds_command`
- [x] 27. `test_journal_disk_usage_parses_size`
- [x] 28. `test_journal_disk_usage_raises_on_failure`
- [x] 29. `test_vacuum_by_size_runs_journalctl`
- [x] 30. `test_read_log_file_dispatches_tail`
- [x] 31. `test_search_log_file_no_matches_still_success`
- [x] 32. `test_write_logrotate_dropin_writes_file_when_new`
- [x] 33. `test_write_logrotate_dropin_skipped_when_content_matches`
- [x] 34. `test_query_logrotate_dropin_reports_existence`

## Tests — Gaps

- [ ] 35. Test `vacuum_by_time`

- [ ] 36. Test `rotate_log` dispatch + failure mapping

- [ ] 37. Test `remove_logrotate_dropin`

- [ ] 38. Test `search_log_file` with matching content

- [ ] 39. Test dry-run short-circuits for every mutating method

## Follow-ups

- [ ] 40. **codecraftai-* reuse check**: `codecraftai-errors` / structured
  log abstractions in sibling packages could replace the bespoke
  `LogManagementError` / parsing helpers — verify.
