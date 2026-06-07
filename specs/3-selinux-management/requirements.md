# Requirements

## Introduction

The SELinux management feature provides the `codecraftai-sysman-linux-redhat` package with
the ability to manage the full SELinux surface on Red Hat Linux systems: file and port
contexts, policy booleans, policy modules, mode persistence, and audit-driven policy
generation. It complements — and does not duplicate — the coarse SELinux mode query and
`setenforce` operations already provided by the `security-compliance` feature.

A maintenance coordinator uses this feature to bring a system's SELinux posture into a
declared desired state, apply labeling, and adjust policy. All commands (`getenforce`,
`sestatus`, `getsebool`, `setsebool`, `semanage`, `semodule`, `chcon`, `restorecon`,
`matchpathcon`, `audit2allow`) are executed through the `codecraftai-interfaces`
command-runner abstraction. All reads and writes against `/etc/selinux/config` and
policy `.pp` files use the `codecraftai-interfaces` file-operator abstraction. All mutating
operations respect dry-run mode.

---

## Requirements

### Requirement 1: Query SELinux Context of a Path

**User Story:** As a maintenance coordinator, I want to query the current SELinux
security context of a file or directory, so that I can compare it to the expected
context before applying changes.

#### Acceptance Criteria

1. WHEN the coordinator queries the context of a path THE SYSTEM SHALL invoke
   `stat --printf='%C' <path>` via the command runner.
2. WHEN the path exists THE SYSTEM SHALL return a `SELinuxContext` record containing
   user, role, type, and sensitivity-level strings parsed from the returned
   `user:role:type:level` value.
3. WHEN the path does not exist THE SYSTEM SHALL raise a `SELinuxPathNotFoundError`.
4. WHEN SELinux is disabled THE SYSTEM SHALL return `None`.

---

### Requirement 2: Set SELinux Context on a Path (Runtime)

**User Story:** As a maintenance coordinator, I want to apply a specific SELinux type to
a path at runtime, so that access decisions for the target match the desired policy.

#### Acceptance Criteria

1. WHEN the coordinator requests a type change THE SYSTEM SHALL invoke
   `chcon -t <type> <path>` via the command runner with elevated privileges.
2. WHEN the coordinator requests a recursive change THE SYSTEM SHALL invoke
   `chcon -R -t <type> <path>`.
3. WHEN the coordinator also supplies user or role THE SYSTEM SHALL append `-u <user>`
   and `-r <role>` accordingly.
4. WHEN the target path already has the requested context THE SYSTEM SHALL skip
   execution and return a `SELinuxActionResult` with status `SKIPPED`.
5. WHEN the operation succeeds THE SYSTEM SHALL return a `SELinuxActionResult` with
   status `SUCCESS`.
6. WHEN the operation fails THE SYSTEM SHALL return a `SELinuxActionResult` with status
   `FAILED`, exit code, and captured stderr.
7. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 3: Restore Default Context

**User Story:** As a maintenance coordinator, I want to restore the default policy
context on a path, so that any drift from the policy is corrected.

#### Acceptance Criteria

1. WHEN the coordinator requests a restore THE SYSTEM SHALL invoke
   `restorecon -v <path>` via the command runner with elevated privileges.
2. WHEN the coordinator requests recursion THE SYSTEM SHALL invoke
   `restorecon -R -v <path>`.
3. WHEN the coordinator requests force-relabel THE SYSTEM SHALL add `-F` to the command.
4. WHEN the path's current context already matches the default THE SYSTEM SHALL return
   a `SELinuxActionResult` with status `SUCCESS` and a `changed=False` indicator.
5. WHEN the operation fails THE SYSTEM SHALL return a `SELinuxActionResult` with status
   `FAILED`, exit code, and captured stderr.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 4: Add Persistent File Context Rule

**User Story:** As a maintenance coordinator, I want to declare a persistent file
context rule in the local policy store, so that newly created files under a path get
the correct type automatically.

#### Acceptance Criteria

1. WHEN the coordinator requests a new file context rule THE SYSTEM SHALL invoke
   `semanage fcontext -a -t <type> "<path-regex>"` with elevated privileges.
2. WHEN a rule with the same regex already exists THE SYSTEM SHALL invoke
   `semanage fcontext -m -t <type> "<path-regex>"` to modify it.
3. WHEN the rule is added THE SYSTEM SHALL return a `SELinuxActionResult` with status
   `SUCCESS`.
4. WHEN the command fails THE SYSTEM SHALL return a `SELinuxActionResult` with status
   `FAILED`, exit code, and captured stderr.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 5: Remove Persistent File Context Rule

**User Story:** As a maintenance coordinator, I want to remove a local file context
rule, so that custom labeling is cleaned up when no longer needed.

#### Acceptance Criteria

1. WHEN the coordinator requests removal THE SYSTEM SHALL invoke
   `semanage fcontext -d "<path-regex>"` with elevated privileges.
2. WHEN the rule does not exist THE SYSTEM SHALL skip execution and return a
   `SELinuxActionResult` with status `SKIPPED`.
