# Design

## Overview

The Samba server management feature is implemented as a single
module, `ccai.sysman.linux.redhat.sambaservermanagement`,
exposing a `SambaServerManager` class that manages the content of
`/etc/samba/smb.conf`, validates via `testparm`, drives
`smbpasswd` / `pdbedit` / `net ads` / `wbinfo` through the
`ICommandRunner` abstraction, and delegates service lifecycle to
an injected `ServiceManager`.

Passphrase material (Samba user passwords, AD-admin credentials
for `net ads join`) flows on runner `stdin`, never argv.  The
returned `SambaActionResult.command` field is sanitised.

**See Also:**
- `3-service-management` — `smb.service`, `nmb.service`,
  `winbind.service` lifecycle.
- `3-firewall-management` — `samba` service or ports 137/138/139/445.
- `3-identity-management` — SSSD / realmd path for AD join;
  this feature exposes `net ads` as the Samba-native alternative.
- `3-user-group-management` — local POSIX accounts backing
  Samba users.
- `codecraftai-credentials` — passphrase / AD-admin credential
  objects.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│            Maintenance Coordinator                          │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            SambaServerManager                               │
│                                                             │
│  smb.conf global:                                           │
│    get_globals(), set_global(), remove_global()             │
│  Shares:                                                    │
│    list_shares(), query_share(),                            │
│    define_share(), update_share(), remove_share()           │
│  Validation + reload:                                       │
│    validate_config(), reload(), restart()                   │
│  Samba users:                                               │
│    list_samba_users(), add_samba_user(),                    │
│    delete_samba_user(), enable_samba_user(),                │
│    disable_samba_user()                                     │
│  AD:                                                        │
│    join_ad(), leave_ad(), test_join(),                      │
│    list_trusted_domains()                                   │
└────────────────┬──────────────────────────┬─────────────────┘
                 │                          │
                 ▼                          ▼
      ┌─────────────────────┐   ┌─────────────────────────┐
      │   ICommandRunner    │   │ Injected ServiceManager │
      └─────────────────────┘   └─────────────────────────┘
```

---

## Configuration

```python
@dataclass
class SambaServerManagerConfig:
    dry_run: bool = False
    elevated: bool = True
    smb_conf: str = "/etc/samba/smb.conf"
    smb_conf_dir: str = "/etc/samba/smb.conf.d"
```

---

## Data Models

```python
class SambaActionStatus(Enum):
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class SambaActionResult:
    status: SambaActionStatus
    detail: str | None = None
    share: str | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    command: list[str] | None = None        # sanitised (no passphrase)


@dataclass
class ShareSpec:
    name: str
    path: str
    comment: str | None = None
    writable: bool | None = None
    browseable: bool | None = None
    guest_ok: bool | None = None
    valid_users: list[str] | None = None
    read_list: list[str] | None = None
    write_list: list[str] | None = None
    create_mask: str | None = None
    directory_mask: str | None = None
    extra_directives: dict[str, str] = field(default_factory=dict)


@dataclass
class ShareInfo:
    name: str
    directives: dict[str, str]              # the full, parsed directive set


@dataclass
class SambaValidateResult:
    ok: bool
    exit_code: int
    stderr: str
    warnings: list[str]
    parsed_sections: list[str]


@dataclass
class SambaUserInfo:
    username: str
    uid: int | None
    flags: str                              # 'U' (enabled), 'D' (disabled), ...
    last_password_change: str | None
```

---

## Key Method Designs

### `define_share(spec, overwrite=False)`

1. Read `smb.conf` via `cat`.
2. Parse into ordered sections.
3. If `[spec.name]` exists and `overwrite=False` → compare every
   directive in `spec` to the current block.  Match → SKIPPED;
   otherwise call `update_share(spec)` internally and return that
   result with `detail = "updated existing share"`.
4. If `[spec.name]` does not exist → insert new block at end.
5. Dry-run: DRY_RUN with args.
6. Rewrite via `tee`.
7. `reload()` (which validates first).

### `add_samba_user(username, credential)`

1. If credential is missing → raise
   `SambaCredentialError(SemanticError)`.
2. Dry-run: DRY_RUN.
3. Dispatch `["smbpasswd", "-a", "-s", username]` with
   `stdin=credential.secret_bytes() + b"\n" +
   credential.secret_bytes() + b"\n"` (the `-s` variant reads the
   password twice from stdin for confirmation).
4. Sanitise recorded command — no password bytes.

### `join_ad(domain, admin_credential)`

1. Dry-run: DRY_RUN.
2. Dispatch `["net", "ads", "join", "-U",
   admin_credential.username]` with
   `stdin=admin_credential.secret_bytes()`.
3. Sanitise recorded command.

### `reload()`

1. Call `validate_config()`.
2. If not OK → return `SambaActionResult(status=FAILED,
   detail="testparm validation failed",
   stderr=<validate.stderr>)`.
3. Delegate to `service_manager.reload("smb")`.

---

## Dry-Run Behaviour

Dry-run disables every mutating path (smb.conf writes, smbpasswd,
net ads, reload, restart).  Read-only queries and `validate_config`
run freely.

---

## Correctness Properties

1. **No passphrase on argv.** Both `add_samba_user` and `join_ad`
   route the password on stdin; recorded `command` field is
   sanitised.
2. **Validate-before-reload.** `reload()` never delegates to the
   service manager when validation fails.
3. **Idempotent share definitions.** `define_share` returns SKIPPED
   when the existing block already carries every requested
   directive.
4. **Block-boundary strip.** `remove_share` strips the `[name]`
   header and every directive until the next section header,
   leaving surrounding sections intact.

---

## Testing Strategy

- `_MockRunner` + `_MockServiceManager` + `_MockCredential`.
- Parser tests for `testparm -s --suppress-prompt`, `pdbedit -L`,
  `wbinfo --trusted-domains`.
- Tests that `add_samba_user` and `join_ad` never place the
  password on argv; verify stdin carries the expected bytes.
- Tests for `reload()` — validate-then-delegate; validate-failed
  path.
- Tests for share CRUD block-boundary correctness.
- Dry-run and failure-mapping sweeps.

---

## Seams With Other Features

| Seam | Owner | This Feature |
|---|---|---|
| `smb.service` / `nmb.service` / `winbind.service` lifecycle | `3-service-management` | Delegated |
| SMB ports through the firewall | `3-firewall-management` | Out of scope here |
| AD join via SSSD / realmd | `3-identity-management` | Alternative path; coordinator chooses |
| Local POSIX accounts | `3-user-group-management` | Samba users must exist as POSIX users first for security modes that require it |
| Credential material | `codecraftai-credentials` | Credential objects accepted; passphrases on stdin |
