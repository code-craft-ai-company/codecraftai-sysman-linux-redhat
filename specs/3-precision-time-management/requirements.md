# Requirements

## Introduction

The precision time management feature provides the
`codecraftai-sysman-linux-redhat` package with the ability to manage
the IEEE 1588 Precision Time Protocol (PTP) stack on Red Hat Linux
systems.  It covers `ptp4l` (the PTP daemon), `phc2sys` (PHC ↔
system-clock synchronisation), and `pmc` (PTP management client).

All system commands (`ptp4l`, `phc2sys`, `pmc`) are executed
through the `codecraftai-interfaces` command-runner abstraction.
Config reads and writes (`/etc/linuxptp/ptp4l.conf`,
`/etc/sysconfig/ptp4l`, `/etc/sysconfig/phc2sys`) route through
the runner (`cat`/`tee`).  All mutating operations respect dry-run
mode.

This feature is parallel to — and does not duplicate — the
Chrony/NTP surface owned by `3-network-configuration`.  NTP and
PTP are distinct protocols for distinct precision regimes.  Service
lifecycle is delegated to an injected `ServiceManager`.

---

## Requirements

### Requirement 1: ptp4l.conf Management

**User Story:** As a maintenance coordinator, I want to set and
query directives in `ptp4l.conf` so that the PTP daemon uses the
declared domain, transport, delay mechanism, and priority.

#### Acceptance Criteria

1. WHEN the coordinator queries directives THE SYSTEM SHALL parse
   `/etc/linuxptp/ptp4l.conf` into `dict[str, dict[str, str]]`
   keyed by section (e.g. `[global]`, `[<interface>]`).
2. WHEN the coordinator sets a directive THE SYSTEM SHALL upsert
   `<key> <value>` in the named section (note: ptp4l uses
   whitespace-separated `key value`, not `key = value`);
   SKIPPED when unchanged.
3. WHEN the coordinator removes a directive THE SYSTEM SHALL
   strip the matching line; SKIPPED when already absent.
4. THE SYSTEM SHALL preserve every other section, directive, and
   comment.

---

### Requirement 2: Common ptp4l Preset Helpers

**User Story:** As a maintenance coordinator, I want helpers for
the common high-level knobs — domain number, transport, delay
mechanism, interface list — so that the common intent can be
expressed without hand-editing sections.

#### Acceptance Criteria

1. WHEN the coordinator sets the PTP domain THE SYSTEM SHALL
   upsert `domainNumber <n>` in `[global]`.
2. WHEN the coordinator sets the transport mode THE SYSTEM SHALL
   upsert `network_transport <L2|UDPv4|UDPv6>` in `[global]`.
3. WHEN the coordinator sets the delay mechanism THE SYSTEM SHALL
   upsert `delay_mechanism <E2E|P2P>` in `[global]`.
4. WHEN the coordinator sets the interface list THE SYSTEM SHALL
   add one section per interface (e.g. `[eth0]`), preserving
   per-interface overrides supplied by the caller, and remove any
   previously-managed interface sections not in the new list.

---

### Requirement 3: `/etc/sysconfig/ptp4l` and `/etc/sysconfig/phc2sys`

**User Story:** As a maintenance coordinator, I want to manage the
environment-style overrides that the systemd units for `ptp4l` and
`phc2sys` consume.

#### Acceptance Criteria

1. WHEN the coordinator sets `OPTIONS="..."` in either sysconfig
   file THE SYSTEM SHALL upsert the variable line; SKIPPED when
   unchanged.
2. WHEN the coordinator queries the current options THE SYSTEM
   SHALL return the stripped value or `None` when absent.
3. WHEN the mutation succeeds THE SYSTEM SHALL populate
   `result.detail` with a notice that the corresponding
   `.service` must be restarted via `3-service-management` for
   the change to take effect.

---

### Requirement 4: Service Lifecycle

**User Story:** As a maintenance coordinator, I want to start /
stop / enable / disable the `ptp4l.service` and `phc2sys.service`
units so that runtime posture matches the declared desired state.

#### Acceptance Criteria

1. WHEN the coordinator requests any lifecycle change THE SYSTEM
   SHALL delegate to the injected `ServiceManager` rather than
   re-spelling `systemctl`.
2. WHEN `phc2sys` is to run THE SYSTEM SHALL require that
   `ptp4l.service` is active or declared active-on-boot;
   otherwise return `FAILED` with a descriptive message noting
   the dependency.

---

### Requirement 5: Runtime Query via `pmc`

**User Story:** As a maintenance coordinator, I want to query the
running ptp4l daemon for offset, drift, and parent-clock
information.

#### Acceptance Criteria

1. WHEN the coordinator queries the clock quality THE SYSTEM SHALL
   invoke `pmc -u -b 0 "GET CURRENT_DATA_SET"` and return a
   `PtpCurrentDataSet` record with `steps_removed`,
   `offset_from_master_ns`, `mean_path_delay_ns`.
2. WHEN the coordinator queries the parent-port-identity THE
   SYSTEM SHALL invoke `pmc -u -b 0 "GET PARENT_DATA_SET"` and
   return a `PtpParentDataSet` record.
3. WHEN the coordinator requests a port-status snapshot THE
   SYSTEM SHALL invoke `pmc -u -b 0 "GET PORT_DATA_SET"` and
   return a `list[PtpPortDataSet]` (one per port).
4. WHEN the ptp4l daemon is not running THE SYSTEM SHALL return
   empty data and a `detail` string explaining the state, rather
   than raising.

---

### Requirement 6: `phc2sys` Posture

**User Story:** As a maintenance coordinator, I want to query and
set the high-level `phc2sys` posture — which PHC drives which
sink.

#### Acceptance Criteria

1. WHEN the coordinator queries `phc2sys` OPTIONS THE SYSTEM SHALL
   return the current `/etc/sysconfig/phc2sys` `OPTIONS` value.
2. WHEN the coordinator sets `phc2sys` to sync system clock from
   a PHC THE SYSTEM SHALL render `OPTIONS="-a -r"` (or a caller-
   supplied alternative) and delegate service reload hint via
   `result.detail`.

---

### Requirement 7: Dry-Run, Elevation, and Error Reporting

Standard dry-run / elevation / failure semantics.

#### Acceptance Criteria

1. WHEN `config.dry_run=True` THE SYSTEM SHALL NOT mutate any
   config file.
2. WHEN `config.elevated=True` THE SYSTEM SHALL dispatch mutating
   commands elevated.
3. WHEN the runner returns non-zero THE SYSTEM SHALL return
   `PtpActionResult(status=FAILED, exit_code=<code>,
   stderr=<captured>)`.
