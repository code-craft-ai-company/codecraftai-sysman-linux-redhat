# Implementation Plan — Firewall Management

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `firewallmanagement` module

- [x] 2. Define `FirewallActionStatus` enum (SUCCESS / FAILED / SKIPPED
  / DRY_RUN)

- [x] 3. Define `FirewallManagerConfig` dataclass (dry_run, elevated)

- [x] 4. Define `FirewallActionResult` dataclass

- [x] 5. Define `ZoneInfo` + `FirewallState` query dataclasses

## FirewallManager — Implemented

- [x] 6. Implement `__init__` accepting runner + optional config

- [x] 7. Implement `add_service(service, zone)` / `remove_service`;
  both idempotent via zone query, permanent, with `--zone=<zone>
  --add/--remove-service=<service>`

- [x] 8. Implement `add_port(port, protocol, zone)` /
  `remove_port(port, protocol, zone)`; both permanent, idempotent

- [x] 9. Implement `add_rich_rule(rule, zone)` /
  `remove_rich_rule(rule, zone)`

- [x] 10. Implement `set_default_zone(zone)` — SKIPPED when already
  default

- [x] 11. Implement `reload()` — `firewall-cmd --reload`

- [x] 12. Implement `query_state()` — returns `FirewallState`
  (running + default zone)

- [x] 13. Implement `query_zone(zone)` / `query_all_zones()` — parse
  `firewall-cmd --list-all[-zones]` output into `ZoneInfo`

- [x] 14. Implement `_dispatch` helper — dry-run short-circuit and
  runner-result mapping

## FirewallManager — Gaps (specified but not implemented)

- [ ] 15. Automatic `reload()` after permanent changes (or an explicit
  flag) — currently callers must invoke `reload` separately for
  `--permanent` changes to take effect in the runtime config

- [ ] 16. Runtime-only changes (omit `--permanent`) as a separate code
  path for short-lived rule overrides

- [ ] 17. `add_forward_port` / `remove_forward_port` helpers

- [ ] 18. Zone creation/removal (`--new-zone=` / `--delete-zone=`)

- [ ] 19. Source/ipset management within a zone

## Tests — Implemented

- [x] 20. `test_query_state_reports_running_and_default`
- [x] 21. `test_query_zone_parses_list_all`
- [x] 22. `test_add_service_skipped_when_present`
- [x] 23. `test_add_service_runs_firewall_cmd`
- [x] 24. `test_add_port_runs_firewall_cmd`
- [x] 25. `test_set_default_zone_skipped_when_same`
- [x] 26. `test_reload_dispatches_command`

## Tests — Gaps

- [ ] 27. Test `remove_service` / `remove_port` / `remove_rich_rule`
  dispatch + idempotency

- [ ] 28. Test `add_rich_rule` dispatch shape

- [ ] 29. Test `query_all_zones` parser — multi-zone output

- [ ] 30. Test dry-run short-circuits for every mutating method

## Follow-ups

- [ ] 31. **automojo-* reuse check**: `3-cockpit-management` also
  spells out `firewall-cmd` directly — consider having it delegate to
  `FirewallManager.add_service("cockpit", zone)` to avoid drift
  between the two features.  Also check whether any sibling
  `automojo-*` package offers a firewall-facing helper already.
