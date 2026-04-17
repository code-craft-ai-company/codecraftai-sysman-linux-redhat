# automojo.sysman.linux.redhat.storage.base
#
# Protocol and data models for command-log storage backends.

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from automojo.sysman.linux.redhat.runners.models import RunResult


@dataclass
class CommandLogEntry:
    """
        Record of a command execution that will be written to a storage backend
        by the :class:`PersistenceAspect`.

        :param entry_id: Backend-assigned identifier for this entry.
        :param command: The argument vector that was executed.
        :param cwd: Working directory for the command.
        :param env: Environment variables supplied to the command.
        :param timestamp_start: Unix epoch time (seconds) when execution began.
        :param timestamp_end: Unix epoch time (seconds) when execution ended.
        :param transport: Identifier of the transport used ("local" or "ssh").
        :param elevated: Whether the command was run with elevated privileges.
        :param exit_code: Exit code recorded after execution.
        :param stdout: Captured standard output (if logging enabled).
        :param stderr: Captured standard error (if logging enabled).
        :param duration_ms: Total execution time in milliseconds.
        :param timed_out: True if the command hit a timeout.
        :param dry_run: True if the command was a dry-run.
        :param retry_count: Number of retries performed.
    """

    entry_id: str
    command: list[str]
    cwd: Optional[str]
    env: Optional[dict[str, str]]
    timestamp_start: float
    transport: str
    elevated: bool
    timestamp_end: Optional[float] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    duration_ms: Optional[float] = None
    timed_out: bool = False
    dry_run: bool = False
    retry_count: int = 0
    extra: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class ICommandStorage(Protocol):
    """
        Protocol for storage backends that log command executions.
    """

    def log_command(self, entry: CommandLogEntry) -> str:
        """
            Persist a new log entry, returning its identifier.
        """
        ...

    def update_command(self, entry_id: str, result: RunResult) -> None:
        """
            Update a previously logged entry with the final execution result.
        """
        ...
