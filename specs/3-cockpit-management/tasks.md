# Implementation Plan — Cockpit Management

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `cockpitmanagement` module
  - `source/packages/ccai/sysman/linux/redhat/cockpitmanagement.py`
    exists

- [x] 2. Define `CockpitActionStatus` enum (SUCCESS / FAILED / SKIPPED /
  DRY_RUN)

- [x] 3. Define `CockpitManagerConfig` dataclass
  - Fields: `dry_run`, `elevated`

- [x] 4. Define `CockpitActionResult` dataclass
  - Fields: `status`, `detail`, `exit_code`, `stdout`, `stderr`, `command`

## CockpitManager — Implemented

- [x] 5. Implement `__init__` accepting runner + optional config

- [x] 6. Implement `enable()` — `systemctl enable --now cockpit.socket`

- [x] 7. Implement `disable()` — `systemctl disable --now cockpit.socket`

- [x] 8. Implement `is_active()` — `systemctl is-active cockpit.socket`;
  returns True iff stdout stripped equals `"active"`

- [x] 9. Implement `open_firewall(zone="public")` —
  `firewall-cmd --zone=<zone> --add-service=cockpit --permanent`

- [x] 10. Implement `close_firewall(zone="public")` —
  `firewall-cmd --zone=<zone> --remove-service=cockpit --permanent`

- [x] 11. Implement `_dispatch` helper — dry-run short-circuit and
  runner-result-to-CockpitActionResult mapping

## CockpitManager — Gaps (specified but not implemented)

- [ ] 12. Configure listening port via
  `/etc/systemd/system/cockpit.socket.d/listen.conf` drop-in
  (ListenStream=)

- [ ] 13. Read/write main config file
  `/etc/cockpit/cockpit.conf` (INI format)

- [ ] 14. Query + manage the installed Cockpit module set (sub-RPMs
  like `cockpit-storaged`, `cockpit-podman`, etc.) — delegated to
  `3-package-management` for the RPM side, but add a Cockpit-scoped
  discovery helper

- [ ] 15. `cockpit-bridge --version` and `cockpit.socket` status query
  helpers returning structured objects

- [ ] 16. `firewall-cmd --reload` call after permanent zone changes so
  the rule takes effect in the runtime configuration (currently
  `open_firewall` writes permanent config without reloading)

- [ ] 17. Idempotency: SKIPPED when `cockpit.socket` is already
  enabled/disabled before issuing the systemctl command

## Tests — Implemented

- [x] 18. `test_enable_dispatches_systemctl_enable`

- [x] 19. `test_is_active_checks_systemctl`

- [x] 20. `test_open_firewall_dispatches_firewall_cmd`

## Tests — Gaps

- [ ] 21. Test `disable()` dispatch shape

- [ ] 22. Test `close_firewall()` dispatch shape and non-default `zone`

- [ ] 23. Test `is_active()` returns False for any non-"active" stdout
  (e.g. "inactive", "failed", empty)

- [ ] 24. Test dry-run short-circuits for every mutating method

- [ ] 25. Test failure mapping — runner non-zero exit → status FAILED
  with exit_code/stderr preserved

## Follow-ups

- [ ] 26. **codecraftai-* reuse check**: evaluate whether firewall ops
  should delegate to `3-firewall-management`'s `FirewallManager` rather
  than spelling out `firewall-cmd` here — reduces drift between the two
  features.
