# Implementation Plan — SELinux Management

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

Complements `3-security-compliance` which handles coarse SELinux mode
queries and persistence.  This feature owns file/port contexts,
booleans, and policy modules.

## Module and Models

- [x] 1. Create the `selinuxmanagement` module

- [x] 2. Define `SELinuxActionStatus` enum (SUCCESS / FAILED /
  SKIPPED / DRY_RUN)

- [x] 3. Define `SELinuxManagerConfig` (dry_run, elevated)

- [x] 4. Define `SELinuxContext`, `SELinuxBoolean`,
  `SELinuxPortRule`, `SELinuxActionResult` dataclasses

## SELinuxManager — Implemented

- [x] 5. Implement `__init__` accepting runner + optional config

- [x] 6. Implement `query_context(path)` — `ls -Zd <path>` parsed
  into `SELinuxContext` (user / role / type / level); returns `None`
  on non-zero exit

- [x] 7. Implement `set_context(path, user, role, type_, level,
  recursive)` — `chcon [...args...] [-R] <path>`

- [x] 8. Implement `restore_context(path, recursive)` — `restorecon
  [-R] <path>`

- [x] 9. Implement `get_boolean(name)` — `getsebool -N <name>`;
  returns bool

- [x] 10. Implement `set_boolean(name, value, persistent=True)` —
  `setsebool [-P] <name> <on|off>`; SKIPPED when current value
  matches

- [x] 11. Implement `list_booleans()` — `getsebool -a` parsed into
  `list[SELinuxBoolean]`

- [x] 12. Implement `add_port_context(port, protocol, context)` —
  `semanage port -a -t <context> -p <proto> <port>`

- [x] 13. Implement `remove_port_context(port, protocol)` —
  `semanage port -d -p <proto> <port>`

- [x] 14. Implement `list_port_contexts(context=None)` —
  `semanage port -l` with optional type filter

- [x] 15. Implement `_dispatch` helper — dry-run short-circuit and
  runner-result mapping

## SELinuxManager — Gaps (specified but not implemented)

- [ ] 16. `semodule_load(name, path)` / `semodule_remove(name)` —
  custom `.pp` policy module management

- [ ] 17. `list_modules()` — `semodule -l`

- [ ] 18. `match_path_context(path)` — `matchpathcon <path>` to
  query the policy-expected label for a path

- [ ] 19. File-context rules (`semanage fcontext -a/-d/-l`) —
  complements `restorecon`

- [ ] 20. `generate_policy_from_audit(log_lines)` — `audit2allow`
  wrapper that returns a `.te` / `.pp` module from captured AVC
  denials

- [ ] 21. Persist SELinux mode in `/etc/selinux/config` — handled in
  `3-security-compliance.set_selinux_mode(persistent=True)`;
  documented seam rather than a gap here

## Tests — Implemented

- [x] 22. `test_query_context_parses_ls_output`
- [x] 23. `test_set_context_dispatches_chcon`
- [x] 24. `test_set_boolean_skipped_when_current_matches`
- [x] 25. `test_set_boolean_dispatches_setsebool`
- [x] 26. `test_add_port_context_dispatches_semanage`

## Tests — Gaps

- [ ] 27. Test `restore_context` — plain and `-R` recursive

- [ ] 28. Test `get_boolean` parsing — handle `on` / `off`

- [ ] 29. Test `list_booleans` parser

- [ ] 30. Test `remove_port_context` dispatch

- [ ] 31. Test `list_port_contexts` with / without context filter

- [ ] 32. Test `set_context` with individual fields (user-only,
  role-only, etc.)

- [ ] 33. Test dry-run short-circuits for every mutating method

## Follow-ups

- [ ] 34. **automojo-* reuse check**: nothing obvious to delegate to
  beyond the fine-grained runner primitives already in use.  Keep the
  seam with `3-security-compliance` clean when tasks 16–20 land.
