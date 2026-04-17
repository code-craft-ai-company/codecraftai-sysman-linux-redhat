# automojo.sysman.linux.redhat.processmanagement
#
# ProcessManager wraps ps, pgrep, kill, renice, ionice, and taskset
# commands through the ICommandRunner abstraction.

import time

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.runners.base import ICommandRunner


class ProcessActionStatus(Enum):
    """
        Outcome status for process management actions.
    """
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class ProcessManagerConfig:
    """
        Configuration for :class:`ProcessManager`.

        :param dry_run: When True, no mutating commands execute.
        :param elevated: When True, mutating commands run elevated.
    """
    dry_run: bool = False
    elevated: bool = False


@dataclass
class ProcessInfo:
    """
        Per-process information record from ``ps``.
    """
    pid: int
    ppid: int
    uid: int
    user: str
    stat: str
    nice: int
    rss: int
    comm: str
    args: str


@dataclass
class CpuAffinity:
    """
        CPU affinity description returned by taskset.
    """
    pid: int
    mask: str
    cpu_list: str


@dataclass
class ProcessActionResult:
    """
        Outcome of a process management action.
    """
    status: ProcessActionStatus
    pid: Optional[int] = None
    pattern: Optional[str] = None
    matched_count: Optional[int] = None
    resulting_value: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


@dataclass
class ProcessWaitResult:
    """
        Outcome of a poll-for-exit wait.

        :param pid: The PID that was waited on.
        :param exited: True when the PID exited within the timeout.
        :param waited_seconds: Wall-clock seconds spent waiting.
    """
    pid: int
    exited: bool
    waited_seconds: float


