# automojo.sysman.linux.redhat.firewallmanagement
#
# FirewallManager wraps firewalld's firewall-cmd through the ICommandRunner
# abstraction.

from dataclasses import dataclass, field
from typing import Optional

from automojo.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


FirewallActionStatus = ActionStatus


@dataclass
class FirewallManagerConfig(FeatureConfig):
    """
        Configuration for :class:`FirewallManager`.  Inherits
        ``dry_run`` and ``elevated`` from :class:`FeatureConfig`.
    """
    pass


@dataclass
class FirewallActionResult(FeatureActionResult):
    """
        Outcome of a firewall management action.  Adds `zone`.
    """
    zone: Optional[str] = None


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


class FirewallManager(BaseFeatureManager):
    """
        Facade for firewalld management through the command runner.
    """

    result_class = FirewallActionResult
    feature_name = "firewall-management"

    def add_service(self, service: str, zone: str) -> FirewallActionResult:
        """
            Add ``service`` to ``zone`` (permanent configuration).
        """
        info = self.query_zone(zone)
        if service in info.services:
            return FirewallActionResult(
                status=FirewallActionStatus.SKIPPED,
                zone=zone, detail=service,
            )
        command = [
            "firewall-cmd",
            "--zone={}".format(zone),
            "--add-service={}".format(service),
            "--permanent",
        ]
        return self._dispatch(
            command=command, zone=zone, detail=service,
            operation="add_service",
        )

    def remove_service(self, service: str, zone: str) -> FirewallActionResult:
        """
            Remove ``service`` from ``zone`` (permanent configuration).
        """
        info = self.query_zone(zone)
        if service not in info.services:
            return FirewallActionResult(
                status=FirewallActionStatus.SKIPPED,
                zone=zone, detail=service,
            )
        command = [
            "firewall-cmd",
            "--zone={}".format(zone),
            "--remove-service={}".format(service),
            "--permanent",
        ]
        return self._dispatch(
            command=command, zone=zone, detail=service,
            operation="remove_service",
        )

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
        return self._dispatch(
            command=command, zone=zone, detail=port_spec,
            operation="add_port",
        )

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
        return self._dispatch(
            command=command, zone=zone, detail=port_spec,
            operation="remove_port",
        )

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
        return self._dispatch(
            command=command, zone=zone, detail=rule,
            operation="add_rich_rule",
        )

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
        return self._dispatch(
            command=command, zone=zone, detail=rule,
            operation="remove_rich_rule",
        )

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
        return self._dispatch(
            command=command, zone=zone, detail=zone,
            operation="set_default_zone",
        )

    def reload(self) -> FirewallActionResult:
        """
            Apply permanent changes to the running firewall.
        """
        command = ["firewall-cmd", "--reload"]
        return self._dispatch(
            command=command, zone=None, detail=None,
            operation="reload",
        )

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
