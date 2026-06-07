# Implementation Plan — DNS Server Management

All tasks are unchecked — new feature.

## Module and Models

- [ ] 1. Create `source/packages/ccai/sysman/linux/redhat/dnsservermanagement.py`
- [ ] 2. Define `DnsActionStatus` enum and `DnsServerBackend` enum
  (BIND / UNBOUND / DNSMASQ)
- [ ] 3. Define `DnsServerManagerConfig` dataclass with per-backend
  directories
- [ ] 4. Define `DnsActionResult`, `ZoneEntry`, `DnsValidateResult`
  (including `zone_check_results` list), `DnsServerStatus`
  dataclasses
- [ ] 5. Define `DnsBackendNotAvailableError` and
  `DnsBackendMismatchError` as `SemanticError` subtypes

## Helpers

- [ ] 6. Implement `__init__(runner, config=None,
  service_manager=None)` — defers backend auto-detect to first
  operation
- [ ] 7. Implement `_ensure_backend()` — probes `command -v
  <bin>` for each backend binary
- [ ] 8. Implement `_require_backend(expected)` — raises
  `DnsBackendMismatchError` when wrong
- [ ] 9. Implement `_read_file(path)` / `_write_file(path,
  content)` via `cat` / `tee`
- [ ] 10. Implement `_dispatch(command, detail=None)` with
  dry-run short-circuit + result mapping

## Validation and Control

- [ ] 11. Implement `validate()` — backend dispatch; for BIND,
  additional per-zone `named-checkzone` calls
  - _Requirements: 2_

- [ ] 12. Implement `reload()` — validate-before-reload; backend
  dispatch (rndc / unbound-control / service_manager.restart)
  - _Requirements: 6.1, 6.2_

- [ ] 13. Implement `restart()` — delegate to
  `service_manager.restart(<name>)`
  - _Requirements: 6.3_

- [ ] 14. Implement `status()` — backend dispatch; returns
  `DnsServerStatus`
  - _Requirements: 7_

## BIND

- [ ] 15. Implement `write_zone_file(zone, content)` —
  `{bind_zones_dir}/<zone>.db` via `tee`; idempotent
  - _Requirements: 3.1_

- [ ] 16. Implement `define_zone(entry)` — drop-in under
  `bind_conf_dropin_dir`; idempotent; calls `reload()`
  - _Requirements: 3.2, 3.5_

- [ ] 17. Implement `remove_zone(name)` — strip block + `rm -f`
  data file; `reload()` on change
  - _Requirements: 3.3_

- [ ] 18. Implement `query_zone(name)` — returns `ZoneEntry` or
  `None`
  - _Requirements: 3.4_

## Unbound

- [ ] 19. Implement `add_forward_zone(name, forwarders)` /
  `remove_forward_zone(name)` with idempotent upsert of
  `forward-zone:` blocks in
  `unbound_dropin_dir/unbound_dropin_basename`; `reload()` on
  change
  - _Requirements: 4.1, 4.3, 4.4_

- [ ] 20. Implement `add_stub_zone(name, addresses)` /
  `remove_stub_zone(name)` with `stub-zone:` blocks
  - _Requirements: 4.2, 4.3_

## dnsmasq

- [ ] 21. Implement `set_static_host(host, ip)` /
  `remove_static_host(host)` using `host-record=` entries
  - _Requirements: 5.1_

- [ ] 22. Implement `set_forwarder_domain(domain, servers)` /
  `remove_forwarder_domain(domain)` using `server=/<domain>/<ip>`
  entries
  - _Requirements: 5.2_

- [ ] 23. Ensure every dnsmasq mutation triggers a
  `service_manager.restart("dnsmasq")`
  - _Requirements: 5.3_

## Tests

- [ ] 24. Create `test_dnsservermanagement.py` with `_MockRunner`
  + `_MockServiceManager`
- [ ] 25. Tests for backend auto-detect (first-found-wins, raises
  when none present)
- [ ] 26. Tests for `DnsBackendMismatchError` when calling
  backend-specific methods against the wrong backend
- [ ] 27. Tests for BIND zone define / remove / query and for the
  `rndc reload` chaining
- [ ] 28. Tests for Unbound forward-zone / stub-zone idempotency
- [ ] 29. Tests for dnsmasq static-host / forwarder-domain
  idempotency and the `service_manager.restart` chaining
- [ ] 30. Tests that `reload()` validates first and does not
  dispatch when validation fails
- [ ] 31. Dry-run sweep
- [ ] 32. Failure-mapping sweep

## Follow-ups

- [ ] 33. **codecraftai-* reuse check**: nothing obvious in sibling
  packages for DNS-server plumbing; verify before extending.
