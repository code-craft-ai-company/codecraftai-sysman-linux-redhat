# Tests for the shared feature base module.

from dataclasses import dataclass, field
from typing import Callable, Optional

from ccai import testbase

from ccai.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
    SanitisedDispatchMixin,
    result_from_run,
)
from ccai.sysman.linux.redhat.runners.models import RunResult


@dataclass
class _MockRunner:
    queued: list = field(default_factory=list)
    calls: list = field(default_factory=list)

    def run(
        self,
        args: list,
        *,
        elevated: bool = False,
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        timeout: Optional[float] = None,
        on_output: Optional[Callable] = None,
        on_error: Optional[Callable] = None,
        capture_output: bool = True,
        stdin: Optional[str] = None,
    ) -> RunResult:
        self.calls.append({"args": list(args), "elevated": elevated, "stdin": stdin})
        if len(self.queued) > 0:
            rtn = self.queued.pop(0)
        else:
            rtn = RunResult(
                exit_code=0, stdout="", stderr="", command=list(args)
            )
        return rtn

    def add_aspect(self, aspect) -> None: return
    def remove_aspect(self, aspect) -> None: return
    def get_metrics(self): return None


def test_dispatch_dry_run_short_circuits():
    """
        With dry_run=True, _dispatch returns DRY_RUN and never calls
        the runner.
    """
    runner = _MockRunner()
    manager = BaseFeatureManager(runner, FeatureConfig(dry_run=True))
    result = manager._dispatch(command=["ls"])
    testbase.assert_equal(result.status, ActionStatus.DRY_RUN)
    testbase.assert_equal(runner.calls, [])
    testbase.assert_equal(result.command, ["ls"])
    return


def test_dispatch_success_maps_zero_exit():
    """
        A zero exit code maps to SUCCESS and preserves runner fields.
    """
    runner = _MockRunner(queued=[
        RunResult(exit_code=0, stdout="ok", stderr="", command=["ls"])
    ])
    manager = BaseFeatureManager(runner, FeatureConfig())
    result = manager._dispatch(command=["ls"], detail="listing")
    testbase.assert_equal(result.status, ActionStatus.SUCCESS)
    testbase.assert_equal(result.exit_code, 0)
    testbase.assert_equal(result.stdout, "ok")
    testbase.assert_equal(result.detail, "listing")
    return


def test_dispatch_failure_maps_nonzero_exit():
    """
        A non-zero exit code maps to FAILED.
    """
    runner = _MockRunner(queued=[
        RunResult(exit_code=2, stdout="", stderr="bad", command=["x"])
    ])
    manager = BaseFeatureManager(runner, FeatureConfig())
    result = manager._dispatch(command=["x"])
    testbase.assert_equal(result.status, ActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 2)
    testbase.assert_equal(result.stderr, "bad")
    return


def test_dispatch_forwards_elevated_and_stdin():
    """
        config.elevated propagates to the runner; stdin too.
    """
    runner = _MockRunner()
    manager = BaseFeatureManager(runner, FeatureConfig(elevated=True))
    manager._dispatch(command=["tee", "/x"], stdin="payload")
    call = runner.calls[0]
    testbase.assert_true(call["elevated"])
    testbase.assert_equal(call["stdin"], "payload")
    return


def test_wrap_result_subclass_returns_custom_type():
    """
        Subclasses override _wrap_result to produce their own result
        type while reusing _dispatch.
    """
    @dataclass
    class CustomResult(FeatureActionResult):
        custom_field: str = ""

    class CustomManager(BaseFeatureManager):
        def _wrap_result(self, **kwargs) -> FeatureActionResult:
            rtnval = CustomResult(
                **kwargs,
                custom_field="tagged",
            )
            return rtnval

    runner = _MockRunner(queued=[
        RunResult(exit_code=0, stdout="", stderr="", command=["x"])
    ])
    manager = CustomManager(runner, FeatureConfig())
    result = manager._dispatch(command=["x"])
    testbase.assert_true(isinstance(result, CustomResult))
    testbase.assert_equal(result.custom_field, "tagged")
    return


def test_sanitised_dispatch_mixin_preserves_caller_argv():
    """
        SanitisedDispatchMixin forces the recorded command field to
        the caller-supplied argv, not whatever the runner returns.
    """
    runner = _MockRunner(queued=[
        RunResult(
            exit_code=0,
            stdout="",
            stderr="",
            command=["this", "is", "what", "runner", "returned"],
        )
    ])

    class SecretManager(SanitisedDispatchMixin, BaseFeatureManager):
        pass

    manager = SecretManager(runner, FeatureConfig())
    result = manager._dispatch(command=["secretly-sensitive"], stdin="secret-bytes")
    testbase.assert_equal(result.command, ["secretly-sensitive"])
    return


def test_result_from_run_maps_runresult_to_actionresult():
    """
        result_from_run builds a FeatureActionResult from an existing
        RunResult without dispatching.
    """
    run_result = RunResult(
        exit_code=0, stdout="a", stderr="b", command=["c"]
    )
    result = result_from_run(run_result=run_result, detail="hello")
    testbase.assert_equal(result.status, ActionStatus.SUCCESS)
    testbase.assert_equal(result.stdout, "a")
    testbase.assert_equal(result.stderr, "b")
    testbase.assert_equal(result.detail, "hello")
    return


