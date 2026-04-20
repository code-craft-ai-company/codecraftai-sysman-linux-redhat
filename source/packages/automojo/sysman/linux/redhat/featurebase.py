# automojo.sysman.linux.redhat.featurebase
#
# Shared base classes for every feature manager in this package.
# Collapses the repeated <Feature>ActionStatus enum, <Feature>Config
# dataclass, <Feature>ActionResult dataclass, and _dispatch() helper
# pattern that every Layer 3 manager currently spells out locally.
#
# Existing features retain their feature-specific dataclasses and can
# migrate to the base incrementally; nothing in this module forces an
# immediate rewrite.  A migrated feature re-exports the base types
# (or sub-classes them) rather than defining its own.

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.runners.base import ICommandRunner
from automojo.sysman.linux.redhat.runners.models import RunResult


class ActionStatus(Enum):
    """
        Canonical outcome-status enum for every feature manager in
        this package.
    """
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class FeatureConfig:
    """
        Canonical configuration base for every feature manager.

        :param dry_run: When True, no mutating command is dispatched;
                        the would-have-been command is still recorded
                        on the returned :class:`FeatureActionResult`.
        :param elevated: When True, mutating commands are dispatched
                         with ``elevated=True`` to the runner.
    """
    dry_run: bool = False
    elevated: bool = False


@dataclass
class FeatureActionResult:
    """
        Canonical per-operation result shape.  Every feature's own
        ``*ActionResult`` dataclass either IS this type or carries
        these fields plus a small feature-specific set.

        :param status: The outcome status.
        :param detail: Free-form human-readable description.  Features
                       commonly use this for the target path, the
                       service name, or a short error summary.
        :param exit_code: Runner exit code when a command was executed.
        :param stdout: Captured stdout from the runner.
        :param stderr: Captured stderr from the runner.
        :param command: The argument vector as dispatched (or
                        would-have-been dispatched in dry-run).
                        Sensitive payloads such as passphrases are
                        NOT included here; features routing secrets
                        on stdin sanitise this field on the return
                        path.
    """
    status: ActionStatus
    detail: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


class BaseFeatureManager:
    """
        Shared base for every feature manager.  Provides:

        - Standard ``__init__(runner, config=None)`` shape
        - Read-only ``config`` property
        - ``_dispatch`` helper implementing the common
          dry-run-short-circuit + runner-result mapping pattern

        Subclasses MUST declare their own feature-specific config /
        result types (typically by sub-classing :class:`FeatureConfig`
        and :class:`FeatureActionResult`) when they need extra fields.
        Otherwise they can reuse these directly.

        The default ``_dispatch`` returns :class:`FeatureActionResult`;
        subclasses that return a richer type override ``_wrap_result``
        (below) to build their own result object from the same common
        inputs.
    """

    # --- Subclass configuration knobs ---------------------------------
    # Feature-specific status enum class.  Subclasses override ONLY when
    # they extend :class:`ActionStatus` with extra members; otherwise
    # the default is fine.
    status_class: type = ActionStatus

    # ------------------------------------------------------------------

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[FeatureConfig] = None,
    ) -> None:
        """
            :param runner: The command runner used by every mutating
                           operation.
            :param config: Manager configuration.  Defaults to the base
                           :class:`FeatureConfig` when omitted; most
                           subclasses pass their own config type.
        """
        if config is None:
            config = FeatureConfig()

        self._runner: ICommandRunner = runner
        self._config: FeatureConfig = config
        return

    @property
    def config(self) -> FeatureConfig:
        """
            The active configuration.
        """
        return self._config

    @property
    def runner(self) -> ICommandRunner:
        """
            The injected command runner.
        """
        return self._runner

    def _dispatch(
        self,
        *,
        command: list[str],
        detail: Optional[str] = None,
        stdin: Optional[str] = None,
    ) -> FeatureActionResult:
        """
            Dispatch a mutating command through the runner, honouring
            dry-run, and map the resulting :class:`RunResult` to a
            :class:`FeatureActionResult`.

            :param command: Argument vector for the runner.
            :param detail: Optional description stored on the returned
                           result.
            :param stdin: Optional stdin payload forwarded to the
                          runner.  Senders that carry secrets on stdin
                          MUST also override the returned result's
                          ``command`` field to sanitise it — see the
                          SanitisedDispatchMixin below.

            :returns: A :class:`FeatureActionResult` describing the
                      outcome.
        """
        if self._config.dry_run:
            dry_result = self._wrap_result(
                status=self.status_class.DRY_RUN,
                detail=detail,
                command=list(command),
            )
            return dry_result

        run_result = self._runner.run(
            list(command),
            elevated=self._config.elevated,
            stdin=stdin,
        )
        mapped_status = (
            self.status_class.SUCCESS
            if run_result.exit_code == 0
            else self.status_class.FAILED
        )
        final_result = self._wrap_result(
            status=mapped_status,
            detail=detail,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
        return final_result

    def _wrap_result(
        self,
        *,
        status: ActionStatus,
        detail: Optional[str] = None,
        exit_code: Optional[int] = None,
        stdout: Optional[str] = None,
        stderr: Optional[str] = None,
        command: Optional[list[str]] = None,
    ) -> FeatureActionResult:
        """
            Construct the feature-specific result type.  The default
            returns :class:`FeatureActionResult`; subclasses with a
            richer result type override this single method and keep
            using ``_dispatch`` as-is.
        """
        rtnval = FeatureActionResult(
            status=status,
            detail=detail,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            command=command,
        )
        return rtnval


class SanitisedDispatchMixin:
    """
        Opt-in mixin for features whose commands may be dispatched with
        secret bytes on ``stdin`` (passphrases, tokens, ...).  When
        mixed in before :class:`BaseFeatureManager`, ``_dispatch``
        forces the recorded ``command`` field back to the caller-
        supplied argv instead of ``run_result.command``.

        Runners already avoid echoing stdin bytes into ``command``, but
        this mixin documents the intent explicitly and protects against
        a future runner that chooses to include them.

        Example::

            class SambaServerManager(SanitisedDispatchMixin,
                                     BaseFeatureManager):
                ...
    """

    def _dispatch(
        self,
        *,
        command: list[str],
        detail: Optional[str] = None,
        stdin: Optional[str] = None,
    ) -> FeatureActionResult:
        if self._config.dry_run:
            dry_result = self._wrap_result(
                status=self.status_class.DRY_RUN,
                detail=detail,
                command=list(command),
            )
            return dry_result

        run_result = self._runner.run(
            list(command),
            elevated=self._config.elevated,
            stdin=stdin,
        )
        mapped_status = (
            self.status_class.SUCCESS
            if run_result.exit_code == 0
            else self.status_class.FAILED
        )
        final_result = self._wrap_result(
            status=mapped_status,
            detail=detail,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(command),        # caller-supplied argv, NOT run_result.command
        )
        return final_result


def result_from_run(
    *,
    run_result: RunResult,
    detail: Optional[str] = None,
) -> FeatureActionResult:
    """
        Utility: map an existing :class:`RunResult` to a
        :class:`FeatureActionResult` without dispatching.  Useful when
        a subclass method has already issued the runner call itself
        (e.g. reading a file via ``cat``) and wants to reuse the common
        result shape.
    """
    mapped_status = (
        ActionStatus.SUCCESS if run_result.exit_code == 0 else ActionStatus.FAILED
    )
    rtnval = FeatureActionResult(
        status=mapped_status,
        detail=detail,
        exit_code=run_result.exit_code,
        stdout=run_result.stdout,
        stderr=run_result.stderr,
        command=list(run_result.command),
    )
    return rtnval
