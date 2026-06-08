# Requirements

## Introduction

The audit management feature provides the `codecraftai-sysman-linux-redhat` package with
the ability to manage the Linux Audit subsystem (`auditd`) on Red Hat Linux systems: the
audit daemon, the active and persistent rule sets, the daemon's configuration, and the
audit-log search and reporting tools.

A maintenance coordinator uses this feature to apply a declared audit posture to the
target system, verify it, and extract findings from the audit log. All system commands
(`auditctl`, `augenrules`, `ausearch`, `aureport`, `systemctl`, `service`) are executed
through the command-runner abstraction (`ccai.sysman.linux.redhat.runners.base.ICommandRunner`).
File reads and writes against `/etc/audit/auditd.conf`, `/etc/audit/audit.rules`, and
files under `/etc/audit/rules.d/` are performed by routing `cat` / `tee` through the
command runner (leveraging the runner's `stdin` channel for `tee`), so remote execution
and dry-run apply uniformly.  All mutating operations respect dry-run mode.

---

## Requirements

### Requirement 1: Query auditd Service State

**User Story:** As a maintenance coordinator, I want to query the current state of the
`auditd` service, so that I can verify the audit subsystem is running before expecting
audit data to be produced.

#### Acceptance Criteria

1. WHEN the coordinator queries the state THE SYSTEM SHALL invoke
   `systemctl is-active auditd` and `systemctl is-enabled auditd` via the command runner.
2. THE SYSTEM SHALL return an `AuditServiceState` record containing active, enabled, and
   (when available) PID fields.
3. WHEN the service unit is not present THE SYSTEM SHALL return a record with
   `present=False` and leave the other fields unset.
4. WHEN the query commands fail for any other reason THE SYSTEM SHALL raise an
   `AuditQueryError` with the exit code and captured stderr.

---

### Requirement 2: Start, Stop, Enable, and Disable auditd

**User Story:** As a maintenance coordinator, I want to start, stop, enable, or disable
the `auditd` service, so that the audit subsystem is in the desired state.

#### Acceptance Criteria

1. WHEN the coordinator requests a lifecycle action (`start`, `stop`, `enable`,
   `disable`) THE SYSTEM SHALL invoke `systemctl <action> auditd` with elevated
   privileges. Note: the `auditd.service` unit is protected against `restart` via
   `systemctl` — the system SHALL use `service auditd restart` for restart instead.
2. WHEN the service is already in the desired state THE SYSTEM SHALL skip the command
   and return an `AuditActionResult` with status `SKIPPED`.
3. WHEN the operation succeeds THE SYSTEM SHALL return an `AuditActionResult` with
   status `SUCCESS`.
4. WHEN the operation fails THE SYSTEM SHALL return an `AuditActionResult` with status
   `FAILED`, exit code, and captured stderr.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 3: List Active Audit Rules

**User Story:** As a maintenance coordinator, I want to list the rules currently active
in the kernel, so that I can compare them against the declared desired rule set.

#### Acceptance Criteria

1. WHEN the coordinator requests the active rule list THE SYSTEM SHALL invoke
   `auditctl -l`.
2. THE SYSTEM SHALL return a list of `AuditRule` records containing the raw rule text
   and, where parseable, the filter list, action, and key.
3. WHEN `auditctl -l` reports `No rules` THE SYSTEM SHALL return an empty list.
4. WHEN the command fails THE SYSTEM SHALL raise an `AuditQueryError`.

---

### Requirement 4: Add a Runtime Audit Rule

**User Story:** As a maintenance coordinator, I want to add a single runtime audit rule
via `auditctl`, so that auditing takes effect immediately without waiting for a reload.

#### Acceptance Criteria

1. WHEN the coordinator supplies a rule string THE SYSTEM SHALL invoke
   `auditctl <rule-args>` with elevated privileges.
2. WHEN the kernel is in immutable mode (`auditctl -s` reports `enabled 2`) THE SYSTEM
   SHALL NOT attempt the operation and SHALL return an `AuditActionResult` with status
   `FAILED` and an `AuditImmutableError` error message.
3. WHEN the rule is already active with the same arguments THE SYSTEM SHALL skip
   execution and return an `AuditActionResult` with status `SKIPPED`.
4. WHEN the operation succeeds THE SYSTEM SHALL return an `AuditActionResult` with
   status `SUCCESS`.
5. WHEN the operation fails THE SYSTEM SHALL return an `AuditActionResult` with status
   `FAILED`, exit code, and captured stderr.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 5: Remove a Runtime Audit Rule

**User Story:** As a maintenance coordinator, I want to remove a specific runtime audit
rule or flush all rules, so that the kernel rule set can be reset.

#### Acceptance Criteria

1. WHEN the coordinator requests removal of a specific rule THE SYSTEM SHALL invoke
   `auditctl -d <rule-args>` with elevated privileges.
2. WHEN the coordinator requests a full flush THE SYSTEM SHALL invoke `auditctl -D`
   with elevated privileges.
3. WHEN the rule does not exist THE SYSTEM SHALL skip and return an `AuditActionResult`
   with status `SKIPPED`.
4. WHEN the operation succeeds THE SYSTEM SHALL return an `AuditActionResult` with
   status `SUCCESS`.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 6: Manage Persistent Rule Drop-Ins

**User Story:** As a maintenance coordinator, I want to write, replace, or remove
`.rules` files under `/etc/audit/rules.d/`, so that the audit rule set persists across
reboots.

#### Acceptance Criteria

1. WHEN the coordinator requests writing a named drop-in THE SYSTEM SHALL write the
   supplied rule text to `/etc/audit/rules.d/<name>.rules` via the file operator with
   mode `0640`, owner `root`, group `root`, and elevated privileges.
2. WHEN the target file already contains identical content THE SYSTEM SHALL skip the
   write and return an `AuditActionResult` with status `SKIPPED`.
3. WHEN the coordinator requests removal of a named drop-in THE SYSTEM SHALL delete
   `/etc/audit/rules.d/<name>.rules`. If the file does not exist the SYSTEM SHALL
   return an `AuditActionResult` with status `SKIPPED`.
4. WHEN the coordinator requests a list of drop-ins THE SYSTEM SHALL enumerate
   `/etc/audit/rules.d/*.rules` and return each as an `AuditRuleFile` record
   containing name, full path, and file size.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT perform any write or delete.

---

### Requirement 7: Reload Persistent Rules

**User Story:** As a maintenance coordinator, I want to reload the persistent rule set
after editing drop-ins, so that the changes take effect without a reboot.

#### Acceptance Criteria

1. WHEN the coordinator requests a reload THE SYSTEM SHALL invoke `augenrules --load`
   with elevated privileges.
2. WHEN the kernel is in immutable mode THE SYSTEM SHALL return an `AuditActionResult`
   with status `FAILED` and an `AuditImmutableError` message, and SHALL NOT execute
   the reload.
3. WHEN the reload succeeds THE SYSTEM SHALL return an `AuditActionResult` with status
   `SUCCESS`.
4. WHEN the reload fails THE SYSTEM SHALL return an `AuditActionResult` with status
   `FAILED`, exit code, and captured stderr.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 8: Query and Update auditd Configuration

**User Story:** As a maintenance coordinator, I want to read or update keys in
`/etc/audit/auditd.conf`, so that the audit daemon's own behavior (log location, size
limits, disk-full action, flush cadence) is correctly configured.

