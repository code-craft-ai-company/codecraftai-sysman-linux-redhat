# Requirements

## Introduction

The filesystem-quotas feature provides the
`codecraftai-sysman-linux-redhat` package with the ability to manage
disk quotas on Red Hat Linux systems for ext* and XFS filesystems.
It covers user and group block/inode quotas via the traditional
`quota` tooling, project quotas on XFS via `xfs_quota`, and the
`quotaon`/`quotaoff` runtime toggle.

All system commands (`quota`, `repquota`, `edquota`, `setquota`,
`quotaon`, `quotaoff`, `quotacheck`, `xfs_quota`) are executed
through the `codecraftai-interfaces` command-runner abstraction.  All
mutating operations respect dry-run mode.

This feature complements — and does not duplicate — the mount-side
surface owned by `3-filesystem-management` (the mount options
`usrquota`, `grpquota`, `prjquota`, `pquota` must already be
active before quotas can be enforced).  This feature enforces and
reports; mount-option changes remain the filesystem-management
feature's responsibility.

---

## Requirements

### Requirement 1: Query Quota Enforcement State

**User Story:** As a maintenance coordinator, I want to query
whether user, group, and project quotas are currently enforced on
a filesystem, so that I know whether `setquota` calls will take
effect.

#### Acceptance Criteria

1. WHEN the coordinator queries the enforcement state THE SYSTEM
   SHALL invoke `quotaon -p <mount>` and parse the output into a
   `QuotaEnforcement` record with booleans `user_enforced`,
   `group_enforced`, `project_enforced`.
2. WHEN the filesystem is XFS THE SYSTEM SHALL invoke `xfs_quota
   -x -c "state" <mount>` instead, and parse the XFS-specific
   output into the same record shape.
3. WHEN `quotaon -p` reports that quotas are off THE SYSTEM SHALL
   return `QuotaEnforcement` with every boolean `False`.
4. WHEN the filesystem is not mounted THE SYSTEM SHALL raise
   `QuotaFilesystemNotMountedError` (a `SemanticError` subtype).

---

### Requirement 2: Enable and Disable Quotas

**User Story:** As a maintenance coordinator, I want to turn
quota enforcement on or off on a filesystem, so that operational
posture can change without remounting.

#### Acceptance Criteria

1. WHEN the coordinator enables quotas THE SYSTEM SHALL invoke
   `quotaon -u -g [-P] <mount>` based on the requested types.
2. WHEN the coordinator disables quotas THE SYSTEM SHALL invoke
   `quotaoff -u -g [-P] <mount>`.
3. WHEN quotas are already in the requested state THE SYSTEM SHALL
   return `SKIPPED`.
4. WHEN the filesystem is XFS THE SYSTEM SHALL use `xfs_quota -x
   -c "enable|disable -ug|-p"` instead.

---

### Requirement 3: Build Quota Database (`quotacheck`)

**User Story:** As a maintenance coordinator, I want to rebuild
the quota database on an ext* filesystem, so that accounting
accurately reflects current usage.

#### Acceptance Criteria

1. WHEN the coordinator requests a quotacheck THE SYSTEM SHALL
   invoke `quotacheck -ug <mount>` (or `-ugm` when the caller
   supplies `remount_ro=False`).
2. WHEN the filesystem is XFS THE SYSTEM SHALL refuse the request
   and return `FAILED` with a descriptive message — XFS does not
   use `quotacheck`.
3. WHEN dry-run is active THE SYSTEM SHALL NOT dispatch.

---

### Requirement 4: Set Block and Inode Quotas

**User Story:** As a maintenance coordinator, I want to set
soft/hard block and inode limits per user, group, or project, so
that disk-usage policy is enforced.

#### Acceptance Criteria

1. WHEN the coordinator sets a user quota THE SYSTEM SHALL invoke
   `setquota -u <name> <soft_blocks> <hard_blocks> <soft_inodes>
   <hard_inodes> <mount>`.
