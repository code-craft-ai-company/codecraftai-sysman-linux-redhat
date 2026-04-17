# automojo.sysman.linux.redhat.selinuxmanagement
#
# SELinuxManager wraps semanage, chcon, restorecon, setsebool, getsebool,
# and semodule through the ICommandRunner abstraction.

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.runners.base import ICommandRunner


class SELinuxActionStatus(Enum):
    """
        Outcome status for SELinux management actions.
    """
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class SELinuxManagerConfig:
    """
        Configuration for :class:`SELinuxManager`.

        :param dry_run: When True, no commands execute.
        :param elevated: When True, write commands run elevated.
    """
    dry_run: bool = False
    elevated: bool = False


@dataclass
class SELinuxContext:
    """
        SELinux security context of a filesystem path.
    """
    path: str
    user: str
    role: str
    type: str
    level: Optional[str] = None


@dataclass
class SELinuxBoolean:
    """
        State of an SELinux policy boolean.
    """
    name: str
    active: bool
    default: Optional[bool] = None


@dataclass
class SELinuxPortRule:
    """
        An SELinux port-type association.
    """
    port: int
    protocol: str
    type: str


@dataclass
class SELinuxActionResult:
    """
        Outcome of an SELinux management action.
    """
    status: SELinuxActionStatus
    detail: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


class SELinuxManager:
    """
        Facade for SELinux configuration through the command runner.
    """

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[SELinuxManagerConfig] = None,
    ) -> None:
        """
            :param runner: Command runner.
            :param config: Manager configuration.
        """
        if config is None:
            config = SELinuxManagerConfig()
        self._runner: ICommandRunner = runner
        self._config: SELinuxManagerConfig = config
        return

    @property
    def config(self) -> SELinuxManagerConfig:
        """
            The active :class:`SELinuxManagerConfig`.
        """
        return self._config

    def query_context(self, path: str) -> Optional[SELinuxContext]:
        """
            Read the SELinux context of ``path`` via ``ls -Z``.
        """
        run_result = self._runner.run(["ls", "-Zd", path], elevated=False)
        if run_result.exit_code != 0:
            return None
        parts = run_result.stdout.strip().split()
        if len(parts) < 2:
            return None
        context_token = parts[0]
        fields = context_token.split(":")
        if len(fields) < 3:
            return None
        context = SELinuxContext(
            path=path,
            user=fields[0],
            role=fields[1],
            type=fields[2],
            level=fields[3] if len(fields) > 3 else None,
        )
        return context

    def set_context(
        self,
        path: str,
        type_: str,
        *,
        user: Optional[str] = None,
        role: Optional[str] = None,
        recursive: bool = False,
    ) -> SELinuxActionResult:
        """
            Apply an SELinux context via ``chcon``.
        """
        command = ["chcon"]
        if recursive:
            command.append("-R")
        if user is not None:
            command.extend(["-u", user])
        if role is not None:
            command.extend(["-r", role])
        command.extend(["-t", type_, path])
        return self._dispatch(command=command, detail=path)

    def restore_context(
        self,
        path: str,
        *,
        recursive: bool = False,
        force: bool = False,
    ) -> SELinuxActionResult:
        """
            Restore default context via ``restorecon``.
        """
        command = ["restorecon"]
        if recursive:
            command.append("-R")
        if force:
            command.append("-F")
        command.append(path)
        return self._dispatch(command=command, detail=path)

    def get_boolean(self, name: str) -> bool:
        """
            Query a single policy boolean via ``getsebool``.
        """
        run_result = self._runner.run(["getsebool", name], elevated=False)
        if run_result.exit_code != 0:
            return False
        line = run_result.stdout.strip()
        _, _, value = line.partition("-->")
        return value.strip().lower() == "on"

    def set_boolean(
        self,
        name: str,
        value: bool,
        *,
        persistent: bool = False,
    ) -> SELinuxActionResult:
        """
            Toggle a policy boolean via ``setsebool``.
        """
        current = self.get_boolean(name)
        if current == value:
            return SELinuxActionResult(
                status=SELinuxActionStatus.SKIPPED,
                detail=name,
            )
        command = ["setsebool"]
        if persistent:
            command.append("-P")
        command.append(name)
        command.append("on" if value else "off")
        return self._dispatch(command=command, detail=name)

    def list_booleans(self) -> list[SELinuxBoolean]:
        """
            List all booleans via ``getsebool -a``.
        """
        run_result = self._runner.run(["getsebool", "-a"], elevated=False)
        if run_result.exit_code != 0:
            return []
        booleans: list[SELinuxBoolean] = []
        for raw in run_result.stdout.splitlines():
            line = raw.strip()
            if len(line) == 0:
                continue
            name_part, _, value_part = line.partition("-->")
            booleans.append(SELinuxBoolean(
                name=name_part.strip(),
                active=value_part.strip().lower() == "on",
            ))
        return booleans

    def add_port_context(
        self,
        port: int,
        protocol: str,
        type_: str,
    ) -> SELinuxActionResult:
        """
            Associate ``port/protocol`` with SELinux type ``type_`` via semanage.
        """
        command = [
            "semanage", "port", "-a", "-t", type_, "-p", protocol, str(port),
        ]
        return self._dispatch(command=command, detail="{}/{}".format(port, protocol))

    def remove_port_context(
        self,
        port: int,
        protocol: str,
    ) -> SELinuxActionResult:
        """
            Remove a local port context via semanage.
        """
        command = [
            "semanage", "port", "-d", "-p", protocol, str(port),
        ]
        return self._dispatch(command=command, detail="{}/{}".format(port, protocol))

    def list_port_contexts(
        self,
        *,
        local_only: bool = False,
    ) -> list[SELinuxPortRule]:
        """
            List SELinux port contexts via ``semanage port -l``.
        """
        command = ["semanage", "port", "-l"]
        if local_only:
            command.append("-C")
        run_result = self._runner.run(command, elevated=False)
        if run_result.exit_code != 0:
            return []
        rules: list[SELinuxPortRule] = []
        header_skipped = False
        for raw in run_result.stdout.splitlines():
            line = raw.strip()
            if len(line) == 0:
                continue
            if not header_skipped:
                header_skipped = True
                continue
            tokens = line.split()
            if len(tokens) < 3:
                continue
            port_tokens = tokens[2].split(",")
            for pt in port_tokens:
                pt = pt.strip()
                if "-" in pt:
                    continue
                try:
                    port = int(pt)
                except ValueError:
                    continue
                rules.append(SELinuxPortRule(
                    port=port, protocol=tokens[1], type=tokens[0],
                ))
        return rules

    def _dispatch(
        self,
        *,
        command: list[str],
        detail: str,
    ) -> SELinuxActionResult:
        """
            Dispatch a command honouring dry-run.
        """
        if self._config.dry_run:
            return SELinuxActionResult(
                status=SELinuxActionStatus.DRY_RUN,
                detail=detail, command=command,
            )
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = SELinuxActionStatus.SUCCESS if run_result.exit_code == 0 else SELinuxActionStatus.FAILED
        return SELinuxActionResult(
            status=status, detail=detail,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