3. WHEN the rule is removed THE SYSTEM SHALL return a `SELinuxActionResult` with status
   `SUCCESS`.
4. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 6: List File Context Rules

**User Story:** As a maintenance coordinator, I want to list all local file context
rules, so that I can audit the policy additions made on the system.

#### Acceptance Criteria

1. WHEN the coordinator requests the list THE SYSTEM SHALL invoke
   `semanage fcontext -l -C` to list only local (customized) rules.
2. WHEN the coordinator requests the full list THE SYSTEM SHALL invoke
   `semanage fcontext -l`.
3. THE SYSTEM SHALL return a list of `SELinuxFileContextRule` records containing the
   path regex, file type indicator, and SELinux type.
4. WHEN the command fails THE SYSTEM SHALL raise a `SELinuxQueryError`.

---

### Requirement 7: Query Policy Boolean

**User Story:** As a maintenance coordinator, I want to query the runtime value of a
policy boolean, so that I can decide whether to toggle it.

#### Acceptance Criteria

1. WHEN the coordinator queries a single boolean THE SYSTEM SHALL invoke
   `getsebool <name>` via the command runner.
2. WHEN the boolean exists THE SYSTEM SHALL return `True` or `False` parsed from the
   command output.
3. WHEN the boolean does not exist THE SYSTEM SHALL raise a `SELinuxBooleanNotFoundError`.
4. WHEN the coordinator requests all booleans THE SYSTEM SHALL invoke
   `getsebool -a` and return a dict of `{name: bool}`.

---

### Requirement 8: Set Policy Boolean

**User Story:** As a maintenance coordinator, I want to toggle a policy boolean both at
runtime and persistently, so that its value survives reboots.

#### Acceptance Criteria

1. WHEN the coordinator requests a persistent boolean change THE SYSTEM SHALL invoke
   `setsebool -P <name> on|off` with elevated privileges.
2. WHEN the coordinator requests a runtime-only change THE SYSTEM SHALL invoke
   `setsebool <name> on|off` without the `-P` flag.
3. WHEN the boolean is already at the target value THE SYSTEM SHALL skip execution and
   return a `SELinuxActionResult` with status `SKIPPED`.
4. WHEN the operation succeeds THE SYSTEM SHALL return a `SELinuxActionResult` with
   status `SUCCESS`.
5. WHEN the operation fails THE SYSTEM SHALL return a `SELinuxActionResult` with status
   `FAILED`, exit code, and captured stderr.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 9: List Booleans with Metadata

**User Story:** As a maintenance coordinator, I want to list booleans with their
runtime value, persistent value, and description, so that I can audit deviations from
the default policy.

#### Acceptance Criteria

1. WHEN the coordinator requests the metadata list THE SYSTEM SHALL invoke
   `semanage boolean -l`.
2. THE SYSTEM SHALL return a list of `SELinuxBoolean` records containing the name,
   current value, default value, and description.
3. WHEN the command fails THE SYSTEM SHALL raise a `SELinuxQueryError`.

---

### Requirement 10: Add Port Context

**User Story:** As a maintenance coordinator, I want to associate a TCP or UDP port
with a SELinux type, so that a service can bind to a non-default port under confinement.

#### Acceptance Criteria

1. WHEN the coordinator requests a new port context THE SYSTEM SHALL invoke
   `semanage port -a -t <type> -p <tcp|udp> <port>` with elevated privileges.
2. WHEN a port context already exists for the same port and protocol THE SYSTEM SHALL
   invoke `semanage port -m -t <type> -p <proto> <port>` to modify it.
3. WHEN the operation succeeds THE SYSTEM SHALL return a `SELinuxActionResult` with
   status `SUCCESS`.
4. WHEN the operation fails THE SYSTEM SHALL return a `SELinuxActionResult` with status
   `FAILED`, exit code, and captured stderr.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 11: Remove Port Context

**User Story:** As a maintenance coordinator, I want to remove a locally-declared port
context, so that a port is no longer reserved for a specific SELinux type.

#### Acceptance Criteria

1. WHEN the coordinator requests removal THE SYSTEM SHALL invoke
   `semanage port -d -p <proto> <port>` with elevated privileges.
2. WHEN no local port context exists for the port THE SYSTEM SHALL skip execution and
   return a `SELinuxActionResult` with status `SKIPPED`.
3. WHEN the removal succeeds THE SYSTEM SHALL return a `SELinuxActionResult` with
   status `SUCCESS`.
4. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 12: List Port Contexts

**User Story:** As a maintenance coordinator, I want to list port contexts, so that I
can audit the ports reserved to SELinux types.

#### Acceptance Criteria

1. WHEN the coordinator requests the list THE SYSTEM SHALL invoke `semanage port -l`.
2. WHEN the coordinator requests only local rules THE SYSTEM SHALL invoke
   `semanage port -l -C`.
3. THE SYSTEM SHALL return a list of `SELinuxPortRule` records containing the type,
   protocol, and port range.
