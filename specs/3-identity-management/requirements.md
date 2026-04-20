# Requirements

## Introduction

The identity management feature provides the
`automojo-sysman-linux-redhat` package with the ability to integrate
Red Hat Linux systems with enterprise identity services: Active
Directory, FreeIPA / Red Hat Identity Management, generic LDAP, and
Kerberos.  It covers domain discovery and join, SSSD configuration,
the Kerberos client stack, and ticket lifecycle.

A maintenance coordinator uses this feature to bring a system into a
declared directory-membership state.  All commands (`realm`,
`adcli`, `sssctl`, `kinit`, `klist`, `kdestroy`, `sss_cache`) are
executed through the `automojo-interfaces` command-runner abstraction.
All reads and writes against `/etc/sssd/sssd.conf`,
`/etc/sssd/conf.d/*.conf`, `/etc/krb5.conf`, `/etc/krb5.conf.d/*`,
and `/etc/nsswitch.conf` route through the runner (`cat`/`tee`).
All mutating operations respect dry-run mode.

This feature complements — and does not duplicate — the local PAM
and NSS surfaces owned by `3-authentication-management`, the
local-account surface owned by `3-user-group-management`, and the
CA-trust surface owned by `3-certificate-trust-management`.
Credentials SHALL route through `automojo-credentials` rather than
being accepted as plain-text function arguments wherever practical.

---

## Requirements

### Requirement 1: Domain Discovery and Membership Query

**User Story:** As a maintenance coordinator, I want to discover
directory domains reachable from the system and to query the
current join state, so that I can decide whether a join / leave
action is necessary.

#### Acceptance Criteria

1. WHEN the coordinator requests discovery THE SYSTEM SHALL invoke
   `realm discover <domain>` and return a `RealmDiscovery` record
   with `domain`, `type` (`"kerberos"`, `"kerberos-member"`,
   `"ipa"`), `realm_name`, `domain_controllers: list[str]`, and the
   raw output.
2. WHEN the coordinator requests the current realm list THE SYSTEM
   SHALL invoke `realm list` and return a `list[RealmMembership]`
   records describing each joined realm (`domain`, `realm_name`,
   `configured`, `server_software`, `client_software`,
   `required_package`).
3. WHEN no realms are joined THE SYSTEM SHALL return an empty list
   without raising.
4. WHEN the `realm` tool is absent THE SYSTEM SHALL raise
   `IdentityToolNotAvailableError` with a hint that the
   `realmd` RPM must be installed.

---

### Requirement 2: Join and Leave an Active Directory or FreeIPA Realm

**User Story:** As a maintenance coordinator, I want to join or
leave an AD / IPA realm, so that the system participates in the
declared directory.

#### Acceptance Criteria

1. WHEN the coordinator requests a join THE SYSTEM SHALL invoke
   `realm join <domain>` with the supplied credential.  Credentials
   SHALL be passed via an `SshCredential`-style abstraction from
   `automojo-credentials` (or an equivalent credential object) and
   forwarded to `realm join` through `stdin` (`--one-time-password`
   or `-U <user>` + `stdin` password) — never on argv.
2. WHEN the system is already joined to the requested realm THE
   SYSTEM SHALL return `IdentityActionResult(status=SKIPPED)`.
3. WHEN the coordinator requests a leave THE SYSTEM SHALL invoke
   `realm leave [<domain>]`; SKIPPED when the system is not
   joined.
4. WHEN `dry_run=True` THE SYSTEM SHALL NOT contact the domain
   controller and SHALL return `DRY_RUN`.
5. WHEN the operation fails THE SYSTEM SHALL surface the runner's
   `exit_code` and `stderr` on an `IdentityActionResult` with status
   `FAILED`.

---

### Requirement 3: SSSD Configuration

**User Story:** As a maintenance coordinator, I want to write and
query SSSD configuration files, so that declared domain
configuration persists and can be audited.

#### Acceptance Criteria

1. WHEN the coordinator writes a drop-in under `/etc/sssd/conf.d/`
   THE SYSTEM SHALL write via `tee` with `stdin`, preserve mode
   `0600`, and own it as `root:root`.
2. WHEN the coordinator queries a drop-in file THE SYSTEM SHALL
   return its parsed `dict[str, dict[str, str]]` section/key map or
   `None` when the file does not exist.
3. WHEN the coordinator updates a directive in an existing section
   THE SYSTEM SHALL preserve every other section, directive, and
   comment in the file.
4. WHEN the coordinator removes a directive THE SYSTEM SHALL return
   `SKIPPED` when the directive is already absent.
5. WHEN the coordinator requests a configuration validation THE
   SYSTEM SHALL invoke `sssctl config-check` and return
   `SssdConfigCheckResult(ok, exit_code, stderr, warnings)`.

---

### Requirement 4: SSSD Runtime Operations