def test_result_class_subclass_instantiated_by_default_wrap():
    """
        Setting result_class on a subclass causes _wrap_result to
        produce that class — no override required.
    """
    @dataclass
    class MyResult(FeatureActionResult):
        my_extra: str = ""

    class MyManager(BaseFeatureManager):
        result_class = MyResult

    runner = _MockRunner(queued=[
        RunResult(exit_code=0, stdout="", stderr="", command=["x"])
    ])
    manager = MyManager(runner, FeatureConfig())
    result = manager._dispatch(command=["x"], my_extra="carried")
    testbase.assert_true(isinstance(result, MyResult))
    testbase.assert_equal(result.my_extra, "carried")
    return


def test_report_autoemit_on_success():
    """
        When a MaintenanceReport is injected, _dispatch emits an
        ActionRecord with matching feature / operation / status.
    """
    from ccai.sysman.linux.redhat.maintenancereporting import (
        MaintenanceReport,
        ActionStatus as ReportStatus,
    )

    class MyManager(BaseFeatureManager):
        feature_name = "sample-feature"

    report = MaintenanceReport(run_id="r1", host="h1")
    runner = _MockRunner(queued=[
        RunResult(exit_code=0, stdout="", stderr="", command=["x"])
    ])
    manager = MyManager(runner, FeatureConfig(), report=report)
    manager._dispatch(command=["x"], operation="do-thing")

    actions = report.actions
    testbase.assert_equal(len(actions), 1)
    testbase.assert_equal(actions[0].feature, "sample-feature")
    testbase.assert_equal(actions[0].operation, "do-thing")
    testbase.assert_equal(actions[0].status, ReportStatus.SUCCESS)
    return


def test_report_autoemit_on_failure_populates_error():
    """
        Failed dispatches emit ActionRecord with `error` populated.
    """
    from ccai.sysman.linux.redhat.maintenancereporting import (
        MaintenanceReport,
        ActionStatus as ReportStatus,
    )

    class MyManager(BaseFeatureManager):
        feature_name = "sample-feature"

    report = MaintenanceReport(run_id="r1", host="h1")
    runner = _MockRunner(queued=[
        RunResult(exit_code=2, stdout="", stderr="oh no", command=["x"])
    ])
    manager = MyManager(runner, FeatureConfig(), report=report)
    manager._dispatch(command=["x"], operation="do-thing")

    actions = report.actions
    testbase.assert_equal(len(actions), 1)
    testbase.assert_equal(actions[0].status, ReportStatus.FAILED)
    testbase.assert_equal(actions[0].error, "oh no")
    return


def test_report_autoemit_in_dry_run():
    """
        Dry-run dispatches still emit ActionRecord (DRY_RUN status).
    """
    from ccai.sysman.linux.redhat.maintenancereporting import (
        MaintenanceReport,
        ActionStatus as ReportStatus,
    )

    class MyManager(BaseFeatureManager):
        feature_name = "sample-feature"

    report = MaintenanceReport(run_id="r1", host="h1")
    runner = _MockRunner()
    manager = MyManager(runner, FeatureConfig(dry_run=True), report=report)
    manager._dispatch(command=["x"], operation="do-thing")

    actions = report.actions
    testbase.assert_equal(len(actions), 1)
    testbase.assert_equal(actions[0].status, ReportStatus.DRY_RUN)
    return


def test_report_autoemit_skipped_when_feature_name_unset():
    """
        With no feature_name class variable, auto-emit is disabled
        even if a report is injected.
    """
    from ccai.sysman.linux.redhat.maintenancereporting import (
        MaintenanceReport,
    )

    report = MaintenanceReport(run_id="r1", host="h1")
    runner = _MockRunner(queued=[
        RunResult(exit_code=0, stdout="", stderr="", command=["x"])
    ])
    # feature_name defaults to None on BaseFeatureManager — no emit.
    manager = BaseFeatureManager(runner, FeatureConfig(), report=report)
    manager._dispatch(command=["x"], operation="do-thing")

    testbase.assert_equal(len(report.actions), 0)
    return


def test_report_autoemit_skipped_when_no_report_injected():
    """
        Without a report, _dispatch never emits records even with
        feature_name set.
    """
    class MyManager(BaseFeatureManager):
        feature_name = "sample-feature"

    runner = _MockRunner(queued=[
        RunResult(exit_code=0, stdout="", stderr="", command=["x"])
    ])
    manager = MyManager(runner, FeatureConfig())
    manager._dispatch(command=["x"], operation="do-thing")
    # No assertion error raised means nothing tried to call emit with
    # a None report.
    return


def test_dispatch_forwards_extra_to_wrap_result():
    """
        Extra kwargs on _dispatch flow through to _wrap_result via
        **extra.
    """
    seen: dict = {}

    class MyManager(BaseFeatureManager):
        def _wrap_result(self, *, status, **kwargs):
            seen.update(kwargs)
            return FeatureActionResult(status=status)

    runner = _MockRunner(queued=[
        RunResult(exit_code=0, stdout="", stderr="", command=["x"])
    ])
    manager = MyManager(runner, FeatureConfig())
    manager._dispatch(command=["x"], detail="d", archive_path="/path/to/archive")

    testbase.assert_equal(seen["detail"], "d")
    testbase.assert_equal(seen["archive_path"], "/path/to/archive")
    return
