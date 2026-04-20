# Design

## Overview

The ACL management feature is implemented as a single module,
`automojo.sysman.linux.redhat.aclmanagement`, exposing an
`AclManager` class that wraps `getfacl` / `setfacl`, `lsattr` /
`chattr`, and `getcap` / `setcap` through the `ICommandRunner`
abstraction.

Follows the standard `ActionStatus` / `Config` / `ActionResult` /
`_dispatch` pattern.

**See Also:**
- `3-filesystem-management` — basic `chmod` / `chown` ownership.
- `3-security-compliance` — reads this feature's queries to build
  compliance reports.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│            Maintenance Coordinator                          │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            AclManager                                       │
│                                                             │
│  POSIX ACLs:                                                │
│    query_acl(), set_acl(), remove_acl_entries(),            │
│    clear_acl(), clear_default_acl()                         │
│  Extended attributes:                                       │
│    query_attrs(), add_attrs(), remove_attrs(), set_attrs()  │
│  File capabilities:                                         │
│    query_caps(), set_caps(), clear_caps()                   │
│  Bulk:                                                      │
│    enumerate_acls(), enumerate_attrs(), enumerate_caps()    │
└────────────────────────┬────────────────────────────────────┘
                         │ delegates to
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                   ICommandRunner                            │
└─────────────────────────────────────────────────────────────┘
```

---

## Configuration

```python
@dataclass
class AclManagerConfig:
    dry_run: bool = False
    elevated: bool = True
```

---

## Data Models

```python
class AclActionStatus(Enum):
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class AclActionResult:
    status: AclActionStatus
    detail: str | None = None
    path: str | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    command: list[str] | None = None


@dataclass
class AclEntry:
    tag: str                    # 'user', 'group', 'mask', 'other', 'user:alice', ...
    qualifier: str | None       # 'alice', 'staff', None for mask/other
    perms: str                  # 'rwx', 'r-x', '---', etc. (three-char)
    is_default: bool = False


@dataclass
class AclSet:
    path: str
    entries: list[AclEntry]
    default_entries: list[AclEntry]
    has_mask: bool


@dataclass
class FileAttributes:
    path: str
    immutable: bool             # i
    append_only: bool           # a
    no_atime: bool              # A
    no_dump: bool               # d
    sync: bool                  # S
    no_tail_merge: bool         # t
    undelete: bool              # u
    raw_flags: str              # full lsattr flag string


@dataclass
class FileCapabilities:
    path: str
    effective: list[str]
    permitted: list[str]
    inheritable: list[str]
    raw: str
```

---

## Key Method Designs

### `set_acl(path, entries, recursive=False)`

1. Call `query_acl(path)` (or enumerate recursively first entry
   when `recursive=True`).
2. Compare requested `entries` to current set.
3. If every requested entry is already present with matching perms
   → SKIPPED.
4. Dry-run: DRY_RUN with args.
5. Build `spec_string = ",".join(entry_to_spec(e) for e in entries)`
   where `entry_to_spec` produces `u:<name>:<perms>`,
   `g:<name>:<perms>`, `m:<perms>`, `o:<perms>`, with `d:` prefix
   for defaults.
6. Dispatch `["setfacl", *(["-R"] if recursive else []), "-m",
   spec_string, path]`.

### `set_caps(path, caps)`

1. Call `query_caps(path)`.
2. If `current == caps` (exact) → SKIPPED.
3. Dry-run: DRY_RUN.
4. Build capability string in canonical form `'cap=ep[+cap2=eip]'`.
5. Dispatch `["setcap", cap_string, path]`.

### `add_attrs(path, flags, recursive=False)`

1. Call `query_attrs(path)`.
2. Determine which flags are already set; compute the set to add.
3. If nothing to add → SKIPPED.
4. Dispatch `["chattr", *(["-R"] if recursive else []), f"+{newflags}",
   path]`.

---

## Dry-Run Behaviour

Dry-run disables every mutating entry point.  Read-only queries
(`query_acl`, `query_attrs`, `query_caps`, and the `enumerate_*`
variants) run freely.

---

## Correctness Properties

1. **Idempotency.** Every `set_*` / `add_*` / `remove_*` method
   SKIPS when the live state already matches the requested state.
2. **Directory-only guards.** `clear_default_acl` and default-ACL
   setters verify the path is a directory before dispatching.
3. **Recursive guards.** `add_attrs(..., recursive=True)` on a
   non-directory returns FAILED with a clear message rather than
   silently ignoring the flag.

---

## Testing Strategy

- `_MockRunner` stub.
- Parser tests for `getfacl --omit-header`, `lsattr -d`, `getcap`.
- Tests for SKIPPED when the live state already matches.
- Tests for the directory-only guard on default ACLs.
- Dry-run and failure-mapping sweeps.
- No live filesystem required.

---

## Seams With Other Features

| Seam | Owner | This Feature |
|---|---|---|
| Basic perms (`chmod` / `chown`) | `3-filesystem-management` | Out of scope here; this feature layers ACLs on top |
| Compliance audit | `3-security-compliance` | Consumes `enumerate_*` queries |
