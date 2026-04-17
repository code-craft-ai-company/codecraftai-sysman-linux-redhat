# automojo.sysman.linux.redhat.logmanagement
#
# LogManager wraps journalctl, log file reads, and logrotate through the
# ICommandRunner abstraction.

import os
import re

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.runners.base import ICommandRunner


class LogActionStatus(Enum):
    """
        Outcome status for log management actions.
    """
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class LogManagerConfig:
    """
        Configuration for :class:`LogManager`.

        :param dry_run: When True, no side-effectful commands execute.
        :param elevated: When True, write commands run elevated.
    """
    dry_run: bool = False
    elevated: bool = False


@dataclass
class JournalQueryParams:
    """
        Filter parameters for a journal query.
    """
    unit: Optional[str] = None
    since: Optional[str] = None
    until: Optional[str] = None
    priority: Optional[str] = None
    lines: Optional[int] = None
    grep: Optional[str] = None
    output_format: Optional[str] = None


@dataclass
class JournalQueryResult:
    """
        Outcome of a journal query.
    """
    success: bool
    lines: list[str]
    stderr: Optional[str]
    command: list[str]


@dataclass
class JournalDiskUsage:
    """
        Disk usage statistics for the journald archive.
    """
    size_human: str
    size_bytes: int


@dataclass
class LogActionResult:
    """
        Outcome of a log management action.
    """
    status: LogActionStatus
    detail: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


@dataclass
class LogFileResult:
    """
        Outcome of reading or searching a log file.
    """
    success: bool
    lines: list[str]
    stderr: Optional[str]
    command: list[str]


@dataclass
class LogrotateConfig:
    """
        Specification for a logrotate drop-in file.
    """
    log_path: str
    rotate: int = 7
    frequency: str = "daily"
    compress: bool = True
    missingok: bool = True
    notifempty: bool = True
    copytruncate: bool = False
    postrotate: Optional[str] = None


@dataclass
class LogrotateDropInState:
    """
        Current state of a logrotate drop-in file.
    """
    name: str
    exists: bool
    content: Optional[str] = None


class LogManagementError(Exception):
    """
        Raised when a non-optional log management query fails.
    """

    def __init__(
        self,
        message: str,
        *,
        exit_code: Optional[int] = None,
        stderr: Optional[str] = None,
    ) -> None:
        self.exit_code = exit_code
        self.stderr = stderr
        err_msg_lines = [message]
        if exit_code is not None:
            err_msg_lines.append("    EXIT_CODE: {}".format(exit_code))
        if stderr is not None:
            err_msg_lines.append("    STDERR: {}".format(stderr))
        err_msg = os.linesep.join(err_msg_lines)
        super().__init__(err_msg)
        return


_SIZE_BYTES_PATTERN = re.compile(r"(\d+)\s*(B|K|M|G|T)?", re.IGNORECASE)

_SIZE_MULTIPLIERS = {
    "b": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4,
}


