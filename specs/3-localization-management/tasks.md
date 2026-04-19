# Implementation Plan

- [ ] 1. Create the `localizationmanagement` module file
  - Create `source/packages/automojo/sysman/linux/redhat/localizationmanagement.py`
  - Add module-level docstring describing purpose and coordinator-driven design
  - Note that NTP server configuration is out of scope (see `network-configuration`)
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11_

- [ ] 2. Define status enum and result/state dataclasses
  - Implement `LocalizationActionStatus` enum with values: `SUCCESS`, `FAILED`,
    `SKIPPED`, `DRY_RUN`
  - Implement `LocalizationActionResult` dataclass with fields: `status`,
    `operation`, `target`, `exit_code`, `stdout`, `stderr`, `command`,
    `error_message`
  - Implement `TimeState` dataclass with fields: `timezone`, `local_rtc`,
    `can_ntp`, `ntp`, `ntp_synchronized`, `time_usec`, `rtc_time_usec`
  - Implement `LocaleState` dataclass with fields: `variables` (dict),
    `vc_keymap`, `x11_layout`, `x11_model`, `x11_variant`, `x11_options`
  - _Requirements: 1, 2, 4, 5, 6, 8, 9_

- [ ] 3. Define typed exceptions
  - Implement `LocalizationQueryError` with `exit_code` and `stderr` attributes
  - Ensure the exception builds a descriptive multi-line message including the
    failing command
  - _Requirements: 1.3, 3.3, 5.3, 7.3, 10.6_

- [ ] 4. Implement `LocalizationManagerConfig` dataclass
  - Add `dry_run: bool = False` field
  - Add `elevated: bool = False` field
  - _Requirements: 2.6, 4.6, 6.5, 8.6, 9.6, 11_

- [ ] 5. Implement `LocalizationManager.__init__`, `get_time_state()`, and `get_locale_state()`
  - Accept `ICommandRunner` and `LocalizationManagerConfig` as constructor
    arguments; store them as instance variables in `__init__`
  - Implement `get_time_state()` using `timedatectl show`; parse key=value
    lines into a `TimeState` (coerce `yes`/`no` to bool, numeric usec fields
    to int); raise `LocalizationQueryError` on non-zero exit
  - Implement `get_locale_state()` using `localectl status`; parse indented
    output into a `LocaleState` (variables dict plus VC/X11 keymap strings);
    raise `LocalizationQueryError` on non-zero exit
  - Both queries run with `elevated=False` regardless of config
  - _Requirements: 1, 5, 11.3_

- [ ] 6. Implement timezone capabilities — `set_timezone()` and `list_timezones()`
  - Implement `list_timezones()` via `timedatectl list-timezones`; raise
    `LocalizationQueryError` on non-zero exit
  - In `set_timezone()`: validate the supplied zone against `list_timezones()`
    and return `FAILED` with an `error_message` if not present (do NOT invoke
    the mutating runner call)
  - Call `get_time_state()` to check idempotency; return `SKIPPED` when the
    current timezone already equals the requested value
  - Honour `dry_run` flag; return `DRY_RUN` result without invoking the runner
  - Pass `elevated` flag to runner for `timedatectl set-timezone <zone>`
  - Parse result into `LocalizationActionResult`
  - _Requirements: 2, 3_

- [ ] 7. Implement `set_local_rtc()`
  - Call `get_time_state()` to check idempotency; return `SKIPPED` if
    `local_rtc` already matches
  - Build `timedatectl set-local-rtc 0` or `timedatectl set-local-rtc 1`
  - When `adjust_system_clock` is `False`, append `--adjust-system-clock=false`
  - Honour `dry_run` flag
  - Pass `elevated` flag to runner; map result to `LocalizationActionResult`
  - _Requirements: 4_

