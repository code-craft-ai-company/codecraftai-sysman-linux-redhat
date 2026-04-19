# Requirements

## Introduction

The kdump management feature provides the `automojo-sysman-linux-redhat` package with
the ability to manage the kernel crash-dump capture subsystem on Red Hat Linux systems:
the `kdump.service` lifecycle, the `/etc/kdump.conf` configuration, the `crashkernel`
kernel command-line reservation, and the kdump initramfs.

A maintenance coordinator uses this feature to bring a system into a state where a
kernel panic produces a usable crash dump. All system commands (`systemctl`,
`kdumpctl`, `kexec`, `grubby`) are executed through the `automojo-interfaces`
command-runner abstraction. All reads and writes against `/etc/kdump.conf` and
`/etc/sysconfig/kdump` use the `automojo-interfaces` file-operator abstraction. All
mutating operations respect dry-run mode.

This feature uses the existing `boot-management` feature for kernel-command-line
changes (`crashkernel=`) and does not duplicate that logic.

---

## Requirements

### Requirement 1: Query kdump Service State

**User Story:** As a maintenance coordinator, I want to query the state of the kdump
service, so that I can verify the capture subsystem is active and armed.

#### Acceptance Criteria

1. WHEN the coordinator queries the service state THE SYSTEM SHALL invoke
   `systemctl is-active kdump` and `systemctl is-enabled kdump` via the command
   runner.
2. WHEN the coordinator queries the armed state THE SYSTEM SHALL invoke
   `kdumpctl status`.
3. THE SYSTEM SHALL return a `KdumpServiceState` record containing `active`,
   `enabled`, `armed`, and (when available) `reserved_memory_bytes`.
4. WHEN the service unit is not present THE SYSTEM SHALL return a record with
   `present=False` and leave the other fields unset.
5. WHEN the query commands fail for any other reason THE SYSTEM SHALL raise a
   `KdumpQueryError`.

---

### Requirement 2: Enable or Disable kdump

**User Story:** As a maintenance coordinator, I want to enable, disable, start, stop,
or restart the kdump service, so that the capture subsystem is in the desired state.

#### Acceptance Criteria

1. WHEN the coordinator requests a lifecycle action (`start`, `stop`, `restart`,
   `enable`, `disable`) THE SYSTEM SHALL invoke `systemctl <action> kdump` with
   elevated privileges.
2. WHEN the service is already in the desired state THE SYSTEM SHALL skip the command
   and return a `KdumpActionResult` with status `SKIPPED`.
3. WHEN enabling kdump AND no `crashkernel` reservation is present on the running
   kernel command line THE SYSTEM SHALL still attempt the enable but SHALL include a
   warning indicating the reservation is missing.
4. WHEN the operation succeeds THE SYSTEM SHALL return a `KdumpActionResult` with
   status `SUCCESS`.
5. WHEN the operation fails THE SYSTEM SHALL return a `KdumpActionResult` with status
   `FAILED`, exit code, and captured stderr.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 3: Query /etc/kdump.conf

**User Story:** As a maintenance coordinator, I want to query the current kdump
configuration keys, so that I can verify the target dump location, compressor, and
actions match the declared desired state.

#### Acceptance Criteria

1. WHEN the coordinator requests a configuration value by key THE SYSTEM SHALL read
   `/etc/kdump.conf` through the file operator, parse `<key> <value>` whitespace-
   separated lines, and return the first value or `None`.
2. WHEN the coordinator requests the full parsed configuration THE SYSTEM SHALL
   return an ordered dict of keys to values.
3. WHEN the configuration file does not exist THE SYSTEM SHALL raise a
   `KdumpConfigNotFoundError`.
4. WHEN the file read fails for any other reason THE SYSTEM SHALL raise a
   `KdumpQueryError`.

---

### Requirement 4: Set /etc/kdump.conf Keys

**User Story:** As a maintenance coordinator, I want to set or replace keys in
`/etc/kdump.conf`, so that the capture target, action on failure, compressor, and
other options reflect the declared desired state.

#### Acceptance Criteria

1. WHEN the coordinator requests a set-or-replace THE SYSTEM SHALL update the matching
   `<key> <value>` line in place, preserving comments and ordering, or append the
   line if absent, and write the file back with mode `0600`, owner `root`, group
   `root`, and elevated privileges.
2. WHEN the coordinator requests deletion of a key THE SYSTEM SHALL remove the
   matching line.
3. WHEN the current value already matches the requested value THE SYSTEM SHALL skip
   the write and return a `KdumpActionResult` with status `SKIPPED`.
4. WHEN the write succeeds THE SYSTEM SHALL return a `KdumpActionResult` with status
   `SUCCESS` and include a note indicating that `kdumpctl rebuild` and a service
   restart are required for some keys to take effect.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT write to the file.

---

### Requirement 5: Query crashkernel Reservation

**User Story:** As a maintenance coordinator, I want to query the active and
persistent `crashkernel` reservation, so that I can verify adequate memory is
reserved for kdump.

#### Acceptance Criteria

1. WHEN the coordinator queries the runtime reservation THE SYSTEM SHALL read
   `/proc/cmdline`, locate the `crashkernel=` parameter, and return its raw value
   string or `None`.
