# Requirements

## Introduction

The encryption management feature provides the
`codecraftai-sysman-linux-redhat` package with the ability to manage
block-level encryption on Red Hat Linux systems using LUKS (via
`cryptsetup`), including TPM2-bound and Clevis-bound auto-unlock
configurations, and the `/etc/crypttab` boot-time unlock table.

A maintenance coordinator uses this feature to bring a system's
disk-encryption posture into a declared desired state.  All commands
(`cryptsetup`, `systemd-cryptenroll`, `clevis`, `clevis-luks-*`)
are executed through the `codecraftai-interfaces` command-runner
abstraction.  Reads and writes against `/etc/crypttab` route through
the runner (`cat`/`tee`).  All mutating operations respect dry-run
mode.

This feature complements — and does not duplicate — the block-device
surface owned by `3-storage-management` (partitioning, filesystem
creation, LVM, RAID) and the mount surface owned by
`3-filesystem-management` (`/etc/fstab`, `mount`/`umount`).  The
typical stack is: `storage-management` partitions →
`encryption-management` LUKS-formats and opens → `storage-management`
creates PV/VG/LV on top → `filesystem-management` mounts.

Passphrase material SHALL route through `codecraftai-credentials` (or
an equivalent credential object) and be delivered to underlying
commands via runner `stdin`, never on argv.

---

## Requirements

### Requirement 1: Query LUKS Header State

**User Story:** As a maintenance coordinator, I want to query the
LUKS header of a block device, so that I can make conditional
decisions (format vs. skip, rotate keys vs. leave alone) without
mutating state.

#### Acceptance Criteria

1. WHEN the coordinator queries a device THE SYSTEM SHALL invoke
   `cryptsetup luksDump <device>` and return a `LuksHeader` record
   with `device`, `version` (`"1"` or `"2"`), `uuid`, `cipher`,
   `hash`, `key_slots: list[KeySlotInfo]`, `tokens: list[dict]`,
   `raw`.
2. WHEN the device is not LUKS-formatted THE SYSTEM SHALL return
   `None` rather than raising.
3. WHEN the `cryptsetup` tool is absent THE SYSTEM SHALL raise
   `EncryptionToolNotAvailableError` with an install hint.
4. THE SYSTEM SHALL NOT dispatch any mutating command in this path.
   Dry-run mode MAY run this command freely because it is
   read-only.

---

### Requirement 2: Format a Device as LUKS

**User Story:** As a maintenance coordinator, I want to LUKS-format
a block device with a specified cipher, key size, and initial
passphrase, so that the device can subsequently be opened as an
encrypted volume.

#### Acceptance Criteria

1. WHEN the coordinator requests a format THE SYSTEM SHALL invoke
   `cryptsetup luksFormat --type luks2 --cipher <cipher>
   --key-size <bits> --hash <hash> --pbkdf <pbkdf> [<extra-args>]
   <device>` with the passphrase supplied via runner `stdin`.
2. THE SYSTEM SHALL reject a format request when `query_header()`
   already returns a non-None `LuksHeader` unless the caller
   explicitly sets `force=True`.  Without `force`, return
   `EncryptionActionResult(status=SKIPPED)`.
3. WHEN `force=True` THE SYSTEM SHALL pass `--batch-mode` to
   `cryptsetup` to suppress interactive confirmations; the caller
   accepts the data-loss risk.
4. WHEN dry-run mode is active THE SYSTEM SHALL NOT dispatch
   `luksFormat` and SHALL return `DRY_RUN` with the command that
   would have been issued.
5. THE SYSTEM SHALL NEVER accept passphrase material on argv.

---

### Requirement 3: Open and Close LUKS Mappings

**User Story:** As a maintenance coordinator, I want to open
(unlock) and close (lock) a LUKS device at runtime, so that the
encrypted volume is mapped into `/dev/mapper/<name>` or
unmapped on demand.

#### Acceptance Criteria

1. WHEN the coordinator requests an open THE SYSTEM SHALL invoke
   `cryptsetup open <device> <name>` with the passphrase supplied
   via `stdin`.
2. WHEN the mapping at `/dev/mapper/<name>` already exists THE
   SYSTEM SHALL return `SKIPPED`.
3. WHEN the coordinator requests a close THE SYSTEM SHALL invoke
   `cryptsetup close <name>`; SKIPPED when the mapping does not
   exist.
4. WHEN the coordinator requests a `--key-file <file>` open THE
   SYSTEM SHALL invoke `cryptsetup open --key-file <file> <device>
   <name>` and NOT open a stdin pipe.
5. WHEN opening via TPM2 / Clevis THE SYSTEM SHALL invoke
   `clevis luks unlock -d <device> -n <name>` (no passphrase).

---

### Requirement 4: Key-Slot Lifecycle

**User Story:** As a maintenance coordinator, I want to add, remove,
and rotate passphrases in LUKS key slots, so that I can manage
administrative access over time without rekeying the underlying
data.

#### Acceptance Criteria

1. WHEN the coordinator adds a passphrase to a slot THE SYSTEM
   SHALL invoke `cryptsetup luksAddKey <device>` with an existing
   valid passphrase via stdin and the new passphrase via a
   secondary prompt (implementation: two stdin payloads joined with
   `\n`, or `--key-file /dev/stdin` plus a temp file — the runner
   abstraction MUST NOT place either passphrase on argv).
