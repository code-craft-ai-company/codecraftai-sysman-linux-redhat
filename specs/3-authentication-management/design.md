# Design

## Overview

The authentication management feature is implemented as a single
module, `automojo.sysman.linux.redhat.authenticationmanagement`,
exposing an `AuthenticationManager` class that wraps `authselect`,
direct PAM drop-ins, `pam_pwquality.conf`,
`pam_faillock.conf`/`faillock`, `/etc/security/limits.d/*`, and
`/etc/nsswitch.conf` through the `ICommandRunner` abstraction.

All file reads route through the runner via `cat`; all file writes
route through the runner via `tee` with `stdin`.  No direct
filesystem access occurs inside this module.

**See Also:**
- `3-user-group-management` for account creation, password expiry,
  and SSH-key management.
- `3-ssh-server-configuration` for sshd-level PAM configuration.
- `3-identity-management` for SSSD / realmd / Kerberos integration.
- `3-security-compliance` for PAM-hardening compliance checks.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│            Maintenance Coordinator                          │
│  - Declares authselect profile + features                   │
│  - Sets pwquality / faillock policy                         │
│  - Writes drop-ins under /etc/pam.d/*.d/ when needed        │
│  - Sets limits.d drop-ins                                   │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            AuthenticationManager                            │
│  automojo.sysman.linux.redhat.authenticationmanagement     │
│                                                             │
│  authselect:                                                │
│    current_profile(), list_profiles(), select_profile()    │
│    enable_feature(), disable_feature(), apply_changes()    │
│  pam.d drop-ins:                                            │
│    read_service(), write_service(), write_dropin()         │
│  pwquality:                                                 │
│    get_pwquality(), set_pwquality_directive()              │
│    remove_pwquality_directive()                             │
│  faillock:                                                  │
│    get_faillock_state(), reset_faillock()                  │
│    set_faillock_directive(), remove_faillock_directive()   │
│  limits.d:                                                  │
│    get_limits(), set_limit(), remove_limit()               │
│  nsswitch:                                                  │
│    get_nss_sources(), set_nss_sources()                    │
└────────────────────────┬────────────────────────────────────┘
                         │ delegates to
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                   ICommandRunner                            │
│  run(args, elevated, stdin, ...) → RunResult                │
└─────────────────────────────────────────────────────────────┘
```

---

## Components

### `AuthenticationManager`

**Location:** `source/packages/automojo/sysman/linux/redhat/authenticationmanagement.py`

| Method | Description |
|---|---|
| `current_profile()` | Parse `authselect current` into `AuthselectProfile` |
| `list_profiles()` | Parse `authselect list` into `list[str]` |
| `select_profile(profile, features)` | `authselect select <profile> [features...] [--force]`; SKIPPED when already matching |
| `enable_feature(feature)` / `disable_feature(feature)` | Toggle; SKIPPED when already in requested state |
| `apply_changes()` | `authselect apply-changes` to persist pending edits |
| `read_service(service)` | `cat /etc/pam.d/<service>` → raw text |
| `write_service(service, content, allow_overwrite_authselect=False)` | Idempotent write of `/etc/pam.d/<service>`; aborts when the existing file begins with the authselect marker unless explicitly overridden |
| `write_dropin(service, name, content)` | Write `/etc/pam.d/<service>.d/<name>` (RHEL 9+ drop-in location) |
| `get_pwquality()` / `set_pwquality_directive(key, value)` / `remove_pwquality_directive(key)` | Manage `/etc/security/pwquality.conf` |
| `get_faillock_state(user)` / `reset_faillock(user)` | Per-user query + reset |
| `set_faillock_directive(key, value)` / `remove_faillock_directive(key)` | Manage `/etc/security/faillock.conf` |
| `get_limits()` / `set_limit(entry, drop_in)` / `remove_limit(entry, drop_in)` | Manage `/etc/security/limits.d/<drop_in>.conf` |
| `get_nss_sources()` / `set_nss_sources(db, sources)` | Manage `/etc/nsswitch.conf` |

---

## Configuration

```python
@dataclass
class AuthenticationManagerConfig:
    dry_run: bool = False
    elevated: bool = True             # authselect + pam almost always need root
    faillock_conf: str = "/etc/security/faillock.conf"
    pwquality_conf: str = "/etc/security/pwquality.conf"
    limits_conf: str = "/etc/security/limits.conf"
    limits_dir:   str = "/etc/security/limits.d"
    nsswitch_conf: str = "/etc/nsswitch.conf"
```

---

## Data Models

```python
class AuthActionStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class AuthActionResult:
    status: AuthActionStatus
    detail: str | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    command: list[str] | None = None


@dataclass
class AuthselectProfile:
    id: str
    features: list[str]
    raw: str                    # full `authselect current` output for reporting


@dataclass
class FaillockState:
    user: str
    recent_failures: int
    locked: bool


@dataclass
class LimitEntry:
    domain: str                 # '@group', 'username', '*'
    limit_type: str             # 'soft' | 'hard' | '-'
    item: str                   # 'nofile', 'nproc', 'stack', 'memlock', ...
    value: str
    source_path: str            # '/etc/security/limits.conf' or '.../limits.d/foo.conf'
```

---

## Key Method Designs

### `select_profile(profile, features=None)`

1. Call `current_profile()`.
2. If returned `id == profile` and every requested feature is in
   `features` → return `AuthActionResult(status=SKIPPED)`.
3. Dry-run: return `DRY_RUN`.
4. Build `["authselect", "select", profile, *features, "--force"]`
   (the `--force` is required when switching from a hand-edited
   `/etc/pam.d/`).
5. Dispatch; map `RunResult` to `AuthActionResult`.

### `write_service(service, content, ...)`

1. Read current content via `_read_file(path="/etc/pam.d/<service>")`.
2. If current content matches desired → SKIPPED.
3. If current content begins with the authselect marker and
   `allow_overwrite_authselect=False` → raise
   `AuthselectOverrideError` (a new `SemanticError` subtype) —
   defensive against accidentally breaking the authselect contract.
4. Dry-run: return `DRY_RUN`.
5. Write via `["tee", path]` with `stdin=content`, elevated.

### `set_pwquality_directive(key, value)`

1. Read current file via `cat`.
2. Parse into a `dict[str, str]`.
3. If `dict[key] == value` → SKIPPED.
4. Rebuild file: upsert line `<key> = <value>` preserving comments
   and line order.
5. Write via `tee`, elevated.

### `get_faillock_state(user)`

1. Dispatch `faillock --user <user>` via runner.
2. Parse output:
   - Header line confirming user.
   - Zero or more `When` / `Type` / `Source` / `Valid` rows.
   - The *presence* of any row whose `Valid` column is `V` (valid
     failed attempt) counts toward the lockout; the kernel side
     compares against the `deny` threshold from `faillock.conf`.
3. Compute `recent_failures = count(row.valid == "V")`.
4. Compute `locked = recent_failures >= deny_threshold` where
   `deny_threshold` is the current `deny` value from
   `get_faillock()` (cached per-call).
5. Return `FaillockState`.

---

## Dry-Run Behaviour

Every mutating entry point follows the same pattern:

- Query / idempotency check → may issue read-only commands even in
  dry-run (`cat`, `authselect current`) because those cannot change
  state.
- If a write would be required AND `config.dry_run=True` → return
  `AuthActionResult(status=DRY_RUN, command=<args>)` without
  dispatching the mutation.

---

## Correctness Properties

1. **authselect-marker protection.** `write_service` must refuse to
   overwrite a file marked as authselect-generated unless the caller
   explicitly opts in.
2. **Idempotency.** Each `set_*` / `select_*` / `enable_*` /
   `disable_*` method returns SKIPPED when the live state already
   matches the requested state — verified against the reader pair.
3. **Empty-limits-file cleanup.** When `remove_limit` empties a
   drop-in file, the file itself is removed.
4. **No silent failure.** Every non-zero exit from the runner
   surfaces as `status=FAILED` with `exit_code` and `stderr`.
5. **Dry-run safety.** No mutating command is issued when
   `dry_run=True`; read-only reconciliation queries may still run.

---

## Testing Strategy

- A local `_MockRunner` dataclass mirroring the pattern already used
  in `test_commandexecution.py` / `test_auditmanagement.py`, with
  keyed results for arbitrary command queues and a call recorder.
- Unit tests per public method covering: happy-path, SKIPPED,
  FAILED, DRY_RUN branches.
- Parser tests for `authselect current` output, `faillock --user`
  output, `/etc/security/limits.conf` and its drop-ins, and
  `/etc/nsswitch.conf`.
- Targeted test for authselect-marker protection in
  `write_service`.
- No live system or root access required.

---

## Module Layout

```
source/packages/automojo/sysman/linux/redhat/
├── authenticationmanagement.py           ← AuthenticationManager + models

source/testroots/testbase/
└── automojo/tests/sysman/linux/redhat/
    └── test_authenticationmanagement.py
```

---

## Seams With Other Features

| Seam | Owner | This Feature |
|---|---|---|
| SSH password/PAM hardening | `3-ssh-server-configuration` | Leaves sshd's PAM directives alone — configured via sshd_config drop-ins |
| Per-user expiry / lock | `3-user-group-management` | This feature handles the *policy* (faillock.conf); user-group handles the *account* (`passwd -l`, `chage`) |
| SSSD-driven authselect profiles | `3-identity-management` | `3-identity-management` may call `select_profile("sssd", [...])`; this feature exposes the primitive |
| Compliance snapshot of PAM posture | `3-security-compliance` | This feature exposes query methods; compliance reads and compares against a baseline |
