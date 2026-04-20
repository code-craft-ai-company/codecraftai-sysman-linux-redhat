# Requirements

## Introduction

The boot management feature provides the `automojo-sysman-linux-redhat` package with
the ability to manage boot-time behavior on Red Hat Linux systems: the default systemd
boot target, the installed kernels known to the bootloader, the default kernel, kernel
command-line arguments, and the GRUB2 configuration.

A maintenance coordinator uses this feature to apply a declared boot posture to the
target system. All commands (`systemctl get-default`, `systemctl set-default`,
`grubby`, `grub2-mkconfig`, `grub2-editenv`, `bootctl`) are executed through the
`automojo-interfaces` command-runner abstraction. All reads and writes against
`/etc/default/grub`, `/etc/default/grub.d/*.cfg`, `/boot/grub2/grub.cfg`, and
`/boot/efi/EFI/redhat/grub.cfg` use the `automojo-interfaces` file-operator
abstraction. All mutating operations respect dry-run mode.

---

## Requirements

### Requirement 1: Query Default Systemd Boot Target

**User Story:** As a maintenance coordinator, I want to query the current default
systemd target, so that I can verify the system will boot into the expected runlevel.

#### Acceptance Criteria

1. WHEN the coordinator queries the default target THE SYSTEM SHALL invoke
   `systemctl get-default` via the command runner.
2. THE SYSTEM SHALL return a `BootTarget` record containing the unit name (e.g.
   `multi-user.target`, `graphical.target`, `rescue.target`).
3. WHEN the query fails THE SYSTEM SHALL raise a `BootQueryError` with the exit code
   and captured stderr.

---

### Requirement 2: Set Default Systemd Boot Target

**User Story:** As a maintenance coordinator, I want to set the default systemd target,
so that the system boots to the desired runlevel after a reboot.

#### Acceptance Criteria

1. WHEN the coordinator requests a new default THE SYSTEM SHALL invoke
   `systemctl set-default <target>` with elevated privileges.
2. WHEN the supplied target is already the default THE SYSTEM SHALL skip execution and
   return a `BootActionResult` with status `SKIPPED`.
3. WHEN the target unit does not exist on the system THE SYSTEM SHALL return a
   `BootActionResult` with status `FAILED` and an explanatory error message, without
   changing any state.
4. WHEN the operation succeeds THE SYSTEM SHALL return a `BootActionResult` with status
   `SUCCESS`.
5. WHEN the operation fails THE SYSTEM SHALL return a `BootActionResult` with status
   `FAILED`, exit code, and captured stderr.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 3: List Installed Kernels

**User Story:** As a maintenance coordinator, I want to list all kernels that the
bootloader knows about, so that I can make decisions about which kernel to boot by
default or to remove.

#### Acceptance Criteria

1. WHEN the coordinator requests the kernel list THE SYSTEM SHALL invoke
   `grubby --info=ALL`.
2. THE SYSTEM SHALL return a list of `KernelEntry` records containing `index`,
   `kernel` path, `args` string, `root` device, `initrd` path, and `title`.
3. WHEN no kernel entries are present THE SYSTEM SHALL return an empty list.
4. WHEN the command fails THE SYSTEM SHALL raise a `BootQueryError`.

---

### Requirement 4: Query and Set Default Kernel

**User Story:** As a maintenance coordinator, I want to query and set the default
kernel used by the bootloader, so that I can control which kernel is booted on the
next reboot.

#### Acceptance Criteria

1. WHEN the coordinator queries the default kernel THE SYSTEM SHALL invoke
   `grubby --default-kernel` and return the reported path.
2. WHEN the coordinator queries the default index THE SYSTEM SHALL invoke
   `grubby --default-index` and return the reported integer.
3. WHEN the coordinator requests a new default by path THE SYSTEM SHALL invoke
   `grubby --set-default=<path>` with elevated privileges.
4. WHEN the coordinator requests a new default by index THE SYSTEM SHALL invoke
   `grubby --set-default-index=<n>` with elevated privileges.
