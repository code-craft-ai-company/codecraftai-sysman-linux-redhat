# Implementation Plan — Hardware Inventory

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

This feature is read-only — no dry-run distinction, no mutating ops.

## Module and Models

- [x] 1. Create the `hardwareinventory` module

- [x] 2. Define `HardwareInventoryConfig` (elevated flag only — no
  dry_run since read-only)

- [x] 3. Define query-result dataclasses: `DmiIdentity`, `CpuInfo`,
  `MemoryInfo`, `BlockDeviceInventory`, `PciDevice`, `UsbDevice`,
  `NetInterfaceHardware`, `OsRelease`, `FirmwareInfo`,
  `HardwareSnapshot`

## HardwareInventoryManager — Implemented

- [x] 4. Implement `__init__` accepting runner + optional config

- [x] 5. Implement `get_dmi_identity()` — prefer `dmidecode`, fall
  back to `/sys/class/dmi/id/*` sysfs reads

- [x] 6. Implement `get_cpu_info()` via `lscpu --json`

- [x] 7. Implement `get_memory_info()` parsing `/proc/meminfo`

- [x] 8. Implement `list_block_devices()` via `lsblk --json`

- [x] 9. Implement `list_pci_devices()` via `lspci -vmmk`

- [x] 10. Implement `list_usb_devices()` via `lsusb`

- [x] 11. Implement `list_net_hardware()` via `ip -j link` / `ethtool`

- [x] 12. Implement `get_os_release()` — kernel via `uname -r`,
  distro via `/etc/os-release`

- [x] 13. Implement `get_firmware_info()` — detect UEFI via
  `/sys/firmware/efi` presence

- [x] 14. Implement `collect_snapshot()` — aggregate all of the above
  into a `HardwareSnapshot`

- [x] 15. Implement `_read_sysfs_line` helper (`cat` via runner,
  strip whitespace)

## HardwareInventoryManager — Gaps (specified but not implemented)

- [ ] 16. Implement `list_disk_controllers()` — separate storage
  controllers from generic PCI

- [ ] 17. Implement `get_smbios_raw()` — full `dmidecode` dump, not
  just the identity

- [ ] 18. Implement `get_gpu_info()` — recognise and enrich GPU PCI
  entries (nvidia-smi, glxinfo)

- [ ] 19. Implement `list_memory_modules()` via `dmidecode -t memory`
  for per-DIMM detail

- [ ] 20. Implement `list_mounts()` pass-through to / duplicate of
  `3-filesystem-management.list_mounts` — or explicitly delegate

## Tests — Implemented

- [x] 21. `test_get_dmi_identity_prefers_dmidecode`
- [x] 22. `test_get_cpu_info_parses_lscpu_json`
- [x] 23. `test_get_memory_info_parses_meminfo`
- [x] 24. `test_list_block_devices_parses_lsblk_json`
- [x] 25. `test_get_os_release_reports_kernel_and_distro`
- [x] 26. `test_get_firmware_info_detects_uefi`

## Tests — Gaps

- [ ] 27. Tests for `list_pci_devices`, `list_usb_devices`,
  `list_net_hardware`

- [ ] 28. Test `get_dmi_identity` sysfs fallback path (when dmidecode
  is absent / unprivileged)

- [ ] 29. Test `collect_snapshot()` — aggregate contains one record
  per sub-query

- [ ] 30. Test `get_firmware_info` BIOS path (no `/sys/firmware/efi`)

## Follow-ups

- [ ] 31. **automojo-* reuse check**: `automojo-interop` may expose
  generic hardware/PCI enumeration helpers that would save a lot of
  output-parsing logic here.  Also check `automojo-landscaping` and
  `automojo-dataprofiles` for existing snapshot schemas.
