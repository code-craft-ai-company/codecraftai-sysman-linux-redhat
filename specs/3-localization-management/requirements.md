# Requirements

## Introduction

The localization management feature provides the `automojo-sysman-linux-redhat`
package with the ability to manage locale, timezone, keyboard, and real-time-clock
settings on Red Hat Linux systems using `localectl` and `timedatectl`. Network time
synchronization (Chrony / NTP servers) remains the responsibility of the
`network-configuration` feature.

A maintenance coordinator uses this feature to apply a declared localization posture
to the target system. All system commands (`timedatectl`, `localectl`) are executed
through the `automojo-interfaces` command-runner abstraction. Any file reads or
writes under `/etc/locale.conf`, `/etc/vconsole.conf`, `/etc/X11/xorg.conf.d/`, or
`/etc/localtime` use the `automojo-interfaces` file-operator abstraction. All
mutating operations respect dry-run mode.

---

## Requirements

### Requirement 1: Query Current Timezone

**User Story:** As a maintenance coordinator, I want to query the current system
timezone and related clock state, so that I can compare it against the declared
desired state.

#### Acceptance Criteria

1. WHEN the coordinator requests the timezone state THE SYSTEM SHALL invoke
   `timedatectl show` and parse the key-value output.
2. THE SYSTEM SHALL return a `TimeState` record containing `timezone`,
   `local_rtc` (bool), `can_ntp` (bool), `ntp` (bool), `ntp_synchronized` (bool),
   `time_usec`, and `rtc_time_usec`.
3. WHEN the command fails THE SYSTEM SHALL raise a `LocalizationQueryError`.

---

### Requirement 2: Set Timezone

**User Story:** As a maintenance coordinator, I want to set the system timezone, so
that local time is displayed correctly and scheduled tasks run at the intended
wall-clock time.

#### Acceptance Criteria

1. WHEN the coordinator requests a timezone THE SYSTEM SHALL invoke
   `timedatectl set-timezone <zone>` with elevated privileges.
2. WHEN the current timezone already equals the requested value THE SYSTEM SHALL
   skip execution and return a `LocalizationActionResult` with status `SKIPPED`.
3. WHEN the supplied timezone string is not present under `/usr/share/zoneinfo/` THE
   SYSTEM SHALL NOT execute the command and SHALL return a
   `LocalizationActionResult` with status `FAILED` and a clear error.
4. WHEN the operation succeeds THE SYSTEM SHALL return a `LocalizationActionResult`
   with status `SUCCESS`.
5. WHEN the operation fails THE SYSTEM SHALL return a `LocalizationActionResult`
   with status `FAILED`, exit code, and captured stderr.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 3: List Available Timezones

**User Story:** As a maintenance coordinator, I want to list all valid timezone
strings, so that I can validate configuration input against the system's view of
available zones.

#### Acceptance Criteria

1. WHEN the coordinator requests the list THE SYSTEM SHALL invoke
   `timedatectl list-timezones`.
2. THE SYSTEM SHALL return a list of zone strings.
3. WHEN the command fails THE SYSTEM SHALL raise a `LocalizationQueryError`.

---

### Requirement 4: Set RTC Clock Mode (Local vs UTC)

**User Story:** As a maintenance coordinator, I want to set whether the real-time
clock is interpreted as local time or UTC, so that multi-boot systems or legacy
hardware behave correctly.

#### Acceptance Criteria

1. WHEN the coordinator requests `local_rtc=False` THE SYSTEM SHALL invoke
   `timedatectl set-local-rtc 0` with elevated privileges.
2. WHEN the coordinator requests `local_rtc=True` THE SYSTEM SHALL invoke
   `timedatectl set-local-rtc 1` with elevated privileges.
3. WHEN the current mode already matches THE SYSTEM SHALL skip execution and return
   a `LocalizationActionResult` with status `SKIPPED`.
4. WHEN the coordinator requests that the RTC not be adjusted at the time of the
   mode change THE SYSTEM SHALL pass `--adjust-system-clock=false`.
5. WHEN the operation succeeds THE SYSTEM SHALL return a `LocalizationActionResult`
   with status `SUCCESS`.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 5: Query Current Locale

**User Story:** As a maintenance coordinator, I want to query the current system
locale variables, so that I can compare them against the declared desired state.

#### Acceptance Criteria

1. WHEN the coordinator requests the locale state THE SYSTEM SHALL invoke
   `localectl status`.
2. THE SYSTEM SHALL return a `LocaleState` record containing a dict of locale
   variables (`LANG`, `LC_CTYPE`, `LC_MESSAGES`, etc.) and the VC and X11 keymap
   strings reported by `localectl`.
3. WHEN the command fails THE SYSTEM SHALL raise a `LocalizationQueryError`.

---

### Requirement 6: Set System Locale

**User Story:** As a maintenance coordinator, I want to set `LANG` or other locale
variables, so that user-facing tools run in the expected language and character
encoding.

