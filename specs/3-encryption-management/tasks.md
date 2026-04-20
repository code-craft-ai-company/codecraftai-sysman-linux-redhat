# Implementation Plan — Encryption Management

All tasks are unchecked — this is a new feature with no existing
implementation.

## Module and Models

- [ ] 1. Create `source/packages/automojo/sysman/linux/redhat/encryptionmanagement.py`
  - _Requirements: All_

- [ ] 2. Define `EncryptionActionStatus` enum (SUCCESS / FAILED /
  SKIPPED / DRY_RUN)
  - _Requirements: 8_

- [ ] 3. Define `EncryptionManagerConfig` dataclass
  - Fields: `dry_run`, `elevated` (default True), `crypttab_path`,
    `default_cipher`, `default_key_size`, `default_hash`,
    `default_pbkdf`

- [ ] 4. Define `EncryptionActionResult` dataclass
  - `command` field MUST be sanitised — passphrase bytes never
    appear in it

- [ ] 5. Define `KeySlotInfo`, `LuksHeader`, `CryptFormatSpec`,
  `CryptBinding`, `CrypttabEntry` dataclasses
  - _Requirements: 1, 2, 5, 6_

- [ ] 6. Define `EncryptionToolNotAvailableError` and
  `EncryptionCredentialError` as `SemanticError` subtypes
  - _Requirements: 1.3, 8.5_

## Constructor and Helpers

- [ ] 7. Implement `__init__` accepting runner + optional config

- [ ] 8. Implement `_read_file(path)` — `cat` via runner, empty on
  non-zero exit

- [ ] 9. Implement `_write_file(path, content)` — `tee` via runner
  with `stdin`; dry-run short-circuit

- [ ] 10. Implement `_mapping_exists(name)` — `test -b
  /dev/mapper/<name>` via runner

- [ ] 11. Implement `_dispatch(command, stdin=None, detail=None)` —
  runner call + dry-run short-circuit + result mapping.  MUST
  return a result whose `command` field never contains the
  passphrase (sanitise when `stdin` is a passphrase payload)

## LUKS Query and Format

- [ ] 12. Implement `query_header(device)` — parse `cryptsetup
  luksDump <device>`; return `None` when not LUKS; raise
  `EncryptionToolNotAvailableError` when cryptsetup is missing
  - _Requirements: 1_

- [ ] 13. Implement `format_device(device, spec, credential,
  force=False)` — SKIPPED when header already present and not
  forced; passphrase on stdin; `--batch-mode` when forced;
  dry-run short-circuit
  - _Requirements: 2, 8.3_

## LUKS Runtime

- [ ] 14. Implement `open(device, name, credential=None,
  key_file=None)` — SKIPPED when `/dev/mapper/<name>` exists;
  passphrase-on-stdin OR `--key-file` branches; raise
  `EncryptionCredentialError` when neither is supplied
  - _Requirements: 3.1, 3.2, 3.4, 3.5, 8.5_

- [ ] 15. Implement `open` Clevis-backed variant — `clevis luks
  unlock -d <device> -n <name>` with no stdin
  - _Requirements: 3.5_

- [ ] 16. Implement `close(name)` — SKIPPED when mapping absent
  - _Requirements: 3.3_

- [ ] 17. Implement `resize(name, sectors=None)` — with optional
  `--size <sectors>`; FAILED when mapping absent
  - _Requirements: 7_

## Key-Slot Lifecycle

- [ ] 18. Implement `add_key(device, existing, new)` — passphrases
  on stdin, never argv
  - _Requirements: 4.1, 8.3_

- [ ] 19. Implement `remove_key_by_slot(device, slot, credential)`
  with the last-slot safety rail: query header, count enabled
  slots, reject removal that would leave zero active slots without
  TPM2 / Clevis binding
  - _Requirements: 4.2, 4.5_

- [ ] 20. Implement `remove_key_by_passphrase(device, credential)`
  - _Requirements: 4.3_

- [ ] 21. Implement `rotate_key(device, old, new)` — old + new on
  stdin
  - _Requirements: 4.4_

