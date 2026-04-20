# Tests for the shared feature base module.

from dataclasses import dataclass, field
from typing import Callable, Optional

from automojo import testbase

from automojo.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
    SanitisedDispatchMixin,
    result_from_run,
)
from automojo.sysman.linux.redhat.runners.models import RunResult


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
