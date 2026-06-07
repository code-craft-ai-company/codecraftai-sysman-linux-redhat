# Requirements

## Introduction

The tuned management feature provides the `codecraftai-sysman-linux-redhat` package with
the ability to manage the `tuned` performance-tuning subsystem on Red Hat Linux
systems: the `tuned.service` lifecycle, the active profile, profile listing, profile
verification, and custom profiles placed under `/etc/tuned/`.

A maintenance coordinator uses this feature to apply a declared performance profile to
the target system and verify that it remains applied. All system commands (`systemctl`,
`tuned-adm`) are executed through the `codecraftai-interfaces` command-runner
abstraction. All file reads and writes under `/etc/tuned/` use the
`codecraftai-interfaces` file-operator abstraction. All mutating operations respect
dry-run mode.

---

## Requirements

### Requirement 1: Query tuned Service State

**User Story:** As a maintenance coordinator, I want to query the state of the tuned
service, so that I can verify the tuning subsystem is running before expecting a
profile to be active.

#### Acceptance Criteria

1. WHEN the coordinator queries the state THE SYSTEM SHALL invoke
   `systemctl is-active tuned` and `systemctl is-enabled tuned` via the command
   runner.
2. THE SYSTEM SHALL return a `TunedServiceState` record containing `active`,
   `enabled`, and `present` fields.
3. WHEN the service unit is not present THE SYSTEM SHALL return a record with
   `present=False` and leave the other fields unset.
4. WHEN the query commands fail for any other reason THE SYSTEM SHALL raise a
   `TunedQueryError`.

---

### Requirement 2: Lifecycle Management

**User Story:** As a maintenance coordinator, I want to start, stop, enable, disable,
or restart the tuned service, so that the tuning subsystem is in the desired state.

#### Acceptance Criteria

1. WHEN the coordinator requests a lifecycle action (`start`, `stop`, `restart`,
   `enable`, `disable`) THE SYSTEM SHALL invoke `systemctl <action> tuned` with
   elevated privileges.
2. WHEN the service is already in the desired state THE SYSTEM SHALL skip the command
   and return a `TunedActionResult` with status `SKIPPED`.
3. WHEN the operation succeeds THE SYSTEM SHALL return a `TunedActionResult` with
   status `SUCCESS`.
4. WHEN the operation fails THE SYSTEM SHALL return a `TunedActionResult` with status
   `FAILED`, exit code, and captured stderr.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 3: List Available Profiles

**User Story:** As a maintenance coordinator, I want to list all available tuned
profiles on the system, so that I can choose one to apply.

#### Acceptance Criteria

1. WHEN the coordinator requests the list THE SYSTEM SHALL invoke `tuned-adm list`.
2. THE SYSTEM SHALL return a list of `TunedProfile` records containing the profile
   name and description.
3. WHEN the list output includes a "Current active profile" line THE SYSTEM SHALL
   also populate that name on the returned list for caller convenience.
4. WHEN the command fails THE SYSTEM SHALL raise a `TunedQueryError`.

---

### Requirement 4: Query Active Profile

**User Story:** As a maintenance coordinator, I want to query the currently active
tuned profile, so that I can compare it against the declared desired profile.

#### Acceptance Criteria

1. WHEN the coordinator requests the active profile THE SYSTEM SHALL invoke
   `tuned-adm active`.
2. WHEN a profile is active THE SYSTEM SHALL return the profile name as a string.
3. WHEN no profile is active THE SYSTEM SHALL return `None`.
4. WHEN the command fails THE SYSTEM SHALL raise a `TunedQueryError`.

---

### Requirement 5: Apply a Profile

**User Story:** As a maintenance coordinator, I want to apply a named tuned profile,
so that the system adopts the desired tuning posture.

#### Acceptance Criteria

1. WHEN the coordinator requests application of a named profile THE SYSTEM SHALL
   invoke `tuned-adm profile <name>` with elevated privileges.
2. WHEN the tuned service is not running THE SYSTEM SHALL NOT attempt the apply and
   SHALL return a `TunedActionResult` with status `FAILED` and an error indicating
   the service is not active.
3. WHEN the requested profile is already active THE SYSTEM SHALL skip execution and
   return a `TunedActionResult` with status `SKIPPED`.
4. WHEN the named profile is not available on the system THE SYSTEM SHALL NOT execute
   the apply and SHALL return a `TunedActionResult` with status `FAILED` and an
   error listing the unavailable name.
