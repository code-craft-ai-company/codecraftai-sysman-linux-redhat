# Tests for the ProcessManager facade.

from dataclasses import dataclass, field
from typing import Callable, Optional

from ccai import testbase

from ccai.sysman.linux.redhat.processmanagement import (
    ProcessActionStatus,
    ProcessManager,
    ProcessManagerConfig,
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
        self.calls.append({"args": list(args)})
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


def test_list_processes_parses_ps_output():
    """
        list_processes() parses ps output into ProcessInfo records.
    """
    stdout = (
        "1 0 0 root Ss 0 2048 systemd /sbin/init\n"
        "123 1 1000 joe S 0 4096 bash /bin/bash\n"
    )
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout=stdout, stderr="", command=["ps"],
    ))
    pm = ProcessManager(runner, ProcessManagerConfig())
    procs = pm.list_processes()
    testbase.assert_equal(len(procs), 2)
    testbase.assert_equal(procs[1].pid, 123)
    testbase.assert_equal(procs[1].comm, "bash")
    return


def test_find_processes_parses_pgrep_output():
    """
        find_processes() returns integer PIDs parsed from pgrep.
    """
    runner = _MockRunner()
    runner.results[("pgrep", "-f", "nginx")] = RunResult(
        exit_code=0, stdout="10\n12\n", stderr="", command=["pgrep"],
    )
    pm = ProcessManager(runner, ProcessManagerConfig())
    pids = pm.find_processes("nginx")
    testbase.assert_equal(pids, [10, 12])
    return


def test_signal_pid_dispatches_kill():
    """
        signal_pid() dispatches ``kill -<signal> <pid>``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["kill"],
    ))
    pm = ProcessManager(runner, ProcessManagerConfig(elevated=True))
    result = pm.signal_pid(1234, "TERM")
    testbase.assert_equal(result.status, ProcessActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"], ["kill", "-TERM", "1234"],
    )
    return


def test_renice_dispatches_renice():
    """
        renice() runs ``renice <value> -p <pid>``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["renice"],
    ))
    pm = ProcessManager(runner, ProcessManagerConfig(elevated=True))
    result = pm.renice(1234, 10)
    testbase.assert_equal(result.status, ProcessActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"], ["renice", "10", "-p", "1234"],
    )
    return


def test_ionice_dispatches_ionice():
    """
        ionice() runs ``ionice -c <class> -n <priority> -p <pid>``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["ionice"],
    ))
    pm = ProcessManager(runner, ProcessManagerConfig(elevated=True))
    result = pm.ionice(1234, 2, 4)
    testbase.assert_equal(result.status, ProcessActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"],
        ["ionice", "-c", "2", "-n", "4", "-p", "1234"],
    )
    return


def test_signal_by_pattern_skipped_when_no_match():
    """
        signal_by_pattern() returns SKIPPED when pgrep returns no PIDs.
    """
    runner = _MockRunner()
    runner.results[("pgrep", "-f", "absent")] = RunResult(
        exit_code=1, stdout="", stderr="", command=["pgrep"],
    )
    pm = ProcessManager(runner, ProcessManagerConfig())
    result = pm.signal_by_pattern("absent", "TERM")
    testbase.assert_equal(result.status, ProcessActionStatus.SKIPPED)
    testbase.assert_equal(result.matched_count, 0)
    return


def test_set_cpu_affinity_with_cpu_list():
    """
        set_cpu_affinity() with cpu_list runs ``taskset -cp``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["taskset"],
    ))
    pm = ProcessManager(runner, ProcessManagerConfig(elevated=True))
    result = pm.set_cpu_affinity(1234, cpu_list="0-3")
    testbase.assert_equal(result.status, ProcessActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"], ["taskset", "-cp", "0-3", "1234"],
    )
    return
