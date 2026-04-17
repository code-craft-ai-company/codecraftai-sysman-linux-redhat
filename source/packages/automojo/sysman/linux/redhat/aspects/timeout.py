# automojo.sysman.linux.redhat.aspects.timeout
#
# Execution aspect that enforces a default timeout on all commands.

from dataclasses import dataclass

from automojo.sysman.linux.redhat.runners.models import ExecutionContext, RunResult


@dataclass
class TimeoutAspectConfig:
    """
        Configuration for :class:`TimeoutAspect`.

        :param default_timeout: Default timeout in seconds applied to commands
                                that do not set a timeout of their own.
        :param allow_override: When True, command-supplied timeouts are kept
                               as-is.  When False, the default timeout always
                               overrides the command's timeout.
    """

    default_timeout: float
    allow_override: bool = True


class TimeoutAspect:
    """
        Execution aspect that ensures each command has a timeout value set
        on its :class:`ExecutionContext`.
    """

    def __init__(self, config: TimeoutAspectConfig) -> None:
        """
            :param config: Timeout configuration.
        """
        self._config: TimeoutAspectConfig = config
        self._enabled: bool = True
        return

    @property
    def enabled(self) -> bool:
        """
            Whether this aspect is active.
        """
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value
        return

    @property
    def config(self) -> TimeoutAspectConfig:
        """
            The active :class:`TimeoutAspectConfig`.
        """
        return self._config

    def before_execute(self, context: ExecutionContext) -> None:
        """
            Apply the default timeout to the context when one is not set, or
            force the default when override is disallowed.
        """
        if self._config.allow_override:
            if context.timeout is None:
                context.timeout = self._config.default_timeout
        else:
            context.timeout = self._config.default_timeout
        return

    def after_execute(
        self,
        context: ExecutionContext,
        result: RunResult,
    ) -> RunResult:
        """
            No-op after execution.  Timeout enforcement happens in the runner.
        """
        return result
