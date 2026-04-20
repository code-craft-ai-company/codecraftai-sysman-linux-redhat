# Implementation Plan — Network Configuration

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `networkconfiguration` module

- [x] 2. Define `NetworkActionStatus` enum (SUCCESS / FAILED / SKIPPED
  / DRY_RUN)

- [x] 3. Define `NetworkManagerConfig` (dry_run, elevated)

- [x] 4. Define `NetworkActionResult` dataclass

- [x] 5. Define query/spec dataclasses: `HostnameInfo`, `HostsEntry`,
  `NtpSyncInfo`, `NmConnectionSpec`, `NmConnectionInfo`

## NetworkConfigurator — Implemented

- [x] 6. Implement `__init__` accepting runner + optional config

### Hostname

- [x] 7. Implement `set_hostname(hostname)` — `hostnamectl
  set-hostname`; SKIPPED when `query_hostname()` already matches

- [x] 8. Implement `query_hostname()` — `hostnamectl --json=short`
  parsed into `HostnameInfo`

### /etc/hosts

- [x] 9. Implement `add_hosts_entry(ip, hostname, aliases)` — read
  `/etc/hosts` via `cat`, upsert via `_upsert_hosts_entry`, write via
  `tee`; idempotent

- [x] 10. Implement `remove_hosts_entry(ip)` — `_remove_hosts_entry`
  filter + rewrite

- [x] 11. Implement `query_hosts()` — parse `/etc/hosts` into
  `HostsEntry` records

### DNS

- [x] 12. Implement `set_nameservers(nameservers)` — replace
  `nameserver` lines in `/etc/resolv.conf`

- [x] 13. Implement `set_search_domains(domains)` — replace `search`
  line in `/etc/resolv.conf`

### NTP / Chrony

- [x] 14. Implement `add_ntp_server(address)` — append `server
  <address> iburst` to `/etc/chrony.conf`; idempotent

- [x] 15. Implement `remove_ntp_server(address)` — strip matching
  `server <address> ...` line

- [x] 16. Implement `query_ntp_sync()` — `chronyc tracking` parsed
  into `NtpSyncInfo`

### NetworkManager connections

- [x] 17. Implement `add_connection(spec)` — `nmcli connection add`
  with IP mode, address, gateway, DNS as specified

- [x] 18. Implement `remove_connection(name)` — `nmcli connection
  delete`; SKIPPED when absent

- [x] 19. Implement `query_connections()` — `nmcli -t connection
  show` parsed into `NmConnectionInfo`

- [x] 20. Implement `_find_connection(name)` helper for idempotent
  add/remove

## NetworkConfigurator — Gaps (specified but not implemented)

- [ ] 21. Manage `systemd-resolved` / `/etc/systemd/resolved.conf` —
  alternative to direct `/etc/resolv.conf` writes for systems where
  `resolved` is authoritative

- [ ] 22. Bring connections up/down (`nmcli connection up/down
  <name>`)

- [ ] 23. Modify existing connection (`nmcli connection modify`) —
  currently only add/remove

- [ ] 24. Query interface state (`ip link show` / `ip addr show`)
  returning structured records

- [ ] 25. `force_refresh_ntp()` — `chronyc makestep`

- [ ] 26. Read/write `/etc/hostname` directly as fallback when
  `hostnamectl` is unavailable

## Tests — Implemented

- [x] 27. `test_set_hostname_skipped_when_matching`
- [x] 28. `test_set_hostname_dispatches_hostnamectl`
- [x] 29. `test_add_hosts_entry_writes_tee`
- [x] 30. `test_remove_hosts_entry_strips_line`
- [x] 31. `test_set_nameservers_replaces_existing_lines`
- [x] 32. `test_add_ntp_server_appends_line`
- [x] 33. `test_add_connection_dispatches_nmcli`

## Tests — Gaps

- [ ] 34. Test `set_search_domains`

- [ ] 35. Test `remove_ntp_server` + idempotency

- [ ] 36. Test `query_ntp_sync` parser

- [ ] 37. Test `remove_connection` + SKIPPED path

- [ ] 38. Test `query_connections` parser

- [ ] 39. Test dry-run short-circuits for every mutating method

## Follow-ups

- [ ] 40. **automojo-* reuse check**: `automojo-networking` is a
  sibling package — verify it is not duplicating any of this feature's
  responsibilities (hostname, /etc/hosts, DNS, NTP, NetworkManager).
