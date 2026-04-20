# Implementation Plan — Identity Management

All tasks are unchecked — this is a new feature with no existing
implementation.

## Module and Models

- [ ] 1. Create `source/packages/automojo/sysman/linux/redhat/identitymanagement.py`
  - _Requirements: All_

- [ ] 2. Define `IdentityActionStatus` enum (SUCCESS / FAILED /
  SKIPPED / DRY_RUN)
  - _Requirements: 8_

- [ ] 3. Define `IdentityManagerConfig` dataclass
  - Fields: `dry_run`, `elevated` (default True),
    `sssd_conf_path`, `sssd_dropin_dir`, `krb5_conf_path`,
    `krb5_dropin_dir`, `default_keytab`

- [ ] 4. Define `IdentityActionResult` dataclass

- [ ] 5. Define `RealmDiscovery`, `RealmMembership`,
  `SssdConfigCheckResult`, `SssdStatus`, `KrbClientConfig`,
  `KrbTicket` dataclasses
  - _Requirements: 1, 3, 4, 5, 6_

- [ ] 6. Define `IdentityToolNotAvailableError` and
  `IdentityCredentialError` as `SemanticError` subtypes
  - _Requirements: 1.4, 8.5_

## Constructor and Helpers

- [ ] 7. Implement `__init__` accepting `runner`, optional
  `config`, optional `service_manager` (from `3-service-management`),
  and optional `authentication_manager` (from
  `3-authentication-management`); default the two sibling managers
  to thin local wrappers only used when the injected ones are `None`
  - _Requirements: 4.1, 7_

- [ ] 8. Implement `_read_file(path)` — `cat` via runner, return
  empty on non-zero exit

- [ ] 9. Implement `_write_file(path, content, mode=0o600)` —
  `tee` via runner with `stdin`; chmod after the write; dry-run
  short-circuit

- [ ] 10. Implement `_dispatch(command, detail, stdin=None)` —
  runner call + dry-run short-circuit + result mapping

## realm / adcli

- [ ] 11. Implement `discover(domain)` — `realm discover <domain>`
  parsed into `RealmDiscovery`; raise
  `IdentityToolNotAvailableError` when `realm` is not installed
  - _Requirements: 1.1, 1.4_

- [ ] 12. Implement `list_realms()` — `realm list` parsed into
  `list[RealmMembership]`; empty list on no-realms-joined
  - _Requirements: 1.2, 1.3_

- [ ] 13. Implement `join(domain, credential=None,
  one_time_password=None, user=None)` — password / OTP via `stdin`;
  SKIPPED when already joined; argv MUST NEVER contain the password
  - _Requirements: 2.1, 2.2, 2.4, 2.5, 8.3_

- [ ] 14. Implement `leave(domain=None)` — `realm leave`; SKIPPED
  when not joined
  - _Requirements: 2.3_

## SSSD Config

- [ ] 15. Implement `read_sssd_conf()` — parse into `dict[str,
  dict[str, str]]`; returns empty dict when file absent
  - _Requirements: 3_

- [ ] 16. Implement `write_sssd_dropin(name, sections)` — write
  `/etc/sssd/conf.d/<name>.conf` with mode 0600, owner root:root
  - _Requirements: 3.1_

- [ ] 17. Implement `set_sssd_directive(section, key, value,
  path=None)` — upsert preserving comments; SKIPPED when unchanged
  - _Requirements: 3.3_

- [ ] 18. Implement `remove_sssd_directive(section, key,
  path=None)` — strip; SKIPPED when absent
  - _Requirements: 3.4_

- [ ] 19. Implement `config_check()` — `sssctl config-check` →
  `SssdConfigCheckResult`
  - _Requirements: 3.5_

## SSSD Runtime

- [ ] 20. Implement `restart_sssd()` — delegate to injected
  `ServiceManager.restart("sssd")`
  - _Requirements: 4.1_

