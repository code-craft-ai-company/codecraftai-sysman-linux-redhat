# Implementation Plan — Insights Management

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `insightsmanagement` module

- [x] 2. Define `InsightsActionStatus` enum (SUCCESS / FAILED /
  SKIPPED / DRY_RUN)

- [x] 3. Define `InsightsManagerConfig` (dry_run, elevated)

- [x] 4. Define `InsightsActionResult` dataclass

## InsightsManager — Implemented

- [x] 5. Implement `__init__` accepting runner + optional config

- [x] 6. Implement `register()` — `insights-client --register`

- [x] 7. Implement `unregister()` — `insights-client --unregister`

- [x] 8. Implement `status()` — `insights-client --status`

- [x] 9. Implement `run_check()` — `insights-client` (default upload)

- [x] 10. Implement `_dispatch` helper — dry-run short-circuit and
  runner-result mapping

## InsightsManager — Gaps (specified but not implemented)

- [ ] 11. Read/write `/etc/insights-client/insights-client.conf` —
  manage `base_url`, `auto_config`, `display_name`, `proxy`,
  `loglevel`, etc.

- [ ] 12. Tag-file management under `/etc/insights-client/tags.yaml`

- [ ] 13. `test_connection()` — `insights-client --test-connection`
  wrapper with structured result

- [ ] 14. `version()` helper — `insights-client --version` returning
  parsed version string

- [ ] 15. Idempotency on `register` — SKIPPED when already registered
  (check `status()` first)

## Tests — Implemented

- [x] 16. `test_register_dispatches_insights_client`

- [x] 17. `test_run_check_dispatches_insights_client`

## Tests — Gaps

- [ ] 18. Test `unregister()` dispatch

- [ ] 19. Test `status()` dispatch and SUCCESS/FAILED mapping

- [ ] 20. Test dry-run short-circuits for every mutating method

## Follow-ups

- [ ] 21. **automojo-* reuse check**: RHSM (`3-subscription-management`)
  and Insights are often co-managed; consider whether shared
  connection-test helpers should live in a common place.