2. WHEN the coordinator queries the persistent reservation for a specific kernel THE
   SYSTEM SHALL invoke `grubby --info=<path>` and extract `crashkernel=...` from the
   `args` field.
3. THE SYSTEM SHALL return a `CrashkernelReservation` record containing the raw
   specifier (e.g. `1G-4G:192M,4G-64G:256M,64G-:512M`) and, when parseable, a list of
   `(range, size)` tuples.
4. WHEN no reservation is present THE SYSTEM SHALL return `None`.

---

### Requirement 6: Set crashkernel Reservation

**User Story:** As a maintenance coordinator, I want to set the `crashkernel`
reservation on installed kernels, so that kdump has memory reserved at the next boot.

#### Acceptance Criteria

1. WHEN the coordinator requests setting `crashkernel=<value>` across all kernels THE
   SYSTEM SHALL delegate to the `boot-management` feature's "Add Kernel Command-Line
   Arguments" capability, supplying the fully formed `crashkernel=<value>` argument.
2. WHEN the coordinator requests removal THE SYSTEM SHALL delegate to the
   `boot-management` feature's "Remove Kernel Command-Line Arguments" capability,
   supplying `crashkernel`.
3. WHEN the requested value already matches the persistent value on every targeted
   kernel THE SYSTEM SHALL skip execution and return a `KdumpActionResult` with
   status `SKIPPED`.
4. WHEN the delegate operation returns `SUCCESS` THE SYSTEM SHALL return a
   `KdumpActionResult` with status `SUCCESS` and include a note that a reboot is
   required for the reservation to take effect.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command through the
   delegate.

---

### Requirement 7: Rebuild kdump initramfs

**User Story:** As a maintenance coordinator, I want to rebuild the kdump initramfs
after configuration changes, so that the capture kernel can mount the target dump
location.

#### Acceptance Criteria

1. WHEN the coordinator requests a rebuild THE SYSTEM SHALL invoke
   `kdumpctl rebuild` with elevated privileges.
2. WHEN the rebuild succeeds THE SYSTEM SHALL return a `KdumpActionResult` with
   status `SUCCESS`.
3. WHEN the rebuild fails THE SYSTEM SHALL return a `KdumpActionResult` with status
   `FAILED`, exit code, and captured stderr.
4. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 8: Verify Reserved Memory Is Loaded

**User Story:** As a maintenance coordinator, I want to verify that the capture kernel
has been loaded into the reserved memory region, so that I know kdump is actually
armed.

#### Acceptance Criteria

1. WHEN the coordinator requests a verify THE SYSTEM SHALL read
   `/sys/kernel/kexec_crash_loaded` through the file operator and interpret `"1"` as
   loaded and `"0"` as not loaded.
2. WHEN the coordinator requests reserved size THE SYSTEM SHALL read
   `/sys/kernel/kexec_crash_size` and return the value as bytes.
3. THE SYSTEM SHALL return a `KdumpArmedState` record containing `loaded`, a boolean,
   and `reserved_bytes`, an integer.
4. WHEN either file is absent THE SYSTEM SHALL return the record with `loaded=False`
   and `reserved_bytes=0`.

---

### Requirement 9: Query and Set /etc/sysconfig/kdump

**User Story:** As a maintenance coordinator, I want to read and update keys in
`/etc/sysconfig/kdump`, so that environment-style kdump settings (e.g.
`KDUMP_COMMANDLINE_APPEND`, `KDUMP_IMG`, `KEXEC_ARGS`) can be controlled.

#### Acceptance Criteria

1. WHEN the coordinator queries a key THE SYSTEM SHALL read `/etc/sysconfig/kdump`
   and return the `KEY=value` value or `None`.
2. WHEN the coordinator requests a set-or-replace THE SYSTEM SHALL update or append
   the key, preserve existing quoting style, and write the file back with mode
   `0644`, owner `root`, group `root`, and elevated privileges.
3. WHEN the current value already matches the requested value THE SYSTEM SHALL skip
   the write and return a `KdumpActionResult` with status `SKIPPED`.
4. WHEN the write succeeds THE SYSTEM SHALL return a `KdumpActionResult` with status
   `SUCCESS` and include a note that a kdump service restart is required.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT write to the file.

---

### Requirement 10: Elevated Privilege Execution

**User Story:** As a maintenance coordinator, I want all mutating kdump commands to
run with the privileges required by the kdump tooling, so that operations succeed on
standard systems where the process does not run as root.

#### Acceptance Criteria

1. WHEN the system is configured to use sudo elevation THE SYSTEM SHALL prefix all
   mutating commands (`systemctl <action> kdump`, `kdumpctl rebuild`, writes under
   `/etc/kdump.conf` and `/etc/sysconfig/kdump`) with the command runner's elevation
   mechanism.
2. WHEN the system is running as root THE SYSTEM SHALL NOT prefix commands with
   `sudo`.
3. WHEN read-only query commands (`systemctl is-*`, `kdumpctl status`, reads of
   `/proc/cmdline` and `/sys/kernel/kexec_crash_*`) are invoked THE SYSTEM SHALL NOT
   require elevation.
