# Implementation Plan — Kernel Parameters

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

The module exposes two managers: `KernelParamManager` (sysctl) and
`KernelModuleManager` (modprobe / `/etc/modules-load.d/`).

## Module and Models

- [x] 1. Create the `kernelparameters` module

- [x] 2. Define `KernelParamActionStatus` / `KernelModuleActionStatus`
  enums (SUCCESS / FAILED / SKIPPED / DRY_RUN)

- [x] 3. Define `KernelManagerConfig` (dry_run, elevated)

- [x] 4. Define `KernelParamInfo`, `KernelParamActionResult`,
  `KernelModuleInfo`, `KernelModuleActionResult` dataclasses

## KernelParamManager — Implemented

- [x] 5. Implement `__init__` accepting runner + optional config

- [x] 6. Implement `read(key)` — `sysctl -n <key>` returning
  `KernelParamInfo` or `None`

- [x] 7. Implement `write(key, value)` — runtime via `sysctl -w`;
  SKIPPED when value already matches

- [x] 8. Implement `persist(key, value, filename=None)` —
  `/etc/sysctl.d/<filename>` drop-in; idempotent via pre-read compare

- [x] 9. Implement `remove_persisted(key, filename=None)` — strip
  matching line from drop-in; SKIPPED when absent

- [x] 10. Implement `apply_all()` — `sysctl --system`

- [x] 11. Implement `query_all()` — `sysctl -a`; parse into list of
  `KernelParamInfo`

- [x] 12. Implement `_read_file` / `_write_drop_in` helpers

## KernelModuleManager — Implemented

- [x] 13. Implement `__init__` accepting runner + optional config

- [x] 14. Implement `load(name, params=None)` — `modprobe [params]
  <name>`; SKIPPED when already loaded (verified via `lsmod`)

- [x] 15. Implement `unload(name)` — `modprobe -r <name>`

- [x] 16. Implement `query_loaded()` — `lsmod` parsed into
  `KernelModuleInfo` list

- [x] 17. Implement `persist_load(name)` —
  `/etc/modules-load.d/<name>.conf` with module name on first line;
  idempotent

## Gaps (specified but not implemented)

- [ ] 18. `remove_persisted_load(name)` — counterpart to
  `persist_load`

- [ ] 19. `blacklist_module(name, filename=None)` —
  `/etc/modprobe.d/<filename>.conf` with `blacklist <name>`

- [ ] 20. `query_module_info(name)` — `modinfo <name>` parsed into a
  structured record (path, deps, parameters, signature)

- [ ] 21. Support per-key file paths in `persist(key, value,
  filename="...")` with default chosen by the first two path components
  of `key`

## Tests — Implemented

- [x] 22. `test_sysctl_read_returns_info`
- [x] 23. `test_sysctl_write_skipped_when_value_matches`
- [x] 24. `test_sysctl_write_dispatches_sysctl_w`
- [x] 25. `test_persist_writes_drop_in_file`
- [x] 26. `test_persist_skipped_when_value_matches`
- [x] 27. `test_apply_all_runs_sysctl_system`
- [x] 28. `test_module_load_dispatches_modprobe`
- [x] 29. `test_module_load_skipped_when_already_loaded`
- [x] 30. `test_module_persist_load_appends_file`

## Tests — Gaps

- [ ] 31. Test `remove_persisted` path

- [ ] 32. Test `query_all` parser

- [ ] 33. Test module `unload` and `query_loaded` parser

- [ ] 34. Test dry-run short-circuits for every mutating method

## Follow-ups

- [ ] 35. **automojo-* reuse check**: sysctl lookups are simple enough
  that local implementation is fine; verify before adding any new
  parsing helpers.