2. WHEN the coordinator removes a key slot by index THE SYSTEM
   SHALL invoke `cryptsetup luksKillSlot <device> <slot>` with an
   existing valid passphrase via stdin.
3. WHEN the coordinator removes a key slot by passphrase THE SYSTEM
   SHALL invoke `cryptsetup luksRemoveKey <device>` with the target
   passphrase on stdin.
4. WHEN the coordinator rotates a passphrase (replace old with new)
   THE SYSTEM SHALL invoke `cryptsetup luksChangeKey <device>` with
   the old and new passphrases on stdin.
5. THE SYSTEM SHALL reject `luksKillSlot` when it would leave the
   device with zero active key slots, returning `FAILED` with a
   descriptive message before dispatching.

---

### Requirement 5: TPM2 and Clevis Enrolment

**User Story:** As a maintenance coordinator, I want to enrol a
TPM2 binding for a LUKS device so that the system can auto-unlock
on trusted hardware without manual passphrase entry.

#### Acceptance Criteria

1. WHEN the coordinator enrols a TPM2 binding THE SYSTEM SHALL
   invoke `systemd-cryptenroll --tpm2-device=auto <device>
   --tpm2-pcrs=<pcrs>` with an existing passphrase via stdin.
2. WHEN the coordinator removes a TPM2 binding THE SYSTEM SHALL
   invoke `systemd-cryptenroll --wipe-slot=<slot> <device>`.
3. WHEN the coordinator enrols a Clevis binding THE SYSTEM SHALL
   invoke `clevis luks bind -d <device> <pin> <config-json>`
   with any passphrase input via stdin.
4. WHEN the coordinator removes a Clevis binding THE SYSTEM SHALL
   invoke `clevis luks unbind -d <device> -s <slot>`.
5. WHEN the coordinator queries the current bindings THE SYSTEM
   SHALL return `list[CryptBinding]` records parsed from
   `systemd-cryptenroll <device>` and `clevis luks list -d
   <device>`.

---

### Requirement 6: `/etc/crypttab` Management

**User Story:** As a maintenance coordinator, I want to add, remove,
and list entries in `/etc/crypttab`, so that LUKS devices unlock
automatically at boot.

#### Acceptance Criteria

1. WHEN the coordinator lists crypttab entries THE SYSTEM SHALL
   parse `/etc/crypttab` into `list[CrypttabEntry]` records with
   `name`, `device`, `keyfile`, `options: list[str]`.
2. WHEN the coordinator adds an entry THE SYSTEM SHALL read the
   current file, append the new line if absent, and rewrite via
   `tee`; SKIPPED when an identical entry already exists.
3. WHEN the coordinator removes an entry by `name` THE SYSTEM SHALL
   strip the matching line and rewrite; SKIPPED when absent.
4. WHEN the coordinator updates an existing entry (same `name`,
   different `device`/`keyfile`/`options`) THE SYSTEM SHALL
   replace the matching line in place.
5. WHEN the entry references a keyfile path THE SYSTEM SHALL NOT
   itself create, mutate, or read that keyfile — keyfile content
   management is the caller's responsibility.

---

### Requirement 7: Resize a LUKS Mapping

**User Story:** As a maintenance coordinator, I want to resize a
mapped LUKS device so that filesystem expansion after an LVM or
partition growth is complete.

#### Acceptance Criteria

1. WHEN the coordinator requests a resize THE SYSTEM SHALL invoke
   `cryptsetup resize <name>` (no size argument grows to the
   underlying device).
2. WHEN the mapping does not exist THE SYSTEM SHALL return
   `FAILED` with a descriptive message.
3. WHEN the coordinator supplies an explicit size THE SYSTEM SHALL
   invoke `cryptsetup resize <name> --size <sectors>`.

---

### Requirement 8: Dry-Run, Elevation, Credential Handling, Error
Reporting

**User Story:** As a maintenance coordinator, I want every mutating
operation to respect dry-run, run elevated, handle passphrase
material securely, and surface failures structurally.

#### Acceptance Criteria

1. WHEN `config.dry_run=True` THE SYSTEM SHALL NOT dispatch any
   mutating `cryptsetup` / `systemd-cryptenroll` / `clevis`
   command, NOR rewrite `/etc/crypttab`, and SHALL return
   `EncryptionActionResult(status=DRY_RUN, command=<would-dispatch>)`.
2. WHEN `config.elevated=True` THE SYSTEM SHALL dispatch mutating
   commands with `elevated=True` to the runner.
3. THE SYSTEM SHALL NEVER place passphrases on argv.  Every
   passphrase-accepting code path routes passphrase bytes through
   `stdin`.
4. WHEN the runner returns a non-zero exit code THE SYSTEM SHALL
   return `EncryptionActionResult(status=FAILED, exit_code=<code>,
   stderr=<captured>)`.
5. WHEN a required credential is absent THE SYSTEM SHALL raise
   `EncryptionCredentialError` (a `SemanticError` subtype); never
   shell out with an empty passphrase.
