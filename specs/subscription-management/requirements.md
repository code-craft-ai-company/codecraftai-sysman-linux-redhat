# Requirements

## Introduction

The subscription management feature provides the `automojo-sysman-linux-redhat` package
with the ability to register a Red Hat Enterprise Linux system with Red Hat Subscription
Management (RHSM), attach entitlements, manage subscription-provided repositories, set the
release version, and query subscription state. All operations are driven by a maintenance
coordinator through this feature's public interfaces.

All system commands (`subscription-manager`, `rhsmcertd`, `rct`) are executed exclusively
through the `automojo-interfaces` command-runner abstraction, enabling dry-run simulation,
command auditing, and test mocking. All file reads and writes use the
`automojo-interfaces` file-operator abstraction.

---

## Requirements

### Requirement 1: Register System with Subscription Manager

**User Story:** As a maintenance coordinator, I want to register the system with Red Hat
Subscription Management using either credentials or an activation key, so that the system
becomes entitled to receive content and updates from Red Hat.

#### Acceptance Criteria

1. WHEN the coordinator requests registration by username and password THE SYSTEM SHALL
   invoke `subscription-manager register --username <user> --password <pass>` via the
   command runner with elevated privileges.
2. WHEN the coordinator requests registration by activation key THE SYSTEM SHALL invoke
   `subscription-manager register --org <org-id> --activationkey <key>`.
3. WHEN the coordinator supplies `--force` THE SYSTEM SHALL pass `--force` to allow
   re-registration of an already-registered system.
4. WHEN the system is already registered AND `--force` is not supplied THE SYSTEM SHALL
   skip execution and return a `SubscriptionActionResult` with status `SKIPPED`.
5. WHEN registration succeeds THE SYSTEM SHALL return a `SubscriptionActionResult` with
   status `SUCCESS` and the captured system UUID.
6. WHEN registration fails THE SYSTEM SHALL return a `SubscriptionActionResult` with
   status `FAILED`, exit code, and captured stderr.
7. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command and SHALL return
   a `SubscriptionActionResult` with status `DRY_RUN` that does not expose credentials.

---

### Requirement 2: Unregister System

**User Story:** As a maintenance coordinator, I want to unregister a system from
Subscription Management, so that entitlements are released and the system no longer
consumes a subscription.

#### Acceptance Criteria

1. WHEN the coordinator requests unregistration THE SYSTEM SHALL invoke
   `subscription-manager unregister` via the command runner with elevated privileges.
2. WHEN the system is not registered THE SYSTEM SHALL skip execution and return a
   `SubscriptionActionResult` with status `SKIPPED`.
3. WHEN unregistration succeeds THE SYSTEM SHALL return a `SubscriptionActionResult` with
   status `SUCCESS`.
4. WHEN unregistration fails THE SYSTEM SHALL return a `SubscriptionActionResult` with
   status `FAILED`, exit code, and captured stderr.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 3: Query Registration Identity

**User Story:** As a maintenance coordinator, I want to query whether a system is
registered and retrieve its identity, so that I can make conditional decisions about
registration and repository setup.

#### Acceptance Criteria

1. WHEN the coordinator requests the identity THE SYSTEM SHALL invoke
   `subscription-manager identity` via the command runner.
2. WHEN the system is registered THE SYSTEM SHALL return a `SubscriptionIdentity` record
   containing the system UUID, name, and organization.
3. WHEN the system is not registered THE SYSTEM SHALL return `None`.
4. WHEN the query command fails for any reason other than not-registered THE SYSTEM SHALL
   raise a `SubscriptionQueryError` with the exit code and captured stderr.

---

### Requirement 4: Query Subscription Status

**User Story:** As a maintenance coordinator, I want to query the overall subscription
status, so that I can verify that the system is compliant and its entitlements are
current.

#### Acceptance Criteria

1. WHEN the coordinator requests subscription status THE SYSTEM SHALL invoke
   `subscription-manager status` via the command runner.
2. WHEN the status output reports `Overall Status: Current` THE SYSTEM SHALL return a
   `SubscriptionStatus` record with `overall=CURRENT`.
3. WHEN the status output reports any other overall value (e.g. `Insufficient`,
   `Invalid`, `Disabled`, `Unknown`) THE SYSTEM SHALL map it to the corresponding enum
   value on the returned record.
4. WHEN the query command fails THE SYSTEM SHALL raise a `SubscriptionQueryError`.

---

### Requirement 5: Attach Subscriptions

**User Story:** As a maintenance coordinator, I want to attach subscriptions either
automatically or by pool ID, so that a registered system obtains the entitlements it
needs to receive content.

#### Acceptance Criteria

1. WHEN the coordinator requests an auto-attach THE SYSTEM SHALL invoke
   `subscription-manager attach --auto` via the command runner with elevated privileges.
2. WHEN the coordinator requests attachment by pool ID THE SYSTEM SHALL invoke
   `subscription-manager attach --pool <pool-id>` for each supplied pool.
3. WHEN the coordinator supplies a quantity THE SYSTEM SHALL append
   `--quantity <n>` to the command.
4. WHEN attachment succeeds THE SYSTEM SHALL return a `SubscriptionActionResult` with
   status `SUCCESS` and the list of attached pool IDs.
5. WHEN attachment fails THE SYSTEM SHALL return a `SubscriptionActionResult` with status
   `FAILED`, exit code, and captured stderr.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 6: Remove Attached Subscriptions

**User Story:** As a maintenance coordinator, I want to remove attached subscriptions by
serial or all at once, so that entitlements can be released when no longer needed.

#### Acceptance Criteria