- [ ] 21. Implement `invalidate_cache(user=None, group=None)` —
  `sss_cache -E` / `-u <user>` / `-g <group>`
  - _Requirements: 4.2_

- [ ] 22. Implement `domain_status(domain)` — `sssctl
  domain-status <domain>` → `SssdStatus`
  - _Requirements: 4.3_

## Kerberos Config

- [ ] 23. Implement `read_krb5_conf()` — merge `/etc/krb5.conf` +
  drop-ins with later-overrides-earlier semantics; return
  `KrbClientConfig`
  - _Requirements: 5_

- [ ] 24. Implement `set_default_realm(realm)` — rewrite only
  `[libdefaults].default_realm` in `/etc/krb5.conf`
  - _Requirements: 5.2_

- [ ] 25. Implement `add_kdc(realm, host)` / `remove_kdc(realm,
  host)` — upsert / strip `kdc = <host>` line within the realm
  block; SKIPPED idempotency
  - _Requirements: 5.3_

## Kerberos Tickets

- [ ] 26. Implement `kinit(principal, credential=None,
  keytab=None)` — password-via-stdin or `-kt <keytab>`; raise
  `IdentityCredentialError` when neither is supplied
  - _Requirements: 6.1, 8.3, 8.5_

- [ ] 27. Implement `klist()` — parse `klist` into `list[KrbTicket]`;
  empty list when no cache exists
  - _Requirements: 6.2, 6.3_

- [ ] 28. Implement `kdestroy(all_caches=False)` — `kdestroy [-A]`
  - _Requirements: 6.4_

## NSS Seam

- [ ] 29. Implement `ensure_nss_ordering(databases)` — delegate to
  injected `AuthenticationManager.set_nss_sources` for each
  database; aggregate the returned `AuthActionResult` records into
  a single `IdentityActionResult`
  - _Requirements: 7_

## Tests

- [ ] 30. Create `test_identitymanagement.py` with `_MockRunner`,
  `_MockServiceManager`, and `_MockAuthenticationManager` stubs

- [ ] 31. Tests for `discover`, `list_realms` parsers including
  the no-realms and `realm-not-installed` paths

- [ ] 32. Tests for `join`:
  - Password path: assert stdin carries the password; assert argv
    does NOT contain the password
  - OTP path: assert stdin carries the OTP
  - SKIPPED when already joined
  - DRY_RUN short-circuit
  - FAILED mapping

- [ ] 33. Tests for `leave` incl. SKIPPED when not joined

- [ ] 34. Tests for `read_sssd_conf` parser and `write_sssd_dropin`
  mode-and-owner path

- [ ] 35. Tests for `set_sssd_directive` (happy, SKIPPED,
  cross-section preservation) and `remove_sssd_directive`

- [ ] 36. Tests for `config_check` result mapping

- [ ] 37. Tests for `restart_sssd` delegation to ServiceManager
  mock

- [ ] 38. Tests for `invalidate_cache` dispatch variants

- [ ] 39. Tests for `read_krb5_conf` merge with drop-ins,
  `set_default_realm`, `add_kdc` / `remove_kdc`

- [ ] 40. Tests for `kinit` password / keytab / missing-credential
  paths; assert password never on argv

- [ ] 41. Tests for `klist` parser and empty-cache handling

- [ ] 42. Tests for `kdestroy` with and without `-A`

- [ ] 43. Tests for `ensure_nss_ordering` delegation to the
  `AuthenticationManager` mock

- [ ] 44. Dry-run short-circuit sweep across every mutating method

- [ ] 45. Failure-mapping sweep — runner non-zero → FAILED with
  exit_code/stderr preserved

## Follow-ups

- [ ] 46. **automojo-* reuse check**: route the credential
  parameter of `join` / `kinit` through
  `automojo.credentials.sshcredential.SshCredential` (or a similar
  credential abstraction) rather than accepting a plain password
  string.