#### Acceptance Criteria

1. WHEN the coordinator supplies one or more locale variables THE SYSTEM SHALL
   invoke `localectl set-locale LANG=<value> [LC_*=<value> ...]` with elevated
   privileges.
2. WHEN the supplied `LANG` value is not present in `localectl list-locales` THE
   SYSTEM SHALL NOT execute the command and SHALL return a
   `LocalizationActionResult` with status `FAILED` and a clear error.
3. WHEN every supplied variable already matches the current locale state THE SYSTEM
   SHALL skip execution and return a `LocalizationActionResult` with status
   `SKIPPED`.
4. WHEN the operation succeeds THE SYSTEM SHALL return a `LocalizationActionResult`
   with status `SUCCESS`.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 7: List Available Locales

**User Story:** As a maintenance coordinator, I want to list installed locales, so
that I can validate locale input against the system's installed glibc data.

#### Acceptance Criteria

1. WHEN the coordinator requests the list THE SYSTEM SHALL invoke
   `localectl list-locales`.
2. THE SYSTEM SHALL return a list of locale strings.
3. WHEN the command fails THE SYSTEM SHALL raise a `LocalizationQueryError`.

---

### Requirement 8: Set Virtual Console Keymap

**User Story:** As a maintenance coordinator, I want to set the keyboard layout for
the virtual console, so that console logins work with the expected layout.

#### Acceptance Criteria

1. WHEN the coordinator supplies a VC keymap name THE SYSTEM SHALL invoke
   `localectl set-keymap <keymap>` with elevated privileges.
2. WHEN the coordinator also requests that the X11 layout be converted THE SYSTEM
   SHALL pass the `--convert` flag.
3. WHEN the supplied keymap is not present in `localectl list-keymaps` THE SYSTEM
   SHALL NOT execute the command and SHALL return a `LocalizationActionResult` with
   status `FAILED`.
4. WHEN the current VC keymap already matches THE SYSTEM SHALL skip execution and
   return a `LocalizationActionResult` with status `SKIPPED`.
5. WHEN the operation succeeds THE SYSTEM SHALL return a `LocalizationActionResult`
   with status `SUCCESS`.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 9: Set X11 Keymap

**User Story:** As a maintenance coordinator, I want to set the X11 keyboard layout,
model, variant, and options, so that graphical login sessions use the desired
layout.

#### Acceptance Criteria

1. WHEN the coordinator supplies layout, model, variant, and options THE SYSTEM
   SHALL invoke `localectl set-x11-keymap <layout> [<model> [<variant> [<options>]]]`
   with elevated privileges.
2. WHEN the coordinator also requests that the VC keymap be converted THE SYSTEM
   SHALL pass the `--convert` flag.
3. WHEN the supplied layout is not present in `localectl list-x11-keymap-layouts`
   THE SYSTEM SHALL NOT execute the command and SHALL return a
   `LocalizationActionResult` with status `FAILED`.
4. WHEN every supplied component already matches the current X11 keymap state THE
   SYSTEM SHALL skip execution and return a `LocalizationActionResult` with status
   `SKIPPED`.
5. WHEN the operation succeeds THE SYSTEM SHALL return a `LocalizationActionResult`
   with status `SUCCESS`.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 10: List X11 Keymap Metadata

**User Story:** As a maintenance coordinator, I want to list X11 keymap models,
layouts, variants, and options, so that I can validate X11 input.

#### Acceptance Criteria

1. WHEN the coordinator requests layouts THE SYSTEM SHALL invoke
   `localectl list-x11-keymap-layouts`.
2. WHEN the coordinator requests models THE SYSTEM SHALL invoke
   `localectl list-x11-keymap-models`.
3. WHEN the coordinator requests variants for a layout THE SYSTEM SHALL invoke
   `localectl list-x11-keymap-variants <layout>`.
4. WHEN the coordinator requests options THE SYSTEM SHALL invoke
   `localectl list-x11-keymap-options`.
5. THE SYSTEM SHALL return the captured list for each.
6. WHEN any command fails THE SYSTEM SHALL raise a `LocalizationQueryError`.

---

### Requirement 11: Elevated Privilege Execution

**User Story:** As a maintenance coordinator, I want all mutating localization
commands to run with the privileges required by the tools, so that operations
succeed on standard systems where the process does not run as root.

#### Acceptance Criteria

1. WHEN the system is configured to use sudo elevation THE SYSTEM SHALL prefix all
   mutating commands (`timedatectl set-*`, `localectl set-*`) with the command
   runner's elevation mechanism.
2. WHEN the system is running as root THE SYSTEM SHALL NOT prefix commands with
   `sudo`.
3. WHEN read-only query commands (`timedatectl show`, `localectl status`,
   `timedatectl list-timezones`, `localectl list-*`) are invoked THE SYSTEM SHALL
   NOT require elevation.
