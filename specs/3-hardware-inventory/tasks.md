# Implementation Plan

- [ ] 1. Create the `hardwareinventory` module file
  - Create `source/packages/automojo/sysman/linux/redhat/hardwareinventory.py`
  - Add module-level docstring describing purpose, coordinator-driven design,
    and the read-only (no dry-run) nature of the feature
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11_

- [ ] 2. Define core data models for identity and topology
  - Implement `DmiIdentity` dataclass with fields listed in design §Data Models
  - Implement `CpuInfo` dataclass with `arch`, `byte_order`, `cpus`,
    `threads_per_core`, `cores_per_socket`, `sockets`, `numa_nodes`, `vendor`,
    `model_name`, `model`, `family`, `stepping`, `mhz`, `virtualization`,
    `hypervisor`, `flags`, `l1d_cache`, `l1i_cache`, `l2_cache`, `l3_cache`,
    `source`
  - Implement `MemoryInfo` and `Dimm` dataclasses with fields listed in design
    §Data Models (do not use mutable default values; default `dimms` via
    `field(default_factory=list)`)
  - _Requirements: 1, 2, 3_

- [ ] 3. Define device and platform data models
  - Implement `BlockDeviceInventory` dataclass with `name`, `kname`, `type`,
    `size_bytes`, `rota`, `model`, `serial`, `vendor`, `wwn`, `tran`, `fstype`,
    `uuid`, `label`, `mountpoint`, `children`
  - Implement `PciDevice` dataclass
  - Implement `UsbDevice` dataclass
  - Implement `NetInterfaceHardware` dataclass (link-layer fields only — no IP
    addressing)
  - Implement `OsRelease` dataclass
  - Implement `FirmwareInfo` dataclass with `vendor`, `version`, `release_date`,
    `mode`, `available`
  - _Requirements: 4, 5, 6, 7, 8, 9_

- [ ] 4. Define snapshot and warning data models
  - Implement `PartialInventoryWarning` dataclass with `field`, `reason`,
    `detail`
  - Implement `HardwareSnapshot` dataclass with `collected_at`, `dmi`, `cpu`,
    `memory`, `block_devices`, `pci`, `usb`, `net_hardware`, `os_release`,
    `firmware`, and `warnings` list
  - Use `field(default_factory=list)` for the `warnings` list — never a mutable
    default
  - _Requirements: 10_

- [ ] 5. Define typed exceptions
  - Implement `HardwareInventoryQueryError` with `command`, `exit_code`,
    `stderr`, and optional `parse_detail` attributes
  - Implement `HardwareInventoryPermissionError` with `field`, `command`,
    `stderr` attributes and actionable message guiding the caller to retry
    with elevation
  - _Requirements: 1.4, 3.5, 10.3, 11_

- [ ] 6. Implement `HardwareInventoryConfig` dataclass
  - Add `elevated: bool = False` field
  - Document explicitly in the docstring that this feature is read-only and
    therefore has no `dry_run` field
  - _Requirements: 11_

- [ ] 7. Implement `HardwareInventoryManager.__init__` and `get_os_release()`
  - Accept `ICommandRunner`, `IFileOperator`, and `HardwareInventoryConfig` as
    constructor arguments; assign each to an instance variable in `__init__`
    only (no instance variables created elsewhere)
  - Implement `get_os_release()` combining `uname -srvmo`, `/etc/os-release`
    (parsed via shell-style `KEY=value` parser), and machine identity
    (`hostnamectl --json=short` when available; else `/etc/machine-id` and
    `/etc/hostname` through the file operator)
  - Always runs with `elevated=False`; never fails on missing optional fields
  - Assign the constructed `OsRelease` to a local variable before returning
  - _Requirements: 8_

