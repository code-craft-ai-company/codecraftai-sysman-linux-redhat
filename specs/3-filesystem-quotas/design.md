# Design

## Overview

The filesystem-quotas feature is implemented as a single module,
`ccai.sysman.linux.redhat.filesystemquotas`, exposing a
`FilesystemQuotasManager` class that wraps `quota`, `repquota`,
`edquota`, `setquota`, `quotaon`/`quotaoff`, `quotacheck`, and
`xfs_quota` through the `ICommandRunner` abstraction.  It also
reads and writes `/etc/projects` and `/etc/projid` for XFS project
quotas.

The manager auto-detects whether the target mount is XFS or ext*
and dispatches the right tool-family accordingly; callers express
intent at a higher level and do not select a tool.

Follows the standard `ActionStatus` / `Config` / `ActionResult` /
`_dispatch` pattern.

**See Also:**
- `3-filesystem-management` — mount and `/etc/fstab` ownership;
  callers must ensure `usrquota` / `grpquota` / `prjquota` mount
  options are active before this feature can enforce quotas.
- `3-user-group-management` — accounts and groups that quotas
  target.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│            Maintenance Coordinator                          │
│  - Declares quota limits per user/group/project             │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            FilesystemQuotasManager                          │
│                                                             │
│  Detection:                                                 │
│    _fstype_of(mount) — dispatches XFS vs ext*               │
│  Enforcement state:                                         │
│    query_enforcement(), enable(), disable(), quotacheck()   │
│  Limits:                                                    │
│    set_user_quota(), set_group_quota(),                     │
│    set_project_quota()                                      │
│  Query:                                                     │
│    query_user(), query_group(), query_project(),            │
│    list_all()                                               │
│  Grace period:                                              │
│    get_grace_period(), set_grace_period()                   │
│  XFS project plumbing:                                      │
│    define_project(), remove_project(),                      │
│    activate_project(), list_projects()                      │
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
class FilesystemQuotasConfig:
    dry_run: bool = False
    elevated: bool = True
    projects_file: str = "/etc/projects"
    projid_file: str = "/etc/projid"
```

---

## Data Models

```python
class QuotaActionStatus(Enum):
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class QuotaActionResult:
    status: QuotaActionStatus
    detail: str | None = None
    mount: str | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    command: list[str] | None = None


@dataclass
class QuotaEnforcement:
    mount: str
    fstype: str                         # 'ext4', 'xfs', ...
    user_enforced: bool
    group_enforced: bool
    project_enforced: bool


class QuotaTarget(Enum):
    USER = "user"
    GROUP = "group"
    PROJECT = "project"


@dataclass
class QuotaLimits:
    block_soft: int                     # blocks of 1K (ext*) or bytes (XFS)
    block_hard: int
    inode_soft: int
    inode_hard: int


@dataclass
class QuotaEntry:
    mount: str
    target: QuotaTarget
    name: str                           # username, group name, or project name
    blocks_used: int
    block_soft: int
    block_hard: int
    inodes_used: int
    inode_soft: int
    inode_hard: int
    grace_blocks: str | None            # 'none' | '7days' | ...
    grace_inodes: str | None


@dataclass
class GracePeriod:
    mount: str
    target: QuotaTarget
    block_seconds: int
    inode_seconds: int


@dataclass
class ProjectDefinition:
    project_id: int
    project_name: str
    paths: list[str]                    # one per /etc/projects line bound to this id
```

---

## Exceptions

```python
class QuotaFilesystemNotMountedError(SemanticError):
    ...


class QuotaUnsupportedOperationError(SemanticError):
    """`quotacheck` on XFS, etc."""
```

---

## Key Method Designs

### `_fstype_of(mount)`

1. Dispatch `findmnt -n -o FSTYPE <mount>` via runner.
2. If exit non-zero → raise `QuotaFilesystemNotMountedError`.
3. Return the stripped fstype string.

### `query_enforcement(mount)`

1. `fstype = _fstype_of(mount)`.
2. If `fstype == "xfs"`:
   - Dispatch `xfs_quota -x -c "state" <mount>`.
   - Parse XFS state output for `"User quota accounting: ON/OFF"`,
     `"Group quota accounting: ON/OFF"`, `"Project quota
     accounting: ON/OFF"`, and the corresponding `Enforcement:`
     lines.  Both accounting AND enforcement must be ON for the
     corresponding `*_enforced` boolean.
3. Else (ext*):
   - Dispatch `quotaon -p <mount>`.
   - Parse `"user quota on <mount> (<dev>) is on/off"` and the
     group variant.

### `set_user_quota(mount, username, limits)`

1. `fstype = _fstype_of(mount)`.
2. Call `query_user(mount, username)`.
3. Compare `current.{block_soft, block_hard, inode_soft, inode_hard}`
   to `limits.*`.  Match → SKIPPED.
4. Dry-run: DRY_RUN.
5. Dispatch (ext*):
   ```
   ["setquota", "-u", username,
    str(limits.block_soft), str(limits.block_hard),
    str(limits.inode_soft), str(limits.inode_hard),
    mount]
   ```
   XFS:
   ```
   ["xfs_quota", "-x", "-c",
    f"limit -u bsoft={...} bhard={...} isoft={...} ihard={...} {username}",
    mount]
   ```

### `quotacheck(mount, remount_ro=True)`

1. `fstype = _fstype_of(mount)`.
2. If `fstype == "xfs"` → raise
   `QuotaUnsupportedOperationError`.
3. Dispatch `["quotacheck", "-ug" if remount_ro else "-ugm",
   mount]`.

---

## Dry-Run Behaviour

Dry-run disables every mutating entry point (`enable`, `disable`,
`quotacheck`, `set_user_quota`, `set_group_quota`,
`set_project_quota`, `set_grace_period`, `define_project`,
`remove_project`, `activate_project`).  Read-only queries run
freely.

---

## Correctness Properties

1. **Fstype dispatch correctness.** Every mount-scoped method
   routes through `_fstype_of` and uses the tool family matching
   the returned fstype.
2. **Idempotent limits.** Setting limits that already match is a
   `SKIPPED` no-op.
3. **XFS quotacheck refusal.** `quotacheck` on XFS raises
   `QuotaUnsupportedOperationError` rather than dispatching a
   command that will fail mysteriously.
4. **Project file coherence.** `define_project` always updates
   BOTH `/etc/projects` (id→path) AND `/etc/projid` (name→id);
   there is no partial-update path.

---

## Testing Strategy

- `_MockRunner` stub; queue `RunResult` keyed by command tuple.
- Parser tests for `quotaon -p`, `repquota`, `quota -v`,
  `xfs_quota state`, and `xfs_quota report`.
- Tests that every mount-scoped method routes through
  `_fstype_of` (assert the `findmnt` call happens first).
- Test that `quotacheck` on XFS raises
  `QuotaUnsupportedOperationError` without dispatching.
- Tests for `/etc/projects` + `/etc/projid` upsert and strip
  correctness.
- Dry-run and failure-mapping sweeps.
- No live filesystem required.

---

## Seams With Other Features

| Seam | Owner | This Feature |
|---|---|---|
| Mount options (`usrquota`, `grpquota`, `prjquota`) | `3-filesystem-management` | Coordinator sets mount options there first; this feature assumes they are active |
| Target accounts / groups | `3-user-group-management` | Caller ensures names are valid; this feature does not verify existence |
| Project-quota directory subtrees | This feature (via `define_project`) | `3-filesystem-management` created the directory; this feature marks it |
