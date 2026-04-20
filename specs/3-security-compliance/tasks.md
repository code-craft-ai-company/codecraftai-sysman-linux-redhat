# Implementation Plan — Security Compliance

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

This feature is the coarse-grained security-posture facade; fine-
grained SELinux work lives in `3-selinux-management` and full SSH
config management lives in `3-ssh-server-configuration`.

## Module and Models

- [x] 1. Create the `securitycompliance` module

- [x] 2. Define `SELinuxMode` enum (ENFORCING / PERMISSIVE /
  DISABLED)

- [x] 3. Define `SecurityComplianceConfig` (dry_run, elevated)

- [x] 4. Define query/result dataclasses: `SELinuxStatus`,
  `FilePermissionCheck`, `FilePermissionResult`, `SSHConfigFinding`,
  `ComplianceActionResult`

## SecurityComplianceManager — Implemented

- [x] 5. Implement `__init__` accepting runner + optional config

- [x] 6. Implement `check_selinux()` — `getenforce` + `sestatus`
  parsed into `SELinuxStatus`

- [x] 7. Implement `set_selinux_mode(mode, persistent=False)` —
  `setenforce 0|1` for runtime; optionally edit
  `/etc/selinux/config` via `tee` when `persistent=True`

- [x] 8. Implement `check_file_permissions(checks)` — `stat`-based
  mode/owner/group query; returns list of
  `FilePermissionResult` flagging mismatches

- [x] 9. Implement `remediate_file_permissions(checks)` — `chmod` +
  `chown` for each mismatched check

- [x] 10. Implement `check_ssh_config(baseline=None)` — read
  `/etc/ssh/sshd_config`, parse via `_parse_ssh_config`, compare to
  `_default_ssh_baseline()` or supplied baseline; returns
  `list[SSHConfigFinding]`

- [x] 11. Implement `apply_ssh_hardening(settings=None)` —
  `_apply_ssh_settings` updates sshd_config, then restarts
  `sshd.service`

- [x] 12. Implement `find_world_writable_files(root)` — `find`
  with `-perm -0002` parsed via `_split_lines`

- [x] 13. Implement `find_suid_sgid_files(root)` — `find` with
  `-perm -u+s,g+s`

## SecurityComplianceManager — Gaps (specified but not implemented)

- [ ] 14. `run_compliance_suite()` / pluggable check registry that
  produces a structured `ComplianceReport` aggregating findings
  across SELinux, permissions, SSH, and SUID/SGID discovery (the
  overview describes this but the runner is not present)

- [ ] 15. `verify_pam_config()` / `verify_password_quality` —
  `/etc/pam.d/*` policy inspection

- [ ] 16. `firewall_posture_check()` — delegate to
  `3-firewall-management` and compare against a baseline of
  expected zones/services

- [ ] 17. `audit_posture_check()` — delegate to
  `3-audit-management` and compare the active rule set against a
  baseline

- [ ] 18. Severity classification on findings (LOW / MEDIUM / HIGH
  / CRITICAL)

## Tests — Implemented

- [x] 19. `test_check_selinux_reports_enforcing`
- [x] 20. `test_check_file_permissions_detects_mismatch`
- [x] 21. `test_remediate_file_permissions_runs_chmod_and_chown`
- [x] 22. `test_check_ssh_config_flags_missing_setting`
- [x] 23. `test_apply_ssh_hardening_writes_and_restarts`
- [x] 24. `test_find_world_writable_files_parses_output`

## Tests — Gaps

- [ ] 25. Test `set_selinux_mode(..., persistent=True)` writes
  `/etc/selinux/config`

- [ ] 26. Test `find_suid_sgid_files` parser

- [ ] 27. Test dry-run short-circuits for `set_selinux_mode`,
  `remediate_file_permissions`, `apply_ssh_hardening`

- [ ] 28. Test `check_file_permissions` with passing (no-mismatch)
  checks

## Follow-ups

- [ ] 29. **automojo-* reuse check**: `3-selinux-management`,
  `3-ssh-server-configuration`, `3-firewall-management`, and
  `3-audit-management` cover the fine-grained control surfaces.
  When `run_compliance_suite` is built (task 14) it should compose
  them rather than re-implementing the underlying commands here.