- [ ] 8. Implement `get_dmi_identity()`
  - Build `dmidecode -t system -t baseboard -t chassis -t bios` command
  - Pass `elevated=self._config.elevated` to the runner (DMI is one of the
    elevation-eligible commands)
  - Parse multi-section `dmidecode` output into `DmiIdentity`; `source="dmidecode"`
  - On permission-denied indication raise `HardwareInventoryPermissionError`
  - On command-not-found fall back to `/sys/class/dmi/id/` via file operator;
    `source="sysfs"`
  - On any other non-zero exit raise `HardwareInventoryQueryError`
  - _Requirements: 1, 11.1_

- [ ] 9. Implement `get_cpu_info()`
  - Build `lscpu -J` command; pass `elevated=False`
  - Parse JSON into `CpuInfo` with `source="lscpu"`; raise
    `HardwareInventoryQueryError` on malformed JSON or non-zero exit
  - When `lscpu` is absent (exit 127 / command-not-found) fall back to
    `/proc/cpuinfo` through the file operator and populate what is available
    with `source="proc_cpuinfo"`
  - Assign constructed `CpuInfo` to a local variable before returning
  - _Requirements: 2_

- [ ] 10. Implement `get_memory_info()`
  - Build `lsmem --output-all -J` command; pass `elevated=False`
  - Parse JSON into the aggregate `MemoryInfo` fields
    (`total_online_bytes`, `total_offline_bytes`, `memory_block_size`); fall
    back to `/proc/meminfo` when `lsmem` is absent
  - Populate `available_bytes`, `free_bytes`, `swap_total_bytes`,
    `swap_free_bytes` from `/proc/meminfo` via file operator in all cases
  - When `dmidecode` is available, build `dmidecode -t memory` with
    `elevated=self._config.elevated` and populate the `dimms` list of `Dimm`
    records; on permission denied raise `HardwareInventoryPermissionError`
  - When `dmidecode` is absent leave `dimms` as an empty list
  - _Requirements: 3, 11.1_

- [ ] 11. Implement `list_block_devices()`
  - Build
    `lsblk -J -b -o NAME,KNAME,TYPE,SIZE,ROTA,MODEL,SERIAL,VENDOR,WWN,TRAN,FSTYPE,UUID,LABEL,MOUNTPOINT`
    command; pass `elevated=False`
  - Parse JSON output into a list of `BlockDeviceInventory` records recursively
    populating `children`
  - Raise `HardwareInventoryQueryError` on command failure or malformed JSON
  - Assign the list to a local variable before returning
  - _Requirements: 4_

- [ ] 12. Implement `list_pci_devices()`
  - Build `lspci -vmm -nnk` command; pass `elevated=False`
  - Parse the multi-record output (blank-line-delimited records with
    `Key: value` lines) into `PciDevice` records, including `vendor_id`,
    `device_id`, bound `driver`, and `modules_available`
  - When `lspci` is not installed return an empty list (do not raise)
  - On any other non-zero exit raise `HardwareInventoryQueryError`
  - _Requirements: 5_

- [ ] 13. Implement `list_usb_devices()`
  - Build `lsusb` command; pass `elevated=False`
  - Parse the plain-text output into `UsbDevice` records with `bus`, `device`,
    `vendor_id`, `product_id`, and `description`
  - When `lsusb` is not installed return an empty list (do not raise)
  - On any other non-zero exit raise `HardwareInventoryQueryError`
  - _Requirements: 6_

- [ ] 14. Implement `list_net_hardware()`
  - Build `ip -j -d link show` command; pass `elevated=False`
  - Parse JSON into a base list of records (name, MAC, link state, MTU)
  - For each interface, read `/sys/class/net/<iface>/speed`,
    `/sys/class/net/<iface>/duplex`, and resolve the `device/driver` symlink
    through the file operator; derive `pci_slot` when discoverable
  - Do NOT populate any IP / route / DNS data — separation from
    `network-configuration` feature
  - On command failure raise `HardwareInventoryQueryError`
  - _Requirements: 7_

