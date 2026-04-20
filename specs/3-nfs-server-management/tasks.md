# Implementation Plan — NFS Server Management

All tasks are unchecked — new feature.

## Module and Models

- [ ] 1. Create `source/packages/automojo/sysman/linux/redhat/nfsservermanagement.py`
- [ ] 2. Define `NfsActionStatus`, `NfsServerManagerConfig`,
  `NfsActionResult`, `ClientSpec`, `ExportEntry`, `NfsServerStats`
  dataclasses

## Helpers

- [ ] 3. Implement `__init__(runner, config=None, service_manager=None)`
- [ ] 4. Implement `_read_file(path)` / `_write_file(path, content)`
  via `cat` / `tee`
- [ ] 5. Implement `_dispatch(command, detail=None, path=None)` with
  dry-run short-circuit + result mapping
- [ ] 6. Implement `_render_export_line(entry)` / `_parse_export_line(line)`
  helpers for canonical `<path> <client>(<opts>) <client>(<opts>)`
  representation

## Exports

- [ ] 7. Implement `list_active()` — `exportfs -v` → `list[ExportEntry]`
  - _Requirements: 1.1_

- [ ] 8. Implement `list_configured()` — merge `/etc/exports` +
  `/etc/exports.d/*.exports`; tag each entry with `source_file`
  - _Requirements: 1.2_

- [ ] 9. Implement `showmount()` — `showmount -e localhost`
  - _Requirements: 1.3_

- [ ] 10. Implement `add_export(entry)` — append to
  `{exports_dir}/{exports_dropin}` with idempotency; reload via
  `exportfs -r`
  - _Requirements: 2.1, 2.2_

- [ ] 11. Implement `update_export(entry)` — replace matching
  path's line(s) in the drop-in; reload
  - _Requirements: 2.3_

- [ ] 12. Implement `remove_export(path)` — strip matching lines,
  `rm -f` when empty, reload; SKIPPED when no match
  - _Requirements: 2.4, 2.5_

- [ ] 13. Implement `reload()` — `exportfs -r`
  - _Requirements: 3.1_

- [ ] 14. Implement `unexport_all()` — `exportfs -ua`
  - _Requirements: 3.2_

- [ ] 15. Implement `restart()` — delegate to
  `service_manager.restart("nfs-server")`
  - _Requirements: 3.3_

## Idmapping

- [ ] 16. Implement `get_idmap_domain()` / `set_idmap_domain(value)`
  — upsert `Domain = <value>` in `[General]` of
  `/etc/idmapd.conf`; result detail directs caller to restart
  `nfs-idmapd` via ServiceManager
  - _Requirements: 4_

## /etc/nfs.conf

- [ ] 17. Implement `get_directives()` — parse into
  `dict[str, dict[str, str]]`
  - _Requirements: 5.4_

- [ ] 18. Implement `set_directive(section, key, value)` — upsert
  preserving other lines; SKIPPED when unchanged
  - _Requirements: 5.1, 5.2_

- [ ] 19. Implement `remove_directive(section, key)` — strip;
  SKIPPED when absent
  - _Requirements: 5.3_

## Stats

- [ ] 20. Implement `server_stats()` — `nfsstat -s` parsed into
  `NfsServerStats`
  - _Requirements: 6_

## Tests

- [ ] 21. Create `test_nfsservermanagement.py` with `_MockRunner`
  + `_MockServiceManager`
- [ ] 22. Parser tests for `exportfs -v`, `showmount -e`,
  `nfsstat -s`
- [ ] 23. Tests for `add_export` idempotency (same canonical line
  already present → SKIPPED)
- [ ] 24. Test `remove_export` empty-file cleanup — stripping the
  last line triggers `rm -f`
- [ ] 25. Test that mutations followed by reload always dispatch
  `exportfs -r` (unless SKIPPED)
- [ ] 26. Test that `restart()` delegates to `ServiceManager` and
  never spells `systemctl` directly
- [ ] 27. Parser tests for `/etc/nfs.conf` multi-section INI
- [ ] 28. Dry-run sweep
- [ ] 29. Failure-mapping sweep

## Follow-ups

- [ ] 30. **automojo-* reuse check**: no obvious sibling helpers
  for NFS; verify before extending.
