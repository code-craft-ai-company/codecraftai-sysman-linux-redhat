# Implementation Plan — Virtualization Management

All tasks are unchecked — this is a new feature with no existing
implementation.

## Module and Models

- [ ] 1. Create `source/packages/ccai/sysman/linux/redhat/virtualizationmanagement.py`
  - _Requirements: All_

- [ ] 2. Define `VirtActionStatus` enum (SUCCESS / FAILED / SKIPPED
  / DRY_RUN)
  - _Requirements: 7_

- [ ] 3. Define `VirtualizationManagerConfig` dataclass with
  `dry_run`, `elevated`, `uri="qemu:///system"`, `virsh_bin`,
  `virt_install_bin`
  - _Requirements: 7.4_

- [ ] 4. Define `VirtActionResult` dataclass

- [ ] 5. Define `DomainInfo`, `VirtInstallSpec`, `NetworkInfo`,
  `StoragePoolInfo`, `StorageVolumeInfo`, `SnapshotInfo`
  dataclasses

## Helpers

- [ ] 6. Implement `__init__` accepting runner + optional config

- [ ] 7. Implement `_virsh(args)` — returns `[virsh_bin,
  "--connect", uri, *args]` (omits `--connect` when `uri == ""`)

- [ ] 8. Implement `_dispatch(command, detail=None, stdin=None)` —
  dry-run short-circuit + result mapping

- [ ] 9. Implement `_write_temp_xml(content)` context manager /
  helper that writes via `tee` to a temp path and cleans up in a
  `finally` block

## Domain Lifecycle

- [ ] 10. Implement `list_domains()` — `virsh list --all --name`
  + `virsh domstate` per name → `list[DomainInfo]`
  - _Requirements: 1.1_

- [ ] 11. Implement `query_domain(name)` — `virsh dominfo <name>`
  → `DomainInfo` or `None`
  - _Requirements: 1.2_

- [ ] 12. Implement `start_domain(name)` / `shutdown_domain(name)`
  / `destroy_domain(name)` / `reboot_domain(name)` with SKIPPED
  semantics based on `query_domain().state`
  - _Requirements: 1.3, 1.4, 1.5, 1.6_

- [ ] 13. Implement `set_autostart(name, enabled)` — `virsh
  autostart [--disable] <name>`; SKIPPED when already matching
  - _Requirements: 1.7_

## Domain Definition

- [ ] 14. Implement `define_domain(xml_content)` — temp-file +
  `virsh define`; cleanup guaranteed
  - _Requirements: 2.1_

- [ ] 15. Implement `undefine_domain(name, remove_storage=False,
  nvram=False)` — SKIPPED when not defined
  - _Requirements: 2.2_

- [ ] 16. Implement `dumpxml(name)` — returns raw XML text
  - _Requirements: 2.3_

## virt-install

- [ ] 17. Implement `create_vm(spec, replace=False)` with the
  replace-undefine-first flow and idempotency
  - _Requirements: 3_

## Networks

- [ ] 18. Implement `list_networks()` / `query_network(name)` /
  `define_network(xml_content)` / `undefine_network(name)` /
  `start_network(name)` / `destroy_network(name)` /
  `set_network_autostart(name, enabled)`
  - _Requirements: 4_

## Pools and Volumes

- [ ] 19. Implement `list_pools()` / `define_pool(xml_content)` /
  `start_pool(name)` / `set_pool_autostart(name, enabled)`
  - _Requirements: 5.1, 5.2_

- [ ] 20. Implement `create_volume(pool, name, size,
  format_="qcow2")` — SKIPPED when already present
  - _Requirements: 5.3_

- [ ] 21. Implement `delete_volume(pool, name)` — SKIPPED when
  absent
  - _Requirements: 5.4_

- [ ] 22. Implement `list_volumes(pool)` — `virsh vol-list
  --details` → `list[StorageVolumeInfo]`
  - _Requirements: 5.5_

## Snapshots

- [ ] 23. Implement `create_snapshot(domain, name, description=None)`
  - _Requirements: 6.1_

- [ ] 24. Implement `list_snapshots(domain)` parsed from tree output
  - _Requirements: 6.2_

- [ ] 25. Implement `revert_snapshot(domain, name)` — idempotency
  not assumed; caller decides

- [ ] 26. Implement `delete_snapshot(domain, name)` — SKIPPED when
  absent
  - _Requirements: 6.4_

## Tests

- [ ] 27. Create `test_virtualizationmanagement.py` with
  `_MockRunner`

- [ ] 28. Tests for domain listing and query parsers (`virsh list`,
  `virsh dominfo`)

- [ ] 29. Tests for lifecycle SKIPPED branches (start when running,
  stop when already off)

- [ ] 30. Tests for `define_domain` temp-file cleanup on success
  AND on failure

- [ ] 31. Tests for `create_vm` idempotency and `replace=True`
  flow

- [ ] 32. Parser tests for `virsh net-list`, `pool-list --details`,
  `vol-list --details`

- [ ] 33. Snapshot lifecycle tests incl. SKIPPED delete

- [ ] 34. Dry-run short-circuit sweep across every mutating method

- [ ] 35. Failure-mapping sweep

## Follow-ups

- [ ] 36. **codecraftai-* reuse check**: `codecraftai-interop` may
  already wrap libvirt — confirm before extending `virsh` parsing
  here.