5. WHEN the supplied kernel path is already the default THE SYSTEM SHALL skip
   execution and return a `BootActionResult` with status `SKIPPED`.
6. WHEN the set succeeds THE SYSTEM SHALL return a `BootActionResult` with status
   `SUCCESS`.
7. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any mutating command.

---

### Requirement 5: Add Kernel Command-Line Arguments

**User Story:** As a maintenance coordinator, I want to add a kernel command-line
argument to all installed kernels (or a specific one), so that required kernel
parameters are applied at the next boot.

#### Acceptance Criteria

1. WHEN the coordinator requests adding args to all kernels THE SYSTEM SHALL invoke
   `grubby --update-kernel=ALL --args="<args>"` with elevated privileges.
2. WHEN the coordinator requests adding args to a specific kernel path THE SYSTEM
   SHALL invoke `grubby --update-kernel=<path> --args="<args>"` with elevated
   privileges.
3. WHEN every targeted kernel already contains the supplied argument with the same
   value THE SYSTEM SHALL skip execution and return a `BootActionResult` with status
   `SKIPPED`.
4. WHEN the operation succeeds THE SYSTEM SHALL return a `BootActionResult` with
   status `SUCCESS`.
5. WHEN the operation fails THE SYSTEM SHALL return a `BootActionResult` with status
   `FAILED`, exit code, and captured stderr.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 6: Remove Kernel Command-Line Arguments

**User Story:** As a maintenance coordinator, I want to remove a kernel command-line
argument from all or selected kernels, so that obsolete or incorrect parameters do not
persist across reboots.

#### Acceptance Criteria

1. WHEN the coordinator requests removal across all kernels THE SYSTEM SHALL invoke
   `grubby --update-kernel=ALL --remove-args="<args>"` with elevated privileges.
2. WHEN the coordinator requests removal on a specific path THE SYSTEM SHALL invoke
   `grubby --update-kernel=<path> --remove-args="<args>"` with elevated privileges.
3. WHEN no targeted kernel contains the supplied argument THE SYSTEM SHALL skip
   execution and return a `BootActionResult` with status `SKIPPED`.
4. WHEN the operation succeeds THE SYSTEM SHALL return a `BootActionResult` with
   status `SUCCESS`.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 7: Query Effective Kernel Command Line

**User Story:** As a maintenance coordinator, I want to query the effective kernel
command line for both the running system and each installed kernel, so that I can
compare runtime behavior against persistent bootloader configuration.

#### Acceptance Criteria

1. WHEN the coordinator queries the runtime command line THE SYSTEM SHALL read
   `/proc/cmdline` through the file operator.
2. WHEN the coordinator queries the command line for a specific kernel THE SYSTEM
   SHALL invoke `grubby --info=<path>` and parse the `args` field from the output.
3. THE SYSTEM SHALL return a `KernelCmdLine` record containing the raw string and a
   dict of parsed argument name-value pairs.
4. WHEN the file read or command fails THE SYSTEM SHALL raise a `BootQueryError`.

---

### Requirement 8: Regenerate GRUB2 Configuration

**User Story:** As a maintenance coordinator, I want to regenerate the active GRUB2
configuration file, so that changes made to `/etc/default/grub` and drop-ins are
written to the bootloader.

#### Acceptance Criteria

1. WHEN the coordinator requests a regeneration AND the system uses BIOS boot THE
   SYSTEM SHALL invoke `grub2-mkconfig -o /boot/grub2/grub.cfg` with elevated
   privileges.
2. WHEN the coordinator requests a regeneration AND the system uses UEFI boot THE
   SYSTEM SHALL invoke `grub2-mkconfig -o /boot/efi/EFI/redhat/grub.cfg` with
   elevated privileges.
3. WHEN the coordinator supplies an explicit output path THE SYSTEM SHALL use that
   path regardless of boot mode.
4. WHEN the operation succeeds THE SYSTEM SHALL return a `BootActionResult` with
   status `SUCCESS` and the path of the generated file.
