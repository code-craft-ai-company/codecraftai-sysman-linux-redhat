# Requirements

## Introduction

The NFS server management feature provides the
`automojo-sysman-linux-redhat` package with the ability to manage
a local NFS v3/v4 server on Red Hat Linux systems.  It covers
`/etc/exports` and `/etc/exports.d/*.exports` management,
`exportfs` reloads, NFSv4 idmapping configuration
(`/etc/idmapd.conf`), server-side configuration via
`/etc/nfs.conf`, and basic server-state queries (`nfsstat`,
`showmount`).

All system commands are executed through the `automojo-interfaces`
command-runner abstraction.  All mutating operations respect
dry-run mode.

This feature complements — and does not duplicate — the
`nfs-server.service` lifecycle owned by `3-service-management`,
the RPC-port firewall surface owned by `3-firewall-management`, and
the NFS-*client*-side mount surface owned by
`3-filesystem-management`.

---

## Requirements

### Requirement 1: List and Query Exports

**User Story:** As a maintenance coordinator, I want to list
active and configured NFS exports so that I can compare against
the declared desired state.

#### Acceptance Criteria

1. WHEN the coordinator lists active exports THE SYSTEM SHALL
   invoke `exportfs -v` and parse the output into
   `list[ExportEntry]` records with `path`, `clients: list[
   ClientSpec]`.
2. WHEN the coordinator lists configured exports THE SYSTEM SHALL
   read `/etc/exports` plus every file under `/etc/exports.d/` and
   return `list[ExportEntry]` with source-path annotation.
3. WHEN the coordinator lists client mounts against the server THE
   SYSTEM SHALL invoke `showmount -e localhost` and return the
   parsed client→export map.

---

### Requirement 2: Add, Update, and Remove Exports

**User Story:** As a maintenance coordinator, I want to add,
update, and remove NFS exports so that the declared share set
matches the desired state.

#### Acceptance Criteria

1. WHEN the coordinator adds an export THE SYSTEM SHALL append an
   export line to a named drop-in file under `/etc/exports.d/`
   (default file is `automojo.exports`), then run `exportfs -r`
   to reload.
2. WHEN an identical export line already exists in the drop-in
   THE SYSTEM SHALL return `SKIPPED`.
3. WHEN the coordinator updates an export (same path, different
   client spec) THE SYSTEM SHALL replace the matching line within
   the drop-in rather than appending, then `exportfs -r`.
4. WHEN the coordinator removes an export THE SYSTEM SHALL strip
   every line whose path matches the requested export, then
   `exportfs -r`; SKIPPED when no lines match.
5. WHEN `/etc/exports.d/automojo.exports` becomes empty after
   removal THE SYSTEM SHALL `rm -f` the file.

---

### Requirement 3: Reload and Restart

**User Story:** As a maintenance coordinator, I want to reload or
re-export the NFS server, so that configuration changes take
effect.

#### Acceptance Criteria

1. WHEN the coordinator requests a reload THE SYSTEM SHALL invoke
   `exportfs -r` (re-export all tables).
2. WHEN the coordinator requests `unexport_all` THE SYSTEM SHALL
   invoke `exportfs -ua`.
3. WHEN the coordinator requests `restart` THE SYSTEM SHALL
   delegate to the injected `ServiceManager` for
   `nfs-server.service`; this feature does NOT re-spell
   `systemctl`.

---

### Requirement 4: NFSv4 Idmapping

**User Story:** As a maintenance coordinator, I want to set the
NFSv4 idmap domain so that uid/gid translation between server and
clients is deterministic.

#### Acceptance Criteria

1. WHEN the coordinator sets the idmap domain THE SYSTEM SHALL
   upsert `Domain = <value>` in `[General]` of `/etc/idmapd.conf`;
   SKIPPED when unchanged.
2. WHEN the coordinator queries the current idmap domain THE
   SYSTEM SHALL return `None` when the directive is absent,
   otherwise the current string.
3. WHEN idmapping changes require a daemon reload THE SYSTEM SHALL
   instruct callers via the returned `NfsActionResult.detail` that
   an `nfs-idmapd.service` restart (via `3-service-management`) is
   required for the change to take effect.

---

### Requirement 5: `/etc/nfs.conf` Directives

**User Story:** As a maintenance coordinator, I want to set a
subset of `/etc/nfs.conf` directives so that server behaviour
(threads, UDP/TCP, debug levels) aligns with the desired state.

#### Acceptance Criteria

1. WHEN the coordinator sets a directive THE SYSTEM SHALL upsert
   `<key> = <value>` in the named `[<section>]` block of
   `/etc/nfs.conf`, preserving every other line.
2. WHEN the directive already matches THE SYSTEM SHALL return
   `SKIPPED`.
3. WHEN the coordinator removes a directive THE SYSTEM SHALL strip
   the matching line; SKIPPED when already absent.
4. WHEN the coordinator queries directives THE SYSTEM SHALL return
   a `dict[str, dict[str, str]]` mapping section → key → value.

---

### Requirement 6: Statistics

**User Story:** As a maintenance coordinator, I want to query
server statistics so that reports can capture load.

#### Acceptance Criteria

1. WHEN the coordinator queries statistics THE SYSTEM SHALL invoke
   `nfsstat -s` and return the raw lines, along with a parsed
   `NfsServerStats` record containing `rpc_calls`, `rpc_badcalls`,
   `nfs_v3_op_counts: dict[str, int]`, `nfs_v4_op_counts: dict[
   str, int]`.

---

### Requirement 7: Dry-Run, Elevation, and Error Reporting

Standard dry-run / elevation / failure semantics.

#### Acceptance Criteria

1. WHEN `config.dry_run=True` THE SYSTEM SHALL NOT mutate
   `/etc/exports.d/*`, `/etc/idmapd.conf`, `/etc/nfs.conf`, nor
   dispatch `exportfs -r` / `exportfs -ua`.
2. WHEN `config.elevated=True` THE SYSTEM SHALL dispatch mutating
   commands elevated.
3. WHEN the runner returns non-zero THE SYSTEM SHALL return
   `NfsActionResult(status=FAILED, exit_code=<code>,
   stderr=<captured>)`.
