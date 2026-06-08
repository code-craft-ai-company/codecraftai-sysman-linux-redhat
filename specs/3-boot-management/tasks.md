# Implementation Plan — Boot Management

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `bootmanagement` module
  - `source/packages/ccai/sysman/linux/redhat/bootmanagement.py` exists

- [x] 2. Define `BootActionStatus` enum (SUCCESS / FAILED / SKIPPED /
  DRY_RUN)

- [x] 3. Define `BootManagerConfig` dataclass
  - Fields: `dry_run`, `elevated`

- [x] 4. Define `BootActionResult` dataclass
  - Fields: `status`, `detail`, `exit_code`, `stdout`, `stderr`, `command`

- [x] 5. Define `BootEntry` dataclass
  - Fields: `index`, `title`

## BootManager — Implemented

- [x] 6. Implement `__init__` accepting runner + optional config

- [x] 7. Implement `list_entries()` — parses `grubby --info=ALL` output
  into `BootEntry` records (index + title)

- [x] 8. Implement `set_default(index)` via `grub2-set-default <index>`

- [x] 9. Implement `get_default()` — parses `grub2-editenv list` output
  for `saved_entry=` line; returns `None` on non-int or missing

- [x] 10. Implement `regenerate_config(path="/boot/grub2/grub.cfg")` —
  `grub2-mkconfig -o <path>`

- [x] 11. Implement `add_kernel_param(param)` — `grubby
  --update-kernel=ALL --args=<param>`

- [x] 12. Implement `remove_kernel_param(param)` — `grubby
  --update-kernel=ALL --remove-args=<param>`

- [x] 13. Implement `_dispatch` helper — dry-run short-circuit and
  runner-result-to-BootActionResult mapping

## BootManager — Gaps (specified but not implemented)

- [ ] 14. Query + set default systemd target
  (`systemctl get-default` / `systemctl set-default`)

- [ ] 15. `bootctl`-based queries for systemd-boot systems (EFI path)

- [ ] 16. `/etc/default/grub` and `/etc/default/grub.d/*.cfg` read/write
  helpers (currently only `grubby` and `grub2-mkconfig` are wrapped)

- [ ] 17. Support writing to the EFI grub.cfg path
  (`/boot/efi/EFI/redhat/grub.cfg`) when detected

- [ ] 18. Idempotency: return `SKIPPED` from `set_default` when the
  requested index is already the saved entry; return `SKIPPED` from
  `add_kernel_param` when param is already present in the kernel
  command line

- [ ] 19. Structured kernel-param query — list the current
  command-line args per entry

## Tests — Implemented

- [x] 20. `test_set_default_dispatches_grub2_set_default`
- [x] 21. `test_get_default_parses_saved_entry`
- [x] 22. `test_add_kernel_param_dispatches_grubby`
- [x] 23. `test_regenerate_config_runs_mkconfig`

## Tests — Gaps

- [ ] 24. Test `list_entries` parser — multi-entry grubby output,
  quoted titles, blank/malformed lines

- [ ] 25. Test `remove_kernel_param` dispatch shape

- [ ] 26. Test dry-run short-circuits for every mutating method

- [ ] 27. Test `get_default` — no `saved_entry`, non-int value,
  non-zero exit

- [ ] 28. Test failure mapping — runner non-zero exit → status FAILED
  with exit_code/stderr preserved

## Follow-ups

- [ ] 29. **codecraftai-* reuse check**: look for boot/bootloader helpers
  in `codecraftai-sysman-uefi` or `codecraftai-interop` before expanding this
  module (the `uefi` package is a particularly likely source).
