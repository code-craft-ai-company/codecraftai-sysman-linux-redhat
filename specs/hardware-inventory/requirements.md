# Requirements

## Introduction

The hardware inventory feature provides the `automojo-sysman-linux-redhat` package
with the ability to query and aggregate hardware and low-level system identity
information on Red Hat Linux systems: DMI/SMBIOS, CPU topology, memory, block
devices, PCI/USB buses, network interfaces, firmware, and OS release.

A maintenance coordinator uses this feature to produce a machine-readable hardware
snapshot for maintenance reporting and to make capability-dependent decisions. All
system commands (`dmidecode`, `lscpu`, `lsblk`, `lsmem`, `lspci`, `lsusb`, `ip`,
`uname`, `hostnamectl`) and `/proc` / `/sys` reads are executed through the
`automojo-interfaces` command-runner and file-operator abstractions. This feature
does not mutate system state and therefore has no dry-run behavior distinct from
its normal behavior; all operations are read-only.

---

## Requirements

### Requirement 1: Query DMI / SMBIOS Data

**User Story:** As a maintenance coordinator, I want to query DMI/SMBIOS identity
fields (system manufacturer, product, serial, UUID, chassis, BIOS), so that I can
distinguish physical hosts and virtual machines.

#### Acceptance Criteria

1. WHEN the coordinator requests DMI identity THE SYSTEM SHALL invoke
   `dmidecode -t system -t baseboard -t chassis -t bios` via the command runner.
2. THE SYSTEM SHALL parse the output and return a `DmiIdentity` record containing
   `system_manufacturer`, `system_product`, `system_version`, `system_serial`,
   `system_uuid`, `baseboard_manufacturer`, `baseboard_product`, `baseboard_serial`,
   `chassis_type`, `bios_vendor`, `bios_version`, and `bios_release_date`.
3. WHEN `dmidecode` is not installed THE SYSTEM SHALL fall back to reading fields
   from `/sys/class/dmi/id/` (e.g. `sys_vendor`, `product_name`, `product_serial`,
   `bios_vendor`, `bios_version`) and populate the record with whatever is
   available.
4. WHEN the elevated privileges required to read DMI are not available THE SYSTEM
   SHALL raise a `HardwareInventoryPermissionError` with guidance to retry with
   elevation.

---

### Requirement 2: Query CPU Topology and Features

**User Story:** As a maintenance coordinator, I want to query the CPU topology
(sockets, cores, threads, NUMA nodes) and feature flags, so that capacity and
capability decisions can be made.

#### Acceptance Criteria

1. WHEN the coordinator requests CPU info THE SYSTEM SHALL invoke `lscpu -J`.
2. THE SYSTEM SHALL return a `CpuInfo` record containing `arch`, `byte_order`,
   `cpus`, `threads_per_core`, `cores_per_socket`, `sockets`, `numa_nodes`,
   `vendor`, `model_name`, `model`, `family`, `stepping`, `mhz`, `virtualization`,
   `hypervisor`, `flags` (list of strings), `l1d_cache`, `l1i_cache`, `l2_cache`,
   and `l3_cache`.
3. WHEN `lscpu` is absent THE SYSTEM SHALL fall back to parsing `/proc/cpuinfo` and
   populate the record as best it can.
4. WHEN the command fails THE SYSTEM SHALL raise a `HardwareInventoryQueryError`.

---

### Requirement 3: Query Memory Installed and Online

**User Story:** As a maintenance coordinator, I want to query installed memory size,
online memory, and DIMM-level inventory, so that I can confirm hardware matches the
purchase order and spot degraded DIMMs.

#### Acceptance Criteria

1. WHEN the coordinator requests the aggregate memory view THE SYSTEM SHALL invoke
   `lsmem --output-all -J` when available, else fall back to `/proc/meminfo`.