- [ ] 15. Implement `get_firmware_info()`
  - Read `/sys/class/dmi/id/bios_vendor`, `.../bios_version`, `.../bios_date`
    through the file operator
  - When any field is unreadable AND `dmidecode` is installed, fall back to
    `dmidecode -s bios-vendor`, `-s bios-version`, `-s bios-release-date` with
    `elevated=self._config.elevated`
  - Detect `mode` via file operator `exists("/sys/firmware/efi")` → `"UEFI"`
    else `"BIOS"`
  - When no firmware data is available, return `FirmwareInfo(..., available=False)`
  - _Requirements: 9, 11.1_

- [ ] 16. Implement `collect_snapshot()` aggregator
  - Capture a UTC ISO-8601 `collected_at` timestamp before starting
  - Invoke each sub-query in the order: DMI, CPU, memory, block devices, PCI,
    USB, net hardware, OS release, firmware
  - Wrap each sub-query in a try/except that catches
    `HardwareInventoryPermissionError` only; on catch, leave the corresponding
    field as `None` and append a `PartialInventoryWarning` with `field`,
    `reason`, `detail`
  - Any other exception (including `HardwareInventoryQueryError`) must
    propagate and abort the snapshot — do NOT swallow `SemanticError` or other
    exceptions
  - Assemble the `HardwareSnapshot` into a local variable before returning
  - _Requirements: 10_

- [ ] 17. Create test module and mock infrastructure
  - Create `source/testroots/testbase/automojo/tests/sysman/linux/redhat/test_hardwareinventory.py`
  - Implement `MockCommandRunner` that returns configurable `RunResult` values
    per command-prefix match (so `lscpu -J` vs. `dmidecode -t memory` can be
    stubbed independently)
  - Implement `MockFileOperator` that serves canned text per path and configurable
    `exists()` responses
  - Load canned stdout captures for `lscpu -J`, `lsmem -J`, `lsblk -J`,
    `lspci -vmm -nnk`, `lsusb`, `ip -j -d link show`, `dmidecode -t ...`,
    `hostnamectl --json=short`
  - Set up hypothesis testing framework configuration (100-iteration minimum)
  - Create test fixtures for common test scenarios
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11_

- [ ] 18. Write property-based test for snapshot aggregation completeness
  - **Property 1: Snapshot Aggregation Completeness**
  - Stub every sub-command with canned successful output
  - Invoke `collect_snapshot()` and verify every sub-query was invoked exactly
    once and every corresponding field is populated
  - **Validates: Requirements 10.1, 10.2**
  - _Requirements: 10_

- [ ] 19. Write property-based test for partial snapshot tolerance
  - **Property 2: Partial Snapshot Tolerance**
  - Generate random subsets of sub-queries that raise
    `HardwareInventoryPermissionError`
  - Verify the corresponding fields are `None`, one
    `PartialInventoryWarning` per failed field is appended, and the remaining
    sub-queries still populate their fields
  - **Validates: Requirements 10.3**
  - _Requirements: 10_

- [ ] 20. Write property-based test for DMI fallback to sysfs
  - **Property 3: DMI Fallback to sysfs**
  - Stub `dmidecode` as command-not-found; stub `/sys/class/dmi/id/*` with
    random but valid field values
  - Verify the returned `DmiIdentity.source == "sysfs"` and the fields match
  - **Validates: Requirements 1.3**
  - _Requirements: 1_

- [ ] 21. Write property-based test for CPU fallback to /proc/cpuinfo
  - **Property 4: CPU Fallback to /proc/cpuinfo**
  - Stub `lscpu` as command-not-found; stub `/proc/cpuinfo` with random but
    valid key/value blocks
  - Verify `CpuInfo.source == "proc_cpuinfo"` and that missing fields are
    `None` without the method raising
  - **Validates: Requirements 2.3**
  - _Requirements: 2_

- [ ] 22. Write property-based test for memory fallback to /proc/meminfo
  - **Property 5: Memory Fallback to /proc/meminfo**
  - Stub `lsmem` as command-not-found and `dmidecode` as command-not-found;
    stub `/proc/meminfo` with random totals
  - Verify aggregate memory fields are populated and `dimms` is an empty list
  - **Validates: Requirements 3.1, 3.4**
  - _Requirements: 3_

