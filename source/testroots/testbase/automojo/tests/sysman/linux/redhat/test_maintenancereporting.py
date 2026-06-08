# Tests for MaintenanceReport.

import json
import os
import tempfile

from ccai import testbase

from ccai.sysman.linux.redhat.maintenancereporting import (
    ActionRecord,
    ActionStatus,
    MaintenanceReport,
    MaintenanceReportConfig,
)


def test_add_action_appends_to_report():
    """
        add_action() stores the supplied record in insertion order.
    """
    report = MaintenanceReport(
        run_id="run-1", host="host01",
    )
    report.add_action(ActionRecord(
        feature="package-management",
        operation="install",
        status=ActionStatus.SUCCESS,
    ))
    report.add_action(ActionRecord(
        feature="service-management",
        operation="restart",
        status=ActionStatus.SKIPPED,
    ))
    actions = report.actions
    testbase.assert_equal(len(actions), 2)
    testbase.assert_equal(actions[0].operation, "install")
    testbase.assert_equal(actions[1].status, ActionStatus.SKIPPED)
    return


def test_summary_counts_tallies_by_status():
    """
        summary_counts() tallies succeeded/failed/skipped/dry_run correctly.
    """
    report = MaintenanceReport(run_id="run-1", host="host01")
    report.add_action(ActionRecord(feature="f", operation="op", status=ActionStatus.SUCCESS))
    report.add_action(ActionRecord(feature="f", operation="op", status=ActionStatus.FAILED))
    report.add_action(ActionRecord(feature="f", operation="op", status=ActionStatus.SKIPPED))
    report.add_action(ActionRecord(feature="f", operation="op", status=ActionStatus.DRY_RUN))
    summary = report.summary_counts()
    testbase.assert_equal(summary.total, 4)
    testbase.assert_equal(summary.succeeded, 1)
    testbase.assert_equal(summary.failed, 1)
    testbase.assert_equal(summary.skipped, 1)
    testbase.assert_equal(summary.dry_run, 1)
    return


def test_finalise_sets_overall_status_failed_when_any_failed():
    """
        finalise() sets overall_status to FAILED when any action failed.
    """
    report = MaintenanceReport(run_id="run-1", host="host01")
    report.add_action(ActionRecord(feature="f", operation="op", status=ActionStatus.SUCCESS))
    report.add_action(ActionRecord(feature="f", operation="op", status=ActionStatus.FAILED))
    report.finalise()
    testbase.assert_equal(report.overall_status, ActionStatus.FAILED)
    testbase.assert_true(report.finished_at is not None)
    return


def test_to_json_serialises_all_fields():
    """
        to_json() emits a parseable JSON payload with run metadata.
    """
    report = MaintenanceReport(
        run_id="run-1", host="host01", description="desc",
    )
    report.add_action(ActionRecord(
        feature="package-management", operation="install",
        status=ActionStatus.SUCCESS, detail="nginx",
    ))
    report.finalise()
    payload = json.loads(report.to_json())
    testbase.assert_equal(payload["run_id"], "run-1")
    testbase.assert_equal(payload["host"], "host01")
    testbase.assert_equal(payload["description"], "desc")
    testbase.assert_equal(payload["overall_status"], "success")
    testbase.assert_equal(len(payload["actions"]), 1)
    testbase.assert_equal(payload["actions"][0]["feature"], "package-management")
    return


def test_write_report_dry_run_does_not_create_file():
    """
        write_report() with dry_run=True returns success without writing.
    """
    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, "report.json")
    report = MaintenanceReport(
        run_id="run-1", host="host01",
        config=MaintenanceReportConfig(dry_run=True),
    )
    result = report.write_report(path, "json")
    testbase.assert_true(result.success)
    testbase.assert_true(result.dry_run)
    testbase.assert_false(os.path.exists(path))
    return


def test_write_report_writes_json_to_disk():
    """
        write_report() writes the JSON payload to disk when not in dry-run.
    """
    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, "report.json")
    report = MaintenanceReport(run_id="run-1", host="host01")
    report.add_action(ActionRecord(
        feature="f", operation="op", status=ActionStatus.SUCCESS,
    ))
    result = report.write_report(path, "json")
    testbase.assert_true(result.success)
    testbase.assert_true(os.path.exists(path))
    with open(path, "r", encoding="utf-8") as infile:
        payload = json.load(infile)
    testbase.assert_equal(payload["run_id"], "run-1")
    return


def test_actions_by_feature_filters_records():
    """
        actions_by_feature() returns actions whose feature matches.
    """
    report = MaintenanceReport(run_id="run-1", host="host01")
    report.add_action(ActionRecord(feature="a", operation="op1", status=ActionStatus.SUCCESS))
    report.add_action(ActionRecord(feature="b", operation="op2", status=ActionStatus.SUCCESS))
    testbase.assert_equal(len(report.actions_by_feature("a")), 1)
    testbase.assert_equal(report.actions_by_feature("b")[0].operation, "op2")
    return
