# Requirements

## Introduction

The SSH server configuration feature provides the `automojo-sysman-linux-redhat`
package with the ability to manage the OpenSSH server configuration on Red Hat
Linux systems: the service lifecycle, the main `/etc/ssh/sshd_config`, drop-in files
under `/etc/ssh/sshd_config.d/`, `Match` blocks, host-key files, and configuration
validation.

A maintenance coordinator uses this feature to apply a declared SSH-server posture
to the target system. It complements — and does not duplicate — the SSH hardening
checks already provided by the `security-compliance` feature. It complements — and
does not duplicate — the per-user `authorized_keys` management already provided by
the `user-group-management` feature.

All system commands (`sshd`, `systemctl`, `ssh-keygen`) are executed through the
`automojo-interfaces` command-runner abstraction. All reads and writes under
`/etc/ssh/` use the `automojo-interfaces` file-operator abstraction. All mutating
operations respect dry-run mode.

---

## Requirements

### Requirement 1: Query sshd Service State

**User Story:** As a maintenance coordinator, I want to query the state of the sshd
service, so that I can verify remote login is (or is not) available.

#### Acceptance Criteria

1. WHEN the coordinator queries the service THE SYSTEM SHALL invoke
   `systemctl is-active sshd` and `systemctl is-enabled sshd`.
2. THE SYSTEM SHALL return an `SshdServiceState` record containing `active`,
   `enabled`, and (when available) `main_pid`.
3. WHEN the service unit is not present THE SYSTEM SHALL return a record with
   `present=False` and leave the other fields unset.
4. WHEN the query commands fail for any other reason THE SYSTEM SHALL raise an
   `SshdQueryError`.

---

### Requirement 2: Query Effective sshd Configuration

**User Story:** As a maintenance coordinator, I want to query the effective value of
a sshd configuration directive as it will be seen by the daemon, so that drift from
the declared desired state can be detected.

#### Acceptance Criteria

1. WHEN the coordinator requests a single directive's effective value THE SYSTEM
   SHALL invoke `sshd -T` through the command runner with elevated privileges and
   parse the `<directive> <value>` lines from the output.
2. WHEN the coordinator requests the full effective configuration THE SYSTEM SHALL
   invoke `sshd -T` and return an ordered dict of directive names to values.
3. WHEN `sshd -T` exits non-zero (e.g. invalid config) THE SYSTEM SHALL raise an
   `SshdQueryError` with the captured stderr.
4. WHEN dry-run mode is active THE SYSTEM SHALL still execute `sshd -T` since it is
   read-only.

---

### Requirement 3: Set a Directive via a Drop-In File

**User Story:** As a maintenance coordinator, I want to set a sshd directive in a
named drop-in under `/etc/ssh/sshd_config.d/`, so that site overrides are
separately addressable and don't muddle the distribution-supplied main file.

#### Acceptance Criteria

1. WHEN the coordinator requests `set(<name>, <directive>, <value>)` THE SYSTEM
   SHALL write `/etc/ssh/sshd_config.d/<name>.conf` such that the file contains a
   single line `<directive> <value>` (or merges into an existing drop-in of the
   same name, preserving unrelated directives), with mode `0600`, owner `root`,
   group `root`, and elevated privileges.
2. WHEN the drop-in already contains the same directive and value THE SYSTEM SHALL
   skip the write and return an `SshdActionResult` with status `SKIPPED`.
3. WHEN the coordinator requests deletion of a directive from a drop-in THE SYSTEM
   SHALL remove the matching line; if the drop-in becomes empty the SYSTEM SHALL
   delete the file.
4. WHEN the write succeeds THE SYSTEM SHALL invoke `sshd -t` to validate the
   resulting configuration; if validation fails THE SYSTEM SHALL restore the
   previous content via the file operator's rollback and return an
   `SshdActionResult` with status `FAILED`.
5. WHEN validation passes THE SYSTEM SHALL return an `SshdActionResult` with status
   `SUCCESS` and include a note indicating that `systemctl reload sshd` is
   required for the change to take effect.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT write or delete any file.

---

### Requirement 4: List Drop-In Files

**User Story:** As a maintenance coordinator, I want to list all drop-in files and
their contents, so that the layering of sshd configuration can be audited.

#### Acceptance Criteria

1. WHEN the coordinator requests the list THE SYSTEM SHALL enumerate
   `/etc/ssh/sshd_config.d/*.conf`.
2. THE SYSTEM SHALL return a list of `SshdDropIn` records containing `name`, full
   `path`, and the parsed `(directive, value)` tuples in their file order.
3. WHEN the directory does not exist THE SYSTEM SHALL return an empty list.
4. WHEN a file cannot be parsed THE SYSTEM SHALL include it in the list with
   `valid=False` and the captured error.

---

### Requirement 5: Manage Match Blocks

**User Story:** As a maintenance coordinator, I want to declare, update, and remove
`Match` blocks that apply directives to specific users, groups, hosts, or addresses,
so that policy can be refined without editing the top-level configuration.

#### Acceptance Criteria

1. WHEN the coordinator supplies a match spec (`User`, `Group`, `Host`, `Address`,
   or a combination) and a set of directive-value pairs THE SYSTEM SHALL write a
   drop-in under `/etc/ssh/sshd_config.d/<name>.conf` that contains a
   `Match <spec>` line followed by the supplied directive-value lines, with mode
   `0600`, owner `root`, group `root`, and elevated privileges.
2. WHEN the coordinator requests removal of a named Match block THE SYSTEM SHALL
   delete the corresponding drop-in.
3. WHEN the existing file already represents the same match spec and directive set
   byte-for-byte THE SYSTEM SHALL skip the write and return an `SshdActionResult`
   with status `SKIPPED`.