2. WHEN the coordinator sets a group quota THE SYSTEM SHALL invoke
   `setquota -g <name> <soft_blocks> <hard_blocks> <soft_inodes>
   <hard_inodes> <mount>`.
3. WHEN the coordinator sets a project quota on XFS THE SYSTEM
   SHALL invoke `xfs_quota -x -c "limit -p bsoft=... bhard=...
   isoft=... ihard=... <project>" <mount>`.
4. WHEN the new values already match the current quota THE SYSTEM
   SHALL return `SKIPPED`.
5. WHEN a limit value is `0` THE SYSTEM SHALL interpret it as "no
   limit" (the tool's own convention) — callers wanting an explicit
   zero quota MUST pass `1` and accept the semantic.
6. WHEN dry-run is active THE SYSTEM SHALL NOT dispatch.

---

### Requirement 5: Query Current Quotas

**User Story:** As a maintenance coordinator, I want to read
current quotas and usage for a user or group, so that reports can
summarise posture.

#### Acceptance Criteria

1. WHEN the coordinator queries a single user / group THE SYSTEM
   SHALL invoke `quota -v -u <name>` (or `-g`) and parse the
   output into a `QuotaEntry` record with `blocks_used`,
   `blocks_soft`, `blocks_hard`, `inodes_used`, `inodes_soft`,
   `inodes_hard`, `grace_blocks`, `grace_inodes`.
2. WHEN the coordinator enumerates all quotas on a mount THE
   SYSTEM SHALL invoke `repquota -u|-g <mount>` and parse the
   output into `list[QuotaEntry]`.
3. WHEN the filesystem is XFS THE SYSTEM SHALL use `xfs_quota -x
   -c "report -u|-g|-p"` and produce the same record shape.

---

### Requirement 6: Project Quota Plumbing for XFS

**User Story:** As a maintenance coordinator, I want to manage XFS
project identifiers, so that project quotas can be applied to
directory subtrees.

#### Acceptance Criteria

1. WHEN the coordinator defines a project THE SYSTEM SHALL upsert
   the `<project_id>:<path>` line in `/etc/projects` and the
   `<project_name>:<project_id>` line in `/etc/projid`.
2. WHEN the coordinator activates a project THE SYSTEM SHALL
   invoke `xfs_quota -x -c "project -s <project_name>" <mount>`.
3. WHEN the coordinator removes a project THE SYSTEM SHALL strip
   the matching lines from both files; SKIPPED when the project is
   not defined.
4. WHEN the coordinator lists defined projects THE SYSTEM SHALL
   parse `/etc/projects` + `/etc/projid` into
   `list[ProjectDefinition]`.

---

### Requirement 7: Grace Periods

**User Story:** As a maintenance coordinator, I want to set the
per-filesystem grace period that bounds how long a user may exceed
the soft limit.

#### Acceptance Criteria

1. WHEN the coordinator sets the grace period on ext* THE SYSTEM
   SHALL invoke `setquota -t <block_grace_seconds>
   <inode_grace_seconds> <mount>` (with optional `-u` / `-g`).
2. WHEN the coordinator sets the grace period on XFS THE SYSTEM
   SHALL invoke `xfs_quota -x -c "timer -u|-g|-p ..."` with the
   appropriate time format.
3. WHEN the coordinator queries the grace period THE SYSTEM SHALL
   return a `GracePeriod` record with `block_seconds` and
   `inode_seconds`.

---

### Requirement 8: Dry-Run, Elevation, and Error Reporting

**User Story:** Standard dry-run / elevation / failure semantics.

#### Acceptance Criteria

1. WHEN `config.dry_run=True` THE SYSTEM SHALL NOT dispatch any
   mutating command and SHALL return
   `QuotaActionResult(status=DRY_RUN)`.
2. WHEN `config.elevated=True` THE SYSTEM SHALL dispatch mutating
   commands elevated.
3. WHEN the runner returns non-zero THE SYSTEM SHALL return
   `QuotaActionResult(status=FAILED, exit_code=<code>,
   stderr=<captured>)`.
