# automojo.sysman.linux.redhat.auditmanagement
#
# AuditManager wraps auditctl, ausearch, and aureport through the
# ICommandRunner abstraction.

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.runners.base import ICommandRunner


class AuditActionStatus(Enum):
    """
        Outcome status for audit management actions.
    """
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class AuditManagerConfig:
    """
        Configuration for :class:`AuditManager`.

        :param dry_run: When True, no mutating commands execute.
        :param elevated: When True, audit commands run elevated.
        :param rules_dir: Directory containing audit rule drop-ins.
    """
    dry_run: bool = False
    elevated: bool = False
    rules_dir: str = "/etc/audit/rules.d"


@dataclass
class AuditActionResult:
    """
        Outcome of an audit management action.
    """
    status: AuditActionStatus
    detail: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


@dataclass
class AuditReport:
    """
        Aggregated report produced by aureport/ausearch.
    """
    lines: list[str] = field(default_factory=list)
    command: Optional[list[str]] = None


class AuditManager:
    """
        Facade for the Linux audit subsystem.
    """

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[AuditManagerConfig] = None,
    ) -> None:
        """
            :param runner: Command runner.
            :param config: Manager configuration.
        """
        if config is None:
            config = AuditManagerConfig()
        self._runner: ICommandRunner = runner
        self._config: AuditManagerConfig = config
        return

    @property
    def config(self) -> AuditManagerConfig:
        """
            The active :class:`AuditManagerConfig`.
        """
        return self._config

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
        return self._dispatch(command=command, detail=rule)

    def delete_rule(self, rule: str) -> AuditActionResult:
        """
            Delete a single audit rule at runtime via ``auditctl -d <rule>``.
        """
        tokens = rule.split()
        command = ["auditctl"] + tokens
        if len(tokens) > 0 and tokens[0] == "-a":
            command[1] = "-d"
        return self._dispatch(command=command, detail=rule)

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
        command = ["tee", path]
        if self._config.dry_run:
            return AuditActionResult(
                status=AuditActionStatus.DRY_RUN,
                detail=path, command=command,
            )
        run_result = self._runner.run(
            command, elevated=self._config.elevated, stdin=content,
        )
        status = AuditActionStatus.SUCCESS if run_result.exit_code == 0 else AuditActionStatus.FAILED
        return AuditActionResult(
            status=status, detail=path,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
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

    def _dispatch(
        self,
        *,
        command: list[str],
        detail: str,
    ) -> AuditActionResult:
        """
            Dispatch a mutating audit command honouring dry-run.
        """
        if self._config.dry_run:
            return AuditActionResult(
                status=AuditActionStatus.DRY_RUN,
                detail=detail, command=command,
            )
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = AuditActionStatus.SUCCESS if run_result.exit_code == 0 else AuditActionStatus.FAILED
        return AuditActionResult(
            status=status, detail=detail,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
