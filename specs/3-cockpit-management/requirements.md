# Requirements

## Introduction

The Cockpit management feature provides the `codecraftai-sysman-linux-redhat` package
with the ability to manage the Cockpit web management console on Red Hat Linux
systems: the `cockpit.socket` lifecycle, the listening port, the installed Cockpit
module set, and the main configuration file at `/etc/cockpit/cockpit.conf`.

A maintenance coordinator uses this feature to bring Cockpit into a declared
desired state on each target system. All system commands (`systemctl`, `cockpit-bridge`)
are executed through the `codecraftai-interfaces` command-runner abstraction. All
reads and writes under `/etc/cockpit/` and `/etc/systemd/system/cockpit.socket.d/`
use the `codecraftai-interfaces` file-operator abstraction. All mutating operations
respect dry-run mode.

This feature does not install Cockpit packages; RPM installation remains the
responsibility of the `package-management` feature. Firewall ports required by
Cockpit remain the responsibility of the `firewall-management` feature. systemd
lifecycle primitives remain the responsibility of the `service-management`
feature; this feature uses them.

---

## Requirements

### Requirement 1: Query Cockpit Socket State

**User Story:** As a maintenance coordinator, I want to query the state of
`cockpit.socket`, so that I can verify Cockpit is listening for web connections.

#### Acceptance Criteria

1. WHEN the coordinator queries the state THE SYSTEM SHALL invoke
   `systemctl is-active cockpit.socket` and
   `systemctl is-enabled cockpit.socket` via the command runner.
2. THE SYSTEM SHALL return a `CockpitSocketState` record containing `active`,
   `enabled`, `present`, and the effective listen port (see Requirement 4).
3. WHEN the socket unit is not present THE SYSTEM SHALL return a record with
   `present=False` and leave the other fields unset.
4. WHEN the query commands fail for any other reason THE SYSTEM SHALL raise a
   `CockpitQueryError`.

---

### Requirement 2: Enable or Disable cockpit.socket

**User Story:** As a maintenance coordinator, I want to enable, disable, start, or
stop `cockpit.socket`, so that the web console is (or is not) listening as
declared.

#### Acceptance Criteria

1. WHEN the coordinator requests a lifecycle action (`start`, `stop`, `restart`,
   `enable`, `disable`) THE SYSTEM SHALL invoke
   `systemctl <action> cockpit.socket` with elevated privileges.
2. WHEN the socket is already in the desired state THE SYSTEM SHALL skip the
   command and return a `CockpitActionResult` with status `SKIPPED`.
3. WHEN the operation succeeds THE SYSTEM SHALL return a `CockpitActionResult`
   with status `SUCCESS`.
4. WHEN the operation fails THE SYSTEM SHALL return a `CockpitActionResult` with
   status `FAILED`, exit code, and captured stderr.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 3: List Installed Cockpit Modules

**User Story:** As a maintenance coordinator, I want to list the Cockpit modules
(a.k.a. packages under `/usr/share/cockpit/`) that are installed on the target,
so that I can audit the UI surface that Cockpit exposes.

#### Acceptance Criteria

1. WHEN the coordinator requests the list THE SYSTEM SHALL enumerate
   `/usr/share/cockpit/*/manifest.json` through the file operator.
2. THE SYSTEM SHALL parse each manifest and return a list of `CockpitModule`
   records containing `name`, `version`, `priority`, `requires`, and
   `path`.
3. WHEN a manifest is malformed THE SYSTEM SHALL include the module in the list
   with `valid=False` and the captured error.
4. WHEN the directory does not exist THE SYSTEM SHALL return an empty list.

---

### Requirement 4: Query Cockpit Listen Port

**User Story:** As a maintenance coordinator, I want to query the effective
listening port of `cockpit.socket`, so that the firewall feature and external
clients can be configured to match.

#### Acceptance Criteria

1. WHEN the coordinator queries the port THE SYSTEM SHALL invoke
   `systemctl show cockpit.socket --property=Listen` and parse the returned
   `Listen=Stream:<address>:<port>` entries.
2. WHEN no `Listen=Stream` property is reported the SYSTEM SHALL fall back to
   reading the packaged `/usr/lib/systemd/system/cockpit.socket` unit and any
   drop-ins under `/etc/systemd/system/cockpit.socket.d/` through the file
   operator.
3. THE SYSTEM SHALL return a `CockpitListen` record containing a list of
   `(address, port)` tuples.
4. WHEN the command and file read both fail THE SYSTEM SHALL raise a
   `CockpitQueryError`.

---

### Requirement 5: Set Cockpit Listen Port via Drop-In

**User Story:** As a maintenance coordinator, I want to change the port on which
Cockpit listens by writing a systemd socket drop-in, so that the change does not
conflict with distribution updates to the main unit file.

#### Acceptance Criteria

