# Implementation Plan — Remote Log Forwarding

All tasks are unchecked — new feature.

## Module and Models

- [ ] 1. Create `source/packages/ccai/sysman/linux/redhat/remotelogforwarding.py`
- [ ] 2. Define `LogForwardingActionStatus`,
  `RemoteLogForwardingConfig`,
  `LogForwardingActionResult`, `RsyslogForwardRule`,
  `LogForwardingHealth` dataclasses

## Helpers

- [ ] 3. Implement `__init__(runner, config=None, service_manager=None)`
- [ ] 4. Implement `_read_file(path)` / `_write_file(path, content)`
  via `cat` / `tee`
- [ ] 5. Implement `_dispatch(command, detail=None)` with dry-run
  short-circuit
- [ ] 6. Implement `_render_rsyslog_action(rule)` — canonical
  `action(type="omfwd" ...)` renderer including TLS / queue
  options

## rsyslog

- [ ] 7. Implement `list_forward_rules()` — enumerate drop-ins
  under `rsyslog_dropin_dir` matching the ccai naming
  convention; returns raw content + the parsed rule when
  derivable
  - _Requirements: 1 (read side)_

- [ ] 8. Implement `add_forward_rule(rule)` — idempotent drop-in
  write; spool-dir install; validate; delegated reload
  - _Requirements: 1, 2_

- [ ] 9. Implement `remove_forward_rule(name)` — `rm -f` the
  drop-in; SKIPPED when absent; validate + reload on change
  - _Requirements: 1.4_

- [ ] 10. Implement `validate_rsyslog()` — `rsyslogd -N1` returning
  `LogForwardingActionResult` with `ok` computed from exit code
  - _Requirements: 1.5_

## systemd-journal-upload

- [ ] 11. Implement `get_upload_config()` — parse
  `[Upload]` section into `dict[str, str]`
  - _Requirements: 3.4_

- [ ] 12. Implement `set_upload_config(directives)` — idempotent
  upsert
  - _Requirements: 3.1, 3.2_

- [ ] 13. Implement `enable_upload(start=True)` / `disable_upload()`
  — delegate to `service_manager.enable` / `.disable` with
  immediate start / stop
  - _Requirements: 3.3_

## journald forwarding

- [ ] 14. Implement `get_journald_directives()` — merge main
  `journald.conf` + drop-ins under `journald_conf_dropin_dir`
  - _Requirements: 4.3_

- [ ] 15. Implement `set_journald_directive(key, value,
  dropin_name=None)` — upsert in main conf or a named drop-in;
  SKIPPED when unchanged; `detail` flags the required journald
  restart
  - _Requirements: 4.1, 4.2_

## Probe

- [ ] 16. Implement `probe_collector(host, port, tls=False)` —
  TLS vs plain branches; never raises
  - _Requirements: 5_

## Tests

- [ ] 17. Create `test_remotelogforwarding.py` with `_MockRunner`
  + `_MockServiceManager`
- [ ] 18. Renderer tests for `_render_rsyslog_action` across
  plain / TLS / queued variants
- [ ] 19. Tests for `add_forward_rule`:
  - Happy path: spool install + validate + reload chain
  - Idempotent SKIPPED when drop-in content already matches
  - Validate failure aborts the reload delegation
- [ ] 20. Tests for `remove_forward_rule` incl. SKIPPED when
  absent
- [ ] 21. Tests for `set_upload_config` idempotency + preservation
  of unrelated `[Upload]` keys
- [ ] 22. Tests that `set_journald_directive` populates the
  restart-required hint in `result.detail`
- [ ] 23. Tests for `probe_collector` happy / unreachable / TLS
  verified / TLS unverified branches — never raises
- [ ] 24. Dry-run sweep
- [ ] 25. Failure-mapping sweep

## Follow-ups

- [ ] 26. **codecraftai-* reuse check**: no obvious sibling helpers
  for rsyslog plumbing; verify before extending.
