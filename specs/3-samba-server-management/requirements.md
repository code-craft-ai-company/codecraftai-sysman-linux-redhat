# Requirements

## Introduction

The Samba server management feature provides the
`codecraftai-sysman-linux-redhat` package with the ability to manage
a local Samba (SMB/CIFS) file server on Red Hat Linux systems.
It covers `smb.conf` management, share CRUD, Samba-user password
management via `smbpasswd`, configuration validation via
`testparm`, and AD-domain join via `net ads join`.

All system commands (`testparm`, `smbpasswd`, `net`, `wbinfo`,
`pdbedit`) are executed through the `codecraftai-interfaces`
command-runner abstraction.  `/etc/samba/smb.conf` and drop-ins
under `/etc/samba/smb.conf.d/` route through the runner
(`cat`/`tee`).  All mutating operations respect dry-run mode.

This feature complements — and does not duplicate — the
`smb.service` / `nmb.service` / `winbind.service` lifecycle owned
by `3-service-management`, the SMB-port firewall surface owned by
`3-firewall-management`, the identity-integration surface owned
by `3-identity-management`, and the local-account surface owned by
`3-user-group-management`.  Samba-specific user password storage
is the concern of this feature; local POSIX passwords remain with
user-group-management.

---

## Requirements

### Requirement 1: Global Configuration Directives

**User Story:** As a maintenance coordinator, I want to set
directives in the `[global]` section of `smb.conf`, so that the
server's security, naming, and protocol posture aligns with the
declared desired state.

#### Acceptance Criteria

1. WHEN the coordinator sets a global directive THE SYSTEM SHALL
   upsert `<key> = <value>` within the `[global]` section of
   `/etc/samba/smb.conf`, preserving every other section and
   comment.
2. WHEN the directive already matches THE SYSTEM SHALL return
   `SKIPPED`.
3. WHEN the coordinator removes a directive THE SYSTEM SHALL
   strip the matching line; SKIPPED when already absent.
4. WHEN the coordinator queries global directives THE SYSTEM SHALL
   return `dict[str, str]`.

---

### Requirement 2: Share CRUD

**User Story:** As a maintenance coordinator, I want to define,
update, query, list, and remove Samba shares so that the declared
share set matches the desired state.

#### Acceptance Criteria

1. WHEN the coordinator defines a share THE SYSTEM SHALL upsert a
   `[<share>]` block in `/etc/samba/smb.conf` carrying every
   directive in the supplied `ShareSpec`.  SKIPPED when the block
   already contains the requested directives (even if others are
   also present).
2. WHEN the coordinator removes a share THE SYSTEM SHALL strip the
   entire `[<share>]` block from `/etc/samba/smb.conf`; SKIPPED
   when absent.
3. WHEN the coordinator queries a share THE SYSTEM SHALL return a
   `ShareInfo` record with every directive parsed, or `None` when
   the share does not exist.
4. WHEN the coordinator lists shares THE SYSTEM SHALL return
   `list[ShareInfo]` for every section whose name is not
   `[global]`, `[homes]`, or `[printers]` (the standard reserved
   names).
5. WHEN the coordinator updates a share THE SYSTEM SHALL replace
   the directive set of the existing block atomically (one file
   write).

---

### Requirement 3: Validate Configuration

**User Story:** As a maintenance coordinator, I want to validate
the current `smb.conf` before reloading, so that a syntax error
does not cause the server to fail on reload.

#### Acceptance Criteria

1. WHEN the coordinator requests validation THE SYSTEM SHALL
   invoke `testparm -s --suppress-prompt` and return a
   `SambaValidateResult` with `ok: bool`, `exit_code`, `stderr`,
   `warnings: list[str]`, and `parsed_sections: list[str]`.
2. WHEN validation fails THE SYSTEM SHALL NOT dispatch a reload
   (the reload method validates first).

---

### Requirement 4: Reload and Restart

**User Story:** As a maintenance coordinator, I want to reload the
Samba server so that configuration changes take effect.

#### Acceptance Criteria

1. WHEN the coordinator requests a reload THE SYSTEM SHALL first
   call `validate_config()`; on failure, return the
   `SambaActionResult(status=FAILED)` without touching the
   service.
2. WHEN validation passes THE SYSTEM SHALL delegate to the
   injected `ServiceManager` to reload `smb.service` (and
   optionally `nmb.service`).
3. WHEN `winbind` integration is in use THE SYSTEM SHALL also
   delegate a restart of `winbind.service`.

---

### Requirement 5: Samba-User Password Management

**User Story:** As a maintenance coordinator, I want to manage
Samba user passwords via `smbpasswd`, so that SMB access is
authorised for declared accounts.

#### Acceptance Criteria

1. WHEN the coordinator adds a Samba user THE SYSTEM SHALL invoke
   `smbpasswd -a -s <username>` with the password supplied via
   runner `stdin` (never argv), delivered twice (old then new, per
   the tool's prompt order when `-s` is used).
2. WHEN the coordinator deletes a Samba user THE SYSTEM SHALL
   invoke `smbpasswd -x <username>`.
3. WHEN the coordinator enables or disables an account THE SYSTEM
   SHALL invoke `smbpasswd -e <username>` / `smbpasswd -d
   <username>`.
4. WHEN the coordinator lists Samba users THE SYSTEM SHALL invoke
   `pdbedit -L` and parse the output into `list[SambaUserInfo]`.
5. THE SYSTEM SHALL NEVER pass plain-text passwords on argv.  In
   all code paths the password travels on `stdin`.

---

### Requirement 6: Active Directory Join

**User Story:** As a maintenance coordinator, I want to join the
Samba server to an Active Directory domain so that it can
authenticate SMB clients against AD.

#### Acceptance Criteria

1. WHEN the coordinator joins a domain THE SYSTEM SHALL invoke
   `net ads join -U <user>%<password>` — EXCEPT that `<password>`
   SHALL NOT appear on argv.  The implementation SHALL use the
   interactive form `net ads join -U <user>` with the password
   supplied via runner `stdin`.
2. WHEN the coordinator tests domain membership THE SYSTEM SHALL
   invoke `net ads testjoin` and return `bool`.
3. WHEN the coordinator leaves a domain THE SYSTEM SHALL invoke
   `net ads leave -U <user>` with the password via `stdin`.
4. WHEN the coordinator lists trusted domains THE SYSTEM SHALL
   invoke `wbinfo --trusted-domains` and return `list[str]`.
5. WHEN the underlying directory-management operations are already
   performed by `3-identity-management` (via `realm join`) THE
   SYSTEM SHALL expose `net ads` as a separate entry point so
   callers can choose the Samba-native path when appropriate.

---

### Requirement 7: Dry-Run, Elevation, and Error Reporting

Standard dry-run / elevation / failure semantics.

#### Acceptance Criteria

1. WHEN `config.dry_run=True` THE SYSTEM SHALL NOT mutate
   `smb.conf`, NOT dispatch `smbpasswd` / `net ads` / reload
   calls, and SHALL return `SambaActionResult(status=DRY_RUN)`.
2. WHEN `config.elevated=True` THE SYSTEM SHALL dispatch mutating
   commands elevated.
3. WHEN the runner returns non-zero THE SYSTEM SHALL return
   `SambaActionResult(status=FAILED, exit_code=<code>,
   stderr=<captured>)`.
4. THE SYSTEM SHALL NEVER include passphrase material in the
   recorded `command` field on any returned result.
