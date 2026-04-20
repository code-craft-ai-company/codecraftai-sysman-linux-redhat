# Implementation Plan — Precision Time Management

All tasks are unchecked — new feature.

## Module and Models

- [ ] 1. Create `source/packages/automojo/sysman/linux/redhat/precisiontimemanagement.py`
- [ ] 2. Define `PtpActionStatus`, `PrecisionTimeManagerConfig`,
  `PtpActionResult`, `PtpCurrentDataSet`, `PtpParentDataSet`,
  `PtpPortDataSet` dataclasses

## Helpers

- [ ] 3. Implement `__init__(runner, config=None, service_manager=None)`
- [ ] 4. Implement `_read_file(path)` / `_write_file(path,
  content)` via `cat` / `tee`
- [ ] 5. Implement `_dispatch(command, detail=None)` with dry-run
  short-circuit + result mapping
- [ ] 6. Implement `_parse_ptp4l_conf(text)` and
  `_render_ptp4l_conf(sections)` — whitespace-separated
  `key value` syntax, NOT `key = value`

## ptp4l.conf

- [ ] 7. Implement `get_directives()` — parse into
  `dict[str, dict[str, str]]`
  - _Requirements: 1.1_

- [ ] 8. Implement `set_directive(section, key, value)` with
  idempotency
  - _Requirements: 1.2, 1.4_

- [ ] 9. Implement `remove_directive(section, key)` with SKIPPED
  when absent
  - _Requirements: 1.3_

## Preset Helpers

- [ ] 10. Implement `set_domain(n)` — `domainNumber <n>` in
  `[global]`
  - _Requirements: 2.1_

- [ ] 11. Implement `set_transport(mode)` — `network_transport`
  - _Requirements: 2.2_

- [ ] 12. Implement `set_delay_mechanism(mode)` —
  `delay_mechanism E2E|P2P`
  - _Requirements: 2.3_

- [ ] 13. Implement `set_interfaces(interfaces)` — adds / removes
  per-interface sections based on the requested list
  - _Requirements: 2.4_

## sysconfig

- [ ] 14. Implement `get_ptp4l_options()` / `set_ptp4l_options(value)`
  - _Requirements: 3_

- [ ] 15. Implement `get_phc2sys_options()` / `set_phc2sys_options(value)`
  - _Requirements: 6_

## Service Lifecycle

- [ ] 16. Implement `start_ptp4l()` / `stop_ptp4l()` /
  `enable_ptp4l()` / `disable_ptp4l()` delegating to
  `service_manager.<op>("ptp4l")`
  - _Requirements: 4.1_

- [ ] 17. Implement `start_phc2sys()` / `stop_phc2sys()` /
  `enable_phc2sys()` / `disable_phc2sys()` — with the ptp4l
  dependency check before start / enable
  - _Requirements: 4.2_

## pmc Queries

- [ ] 18. Implement `current_data_set()` — parse
  `pmc CURRENT_DATA_SET`; never raises
  - _Requirements: 5.1, 5.4_

- [ ] 19. Implement `parent_data_set()` — parse
  `pmc PARENT_DATA_SET`
  - _Requirements: 5.2_

- [ ] 20. Implement `port_data_sets()` — parse
  `pmc PORT_DATA_SET`
  - _Requirements: 5.3_

## Tests

- [ ] 21. Create `test_precisiontimemanagement.py` with
  `_MockRunner` + `_MockServiceManager`
- [ ] 22. Parser tests for `ptp4l.conf` — `[global]` + interface
  sections + comments preservation
- [ ] 23. Renderer tests — assert `key value` (not `key = value`)
  on every emitted line
- [ ] 24. Tests for `set_interfaces` adding and stripping sections
- [ ] 25. Tests for sysconfig `OPTIONS="..."` upsert idempotency
- [ ] 26. Parser tests for each pmc query; test that queries don't
  raise when the daemon is down
- [ ] 27. Test that `start_phc2sys` refuses without ptp4l posture
- [ ] 28. Test that every lifecycle op delegates to
  `ServiceManager` rather than issuing `systemctl`
- [ ] 29. Dry-run sweep
- [ ] 30. Failure-mapping sweep

## Follow-ups

- [ ] 31. **automojo-* reuse check**: no obvious sibling helpers
  for PTP plumbing; verify before extending.
