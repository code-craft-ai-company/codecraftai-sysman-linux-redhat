# ccai.sysman.linux.redhat.runners.models
#
# Data models exchanged between command runners, aspects, and features.

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class SSHErrorType(Enum):
    """
        Classification of SSH-related errors so that reliability metrics and
        callers can distinguish between distinct failure modes.
    """
    NETWORK_ERROR = "network_error"
    AUTH_ERROR = "auth_error"
    HOST_KEY_ERROR = "host_key_error"
    TIMEOUT_ERROR = "timeout_error"
    CHANNEL_ERROR = "channel_error"
    UNKNOWN_ERROR = "unknown_error"


@dataclass
class RunResult:
    """
        The result of a single command execution returned by any runner.

        :param exit_code: Process exit code.  Negative values indicate abnormal termination.
        :param stdout: Captured standard output.
        :param stderr: Captured standard error.
        :param command: The argument vector that was executed.
        :param timed_out: True if the command was terminated by a timeout.
        :param dry_run: True if the result is synthetic because dry-run was requested.
        :param duration_ms: Wall-clock execution time in milliseconds.
        :param retry_count: Number of retry attempts made before success/failure.
        :param transport: Identifier of the transport used: "local" or "ssh".
    """

    exit_code: int
    stdout: str
    stderr: str
    command: list[str]
    timed_out: bool = False
    dry_run: bool = False
    duration_ms: Optional[float] = None
    retry_count: int = 0
    transport: str = "local"

    @property
    def success(self) -> bool:
        """
            Whether the command completed successfully.
        """
        rtnval = (self.exit_code == 0) and (not self.timed_out)
        return rtnval


@dataclass
class SSHReliabilityMetrics:
    """
        Cumulative reliability metrics for an SSHCommandRunner instance.

        :param success_count: Number of successful command executions.
        :param failure_count: Number of failed command executions (non-zero exit).
        :param timeout_count: Number of command executions that exceeded their timeout.
        :param connection_error_count: Number of connection-level errors encountered.
        :param retry_count: Number of connection retries attempted.
        :param total_duration_ms: Sum of all execution durations.
        :param min_duration_ms: Minimum observed execution duration.
        :param max_duration_ms: Maximum observed execution duration.
        :param avg_duration_ms: Average execution duration over recorded attempts.
        :param last_error: String representation of the most recent error.
        :param last_error_type: Classification of the most recent error.
    """

    success_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    connection_error_count: int = 0
    retry_count: int = 0
    total_duration_ms: float = 0.0
    min_duration_ms: Optional[float] = None
    max_duration_ms: Optional[float] = None
    avg_duration_ms: Optional[float] = None
    last_error: Optional[str] = None
    last_error_type: Optional[SSHErrorType] = None


@dataclass
class ExecutionContext:
    """
        Context object passed to execution aspects describing an in-flight command.

        :param args: The argument vector to execute.
        :param elevated: Whether the command should run with elevated privileges.
        :param cwd: Working directory for the command.
        :param env: Environment variable overrides for the command.
        :param timeout: Timeout in seconds; None means no timeout.
        :param transport: Identifier of the transport used ("local" or "ssh").
        :param dry_run: Whether this execution is in dry-run mode.
        :param metadata: Free-form dictionary for aspects to store correlation data.
    """

    args: list[str]
    elevated: bool
    cwd: Optional[str]
    env: Optional[dict[str, str]]
    timeout: Optional[float]
    transport: str
    dry_run: bool
    metadata: dict[str, Any] = field(default_factory=dict)