2. THE SYSTEM SHALL return a `MemoryInfo` record containing `total_online_bytes`,
   `total_offline_bytes`, `memory_block_size`, `available_bytes`, `free_bytes`,
   `swap_total_bytes`, and `swap_free_bytes`.
3. WHEN the coordinator requests DIMM-level detail AND `dmidecode` is installed
   THE SYSTEM SHALL invoke `dmidecode -t memory` and populate a list of `Dimm`
   records containing `locator`, `bank_locator`, `size_bytes`, `type`, `speed_mts`,
   `manufacturer`, `part_number`, and `serial`.
4. WHEN `dmidecode` is not installed THE SYSTEM SHALL return an empty DIMM list.
5. WHEN the elevated privileges required to read `dmidecode` are not available
   THE SYSTEM SHALL raise a `HardwareInventoryPermissionError`.

---

### Requirement 4: Query Block Devices

**User Story:** As a maintenance coordinator, I want to enumerate block devices and
their type/size/model/serial, so that the physical storage layout is captured in the
inventory.

#### Acceptance Criteria

1. WHEN the coordinator requests the block device list THE SYSTEM SHALL invoke
   `lsblk -J -b -o NAME,KNAME,TYPE,SIZE,ROTA,MODEL,SERIAL,VENDOR,WWN,TRAN,FSTYPE,UUID,LABEL,MOUNTPOINT`.
2. THE SYSTEM SHALL return a list of `BlockDeviceInventory` records populated from
   the JSON output.
3. WHEN the command fails THE SYSTEM SHALL raise a `HardwareInventoryQueryError`.

---

### Requirement 5: Query PCI Devices

**User Story:** As a maintenance coordinator, I want to enumerate PCI devices with
their vendor, product, class, and bound driver, so that I can verify drivers are
loaded for expected hardware.

#### Acceptance Criteria

1. WHEN the coordinator requests the list THE SYSTEM SHALL invoke
   `lspci -vmm -nnk`.
2. THE SYSTEM SHALL parse the multi-record output and return a list of
   `PciDevice` records containing `slot`, `class`, `vendor`, `device`,
   `vendor_id`, `device_id`, `subsystem_vendor`, `subsystem_device`, `driver`,
   and `modules_available`.
3. WHEN `lspci` is not installed THE SYSTEM SHALL return an empty list.
4. WHEN the command fails THE SYSTEM SHALL raise a
   `HardwareInventoryQueryError`.

---

### Requirement 6: Query USB Devices

**User Story:** As a maintenance coordinator, I want to enumerate USB devices, so
that attached peripherals can be audited.

#### Acceptance Criteria

1. WHEN the coordinator requests the list THE SYSTEM SHALL invoke `lsusb`.
2. THE SYSTEM SHALL parse the output into `UsbDevice` records containing `bus`,
   `device`, `vendor_id`, `product_id`, and `description`.
3. WHEN `lsusb` is not installed THE SYSTEM SHALL return an empty list.
4. WHEN the command fails THE SYSTEM SHALL raise a
   `HardwareInventoryQueryError`.

---

### Requirement 7: Query Network Interface Hardware

**User Story:** As a maintenance coordinator, I want to enumerate network interfaces
with their link state, MAC address, MTU, driver, speed, and duplex, so that hardware
discovery reflects the network adapters present.

#### Acceptance Criteria

1. WHEN the coordinator requests the interface list THE SYSTEM SHALL invoke
   `ip -j -d link show`.
2. THE SYSTEM SHALL merge the result with per-interface fields from
   `/sys/class/net/<iface>/speed`, `/sys/class/net/<iface>/duplex`, and
   `/sys/class/net/<iface>/device/driver`, where available.
3. THE SYSTEM SHALL return a list of `NetInterfaceHardware` records containing
   `name`, `mac`, `link_state`, `mtu`, `driver`, `speed_mbps`, `duplex`, and
   `pci_slot` (if discoverable).
