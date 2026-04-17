# automojo.sysman.linux.redhat.firewallmanagement
#
# FirewallManager wraps firewalld's firewall-cmd through the ICommandRunner
# abstraction.

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.runners.base import ICommandRunner


class FirewallActionStatus(Enum):
    """
        Outcome status for firewall management actions.
    """
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class FirewallManagerConfig:
    """
        Configuration for :class:`FirewallManager`.

        :param dry_run: When True, no commands execute.
        :param elevated: When True, write commands run elevated.
    """
    dry_run: bool = False
    elevated: bool = False


@dataclass
class FirewallActionResult:
    """
        Outcome of a firewall management action.
    """
    status: FirewallActionStatus
    zone: Optional[str] = None
    detail: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


@dataclass
class ZoneInfo:
    """
        Summary information for a firewalld zone.
    """
    zone: str
    services: list[str] = field(default_factory=list)
    ports: list[str] = field(default_factory=list)
    rich_rules: list[str] = field(default_factory=list)
    interfaces: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    is_default: bool = False


@dataclass
class FirewallState:
    """
        Current firewalld running state.

        :param running: True when the firewall daemon is running.
        :param default_zone: The configured default zone name.
    """
    running: bool
    default_zone: str


class FirewallManager:
    """
        Facade for firewalld management through the command runner.
    """

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[FirewallManagerConfig] = None,
    ) -> None:
        """
            :param runner: Command runner for all operations.
            :param config: Manager configuration.
        """
        if config is None:
            config = FirewallManagerConfig()
        self._runner: ICommandRunner = runner
        self._config: FirewallManagerConfig = config
        return

    @property
    def config(self) -> FirewallManagerConfig:
        """
            The active :class:`FirewallManagerConfig`.
        """
        return self._config

    def add_service(self, service: str, zone: str) -> FirewallActionResult:
        """
            Add ``service`` to ``zone`` (permanent configuration).
        """
        info = self.query_zone(zone)
        if service in info.services:
            skipped = FirewallActionResult(
                status=FirewallActionStatus.SKIPPED,
                zone=zone, detail=service,
            )
            return skipped
        command = [
            "firewall-cmd",
            "--zone={}".format(zone),
            "--add-service={}".format(service),
            "--permanent",
        ]
        return self._dispatch(command=command, zone=zone, detail=service)

    def remove_service(self, service: str, zone: str) -> FirewallActionResult:
        """
            Remove ``service`` from ``zone`` (permanent configuration).
        """
        info = self.query_zone(zone)
        if service not in info.services:
            skipped = FirewallActionResult(
                status=FirewallActionStatus.SKIPPED,
                zone=zone, detail=service,
            )
            return skipped
        command = [
            "firewall-cmd",
            "--zone={}".format(zone),
            "--remove-service={}".format(service),
            "--permanent",
        ]
        return self._dispatch(command=command, zone=zone, detail=service)

    def add_port(
        self,
        port: int,
        protocol: str,
        zone: str,
    ) -> FirewallActionResult:
        """
            Open ``port/protocol`` in ``zone`` (permanent configuration).
        """
        port_spec = "{}/{}".format(port, protocol)
        info = self.query_zone(zone)
        if port_spec in info.ports:
            return FirewallActionResult(
                status=FirewallActionStatus.SKIPPED,
                zone=zone, detail=port_spec,
            )
        command = [
            "firewall-cmd",
            "--zone={}".format(zone),
            "--add-port={}".format(port_spec),
            "--permanent",
        ]
        return self._dispatch(command=command, zone=zone, detail=port_spec)

    def remove_port(
        self,
        port: int,
        protocol: str,
        zone: str,
    ) -> FirewallActionResult:
        """
            Close ``port/protocol`` in ``zone`` (permanent configuration).
        """
        port_spec = "{}/{}".format(port, protocol)
        info = self.query_zone(zone)
        if port_spec not in info.ports:
            return FirewallActionResult(
                status=FirewallActionStatus.SKIPPED,
                zone=zone, detail=port_spec,
            )
        command = [
            "firewall-cmd",
            "--zone={}".format(zone),
            "--remove-port={}".format(port_spec),
            "--permanent",
        ]
        return self._dispatch(command=command, zone=zone, detail=port_spec)

    def add_rich_rule(self, rule: str, zone: str) -> FirewallActionResult:
        """
            Add a rich rule to ``zone``.
        """
        info = self.query_zone(zone)
        if rule in info.rich_rules:
            return FirewallActionResult(
                status=FirewallActionStatus.SKIPPED,
                zone=zone, detail=rule,
            )
        command = [
            "firewall-cmd",
            "--zone={}".format(zone),
            "--add-rich-rule={}".format(rule),
            "--permanent",
        ]
        return self._dispatch(command=command, zone=zone, detail=rule)

    def remove_rich_rule(self, rule: str, zone: str) -> FirewallActionResult:
        """
            Remove a rich rule from ``zone``.
        """
        info = self.query_zone(zone)
        if rule not in info.rich_rules:
            return FirewallActionResult(
                status=FirewallActionStatus.SKIPPED,
                zone=zone, detail=rule,
            )
        command = [
            "firewall-cmd",
            "--zone={}".format(zone),
            "--remove-rich-rule={}".format(rule),
            "--permanent",
        ]
        return self._dispatch(command=command, zone=zone, detail=rule)

    def set_default_zone(self, zone: str) -> FirewallActionResult:
        """
            Set the firewalld default zone.
        """
        state = self.query_state()
        if state.default_zone == zone:
            return FirewallActionResult(
                status=FirewallActionStatus.SKIPPED,
                zone=zone, detail=zone,
            )
        command = [
            "firewall-cmd", "--set-default-zone={}".format(zone),
        ]
        return self._dispatch(command=command, zone=zone, detail=zone)

    def reload(self) -> FirewallActionResult:
        """
            Apply permanent changes to the running firewall.
        """
        command = ["firewall-cmd", "--reload"]
        return self._dispatch(command=command, zone=None, detail=None)

    def query_state(self) -> FirewallState:
        """
            Report whether the firewalld daemon is running and the default zone.
        """
        running = False
        state_result = self._runner.run(["firewall-cmd", "--state"], elevated=False)
        if state_result.exit_code == 0 and "running" in state_result.stdout.strip().lower():
            running = True
        default_zone = ""
        default_result = self._runner.run(
            ["firewall-cmd", "--get-default-zone"], elevated=False,
        )
        if default_result.exit_code == 0:
            default_zone = default_result.stdout.strip()
        state = FirewallState(running=running, default_zone=default_zone)
        return state

    def query_zone(self, zone: str) -> ZoneInfo:
        """
            Return :class:`ZoneInfo` for ``zone``.
        """
        command = [
            "firewall-cmd", "--zone={}".format(zone), "--list-all",
        ]
        run_result = self._runner.run(command, elevated=False)
        if run_result.exit_code != 0:
            return ZoneInfo(zone=zone)
        info = _parse_zone_block(stdout=run_result.stdout, zone=zone)
        state = self.query_state()
        info.is_default = state.default_zone == zone
        return info

    def query_all_zones(self) -> list[ZoneInfo]:
        """
            Return :class:`ZoneInfo` for every configured zone.
        """
        run_result = self._runner.run(
            ["firewall-cmd", "--list-all-zones"], elevated=False,
        )
        if run_result.exit_code != 0:
            return []
        zones = _parse_all_zones(stdout=run_result.stdout)
        state = self.query_state()
        for z in zones:
            z.is_default = state.default_zone == z.zone
        return zones

    def _dispatch(
        self,
        *,
        command: list[str],
        zone: Optional[str],
        detail: Optional[str],
    ) -> FirewallActionResult:
        """
            Dispatch a firewall-cmd command honouring dry-run.
        """
        if self._config.dry_run:
            dry = FirewallActionResult(
                status=FirewallActionStatus.DRY_RUN,
                zone=zone, detail=detail, command=command,
            )
            return dry
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = FirewallActionStatus.SUCCESS if run_result.exit_code == 0 else FirewallActionStatus.FAILED
        result = FirewallActionResult(
            status=status,
            zone=zone, detail=detail,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
        return result


def _parse_zone_block(*, stdout: str, zone: str) -> ZoneInfo:
    """
        Parse a single zone block from firewall-cmd --list-all output.
    """
    info = ZoneInfo(zone=zone)
    in_rich_rules = False
    for raw in stdout.splitlines():
        line = raw.rstrip()
        if not line.startswith("  ") and not line.startswith("\t"):
            if len(line.strip()) > 0:
                parts = line.strip().split()
                if len(parts) >= 1:
                    info.zone = parts[0]
            in_rich_rules = False
            continue
        stripped = line.strip()
        if stripped.startswith("services:"):
            values = stripped[len("services:"):].strip().split()
            info.services = values
            in_rich_rules = False
        elif stripped.startswith("ports:"):
            values = stripped[len("ports:"):].strip().split()
            info.ports = values
            in_rich_rules = False
        elif stripped.startswith("interfaces:"):
            values = stripped[len("interfaces:"):].strip().split()
            info.interfaces = values
            in_rich_rules = False
        elif stripped.startswith("sources:"):
            values = stripped[len("sources:"):].strip().split()
            info.sources = values
            in_rich_rules = False
        elif stripped.startswith("rich rules:"):
            in_rich_rules = True
        elif in_rich_rules:
            if len(stripped) > 0:
                info.rich_rules.append(stripped)
    return info


def _parse_all_zones(*, stdout: str) -> list[ZoneInfo]:
    """
        Parse firewall-cmd --list-all-zones into zone blocks.
    """
    zones: list[ZoneInfo] = []
    current_block: list[str] = []
    current_zone: Optional[str] = None
    for raw in stdout.splitlines():
        line = raw.rstrip()
        if len(line) == 0:
            continue
        if not line.startswith("  ") and not line.startswith("\t"):
            if current_zone is not None and len(current_block) > 0:
                body = "\n".join(current_block)
                zones.append(_parse_zone_block(stdout=body, zone=current_zone))
            parts = line.strip().split()
            current_zone = parts[0] if len(parts) > 0 else ""
            current_block = [line]
        else:
            current_block.append(line)
    if current_zone is not None and len(current_block) > 0:
        body = "\n".join(current_block)
        zones.append(_parse_zone_block(stdout=body, zone=current_zone))
    return zones