#### Acceptance Criteria

1. WHEN the coordinator requests a configuration value THE SYSTEM SHALL read
   `/etc/audit/auditd.conf`, parse `key = value` lines, and return the value or `None`
   if absent.
2. WHEN the coordinator requests a set-or-replace THE SYSTEM SHALL update the `key =
   value` line in place if present, or append it, and write the file back through the
   file operator with mode `0640`, owner `root`, group `root`, and elevated privileges.
3. WHEN the coordinator requests deletion of a key THE SYSTEM SHALL remove the matching
   line.
4. WHEN the current value already matches the requested value THE SYSTEM SHALL skip the
   write and return an `AuditActionResult` with status `SKIPPED`.
5. WHEN the write succeeds THE SYSTEM SHALL return an `AuditActionResult` with status
   `SUCCESS` and include a note indicating that restarting `auditd` is required for
   some keys to take effect.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT write to the file.

---

### Requirement 9: Search Audit Log via ausearch

**User Story:** As a maintenance coordinator, I want to search the audit log using
`ausearch` filters, so that I can extract structured findings for maintenance reports.

#### Acceptance Criteria

1. WHEN the coordinator supplies a filter (key, message type, user id, time range, or
   executable path) THE SYSTEM SHALL invoke `ausearch` with the corresponding flags
   (`-k`, `-m`, `-ui`, `-ts`, `-te`, `-x`).