4. THE SYSTEM SHALL NOT query IP addressing or routing; those remain the
   responsibility of the `network-configuration` feature.
5. WHEN the command fails THE SYSTEM SHALL raise a
   `HardwareInventoryQueryError`.

---

### Requirement 8: Query Kernel and OS Release

**User Story:** As a maintenance coordinator, I want to query the running kernel,
architecture, and OS release identity, so that platform decisions can be made from
the inventory.

#### Acceptance Criteria

1. WHEN the coordinator requests kernel info THE SYSTEM SHALL invoke
   `uname -srvmo` and parse the four fields.
2. WHEN the coordinator requests OS release info THE SYSTEM SHALL read
   `/etc/os-release` and parse its shell-style `KEY=value` lines.
3. WHEN the coordinator requests machine identity THE SYSTEM SHALL invoke
   `hostnamectl --json=short` when available, else read `/etc/machine-id` and
   `/etc/hostname`.
4. THE SYSTEM SHALL return an `OsRelease` record containing `kernel_name`,
   `kernel_release`, `kernel_version`, `machine`, `os`, `id`, `id_like`,
   `version`, `version_id`, `pretty_name`, `cpe_name`, `machine_id`, and
   `hostname`.

---

### Requirement 9: Query Firmware / BIOS Version

**User Story:** As a maintenance coordinator, I want to query the BIOS / UEFI
firmware vendor and version, so that firmware updates can be scoped.

#### Acceptance Criteria

1. WHEN the coordinator requests firmware info THE SYSTEM SHALL read
   `/sys/class/dmi/id/bios_vendor`, `.../bios_version`, and `.../bios_date`
   through the file operator.
2. WHEN those files are unreadable AND `dmidecode` is installed THE SYSTEM SHALL
   fall back to `dmidecode -s bios-vendor`, `-s bios-version`, and
   `-s bios-release-date`.
3. THE SYSTEM SHALL return a `FirmwareInfo` record containing `vendor`,
   `version`, `release_date`, and `mode` (`BIOS` or `UEFI` — detected by the
   presence of `/sys/firmware/efi`).
4. WHEN no firmware data is available THE SYSTEM SHALL return a record with
   `available=False`.

---

### Requirement 10: Combined Inventory Snapshot

**User Story:** As a maintenance coordinator, I want a single call that produces a
combined inventory snapshot, so that the full hardware state can be serialized into
a maintenance report in one step.

#### Acceptance Criteria

1. WHEN the coordinator requests the combined snapshot THE SYSTEM SHALL invoke the
   capabilities described in Requirements 1 through 9 and aggregate the results.
2. THE SYSTEM SHALL return a `HardwareSnapshot` record containing `dmi`, `cpu`,
   `memory`, `block_devices`, `pci`, `usb`, `net_hardware`, `os_release`, and
   `firmware` fields, plus a `collected_at` timestamp.
3. WHEN a sub-query raises a `HardwareInventoryPermissionError` THE SYSTEM SHALL
   leave the corresponding field as `None` and SHALL append a
   `PartialInventoryWarning` note to the returned record rather than aborting the
   whole snapshot.
4. WHEN a sub-query raises any other exception THE SYSTEM SHALL re-raise it and
   abort the snapshot.

---

### Requirement 11: Elevated Privilege Execution

**User Story:** As a maintenance coordinator, I want DMI and DIMM-level queries that
require root privilege to run via the command runner's elevation mechanism, so that
a full inventory can be collected from a standard user context when elevation is
available.

#### Acceptance Criteria

1. WHEN the coordinator requests DMI or DIMM data AND the system is configured for
   sudo elevation AND the process is not root THE SYSTEM SHALL prefix `dmidecode`
   with the command runner's elevation mechanism.
2. WHEN the system is running as root THE SYSTEM SHALL NOT prefix commands with
   `sudo`.
3. WHEN all other commands in this feature are invoked THE SYSTEM SHALL NOT use
   elevation.
