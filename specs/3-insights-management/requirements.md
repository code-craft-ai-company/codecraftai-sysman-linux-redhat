# Requirements

## Introduction

The Insights management feature provides the `codecraftai-sysman-linux-redhat` package
with the ability to manage the Red Hat Insights client (`insights-client`) on Red
Hat Enterprise Linux systems: registration state, inventory uploads, check-ins,
client configuration, and connection testing.

A maintenance coordinator uses this feature to enroll systems with Red Hat Insights,
trigger or pause telemetry uploads, and verify that the client can reach the
Insights service. All system commands (`insights-client`) are executed through the
`codecraftai-interfaces` command-runner abstraction. All reads and writes against
`/etc/insights-client/insights-client.conf` and tag files use the
`codecraftai-interfaces` file-operator abstraction. All mutating operations respect
dry-run mode.

This feature depends on the `insights-client` RPM being installed; package
installation remains the responsibility of the `package-management` feature.
Subscription Management registration remains the responsibility of the
`subscription-management` feature.

---

## Requirements

### Requirement 1: Query Registration State

**User Story:** As a maintenance coordinator, I want to query whether the system is
currently registered with Red Hat Insights, so that I can decide whether to register
or unregister it.

#### Acceptance Criteria

1. WHEN the coordinator queries the state THE SYSTEM SHALL invoke
   `insights-client --status` via the command runner with elevated privileges.
2. WHEN the output indicates the system is registered THE SYSTEM SHALL return an
   `InsightsState` record with `registered=True` and the captured machine-id/UUID.
3. WHEN the output indicates the system is not registered THE SYSTEM SHALL return
   an `InsightsState` record with `registered=False`.
4. WHEN the `insights-client` binary is not installed THE SYSTEM SHALL raise an
   `InsightsNotInstalledError` before attempting the call.
5. WHEN the command fails for any other reason THE SYSTEM SHALL raise an
   `InsightsQueryError` with the exit code and captured stderr.

---

### Requirement 2: Register with Insights

**User Story:** As a maintenance coordinator, I want to register a system with Red
Hat Insights, so that it begins contributing inventory and analytics data.

#### Acceptance Criteria

1. WHEN the coordinator requests registration THE SYSTEM SHALL invoke
   `insights-client --register` with elevated privileges.
2. WHEN the coordinator supplies a `display_name` THE SYSTEM SHALL pass
   `--display-name=<value>`.
3. WHEN the coordinator supplies a `group` THE SYSTEM SHALL pass
   `--group=<value>`.
4. WHEN the system is already registered AND `--force` was not supplied THE
   SYSTEM SHALL skip execution and return an `InsightsActionResult` with status
   `SKIPPED`.
5. WHEN the coordinator supplies `force=True` THE SYSTEM SHALL pass `--force-reregister`.
6. WHEN registration succeeds THE SYSTEM SHALL return an `InsightsActionResult`
   with status `SUCCESS` and the captured machine-id/UUID.
7. WHEN registration fails THE SYSTEM SHALL return an `InsightsActionResult` with
   status `FAILED`, exit code, and captured stderr.
8. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 3: Unregister from Insights

**User Story:** As a maintenance coordinator, I want to unregister a system from
Red Hat Insights, so that it no longer appears in the inventory and no longer
contributes data.

#### Acceptance Criteria

1. WHEN the coordinator requests unregistration THE SYSTEM SHALL invoke
   `insights-client --unregister` with elevated privileges.
2. WHEN the system is not registered THE SYSTEM SHALL skip execution and return
   an `InsightsActionResult` with status `SKIPPED`.
3. WHEN the operation succeeds THE SYSTEM SHALL return an `InsightsActionResult`
   with status `SUCCESS`.
4. WHEN the operation fails THE SYSTEM SHALL return an `InsightsActionResult`
   with status `FAILED`, exit code, and captured stderr.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 4: Upload Inventory and Diagnostic Data

**User Story:** As a maintenance coordinator, I want to trigger an on-demand upload
of inventory and diagnostic data to Insights, so that changes applied by a
maintenance run are reflected in the Insights view.

#### Acceptance Criteria

1. WHEN the coordinator requests an upload THE SYSTEM SHALL invoke
   `insights-client` (no flags) with elevated privileges, which performs the
   default check-in and upload.
2. WHEN the coordinator requests only a check-in without a fresh collection THE
   SYSTEM SHALL invoke `insights-client --checkin`.
3. WHEN the system is not registered THE SYSTEM SHALL NOT attempt the upload and
   SHALL return an `InsightsActionResult` with status `FAILED` indicating the
   system must first be registered.
4. WHEN the operation succeeds THE SYSTEM SHALL return an `InsightsActionResult`
   with status `SUCCESS`.
5. WHEN the operation fails THE SYSTEM SHALL return an `InsightsActionResult`
   with status `FAILED`, exit code, and captured stderr.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 5: Collect Without Uploading

