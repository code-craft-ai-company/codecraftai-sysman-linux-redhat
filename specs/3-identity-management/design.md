# Design

## Overview

The identity management feature is implemented as a single module,
`ccai.sysman.linux.redhat.identitymanagement`, exposing an
`IdentityManager` class that wraps `realm`, `adcli`, `sssctl`,
`kinit`, `klist`, `kdestroy`, and `sss_cache` through the
`ICommandRunner` abstraction.  It also owns the narrow edits to
`/etc/sssd/sssd.conf`, `/etc/sssd/conf.d/*`, `/etc/krb5.conf`, and
`/etc/krb5.conf.d/*` needed to configure the client stack.

All file reads route through the runner via `cat`; all file writes
route through the runner via `tee` with `stdin`.  Credentials flow
through runner `stdin`, never argv.

**See Also:**
- `3-authentication-management` for PAM, pwquality, faillock,
  limits.d, and the canonical `set_nss_sources` implementation.
- `3-user-group-management` for local-account operations.
- `3-certificate-trust-management` for the enterprise-CA anchors
  required by LDAPS / Kerberos-over-TLS setups.
- `3-service-management` for `sssd.service` lifecycle.
- `codecraftai-credentials` for password / keytab handling.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│            Maintenance Coordinator                          │
│  - Declares directory posture                               │
│  - Supplies SshCredential-style credential                  │
│  - Optional: declarative sssd.conf / krb5.conf drop-ins     │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            IdentityManager                                  │
│                                                             │
│  realm / adcli:                                             │
│    discover(), list_realms(),                               │
│    join(), leave()                                          │
│  SSSD config:                                               │
│    read_sssd_conf(), write_sssd_dropin(),                   │
│    set_sssd_directive(), remove_sssd_directive(),           │
│    config_check()                                           │
│  SSSD runtime:                                              │
│    restart_sssd(),  (delegates to ServiceManager)           │
│    invalidate_cache(), domain_status()                      │
│  Kerberos config:                                           │
│    read_krb5_conf(), set_default_realm(),                   │
│    add_kdc(), remove_kdc()                                  │
│  Kerberos tickets:                                          │
│    kinit(), klist(), kdestroy()                             │
│  NSS ordering seam:                                         │
│    ensure_nss_ordering()  (delegates to                     │
│                            AuthenticationManager)           │
└────────────────────────┬────────────────────────────────────┘
                         │ delegates to
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                   ICommandRunner                            │
│  run(args, elevated, stdin, ...) → RunResult                │
└─────────────────────────────────────────────────────────────┘
```

Two sibling managers are referenced:

- `ServiceManager` from `3-service-management` — used by
  `restart_sssd`.
- `AuthenticationManager` from `3-authentication-management` —
  used by `ensure_nss_ordering`.

Both are injected in `__init__` to keep the wiring explicit and
testable.

---

## Components

### `IdentityManager`

**Location:** `source/packages/ccai/sysman/linux/redhat/identitymanagement.py`

| Method | Description |
|---|---|
| `discover(domain)` | `realm discover <domain>` → `RealmDiscovery` |
| `list_realms()` | `realm list` → `list[RealmMembership]` |
| `join(domain, credential, ...)` | `realm join <domain>` with password via `stdin`; SKIPPED when already joined |
| `leave(domain=None)` | `realm leave`; SKIPPED when not joined |
| `read_sssd_conf()` | `cat /etc/sssd/sssd.conf` parsed to `dict[str, dict[str, str]]` |
| `write_sssd_dropin(name, sections)` | Write `/etc/sssd/conf.d/<name>.conf` atomically; mode 0600 |
| `set_sssd_directive(section, key, value, path=None)` | Upsert a directive in a file under `/etc/sssd/` |
| `remove_sssd_directive(section, key, path=None)` | Strip a directive; SKIPPED when absent |
| `config_check()` | `sssctl config-check` → `SssdConfigCheckResult` |
| `restart_sssd()` | Delegate to injected `ServiceManager.restart("sssd")` |
| `invalidate_cache(user=None, group=None)` | `sss_cache -E` / `-u` / `-g` |
| `domain_status(domain)` | `sssctl domain-status <domain>` parsed into `SssdStatus` |
| `read_krb5_conf()` | Parse `/etc/krb5.conf` into `KrbClientConfig` |
| `set_default_realm(realm)` | Rewrite `[libdefaults].default_realm` only |
| `add_kdc(realm, host)` / `remove_kdc(realm, host)` | Manage KDC list for a realm |
| `kinit(principal, credential=None, keytab=None)` | Acquire ticket via password-on-stdin or keytab |
| `klist()` | `klist` → `list[KrbTicket]` |
| `kdestroy(all_caches=False)` | `kdestroy [-A]` |
| `ensure_nss_ordering(databases)` | Delegate to `AuthenticationManager.set_nss_sources` |

---

## Configuration

```python
@dataclass
class IdentityManagerConfig:
    dry_run: bool = False
    elevated: bool = True
    sssd_conf_path: str = "/etc/sssd/sssd.conf"
    sssd_dropin_dir: str = "/etc/sssd/conf.d"
    krb5_conf_path: str = "/etc/krb5.conf"
    krb5_dropin_dir: str = "/etc/krb5.conf.d"
    default_keytab: str = "/etc/krb5.keytab"
```

---

## Data Models

```python
class IdentityActionStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class IdentityActionResult:
    status: IdentityActionStatus
    detail: str | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    command: list[str] | None = None


@dataclass
class RealmDiscovery:
    domain: str
    realm_type: str              # 'kerberos', 'kerberos-member', 'ipa', ...
    realm_name: str | None
    domain_controllers: list[str]
    raw: str


