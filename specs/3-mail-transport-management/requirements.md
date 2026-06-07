# Requirements

## Introduction

The mail transport management feature provides the
`codecraftai-sysman-linux-redhat` package with the ability to manage a
local Postfix MTA on Red Hat Linux systems.  It covers
`/etc/postfix/main.cf` and `master.cf` read/write via `postconf`
and direct file edits, lookup-table map management (`postmap`),
`/etc/aliases` management (`newaliases`), and the mail-queue
lifecycle (`postqueue`, `mailq`, `postsuper`).

All system commands (`postconf`, `postmap`, `newaliases`,
`postqueue`, `postsuper`, `mailq`) are executed through the
`codecraftai-interfaces` command-runner abstraction.  Config reads
and writes route through the runner (`cat`/`tee`).  All mutating
operations respect dry-run mode.

This feature complements — and does not duplicate — the
`postfix.service` lifecycle owned by `3-service-management`, the
SMTP-port firewall surface owned by `3-firewall-management`, and
the TLS-material surface owned by
`3-certificate-trust-management` / `3-certificate-issuance`.

---

## Requirements

### Requirement 1: `main.cf` Directives

**User Story:** As a maintenance coordinator, I want to set and
query Postfix `main.cf` directives via `postconf`, so that the
server's routing, relay, and security posture aligns with the
declared state.

#### Acceptance Criteria

1. WHEN the coordinator queries a directive THE SYSTEM SHALL
   invoke `postconf -h <key>` and return the stripped value.
2. WHEN the coordinator queries all directives THE SYSTEM SHALL
   invoke `postconf` and parse `key = value` lines into
   `dict[str, str]`.
3. WHEN the coordinator sets a directive THE SYSTEM SHALL invoke
   `postconf -e "<key>=<value>"`; SKIPPED when current value
   matches.
4. WHEN the coordinator removes a directive (revert to default)
   THE SYSTEM SHALL invoke `postconf -X <key>`; SKIPPED when
   already absent.

---

### Requirement 2: `master.cf` Services

**User Story:** As a maintenance coordinator, I want to enable,
disable, or reconfigure named services in `master.cf` so that
Postfix submits, receives, or relays mail according to the declared
topology.

#### Acceptance Criteria

1. WHEN the coordinator queries a service THE SYSTEM SHALL invoke
   `postconf -Mh <service>/<type>` and return the parsed service
   line (or `None` when absent).
2. WHEN the coordinator defines or updates a service THE SYSTEM
   SHALL invoke `postconf -M
   "<service>/<type>=<service> <type> <private> <unpriv> <chroot>
   <wakeup> <maxproc> <command>"`; SKIPPED when the current line
   matches the requested one.
3. WHEN the coordinator sets a service-scoped directive
   (e.g. `submission/inet/smtpd_tls_security_level`) THE SYSTEM
   SHALL invoke `postconf -P
   "<service>/<type>/<key>=<value>"`; SKIPPED when unchanged.
4. WHEN the coordinator removes a service THE SYSTEM SHALL invoke
   `postconf -MX "<service>/<type>"`; SKIPPED when absent.

---

### Requirement 3: Lookup-Table Map Management

**User Story:** As a maintenance coordinator, I want to manage the
content and compiled `.db` for named maps (`transport`, `access`,
`virtual`, `canonical`, `generic`), so that Postfix routes mail
per the declared policy.

#### Acceptance Criteria

1. WHEN the coordinator writes a map file (plain text) THE SYSTEM
   SHALL write `/etc/postfix/<name>` via `tee`; idempotent via
   pre-read compare.
2. WHEN the coordinator compiles a map THE SYSTEM SHALL invoke
   `postmap /etc/postfix/<name>`; always runs after a map-file
   content change, SKIPPED when the `.db` timestamp post-dates the
   source's.
3. WHEN the coordinator queries a map entry THE SYSTEM SHALL
   invoke `postmap -q <key> /etc/postfix/<name>` and return the
   stripped value or `None` when absent.
4. WHEN the coordinator removes a map file THE SYSTEM SHALL `rm
   -f` both the source file and the compiled `.db`; SKIPPED when
   both are already absent.

---

### Requirement 4: `/etc/aliases`

**User Story:** As a maintenance coordinator, I want to manage
`/etc/aliases` entries and keep the compiled aliases database in
sync so that local mail is delivered per the declared policy.

#### Acceptance Criteria

1. WHEN the coordinator adds or updates an alias THE SYSTEM SHALL
   upsert `<name>: <targets>` in `/etc/aliases`; SKIPPED when
   unchanged.
2. WHEN the coordinator removes an alias THE SYSTEM SHALL strip
   the matching line; SKIPPED when absent.
3. WHEN any mutation to `/etc/aliases` succeeds THE SYSTEM SHALL
   invoke `newaliases` to rebuild the aliases database.
4. WHEN the coordinator lists aliases THE SYSTEM SHALL parse
   `/etc/aliases` into `dict[str, list[str]]`.

---

### Requirement 5: Mail Queue Operations

**User Story:** As a maintenance coordinator, I want to inspect,
flush, and delete queued mail so that stuck messages do not
accumulate indefinitely.

#### Acceptance Criteria

1. WHEN the coordinator lists the queue THE SYSTEM SHALL invoke
   `postqueue -p` (same as `mailq`) and parse the output into
   `list[QueuedMessage]` records.
2. WHEN the coordinator flushes the queue THE SYSTEM SHALL invoke
   `postqueue -f` (attempts to deliver everything now).
3. WHEN the coordinator deletes a specific message THE SYSTEM
   SHALL invoke `postsuper -d <queue_id>`; SKIPPED when the
   queue-id does not exist.
4. WHEN the coordinator deletes all queued mail THE SYSTEM SHALL
   invoke `postsuper -d ALL`.
5. WHEN the coordinator requeues a message THE SYSTEM SHALL
   invoke `postsuper -r <queue_id>`.

---

### Requirement 6: Reload / Restart

**User Story:** As a maintenance coordinator, I want to reload or
restart Postfix so that configuration changes take effect.

#### Acceptance Criteria

1. WHEN the coordinator requests a reload THE SYSTEM SHALL
   delegate to the injected `ServiceManager.reload("postfix")`.
2. WHEN the coordinator requests a restart THE SYSTEM SHALL
   delegate to `service_manager.restart("postfix")`.
3. WHEN `main.cf` or `master.cf` has been mutated via this
   feature's methods AND `autoreload=True` is set on the
   configuration THE SYSTEM SHALL auto-reload after the mutation.

---

### Requirement 7: Dry-Run, Elevation, and Error Reporting

Standard dry-run / elevation / failure semantics.

#### Acceptance Criteria

1. WHEN `config.dry_run=True` THE SYSTEM SHALL NOT dispatch any
   mutating `postconf`, `postmap`, `newaliases`, `postqueue`, or
   `postsuper` command and SHALL NOT rewrite `/etc/postfix/*` or
   `/etc/aliases`.
2. WHEN `config.elevated=True` THE SYSTEM SHALL dispatch mutating
   commands elevated.
3. WHEN the runner returns non-zero THE SYSTEM SHALL return
   `MailActionResult(status=FAILED, exit_code=<code>,
   stderr=<captured>)`.