- [ ] 8. Implement locale capabilities — `set_locale()` and `list_locales()`
  - Implement `list_locales()` via `localectl list-locales`; raise
    `LocalizationQueryError` on non-zero exit
  - In `set_locale()`: if a `LANG` variable is supplied, validate it against
    `list_locales()` and return `FAILED` with an `error_message` if not present
  - Call `get_locale_state()` and compare every supplied variable to the
    current `variables` dict; return `SKIPPED` only when every supplied
    variable already matches
  - Build `localectl set-locale LANG=<v> [LC_*=<v> ...]` preserving supplied
    key order for deterministic commands
  - Honour `dry_run` flag
  - Pass `elevated` flag to runner; map result to `LocalizationActionResult`
  - _Requirements: 6, 7_

- [ ] 9. Implement VC keymap capability — `set_vc_keymap()`
  - Query `localectl list-keymaps` to validate the supplied keymap; return
    `FAILED` with an `error_message` if not present
  - Call `get_locale_state()` to check idempotency; return `SKIPPED` when
    `vc_keymap` already matches
  - Build `localectl set-keymap <keymap>`; append `--convert` when requested
  - Honour `dry_run` flag
  - Pass `elevated` flag to runner; map result to `LocalizationActionResult`
  - _Requirements: 8_

- [ ] 10. Implement X11 keymap capability — `set_x11_keymap()`
  - Validate the supplied layout against `list_x11_layouts()`; return
    `FAILED` with an `error_message` if not present
  - Call `get_locale_state()` to check idempotency; return `SKIPPED` when
    every supplied non-`None` component already matches current X11 state
  - Build `localectl set-x11-keymap <layout> [<model> [<variant> [<options>]]]`;
    append `--convert` when requested
  - Honour `dry_run` flag
  - Pass `elevated` flag to runner; map result to `LocalizationActionResult`
  - _Requirements: 9_

- [ ] 11. Implement X11 list queries — `list_x11_layouts`, `list_x11_models`, `list_x11_variants`, `list_x11_options`
  - Build `localectl list-x11-keymap-layouts`, `list-x11-keymap-models`,
    `list-x11-keymap-variants <layout>`, `list-x11-keymap-options`
  - Parse newline-separated output into a `list[str]`
  - All four run with `elevated=False`
  - Raise `LocalizationQueryError` on any non-zero exit code
  - _Requirements: 10_

- [ ] 12. Create test module and mock infrastructure
  - Create `source/testroots/testbase/automojo/tests/sysman/linux/redhat/test_localizationmanagement.py`
  - Implement `MockCommandRunner` that returns configurable `RunResult` values
    keyed by matched argv prefix (e.g. `timedatectl show`, `localectl
    list-locales`)
  - Set up hypothesis testing framework configuration (`settings(max_examples=100)`)
  - Create test fixtures for canonical `timedatectl show` and `localectl
    status` output
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11_

- [ ] 13. Write property-based test for idempotent set-timezone
  - **Property 1: Idempotent Set-Timezone**
  - Generate random timezone strings that match a mock `TimeState.timezone`
  - Verify `set_timezone()` returns `SKIPPED` and the mutating runner call is
    not invoked
  - **Validates: Requirements 2.2**
  - _Requirements: 2_

- [ ] 14. Write property-based test for unknown timezone rejection
  - **Property 2: Unknown Timezone Rejection**
  - Generate zone strings guaranteed absent from the mock `list-timezones`
  - Verify `set_timezone()` returns `FAILED` with `error_message` set and the
    mutating runner call is not invoked
  - **Validates: Requirements 2.3**
  - _Requirements: 2_

- [ ] 15. Write property-based test for idempotent set-locale
  - **Property 3: Idempotent Set-Locale**
  - Generate random dicts of locale variables where every entry matches the
    mock `LocaleState.variables`
  - Verify `set_locale()` returns `SKIPPED` and the mutating runner call is
    not invoked (partial matches must NOT skip)
  - **Validates: Requirements 6.3**
  - _Requirements: 6_

- [ ] 16. Write property-based test for unknown locale rejection
  - **Property 4: Unknown Locale Rejection**
  - Generate `LANG` values guaranteed absent from the mock `list-locales`
  - Verify `set_locale()` returns `FAILED` with `error_message` set and the
    mutating runner call is not invoked
  - **Validates: Requirements 6.2**
  - _Requirements: 6_