- [ ] 23. Write property-based test for empty-list stability
  - **Property 6: Empty List Stability**
  - Stub `lspci` and `lsusb` as command-not-found
  - Verify `list_pci_devices()` and `list_usb_devices()` each return an empty
    `list` and do not raise
  - **Validates: Requirements 5.3, 6.3**
  - _Requirements: 5, 6_

- [ ] 24. Write property-based test for elevation only for DMI
  - **Property 7: Elevation Only for DMI**
  - Generate `HardwareInventoryConfig(elevated=True)` and invoke every
    sub-query
  - Verify `elevated=True` was passed to the runner only when the command was
    `dmidecode` (any argument form); all other commands received
    `elevated=False`
  - **Validates: Requirements 11.1, 11.2, 11.3**
  - _Requirements: 11_

- [ ] 25. Write property-based test for query error status mapping
  - **Property 8: Query Error Status Mapping**
  - Generate random non-zero exit codes and stderr strings for each
    non-fallback sub-query
  - Verify `HardwareInventoryQueryError` is raised carrying command, exit
    code, and stderr; verify permission-denied stderr from `dmidecode`
    produces `HardwareInventoryPermissionError` instead
  - **Validates: Requirements 1.4, 2.4, 3.5, 4.3, 5.4, 6.4, 7.5**
  - _Requirements: 1, 2, 3, 4, 5, 6, 7_

- [ ] 26. Write property-based test for network hardware excludes IP addressing
  - **Property 9: Network Hardware Excludes IP Addressing**
  - Generate random `ip -j -d link show` JSON that optionally includes
    IP-addressing fields
  - Verify every returned `NetInterfaceHardware` record contains only
    link-layer fields and none of the IP / route / DNS fields, even if present
    in the stubbed input
  - **Validates: Requirements 7.3, 7.4**
  - _Requirements: 7_

- [ ] 27. Write property-based test for JSON parse stability
  - **Property 10: JSON Parse Stability**
  - Generate random malformed JSON strings for each JSON-producing command
  - Verify `HardwareInventoryQueryError` is raised (never a raw
    `json.JSONDecodeError`)
  - **Validates: Requirements 2.4, 3.1, 4.3, 7.5, 8.1**
  - _Requirements: 2, 3, 4, 7, 8_

- [ ] 28. Write property-based test for firmware mode detection
  - **Property 11: Firmware Mode Detection**
  - Generate stubs where `/sys/firmware/efi` either exists or does not, and
    random combinations of readable/unreadable firmware field files
  - Verify `FirmwareInfo.mode == "UEFI"` when `/sys/firmware/efi` exists and
    `"BIOS"` otherwise; when no firmware data is readable,
    `available == False`
  - **Validates: Requirements 9.3, 9.4**
  - _Requirements: 9_

- [ ] 29. Write unit tests for `get_dmi_identity()`
  - Test: `dmidecode` success — returns fully populated `DmiIdentity` with
    `source="dmidecode"`
  - Test: `dmidecode` command-not-found — falls back to sysfs; `source="sysfs"`
  - Test: `dmidecode` permission denied — raises
    `HardwareInventoryPermissionError`
  - Test: `dmidecode` other non-zero exit — raises
    `HardwareInventoryQueryError`
  - Test: `elevated=True` config — runner invoked with `elevated=True`
  - _Requirements: 1, 11_

- [ ] 30. Write unit tests for `get_cpu_info()`
  - Test: `lscpu -J` success — returns populated `CpuInfo` with
    `source="lscpu"`
  - Test: `lscpu` command-not-found — falls back to `/proc/cpuinfo`;
    `source="proc_cpuinfo"`
  - Test: malformed JSON from `lscpu` — raises `HardwareInventoryQueryError`
  - Test: runner invoked with `elevated=False` regardless of config
  - _Requirements: 2, 11.3_

