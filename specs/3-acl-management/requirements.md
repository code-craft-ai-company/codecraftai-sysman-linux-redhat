# Requirements

## Introduction

The ACL management feature provides the
`automojo-sysman-linux-redhat` package with the ability to manage
POSIX ACLs, extended file attributes, and file capabilities on
Red Hat Linux systems.  It covers `setfacl` / `getfacl` for POSIX
ACLs, `chattr` / `lsattr` for ext*-family extended attributes, and
`getcap` / `setcap` for file capabilities.

All system commands are executed through the `automojo-interfaces`
command-runner abstraction.  All mutating operations respect
dry-run mode.

This feature complements — and does not duplicate — the
basic-permission surface owned by `3-filesystem-management`
(`chmod`, `chown`) and the compliance-audit surface owned by
`3-security-compliance` (world-writable / SUID discovery).

---

## Requirements

### Requirement 1: Query POSIX ACL

**User Story:** As a maintenance coordinator, I want to read the
current POSIX ACL of a file or directory, so that compliance
checks and diffs are possible.

#### Acceptance Criteria

1. WHEN the coordinator queries an ACL THE SYSTEM SHALL invoke
   `getfacl --absolute-names --omit-header <path>` and parse the
   output into an `AclSet` record with `entries: list[AclEntry]`,
   `default_entries: list[AclEntry]` (for directories), and
   `has_mask: bool`.
2. WHEN the file does not exist THE SYSTEM SHALL return `None`
   rather than raising.
3. WHEN `recursive=True` THE SYSTEM SHALL invoke `getfacl -R
   --absolute-names --omit-header <path>` and return
   `dict[str, AclSet]` keyed by path.

---

### Requirement 2: Set POSIX ACL Entries

**User Story:** As a maintenance coordinator, I want to add,
modify, or remove ACL entries on a path so that access aligns with
the declared posture.

#### Acceptance Criteria

1. WHEN the coordinator adds or modifies entries THE SYSTEM SHALL
   invoke `setfacl [-R] -m <spec>[,<spec>...] <path>` where each
   `<spec>` is of the form `u:<user>:<perms>`, `g:<group>:<perms>`,
   `m:<perms>`, `o:<perms>`, or a default-ACL variant prefixed
   with `d:`.
2. WHEN the coordinator removes entries THE SYSTEM SHALL invoke
   `setfacl [-R] -x <spec>[,<spec>...] <path>`.
3. WHEN the coordinator removes all ACL entries THE SYSTEM SHALL
   invoke `setfacl [-R] -b <path>` (leaves only the base
   owner/group/other entries from `chmod`).
4. WHEN the current ACL (queried first) already contains the
   requested entries with matching perms THE SYSTEM SHALL return
   `SKIPPED`.
5. WHEN `recursive=True` THE SYSTEM SHALL pass `-R` and honour the
   recursive semantics of `setfacl`.
6. WHEN dry-run is active THE SYSTEM SHALL NOT dispatch.

---

### Requirement 3: Default ACLs on Directories

**User Story:** As a maintenance coordinator, I want to set default
ACLs so that newly-created entries inherit the declared access.

#### Acceptance Criteria

1. WHEN the coordinator sets a default entry THE SYSTEM SHALL
   require that `<path>` is a directory (invoke `stat -c %F
   <path>` and reject with `FAILED` if not a directory).
2. WHEN the entry uses the `d:` prefix THE SYSTEM SHALL dispatch
   the same way as for non-default entries.
3. WHEN the coordinator removes all default entries THE SYSTEM
   SHALL invoke `setfacl -k <path>`.

---

### Requirement 4: Extended File Attributes (`chattr` / `lsattr`)

**User Story:** As a maintenance coordinator, I want to set and
query ext*-family extended attributes (`immutable`, `append-only`,
`no-atime`, `no-dump`, etc.) so that files resist accidental
mutation.

#### Acceptance Criteria

1. WHEN the coordinator queries attributes THE SYSTEM SHALL invoke
   `lsattr -d <path>` and parse the output into a `FileAttributes`
   record with booleans for each documented attribute plus a
   `raw_flags: str` field.
2. WHEN the coordinator adds attributes THE SYSTEM SHALL invoke
   `chattr [-R] +<flags> <path>`.
3. WHEN the coordinator removes attributes THE SYSTEM SHALL invoke
   `chattr [-R] -<flags> <path>`.
4. WHEN the coordinator sets attributes to an exact mask THE
   SYSTEM SHALL invoke `chattr [-R] =<flags> <path>`.
5. WHEN the requested attribute state already matches THE SYSTEM
   SHALL return `SKIPPED`.
6. WHEN `recursive=True` is set on a non-directory path THE
   SYSTEM SHALL return `FAILED` with a descriptive message rather
   than silently ignoring it.

---

### Requirement 5: File Capabilities (`getcap` / `setcap`)

**User Story:** As a maintenance coordinator, I want to manage
Linux file capabilities so that specific binaries can perform
privileged operations without being setuid-root.

#### Acceptance Criteria

1. WHEN the coordinator queries capabilities THE SYSTEM SHALL
   invoke `getcap <path>` and return a `FileCapabilities` record
   with `path`, `effective: list[str]`, `permitted: list[str]`,
   `inheritable: list[str]`, and `raw: str`.  Empty lists when no
   capabilities are set.
2. WHEN the coordinator sets capabilities THE SYSTEM SHALL invoke
   `setcap '<cap1>=ep,<cap2>=eip' <path>` (standard capability-set
   syntax).
3. WHEN the coordinator removes all capabilities THE SYSTEM SHALL
   invoke `setcap -r <path>`.
4. WHEN the requested capability set already matches THE SYSTEM
   SHALL return `SKIPPED`.

---

### Requirement 6: Bulk Queries

**User Story:** As a maintenance coordinator, I want to enumerate
ACLs, extended attributes, or file capabilities across a directory
subtree, so that compliance reports can summarise posture.

#### Acceptance Criteria

1. WHEN the coordinator enumerates ACLs recursively THE SYSTEM
   SHALL invoke `getfacl -R --absolute-names --omit-header
   <root>` and return `dict[str, AclSet]`.
2. WHEN the coordinator enumerates extended attributes recursively
   THE SYSTEM SHALL invoke `lsattr -R <root>` and return
   `dict[str, FileAttributes]`.
3. WHEN the coordinator enumerates file capabilities on a subtree
   THE SYSTEM SHALL invoke `getcap -r <root>` and return
   `list[FileCapabilities]`.

---

### Requirement 7: Dry-Run, Elevation, and Error Reporting

**User Story:** Standard dry-run / elevation / failure semantics.

#### Acceptance Criteria

1. WHEN `config.dry_run=True` THE SYSTEM SHALL NOT dispatch
   `setfacl`, `chattr`, or `setcap` and SHALL return
   `AclActionResult(status=DRY_RUN)`.
2. WHEN `config.elevated=True` THE SYSTEM SHALL dispatch mutating
   commands elevated.
3. WHEN the runner returns non-zero THE SYSTEM SHALL return
   `AclActionResult(status=FAILED, exit_code=<code>,
   stderr=<captured>)`.