5. WHEN the apply succeeds THE SYSTEM SHALL return a `TunedActionResult` with status
   `SUCCESS`.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 6: Turn Off Tuning

**User Story:** As a maintenance coordinator, I want to disable the active tuned
profile without stopping the service, so that the system reverts to default kernel
tuning without further changes being applied.

#### Acceptance Criteria

1. WHEN the coordinator requests profile-off THE SYSTEM SHALL invoke `tuned-adm off`
   with elevated privileges.
2. WHEN no profile is currently active THE SYSTEM SHALL skip execution and return a
   `TunedActionResult` with status `SKIPPED`.
3. WHEN the operation succeeds THE SYSTEM SHALL return a `TunedActionResult` with
   status `SUCCESS`.
4. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 7: Recommend a Profile

**User Story:** As a maintenance coordinator, I want to query tuned's recommendation
for the current hardware and role, so that I can automate profile selection on new
systems.

#### Acceptance Criteria

1. WHEN the coordinator requests a recommendation THE SYSTEM SHALL invoke
   `tuned-adm recommend`.
2. THE SYSTEM SHALL return the first non-empty line of output as the recommended
   profile name.
3. WHEN tuned returns no recommendation THE SYSTEM SHALL return `None`.
4. WHEN the command fails THE SYSTEM SHALL raise a `TunedQueryError`.

---

### Requirement 8: Verify the Active Profile

**User Story:** As a maintenance coordinator, I want to verify that the active tuned
profile is correctly applied, so that I can detect drift introduced by other tools or
manual tuning.

#### Acceptance Criteria

1. WHEN the coordinator requests a verify THE SYSTEM SHALL invoke `tuned-adm verify`.
2. WHEN the verify reports success THE SYSTEM SHALL return a `TunedVerifyResult`
   with `success=True`.
3. WHEN the verify reports drift THE SYSTEM SHALL return a `TunedVerifyResult` with
   `success=False` and the captured list of mismatched settings.
4. WHEN the command exits with a non-verify error THE SYSTEM SHALL raise a
   `TunedQueryError`.

---

### Requirement 9: Manage a Custom Profile

**User Story:** As a maintenance coordinator, I want to install, update, or remove a
custom profile under `/etc/tuned/<name>/`, so that site-specific tuning can be
delivered alongside stock profiles.

#### Acceptance Criteria

1. WHEN the coordinator requests installation of a profile `<name>` with a supplied
   `tuned.conf` body THE SYSTEM SHALL create the directory
   `/etc/tuned/<name>/` (mode `0755`) and write `tuned.conf` (mode `0644`, owner
   `root`, group `root`) through the file operator with elevated privileges.
2. WHEN additional payload files are supplied THE SYSTEM SHALL write each into the
   profile directory with the supplied path and permissions.
3. WHEN the existing `tuned.conf` content matches the requested content byte-for-byte
   AND no other payload is requested THE SYSTEM SHALL skip the write and return a
   `TunedActionResult` with status `SKIPPED`.
4. WHEN the coordinator requests removal of a custom profile `<name>` THE SYSTEM
   SHALL remove the `/etc/tuned/<name>/` directory recursively. If the directory does
   not exist the SYSTEM SHALL return a `TunedActionResult` with status `SKIPPED`.
5. WHEN the profile being removed is currently active THE SYSTEM SHALL NOT perform
   the removal and SHALL return a `TunedActionResult` with status `FAILED` and an
   error indicating the profile must be deactivated first.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT perform any write or delete.

---

### Requirement 10: Elevated Privilege Execution

**User Story:** As a maintenance coordinator, I want all mutating tuned commands to
run with the privileges required by the tool, so that operations succeed on standard
systems where the process does not run as root.

#### Acceptance Criteria

1. WHEN the system is configured to use sudo elevation THE SYSTEM SHALL prefix all
   mutating commands (`systemctl <action> tuned`, `tuned-adm profile`, `tuned-adm off`,
   writes under `/etc/tuned/`) with the command runner's elevation mechanism.
2. WHEN the system is running as root THE SYSTEM SHALL NOT prefix commands with
   `sudo`.
3. WHEN read-only query commands (`systemctl is-*`, `tuned-adm list`, `tuned-adm active`,
   `tuned-adm recommend`, `tuned-adm verify`) are invoked THE SYSTEM SHALL NOT
   require elevation.
