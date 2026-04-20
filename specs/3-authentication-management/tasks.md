# Implementation Plan — Authentication Management

All tasks are unchecked — this is a new feature with no existing
implementation.  Checked items (`[x]`) will be marked once the
corresponding code lands.

## Module and Models

- [ ] 1. Create `source/packages/automojo/sysman/linux/redhat/authenticationmanagement.py`
  with module-level comment describing purpose and the seams with
  related features
  - _Requirements: All_

- [ ] 2. Define `AuthActionStatus` enum (SUCCESS / FAILED / SKIPPED /
  DRY_RUN)
  - _Requirements: 8_

- [ ] 3. Define `AuthenticationManagerConfig` dataclass
  - Fields: `dry_run`, `elevated` (default True),
    `faillock_conf`, `pwquality_conf`, `limits_conf`, `limits_dir`,
    `nsswitch_conf`
  - _Requirements: 8_

- [ ] 4. Define `AuthActionResult` dataclass
  - Fields: `status`, `detail`, `exit_code`, `stdout`, `stderr`,
    `command`
  - _Requirements: 8_

- [ ] 5. Define `AuthselectProfile`, `FaillockState`, `LimitEntry`
  dataclasses
  - _Requirements: 1, 5, 6_

- [ ] 6. Define `AuthselectOverrideError(SemanticError)` for the
  authselect-marker-protection path
  - _Requirements: 3.4_

## authselect Operations

- [ ] 7. Implement `current_profile()` — `authselect current` parsed
  into `AuthselectProfile`
  - _Requirements: 1.1_

- [ ] 8. Implement `list_profiles()` — `authselect list` parsed into
  `list[str]`
  - _Requirements: 1.5_

- [ ] 9. Implement `select_profile(profile, features=None)` —
  `authselect select <profile> [features...] --force`; SKIPPED when
  current profile and feature set already match
  - _Requirements: 1.2, 1.3, 1.4_

- [ ] 10. Implement `enable_feature(feature)` /
  `disable_feature(feature)` — `authselect enable-feature` /
  `disable-feature`; SKIPPED when already in requested state
  - _Requirements: 2_

- [ ] 11. Implement `apply_changes()` — `authselect apply-changes`
  wrapper
  - _Requirements: 2.4_

## Direct PAM Drop-Ins

- [ ] 12. Implement `read_service(service)` — `cat
  /etc/pam.d/<service>`
  - _Requirements: 3.3_

- [ ] 13. Implement `write_service(service, content,
  allow_overwrite_authselect=False)` — idempotent write with
  authselect-marker protection
  - _Requirements: 3.1, 3.2, 3.4_

- [ ] 14. Implement `write_dropin(service, name, content)` — write
  `/etc/pam.d/<service>.d/<name>` (RHEL 9+ drop-in location); create
  parent directory via `mkdir -p` when missing
  - _Requirements: 3_

## pam_pwquality

- [ ] 15. Implement `get_pwquality()` — parse
  `/etc/security/pwquality.conf` into `dict[str, str]`
  - _Requirements: 4.4_

- [ ] 16. Implement `set_pwquality_directive(key, value)` — upsert
  the `key = value` line; SKIPPED when unchanged
  - _Requirements: 4.1, 4.2_

- [ ] 17. Implement `remove_pwquality_directive(key)` — strip
  matching line; SKIPPED when already absent
  - _Requirements: 4.3_

## pam_faillock

- [ ] 18. Implement `get_faillock_state(user)` — `faillock --user
  <user>` parsed into `FaillockState` with `recent_failures` and
  `locked` computed against the current `deny` threshold
  - _Requirements: 5.2_

- [ ] 19. Implement `reset_faillock(user)` — `faillock --user <user>
  --reset`
  - _Requirements: 5.3_

- [ ] 20. Implement `set_faillock_directive(key, value)` — upsert
  in `/etc/security/faillock.conf`; flag `even_deny_root` changes
  prominently in `AuthActionResult.detail`
  - _Requirements: 5.1, 5.4_

- [ ] 21. Implement `remove_faillock_directive(key)` — strip
  matching line
  - _Requirements: 5_

## /etc/security/limits.d

- [ ] 22. Implement `get_limits()` — aggregate
  `/etc/security/limits.conf` and every file under
  `/etc/security/limits.d/` into `list[LimitEntry]`; each entry
  carries the source path for traceability
  - _Requirements: 6.4_

- [ ] 23. Implement `set_limit(entry, drop_in)` — upsert matching
  line (by `domain + type + item`) in the named drop-in; SKIPPED
  when unchanged
  - _Requirements: 6.1, 6.2_

- [ ] 24. Implement `remove_limit(entry, drop_in)` — strip matching
  line; `rm -f` the drop-in file when it becomes empty after the
  removal
  - _Requirements: 6.3_

## /etc/nsswitch.conf

- [ ] 25. Implement `get_nss_sources()` — parse
  `/etc/nsswitch.conf` into `dict[str, list[str]]`
  - _Requirements: 7.1_

- [ ] 26. Implement `set_nss_sources(db, sources)` — replace the
  matching database line, preserving all other lines and trailing
  comments; SKIPPED when unchanged
  - _Requirements: 7.2, 7.3_

## Helpers

- [ ] 27. Implement `_read_file(path)` — `cat` via runner, return
  empty on non-zero exit

- [ ] 28. Implement `_write_file(path, content)` — `tee` via
  runner with stdin; dry-run short-circuit

- [ ] 29. Implement `_dispatch(command, detail)` — runner call with
  dry-run short-circuit and `RunResult`→`AuthActionResult` mapping

## Tests

- [ ] 30. Create `test_authenticationmanagement.py` with a local
  `_MockRunner` stub
  - _Requirements: All_

- [ ] 31. Tests for `current_profile()` / `list_profiles()` /
  `select_profile()` incl. SKIPPED path

- [ ] 32. Tests for `enable_feature()` / `disable_feature()` incl.
  SKIPPED and `apply_changes()` chaining

- [ ] 33. Tests for `write_service()` — happy path, SKIPPED,
  `AuthselectOverrideError` when the marker is present and override
  is not requested

- [ ] 34. Tests for `write_dropin()` including `mkdir -p` of the
  drop-in directory

- [ ] 35. Tests for pwquality: get / set / remove + SKIPPED

- [ ] 36. Tests for faillock: `get_faillock_state` parsing,
  `reset_faillock`, directive upsert, `even_deny_root` detail flag

- [ ] 37. Tests for limits: get / set / remove + empty-file
  cleanup + drop-in path

- [ ] 38. Tests for nsswitch: get / set + preservation of comments
  and non-target database lines

- [ ] 39. Dry-run short-circuit tests for every mutating method

- [ ] 40. Failure-mapping tests — runner non-zero exit → FAILED with
  `exit_code`/`stderr` preserved

## Follow-ups

- [ ] 41. **automojo-* reuse check**: consider whether
  `automojo-configuration` offers a better substrate for the
  pwquality / faillock / nsswitch INI-ish parsers than the bespoke
  regex approach.
