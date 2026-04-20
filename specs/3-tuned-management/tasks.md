# Implementation Plan — Tuned Management

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `tunedmanagement` module

- [x] 2. Define `TunedActionStatus` enum (SUCCESS / FAILED / SKIPPED
  / DRY_RUN)

- [x] 3. Define `TunedManagerConfig` (dry_run, elevated)

- [x] 4. Define `TunedActionResult` dataclass

## TunedManager — Implemented

- [x] 5. Implement `__init__` accepting runner + optional config

- [x] 6. Implement `current_profile()` — `tuned-adm active` parsed
  to return the active profile name or `None`

- [x] 7. Implement `list_profiles()` — `tuned-adm list` parsed into
  `list[str]` of available profiles

- [x] 8. Implement `set_profile(profile)` — `tuned-adm profile
  <profile>`; SKIPPED when `current_profile()` already matches

- [x] 9. Implement `recommend()` — `tuned-adm recommend` returning
  the recommended profile name or `None`

- [x] 10. Implement `_dispatch` helper — dry-run short-circuit and
  runner-result mapping

## TunedManager — Gaps (specified but not implemented)

- [ ] 11. `verify_profile()` — `tuned-adm verify` wrapper returning
  a structured result (ok / mismatches list)

- [ ] 12. `tuned.service` lifecycle helpers (enable / disable /
  status) — or explicit delegation to `3-service-management`

- [ ] 13. Custom profile management under `/etc/tuned/` — create a
  profile directory with a `tuned.conf` body via a spec object;
  remove custom profile; list only custom profiles

- [ ] 14. `profile_off()` — `tuned-adm off` to disable all
  tuning without uninstalling

## Tests — Implemented

- [x] 15. `test_current_profile_parses_active_output`
- [x] 16. `test_set_profile_skipped_when_already_active`
- [x] 17. `test_set_profile_dispatches_command`
- [x] 18. `test_list_profiles_parses_list_output`

## Tests — Gaps

- [ ] 19. Test `recommend()` dispatch and parser

- [ ] 20. Test dry-run short-circuit on `set_profile`

- [ ] 21. Test `current_profile()` when no profile is active
  (`tuned-adm active` exits with no-active-profile message)

- [ ] 22. Test `set_profile` FAILED mapping when the profile name
  is invalid

## Follow-ups

- [ ] 23. **automojo-* reuse check**: `3-service-management` for
  tuned.service lifecycle (task 12).