**User Story:** As a maintenance coordinator, I want to run a local collection
without uploading, so that the collected payload can be inspected locally before
being sent.

#### Acceptance Criteria

1. WHEN the coordinator requests a local collection THE SYSTEM SHALL invoke
   `insights-client --no-upload` with elevated privileges.
2. WHEN the output directory for the payload is configurable THE SYSTEM SHALL
   capture and return the path reported by `insights-client`.
3. WHEN the operation succeeds THE SYSTEM SHALL return an `InsightsActionResult`
   with status `SUCCESS` and the captured payload path.
4. WHEN the operation fails THE SYSTEM SHALL return an `InsightsActionResult`
   with status `FAILED`, exit code, and captured stderr.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 6: Query Client Version

**User Story:** As a maintenance coordinator, I want to query the installed
`insights-client` version, so that compatibility and upgrade decisions can be made.

#### Acceptance Criteria

1. WHEN the coordinator requests the version THE SYSTEM SHALL invoke
   `insights-client --version`.
2. THE SYSTEM SHALL return an `InsightsVersion` record containing the reported
   `client_version` and, when present in the output, the `core_version`.
3. WHEN the command fails THE SYSTEM SHALL raise an `InsightsQueryError`.

---

### Requirement 7: Test Connection to Insights

**User Story:** As a maintenance coordinator, I want to test the client's
connectivity to the Insights service, so that transport and proxy issues can be
diagnosed independently of uploads.

#### Acceptance Criteria

1. WHEN the coordinator requests a connection test THE SYSTEM SHALL invoke
   `insights-client --test-connection` with elevated privileges.
2. WHEN the test passes THE SYSTEM SHALL return an `InsightsConnectionResult` with
   `ok=True`.
3. WHEN the test fails THE SYSTEM SHALL return an `InsightsConnectionResult` with
   `ok=False` and the captured output.
4. WHEN the command cannot be invoked (binary missing, permission denied) THE
   SYSTEM SHALL raise an `InsightsNotInstalledError` or `InsightsPermissionError`.

---

### Requirement 8: Manage Client Configuration

**User Story:** As a maintenance coordinator, I want to read and update keys in
`/etc/insights-client/insights-client.conf`, so that proxy, authmethod, redaction,
and display-name settings can be controlled declaratively.

#### Acceptance Criteria

1. WHEN the coordinator queries a key THE SYSTEM SHALL read
   `/etc/insights-client/insights-client.conf`, parse the `[insights-client]`
   section, and return the `<key>=<value>` value or `None`.
2. WHEN the coordinator requests a set-or-replace THE SYSTEM SHALL update the
   matching line in the `[insights-client]` section (creating the section if
   absent), preserve surrounding comments, and write the file back through the
   file operator with mode `0640`, owner `root`, group `root`, and elevated
   privileges.
3. WHEN the coordinator requests deletion of a key THE SYSTEM SHALL remove the
   matching line.
4. WHEN the current value already matches the requested value THE SYSTEM SHALL
   skip the write and return an `InsightsActionResult` with status `SKIPPED`.
5. WHEN the write succeeds THE SYSTEM SHALL return an `InsightsActionResult`
   with status `SUCCESS`.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT write to the file.

---

### Requirement 9: Manage Tag Metadata

**User Story:** As a maintenance coordinator, I want to read and update
`/etc/insights-client/tags.yaml`, so that systems can be labeled with
inventory-relevant tags.

#### Acceptance Criteria

1. WHEN the coordinator supplies a dict of tags THE SYSTEM SHALL serialize it as
   YAML and write it to `/etc/insights-client/tags.yaml` through the file
   operator with mode `0640`, owner `root`, group `root`, and elevated privileges.
2. WHEN the file already contains the equivalent YAML content (after parsing) THE
   SYSTEM SHALL skip the write and return an `InsightsActionResult` with status
   `SKIPPED`.
3. WHEN the coordinator requests a read THE SYSTEM SHALL parse the YAML and
   return the tag dict, or an empty dict if the file is absent.
4. WHEN dry-run mode is active THE SYSTEM SHALL NOT write to the file.

---

### Requirement 10: Elevated Privilege Execution

**User Story:** As a maintenance coordinator, I want all Insights operations to
run with the privileges required by the client, so that they succeed on standard
systems where the process does not run as root.

#### Acceptance Criteria

1. WHEN the system is configured to use sudo elevation THE SYSTEM SHALL prefix
   every `insights-client` invocation and every write under
   `/etc/insights-client/` with the command runner's elevation mechanism.
2. WHEN the system is running as root THE SYSTEM SHALL NOT prefix commands with
   `sudo`.
3. WHEN reading `/etc/insights-client/insights-client.conf` or
   `/etc/insights-client/tags.yaml` and the file is not readable to the calling
   user THE SYSTEM SHALL use elevation transparently to complete the read.