@dataclass
class RealmMembership:
    domain: str
    realm_name: str | None
    configured: bool
    server_software: str | None
    client_software: str | None
    required_package: str | None


@dataclass
class SssdConfigCheckResult:
    ok: bool
    exit_code: int
    stderr: str
    warnings: list[str]


@dataclass
class SssdStatus:
    domain: str
    online: bool
    last_request: str | None
    raw: str


@dataclass
class KrbClientConfig:
    default_realm: str | None
    realms: dict[str, list[str]]        # realm -> list of KDC host strings
    domain_realm: dict[str, str]        # dns suffix -> realm


@dataclass
class KrbTicket:
    principal: str
    service: str                        # 'krbtgt/REALM@REALM', etc.
    starts: str                         # keep ISO-ish string; parsing left to caller
    expires: str
    renewable_until: str | None
```

---

## Key Method Designs

### `join(domain, credential, one_time_password=None, ...)`

1. Call `list_realms()`.  If any membership has `domain == domain`
   and `configured=True` → return `SKIPPED`.
2. If `dry_run=True` → return `DRY_RUN`.
3. Build args:
   `["realm", "join", domain, "-U", credential.username]` (or
   `["realm", "join", domain, "--one-time-password", ...]` when an
   OTP is supplied — OTP SHALL still ride on `stdin`, not argv).
4. Password / OTP is supplied via `stdin=credential.password` or
   `stdin=one_time_password`.  Argv NEVER contains the password.
5. Dispatch with `elevated=True`.
6. Map `RunResult` to `IdentityActionResult`.

### `kinit(principal, credential=None, keytab=None)`

1. If `keytab` is provided → `["kinit", "-kt", keytab, principal]`
   with no stdin.
2. Else if `credential` is provided → `["kinit", principal]` with
   `stdin=credential.password`.
3. Else raise `IdentityCredentialError(principal=<principal>)`.
4. Dry-run: return `DRY_RUN` without dispatch.
5. Dispatch; map result.

### `set_sssd_directive(section, key, value, path=None)`

1. `path` defaults to `sssd_conf_path`.
2. Read current content via `_read_file`.
3. Parse into `dict[str, dict[str, str]]`.
4. If `parsed[section][key] == value` → SKIPPED.
5. Rewrite file: preserve section order, blank lines, and comments.
   Upsert the `(section, key)` line; insert a new section at EOF if
   necessary.
6. Write via `tee` with `stdin`; ensure mode 0600, owner `root:root`
   after the write.

### `read_krb5_conf()`

1. Read `/etc/krb5.conf` + every file under `/etc/krb5.conf.d/`.
2. Merge using krb5's actual precedence (later drop-ins override
   earlier ones).
3. Extract `default_realm` from `[libdefaults]`, the realm→KDC map
   from `[realms]`, and the DNS suffix→realm map from
   `[domain_realm]`.

---

## Dry-Run Behaviour

- `dry_run=True` disables: `join`, `leave`, every `write_*` /
  `set_*` / `remove_*`, `kinit`, `kdestroy`, `invalidate_cache`,
  `restart_sssd`, `config_check`.
- `dry_run=True` allows: `discover`, `list_realms`, `read_*`,
  `klist`, `domain_status`.

---

## Correctness Properties

1. **No plaintext password on argv.** Every code path that accepts
   a password routes it through `stdin` on the runner call.
2. **Credential-required-but-missing fails fast.** Methods that
   need a credential raise `IdentityCredentialError` rather than
   shelling out with an empty password.
3. **Idempotent join / leave.** `join` SKIPPED when already joined;
   `leave` SKIPPED when not joined.
4. **Dry-run blocks remote contact.** No `realm`, `kinit`, or
   `sssctl` command that would reach a KDC or DC runs when
   `dry_run=True`.
5. **Seam with auth-management.** `set_nss_sources` is not called
   directly — every nsswitch write goes through
   `ensure_nss_ordering` which delegates.

---

## Testing Strategy

- `_MockRunner` following the existing pattern.
- `_MockServiceManager` exposing a `restart(name) -> bool` capturer.
- `_MockAuthenticationManager` exposing `set_nss_sources(db,
  sources) -> AuthActionResult`.
- Unit tests for every public method: happy-path, SKIPPED, FAILED,
  DRY_RUN.
- Explicit test that `join(...)` never places the password on argv
  (assert via the mock's recorded call args).
- Parser tests for `realm discover`, `realm list`, `klist`, and
  `krb5.conf`.
- Dry-run negative tests: no mutating runner call for `join`,
  `leave`, `kinit`, `set_default_realm`.
- No live KDC / DC / SSSD service required.

---

## Module Layout

```
source/packages/ccai/sysman/linux/redhat/
└── identitymanagement.py

source/testroots/testbase/
└── ccai/tests/sysman/linux/redhat/
    └── test_identitymanagement.py
```

---

## Seams With Other Features

| Seam | Owner | This Feature |
|---|---|---|
| NSS source order | `3-authentication-management` | `ensure_nss_ordering` delegates |
| SSSD service lifecycle | `3-service-management` | `restart_sssd` delegates |
| Per-user Kerberos tickets after local account creation | `3-user-group-management` | This feature exposes `kinit`; coordinator composes |
| Enterprise CA trust for LDAPS / Kerberos-over-TLS | `3-certificate-trust-management` | Coordinator installs the anchor first, then this feature joins |
| Credential objects | `codecraftai-credentials` | `join`, `kinit` accept credential objects; passwords never on argv |
