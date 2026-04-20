# automojo.sysman.linux.redhat.selinuxmanagement
#
# SELinuxManager wraps semanage, chcon, restorecon, setsebool, getsebool,
# and semodule through the ICommandRunner abstraction.

from dataclasses import dataclass
from typing import Optional

from automojo.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


# Backwards-compatible alias.
SELinuxActionStatus = ActionStatus


@dataclass
class SELinuxManagerConfig(FeatureConfig):
    """
        Configuration for :class:`SELinuxManager`.  Inherits
        ``dry_run`` and ``elevated`` from :class:`FeatureConfig`.
    """
    pass


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
class SELinuxActionResult(FeatureActionResult):
    """
        Outcome of an SELinux management action.  No feature-specific
        extras beyond the base fields.
    """
    pass


class SELinuxManager(BaseFeatureManager):
    """
        Facade for SELinux configuration through the command runner.
    """

    result_class = SELinuxActionResult
    feature_name = "selinux-management"

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
        return self._dispatch(command=command, detail=path, operation="set_context")

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
        return self._dispatch(command=command, detail=path, operation="restore_context")

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
        return self._dispatch(command=command, detail=name, operation="set_boolean")

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
        return self._dispatch(
            command=command,
            detail="{}/{}".format(port, protocol),
            operation="add_port_context",
        )

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
        return self._dispatch(
            command=command,
            detail="{}/{}".format(port, protocol),
            operation="remove_port_context",
        )

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
