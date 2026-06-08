# Implementation Plan — SSH Server Configuration

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

Complements `3-security-compliance` (hardening checks) and
`3-user-group-management` (per-user `authorized_keys`).

## Module and Models

- [x] 1. Create the `sshserverconfiguration` module

- [x] 2. Define `SshdActionStatus` enum (SUCCESS / FAILED / SKIPPED
  / DRY_RUN)

- [x] 3. Define `SshServerConfig` (dry_run, elevated,
  dropin_dir="/etc/ssh/sshd_config.d", ...)

- [x] 4. Define `SshdActionResult`, `SshdValidateResult` dataclasses

## SshServerConfigurator — Implemented

- [x] 5. Implement `__init__` accepting runner + optional config

- [x] 6. Implement `get_effective_directive(name)` — `sshd -T` +
  case-insensitive name match returning the effective value

- [x] 7. Implement `get_full_effective_config()` — `sshd -T` parsed
  into `dict[str, str]`

- [x] 8. Implement `set_directive_dropin(name, directive, value)`
  — writes `<dropin_dir>/<name>.conf` via `tee`; idempotent via
  pre-read compare; uses `_apply_directive` for upsert

- [x] 9. Implement `delete_directive_dropin(name, directive)` —
  strips matching directive from the drop-in via
  `_remove_directive`; rewrites via `tee`

- [x] 10. Implement `validate_config()` — `sshd -t` returning
  `SshdValidateResult` (ok, exit_code, stderr)

- [x] 11. Implement `reload_config()` — runs `validate_config()`
  first; returns FAILED (without reloading) when validation fails;
  otherwise `systemctl reload sshd`

- [x] 12. Implement `_read` (cat-via-runner), `_write`
  (tee-via-runner with stdin), helpers

## SshServerConfigurator — Gaps (specified but not implemented)

- [ ] 13. `Match` block support — set/remove directives inside a
  `Match Host X` / `Match User Y` block within a drop-in

- [ ] 14. Direct `/etc/ssh/sshd_config` read/write (currently only
  drop-ins under `/etc/ssh/sshd_config.d/` are managed)

- [ ] 15. Host-key file management (`/etc/ssh/ssh_host_*`) —
  generate, rotate, inspect

- [ ] 16. List directives from a specific drop-in file
  (`query_dropin(name)`) returning a `dict[str, str]`

- [ ] 17. Service lifecycle helpers that delegate to
  `3-service-management.ServiceManager` instead of spelling
  `systemctl reload sshd` directly

## Tests — Implemented

- [x] 18. `test_get_effective_directive_parses_sshd_T`
- [x] 19. `test_set_directive_dropin_writes_new_file`
- [x] 20. `test_set_directive_dropin_skipped_when_unchanged`
- [x] 21. `test_reload_config_fails_when_validation_fails`
- [x] 22. `test_reload_config_runs_systemctl_reload_when_valid`

## Tests — Gaps

- [ ] 23. Test `get_full_effective_config()` parser

- [ ] 24. Test `delete_directive_dropin` — with matching directive,
  with no match

- [ ] 25. Test `validate_config` SUCCESS path

- [ ] 26. Test dry-run short-circuits for mutating methods

## Follow-ups

- [ ] 27. **codecraftai-* reuse check**: `3-service-management` is the
  canonical systemd surface; delegating `reload_config` would keep
  restart/reload semantics consistent.  Cross-cutting with
  `3-security-compliance.apply_ssh_hardening` — keep the seam clear.
