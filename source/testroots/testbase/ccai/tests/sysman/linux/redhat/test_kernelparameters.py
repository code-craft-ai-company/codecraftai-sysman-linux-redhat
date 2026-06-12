# Tests for KernelParamManager and KernelModuleManager.

from dataclasses import dataclass, field
from typing import Callable, Optional

from ccai import testbase

from ccai.sysman.linux.redhat.kernelparameters import (
    KernelManagerConfig,
    KernelModuleActionStatus,
    KernelModuleManager,
    KernelParamActionStatus,
    KernelParamManager,
)
from ccai.sysman.linux.redhat.runners.models import RunResult


@dataclass
class _MockRunner:

    results: dict = field(default_factory=dict)
    default_result: Optional[RunResult] = None
    calls: list = field(default_factory=list)

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
        self.calls.append({"args": list(args), "stdin": stdin})
        key = tuple(args)
        if key in self.results:
            return self.results[key]
        if self.default_result is not None:
            return self.default_result
        return RunResult(exit_code=0, stdout="", stderr="", command=list(args))

    def add_aspect(self, aspect) -> None:
        return

    def remove_aspect(self, aspect) -> None:
        return

    def get_metrics(self):
        return None


def test_sysctl_read_returns_info():
    """
        read() returns a KernelParamInfo from sysctl -n output.
    """
    runner = _MockRunner()
    runner.results[("sysctl", "-n", "net.ipv4.ip_forward")] = RunResult(
        exit_code=0, stdout="1\n", stderr="", command=["sysctl"],
    )
    manager = KernelParamManager(runner, KernelManagerConfig())
    info = manager.read("net.ipv4.ip_forward")
    testbase.assert_true(info is not None)
    testbase.assert_equal(info.value, "1")
    return


def test_sysctl_write_skipped_when_value_matches():
    """
        write() returns SKIPPED when the current value already matches.
    """
    runner = _MockRunner()
    runner.results[("sysctl", "-n", "net.ipv4.ip_forward")] = RunResult(
        exit_code=0, stdout="1\n", stderr="", command=["sysctl"],
    )
    manager = KernelParamManager(runner, KernelManagerConfig())
    result = manager.write("net.ipv4.ip_forward", "1")
    testbase.assert_equal(result.status, KernelParamActionStatus.SKIPPED)
    return


def test_sysctl_write_dispatches_sysctl_w():
    """
        write() dispatches ``sysctl -w key=value`` when the value changes.
    """
    runner = _MockRunner()
    runner.results[("sysctl", "-n", "net.ipv4.ip_forward")] = RunResult(
        exit_code=0, stdout="0\n", stderr="", command=["sysctl"],
    )
    manager = KernelParamManager(runner, KernelManagerConfig(elevated=True))
    result = manager.write("net.ipv4.ip_forward", "1")
    testbase.assert_equal(result.status, KernelParamActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"],
        ["sysctl", "-w", "net.ipv4.ip_forward=1"],
    )
    return


def test_persist_writes_drop_in_file():
    """
        persist() writes the drop-in file via tee when key is new.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/sysctl.d/99-app.conf")] = RunResult(
        exit_code=1, stdout="", stderr="", command=["cat"],
    )
    manager = KernelParamManager(runner, KernelManagerConfig(elevated=True))
    result = manager.persist("fs.file-max", "100000", "99-app.conf")
    testbase.assert_equal(result.status, KernelParamActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_true("fs.file-max = 100000" in tee_calls[0]["stdin"])
    return


def test_persist_skipped_when_value_matches():
    """
        persist() returns SKIPPED when the drop-in already has the value.
    """
    existing = "fs.file-max = 100000\n"
    runner = _MockRunner()
    runner.results[("cat", "/etc/sysctl.d/99-app.conf")] = RunResult(
        exit_code=0, stdout=existing, stderr="", command=["cat"],
    )
    manager = KernelParamManager(runner, KernelManagerConfig())
    result = manager.persist("fs.file-max", "100000", "99-app.conf")
    testbase.assert_equal(result.status, KernelParamActionStatus.SKIPPED)
    return


def test_apply_all_runs_sysctl_system():
    """
        apply_all() dispatches ``sysctl --system``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["sysctl"],
    ))
    manager = KernelParamManager(runner, KernelManagerConfig(elevated=True))
    result = manager.apply_all()
    testbase.assert_equal(result.status, KernelParamActionStatus.SUCCESS)
    testbase.assert_equal(runner.calls[-1]["args"], ["sysctl", "--system"])
    return


def test_module_load_dispatches_modprobe():
    """
        load() dispatches ``modprobe <name>`` when the module is missing.
    """
    runner = _MockRunner()
    runner.results[("lsmod",)] = RunResult(
        exit_code=0, stdout="Module Size Used by\n", stderr="",
        command=["lsmod"],
    )
    manager = KernelModuleManager(runner, KernelManagerConfig(elevated=True))
    result = manager.load("nf_conntrack")
    testbase.assert_equal(result.status, KernelModuleActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"], ["modprobe", "nf_conntrack"],
    )
    return


def test_module_load_skipped_when_already_loaded():
    """
        load() returns SKIPPED when the module is already listed by lsmod.
    """
    runner = _MockRunner()
    runner.results[("lsmod",)] = RunResult(
        exit_code=0,
        stdout="Module Size Used by\nnf_conntrack 12345 2\n",
        stderr="",
        command=["lsmod"],
    )
    manager = KernelModuleManager(runner, KernelManagerConfig())
    result = manager.load("nf_conntrack")
    testbase.assert_equal(result.status, KernelModuleActionStatus.SKIPPED)
    return


def test_module_persist_load_appends_file():
    """
        persist_load() appends the module name to the drop-in file.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/modules-load.d/app.conf")] = RunResult(
        exit_code=0, stdout="other_mod\n", stderr="", command=["cat"],
    )
    manager = KernelModuleManager(runner, KernelManagerConfig(elevated=True))
    result = manager.persist_load("nf_conntrack", "app.conf")
    testbase.assert_equal(result.status, KernelModuleActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_true("nf_conntrack" in tee_calls[0]["stdin"])
    testbase.assert_true("other_mod" in tee_calls[0]["stdin"])
    return