- [ ] 31. Write unit tests for `get_memory_info()`
  - Test: `lsmem -J` + `dmidecode -t memory` success — populated aggregate and
    DIMM list
  - Test: `lsmem` absent — falls back to `/proc/meminfo`
  - Test: `dmidecode` absent — `dimms` is empty list, aggregate still
    populated
  - Test: `dmidecode` permission denied — raises
    `HardwareInventoryPermissionError`
  - Test: `/proc/meminfo` fields populate available/free/swap correctly
  - _Requirements: 3, 11_

- [ ] 32. Write unit tests for `list_block_devices()`
  - Test: valid JSON — returns populated list with recursive `children`
  - Test: command failure — raises `HardwareInventoryQueryError`
  - Test: malformed JSON — raises `HardwareInventoryQueryError`
  - Test: runner invoked with `elevated=False`
  - _Requirements: 4, 11.3_

- [ ] 33. Write unit tests for `list_pci_devices()` and `list_usb_devices()`
  - Test: `lspci -vmm -nnk` success — returns populated `PciDevice` list with
    `vendor_id`, `device_id`, `driver`, `modules_available`
  - Test: `lspci` not installed — returns empty list, does not raise
  - Test: `lspci` other non-zero exit — raises `HardwareInventoryQueryError`
  - Test: `lsusb` success — returns populated `UsbDevice` list
  - Test: `lsusb` not installed — returns empty list, does not raise
  - Test: `lsusb` other non-zero exit — raises `HardwareInventoryQueryError`
  - _Requirements: 5, 6_

- [ ] 34. Write unit tests for `list_net_hardware()`
  - Test: `ip -j -d link show` success — returns list with MAC, link state,
    MTU, driver from sysfs, speed/duplex from sysfs, optional `pci_slot`
  - Test: input JSON that contains IP addressing — verify output records still
    contain no IP / routing / DNS fields
  - Test: missing `/sys/class/net/<iface>/speed` — record has `speed_mbps=None`
  - Test: command failure — raises `HardwareInventoryQueryError`
  - Test: runner invoked with `elevated=False`
  - _Requirements: 7, 11.3_

- [ ] 35. Write unit tests for `get_os_release()`
  - Test: `uname`, `/etc/os-release`, `hostnamectl --json=short` all succeed —
    fully populated `OsRelease`
  - Test: `hostnamectl` absent — falls back to `/etc/machine-id` and
    `/etc/hostname`
  - Test: `/etc/os-release` parsed correctly (quoted values, commented lines,
    blank lines)
  - Test: runner invoked with `elevated=False`
  - _Requirements: 8, 11.3_

- [ ] 36. Write unit tests for `get_firmware_info()`
  - Test: all `/sys/class/dmi/id/bios_*` readable — populated `FirmwareInfo`
    with `available=True`
  - Test: sysfs unreadable AND `dmidecode` available — falls back to
    `dmidecode -s bios-*` with elevation
  - Test: `/sys/firmware/efi` exists — `mode="UEFI"`
  - Test: `/sys/firmware/efi` missing — `mode="BIOS"`
  - Test: no firmware data anywhere — `available=False`
  - _Requirements: 9, 11.1_

- [ ] 37. Write unit tests for `collect_snapshot()`
  - Test: all sub-queries succeed — `HardwareSnapshot` fully populated,
    `warnings` is empty list
  - Test: DMI sub-query raises `HardwareInventoryPermissionError` — `dmi`
    field is `None`, one `PartialInventoryWarning` entry with `field="dmi"`,
    remaining fields populated
  - Test: multiple sub-queries raise permission errors — corresponding
    fields `None` and one warning each
  - Test: a sub-query raises `HardwareInventoryQueryError` — snapshot aborts
    and re-raises
  - Test: `collected_at` is a valid ISO-8601 UTC timestamp
  - _Requirements: 10_

- [ ] 38. Checkpoint - Ensure all tests pass
  - Run all unit tests and property-based tests
  - Verify all properties pass with 100+ iterations
  - Ensure all tests pass, ask the user if questions arise
