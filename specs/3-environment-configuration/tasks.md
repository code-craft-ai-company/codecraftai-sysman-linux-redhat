# Implementation Plan — Environment Configuration

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `environmentconfiguration` module
  - `source/packages/automojo/sysman/linux/redhat/environmentconfiguration.py`
    exists

- [x] 2. Define `EnvActionStatus` enum (SUCCESS / FAILED / SKIPPED /
  DRY_RUN)

- [x] 3. Define `EnvironmentConfiguratorConfig` dataclass
  - Fields: `dry_run`, `elevated`

- [x] 4. Define `EnvActionResult` dataclass
  - Fields: `status`, `path`, `name`, `value`, `message`

- [x] 5. Define `EnvConfigError` exception (for unknown users etc.)

- [x] 6. Define line-format patterns:
  - `_EXPORT_LINE_PATTERN` — `^export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$`
  - `_ENV_LINE_PATTERN` — `^([A-Za-z_][A-Za-z0-9_]*)=(.*)$`

## EnvironmentConfigurator — Implemented

- [x] 7. Implement `__init__` accepting runner + optional config

- [x] 8. Implement `set_system_var(name, value, filename)` —
  `/etc/profile.d/<filename>`; uses `_set_export_line` helper;
  idempotent via pre-read compare; seeds new files with `#!/usr/bin/env
  bash` shebang

- [x] 9. Implement `unset_system_var(name, filename)`
  - Removes matching export line; deletes the file via `rm -f` when
    the remaining content is only shebang/comments/blanks
  - SKIPPED when file empty or no matching line

- [x] 10. Implement `set_etc_env_var(name, value)` — `/etc/environment`
  using `_set_env_line` (no `export` prefix)

- [x] 11. Implement `unset_etc_env_var(name)` — removes matching line;
  SKIPPED when no match

- [x] 12. Implement `set_user_var(username, name, value, rc_file)`
  - Uses `_user_rc_path` which runs `getent passwd <user>` via the
    runner to locate the home directory; raises `EnvConfigError` when
    the user is unknown

- [x] 13. Implement `unset_user_var(username, name, rc_file)` —
  matching behaviour

- [x] 14. Implement `query_system(filename)`,
  `query_etc_env()`, `query_user(username, rc_file)` — parse files
  into `dict[str, str]`, stripping quotes via `_strip_quotes`

- [x] 15. Implement value escaping — backslash and double-quote escape
  on the way in, reverse unescape on the way out

- [x] 16. Implement `_read` (cat-via-runner), `_write` (tee-via-runner
  with stdin), `_delete` (rm -f via-runner)

## EnvironmentConfigurator — Gaps (specified but not implemented)

- [ ] 17. Implement `/etc/bashrc` and `/etc/bash.bashrc` support
  (system-wide interactive rc)

- [ ] 18. Implement reading from `/etc/profile` (read-only access for
  inspection, not write)

- [ ] 19. Implement a `query_effective(username)` helper that merges
  `/etc/environment` + `/etc/profile.d/*.sh` + user rc files into the
  effective environment for a given user

- [ ] 20. Implement `filename` validation for `set_system_var` — must
  end in `.sh` and contain only safe characters

- [ ] 21. Implement `append_to_path(name, entry, mode="front"|"back")`
  for appending to PATH-style variables without clobbering existing
  entries

- [ ] 22. SKIPPED detection for empty-file writes when there's nothing
  to remove — currently `set_system_var` on an absent file always
  writes (correct) but error surfaces could be clearer

## Tests — Implemented

- [x] 23. `test_set_system_var_writes_new_file`
- [x] 24. `test_set_system_var_skipped_when_value_matches`
- [x] 25. `test_unset_system_var_removes_line`
- [x] 26. `test_unset_system_var_deletes_empty_file`
- [x] 27. `test_set_etc_env_var_appends_line`
- [x] 28. `test_query_etc_env_parses_lines`
- [x] 29. `test_query_system_parses_export_lines`

## Tests — Gaps

- [ ] 30. Tests for user-scoped variants (`set_user_var`,
  `unset_user_var`, `query_user`) including the `getent passwd` path
  and `EnvConfigError` on unknown user

- [ ] 31. Tests for `unset_etc_env_var`

- [ ] 32. Tests for escape/unescape — value containing backslashes and
  double quotes round-trips correctly through set → query

- [ ] 33. Dry-run short-circuits for every mutating method

- [ ] 34. Tests for `_is_empty_profile` detection (shebang-only,
  comments, blank lines)

- [ ] 35. Test `_set_env_line` with an existing matching `NAME=` line
  (replace path) vs. appending a new line

## Follow-ups

- [ ] 36. **automojo-* reuse check**: if `automojo-configuration`
  exposes a shell-style config parser, prefer it over the local regex
  approach.  Also verify `automojo-interop` doesn't have a
  `getent`-style user lookup helper before keeping the local one.

- [ ] 37. `traceDb.md` exists in this spec folder — unusual; review
  whether it carries content worth preserving or should be deleted.
