# Requirements

## Introduction

The remote log forwarding feature provides the
`codecraftai-sysman-linux-redhat` package with the ability to ship
logs off-host, as the counterpart to the existing local-journal +
logrotate surface in `3-log-management`.  It covers rsyslog
forward rules (TCP + TLS), `systemd-journal-upload`, and the
`journald.conf` `RemoteForward`-style directives.

All system commands (`rsyslogd -N1`, `systemctl` via the injected
`ServiceManager`, `journalctl --verify-key`) are executed through
the `codecraftai-interfaces` command-runner abstraction.  Config
reads and writes (`/etc/rsyslog.conf`, `/etc/rsyslog.d/*`,
`/etc/systemd/journal-upload.conf`, `/etc/systemd/journald.conf`)
route through the runner (`cat`/`tee`).  All mutating operations
respect dry-run mode.

This feature complements — and does not duplicate — the
`3-log-management` local surface and the
`3-certificate-trust-management` / `3-certificate-issuance` TLS
material surfaces.

---

## Requirements

### Requirement 1: rsyslog Forward Rules (Plain + TLS)

**User Story:** As a maintenance coordinator, I want to declare
rsyslog forward rules that ship local syslog traffic to one or
more remote collectors, optionally over TLS.

#### Acceptance Criteria

1. WHEN the coordinator adds a forward rule THE SYSTEM SHALL
   write or update a drop-in under `/etc/rsyslog.d/<name>.conf`
   containing the canonical `action(type="omfwd" target="<host>"
   port="<port>" protocol="<tcp|udp>" [...StreamDriver...])`
   construct.
2. WHEN the rule is TLS-enabled THE SYSTEM SHALL include
   `StreamDriver="gtls"`, `StreamDriverMode="1"`, and
   `StreamDriverAuthMode="<auth_mode>"` (defaulting to `"x509/name"`
   with `permittedPeer=...`).
3. WHEN the requested rule content already matches the drop-in
   THE SYSTEM SHALL return `SKIPPED`.
4. WHEN the coordinator removes a forward rule THE SYSTEM SHALL
   delete the drop-in; SKIPPED when already absent.
5. WHEN a mutation succeeds THE SYSTEM SHALL `rsyslogd -N1` to
   validate; on failure return `FAILED` without delegating a
   service reload.
6. WHEN validation passes THE SYSTEM SHALL delegate
   `rsyslog.service` reload via the injected `ServiceManager`.

---

### Requirement 2: rsyslog Forward-Queue Configuration

**User Story:** As a maintenance coordinator, I want to set
queue-on-disk parameters on a forward rule so that logs are not
lost during transient collector outages.

#### Acceptance Criteria

1. WHEN the coordinator sets queue parameters on a rule THE
   SYSTEM SHALL include `queue.type="LinkedList"`,
   `queue.filename="<prefix>"`, `queue.spoolDirectory="<path>"`,
   `queue.maxDiskSpace="<bytes>"`, and `queue.saveOnShutdown="on"`
   in the `action()` construct.
2. WHEN the spool directory does not exist THE SYSTEM SHALL
   invoke `install -d -o syslog -g syslog -m 0700 <path>` (via
   the runner) before attempting the service reload.

---

### Requirement 3: systemd-journal-upload

**User Story:** As a maintenance coordinator, I want to configure
`systemd-journal-upload` to push the local journal to a
`systemd-journal-remote` collector.

#### Acceptance Criteria

1. WHEN the coordinator sets the upload URL THE SYSTEM SHALL
   upsert `URL=<url>` in the `[Upload]` section of
   `/etc/systemd/journal-upload.conf`; SKIPPED when unchanged.
2. WHEN the coordinator sets TLS material THE SYSTEM SHALL upsert
   the `ServerKeyFile`, `ServerCertificateFile`, and
   `TrustedCertificateFile` directives.  This feature does NOT
   create these files — caller provides them, typically via
   `3-certificate-issuance` / `3-certificate-trust-management`.
3. WHEN the coordinator enables the upload service THE SYSTEM
   SHALL delegate to
   `service_manager.enable("systemd-journal-upload")` with
   immediate start.
4. WHEN the coordinator queries upload config THE SYSTEM SHALL
   return a `dict[str, str]` parsed from the `[Upload]` section.

---

### Requirement 4: journald.conf `ForwardTo*` and Remote Settings

**User Story:** As a maintenance coordinator, I want to set
`journald.conf` directives that govern whether journal records are
also forwarded to syslog, wall, kmsg, or console.

#### Acceptance Criteria

1. WHEN the coordinator sets `ForwardToSyslog`,
   `ForwardToKMsg`, `ForwardToConsole`, or `ForwardToWall` THE
   SYSTEM SHALL upsert the matching directive in `[Journal]` of
   `/etc/systemd/journald.conf` (or a drop-in under
   `/etc/systemd/journald.conf.d/`); SKIPPED when unchanged.
2. WHEN the mutation succeeds THE SYSTEM SHALL note in the
   result's `detail` that `systemd-journald.service` must be
   restarted via `3-service-management` for the change to take
   effect.  This feature does NOT auto-restart journald because a
   restart loses in-memory journal records.
3. WHEN the coordinator queries journald directives THE SYSTEM
   SHALL return a `dict[str, str]`.

---

### Requirement 5: Connection Health Probe

**User Story:** As a maintenance coordinator, I want a simple
health probe that verifies the collector is reachable before
declaring the change complete.

#### Acceptance Criteria

1. WHEN the coordinator calls `probe_collector(host, port,
   tls=False)` THE SYSTEM SHALL invoke either
   `openssl s_client -connect <host>:<port> -verify 1` (TLS) or
   `timeout 5 bash -c 'cat < /dev/tcp/<host>/<port>'` (non-TLS)
   through the runner and return a `LogForwardingHealth` record
   with `reachable: bool`, `tls_verified: bool | None`, `raw`.
2. THE SYSTEM SHALL NOT fail hard when the probe times out;
   `reachable=False` is the normal signal.

---

### Requirement 6: Dry-Run, Elevation, and Error Reporting

Standard dry-run / elevation / failure semantics.

#### Acceptance Criteria

1. WHEN `config.dry_run=True` THE SYSTEM SHALL NOT mutate any
   config file, NOT dispatch `rsyslogd -N1`, and NOT delegate
   any service reload / enable.
2. WHEN `config.elevated=True` THE SYSTEM SHALL dispatch mutating
   commands elevated.
3. WHEN the runner returns non-zero THE SYSTEM SHALL return
   `LogForwardingActionResult(status=FAILED, exit_code=<code>,
   stderr=<captured>)`.