2. THE SYSTEM SHALL return a list of `AuditEvent` records containing the event id,
   timestamp, type, serial, and raw event text.
3. WHEN `ausearch` reports no results THE SYSTEM SHALL return an empty list.
4. WHEN the command fails for any other reason THE SYSTEM SHALL raise an
   `AuditQueryError`.

---

### Requirement 10: Generate Summary Report via aureport

**User Story:** As a maintenance coordinator, I want to generate a summary report from
the audit log, so that high-level counts of events can be surfaced in maintenance
reports.

#### Acceptance Criteria

1. WHEN the coordinator requests a summary THE SYSTEM SHALL invoke `aureport --summary`.
2. WHEN the coordinator requests a category-specific report (e.g. users, executables,
   failed logins) THE SYSTEM SHALL invoke `aureport` with the appropriate flag
   (`-u`, `-x`, `-au --failed`).
3. WHEN the coordinator supplies a time range THE SYSTEM SHALL pass `-ts <start>` and
   `-te <end>`.
4. THE SYSTEM SHALL return an `AuditReport` record containing the category, the raw
   report text, and a parsed structure where feasible.
5. WHEN the command fails THE SYSTEM SHALL raise an `AuditQueryError`.

---

### Requirement 11: Query Kernel Immutability and Backlog

**User Story:** As a maintenance coordinator, I want to query the kernel audit state
(enabled flag, backlog limit, lost events), so that I can detect an immutable
configuration or saturated backlog.

#### Acceptance Criteria

1. WHEN the coordinator requests the kernel state THE SYSTEM SHALL invoke `auditctl -s`.
2. THE SYSTEM SHALL return an `AuditKernelState` record containing `enabled`,
   `failure`, `pid`, `rate_limit`, `backlog_limit`, `backlog`, and `lost` fields parsed
   from the output.
3. WHEN the command fails THE SYSTEM SHALL raise an `AuditQueryError`.

---

### Requirement 12: Elevated Privilege Execution

**User Story:** As a maintenance coordinator, I want all mutating audit commands to run
with the privileges required by the kernel audit interface, so that rule and
configuration changes succeed on standard systems where the process does not run as
root.

#### Acceptance Criteria

1. WHEN the system is configured to use sudo elevation THE SYSTEM SHALL prefix all
   mutating commands (`auditctl -a|-d|-D`, `augenrules --load`, writes under
   `/etc/audit/`, `systemctl` actions on `auditd`) with the command runner's
   elevation mechanism.
2. WHEN the system is running as root THE SYSTEM SHALL NOT prefix commands with `sudo`.
3. WHEN read-only query commands (`auditctl -l`, `auditctl -s`, `ausearch`, `aureport`,
   configuration file reads) are invoked THE SYSTEM SHALL NOT require elevation unless
   the log files themselves are unreadable to the current user, in which case the
   SYSTEM SHALL use elevation transparently.
