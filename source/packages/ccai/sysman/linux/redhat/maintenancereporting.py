# ccai.sysman.linux.redhat.maintenancereporting
#
# MaintenanceReport collects ActionRecord entries produced by the other
# sysman features and serialises them to JSON or plain text.  The module
# performs no command execution and has no runner dependency.

import json
import os
import socket
import uuid

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class ActionStatus(Enum):
    """
        Outcome categories recognised by the report writer.
    """
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class ActionRecord:
    """
        One recorded action within a maintenance report.

        :param feature: Feature name (e.g. ``"package-management"``).
        :param operation: Short name of the operation performed.
        :param status: Outcome status.
        :param detail: Free-form human-readable description.
        :param error: Error message or captured stderr on failure.
        :param duration_seconds: Wall-clock execution time.
        :param timestamp: Event timestamp (default: construction time).
    """
    feature: str
    operation: str
    status: ActionStatus
    detail: Optional[str] = None
    error: Optional[str] = None
    duration_seconds: Optional[float] = None
    timestamp: Optional[datetime] = None

    def __post_init__(self) -> None:
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)
        return


@dataclass
class ReportSummary:
    """
        Counters tallied by status across a report.
    """
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    dry_run: int = 0


@dataclass
class ReportWriteResult:
    """
        Outcome of :meth:`MaintenanceReport.write_report`.
    """
    success: bool
    path: str
    dry_run: bool = False
    error: Optional[str] = None


@dataclass
class MaintenanceReportConfig:
    """
        Configuration for :class:`MaintenanceReport`.

        :param dry_run: When True, ``write_report`` returns without writing.
    """
    dry_run: bool = False


class MaintenanceReport:
    """
        Aggregator for action records emitted by sysman features.
    """

    def __init__(
        self,
        *,
        run_id: Optional[str] = None,
        host: Optional[str] = None,
        description: Optional[str] = None,
        config: Optional[MaintenanceReportConfig] = None,
    ) -> None:
        """
            :param run_id: Unique identifier for the run; generated if None.
            :param host: Hostname or system identifier; auto-discovered if None.
            :param description: Optional free-form description of the run.
            :param config: Reporting configuration.
        """
        if config is None:
            config = MaintenanceReportConfig()
        if run_id is None:
            run_id = str(uuid.uuid4())
        if host is None:
            try:
                host = socket.gethostname()
            except Exception:
                host = "unknown"

        self._config: MaintenanceReportConfig = config
        self._run_id: str = run_id
        self._host: str = host
        self._description: Optional[str] = description
        self._started_at: datetime = datetime.now(timezone.utc)
        self._finished_at: Optional[datetime] = None
        self._overall_status: Optional[ActionStatus] = None
        self._actions: list[ActionRecord] = []
        return

    @property
    def run_id(self) -> str:
        """
            Unique identifier for this run.
        """
        return self._run_id

    @property
    def host(self) -> str:
        """
            Hostname or system identifier.
        """
        return self._host

    @property
    def description(self) -> Optional[str]:
        """
            Optional description of the run's purpose.
        """
        return self._description

    @property
    def started_at(self) -> datetime:
        """
            UTC time when the report was created.
        """
        return self._started_at

    @property
    def finished_at(self) -> Optional[datetime]:
        """
            UTC time when :meth:`finalise` was last called.
        """
        return self._finished_at

    @property
    def overall_status(self) -> Optional[ActionStatus]:
        """
            Aggregated status; None until :meth:`finalise` is called.
        """
        return self._overall_status

    @property
    def actions(self) -> list[ActionRecord]:
        """
            Snapshot of recorded actions in insertion order.
        """
        snapshot = list(self._actions)
        return snapshot

    @property
    def config(self) -> MaintenanceReportConfig:
        """
            The active :class:`MaintenanceReportConfig`.
        """
        return self._config

    def add_action(self, record: ActionRecord) -> None:
        """
            Append an :class:`ActionRecord` to the report.
        """
        self._actions.append(record)
        return

    def finalise(self) -> None:
        """
            Set ``finished_at`` and compute ``overall_status``.
        """
        self._finished_at = datetime.now(timezone.utc)
        self._overall_status = _compute_overall_status(actions=self._actions)
        return

    def summary_counts(self) -> ReportSummary:
        """
            Return counters tallied by status across all actions.
        """
        summary = ReportSummary(total=len(self._actions))
        for action in self._actions:
            if action.status == ActionStatus.SUCCESS:
                summary.succeeded = summary.succeeded + 1
            elif action.status == ActionStatus.FAILED:
                summary.failed = summary.failed + 1
            elif action.status == ActionStatus.SKIPPED:
                summary.skipped = summary.skipped + 1
            elif action.status == ActionStatus.DRY_RUN:
                summary.dry_run = summary.dry_run + 1
        return summary

    def to_json(self, indent: Optional[int] = 2) -> str:
        """
            Serialise the report to a JSON string.
        """
        payload = _report_to_dict(report=self)
        rendered = json.dumps(payload, indent=indent, default=str)
        return rendered

    def to_text(self) -> str:
        """
            Serialise the report to a human-readable text string.
        """
        lines: list[str] = []
        lines.append("Maintenance Report")
        lines.append("  run_id: {}".format(self._run_id))
        lines.append("  host: {}".format(self._host))
        if self._description is not None:
            lines.append("  description: {}".format(self._description))
        lines.append("  started_at: {}".format(self._started_at.isoformat()))
        if self._finished_at is not None:
            lines.append("  finished_at: {}".format(self._finished_at.isoformat()))
        if self._overall_status is not None:
            lines.append("  overall_status: {}".format(self._overall_status.value))
        summary = self.summary_counts()
        lines.append(
            "  summary: total={} succeeded={} failed={} skipped={} dry_run={}".format(
                summary.total, summary.succeeded, summary.failed,
                summary.skipped, summary.dry_run,
            )
        )
        lines.append("Actions:")
        for idx, action in enumerate(self._actions):
            lines.append(
                "  [{:04d}] {} / {} -> {}".format(
                    idx, action.feature, action.operation, action.status.value,
                )
            )
            if action.detail is not None:
                lines.append("         detail: {}".format(action.detail))
            if action.error is not None:
                lines.append("         error: {}".format(action.error))
        body = "\n".join(lines) + "\n"
        return body

    def write_report(self, path: str, format: str) -> ReportWriteResult:
        """
            Serialise to ``format`` (``"json"`` or ``"text"``) and write to ``path``.
        """
        if self._config.dry_run:
            return ReportWriteResult(
                success=True, path=path, dry_run=True,
            )
        if format not in ("json", "text"):
            return ReportWriteResult(
                success=False, path=path,
                error="unsupported report format: {}".format(format),
            )
        try:
            if format == "json":
                body = self.to_json()
            else:
                body = self.to_text()
            parent = os.path.dirname(path)
            if len(parent) > 0:
                os.makedirs(parent, exist_ok=True)
            with open(path, "w", encoding="utf-8") as outfile:
                outfile.write(body)
        except Exception as ex:
            return ReportWriteResult(
                success=False, path=path, error=str(ex),
            )
        return ReportWriteResult(success=True, path=path)

    def actions_by_status(self, *statuses: ActionStatus) -> list[ActionRecord]:
        """
            Return actions whose status is in ``statuses``.
        """
        if len(statuses) == 0:
            return []
        target = set(statuses)
        result = [a for a in self._actions if a.status in target]
        return result

    def actions_by_feature(self, feature: str) -> list[ActionRecord]:
        """
            Return actions emitted by a specific feature.
        """
        result = [a for a in self._actions if a.feature == feature]
        return result