## TPM2 / Clevis Enrolment

- [ ] 22. Implement `enroll_tpm2(device, credential, pcrs="0,7")` —
  `systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=<pcrs>
  <device>` with passphrase on stdin
  - _Requirements: 5.1_

- [ ] 23. Implement `wipe_tpm2_slot(device, slot)` —
  `systemd-cryptenroll --wipe-slot=<slot> <device>`
  - _Requirements: 5.2_

- [ ] 24. Implement `enroll_clevis(device, pin, config,
  credential)` — `clevis luks bind -d <device> <pin>
  <config-json>`; passphrase on stdin
  - _Requirements: 5.3_

- [ ] 25. Implement `unbind_clevis(device, slot)` — `clevis luks
  unbind -d <device> -s <slot>`
  - _Requirements: 5.4_

- [ ] 26. Implement `list_bindings(device)` — merge
  `systemd-cryptenroll <device>` and `clevis luks list -d <device>`
  into `list[CryptBinding]`
  - _Requirements: 5.5_

## /etc/crypttab

- [ ] 27. Implement `list_crypttab()` — parse `/etc/crypttab` into
  `list[CrypttabEntry]`
  - _Requirements: 6.1_

- [ ] 28. Implement `add_crypttab_entry(entry)` — SKIPPED when
  identical entry already exists
  - _Requirements: 6.2_

- [ ] 29. Implement `remove_crypttab_entry(name)` — SKIPPED when
  absent
  - _Requirements: 6.3_

- [ ] 30. Implement `update_crypttab_entry(entry)` — in-place
  replace by `name`
  - _Requirements: 6.4_

## Tests

- [ ] 31. Create `test_encryptionmanagement.py` with `_MockRunner`
  and `_MockCredential` helpers

- [ ] 32. Tests for `query_header` — LUKS1 / LUKS2 parsing, None on
  non-LUKS, ToolNotAvailableError

- [ ] 33. Tests for `format_device` — happy path, SKIPPED when
  already LUKS, `force=True` passes `--batch-mode`, DRY_RUN, FAILED
  mapping, `EncryptionCredentialError` when no credential

- [ ] 34. **Argv/stdin split test** — assert passphrase bytes
  arrive on stdin and NEVER appear in the recorded argv across
  every passphrase-accepting method (`format_device`, `open`,
  `add_key`, `remove_key_by_slot`, `remove_key_by_passphrase`,
  `rotate_key`, `enroll_tpm2`, `enroll_clevis`)

- [ ] 35. Tests for `open` — passphrase branch, `--key-file`
  branch, Clevis branch, SKIPPED when mapping exists, FAILED
  mapping, `EncryptionCredentialError`

- [ ] 36. Tests for `close` — SKIPPED when mapping absent

- [ ] 37. Tests for `resize` — with and without explicit sectors,
  FAILED when mapping absent

- [ ] 38. Tests for `remove_key_by_slot` last-slot safety rail —
  setup a single-slot header, attempt removal, assert no runner
  dispatch and a FAILED result

- [ ] 39. Tests for `add_key` / `rotate_key` two-stdin-payload
  semantics

- [ ] 40. Tests for `enroll_tpm2` / `wipe_tpm2_slot`

- [ ] 41. Tests for `enroll_clevis` / `unbind_clevis` /
  `list_bindings` merge

- [ ] 42. Tests for `list_crypttab` parser (comment, blank, no-keyfile
  with `-`, options)

- [ ] 43. Tests for `add_crypttab_entry` / `remove_crypttab_entry`
  / `update_crypttab_entry` idempotency

- [ ] 44. Dry-run sweep across every mutating method

- [ ] 45. Failure-mapping sweep — runner non-zero → FAILED with
  exit_code/stderr preserved

## Follow-ups

- [ ] 46. **automojo-* reuse check**: route every `credential`
  parameter through `automojo.credentials.sshcredential.SshCredential`
  (or an equivalent credential abstraction).  Do NOT accept raw
  passphrase strings on the public API.
