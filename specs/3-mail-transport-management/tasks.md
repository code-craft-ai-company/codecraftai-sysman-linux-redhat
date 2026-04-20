# Implementation Plan — Mail Transport Management

All tasks are unchecked — new feature.

## Module and Models

- [ ] 1. Create `source/packages/automojo/sysman/linux/redhat/mailtransportmanagement.py`
- [ ] 2. Define `MailActionStatus`, `MailTransportManagerConfig`,
  `MailActionResult`, `MasterCfService`, `QueuedMessage`
  dataclasses

## Helpers

- [ ] 3. Implement `__init__(runner, config=None, service_manager=None)`
- [ ] 4. Implement `_read_file(path)` / `_write_file(path, content)`
  via `cat` / `tee`
- [ ] 5. Implement `_dispatch(command, detail=None)` with dry-run
  short-circuit + result mapping
- [ ] 6. Implement `_maybe_autoreload()` — called after mutations
  when `config.autoreload=True`

## main.cf

- [ ] 7. Implement `get_main_directive(key)` — `postconf -h`
  - _Requirements: 1.1_

- [ ] 8. Implement `get_all_main_directives()` — `postconf` parsed
  into `dict[str, str]`
  - _Requirements: 1.2_

- [ ] 9. Implement `set_main_directive(key, value)` —
  idempotent `postconf -e`
  - _Requirements: 1.3_

- [ ] 10. Implement `remove_main_directive(key)` — `postconf -X`;
  SKIPPED when already at default
  - _Requirements: 1.4_

## master.cf

- [ ] 11. Implement `get_master_service(service, type_)` —
  `postconf -Mh <service>/<type>`
  - _Requirements: 2.1_

- [ ] 12. Implement `set_master_service(spec)` — `postconf -M`;
  SKIPPED when current line matches
  - _Requirements: 2.2_

- [ ] 13. Implement `set_master_directive(service, type_, key,
  value)` — `postconf -P`; SKIPPED when unchanged
  - _Requirements: 2.3_

- [ ] 14. Implement `remove_master_service(service, type_)` —
  `postconf -MX`; SKIPPED when absent
  - _Requirements: 2.4_

## Maps

- [ ] 15. Implement `write_map(name, content)` — idempotent
  content compare; always calls `compile_map` after a content
  change; also calls `compile_map` when content matched but `.db`
  is stale / missing
  - _Requirements: 3.1_

- [ ] 16. Implement `compile_map(name)` — `postmap
  /etc/postfix/<name>`
  - _Requirements: 3.2_

- [ ] 17. Implement `query_map(name, key)` — `postmap -q`; `None`
  on non-zero exit
  - _Requirements: 3.3_

- [ ] 18. Implement `remove_map(name)` — `rm -f` source + `.db`;
  SKIPPED when both absent
  - _Requirements: 3.4_

## /etc/aliases

- [ ] 19. Implement `list_aliases()` — parse into
  `dict[str, list[str]]`
  - _Requirements: 4.4_

- [ ] 20. Implement `set_alias(name, targets)` — idempotent
  upsert; chains `newaliases`
  - _Requirements: 4.1, 4.3_

- [ ] 21. Implement `remove_alias(name)` — strip matching line;
  SKIPPED when absent; chains `newaliases`
  - _Requirements: 4.2, 4.3_

## Queue

- [ ] 22. Implement `list_queue()` — `postqueue -p` parsed into
  `list[QueuedMessage]`; empty list when "Mail queue is empty"
  - _Requirements: 5.1_

- [ ] 23. Implement `flush_queue()` — `postqueue -f`
  - _Requirements: 5.2_

- [ ] 24. Implement `delete_message(queue_id)` — `postsuper -d`;
  SKIPPED when queue id does not exist
  - _Requirements: 5.3_

- [ ] 25. Implement `delete_all_queued()` — `postsuper -d ALL`
  - _Requirements: 5.4_

- [ ] 26. Implement `requeue_message(queue_id)` — `postsuper -r`
  - _Requirements: 5.5_

## Service lifecycle

- [ ] 27. Implement `reload()` / `restart()` delegating to
  `service_manager.reload("postfix")` / `.restart("postfix")`
  - _Requirements: 6.1, 6.2_

## Tests

- [ ] 28. Create `test_mailtransportmanagement.py` with
  `_MockRunner` + `_MockServiceManager`
- [ ] 29. Parser tests for `postconf`, `postconf -h`,
  `postqueue -p`
- [ ] 30. Tests for idempotent `set_main_directive` /
  `set_master_service` / `set_alias`
- [ ] 31. Tests for map-write chain: content-changed → postmap
  fires; content-matched but `.db` missing → postmap fires
- [ ] 32. Tests that every alias mutation triggers `newaliases`
- [ ] 33. Tests for `autoreload=True` path — mutation calls
  service_manager.reload exactly once
- [ ] 34. Tests that `reload()` / `restart()` delegate and never
  spell `systemctl`
- [ ] 35. Dry-run sweep
- [ ] 36. Failure-mapping sweep

## Follow-ups

- [ ] 37. **automojo-* reuse check**: nothing obvious in sibling
  packages for MTA plumbing; verify before extending.
