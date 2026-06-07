# Implementation Plan — Resource Control Management

All tasks are unchecked — this is a new feature with no existing
implementation.

## Module and Models

- [ ] 1. Create `source/packages/ccai/sysman/linux/redhat/resourcecontrolmanagement.py`
  - _Requirements: All_

- [ ] 2. Define `ResourceActionStatus` enum
  - _Requirements: 8_

- [ ] 3. Define `ResourceControlManagerConfig` dataclass with
  `dry_run`, `elevated`, `systemd_etc_dir`, `dropin_basename`,
  `cgroup_root`, `default_scope_slice`

- [ ] 4. Define `ResourceActionResult` (including `changed_keys` /
  `skipped_keys` lists), `CgroupUsage`, `SliceSpec` dataclasses

## Helpers

- [ ] 5. Implement `__init__` accepting `runner`, optional `config`,
  and an injected `ServiceManager` (default: thin `None`-handling
  wrapper that raises if daemon-reload is requested without one)

- [ ] 6. Implement `_read_file(path)` via `cat`; empty on non-zero

- [ ] 7. Implement `_write_file(path, content)` via `mkdir -p` +
  `tee` with stdin; dry-run short-circuit

- [ ] 8. Implement `_dispatch(command, detail=None)` with dry-run
  short-circuit and `RunResult`→`ResourceActionResult` mapping

## Properties (Query / Set / Remove)

- [ ] 9. Implement `query_properties(unit, keys=None)` — `systemctl
  show <unit> --property <...>`; returns empty dict on non-zero
  - _Requirements: 1_

- [ ] 10. Implement `set_properties_transient(unit, properties)`
  with idempotency via `query_properties`; aggregates `changed_keys`
  and `skipped_keys`
  - _Requirements: 2_

- [ ] 11. Implement `set_properties_persistent(unit, properties)`
  writing `<systemd_etc_dir>/<unit>.d/<dropin_basename>`; merges
  existing content; delegates daemon-reload
  - _Requirements: 3_

- [ ] 12. Implement `remove_property(unit, key)` stripping matching
  line, `rm -f`-ing empty drop-ins, delegating daemon-reload
  - _Requirements: 4_

## Slice Operations

- [ ] 13. Implement `define_slice(spec)` writing
  `<systemd_etc_dir>/<name>.slice`; idempotent content compare;
  daemon-reload on change
  - _Requirements: 5.1_

- [ ] 14. Implement `set_slice_properties(name, properties)` via
  the same drop-in-file pattern
  - _Requirements: 5.2_

- [ ] 15. Implement `attach_to_slice(unit, slice_name)` delegating
  to `set_properties_persistent` with `{"Slice":
  f"{slice_name}.slice"}`
  - _Requirements: 5.3_

- [ ] 16. Implement `remove_slice(name, strict=False)` — checks for
  attached units; detaches each (or raises when strict) before
  removing the slice file
  - _Requirements: 5.4_

## Transient Scopes

- [ ] 17. Implement `run_scoped(command, slice_=None,
  properties=None)` dispatching `systemd-run --scope --slice
  <slice_> --property KEY=VALUE ... <command...>`
  - _Requirements: 6_

## Cgroup Introspection

- [ ] 18. Implement `cgls(unit=None)` — `systemd-cgls --no-pager
  [--unit <unit>]` returning raw output
  - _Requirements: 7.1_

- [ ] 19. Implement `cgtop()` — `systemd-cgtop --iterations=1
  --raw` parsed into `list[CgroupUsage]`
  - _Requirements: 7.2_

- [ ] 20. Implement `read_cgroup_attr(path, attr)` — `cat
  <cgroup_root>/<path>/<attr>`; returns stripped value or `None`
  - _Requirements: 7.3_

## Tests

- [ ] 21. Create `test_resourcecontrolmanagement.py` with
  `_MockRunner` and `_MockServiceManager` stubs

- [ ] 22. Tests for `query_properties` parser (including empty
  on non-zero exit)

- [ ] 23. Tests for transient set: happy path, fully-SKIPPED path,
  partially-changed path — assert `changed_keys` /
  `skipped_keys` lists are accurate

- [ ] 24. Tests for persistent set: write path, idempotent SKIPPED,
  delegation to mock daemon-reload

- [ ] 25. Tests for `remove_property` incl. empty-file cleanup

- [ ] 26. Tests for `define_slice` + `set_slice_properties` +
  `attach_to_slice`

- [ ] 27. Tests for `remove_slice(strict=True)` raising when
  attached units exist

- [ ] 28. Tests for `run_scoped` command assembly

- [ ] 29. Tests for `cgtop` parser

- [ ] 30. **Assert that the feature never issues `systemctl
  daemon-reload` directly** — always via mock ServiceManager

- [ ] 31. Dry-run sweep across every mutating method

- [ ] 32. Failure-mapping sweep

## Follow-ups

- [ ] 33. **codecraftai-* reuse check**: `3-service-management` is the
  canonical systemd surface; confirm this feature delegates
  daemon-reload, enable/disable, and any lifecycle ops rather than
  re-spelling `systemctl`.
