# ccai.sysman.linux.redhat.auditmanagement
#
# AuditManager wraps auditctl, ausearch, and aureport through the
# ICommandRunner abstraction.

from dataclasses import dataclass, field
from typing import Optional

from ccai.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


# Backwards-compatible alias; older callers import AuditActionStatus.
AuditActionStatus = ActionStatus


@dataclass
class AuditManagerConfig(FeatureConfig):
    """
        Configuration for :class:`AuditManager`.

        Inherits ``dry_run`` and ``elevated`` from :class:`FeatureConfig`.

        :param rules_dir: Directory containing audit rule drop-ins.
    """
    rules_dir: str = "/etc/audit/rules.d"


@dataclass
class AuditActionResult(FeatureActionResult):
    """
        Outcome of an audit management action.  Inherits every field
        from :class:`FeatureActionResult` — no extras.
    """
    pass


@dataclass
class AuditReport:
    """
        Aggregated report produced by aureport/ausearch.
    """
    lines: list[str] = field(default_factory=list)
    command: Optional[list[str]] = None


class AuditManager(BaseFeatureManager):
    """
        Facade for the Linux audit subsystem.
    """

    result_class = AuditActionResult
    feature_name = "audit-management"

    def list_rules(self) -> list[str]:
        """
            List runtime rules via ``auditctl -l``.
        """
        run_result = self._runner.run(
            ["auditctl", "-l"], elevated=self._config.elevated,
        )
        if run_result.exit_code != 0:
            return []
        rules: list[str] = []
        for raw in run_result.stdout.splitlines():
            line = raw.strip()
            if len(line) > 0:
                rules.append(line)
        return rules

    def add_rule(self, rule: str) -> AuditActionResult:
        """
            Add a single audit rule at runtime via ``auditctl``.
        """
        command = ["auditctl"] + rule.split()
        return self._dispatch(command=command, detail=rule, operation="add_rule")

    def delete_rule(self, rule: str) -> AuditActionResult:
        """
            Delete a single audit rule at runtime via ``auditctl -d <rule>``.
        """
        tokens = rule.split()
        command = ["auditctl"] + tokens
        if len(tokens) > 0 and tokens[0] == "-a":
            command[1] = "-d"
        return self._dispatch(command=command, detail=rule, operation="delete_rule")

    def write_rules_file(
        self,
        name: str,
        rules: list[str],
    ) -> AuditActionResult:
        """
            Write a rules drop-in under ``<rules_dir>/<name>.rules``.
        """
        path = "{}/{}.rules".format(self._config.rules_dir, name)
        content = "\n".join(rules) + "\n"
        current = self._read(path=path)
        if current == content:
            return AuditActionResult(
                status=AuditActionStatus.SKIPPED, detail=path,
            )
        return self._dispatch(
            command=["tee", path],
            detail=path,
            stdin=content,
            operation="write_rules_file",
        )

    def search(
        self,
        *,
        key: Optional[str] = None,
        message_type: Optional[str] = None,
        start: Optional[str] = None,
    ) -> AuditReport:
        """
            Run ``ausearch`` with the given filters and return the raw lines.
        """
        command = ["ausearch"]
        if key is not None:
            command.extend(["-k", key])
        if message_type is not None:
            command.extend(["-m", message_type])
        if start is not None:
            command.extend(["-ts", start])
        run_result = self._runner.run(command, elevated=self._config.elevated)
        if run_result.exit_code != 0:
            return AuditReport(command=list(command))
        return AuditReport(
            lines=run_result.stdout.splitlines(),
            command=list(command),
        )

    def summary(self) -> AuditReport:
        """
            Run ``aureport --summary``.
        """
        run_result = self._runner.run(
            ["aureport", "--summary"], elevated=self._config.elevated,
        )
        return AuditReport(
            lines=run_result.stdout.splitlines() if run_result.exit_code == 0 else [],
            command=list(run_result.command),
        )

    def _read(self, *, path: str) -> str:
        """
            Read a file via ``cat`` returning empty on missing.
        """
        run_result = self._runner.run(["cat", path], elevated=False)
        if run_result.exit_code != 0:
            return ""
        return run_result.stdout
