# Implementation Plan — User & Group Management

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

The module exposes two managers: `UserManager` and `GroupManager`,
sharing a single `UserGroupManagerConfig`.

## Module and Models

- [x] 1. Create the `usergroupmanagement` module

- [x] 2. Define `ActionStatus` enum (SUCCESS / FAILED / SKIPPED /
  DRY_RUN)

- [x] 3. Define `UserGroupManagerConfig` (dry_run, elevated)

- [x] 4. Define spec dataclasses: `UserSpec`, `UserModSpec`,
  `GroupSpec`, `PasswordAgeSpec`

- [x] 5. Define query dataclasses: `UserInfo`, `GroupInfo`

- [x] 6. Define `UserActionResult`, `GroupActionResult`

- [x] 7. Define `UserNotFoundError` and `GroupNotFoundError`
  exceptions

## GroupManager — Implemented

- [x] 8. Implement `__init__` accepting runner + optional config

- [x] 9. Implement `create(spec)` — `groupadd [--gid] [--system]
  <name>`; SKIPPED when `query(name)` finds an existing group

- [x] 10. Implement `delete(groupname)` — `groupdel <name>`;
  SKIPPED when group absent

- [x] 11. Implement `query(groupname)` — parses `getent group
  <name>` into `GroupInfo`; returns `None` when absent

## UserManager — Implemented

- [x] 12. Implement `__init__` accepting runner + optional config

- [x] 13. Implement `create(spec)` — `useradd [--uid] [--gid]
  [--groups] [--shell] [--home] [--system] [--create-home]
  <username>`; SKIPPED when user already exists

- [x] 14. Implement `delete(username, remove_home=False)` —
  `userdel [--remove] <username>`; SKIPPED when user absent

- [x] 15. Implement `modify(username, spec)` — `usermod ...`;
  raises `UserNotFoundError` when target user doesn't exist

- [x] 16. Implement `query(username)` — `getent passwd <username>`
  parsed into `UserInfo`

- [x] 17. Implement `add_to_group(username, group)` / 
  `remove_from_group(username, group)` — `usermod -aG` / `gpasswd -d`;
  SKIPPED based on current membership

- [x] 18. Implement `lock(username)` — `usermod -L <username>`

- [x] 19. Implement `unlock(username)` — `usermod -U <username>`

- [x] 20. Implement `expire_password(username)` — `passwd -e
  <username>`

- [x] 21. Implement `set_password_age(username, age_spec)` —
  `chage` with `--mindays / --maxdays / --warndays / --inactive /
  --expiredate` as populated

- [x] 22. Implement `add_ssh_key(username, public_key)` — reads
  `~/.ssh/authorized_keys` via `cat`, SKIPPED when key already
  present; appends via `tee`; creates `~/.ssh/` with 0700 and the
  keys file with 0600 when missing

- [x] 23. Implement `remove_ssh_key(username, public_key)` —
  strips matching line from `authorized_keys`; SKIPPED when absent

## Gaps (specified but not implemented)

- [ ] 24. `list_users()` / `list_groups()` — enumerate via
  `getent passwd` / `getent group`

- [ ] 25. `list_ssh_keys(username)` — return parsed
  `authorized_keys` entries for introspection

- [ ] 26. `set_password(username, password)` — currently only
  `expire_password` / `set_password_age` are exposed; plain-text
  `passwd` input is missing (needs `chpasswd` via stdin + secrets
  via `codecraftai-credentials`)

- [ ] 27. Idempotency for `modify()` — currently always dispatches
  `usermod` even when all fields already match

- [ ] 28. User/group ID validation at `create` time (reject
  overlapping UIDs/GIDs explicitly)

- [ ] 29. `copy_user(src, dst)` — clone a user including groups
  and shell

## Tests — Implemented

- [x] 30. `test_user_query_returns_info_when_present`
- [x] 31. `test_user_query_returns_none_when_absent`
- [x] 32. `test_user_create_when_absent`
- [x] 33. `test_user_create_skipped_when_present`
- [x] 34. `test_user_delete_when_present`
- [x] 35. `test_user_delete_skipped_when_absent`
- [x] 36. `test_user_modify_requires_existing_user`
- [x] 37. `test_user_modify_dispatches_usermod`
- [x] 38. `test_user_add_to_group_skipped_when_already_member`
- [x] 39. `test_user_add_to_group_dispatches_usermod`
- [x] 40. `test_user_remove_from_group_skipped_when_not_member`
- [x] 41. `test_user_lock_and_unlock`
- [x] 42. `test_user_set_password_age_dispatches_chage`
- [x] 43. `test_user_add_ssh_key_skipped_when_present`
- [x] 44. `test_user_add_ssh_key_writes_keys_file`
- [x] 45. `test_group_create_when_absent`
- [x] 46. `test_group_create_skipped_when_present`
- [x] 47. `test_group_delete_skipped_when_absent`
- [x] 48. `test_group_query_parses_members`

## Tests — Gaps

- [ ] 49. Test `expire_password` dispatch shape

- [ ] 50. Test `remove_ssh_key` — present and absent cases

- [ ] 51. Test `add_ssh_key` home-dir creation path (when `~/.ssh/`
  does not yet exist)

- [ ] 52. Test `group_delete` when group exists (counterpart to
  the SKIPPED test)

- [ ] 53. Test dry-run short-circuits for every mutating method

## Follow-ups

- [ ] 54. **codecraftai-* reuse check**: `codecraftai-credentials` for
  task 26 (set_password routing); `3-ssh-server-configuration`
  owns system-wide sshd config — per-user `authorized_keys` cleanly
  belongs here.
