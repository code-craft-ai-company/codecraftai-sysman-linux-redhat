# Implementation Plan — Subscription Management

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `subscriptionmanagement` module

- [x] 2. Define `SubscriptionActionStatus` enum (SUCCESS / FAILED /
  SKIPPED / DRY_RUN)

- [x] 3. Define `SubscriptionManagerConfig` (dry_run, elevated)

- [x] 4. Define `SubscriptionActionResult`, `SubscriptionStatus`
  dataclasses

## SubscriptionManager — Implemented

- [x] 5. Implement `__init__` accepting runner + optional config

- [x] 6. Implement `register(activation_key=None, org=None,
  username=None, password=None)` — `subscription-manager register`
  with either `--activationkey/--org` or `--username/--password`
  authentication

- [x] 7. Implement `unregister()` — `subscription-manager unregister`

- [x] 8. Implement `attach(pool=None, auto=True)` —
  `subscription-manager attach [--auto|--pool=<id>]`

- [x] 9. Implement `refresh()` — `subscription-manager refresh`

- [x] 10. Implement `enable_repo(repo_id)` /
  `disable_repo(repo_id)` — `subscription-manager repos
  --enable/--disable=<id>`

- [x] 11. Implement `status()` — `subscription-manager status`
  parsed into `SubscriptionStatus`

- [x] 12. Implement `_dispatch` helper — dry-run short-circuit and
  runner-result mapping

## SubscriptionManager — Gaps (specified but not implemented)

- [ ] 13. `set_release(version)` — `subscription-manager release
  --set=<version>`; `unset_release` / `get_release` counterparts

- [ ] 14. `list_pools(available_only=False)` —
  `subscription-manager list --available` or `--consumed` parsed
  into records

- [ ] 15. `list_consumed()` — attached subscriptions

- [ ] 16. `import_cert(path)` — `rct cat-cert` / manual cert import
  helpers

- [ ] 17. `rhsmcertd` daemon control — delegate via
  `3-service-management`

- [ ] 18. Idempotency on `register` — SKIPPED when `status()` already
  reports registered

- [ ] 19. Credential handling currently passes `password` directly
  on the argument vector; route through `codecraftai-credentials` so
  secrets aren't exposed in process listings / audit logs

## Tests — Implemented

- [x] 20. `test_register_with_activation_key`
- [x] 21. `test_register_with_username_password`
- [x] 22. `test_enable_repo_dispatches_subscription_manager`
- [x] 23. `test_status_parses_overall_status`

## Tests — Gaps

- [ ] 24. Test `unregister()` dispatch

- [ ] 25. Test `attach(pool=<id>)` and `attach(auto=False)`
  variants

- [ ] 26. Test `refresh()` dispatch

- [ ] 27. Test `disable_repo(repo_id)` dispatch

- [ ] 28. Test dry-run short-circuits for every mutating method

## Follow-ups

- [ ] 29. **codecraftai-* reuse check**: `codecraftai-credentials` for
  task 19 (secrets in register).  `3-repository-management`
  overlaps on the repos-enable/-disable surface — document the seam
  (RHSM-managed repos owned here; `.repo` files under
  `/etc/yum.repos.d/` owned by repository-management).