**User Story:** As a maintenance coordinator, I want to manage the
SSSD service lifecycle and clear its caches, so that configuration
changes take effect and stale data does not linger.

#### Acceptance Criteria

1. WHEN the coordinator restarts SSSD THE SYSTEM SHALL delegate to
   `3-service-management`'s `ServiceManager.restart("sssd")` rather
   than re-spelling `systemctl` locally.
2. WHEN the coordinator invalidates the SSSD cache THE SYSTEM SHALL
   invoke `sss_cache -E` (or `-u <user>` / `-g <group>` when a
   target is supplied).
3. WHEN the coordinator queries SSSD status THE SYSTEM SHALL invoke
   `sssctl domain-list` and `sssctl domain-status <domain>` and
   return an `SssdStatus` record.

---

### Requirement 5: Kerberos Client Configuration (`krb5.conf`)

**User Story:** As a maintenance coordinator, I want to configure
the Kerberos client (default realm, KDC list, domain-to-realm
mapping), so that authentication against the declared KDCs works.

#### Acceptance Criteria

1. WHEN the coordinator writes a `krb5.conf.d` drop-in THE SYSTEM
   SHALL route through `tee` and follow the same idempotency rules
   as SSSD drop-ins.
2. WHEN the coordinator sets the default realm in
   `/etc/krb5.conf` THE SYSTEM SHALL rewrite only the
   `[libdefaults].default_realm` directive, preserving all other
   directives.
3. WHEN the coordinator adds or removes a KDC for a realm THE
   SYSTEM SHALL upsert / strip the `kdc = <host>` line within the
   `[realms]` / `<REALM>` block.
4. WHEN the coordinator queries the Kerberos configuration THE
   SYSTEM SHALL return a `KrbClientConfig` record with
   `default_realm`, `realms: dict[str, list[str]]` (realm → KDCs),
   and `domain_realm: dict[str, str]`.

---

### Requirement 6: Kerberos Ticket Lifecycle

**User Story:** As a maintenance coordinator, I want to obtain,
inspect, and destroy Kerberos tickets, so that maintenance tasks
requiring a ticket can acquire one on demand without shelling out.

#### Acceptance Criteria

1. WHEN the coordinator requests a ticket THE SYSTEM SHALL invoke
   `kinit <principal>` with the password supplied via `stdin`
   through the runner's `stdin` channel (never argv); keytab-based
   ticket acquisition SHALL use `kinit -kt <keytab> <principal>`.
2. WHEN the coordinator inspects current tickets THE SYSTEM SHALL
   invoke `klist` and return a `list[KrbTicket]` records with
   `principal`, `starts`, `expires`, `renewable_until`,
   `service`.
3. WHEN no ticket cache exists THE SYSTEM SHALL return an empty
   list rather than raising.
4. WHEN the coordinator destroys tickets THE SYSTEM SHALL invoke
   `kdestroy [-A]`.

---

### Requirement 7: NSS Source Ordering Seam

**User Story:** As a maintenance coordinator, I want a narrow,
declared boundary with `3-authentication-management` around
`/etc/nsswitch.conf`, so that AD / IPA join flows that ordinarily
adjust NSS ordering do not silently fight with a direct
`set_nss_sources` call.

#### Acceptance Criteria

1. WHEN `realm join` completes it typically adjusts
   `/etc/nsswitch.conf` as a side-effect.  THE SYSTEM SHALL expose a
   convenience method `ensure_nss_ordering(databases, sources)` that
   delegates to `3-authentication-management.set_nss_sources`, so
   a single well-defined code path owns nsswitch edits.
2. THE SYSTEM SHALL document this seam prominently in
   `design.md` so callers understand not to drive
   `authentication-management.set_nss_sources` in parallel with a
   `realm join` in the same maintenance run.

---

### Requirement 8: Dry-Run, Elevation, and Credential Handling

**User Story:** As a maintenance coordinator, I want every mutating
operation to respect dry-run, run with declared elevation, surface
failures structurally, and handle credentials through a secure
abstraction.

#### Acceptance Criteria

1. WHEN `config.dry_run=True` THE SYSTEM SHALL NOT contact any KDC
   or domain controller and SHALL NOT dispatch any mutation;
   read-only lookups (`realm discover`, `klist`) MAY still run.
2. WHEN `config.elevated=True` THE SYSTEM SHALL dispatch mutating
   commands with `elevated=True` to the runner.
3. THE SYSTEM SHALL NEVER pass plain-text passwords on the argument
   vector.  Password payloads MUST be routed via runner `stdin`.
4. WHEN the runner returns a non-zero exit code THE SYSTEM SHALL
   return `IdentityActionResult(status=FAILED, exit_code=<code>,
   stderr=<captured>)`.
5. WHEN a required credential is absent THE SYSTEM SHALL raise
   `IdentityCredentialError` (a `SemanticError` subtype) with a
   descriptive message; never shell out with an empty password.
