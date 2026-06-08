# ccai.sysman.linux.redhat.featurebase
#
# Shared base classes for every feature manager in this package.
# Collapses the repeated <Feature>ActionStatus enum, <Feature>Config
# dataclass, <Feature>ActionResult dataclass, and _dispatch() helper
# pattern that every Layer 3 manager currently spells out locally.

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Optional

from ccai.sysman.linux.redhat.runners.base import ICommandRunner
from ccai.sysman.linux.redhat.runners.models import RunResult

if TYPE_CHECKING:
    from ccai.sysman.linux.redhat.maintenancereporting import (
        MaintenanceReport,
    )


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
        ``*ActionResult`` dataclass subclasses this and adds
        feature-specific fields (path, name, archive_path, ...).
    """
    status: ActionStatus
    detail: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


class BaseFeatureManager:
    """
        Shared base for every feature manager.

        Subclasses override the class variables ``result_class`` and
        (rarely) ``status_class`` to plug in feature-specific result
        and status types.  Methods that need to populate extra
        result fields either pass them through as ``**extra`` to
        ``_dispatch`` / ``_wrap_result``, or override ``_wrap_result``
        to inject them explicitly.

        An optional ``report: MaintenanceReport`` may be injected at
        construction time; when present, every ``_dispatch`` call
        appends an :class:`ActionRecord` to the report automatically.
        This is the B.2 auto-wired-reporting hook.
    """

    # --- Subclass configuration knobs ---------------------------------
    # Feature-specific result dataclass.  MUST subclass
    # :class:`FeatureActionResult` so the standard fields are accepted
    # as keyword arguments.
    result_class: type = FeatureActionResult

    # Feature-specific status enum.  Defaults to :class:`ActionStatus`;
    # subclasses override only if they extend the base enum.
    status_class: type = ActionStatus

    # Feature name used when auto-emitting :class:`ActionRecord` into
    # an injected :class:`MaintenanceReport`.  Subclasses set this to
    # the canonical spec-folder name (without the layer prefix), e.g.
    # ``"audit-management"``.  When None, auto-reporting is disabled
    # even if a ``report`` instance was injected.
    feature_name: Optional[str] = None

    # ------------------------------------------------------------------

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[FeatureConfig] = None,
        *,
        report: "Optional[MaintenanceReport]" = None,
    ) -> None:
        """
            :param runner: The command runner used by every mutating
                           operation.
            :param config: Manager configuration.  Defaults to the base
                           :class:`FeatureConfig` when omitted.
            :param report: Optional :class:`MaintenanceReport` to
                           receive an :class:`ActionRecord` entry for
                           every ``_dispatch`` call.
        """
        if config is None:
            config = FeatureConfig()

        self._runner: ICommandRunner = runner
        self._config: FeatureConfig = config
        self._report: "Optional[MaintenanceReport]" = report
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

    @property
    def report(self) -> "Optional[MaintenanceReport]":
        """
            The injected :class:`MaintenanceReport`, or None.
        """
        return self._report

    def _dispatch(
        self,
        *,
        command: list[str],
        detail: Optional[str] = None,
        stdin: Optional[str] = None,
        operation: Optional[str] = None,
        **extra,
    ) -> FeatureActionResult:
        """
            Dispatch a mutating command through the runner, honouring
            dry-run, map the result to the feature's result type, and
            optionally emit an :class:`ActionRecord` when a
            :class:`MaintenanceReport` is wired in.

            :param command: Argument vector for the runner.
            :param detail: Optional human-readable description.
            :param stdin: Optional stdin payload forwarded to the runner.
            :param operation: Operation name written into the emitted
                              :class:`ActionRecord`.  Defaults to
                              ``"unknown"`` when a report is wired in
                              and the caller did not supply one.
            :param extra: Extra keyword arguments forwarded to
                          :meth:`_wrap_result` — features that carry
                          additional result fields (archive_path,
                          mount, etc.) pass them here.
        """
        if self._config.dry_run:
            dry_result = self._wrap_result(
                status=self.status_class.DRY_RUN,
                detail=detail,
                command=list(command),
                **extra,
            )
            self._emit_record(result=dry_result, operation=operation)
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
            **extra,
        )
        self._emit_record(result=final_result, operation=operation)
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
        **extra,
    ) -> FeatureActionResult:
        """
            Construct the feature-specific result.  The default
            instantiates ``self.result_class`` with every passed
            field; subclasses whose result dataclass does not accept
            a given key override this method to filter or transform.
        """
        kwargs = {
            "status": status,
            "detail": detail,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "command": command,
        }
        kwargs.update(extra)
        rtnval = self.result_class(**kwargs)
        return rtnval

    def _emit_record(
        self,
        *,
        result: FeatureActionResult,
        operation: Optional[str],
    ) -> None:
        """
            Append an :class:`ActionRecord` to the injected report
            when one is configured.  No-op otherwise.
        """
        if self._report is None:
            return
        if self.feature_name is None:
            return

        # Lazy import to avoid a circular import at module-load time.
        from ccai.sysman.linux.redhat.maintenancereporting import (
            ActionRecord,
            ActionStatus as ReportActionStatus,
        )

        # Map our local ActionStatus value onto the report's enum by
        # string value — robust to subclass extensions.
        report_status = ReportActionStatus(result.status.value)

        record = ActionRecord(
            feature=self.feature_name,
            operation=(operation if operation is not None else "unknown"),
            status=report_status,
            detail=result.detail,
            error=result.stderr if result.status == self.status_class.FAILED else None,
        )
        self._report.add_action(record)
        return


class SanitisedDispatchMixin:
    """
        Opt-in mixin for features whose commands may be dispatched with
        secret bytes on ``stdin`` (passphrases, tokens, ...).  When
        mixed in before :class:`BaseFeatureManager`, ``_dispatch``
        forces the recorded ``command`` field back to the caller-
        supplied argv instead of ``run_result.command``.
    """

    def _dispatch(
        self,
        *,
        command: list[str],
        detail: Optional[str] = None,
        stdin: Optional[str] = None,
        operation: Optional[str] = None,
        **extra,
    ) -> FeatureActionResult:
        if self._config.dry_run:
            dry_result = self._wrap_result(
                status=self.status_class.DRY_RUN,
                detail=detail,
                command=list(command),
                **extra,
            )
            self._emit_record(result=dry_result, operation=operation)
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
            command=list(command),        # caller-supplied argv; never run_result.command
            **extra,
        )
        self._emit_record(result=final_result, operation=operation)
        return final_result


def result_from_run(
    *,
    run_result: RunResult,
    detail: Optional[str] = None,
) -> FeatureActionResult:
    """
        Utility: map an existing :class:`RunResult` to a
        :class:`FeatureActionResult` without dispatching.
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
