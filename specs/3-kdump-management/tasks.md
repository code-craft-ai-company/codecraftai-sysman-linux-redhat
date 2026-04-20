# Implementation Plan — Kdump Management

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `kdumpmanagement` module

- [x] 2. Define `KdumpActionStatus` enum (SUCCESS / FAILED / SKIPPED
  / DRY_RUN)

- [x] 3. Define `KdumpManagerConfig` (dry_run, elevated)

- [x] 4. Define `KdumpActionResult` dataclass

- [x] 5. Define `KdumpStatus` query dataclass (active, enabled, ...)

## KdumpManager — Implemented

- [x] 6. Implement `__init__` accepting runner + optional config

- [x] 7. Implement `query_status()` — `systemctl is-active kdump`
  and `systemctl is-enabled kdump` → `KdumpStatus`

- [x] 8. Implement `enable()` — `systemctl enable --now kdump`

- [x] 9. Implement `disable()` — `systemctl disable --now kdump`

- [x] 10. Implement `set_config_directive(key, value)` — read
  `/etc/kdump.conf` via `cat`, apply directive via `_apply_directive`
  helper, rewrite via `tee`; SKIPPED when unchanged

- [x] 11. Implement `_dispatch` helper — dry-run short-circuit and
  runner-result mapping

## KdumpManager — Gaps (specified but not implemented)

- [ ] 12. Delegate `crashkernel=` kernel-command-line management to
  `3-boot-management` (the spec explicitly promises this but the
  wiring does not exist)

- [ ] 13. Rebuild kdump initramfs — `kdumpctl rebuild` wrapper

- [ ] 14. `kexec` wrapper for testing kdump without a real panic

- [ ] 15. `/etc/sysconfig/kdump` read/write helpers

- [ ] 16. Remove a directive (`remove_config_directive(key)`) —
  counterpart to `set_config_directive`

## Tests — Implemented

- [x] 17. `test_query_status_reports_active_and_enabled`

- [x] 18. `test_enable_runs_systemctl_enable_now`

- [x] 19. `test_set_config_directive_writes_tee`

## Tests — Gaps

- [ ] 20. Test `disable()` dispatch

- [ ] 21. Test `set_config_directive` SKIPPED when value already
  matches

- [ ] 22. Test `query_status` when kdump unit is absent

- [ ] 23. Test dry-run short-circuits for every mutating method

## Follow-ups

- [ ] 24. **automojo-* reuse check**: `3-boot-management` for
  `crashkernel=` delegation (task 12); consider whether
  `3-service-management` should be used for the `systemctl enable/disable`
  dispatches rather than re-spelling them.
