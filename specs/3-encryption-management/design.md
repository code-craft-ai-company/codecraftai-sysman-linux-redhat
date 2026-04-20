# Design

## Overview

The encryption management feature is implemented as a single module,
`automojo.sysman.linux.redhat.encryptionmanagement`, exposing an
`EncryptionManager` class that wraps `cryptsetup`,
`systemd-cryptenroll`, and `clevis` through the `ICommandRunner`
abstraction.  It also owns read/write of `/etc/crypttab`.

All file reads route through the runner via `cat`; all file writes
route through the runner via `tee` with `stdin`.  Passphrase bytes
flow through runner `stdin`, never argv.

**See Also:**
- `3-storage-management` for partitioning, LVM, and RAID — usually
  invoked before and after this feature in a maintenance run.
- `3-filesystem-management` for mounting the decrypted volume.
- `3-boot-management` for `crashkernel=` and GRUB changes when the
  root filesystem is LUKS-encrypted (out of scope here).
- `automojo-credentials` for passphrase handling.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│            Maintenance Coordinator                          │
│  - Declares encryption posture                              │
│  - Supplies credential object for passphrase material       │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            EncryptionManager                                │
│                                                             │
│  LUKS query / format:                                       │
│    query_header(), format_device()                          │
│  LUKS open / close / resize:                                │
│    open(), close(), resize()                                │
│  Key-slot lifecycle:                                        │
│    add_key(), remove_key_by_slot(),                         │
│    remove_key_by_passphrase(), rotate_key()                 │
│  TPM2 / Clevis:                                             │
│    enroll_tpm2(), wipe_tpm2_slot(),                         │
│    enroll_clevis(), unbind_clevis(),                        │
│    list_bindings()                                          │
│  /etc/crypttab:                                             │
│    list_crypttab(), add_crypttab_entry(),                   │
│    remove_crypttab_entry(), update_crypttab_entry()         │
└────────────────────────┬────────────────────────────────────┘
                         │ delegates to
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                   ICommandRunner                            │
│  run(args, elevated, stdin, ...) → RunResult                │
└─────────────────────────────────────────────────────────────┘
```

---

## Components

### `EncryptionManager`

**Location:** `source/packages/automojo/sysman/linux/redhat/encryptionmanagement.py`

| Method | Description |
|---|---|
| `query_header(device)` | `cryptsetup luksDump <device>` → `LuksHeader \| None` |
| `format_device(device, spec, credential, force=False)` | `cryptsetup luksFormat` with passphrase on stdin; SKIPPED when header already present and `force=False` |
| `open(device, name, credential=None, key_file=None)` | `cryptsetup open ...` (passphrase on stdin or `--key-file`); SKIPPED when `/dev/mapper/<name>` exists |
| `close(name)` | `cryptsetup close <name>`; SKIPPED when mapping absent |
| `resize(name, sectors=None)` | `cryptsetup resize <name> [--size <sectors>]` |
| `add_key(device, existing, new)` | `luksAddKey` with two stdin payloads |
| `remove_key_by_slot(device, slot, credential)` | `luksKillSlot`; rejects when slot count would reach zero |
| `remove_key_by_passphrase(device, credential)` | `luksRemoveKey` |
| `rotate_key(device, old, new)` | `luksChangeKey` |
| `enroll_tpm2(device, credential, pcrs="0,7")` | `systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=<pcrs> <device>` with passphrase on stdin |
| `wipe_tpm2_slot(device, slot)` | `systemd-cryptenroll --wipe-slot=<slot>` |
| `enroll_clevis(device, pin, config, credential)` | `clevis luks bind -d <device> <pin> <json>` |
| `unbind_clevis(device, slot)` | `clevis luks unbind -d <device> -s <slot>` |
| `list_bindings(device)` | Merge `systemd-cryptenroll <device>` and `clevis luks list` into `list[CryptBinding]` |
| `list_crypttab()` | Parse `/etc/crypttab` |
| `add_crypttab_entry(entry)` / `remove_crypttab_entry(name)` / `update_crypttab_entry(entry)` | Idempotent `/etc/crypttab` edits |

---

## Configuration

```python
@dataclass
class EncryptionManagerConfig:
    dry_run: bool = False
    elevated: bool = True
    crypttab_path: str = "/etc/crypttab"
    default_cipher: str = "aes-xts-plain64"
    default_key_size: int = 512
    default_hash: str = "sha256"
    default_pbkdf: str = "argon2id"
```

---

## Data Models

```python
class EncryptionActionStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class EncryptionActionResult:
    status: EncryptionActionStatus
    detail: str | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    command: list[str] | None = None        # sanitised — no passphrase


@dataclass
class KeySlotInfo:
    slot: int
    state: str                               # 'ENABLED' | 'DISABLED'
    key_size_bits: int | None
    token: int | None                        # LUKS2 token id if present


@dataclass
class LuksHeader:
    device: str
    version: str                             # '1' | '2'
    uuid: str | None
    cipher: str
    hash: str
    key_slots: list[KeySlotInfo]
    tokens: list[dict]                       # LUKS2 tokens parsed as dicts
    raw: str


@dataclass
class CryptFormatSpec:
    cipher: str | None = None                # fall back to config default
    key_size: int | None = None
    hash: str | None = None
    pbkdf: str | None = None
    sector_size: int | None = None
    luks_version: str = "luks2"
    extra_args: list[str] | None = None


@dataclass
class CryptBinding:
    slot: int
    backend: str                             # 'tpm2' | 'clevis' | 'passphrase'
    pin: str | None                          # for clevis: 'tpm2', 'tang', ...
    config: dict | None                      # for clevis: parsed JSON config
    raw: str


