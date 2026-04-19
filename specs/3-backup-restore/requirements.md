# Requirements

## Introduction

The backup and restore feature provides the `automojo-sysman-linux-redhat` package
with the ability to produce, verify, restore, and prune file-tree archives of
directories and configuration trees on Red Hat Linux systems. It covers archive
creation (`tar`/`rsync`), manifest and checksum generation, restore into the same or
alternate locations, retention policy application, and archive content queries.

A maintenance coordinator uses this feature to capture known-good snapshots of
specified paths before destructive or risky changes, and to restore from those
snapshots on demand. All system commands (`tar`, `rsync`, `sha256sum`, `gzip`,
`zstd`, `xz`, `openssl enc`) are executed through the `automojo-interfaces`
command-runner abstraction. All reads and writes against the backup repository
directory use the `automojo-interfaces` file-operator abstraction. All mutating
operations respect dry-run mode.

This feature does not back up databases, block devices, or LVM snapshots;
block-level snapshots remain the responsibility of the `storage-management`
feature, and application-consistent database dumps remain the responsibility of
application-specific tooling.

---

## Requirements

### Requirement 1: Initialize a Backup Repository

**User Story:** As a maintenance coordinator, I want to initialize a directory as a
backup repository with a known layout, so that subsequent archives can be stored
and retrieved consistently.

#### Acceptance Criteria

1. WHEN the coordinator supplies a repository root path THE SYSTEM SHALL create the
   directory (mode `0750`, owner `root`, group `root`) and the subdirectories
   `archives/`, `manifests/`, and `tmp/` (mode `0750`) through the file operator
   with elevated privileges.
2. WHEN the repository root already exists with the expected layout THE SYSTEM
   SHALL skip creation and return a `BackupActionResult` with status `SKIPPED`.
3. WHEN the repository root exists but is not a directory or has non-matching
   permissions THE SYSTEM SHALL return a `BackupActionResult` with status `FAILED`
   and an error describing the mismatch, and SHALL NOT modify it.
4. WHEN initialization succeeds THE SYSTEM SHALL write a `repository.json` file at
   the repository root containing the schema version, creation timestamp, and
   default compression setting.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT create any directory or file.

---

### Requirement 2: Create an Archive

**User Story:** As a maintenance coordinator, I want to create a tar archive of one
or more source paths into the backup repository, so that a known-good snapshot is
captured before risky changes.

#### Acceptance Criteria

1. WHEN the coordinator supplies a repository, an archive name, a list of source
   paths, and an optional list of exclude patterns THE SYSTEM SHALL invoke
   `tar --create --file <tmp-path> --acls --xattrs --selinux --numeric-owner
   --sparse [--one-file-system] [--exclude=<pat> ...] [compression] -- <sources>`
   through the command runner with elevated privileges, where the compression
   flag is selected from the coordinator's compression choice (`none`, `gzip`,
   `zstd`, `xz`).
2. WHEN all sources are missing THE SYSTEM SHALL return a `BackupActionResult` with
   status `FAILED` and an error naming the missing paths, and SHALL NOT create any
   archive file.
3. WHEN creation succeeds THE SYSTEM SHALL compute the SHA-256 of the resulting
   archive via `sha256sum`, atomically rename the temp file to
   `archives/<name>-<timestamp>.tar[.<ext>]`, and write a sibling
   `manifests/<name>-<timestamp>.json` containing archive size, checksum, compression,
   source list, exclude list, host fields, and the tool versions used.
4. THE SYSTEM SHALL set the archive file mode to `0600` and owner/group to `root`.
5. WHEN creation succeeds THE SYSTEM SHALL return a `BackupActionResult` with
   status `SUCCESS`, the archive path, manifest path, and SHA-256 checksum.
6. WHEN creation fails THE SYSTEM SHALL remove the incomplete temp file and return
   a `BackupActionResult` with status `FAILED`, exit code, and captured stderr.
7. WHEN dry-run mode is active THE SYSTEM SHALL NOT create any file and SHALL
   return the archive path that would have been created.

---

### Requirement 3: Create an Incremental Mirror via rsync

**User Story:** As a maintenance coordinator, I want to create an `rsync`-based
mirror of a source tree inside the repository, so that very large trees can be
snapshotted efficiently using hard-linked deltas.

