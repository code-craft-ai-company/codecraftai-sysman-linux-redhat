# automojo.sysman.linux.redhat.runners.base
#
# Protocol definitions for the command runner abstraction.

from typing import TYPE_CHECKING, Callable, Optional, Protocol, runtime_checkable

from automojo.sysman.linux.redhat.runners.models import RunResult, SSHReliabilityMetrics

if TYPE_CHECKING:
    from automojo.sysman.linux.redhat.aspects.base import IExecutionAspect


@runtime_checkable
class ICommandRunner(Protocol):
    """
        Transport-agnostic interface for executing commands.  Implementations
        are free to execute locally, remotely over SSH, or against a mock
        backend for tests.
    """

    def run(
        self,
        args: list[str],
        *,
        elevated: bool = False,
        cwd: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
        timeout: Optional[float] = None,
        on_output: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        capture_output: bool = True,
        stdin: Optional[str] = None,
    ) -> RunResult:
        """
            Execute a command and return a :class:`RunResult`.
        """
        ...

    def add_aspect(self, aspect: "IExecutionAspect") -> None:
        """
            Register an execution aspect on this runner.
        """
        ...

    def remove_aspect(self, aspect: "IExecutionAspect") -> None:
        """
            Unregister a previously registered execution aspect.
        """
        ...

    def get_metrics(self) -> Optional[SSHReliabilityMetrics]:
        """
            Return reliability metrics for this runner, or None if none are tracked.
        """
        ...
