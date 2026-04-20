# Implementation Plan — Maintenance Reporting

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `maintenancereporting` module
  - `source/packages/automojo/sysman/linux/redhat/maintenancereporting.py`
    exists
  - Module comment makes clear the feature has no runner or command-execution
    dependencies
  - _Requirements: 1_

- [x] 2. Define the `ActionStatus` enum
  - Members: `SUCCESS`, `FAILED`, `SKIPPED`, `DRY_RUN` (string-valued)
  - _Requirements: 2, 3_

- [x] 3. Define the `ActionRecord` dataclass
  - Fields: `feature`, `operation`, `status`, `detail`, `error`,
    `duration_seconds`, `timestamp`
  - `__post_init__` defaults `timestamp` to `datetime.utcnow()` when omitted
  - _Requirements: 2_

- [x] 4. Define the `ReportSummary` dataclass
  - Fields: `total`, `succeeded`, `failed`, `skipped`, `dry_run` (defaults 0)
  - _Requirements: 4_

- [x] 5. Define the `ReportWriteResult` dataclass
  - Fields: `success`, `path`, `dry_run`, `error`
  - _Requirements: 7_

- [x] 6. Define the `MaintenanceReportConfig` dataclass
  - Field: `dry_run: bool = False`
  - _Requirements: 7.8_

## MaintenanceReport Class

- [x] 7. Implement `__init__`
  - Accept `run_id`, `host`, `description`, `config` (all optional)
  - Auto-generate `run_id` via `uuid.uuid4()` when None
  - Auto-discover `host` via `socket.gethostname()` (fall back to
    `"unknown"` when it raises)
  - Record `started_at = datetime.utcnow()`
  - Initialise empty `_actions`, `_finished_at=None`, `_overall_status=None`
  - _Requirements: 1_

- [x] 8. Expose read-only properties for `run_id`, `host`, `description`,
  `started_at`, `finished_at`, `overall_status`, `actions` (snapshot), and
  `config`
  - _Requirements: 1, 2, 3, 4_

- [x] 9. Implement `add_action(record)`
  - Append to `_actions` preserving insertion order
  - _Requirements: 2.1_

- [x] 10. Implement `finalise()`
  - Record `finished_at = datetime.utcnow()`
  - Delegate to `_compute_overall_status(actions=...)` which:
    - Returns `SUCCESS` for the empty action list
    - Returns `FAILED` if any action has `status=FAILED`
    - Returns `DRY_RUN` if every action has `status=DRY_RUN`
    - Returns `SKIPPED` if every non-dry-run action has `status=SKIPPED`
    - Otherwise returns `SUCCESS`
  - Idempotent: re-invocation refreshes both fields
  - _Requirements: 3_

- [x] 11. Implement `summary_counts()`
  - Return `ReportSummary` tallying SUCCESS/FAILED/SKIPPED/DRY_RUN counts
    and total
  - _Requirements: 4_

- [x] 12. Implement `actions_by_status(*statuses)`
  - Return subset of actions whose status is in `statuses`, preserving
    insertion order
  - Return `[]` when no statuses are supplied
  - _Requirements: 8_

- [x] 13. Implement `actions_by_feature(feature)`
  - Return subset of actions where `action.feature == feature`
  - _Requirements: 9_

## Serialisation

- [x] 14. Implement `to_json(indent=2)`
  - Build payload via `_report_to_dict` and render via
    `json.dumps(payload, indent=indent, default=str)`
  - Serialise `started_at`, `finished_at`, and `timestamp` as ISO 8601
  - Serialise `overall_status` as the enum's string value (or `null`)
  - Include summary counts inline
  - _Requirements: 5_

- [x] 15. Implement `to_text()`
  - Render header (run_id, host, description, started_at, finished_at,
    overall_status), summary line, and one line per action (with optional
    `detail`/`error` indented below)
  - _Requirements: 6.1, 6.2_

- [x] 16. Implement `write_report(path, format)`
  - Dry-run: return `ReportWriteResult(success=True, dry_run=True)` without
    writing
  - Validate `format in {"json", "text"}`, else return failure result
  - Call `to_json`/`to_text`, create parent directory via
    `os.makedirs(parent, exist_ok=True)`, open UTF-8 file, and write
  - Catch exceptions and return `ReportWriteResult(success=False,
    error=str(ex))`
  - _Requirements: 7_

## Tests — Implemented

- [x] 17. Test `add_action` preserves insertion order
  - _Requirements: 2.1_ (`test_add_action_appends_to_report`)

- [x] 18. Test `summary_counts` tallies each status
  - _Requirements: 4.1_ (`test_summary_counts_tallies_by_status`)

- [x] 19. Test `finalise` sets `overall_status=FAILED` when any action
  failed, populates `finished_at`
  - _Requirements: 3.2, 3.1_
    (`test_finalise_sets_overall_status_failed_when_any_failed`)

- [x] 20. Test `to_json` emits parseable JSON with run metadata and action
  list
  - _Requirements: 5.1, 5.3, 5.4_ (`test_to_json_serialises_all_fields`)

- [x] 21. Test `write_report` dry-run does not create a file
  - _Requirements: 7.8_ (`test_write_report_dry_run_does_not_create_file`)

- [x] 22. Test `write_report` writes JSON payload to disk
  - _Requirements: 7.1, 7.3, 7.6_ (`test_write_report_writes_json_to_disk`)

- [x] 23. Test `actions_by_feature` filters to matching feature
  - _Requirements: 9.1_ (`test_actions_by_feature_filters_records`)

## Tests — Gaps

- [ ] 24. Test `finalise` sets `overall_status=DRY_RUN` when every action is
  dry-run
  - _Requirements: 3.3_

- [ ] 25. Test `finalise` sets `overall_status=SKIPPED` when every
  non-dry-run action is skipped and no failures are present
  - _Requirements: 3.4_

- [ ] 26. Test `finalise` with zero actions sets `overall_status=SUCCESS`
  - _Requirements: 3.6_

- [ ] 27. Test `finalise` is idempotent — second call refreshes
  `finished_at` and recomputes status after new actions are added
  - _Requirements: 3.7_

- [ ] 28. Test `__init__` auto-generates `run_id` via `uuid.uuid4()` when
  omitted
  - _Requirements: 1.3_

- [ ] 29. Test `__init__` auto-discovers `host` and falls back to
  `"unknown"` when `socket.gethostname()` raises
  - _Requirements: 1.5_

- [ ] 30. Test `to_text` renders action detail and error lines
  - _Requirements: 6.2_

- [ ] 31. Test `write_report` with unsupported format returns failure with
  descriptive error
  - _Requirements: 7.5_

- [ ] 32. Test `write_report` creates parent directories when missing
  - _Requirements: 7.2_

- [ ] 33. Test `write_report` exception path returns `success=False` with
  `error=str(ex)` (e.g. path under a read-only filesystem)
  - _Requirements: 7.7_

- [ ] 34. Test `actions_by_status` with multiple statuses and empty
  argument list
  - _Requirements: 8.1, 8.2_

## Known Issues

- [x] 35. Replace deprecated `datetime.utcnow()` with
  `datetime.now(timezone.utc)`
  - `datetime.utcnow()` was deprecated in Python 3.12+; migrated in
    `ActionRecord.__post_init__`, `MaintenanceReport.__init__`, and
    `MaintenanceReport.finalise` as part of the Part B.5 sweep