#### Acceptance Criteria

1. WHEN the coordinator requests a mirror and a previous mirror exists THE SYSTEM
   SHALL invoke
   `rsync -aAXH --numeric-ids --delete --link-dest=<previous> <source>/ <target>/`
   through the command runner with elevated privileges.
2. WHEN no previous mirror exists THE SYSTEM SHALL invoke
   `rsync -aAXH --numeric-ids --delete <source>/ <target>/` without a link-dest.
3. WHEN the source does not exist THE SYSTEM SHALL return a `BackupActionResult`
   with status `FAILED` and an error naming the path.
4. WHEN the operation succeeds THE SYSTEM SHALL write a sibling manifest that
   captures the source, target, link-dest, and the `rsync` exit code.
5. WHEN dry-run mode is active THE SYSTEM SHALL pass `--dry-run` to `rsync` and
   SHALL capture the planned file list into the returned result, making no
   actual changes.

---

### Requirement 4: Verify an Archive

**User Story:** As a maintenance coordinator, I want to verify that an archive's
on-disk bytes match the checksum recorded in its manifest, so that corrupted
archives can be detected before being relied on.

#### Acceptance Criteria

1. WHEN the coordinator supplies a manifest name THE SYSTEM SHALL read the
   corresponding manifest file, compute `sha256sum <archive-path>`, and compare
   the result to the manifest-recorded checksum.
2. WHEN checksums match THE SYSTEM SHALL return a `BackupVerifyResult` with
   `ok=True`.
3. WHEN checksums differ THE SYSTEM SHALL return a `BackupVerifyResult` with
   `ok=False` and the computed-vs-recorded checksums.
4. WHEN the manifest or archive is missing THE SYSTEM SHALL raise a
   `BackupNotFoundError`.
5. WHEN the archive is a tarball the coordinator MAY also request a `tar --test`
   which the SYSTEM SHALL perform via
   `tar --test-label --file <archive> >/dev/null`. If this sub-step fails THE
   SYSTEM SHALL set `ok=False` and include the captured tar error.

---

### Requirement 5: List Archives and Manifests

**User Story:** As a maintenance coordinator, I want to list archives in a
repository with their manifests, so that retention and restore decisions can be
made without walking the filesystem manually.

#### Acceptance Criteria

1. WHEN the coordinator requests the list THE SYSTEM SHALL enumerate
   `manifests/*.json` under the repository root.
2. THE SYSTEM SHALL return a list of `BackupEntry` records containing `name`,
   `timestamp`, `archive_path`, `manifest_path`, `size_bytes`, `compression`,
   and `sha256`.
3. WHEN the coordinator requests filtering by `name` prefix THE SYSTEM SHALL
   include only matching entries.
4. WHEN the repository does not exist THE SYSTEM SHALL raise a
   `BackupRepositoryNotFoundError`.

---

### Requirement 6: Restore an Archive

**User Story:** As a maintenance coordinator, I want to restore an archive into its
original location or an alternate root, so that a prior state can be recovered.

#### Acceptance Criteria

1. WHEN the coordinator supplies a manifest name and a target root THE SYSTEM
   SHALL first invoke the Verify capability (Requirement 4). If verification
   fails the SYSTEM SHALL NOT restore and SHALL return a `BackupActionResult`
   with status `FAILED`.
2. WHEN verification passes THE SYSTEM SHALL invoke
   `tar --extract --file <archive> --acls --xattrs --selinux --numeric-owner
   -C <target-root> [compression]` through the command runner with elevated
   privileges.
3. WHEN the target root does not exist THE SYSTEM SHALL create it (mode `0755`)
   before extracting, unless the coordinator passed `require_existing=True`, in
   which case the SYSTEM SHALL return a `BackupActionResult` with status
   `FAILED`.
4. WHEN the coordinator requests a restricted file list THE SYSTEM SHALL pass
   the list to `tar --extract <path1> <path2> ...`.
5. WHEN extraction succeeds THE SYSTEM SHALL return a `BackupActionResult` with
   status `SUCCESS` and the count of restored files.