- [ ] 17. Write property-based test for idempotent set-vc-keymap
  - **Property 5: Idempotent Set-VC-Keymap**
  - Generate random keymap names that match the mock `LocaleState.vc_keymap`
    (and separately, names absent from `list-keymaps`)
  - Verify `set_vc_keymap()` returns `SKIPPED` for matches and `FAILED` for
    unknown keymaps, with no mutating runner call in either case
  - **Validates: Requirements 8.3, 8.4**
  - _Requirements: 8_

- [ ] 18. Write property-based test for idempotent set-x11-keymap
  - **Property 6: Idempotent Set-X11-Keymap**
  - Generate random `(layout, model, variant, options)` tuples where every
    supplied non-`None` component matches the mock X11 state
  - Verify `set_x11_keymap()` returns `SKIPPED` and the mutating runner call
    is not invoked; verify unknown-layout inputs return `FAILED`
  - **Validates: Requirements 9.3, 9.4**
  - _Requirements: 9_

- [ ] 19. Write property-based test for idempotent set-local-rtc
  - **Property 7: Idempotent Set-Local-RTC**
  - Generate boolean `local` values that match the mock
    `TimeState.local_rtc`
  - Verify `set_local_rtc()` returns `SKIPPED` and the mutating runner call
    is not invoked
  - **Validates: Requirements 4.3**
  - _Requirements: 4_

- [ ] 20. Write property-based test for dry-run safety
  - **Property 8: Dry-Run Safety**
  - Generate random mutating operations (timezone, local_rtc, locale, vc
    keymap, x11 keymap) with `dry_run=True`
  - Verify the mutating runner call is never invoked and every result has
    `status=DRY_RUN`
  - **Validates: Requirements 2.6, 4.6, 6.5, 8.6, 9.6**
  - _Requirements: 2, 4, 6, 8, 9_

- [ ] 21. Write property-based test for elevation consistency
  - **Property 9: Elevation Consistency**
  - Generate random operations with `elevated=True`/`False`
  - Verify mutating operations pass the `elevated` flag through correctly
  - Verify read-only queries (`get_time_state`, `get_locale_state`, all
    `list_*`) never invoke the runner with `elevated=True`
  - **Validates: Requirements 11.1, 11.2, 11.3**
  - _Requirements: 11_

- [ ] 22. Write property-based test for status mapping correctness
  - **Property 10: Status Mapping Correctness**
  - Generate random exit codes for mutating commands
  - Verify `0` maps to `SUCCESS`; non-zero maps to `FAILED` with `exit_code`
    and `stderr` captured; validation failures map to `FAILED` with
    `error_message` set
  - **Validates: Requirements 2.4, 2.5, 4.5, 6.4, 8.5, 9.5**
  - _Requirements: 2, 4, 6, 8, 9_

- [ ] 23. Write property-based test for query failure raises `LocalizationQueryError`
  - **Property 11: Query Failure Raises `LocalizationQueryError`**
  - Generate random non-zero exit codes for each read-only query (`show`,
    `status`, `list-timezones`, `list-locales`, `list-keymaps`,
    `list-x11-keymap-*`)
  - Verify `LocalizationQueryError` is raised and captures exit code + stderr
  - **Validates: Requirements 1.3, 3.3, 5.3, 7.3, 10.6**
  - _Requirements: 1, 3, 5, 7, 10_

- [ ] 24. Write unit tests for `get_time_state()`
  - Test: canonical `timedatectl show` output — returns a populated
    `TimeState` with correct types
  - Test: `yes`/`no` fields parsed as booleans
  - Test: non-zero exit code — raises `LocalizationQueryError`
  - _Requirements: 1_