1. WHEN the coordinator requests removal of all attached subscriptions THE SYSTEM SHALL
   invoke `subscription-manager remove --all` with elevated privileges.
2. WHEN the coordinator supplies a list of serial numbers THE SYSTEM SHALL invoke
   `subscription-manager remove --serial <serial>` for each serial.
3. WHEN no subscriptions are currently attached THE SYSTEM SHALL skip execution and
   return a `SubscriptionActionResult` with status `SKIPPED`.
4. WHEN removal succeeds THE SYSTEM SHALL return a `SubscriptionActionResult` with status
   `SUCCESS`.
5. WHEN removal fails THE SYSTEM SHALL return a `SubscriptionActionResult` with status
   `FAILED`, exit code, and captured stderr.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 7: List Subscription Repositories

**User Story:** As a maintenance coordinator, I want to list all repositories that
subscriptions make available to the system, so that I can decide which ones to enable.

#### Acceptance Criteria

1. WHEN the coordinator requests the full repository list THE SYSTEM SHALL invoke
   `subscription-manager repos --list` via the command runner.
2. WHEN the coordinator requests only enabled repositories THE SYSTEM SHALL invoke
   `subscription-manager repos --list-enabled`.
3. WHEN the coordinator requests only disabled repositories THE SYSTEM SHALL invoke
   `subscription-manager repos --list-disabled`.
4. THE SYSTEM SHALL return a list of `SubscriptionRepo` records containing repo id,
   name, URL, and enabled state.
5. WHEN the command fails THE SYSTEM SHALL raise a `SubscriptionQueryError`.

---

### Requirement 8: Enable or Disable a Repository

**User Story:** As a maintenance coordinator, I want to enable or disable a specific
subscription-provided repository, so that only the required content is available to the
package manager.

#### Acceptance Criteria

1. WHEN the coordinator requests enabling a repository THE SYSTEM SHALL invoke
   `subscription-manager repos --enable <repo-id>` with elevated privileges.
2. WHEN the coordinator requests disabling a repository THE SYSTEM SHALL invoke
   `subscription-manager repos --disable <repo-id>` with elevated privileges.
3. WHEN the requested repository is already in the desired state THE SYSTEM SHALL skip
   execution and return a `SubscriptionActionResult` with status `SKIPPED`.
4. WHEN the operation succeeds THE SYSTEM SHALL return a `SubscriptionActionResult` with
   status `SUCCESS`.
5. WHEN the operation fails THE SYSTEM SHALL return a `SubscriptionActionResult` with
   status `FAILED`, exit code, and captured stderr.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 9: Set and Query Release Version

**User Story:** As a maintenance coordinator, I want to pin a system to a specific
minor release version, so that updates stay within that release until explicitly rolled
forward.

#### Acceptance Criteria

1. WHEN the coordinator sets a release version THE SYSTEM SHALL invoke
   `subscription-manager release --set <version>` with elevated privileges.
2. WHEN the coordinator clears the release version THE SYSTEM SHALL invoke
   `subscription-manager release --unset` with elevated privileges.
3. WHEN the coordinator queries the current release THE SYSTEM SHALL invoke
   `subscription-manager release --show` and parse the returned release string.
4. WHEN the coordinator lists available releases THE SYSTEM SHALL invoke
   `subscription-manager release --list` and return the available release strings.
5. WHEN any mutating operation succeeds THE SYSTEM SHALL return a
   `SubscriptionActionResult` with status `SUCCESS`.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any mutating command.

---

### Requirement 10: Refresh Subscription Data

**User Story:** As a maintenance coordinator, I want to force a refresh of subscription
and entitlement data, so that any recent changes made on the subscription server are
reflected on the local system.

#### Acceptance Criteria

1. WHEN the coordinator requests a refresh THE SYSTEM SHALL invoke
   `subscription-manager refresh` with elevated privileges.
2. WHEN the refresh succeeds THE SYSTEM SHALL return a `SubscriptionActionResult` with
   status `SUCCESS`.
3. WHEN the refresh fails THE SYSTEM SHALL return a `SubscriptionActionResult` with
   status `FAILED`, exit code, and captured stderr.
4. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 11: Elevated Privilege Execution

**User Story:** As a maintenance coordinator, I want all mutating subscription commands
to run with the privileges required by `subscription-manager`, so that registration and
entitlement changes succeed on standard systems where the process does not run as root.

#### Acceptance Criteria

1. WHEN the system is configured to use sudo elevation THE SYSTEM SHALL prefix all
   `subscription-manager` mutating commands (register, unregister, attach, remove,
   repos --enable/--disable, release --set/--unset, refresh) with the command runner's
   elevation mechanism.
2. WHEN the system is running as root THE SYSTEM SHALL NOT prefix commands with `sudo`.
3. WHEN read-only query commands (`identity`, `status`, `repos --list`,
   `release --show`, `release --list`) are invoked THE SYSTEM SHALL NOT require
   elevation.

---

### Requirement 12: Credential Handling

**User Story:** As a maintenance coordinator, I want credentials and activation keys to
never appear in logs, command traces, or dry-run output, so that secrets are not leaked
through the command-runner auditing pipeline.

#### Acceptance Criteria

1. WHEN the command runner records a command for auditing THE SYSTEM SHALL redact the
   value following `--password` and `--activationkey` to a fixed masking token.
2. WHEN dry-run output is produced THE SYSTEM SHALL redact credentials in the same way.
3. WHEN captured stderr contains the credential string THE SYSTEM SHALL redact it before
   returning the `SubscriptionActionResult`.
4. WHEN a credential is redacted THE SYSTEM SHALL preserve the fact that the argument
   was present in the audit record.