4. WHEN the write succeeds THE SYSTEM SHALL invoke `sshd -t` to validate; on
   failure the SYSTEM SHALL restore the previous content and return an
   `SshdActionResult` with status `FAILED`.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT write or delete any file.

---

### Requirement 6: Validate Configuration

**User Story:** As a maintenance coordinator, I want to explicitly validate the
entire sshd configuration, so that composite changes can be verified without
restarting the daemon.

#### Acceptance Criteria

1. WHEN the coordinator requests a validate THE SYSTEM SHALL invoke `sshd -t` with
   elevated privileges.
2. WHEN validation passes THE SYSTEM SHALL return an `SshdValidateResult` with
   `ok=True`.
3. WHEN validation fails THE SYSTEM SHALL return an `SshdValidateResult` with
   `ok=False` and the captured stderr.
4. WHEN `sshd` itself is not installed THE SYSTEM SHALL raise an
   `SshdNotInstalledError`.

---

### Requirement 7: Reload Configuration

**User Story:** As a maintenance coordinator, I want to reload the sshd
configuration, so that configuration changes take effect without dropping existing
connections.

#### Acceptance Criteria

1. WHEN the coordinator requests a reload THE SYSTEM SHALL first invoke the
   validate capability (Requirement 6). If validation fails the SYSTEM SHALL NOT
   reload and SHALL return an `SshdActionResult` with status `FAILED` carrying the
   validation error.
2. WHEN validation passes THE SYSTEM SHALL invoke `systemctl reload sshd` with
   elevated privileges.
3. WHEN the reload succeeds THE SYSTEM SHALL return an `SshdActionResult` with
   status `SUCCESS`.
4. WHEN the reload fails THE SYSTEM SHALL return an `SshdActionResult` with status
   `FAILED`, exit code, and captured stderr.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 8: Manage Host Keys

**User Story:** As a maintenance coordinator, I want to manage the server host-key
files, so that key algorithms can be rotated or new host keys can be generated and
installed.

#### Acceptance Criteria

1. WHEN the coordinator requests generation of a new host key of a specific type
   (`rsa`, `ecdsa`, `ed25519`) THE SYSTEM SHALL invoke
   `ssh-keygen -t <type> -f /etc/ssh/ssh_host_<type>_key -N ''` with elevated
   privileges.
2. WHEN the target host key file already exists AND `force=False` THE SYSTEM SHALL
   skip and return an `SshdActionResult` with status `SKIPPED`.
3. WHEN `force=True` THE SYSTEM SHALL delete the existing private and public key
   files before running `ssh-keygen`.
4. WHEN the coordinator requests removal of a host-key type THE SYSTEM SHALL
   delete `/etc/ssh/ssh_host_<type>_key` and `/etc/ssh/ssh_host_<type>_key.pub`.
5. THE SYSTEM SHALL ensure generated private keys have mode `0600` and public keys
   have mode `0644`, with owner and group `root`.
6. WHEN generation succeeds THE SYSTEM SHALL return an `SshdActionResult` with
   status `SUCCESS` and the captured public-key fingerprint.
7. WHEN dry-run mode is active THE SYSTEM SHALL NOT create or delete any file.

---

### Requirement 9: Query Host Key Fingerprints

**User Story:** As a maintenance coordinator, I want to query the fingerprints of
the installed host keys, so that they can be recorded in the inventory and
advertised to clients.

#### Acceptance Criteria

1. WHEN the coordinator requests the fingerprints THE SYSTEM SHALL enumerate
   `/etc/ssh/ssh_host_*_key.pub` and invoke `ssh-keygen -l -f <path>` for each.
2. THE SYSTEM SHALL return a list of `HostKeyFingerprint` records containing
   `type`, `bits`, `sha256_fingerprint`, and `comment`.
3. WHEN no host-key files are present THE SYSTEM SHALL return an empty list.
4. WHEN `ssh-keygen` fails for any file THE SYSTEM SHALL include the file in the
   result with `valid=False` and the captured error.

---

### Requirement 10: Ensure Banner Content

**User Story:** As a maintenance coordinator, I want to ensure the login banner
file exists with required content, so that legal or operational banner requirements
are met.

#### Acceptance Criteria

1. WHEN the coordinator supplies a banner text and path THE SYSTEM SHALL write the
   file through the file operator with mode `0644`, owner `root`, group `root`, and
   elevated privileges.
2. WHEN the existing file content matches byte-for-byte THE SYSTEM SHALL skip the
   write and return an `SshdActionResult` with status `SKIPPED`.
3. WHEN the coordinator also requests that the `Banner` directive point to the
   supplied path THE SYSTEM SHALL call the Drop-In capability (Requirement 3) to
   set `Banner <path>`.
4. WHEN dry-run mode is active THE SYSTEM SHALL NOT write any file.

---

### Requirement 11: Elevated Privilege Execution

**User Story:** As a maintenance coordinator, I want all mutating sshd operations
to run with the privileges required by the tool, so that operations succeed on
standard systems where the process does not run as root.

#### Acceptance Criteria

1. WHEN the system is configured to use sudo elevation THE SYSTEM SHALL prefix all
   mutating commands (`systemctl reload sshd`, `ssh-keygen` on `/etc/ssh/`, writes
   and deletes under `/etc/ssh/sshd_config.d/` and `/etc/ssh/`) with the command
   runner's elevation mechanism.
2. WHEN the system is running as root THE SYSTEM SHALL NOT prefix commands with
   `sudo`.
3. WHEN `sshd -T` or `sshd -t` is invoked THE SYSTEM SHALL use elevation because
   those commands read host-key files.
4. WHEN read-only file operator queries (listing drop-ins, reading banner) do not
   require elevated access THE SYSTEM SHALL NOT elevate.