- [ ] 25. Write unit tests for `set_timezone()`
  - Test: current zone differs — runner called with
    `timedatectl set-timezone <zone>`, returns `SUCCESS`
  - Test: current zone already matches — returns `SKIPPED`, mutating runner
    call not invoked
  - Test: unknown zone — returns `FAILED` with `error_message`, mutating
    runner call not invoked
  - Test: runner exit code non-zero — returns `FAILED` with exit code + stderr
  - Test: dry-run mode — returns `DRY_RUN`, mutating runner call not invoked
  - Test: elevated mode — runner called with `elevated=True`
  - _Requirements: 2_

- [ ] 26. Write unit tests for `list_timezones()`
  - Test: canonical output — returns list of zone strings
  - Test: non-zero exit code — raises `LocalizationQueryError`
  - Test: runner called with `elevated=False`
  - _Requirements: 3_

- [ ] 27. Write unit tests for `set_local_rtc()`
  - Test: current mode differs — runner called with
    `timedatectl set-local-rtc 0|1`, returns `SUCCESS`
  - Test: `adjust_system_clock=False` — `--adjust-system-clock=false` appended
  - Test: current mode already matches — returns `SKIPPED`
  - Test: dry-run mode — returns `DRY_RUN`, runner not invoked
  - _Requirements: 4_

- [ ] 28. Write unit tests for `get_locale_state()`
  - Test: canonical `localectl status` output — returns a populated
    `LocaleState` with variables dict and VC/X11 keymap fields
  - Test: missing VC/X11 fields — returns `None` for those fields
  - Test: non-zero exit code — raises `LocalizationQueryError`
  - _Requirements: 5_

- [ ] 29. Write unit tests for `set_locale()`
  - Test: one variable differs — runner called with `localectl set-locale
    LANG=<v>`, returns `SUCCESS`
  - Test: multiple variables differ — runner command preserves supplied order
  - Test: every supplied variable already matches — returns `SKIPPED`,
    mutating runner call not invoked
  - Test: partial match (one differs, one matches) — runner invoked with all
    supplied variables, returns `SUCCESS` (not `SKIPPED`)
  - Test: unknown `LANG` — returns `FAILED` with `error_message`, mutating
    runner call not invoked
  - Test: dry-run mode — returns `DRY_RUN`, runner not invoked
  - _Requirements: 6_

- [ ] 30. Write unit tests for `list_locales()`
  - Test: canonical output — returns list of locale strings
  - Test: non-zero exit code — raises `LocalizationQueryError`
  - _Requirements: 7_

- [ ] 31. Write unit tests for `set_vc_keymap()`
  - Test: current keymap differs — runner called with `localectl set-keymap
    <keymap>`, returns `SUCCESS`
  - Test: `convert=True` — `--convert` flag appended
  - Test: unknown keymap — returns `FAILED` with `error_message`, runner not
    invoked for the mutating command
  - Test: current keymap already matches — returns `SKIPPED`
  - Test: dry-run mode — returns `DRY_RUN`, runner not invoked
  - _Requirements: 8_

- [ ] 32. Write unit tests for `set_x11_keymap()`
  - Test: full `(layout, model, variant, options)` tuple — runner called with
    correct positional args, returns `SUCCESS`
  - Test: only layout supplied — other positional args omitted from command
  - Test: `convert=True` — `--convert` flag appended
  - Test: unknown layout — returns `FAILED` with `error_message`, mutating
    runner call not invoked
  - Test: every supplied component already matches — returns `SKIPPED`
  - Test: dry-run mode — returns `DRY_RUN`, runner not invoked
  - _Requirements: 9_

- [ ] 33. Write unit tests for the X11 list queries
  - Test: `list_x11_layouts()` — canonical output parsed to list of strings
  - Test: `list_x11_models()` — canonical output parsed to list of strings
  - Test: `list_x11_variants(layout)` — command includes the layout argument
  - Test: `list_x11_options()` — canonical output parsed to list of strings
  - Test: each query raises `LocalizationQueryError` on non-zero exit
  - Test: each query runs with `elevated=False`
  - _Requirements: 10, 11.3_

- [ ] 34. Checkpoint - Ensure all tests pass
  - Run all unit tests and property-based tests
  - Verify all properties pass with 100+ iterations
  - Ensure all tests pass, ask the user if questions arise