6. WHEN extraction fails THE SYSTEM SHALL return a `BackupActionResult` with
   status `FAILED`, exit code, and captured stderr. The SYSTEM SHALL NOT attempt
   a partial rollback; partial extraction is the caller's responsibility to
   resolve.
7. WHEN dry-run mode is active THE SYSTEM SHALL NOT extract any file and SHALL
   return the list of paths that would have been written (computed via
   `tar --list --file <archive>`).

---

### Requirement 7: Query Archive Contents

**User Story:** As a maintenance coordinator, I want to list the paths contained in
an archive, so that I can preview what a restore would touch.

#### Acceptance Criteria

1. WHEN the coordinator supplies a manifest name THE SYSTEM SHALL invoke
   `tar --list --file <archive> [compression]` through the command runner.
2. THE SYSTEM SHALL return a `BackupContents` record containing the total entry
   count and, when requested, the full list of archive paths.
3. WHEN the archive is missing THE SYSTEM SHALL raise a `BackupNotFoundError`.
4. WHEN the command fails THE SYSTEM SHALL raise a `BackupQueryError`.

---

### Requirement 8: Apply a Retention Policy

**User Story:** As a maintenance coordinator, I want to prune archives according to
a declared retention policy, so that the repository does not grow unbounded.

#### Acceptance Criteria

1. WHEN the coordinator supplies a policy consisting of `keep_last_n` and/or
   `keep_older_than_days` and/or `keep_min_total_bytes` THE SYSTEM SHALL evaluate
   the full archive list and compute the set of entries to remove, applying the
   rules such that no entry is removed unless all supplied rules permit its
   removal.
2. WHEN no entries are eligible for removal THE SYSTEM SHALL return a
   `BackupActionResult` with status `SKIPPED`.
3. WHEN at least one entry is eligible THE SYSTEM SHALL remove both the archive
   file and its manifest through the file operator with elevated privileges.
4. WHEN removal succeeds THE SYSTEM SHALL return a `BackupActionResult` with
   status `SUCCESS`, the count and total byte size removed, and the list of
   removed names.
5. WHEN removal fails for any entry THE SYSTEM SHALL continue removing the rest
   and return a `BackupActionResult` with status `FAILED` listing both the
   successfully-removed entries and the per-entry errors.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT remove any file and SHALL
   return the list of entries that would have been removed.

---

### Requirement 9: Optional Archive Encryption

**User Story:** As a maintenance coordinator, I want to optionally encrypt archives
with a passphrase-derived key, so that at-rest archives are protected when the
repository is shared or off-site.

#### Acceptance Criteria

1. WHEN the coordinator supplies `encryption=True` and a passphrase source to the
   archive-create capability THE SYSTEM SHALL pipe the output of `tar`/compression
   through `openssl enc -aes-256-cbc -salt -pbkdf2` with the passphrase read from
   stdin (never from argv).
2. THE SYSTEM SHALL name the resulting file with a trailing `.enc` extension and
   record `encryption=aes-256-cbc/pbkdf2` in the manifest.
3. WHEN the coordinator restores or verifies an encrypted archive THE SYSTEM
   SHALL pipe the input through `openssl enc -d -aes-256-cbc -pbkdf2` using the
   same stdin passphrase mechanism.
4. THE SYSTEM SHALL NEVER include the passphrase on the command line and SHALL
   redact it from any audit record.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT invoke any encryption command.

---

### Requirement 10: Elevated Privilege Execution

**User Story:** As a maintenance coordinator, I want all mutating backup operations
to run with the privileges required by the underlying tools, so that archives and
restores capture and recreate the full permission and ownership set.

#### Acceptance Criteria

1. WHEN the system is configured to use sudo elevation THE SYSTEM SHALL prefix all
   archive create, rsync mirror, tar extract, repository initialization, and
   prune commands with the command runner's elevation mechanism.
2. WHEN the system is running as root THE SYSTEM SHALL NOT prefix commands with
   `sudo`.
3. WHEN read-only commands (`sha256sum` on an already-readable file, `tar --list`,
   `tar --test-label`, manifest reads) are invoked THE SYSTEM SHALL NOT require
   elevation unless the archive itself is not readable to the calling user, in
   which case the SYSTEM SHALL use elevation transparently.
