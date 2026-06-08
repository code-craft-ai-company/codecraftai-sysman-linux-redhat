# Implementation Plan — Scheduled Tasks

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `scheduledtasks` module

- [x] 2. Define `TaskType` enum (CRON / SYSTEMD)

- [x] 3. Define `ScheduledTaskConfig` (dry_run, elevated)

- [x] 4. Define `CronJobEntry` dataclass (name, schedule, command,
  user)

- [x] 5. Define `SystemdTimerEntry` dataclass (name, schedule,
  command, user, description, ...)

- [x] 6. Define `ScheduledTaskInfo`, `ScheduledTaskResult` dataclasses

- [x] 7. Define `ScheduledTaskQueryError` exception

## ScheduledTaskManager — Implemented

- [x] 8. Implement `__init__` accepting runner + optional config

- [x] 9. Implement `add_cron_job(entry)` — renders via `_render_cron`,
  writes `/etc/cron.d/<name>` via `tee`; idempotent via pre-read
  compare; validates name via `_ensure_valid_name`

- [x] 10. Implement `remove_cron_job(name)` — `rm -f
  /etc/cron.d/<name>`

- [x] 11. Implement `add_systemd_timer(entry)` — renders `.service`
  and `.timer` unit files, writes both under
  `/etc/systemd/system/`, then runs `systemctl daemon-reload` +
  `systemctl enable --now <name>.timer`

- [x] 12. Implement `remove_systemd_timer(name)` — stops/disables
  the timer, removes both unit files, runs `systemctl
  daemon-reload`

- [x] 13. Implement `query_cron(name)` — reads
  `/etc/cron.d/<name>` via `cat`; parses via `_parse_cron_content`;
  returns `Optional[ScheduledTaskInfo]`

- [x] 14. Implement `query_systemd(name)` — `systemctl show
  <name>.timer` parsed via `_parse_show_output`

- [x] 15. Implement `_render_cron`, `_render_systemd_unit`,
  `_parse_cron_content`, `_parse_ini_section`,
  `_parse_show_output`, `_ensure_valid_name` helpers

## ScheduledTaskManager — Gaps (specified but not implemented)

- [ ] 16. `list_cron_jobs()` — enumerate entries under
  `/etc/cron.d/`

- [ ] 17. `list_systemd_timers()` — `systemctl list-timers --all`
  parsed into `list[ScheduledTaskInfo]`

- [ ] 18. Update an existing job/timer without full remove-and-add
  cycle

- [ ] 19. Pause / resume semantics for systemd timers (distinct from
  remove)

- [ ] 20. Support per-user crontabs (`crontab -u <user>`) — currently
  only `/etc/cron.d/` drop-ins are managed

## Tests — Implemented

- [x] 21. `test_add_cron_job_writes_drop_in`
- [x] 22. `test_add_cron_job_skipped_when_content_matches`
- [x] 23. `test_add_systemd_timer_writes_units`
- [x] 24. `test_query_cron_returns_info_when_present`
- [x] 25. `test_query_cron_returns_none_when_absent`
- [x] 26. `test_invalid_name_raises_value_error`

## Tests — Gaps

- [ ] 27. Test `remove_cron_job` dispatch

- [ ] 28. Test `remove_systemd_timer` full sequence (stop + disable
  + rm + daemon-reload)

- [ ] 29. Test `query_systemd` parser

- [ ] 30. Test dry-run short-circuits for all mutating methods

## Follow-ups

- [ ] 31. **codecraftai-* reuse check**: `3-service-management` already
  exposes `daemon_reload()`, `enable()`, `disable()` — evaluate
  delegating to it rather than re-spelling `systemctl` here.