def _compute_overall_status(
    *,
    actions: list[ActionRecord],
) -> ActionStatus:
    """
        Aggregate individual action statuses into a single outcome.
    """
    if len(actions) == 0:
        return ActionStatus.SUCCESS
    any_failed = False
    all_skipped = True
    all_dry_run = True
    for action in actions:
        if action.status == ActionStatus.FAILED:
            any_failed = True
        if action.status != ActionStatus.SKIPPED:
            all_skipped = False
        if action.status != ActionStatus.DRY_RUN:
            all_dry_run = False
    if any_failed:
        return ActionStatus.FAILED
    if all_dry_run:
        return ActionStatus.DRY_RUN
    if all_skipped:
        return ActionStatus.SKIPPED
    return ActionStatus.SUCCESS


def _report_to_dict(*, report: MaintenanceReport) -> dict:
    """
        Produce a JSON-serialisable dict for a :class:`MaintenanceReport`.
    """
    payload = {
        "run_id": report.run_id,
        "host": report.host,
        "description": report.description,
        "started_at": report.started_at.isoformat(),
        "finished_at": report.finished_at.isoformat() if report.finished_at is not None else None,
        "overall_status": report.overall_status.value if report.overall_status is not None else None,
        "summary": {
            "total": report.summary_counts().total,
            "succeeded": report.summary_counts().succeeded,
            "failed": report.summary_counts().failed,
            "skipped": report.summary_counts().skipped,
            "dry_run": report.summary_counts().dry_run,
        },
        "actions": [],
    }
    for action in report.actions:
        payload["actions"].append({
            "feature": action.feature,
            "operation": action.operation,
            "status": action.status.value,
            "detail": action.detail,
            "error": action.error,
            "duration_seconds": action.duration_seconds,
            "timestamp": action.timestamp.isoformat() if action.timestamp is not None else None,
        })
    return payload
