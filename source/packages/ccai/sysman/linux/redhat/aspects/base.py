# ccai.sysman.linux.redhat.aspects.base
#
# Base protocol for execution aspects.

from typing import Protocol, runtime_checkable

from ccai.sysman.linux.redhat.runners.models import ExecutionContext, RunResult


@runtime_checkable
class IExecutionAspect(Protocol):
    """
        Protocol for aspects that wrap command execution.  Aspects are invoked
        by runners before and after each command execution.  The 'before_execute'
        phase runs in registration order; the 'after_execute' phase runs in
        reverse registration order.
    """

    def before_execute(self, context: ExecutionContext) -> None:
        """
            Invoked before a command is executed.  May inspect and/or mutate the
            provided context.  Raising an exception aborts the execution.

            :param context: The execution context for the pending command.
        """

    def after_execute(
        self,
        context: ExecutionContext,
        result: RunResult,
    ) -> RunResult:
        """
            Invoked after a command has executed.  May transform the result
            before returning it to the caller.

            :param context: The execution context for the completed command.
            :param result: The result returned by the runner.

            :returns: The (possibly modified) :class:`RunResult`.
        """

    @property
    def enabled(self) -> bool:
        """
            Whether this aspect is currently enabled.  Disabled aspects are
            skipped by the runner.
        """