class ProcessManager:
    """
        Facade for process management through the command runner.
    """

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[ProcessManagerConfig] = None,
    ) -> None:
        """
            :param runner: Command runner for all operations.
            :param config: Manager configuration.
        """
        if config is None:
            config = ProcessManagerConfig()
        self._runner: ICommandRunner = runner
        self._config: ProcessManagerConfig = config
        return

    @property
    def config(self) -> ProcessManagerConfig:
        """
            The active :class:`ProcessManagerConfig`.
        """
        return self._config

    def list_processes(
        self,
        user: Optional[str] = None,
    ) -> list[ProcessInfo]:
        """
            List running processes, optionally scoped to ``user``.
        """
        format_spec = "pid=,ppid=,uid=,user=,stat=,nice=,rss=,comm=,args="
        command = ["ps", "-eo", format_spec]
        if user is not None:
            command = ["ps", "-u", user, "-o", format_spec]
        run_result = self._runner.run(command, elevated=False)
        if run_result.exit_code != 0:
            return []
        processes = _parse_ps_output(stdout=run_result.stdout)
        return processes

    def query_process(self, pid: int) -> Optional[ProcessInfo]:
        """
            Return the :class:`ProcessInfo` for ``pid`` or None.
        """
        format_spec = "pid=,ppid=,uid=,user=,stat=,nice=,rss=,comm=,args="
        command = ["ps", "-p", str(pid), "-o", format_spec]
        run_result = self._runner.run(command, elevated=False)
        if run_result.exit_code != 0:
            return None
        processes = _parse_ps_output(stdout=run_result.stdout)
        if len(processes) == 0:
            return None
        return processes[0]

    def find_processes(
        self,
        pattern: str,
        *,
        full: bool = True,
        user: Optional[str] = None,
    ) -> list[int]:
        """
            Return PIDs matching ``pattern`` via ``pgrep``.
        """
        command = ["pgrep"]
        if full:
            command.append("-f")
        if user is not None:
            command.extend(["-u", user])
        command.append(pattern)
        run_result = self._runner.run(command, elevated=False)
        if run_result.exit_code != 0:
            return []
        pids: list[int] = []
        for line in run_result.stdout.splitlines():
            stripped = line.strip()
            if len(stripped) == 0:
                continue
            try:
                pids.append(int(stripped))
            except ValueError:
                continue
        return pids

    def signal_pid(self, pid: int, signal: str) -> ProcessActionResult:
        """
            Deliver a POSIX signal to a single PID.
        """
        command = ["kill", "-{}".format(signal), str(pid)]
        if self._config.dry_run:
            return ProcessActionResult(
                status=ProcessActionStatus.DRY_RUN,
                pid=pid, command=command,
            )
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = ProcessActionStatus.SUCCESS if run_result.exit_code == 0 else ProcessActionStatus.FAILED
        result = ProcessActionResult(
            status=status,
            pid=pid,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
        return result

    def signal_by_pattern(
        self,
        pattern: str,
        signal: str,
        *,
        graceful: bool = False,
        grace_period: float = 10.0,
    ) -> ProcessActionResult:
        """
            Signal every PID matching ``pattern``.  When ``graceful=True``
            sends TERM, waits up to ``grace_period``, then sends KILL if needed.
        """
        pids = self.find_processes(pattern)
        if len(pids) == 0:
            return ProcessActionResult(
                status=ProcessActionStatus.SKIPPED,
                pattern=pattern,
                matched_count=0,
            )

        target_signal = "TERM" if graceful else signal
        failures: list[int] = []
        for pid in pids:
            r = self.signal_pid(pid, target_signal)
            if r.status == ProcessActionStatus.FAILED:
                failures.append(pid)

        if graceful:
            deadline = time.monotonic() + grace_period
            remaining = list(pids)
            while time.monotonic() < deadline and len(remaining) > 0:
                still_running: list[int] = []
                for pid in remaining:
                    info = self.query_process(pid)
                    if info is not None:
                        still_running.append(pid)
                remaining = still_running
                if len(remaining) == 0:
                    break
                time.sleep(0.2)
            for pid in remaining:
                self.signal_pid(pid, "KILL")

        status = ProcessActionStatus.SUCCESS if len(failures) == 0 else ProcessActionStatus.FAILED
        result = ProcessActionResult(
            status=status,
            pattern=pattern,
            matched_count=len(pids),
        )
        return result

    def renice(self, pid: int, nice_value: int) -> ProcessActionResult:
        """
            Change a process's CPU nice value using ``renice``.
        """
        command = ["renice", str(nice_value), "-p", str(pid)]
        if self._config.dry_run:
            return ProcessActionResult(
                status=ProcessActionStatus.DRY_RUN,
                pid=pid, command=command,
                resulting_value=str(nice_value),
            )
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = ProcessActionStatus.SUCCESS if run_result.exit_code == 0 else ProcessActionStatus.FAILED
        return ProcessActionResult(
            status=status, pid=pid, resulting_value=str(nice_value),
            exit_code=run_result.exit_code, stdout=run_result.stdout,
            stderr=run_result.stderr, command=list(run_result.command),
        )

    def ionice(
        self,
        pid: int,
        io_class: int,
        priority: int,
    ) -> ProcessActionResult:
        """
            Change a process's I/O scheduling class and priority.
        """
        command = [
            "ionice",
            "-c", str(io_class),
            "-n", str(priority),
            "-p", str(pid),
        ]
        if self._config.dry_run:
            return ProcessActionResult(
                status=ProcessActionStatus.DRY_RUN,
                pid=pid, command=command,
                resulting_value="{}/{}".format(io_class, priority),
            )
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = ProcessActionStatus.SUCCESS if run_result.exit_code == 0 else ProcessActionStatus.FAILED
        return ProcessActionResult(
            status=status, pid=pid,
            resulting_value="{}/{}".format(io_class, priority),
            exit_code=run_result.exit_code, stdout=run_result.stdout,
            stderr=run_result.stderr, command=list(run_result.command),
        )

    def get_cpu_affinity(self, pid: int) -> Optional[CpuAffinity]:
        """
            Query CPU affinity via ``taskset -p``.
        """
        mask_result = self._runner.run(
            ["taskset", "-p", str(pid)], elevated=False,
        )
        list_result = self._runner.run(
            ["taskset", "-cp", str(pid)], elevated=False,
        )
        if mask_result.exit_code != 0 or list_result.exit_code != 0:
            return None
        mask = _parse_taskset_value(stdout=mask_result.stdout)
        cpu_list = _parse_taskset_value(stdout=list_result.stdout)
        affinity = CpuAffinity(pid=pid, mask=mask, cpu_list=cpu_list)
        return affinity

    def set_cpu_affinity(
        self,
        pid: int,
        *,
        mask: Optional[str] = None,
        cpu_list: Optional[str] = None,
    ) -> ProcessActionResult:
        """
            Pin ``pid`` to ``mask`` or ``cpu_list`` via ``taskset -p``.
        """
        if mask is None and cpu_list is None:
            return ProcessActionResult(
                status=ProcessActionStatus.FAILED,
                pid=pid, stderr="no affinity specification provided",
            )
        if mask is not None:
            command = ["taskset", "-p", mask, str(pid)]
        else:
            command = ["taskset", "-cp", cpu_list, str(pid)]
        if self._config.dry_run:
            return ProcessActionResult(
                status=ProcessActionStatus.DRY_RUN,
                pid=pid, command=command,
                resulting_value=mask if mask is not None else cpu_list,
            )
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = ProcessActionStatus.SUCCESS if run_result.exit_code == 0 else ProcessActionStatus.FAILED
        return ProcessActionResult(
            status=status, pid=pid,
            resulting_value=mask if mask is not None else cpu_list,
            exit_code=run_result.exit_code, stdout=run_result.stdout,
            stderr=run_result.stderr, command=list(run_result.command),
        )


def _parse_ps_output(*, stdout: str) -> list[ProcessInfo]:
    """
        Parse ``ps -eo pid=,ppid=,uid=,user=,stat=,nice=,rss=,comm=,args=`` output.
    """
    processes: list[ProcessInfo] = []
    for raw in stdout.splitlines():
        line = raw.rstrip()
        if len(line.strip()) == 0:
            continue
        tokens = line.split(None, 8)
        if len(tokens) < 9:
            continue
        try:
            pid = int(tokens[0])
            ppid = int(tokens[1])
            uid = int(tokens[2])
            nice = int(tokens[5])
            rss = int(tokens[6])
        except ValueError:
            continue
        info = ProcessInfo(
            pid=pid,
            ppid=ppid,
            uid=uid,
            user=tokens[3],
            stat=tokens[4],
            nice=nice,
            rss=rss,
            comm=tokens[7],
            args=tokens[8],
        )
        processes.append(info)
    return processes


def _parse_taskset_value(*, stdout: str) -> str:
    """
        Parse the trailing value from ``pid <N>'s current affinity mask: <value>``.
    """
    line = stdout.strip()
    idx = line.rfind(":")
    if idx < 0:
        return ""
    value = line[idx + 1:].strip()
    return value