5. WHEN the operation fails THE SYSTEM SHALL return a `BootActionResult` with status
   `FAILED`, exit code, and captured stderr.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 9: Remove a Kernel Entry

**User Story:** As a maintenance coordinator, I want to remove an installed kernel
entry from the bootloader, so that obsolete kernels do not occupy the bootloader menu
or `/boot` space. The actual RPM removal remains the responsibility of the
`package-management` feature.

#### Acceptance Criteria

1. WHEN the coordinator requests removal of a kernel entry by path THE SYSTEM SHALL
   invoke `grubby --remove-kernel=<path>` with elevated privileges.
2. WHEN the requested path is the current default THE SYSTEM SHALL NOT execute the
   removal and SHALL return a `BootActionResult` with status `FAILED` and an error
   explaining that the default kernel must be changed first.
3. WHEN the requested path is the currently running kernel THE SYSTEM SHALL return a
   `BootActionResult` with status `FAILED` and an error explaining that the running
   kernel cannot be removed.
4. WHEN the entry does not exist THE SYSTEM SHALL skip execution and return a
   `BootActionResult` with status `SKIPPED`.
5. WHEN the removal succeeds THE SYSTEM SHALL return a `BootActionResult` with status
   `SUCCESS`.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 10: Manage /etc/default/grub Settings

**User Story:** As a maintenance coordinator, I want to read and set keys in
`/etc/default/grub`, so that persistent GRUB defaults (timeout, distributor, default
entry) can be controlled.

#### Acceptance Criteria

1. WHEN the coordinator requests a key's current value THE SYSTEM SHALL read
   `/etc/default/grub`, parse `KEY=value` lines (preserving quoting), and return the
   value or `None`.
2. WHEN the coordinator requests a set-or-replace THE SYSTEM SHALL update or append
   the key, preserve existing quoting style, and write the file back through the file
   operator with mode `0644`, owner `root`, group `root`, and elevated privileges.
3. WHEN the coordinator requests a delete THE SYSTEM SHALL remove the matching line.
4. WHEN the requested value already matches the current value THE SYSTEM SHALL skip
   the write and return a `BootActionResult` with status `SKIPPED`.
5. WHEN a mutating operation succeeds THE SYSTEM SHALL return a `BootActionResult`
   with status `SUCCESS` and include a note that `grub2-mkconfig` is required for the
   change to take effect.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT write to the file.

---

### Requirement 11: Query Boot Mode (BIOS vs UEFI)

**User Story:** As a maintenance coordinator, I want to query whether the system
booted via BIOS or UEFI, so that I can direct subsequent boot operations at the
correct bootloader configuration.

#### Acceptance Criteria

1. WHEN the coordinator queries the boot mode THE SYSTEM SHALL check for the existence
   of `/sys/firmware/efi` via the file operator.
2. WHEN `/sys/firmware/efi` exists THE SYSTEM SHALL return `BootFirmware.UEFI`.
3. WHEN `/sys/firmware/efi` does not exist THE SYSTEM SHALL return `BootFirmware.BIOS`.
4. WHEN the file-operator probe fails for any reason other than not-found THE SYSTEM
   SHALL raise a `BootQueryError`.

---

### Requirement 12: Elevated Privilege Execution

**User Story:** As a maintenance coordinator, I want all mutating boot management
commands to run with the privileges required by the bootloader tooling, so that
operations succeed on standard systems where the process does not run as root.

#### Acceptance Criteria

1. WHEN the system is configured to use sudo elevation THE SYSTEM SHALL prefix all
   mutating commands (`systemctl set-default`, `grubby --set-default*`,
   `grubby --update-kernel=... --args|--remove-args`, `grubby --remove-kernel`,
   `grub2-mkconfig`, writes under `/etc/default/grub*`) with the command runner's
   elevation mechanism.
2. WHEN the system is running as root THE SYSTEM SHALL NOT prefix commands with
   `sudo`.
3. WHEN read-only query commands (`systemctl get-default`, `grubby --info=*`,
   `/proc/cmdline` reads) are invoked THE SYSTEM SHALL NOT require elevation.