1. WHEN the coordinator supplies a new listen address and port THE SYSTEM SHALL
   write `/etc/systemd/system/cockpit.socket.d/listen.conf` with contents:
   ```
   [Socket]
   ListenStream=
   ListenStream=<address>:<port>
   ```
   through the file operator with mode `0644`, owner `root`, group `root`, and
   elevated privileges.
2. WHEN the drop-in already contains the equivalent content THE SYSTEM SHALL
   skip the write and return a `CockpitActionResult` with status `SKIPPED`.
3. WHEN the write succeeds THE SYSTEM SHALL call the `service-management`
   feature to perform `systemctl daemon-reload` and to restart
   `cockpit.socket`. This feature SHALL NOT perform those lifecycle calls
   directly.
4. WHEN the coordinator requests removal of the drop-in THE SYSTEM SHALL delete
   the file; if absent the SYSTEM SHALL return a `CockpitActionResult` with
   status `SKIPPED`.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT write or delete any file.

---

### Requirement 6: Manage /etc/cockpit/cockpit.conf

**User Story:** As a maintenance coordinator, I want to read and update keys in
`/etc/cockpit/cockpit.conf`, so that UI-facing settings such as login banner,
max session age, and allowed hosts can be controlled declaratively.

#### Acceptance Criteria

1. WHEN the coordinator queries a section/key THE SYSTEM SHALL read
   `/etc/cockpit/cockpit.conf` as an INI file and return the value of the
   specified key in the specified section or `None`.
2. WHEN the coordinator requests a set-or-replace THE SYSTEM SHALL update or add
   the `<key>=<value>` line in the named section (creating the section if
   absent), preserve ordering and comments where possible, and write the file
   back through the file operator with mode `0644`, owner `root`, group `root`,
   and elevated privileges. Creating the file SHALL also create
   `/etc/cockpit/` with mode `0755` if it does not already exist.
3. WHEN the coordinator requests deletion of a key THE SYSTEM SHALL remove the
   matching line; when the section becomes empty the SYSTEM SHALL remove the
   section header as well.
4. WHEN the current value already matches the requested value THE SYSTEM SHALL
   skip the write and return a `CockpitActionResult` with status `SKIPPED`.
5. WHEN the write succeeds THE SYSTEM SHALL return a `CockpitActionResult` with
   status `SUCCESS` and include a note indicating that a Cockpit session
   reconnect (or a `systemctl restart cockpit.socket`) is required for some
   keys to take effect.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT write to the file.

---

### Requirement 7: Manage Login Banner File

**User Story:** As a maintenance coordinator, I want to install a Cockpit login
banner file at a declared path, so that the web console can present a legal or
operational banner to users.

#### Acceptance Criteria

1. WHEN the coordinator supplies banner text and a target path THE SYSTEM SHALL
   write the file through the file operator with mode `0644`, owner `root`,
   group `root`, and elevated privileges.
2. WHEN the existing file content matches byte-for-byte THE SYSTEM SHALL skip
   the write and return a `CockpitActionResult` with status `SKIPPED`.
3. WHEN the coordinator also requests that the `[Session]` → `Banner` key in
   `cockpit.conf` point to the supplied path THE SYSTEM SHALL delegate to
   Requirement 6's set capability.
4. WHEN dry-run mode is active THE SYSTEM SHALL NOT write any file.

---

### Requirement 8: Query Cockpit Version

**User Story:** As a maintenance coordinator, I want to query the installed
Cockpit version, so that compatibility decisions can be made.

#### Acceptance Criteria

1. WHEN the coordinator requests the version THE SYSTEM SHALL invoke
   `cockpit-bridge --version` via the command runner.
2. THE SYSTEM SHALL return the captured version string.
3. WHEN the binary is not installed THE SYSTEM SHALL raise a
   `CockpitNotInstalledError`.
4. WHEN the command fails for any other reason THE SYSTEM SHALL raise a
   `CockpitQueryError`.

---

### Requirement 9: Elevated Privilege Execution

**User Story:** As a maintenance coordinator, I want all mutating Cockpit
operations to run with the privileges required by the tooling, so that operations
succeed on standard systems where the process does not run as root.

#### Acceptance Criteria

1. WHEN the system is configured to use sudo elevation THE SYSTEM SHALL prefix
   all mutating commands (`systemctl <action> cockpit.socket`, writes under
   `/etc/cockpit/`, writes and deletes under
   `/etc/systemd/system/cockpit.socket.d/`) with the command runner's
   elevation mechanism.
2. WHEN the system is running as root THE SYSTEM SHALL NOT prefix commands with
   `sudo`.
3. WHEN read-only commands (`systemctl is-*`, `systemctl show`,
   `cockpit-bridge --version`, manifest reads under `/usr/share/cockpit/`) are
   invoked THE SYSTEM SHALL NOT require elevation.