4. WHEN the command fails THE SYSTEM SHALL raise a `SELinuxQueryError`.

---

### Requirement 13: Install Policy Module

**User Story:** As a maintenance coordinator, I want to install a compiled SELinux
policy module (`.pp` file), so that custom policy shipped with the maintenance bundle
becomes effective.

#### Acceptance Criteria

1. WHEN the coordinator requests installation THE SYSTEM SHALL verify the path exists
   and raise `SELinuxPathNotFoundError` if it does not.
2. WHEN the path exists THE SYSTEM SHALL invoke `semodule -i <path>` with elevated
   privileges.
3. WHEN the module is already installed at the same priority THE SYSTEM SHALL skip
   execution and return a `SELinuxActionResult` with status `SKIPPED`.
4. WHEN installation succeeds THE SYSTEM SHALL return a `SELinuxActionResult` with
   status `SUCCESS`.
5. WHEN installation fails THE SYSTEM SHALL return a `SELinuxActionResult` with status
   `FAILED`, exit code, and captured stderr.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 14: Remove Policy Module

**User Story:** As a maintenance coordinator, I want to remove an installed policy
module by name, so that policy additions can be rolled back.

#### Acceptance Criteria

1. WHEN the coordinator requests removal THE SYSTEM SHALL invoke
   `semodule -r <name>` with elevated privileges.
2. WHEN the module is not installed THE SYSTEM SHALL skip execution and return a
   `SELinuxActionResult` with status `SKIPPED`.
3. WHEN removal succeeds THE SYSTEM SHALL return a `SELinuxActionResult` with status
   `SUCCESS`.
4. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 15: List Installed Policy Modules

**User Story:** As a maintenance coordinator, I want to list installed policy modules,
so that I can audit what custom policy is active on the system.

#### Acceptance Criteria

1. WHEN the coordinator requests the list THE SYSTEM SHALL invoke `semodule -l`.
2. THE SYSTEM SHALL return a list of `SELinuxModule` records containing the module name
   and, where available, priority.
3. WHEN the command fails THE SYSTEM SHALL raise a `SELinuxQueryError`.

---

### Requirement 16: Persist SELinux Mode in Configuration

**User Story:** As a maintenance coordinator, I want to change the SELinux mode recorded
in `/etc/selinux/config`, so that the mode persists across reboots. Runtime mode
toggling remains the responsibility of the `security-compliance` feature.

#### Acceptance Criteria

1. WHEN the coordinator requests a persistent mode change THE SYSTEM SHALL read
   `/etc/selinux/config`, replace the `SELINUX=` line with the requested value
   (`enforcing`, `permissive`, or `disabled`), and write it back through the file
   operator with elevated privileges.
2. WHEN the current persistent value already matches the requested value THE SYSTEM
   SHALL skip the write and return a `SELinuxActionResult` with status `SKIPPED`.
3. WHEN the persistent change is `disabled` THE SYSTEM SHALL include in the returned
   `SELinuxActionResult` a warning indicating a reboot is required.
4. WHEN the write succeeds THE SYSTEM SHALL return a `SELinuxActionResult` with status
   `SUCCESS`.
5. WHEN the write fails THE SYSTEM SHALL return a `SELinuxActionResult` with status
   `FAILED` and the captured error.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT write to the file.

---

### Requirement 17: Generate Allow Rules from Audit Denials

**User Story:** As a maintenance coordinator, I want to generate a policy module that
allows the denials currently present in the audit log, so that a targeted exception can
be produced for review.

#### Acceptance Criteria

1. WHEN the coordinator requests a preview THE SYSTEM SHALL invoke `audit2allow -a`.
2. WHEN the coordinator requests a compiled module THE SYSTEM SHALL invoke
   `audit2allow -a -M <module-name>` in a caller-supplied working directory.
3. THE SYSTEM SHALL return an `SELinuxGeneratedModule` record containing the generated
   `.pp` and `.te` file paths and the raw rule output.
4. WHEN the generation fails THE SYSTEM SHALL raise a `SELinuxQueryError`.
5. THE SYSTEM SHALL NOT install the generated module; installation remains an explicit
   caller action via Requirement 13.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT produce any files and SHALL return
   the raw rule preview only.

---

### Requirement 18: Elevated Privilege Execution

**User Story:** As a maintenance coordinator, I want all mutating SELinux commands to
run with the privileges required by the tool, so that operations succeed on standard
systems where the process does not run as root.

#### Acceptance Criteria

1. WHEN the system is configured to use sudo elevation THE SYSTEM SHALL prefix all
   mutating commands (`chcon`, `restorecon`, `setsebool -P`, `semanage -a/-m/-d`,
   `semodule -i/-r`) with the command runner's elevation mechanism.
2. WHEN the system is running as root THE SYSTEM SHALL NOT prefix commands with `sudo`.
3. WHEN read-only query commands (`getenforce`, `sestatus`, `getsebool`,
   `semanage -l`, `semodule -l`, `stat -Z`) are invoked THE SYSTEM SHALL NOT require
   elevation.
