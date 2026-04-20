# Implementation Plan — Audit Management

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `auditmanagement` module
  - `source/packages/automojo/sysman/linux/redhat/auditmanagement.py` exists
  - _Requirements: All_

- [x] 2. Define `AuditActionStatus` enum (SUCCESS / FAILED / SKIPPED /
  DRY_RUN)

- [x] 3. Define `AuditManagerConfig` dataclass
  - Fields: `dry_run: bool = False`, `elevated: bool = False`,
    `rules_dir: str = "/etc/audit/rules.d"`

- [x] 4. Define `AuditActionResult` dataclass
  - Fields: `status`, `detail`, `exit_code`, `stdout`, `stderr`, `command`

- [x] 5. Define `AuditReport` dataclass
  - Fields: `lines`, `command`

## AuditManager — Implemented

- [x] 6. Implement `AuditManager.__init__` accepting runner + optional
  config
  - _Requirements: 1_

- [x] 7. Implement `list_rules()` via `auditctl -l`; returns stripped
  non-empty lines; empty list on non-zero exit
  - _Requirements: 3_

- [x] 8. Implement `add_rule(rule)` via `auditctl <rule-tokens>` with
  dry-run short-circuit and status mapping
  - _Requirements: 4_

- [x] 9. Implement `delete_rule(rule)` — parses tokens, swaps `-a` for
  `-d` when present
  - _Requirements: 5_

- [x] 10. Implement `write_rules_file(name, rules)`
  - Idempotent: reads current file via runner `cat`; returns `SKIPPED`
    when content matches
  - Writes via `tee <path>` with runner `stdin`
  - Dry-run short-circuits before invocation
  - _Requirements: (persist rules drop-in)_

- [x] 11. Implement `search(key, message_type, start)` via `ausearch`

- [x] 12. Implement `summary()` via `aureport --summary`

## AuditManager — Gaps (specified but not implemented)

- [ ] 13. Implement `get_service_state()` — `systemctl is-active auditd`
  + `systemctl is-enabled auditd` returning `AuditServiceState`
  - _Requirements: 1_

- [ ] 14. Implement `start()` / `stop()` / `enable()` / `disable()` with
  SKIPPED when already in desired state.  Note: `auditd.service` is
  protected against `systemctl restart` — use `service auditd restart`
  for restart.
  - _Requirements: 2_

- [ ] 15. Implement immutable-mode detection (`auditctl -s` → `enabled 2`)
  so `add_rule` / `delete_rule` return `AuditImmutableError` without
  attempting the mutation
  - _Requirements: 4.2_

- [ ] 16. Implement duplicate-detection for `add_rule` (SKIPPED when rule
  already active)
  - _Requirements: 4.3_

- [ ] 17. Implement rule-parsing in `list_rules` returning structured
  `AuditRule` records (filter list, action, key)
  - _Requirements: 3.2_

- [ ] 18. Implement `augenrules --load` wrapper for loading drop-in rules

- [ ] 19. Implement `/etc/audit/auditd.conf` read/write helpers

## Tests — Implemented

- [x] 20. `test_list_rules_returns_auditctl_output`
  - _Requirements: 3.1_

- [x] 21. `test_add_rule_dispatches_auditctl` — verifies token splitting
  and args passed
  - _Requirements: 4.1_

- [x] 22. `test_write_rules_file_writes_via_tee` — verifies idempotency
  check and stdin delivery
  - _Requirements: (persist rules drop-in)_

- [x] 23. `test_search_returns_lines`
  - _Requirements: (search audit log)_

## Tests — Gaps

- [ ] 24. Test `delete_rule` — verify `-a` → `-d` token swap

- [ ] 25. Test `summary()` — `aureport --summary` dispatch and lines
  capture

- [ ] 26. Test dry-run short-circuits for `add_rule`, `delete_rule`,
  `write_rules_file`

- [ ] 27. Test `write_rules_file` idempotency — SKIPPED when content
  already matches

- [ ] 28. Tests for all service-state / lifecycle methods once
  implemented (tasks 13–14)

- [ ] 29. Test `list_rules` empty-rule and non-zero-exit paths

## Follow-ups

- [ ] 30. **automojo-* reuse check**: file reads currently route `cat` /
  `tee` through the command runner.  Evaluate whether
  `automojo-interfaces`' file-operator abstraction (if any) or another
  sibling package provides a higher-level file API that works over
  both local and SSH runners, and migrate if available.
