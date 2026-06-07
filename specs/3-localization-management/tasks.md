# Implementation Plan — Localization Management

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `localizationmanagement` module

- [x] 2. Define `LocalizationActionStatus` enum (SUCCESS / FAILED /
  SKIPPED / DRY_RUN)

- [x] 3. Define `LocalizationManagerConfig` (dry_run, elevated)

- [x] 4. Define `LocalizationActionResult`, `LocaleInfo`, `KeymapInfo`,
  `TimezoneInfo` dataclasses

## LocalizationManager — Implemented

- [x] 5. Implement `__init__` accepting runner + optional config

- [x] 6. Implement `query_locale()` — `localectl status` parsed into
  `LocaleInfo`

- [x] 7. Implement `set_locale(lang=None, lc_all=None, ...)` —
  `localectl set-locale LANG=<lang> LC_ALL=<lc_all> ...`; SKIPPED when
  every requested setting already matches `query_locale()`

- [x] 8. Implement `query_keymap()` — parses `localectl status` into
  `KeymapInfo` (vconsole + x11)

- [x] 9. Implement `set_keymap(keymap)` — `localectl set-keymap`

- [x] 10. Implement `query_timezone()` — `timedatectl show` parsed
  into `TimezoneInfo`

- [x] 11. Implement `set_timezone(timezone)` —
  `timedatectl set-timezone <timezone>`

- [x] 12. Implement `set_ntp(enabled)` —
  `timedatectl set-ntp <on|off>`

- [x] 13. Implement `_dispatch` helper — dry-run short-circuit and
  runner-result mapping

## LocalizationManager — Gaps (specified but not implemented)

- [ ] 14. Implement `list_available_locales()` —
  `localectl list-locales` parsed into `list[str]`

- [ ] 15. Implement `list_available_keymaps()` —
  `localectl list-keymaps`

- [ ] 16. Implement `list_available_timezones()` —
  `timedatectl list-timezones`

- [ ] 17. Implement `set_rtc_mode(local_rtc=False)` —
  `timedatectl set-local-rtc <0|1>`

- [ ] 18. Implement direct-write helpers for `/etc/locale.conf`,
  `/etc/vconsole.conf`, and `/etc/localtime` symlink management — for
  environments where `localectl`/`timedatectl` are unavailable
  (minimal containers, chroots)

- [ ] 19. Delegate NTP-source configuration to
  `3-network-configuration` (the spec explicitly says NTP servers are
  out of scope here, but `set_ntp` only toggles the client — not
  sources)

## Tests — Implemented

- [x] 20. `test_query_locale_parses_localectl_status`
- [x] 21. `test_set_locale_skipped_when_current_matches`
- [x] 22. `test_query_timezone_parses_timedatectl_show`
- [x] 23. `test_set_timezone_dispatches_timedatectl`

## Tests — Gaps

- [ ] 24. Tests for `query_keymap` / `set_keymap` dispatch + parsing

- [ ] 25. Test `set_ntp(True)` / `set_ntp(False)` dispatch shapes

- [ ] 26. Test `set_locale` dispatch — multiple env vars in single
  `localectl set-locale` invocation

- [ ] 27. Test dry-run short-circuits for every mutating method

- [ ] 28. Test `set_locale` FAILED mapping (runner non-zero exit)

## Follow-ups

- [ ] 29. **codecraftai-* reuse check**: `timedatectl` / `localectl`
  parsing is bespoke enough that local parsers are fine, but verify
  nothing exists already in `codecraftai-interop` before extending.