@dataclass
class CrypttabEntry:
    name: str
    device: str
    keyfile: str | None                      # None when no keyfile
    options: list[str]                       # ['luks', 'tpm2-device=auto', ...]
```

---

## Key Method Designs

### `format_device(device, spec, credential, force=False)`

1. Call `query_header(device)`.
2. If result is not `None` AND `force=False` →
   `EncryptionActionResult(status=SKIPPED, detail="<device> is
   already LUKS")`.
3. If `credential is None` → raise `EncryptionCredentialError`.
4. If `dry_run=True` → build args, return `DRY_RUN`.
5. Build args:
   ```
   [
     "cryptsetup", "luksFormat",
     "--type", spec.luks_version,
     "--cipher", spec.cipher or config.default_cipher,
     "--key-size", str(spec.key_size or config.default_key_size),
     "--hash", spec.hash or config.default_hash,
     "--pbkdf", spec.pbkdf or config.default_pbkdf,
     *(["--sector-size", str(spec.sector_size)] if spec.sector_size else []),
     *(spec.extra_args or []),
     *(["--batch-mode"] if force else []),
     device,
   ]
   ```
6. Dispatch with `stdin=credential.secret_bytes()` (or the
   credential's pass-through), `elevated=config.elevated`.
7. Map `RunResult` → `EncryptionActionResult`.  The returned
   `command` field is sanitised — passphrase bytes never appear in
   it.

### `remove_key_by_slot(device, slot, credential)`

1. Call `query_header(device)`.
2. Count enabled slots in `header.key_slots` where
   `state == "ENABLED"`.
3. If the removal would leave 0 enabled slots → return
   `FAILED(detail="refusing to remove last key slot")` WITHOUT
   dispatching.  This is a safety rail.
4. Otherwise: `cryptsetup luksKillSlot <device> <slot>` with
   `stdin=credential.secret_bytes()`.

### `open(device, name, credential=None, key_file=None)`

1. Probe `/dev/mapper/<name>` presence via `test -b
   /dev/mapper/<name>` (runner call).  If mapping exists → SKIPPED.
2. Build args:
   - `credential` → `["cryptsetup", "open", device, name]` with
     `stdin=credential.secret_bytes()`.
   - `key_file` → `["cryptsetup", "open", "--key-file", key_file,
     device, name]` with no stdin.
   - Neither → raise `EncryptionCredentialError`.
3. Dispatch; map.

### `list_crypttab()`

1. Read `/etc/crypttab` via `cat`.
2. Skip empty / comment lines.
3. Split each line into `<name> <device> <keyfile> <options>`
   (with `-` representing "no keyfile").
4. Return `list[CrypttabEntry]` preserving input order.

---

## Dry-Run Behaviour

Dry-run disables: `format_device`, `open`, `close`, `resize`,
`add_key`, `remove_key_by_*`, `rotate_key`, `enroll_tpm2`,
`wipe_tpm2_slot`, `enroll_clevis`, `unbind_clevis`,
`add_crypttab_entry`, `remove_crypttab_entry`,
`update_crypttab_entry`.

Dry-run allows: `query_header`, `list_bindings`, `list_crypttab`
(read-only).

---

## Safety and Correctness Properties

1. **No passphrase on argv.** Every passphrase-accepting code path
   supplies the passphrase via `stdin`.  `EncryptionActionResult.command`
   field preserves this invariant.
2. **Reject last-slot removal.** `remove_key_by_slot` refuses to
   remove the last enabled key slot without an alternative binding
   (TPM2 / Clevis / keyfile).
3. **Idempotent format.** `format_device` refuses to run against an
   already-formatted device unless `force=True`; prevents
   accidental data loss.
4. **Idempotent open / close.** `open` SKIPPED when mapping exists;
   `close` SKIPPED when mapping absent.
5. **Idempotent crypttab edits.** `add_crypttab_entry` SKIPPED when
   identical entry present; `remove_crypttab_entry` SKIPPED when
   entry absent.
6. **Keyfile content untouched.** This feature never creates,
   reads, or writes the referenced `<keyfile>` — it only edits the
   crypttab line that points at it.

---

## Testing Strategy

- `_MockRunner` following the existing pattern.
- `_MockCredential` with a configurable `secret_bytes()` method
  and a flag to assert it was called exactly once per dispatch
  path.
- Unit tests per public method covering happy / SKIPPED / FAILED /
  DRY_RUN branches.
- Tests that verify passphrase bytes appear in `stdin` and NEVER
  in the recorded argv.
- Tests for the last-slot safety rail and for the
  `format_device` idempotency check.
- Parser tests for `luksDump`, `systemd-cryptenroll`, `clevis luks
  list`, and `/etc/crypttab`.
- No live block device required.

---

## Module Layout

```
source/packages/automojo/sysman/linux/redhat/
└── encryptionmanagement.py

source/testroots/testbase/
└── automojo/tests/sysman/linux/redhat/
    └── test_encryptionmanagement.py
```

---

## Seams With Other Features

| Seam | Owner | This Feature |
|---|---|---|
| Partitioning / LVM / RAID | `3-storage-management` | Coordinator invokes storage first; this feature LUKS-formats the partition; coordinator invokes storage again for PV/VG/LV on the mapped device |
| `/etc/fstab` mounts | `3-filesystem-management` | Coordinator mounts `/dev/mapper/<name>` after this feature opens it |
| Credential / passphrase handling | `automojo-credentials` | This feature accepts a credential object; passphrase bytes flow via `stdin` |
| Boot-time unlock via TPM2 / Clevis | This feature | Writes to `/etc/crypttab` with `tpm2-device=` or `clevis` options; no changes to kernel/initramfs (handled by `3-boot-management` when required) |