class LogManager:
    """
        Facade for log management across journalctl, log files, and logrotate.
    """

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[LogManagerConfig] = None,
    ) -> None:
        """
            :param runner: Command runner.
            :param config: Manager configuration.
        """
        if config is None:
            config = LogManagerConfig()
        self._runner: ICommandRunner = runner
        self._config: LogManagerConfig = config
        return

    @property
    def config(self) -> LogManagerConfig:
        """
            The active :class:`LogManagerConfig`.
        """
        return self._config

    def query_journal(self, params: JournalQueryParams) -> JournalQueryResult:
        """
            Query the systemd journal with the given filters.
        """
        command = ["journalctl", "--no-pager"]
        if params.unit is not None:
            command.append("--unit={}".format(params.unit))
        if params.since is not None:
            command.append("--since={}".format(params.since))
        if params.until is not None:
            command.append("--until={}".format(params.until))
        if params.priority is not None:
            command.append("--priority={}".format(params.priority))
        if params.lines is not None:
            command.append("--lines={}".format(params.lines))
        if params.grep is not None:
            command.append("--grep={}".format(params.grep))
        if params.output_format is not None:
            command.append("--output={}".format(params.output_format))

        run_result = self._runner.run(command, elevated=False)
        success = run_result.exit_code == 0
        lines = run_result.stdout.splitlines() if success else []
        result = JournalQueryResult(
            success=success,
            lines=lines,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
        return result

    def journal_disk_usage(self) -> JournalDiskUsage:
        """
            Report the disk usage of the journal archive.
        """
        command = ["journalctl", "--disk-usage", "--no-pager"]
        run_result = self._runner.run(command, elevated=False)
        if run_result.exit_code != 0:
            raise LogManagementError(
                "journalctl --disk-usage failed",
                exit_code=run_result.exit_code,
                stderr=run_result.stderr,
            )
        usage = _parse_disk_usage(stdout=run_result.stdout)
        return usage

    def vacuum_by_size(self, size: str) -> LogActionResult:
        """
            Vacuum the journal archive down to ``size``.
        """
        command = ["journalctl", "--vacuum-size={}".format(size)]
        return self._dispatch_mutation(command=command, detail=size)

    def vacuum_by_time(self, period: str) -> LogActionResult:
        """
            Vacuum the journal archive down to ``period``.
        """
        command = ["journalctl", "--vacuum-time={}".format(period)]
        return self._dispatch_mutation(command=command, detail=period)

    def read_log_file(self, path: str, lines: int) -> LogFileResult:
        """
            Read the last ``lines`` from ``path`` using ``tail``.
        """
        command = ["tail", "--lines={}".format(lines), path]
        run_result = self._runner.run(command, elevated=False)
        success = run_result.exit_code == 0
        body_lines = run_result.stdout.splitlines() if success else []
        result = LogFileResult(
            success=success,
            lines=body_lines,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
        return result

    def search_log_file(
        self,
        path: str,
        pattern: str,
        *,
        context_lines: int = 0,
        max_matches: Optional[int] = None,
    ) -> LogFileResult:
        """
            Search ``path`` using ``grep --extended-regexp``.
        """
        command = ["grep", "--extended-regexp"]
        if context_lines > 0:
            command.append("--context={}".format(context_lines))
        if max_matches is not None:
            command.append("--max-count={}".format(max_matches))
        command.append(pattern)
        command.append(path)
        run_result = self._runner.run(command, elevated=False)
        success = run_result.exit_code in (0, 1)
        body_lines = run_result.stdout.splitlines() if success else []
        result = LogFileResult(
            success=success,
            lines=body_lines,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
        return result

    def rotate_log(self, config_path: str) -> LogActionResult:
        """
            Run ``logrotate`` against ``config_path``.
        """
        if self._config.dry_run:
            command = ["logrotate", "--debug", config_path]
            run_result = self._runner.run(command, elevated=self._config.elevated)
            return LogActionResult(
                status=LogActionStatus.DRY_RUN,
                detail=config_path,
                exit_code=run_result.exit_code,
                stdout=run_result.stdout,
                stderr=run_result.stderr,
                command=list(run_result.command),
            )
        command = ["logrotate", "--force", config_path]
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = LogActionStatus.SUCCESS if run_result.exit_code == 0 else LogActionStatus.FAILED
        result = LogActionResult(
            status=status,
            detail=config_path,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
        return result

    def write_logrotate_dropin(
        self,
        name: str,
        config: LogrotateConfig,
    ) -> LogActionResult:
        """
            Write a logrotate drop-in file under ``/etc/logrotate.d/``.
        """
        path = "/etc/logrotate.d/{}".format(name)
        content = _render_logrotate(config=config)
        current = self._read_file(path=path)
        if current == content:
            return LogActionResult(
                status=LogActionStatus.SKIPPED,
                detail=path,
            )
        if self._config.dry_run:
            return LogActionResult(
                status=LogActionStatus.DRY_RUN,
                detail=path,
                command=["tee", path],
            )
        run_result = self._runner.run(
            ["tee", path],
            elevated=self._config.elevated,
            stdin=content,
        )
        status = LogActionStatus.SUCCESS if run_result.exit_code == 0 else LogActionStatus.FAILED
        return LogActionResult(
            status=status,
            detail=path,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )

    def remove_logrotate_dropin(self, name: str) -> LogActionResult:
        """
            Delete a logrotate drop-in file.
        """
        path = "/etc/logrotate.d/{}".format(name)
        existing = self._read_file(path=path)
        if len(existing) == 0:
            return LogActionResult(
                status=LogActionStatus.SKIPPED,
                detail=path,
            )
        command = ["rm", "-f", path]
        if self._config.dry_run:
            return LogActionResult(
                status=LogActionStatus.DRY_RUN,
                detail=path,
                command=command,
            )
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = LogActionStatus.SUCCESS if run_result.exit_code == 0 else LogActionStatus.FAILED
        return LogActionResult(
            status=status,
            detail=path,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )

    def query_logrotate_dropin(self, name: str) -> LogrotateDropInState:
        """
            Return the current state of a logrotate drop-in file.
        """
        path = "/etc/logrotate.d/{}".format(name)
        content = self._read_file(path=path)
        exists = len(content) > 0
        state = LogrotateDropInState(
            name=name,
            exists=exists,
            content=content if exists else None,
        )
        return state

    def _dispatch_mutation(
        self,
        *,
        command: list[str],
        detail: str,
    ) -> LogActionResult:
        """
            Dispatch a vacuum-style command honouring dry-run.
        """
        if self._config.dry_run:
            return LogActionResult(
                status=LogActionStatus.DRY_RUN,
                detail=detail,
                command=command,
            )
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = LogActionStatus.SUCCESS if run_result.exit_code == 0 else LogActionStatus.FAILED
        return LogActionResult(
            status=status,
            detail=detail,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )

    def _read_file(self, *, path: str) -> str:
        """
            Read a file via ``cat`` returning empty on missing.
        """
        run_result = self._runner.run(["cat", path], elevated=False)
        if run_result.exit_code != 0:
            return ""
        return run_result.stdout


def _parse_disk_usage(*, stdout: str) -> JournalDiskUsage:
    """
        Parse journalctl --disk-usage output (``Archived and active journals take up 1.2G in the file system.``).
    """
    size_human = ""
    size_bytes = 0
    text = stdout.strip()
    marker = "take up "
    idx = text.find(marker)
    if idx >= 0:
        remainder = text[idx + len(marker):]
        size_token = remainder.split()[0] if len(remainder.split()) > 0 else ""
        size_human = size_token
        size_bytes = _parse_size_token(token=size_token)
    usage = JournalDiskUsage(size_human=size_human, size_bytes=size_bytes)
    return usage


def _parse_size_token(*, token: str) -> int:
    """
        Parse ``1.2G`` / ``512M`` / ``123`` into an approximate byte count.
    """
    match = _SIZE_BYTES_PATTERN.match(token)
    if match is None:
        return 0
    number_str = match.group(1)
    unit = (match.group(2) or "B").lower()
    try:
        number = int(number_str)
    except ValueError:
        return 0
    multiplier = _SIZE_MULTIPLIERS.get(unit, 1)
    return number * multiplier


def _render_logrotate(*, config: LogrotateConfig) -> str:
    """
        Render a :class:`LogrotateConfig` into a logrotate drop-in body.
    """
    lines: list[str] = []
    lines.append("{} {{".format(config.log_path))
    lines.append("    {}".format(config.frequency))
    lines.append("    rotate {}".format(config.rotate))
    if config.compress:
        lines.append("    compress")
    if config.missingok:
        lines.append("    missingok")
    if config.notifempty:
        lines.append("    notifempty")
    if config.copytruncate:
        lines.append("    copytruncate")
    if config.postrotate is not None:
        lines.append("    postrotate")
        lines.append("        {}".format(config.postrotate))
        lines.append("    endscript")
    lines.append("}")
    body = "\n".join(lines) + "\n"
    return body
