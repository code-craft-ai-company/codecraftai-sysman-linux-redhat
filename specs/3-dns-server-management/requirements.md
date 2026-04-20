# Requirements

## Introduction

The DNS server management feature provides the
`automojo-sysman-linux-redhat` package with the ability to manage
a local DNS server on Red Hat Linux systems.  It supports three
backends — BIND (`named`), Unbound, and dnsmasq — and exposes a
common surface for config validation, zone CRUD (BIND), forwarder
configuration (Unbound / dnsmasq), and runtime control.

All system commands (`named-checkconf`, `named-checkzone`, `rndc`,
`unbound-checkconf`, `unbound-control`, `dnsmasq --test`) are
executed through the `automojo-interfaces` command-runner
abstraction.  Config reads and writes route through the runner
(`cat`/`tee`).  All mutating operations respect dry-run mode.

This feature complements — and does not duplicate — the DNS
*resolver* configuration (`/etc/resolv.conf`, `systemd-resolved`)
owned by `3-network-configuration`, the service lifecycle owned by
`3-service-management`, and the firewall-port surface owned by
`3-firewall-management`.

---

## Requirements

### Requirement 1: Backend Selection

**User Story:** As a maintenance coordinator, I want to declare
which DNS server is in use so that this feature dispatches the
right tool family.

#### Acceptance Criteria

1. THE SYSTEM SHALL accept a `DnsServerBackend` enum value (
   `BIND`, `UNBOUND`, `DNSMASQ`) at construction time.
2. WHEN no backend is supplied THE SYSTEM SHALL infer it by
   probing the presence of `named`, `unbound`, and `dnsmasq`
   binaries (in that order) via `command -v <name>`; if none
   are found, raise `DnsBackendNotAvailableError`.
3. WHEN `config.backend == BIND` THE SYSTEM SHALL dispatch
   `named-checkconf` / `named-checkzone` / `rndc`.
4. WHEN `config.backend == UNBOUND` THE SYSTEM SHALL dispatch
   `unbound-checkconf` / `unbound-control`.
5. WHEN `config.backend == DNSMASQ` THE SYSTEM SHALL dispatch
   `dnsmasq --test` and manage `/etc/dnsmasq.conf` and
   `/etc/dnsmasq.d/*`.

---

### Requirement 2: Config Validation

**User Story:** As a maintenance coordinator, I want to validate
the current DNS server configuration before requesting a reload.

#### Acceptance Criteria

1. WHEN the coordinator requests validation THE SYSTEM SHALL
   dispatch the backend-specific validator and return a
   `DnsValidateResult` with `ok: bool`, `exit_code`, `stderr`.
2. WHEN a specific zone file is provided (BIND) THE SYSTEM SHALL
   additionally invoke `named-checkzone <zone> <file>` and
   aggregate the result into `DnsValidateResult`.

---

### Requirement 3: BIND Zone Management

**User Story:** As a maintenance coordinator, I want to write,
update, query, and remove BIND zone files and their references in
`/etc/named.conf` (or `/etc/named/*.conf`).

#### Acceptance Criteria

1. WHEN the coordinator writes a zone file THE SYSTEM SHALL
   write to `/var/named/<zone>.db` (or the configured path) via
   `tee`; idempotent via pre-read compare.
2. WHEN the coordinator declares a zone in a `named.conf`-style
   drop-in THE SYSTEM SHALL write `zone "<name>" { type ...;
   file "<path>"; ...; };` into a named drop-in under
   `/etc/named/zones.d/` (or caller-specified directory);
   idempotent.
3. WHEN the coordinator removes a zone THE SYSTEM SHALL strip the
   matching zone block from its drop-in AND `rm -f` the
   zone-data file.
4. WHEN the coordinator queries a zone THE SYSTEM SHALL return a
   `ZoneEntry` record with `name`, `type`, `file_path`,
   `file_content`, `source_conf`.
5. WHEN a mutation succeeds THE SYSTEM SHALL invoke `rndc reload`
   to take the change live.

---

### Requirement 4: Unbound Forwarder and Stub Zones

**User Story:** As a maintenance coordinator, I want to add /
remove forwarder zones and stub zones in Unbound.

#### Acceptance Criteria

1. WHEN the coordinator adds a forwarder THE SYSTEM SHALL upsert
   a `forward-zone:` block (`name:`, `forward-addr:` per server)
   in a drop-in under `/etc/unbound/conf.d/`; idempotent.
2. WHEN the coordinator adds a stub zone THE SYSTEM SHALL upsert
   a `stub-zone:` block.
3. WHEN the coordinator removes a forwarder or stub THE SYSTEM
   SHALL strip the matching block; SKIPPED when absent.
4. WHEN a mutation succeeds THE SYSTEM SHALL invoke
   `unbound-control reload`.

---

### Requirement 5: dnsmasq Static / Forwarder Records

**User Story:** As a maintenance coordinator, I want to configure
dnsmasq with static hosts, address records, forwarders, and DHCP
ranges.

#### Acceptance Criteria

1. WHEN the coordinator adds a static mapping THE SYSTEM SHALL
   upsert `address=/<host>/<ip>` or `host-record=<host>,<ip>` in
   a drop-in under `/etc/dnsmasq.d/`; idempotent.
2. WHEN the coordinator adds a forwarder for a domain THE SYSTEM
   SHALL upsert `server=/<domain>/<ip>` in the same drop-in
   style.
3. WHEN a mutation succeeds THE SYSTEM SHALL delegate a
   `dnsmasq.service` reload to the injected `ServiceManager`
   (dnsmasq does not support runtime reload via signal-free
   tooling; restart is the conventional path).

---

### Requirement 6: Runtime Control

**User Story:** As a maintenance coordinator, I want a single
method to reload the DNS server, regardless of backend.

#### Acceptance Criteria

1. WHEN the coordinator calls `reload()` THE SYSTEM SHALL first
   `validate()` and return `FAILED` without touching the server
   if validation fails.
2. WHEN validation passes THE SYSTEM SHALL invoke the
   backend-appropriate reload (`rndc reload` for BIND,
   `unbound-control reload` for Unbound, delegate restart for
   dnsmasq).
3. WHEN the coordinator calls `restart()` THE SYSTEM SHALL
   delegate to the injected `ServiceManager`.

---

### Requirement 7: Status and Stats

**User Story:** As a maintenance coordinator, I want to query
server status.

#### Acceptance Criteria

1. WHEN the coordinator queries status THE SYSTEM SHALL dispatch
   the backend-appropriate status command (`rndc status`,
   `unbound-control status`, `systemctl is-active dnsmasq` via
   the ServiceManager) and return a `DnsServerStatus` record
   (`running: bool`, `version: str | None`,
   `raw: str`).

---

### Requirement 8: Dry-Run, Elevation, and Error Reporting

Standard dry-run / elevation / failure semantics.

#### Acceptance Criteria

1. WHEN `config.dry_run=True` THE SYSTEM SHALL NOT dispatch any
   mutating command and SHALL NOT rewrite any zone / conf file.
2. WHEN `config.elevated=True` THE SYSTEM SHALL dispatch mutating
   commands elevated.
3. WHEN the runner returns non-zero THE SYSTEM SHALL return
   `DnsActionResult(status=FAILED, exit_code=<code>,
   stderr=<captured>)`.
