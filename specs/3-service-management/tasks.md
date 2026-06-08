# Implementation Plan — Service Management

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `servicemanagement` module

- [x] 2. Define `ServiceActionStatus` enum (SUCCESS / FAILED /
  SKIPPED / DRY_RUN)

- [x] 3. Define `ServiceManagerConfig` (dry_run, elevated)

- [x] 4. Define `ServiceInfo` query dataclass (active, enabled,
  masked, PID, description)

- [x] 5. Define `ServiceActionResult` dataclass

- [x] 6. Define `ServiceQueryError` exception

## ServiceManager — Implemented

- [x] 7. Implement `__init__` accepting runner + optional config;
  expose `runner` + `config` properties

- [x] 8. Implement `start(name)` — `systemctl start <name>`;
  SKIPPED when already active (verified via `_query_state`)

- [x] 9. Implement `stop(name)` — `systemctl stop <name>`;
  SKIPPED when already inactive

- [x] 10. Implement `restart(name)` — `systemctl restart <name>`;
  unconditional (never SKIPPED)

- [x] 11. Implement `reload(name)` — `systemctl reload <name>`;
  FAILED when service is inactive (reload on a stopped service is a
  no-op)

- [x] 12. Implement `enable(name)` — `systemctl enable <name>`;
  SKIPPED when already enabled

- [x] 13. Implement `disable(name)` — `systemctl disable <name>`;
  SKIPPED when already disabled

- [x] 14. Implement `mask(name)` — `systemctl mask <name>`;
  SKIPPED when already masked

- [x] 15. Implement `unmask(name)` — `systemctl unmask <name>`;
  SKIPPED when not masked

- [x] 16. Implement `daemon_reload()` — `systemctl daemon-reload`

- [x] 17. Implement `query(name)` — returns `ServiceInfo` combining
  `systemctl is-active` / `is-enabled` / `show` output; raises
  `ServiceQueryError` on unknown state strings

- [x] 18. Implement `get_journal(name, lines=50)` — `journalctl
  -u <name> -n <lines>`; raises on non-zero exit

- [x] 19. Implement `_issue_write`, `_query_state`,
  `_result_from_run` helpers

## ServiceManager — Gaps (specified but not implemented)

- [ ] 20. `list_units(pattern=None)` — `systemctl list-units
  --type=service` parsed into `list[ServiceInfo]`

- [ ] 21. `list_unit_files(pattern=None)` — `systemctl
  list-unit-files` (includes masked/static units that `list-units`
  skips)

- [ ] 22. Per-user (`--user`) service operations — currently only
  system-wide

- [ ] 23. Edit/override a unit via `/etc/systemd/system/<unit>.d/`
  drop-ins (standard way to set resource limits, env, etc. without
  editing the vendor unit)

- [ ] 24. Template-unit instance management (`foo@.service`
  instances) with explicit dispatcher

## Tests — Implemented

- [x] 25. `test_start_runs_systemctl_when_inactive`
- [x] 26. `test_start_skipped_when_already_active`
- [x] 27. `test_stop_skipped_when_inactive`
- [x] 28. `test_restart_unconditional`
- [x] 29. `test_reload_fails_when_inactive`
- [x] 30. `test_reload_runs_when_active`
- [x] 31. `test_dry_run_write`
- [x] 32. `test_enable_runs_when_disabled`
- [x] 33. `test_enable_skipped_when_already_enabled`
- [x] 34. `test_disable_runs_when_enabled`
- [x] 35. `test_mask_runs_when_not_masked`
- [x] 36. `test_mask_skipped_when_masked`
- [x] 37. `test_unmask_skipped_when_not_masked`
- [x] 38. `test_query_returns_info`
- [x] 39. `test_query_raises_on_unknown_state`
- [x] 40. `test_get_journal_returns_stdout`
- [x] 41. `test_get_journal_raises_on_failure`
- [x] 42. `test_daemon_reload_runs`

## Tests — Gaps

- [ ] 43. Test `stop` dispatch when running (counterpart to the
  SKIPPED-when-inactive test)

- [ ] 44. Test failure mapping — runner non-zero exit → status
  FAILED with exit_code/stderr preserved

## Follow-ups

- [ ] 45. **codecraftai-* reuse check**: `3-kdump-management`,
  `3-cockpit-management`, `3-scheduled-tasks` all spell out
  `systemctl` directly.  Evaluate delegating their systemd calls to
  `ServiceManager` to keep lifecycle semantics (especially SKIPPED
  detection) consistent.
