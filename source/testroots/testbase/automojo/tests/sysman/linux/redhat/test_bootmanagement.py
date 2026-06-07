# Tests for BootManager.

from dataclasses import dataclass, field
from typing import Optional

from ccai import testbase

from ccai.sysman.linux.redhat.bootmanagement import (
    BootActionStatus,
    BootManager,
    BootManagerConfig,
)
from ccai.sysman.linux.redhat.runners.models import RunResult


@dataclass
class _MockRunner:
    results: dict = field(default_factory=dict)
    default_result: Optional[RunResult] = None
    calls: list = field(default_factory=list)

    def run(self, args, *, elevated=False, cwd=None, env=None, timeout=None,
            on_output=None, on_error=None, capture_output=True, stdin=None):
        self.calls.append({"args": list(args)})
        key = tuple(args)
        if key in self.results:
            return self.results[key]
        if self.default_result is not None:
            return self.default_result
        return RunResult(exit_code=0, stdout="", stderr="", command=list(args))

    def add_aspect(self, aspect): return
    def remove_aspect(self, aspect): return
    def get_metrics(self): return None


def test_set_default_dispatches_grub2_set_default():
    """
        set_default() runs ``grub2-set-default <index>``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["grub2-set-default"],
    ))
    mgr = BootManager(runner, BootManagerConfig(elevated=True))
    result = mgr.set_default(1)
    testbase.assert_equal(result.status, BootActionStatus.SUCCESS)
    testbase.assert_equal(runner.calls[-1]["args"], ["grub2-set-default", "1"])
    return


def test_get_default_parses_saved_entry():
    """
        get_default() extracts saved_entry from grub2-editenv list output.
    """
    runner = _MockRunner()
    runner.results[("grub2-editenv", "list")] = RunResult(
        exit_code=0, stdout="saved_entry=2\nboot_success=1\n", stderr="",
        command=["grub2-editenv"],
    )
    mgr = BootManager(runner, BootManagerConfig())
    testbase.assert_equal(mgr.get_default(), 2)
    return


def test_add_kernel_param_dispatches_grubby():
    """
        add_kernel_param() runs grubby --update-kernel=ALL --args=param.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["grubby"],
    ))
    mgr = BootManager(runner, BootManagerConfig(elevated=True))
    result = mgr.add_kernel_param("quiet")
    testbase.assert_equal(result.status, BootActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"],
        ["grubby", "--update-kernel=ALL", "--args=quiet"],
    )
    return


def test_regenerate_config_runs_mkconfig():
    """
        regenerate_config() dispatches grub2-mkconfig -o path.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["grub2-mkconfig"],
    ))
    mgr = BootManager(runner, BootManagerConfig(elevated=True))
    result = mgr.regenerate_config("/boot/grub2/grub.cfg")
    testbase.assert_equal(result.status, BootActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"],
        ["grub2-mkconfig", "-o", "/boot/grub2/grub.cfg"],
    )
    return
