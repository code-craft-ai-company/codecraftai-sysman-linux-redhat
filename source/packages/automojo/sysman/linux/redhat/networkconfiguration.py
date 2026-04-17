# automojo.sysman.linux.redhat.networkconfiguration
#
# NetworkConfigurator manages hostname, /etc/hosts, resolver, chrony, and
# NetworkManager connections through the ICommandRunner abstraction.

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.runners.base import ICommandRunner


class NetworkActionStatus(Enum):
    """
        Outcome status for network configuration actions.
    """
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class NetworkManagerConfig:
    """
        Configuration for :class:`NetworkConfigurator`.

        :param dry_run: When True, no commands execute.
        :param elevated: When True, write commands run elevated.
        :param hosts_path: Path to the hosts file.
        :param resolv_conf_path: Path to the resolver config.
        :param chrony_conf_path: Path to the Chrony config.
    """
    dry_run: bool = False
    elevated: bool = False
    hosts_path: str = "/etc/hosts"
    resolv_conf_path: str = "/etc/resolv.conf"
    chrony_conf_path: str = "/etc/chrony.conf"


@dataclass
class NetworkActionResult:
    """
        Outcome of a network configuration action.
    """
    status: NetworkActionStatus
    detail: Optional[str] = None
    diff: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


@dataclass
class HostnameInfo:
    """
        Hostname fields as reported by ``hostnamectl``.
    """
    static_hostname: str
    transient_hostname: str
    pretty_hostname: str


@dataclass
class HostsEntry:
    """
        A parsed /etc/hosts line.
    """
    ip: str
    hostnames: list[str]


@dataclass
class NtpSyncInfo:
    """
        Summary of chrony tracking.
    """
    reference_source: str
    system_time_offset_seconds: float
    is_synchronised: bool


@dataclass
class NmConnectionSpec:
    """
        Input spec for ``nmcli connection add``.
    """
    name: str
    conn_type: str
    device: Optional[str] = None
    ip4: Optional[str] = None
    gw4: Optional[str] = None
    dns4: list[str] = field(default_factory=list)
    autoconnect: bool = True


@dataclass
class NmConnectionInfo:
    """
        Summary of an existing nmcli connection.
    """
    name: str
    conn_type: str
    device: str
    state: str


class NetworkConfigurator:
    """
        Facade for network configuration through the command runner.
    """

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[NetworkManagerConfig] = None,
    ) -> None:
        """
            :param runner: Command runner.
            :param config: Manager configuration.
        """
        if config is None:
            config = NetworkManagerConfig()
        self._runner: ICommandRunner = runner
        self._config: NetworkManagerConfig = config
        return

    @property
    def config(self) -> NetworkManagerConfig:
        """
            The active :class:`NetworkManagerConfig`.
        """
        return self._config

    def set_hostname(self, hostname: str) -> NetworkActionResult:
        """
            Set the static hostname via ``hostnamectl``.
        """
        current = self.query_hostname()
        if current.static_hostname == hostname:
            return NetworkActionResult(
                status=NetworkActionStatus.SKIPPED, detail=hostname,
            )
        command = ["hostnamectl", "set-hostname", hostname]
        return self._dispatch(command=command, detail=hostname)

    def query_hostname(self) -> HostnameInfo:
        """
            Read hostname fields via ``hostnamectl status``.
        """
        run_result = self._runner.run(["hostnamectl", "status"], elevated=False)
        info = HostnameInfo(
            static_hostname="",
            transient_hostname="",
            pretty_hostname="",
        )
        if run_result.exit_code != 0:
            return info
        for raw in run_result.stdout.splitlines():
            line = raw.strip()
            if line.startswith("Static hostname:"):
                info.static_hostname = line.split(":", 1)[1].strip()
            elif line.startswith("Transient hostname:"):
                info.transient_hostname = line.split(":", 1)[1].strip()
            elif line.startswith("Pretty hostname:"):
                info.pretty_hostname = line.split(":", 1)[1].strip()
        return info

    def add_hosts_entry(
        self,
        ip: str,
        hostnames: list[str],
    ) -> NetworkActionResult:
        """
            Add or update an /etc/hosts entry keyed by ``ip``.
        """
        current = self._read(self._config.hosts_path)
        new_content = _upsert_hosts_entry(
            content=current, ip=ip, hostnames=hostnames,
        )
        if new_content == current:
            return NetworkActionResult(
                status=NetworkActionStatus.SKIPPED, detail=ip,
            )
        return self._write(
            path=self._config.hosts_path, content=new_content, detail=ip,
        )

    def remove_hosts_entry(self, ip: str) -> NetworkActionResult:
        """
            Remove the /etc/hosts line starting with ``ip``.
        """
        current = self._read(self._config.hosts_path)
        new_content = _remove_hosts_entry(content=current, ip=ip)
        if new_content == current:
            return NetworkActionResult(
                status=NetworkActionStatus.SKIPPED, detail=ip,
            )
        return self._write(
            path=self._config.hosts_path, content=new_content, detail=ip,
        )

    def query_hosts(self) -> list[HostsEntry]:
        """
            Return parsed /etc/hosts entries.
        """
        current = self._read(self._config.hosts_path)
        entries: list[HostsEntry] = []
        for raw in current.splitlines():
            line = raw.strip()
            if len(line) == 0 or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            entries.append(HostsEntry(
                ip=parts[0], hostnames=parts[1:],
            ))
        return entries

    def set_nameservers(self, nameservers: list[str]) -> NetworkActionResult:
        """
            Replace nameserver lines in the resolver config.
        """
        current = self._read(self._config.resolv_conf_path)
        new_content = _replace_resolv_directives(
            content=current,
            directive="nameserver",
            values=nameservers,
        )
        if new_content == current:
            return NetworkActionResult(
                status=NetworkActionStatus.SKIPPED, detail=",".join(nameservers),
            )
        return self._write(
            path=self._config.resolv_conf_path, content=new_content,
            detail=",".join(nameservers),
        )

    def set_search_domains(self, domains: list[str]) -> NetworkActionResult:
        """
            Replace the ``search`` directive in the resolver config.
        """
        current = self._read(self._config.resolv_conf_path)
        new_content = _replace_resolv_search(
            content=current, domains=domains,
        )
        if new_content == current:
            return NetworkActionResult(
                status=NetworkActionStatus.SKIPPED, detail=",".join(domains),
            )
        return self._write(
            path=self._config.resolv_conf_path, content=new_content,
            detail=",".join(domains),
        )

    def add_ntp_server(self, address: str) -> NetworkActionResult:
        """
            Append an NTP server line to chrony.conf.
        """
        current = self._read(self._config.chrony_conf_path)
        new_content = _upsert_chrony_server(content=current, address=address)
        if new_content == current:
            return NetworkActionResult(
                status=NetworkActionStatus.SKIPPED, detail=address,
            )
        return self._write(
            path=self._config.chrony_conf_path, content=new_content,
            detail=address,
        )

    def remove_ntp_server(self, address: str) -> NetworkActionResult:
        """
            Remove an NTP server line from chrony.conf.
        """
        current = self._read(self._config.chrony_conf_path)
        new_content = _remove_chrony_server(content=current, address=address)
        if new_content == current:
            return NetworkActionResult(
                status=NetworkActionStatus.SKIPPED, detail=address,
            )
        return self._write(
            path=self._config.chrony_conf_path, content=new_content,
            detail=address,
        )

    def query_ntp_sync(self) -> NtpSyncInfo:
        """
            Parse ``chronyc tracking`` output for sync state.
        """
        run_result = self._runner.run(["chronyc", "tracking"], elevated=False)
        info = NtpSyncInfo(
            reference_source="",
            system_time_offset_seconds=0.0,
            is_synchronised=False,
        )
        if run_result.exit_code != 0:
            return info
        for raw in run_result.stdout.splitlines():
            line = raw.strip()
            if line.startswith("Reference ID"):
                _, _, value = line.partition(":")
                info.reference_source = value.strip()
            elif line.startswith("System time"):
                _, _, value = line.partition(":")
                tokens = value.strip().split()
                if len(tokens) >= 2:
                    try:
                        offset = float(tokens[0])
                        if "slow" in tokens:
                            offset = -offset
                        info.system_time_offset_seconds = offset
                    except ValueError:
                        pass
            elif line.startswith("Leap status"):
                _, _, value = line.partition(":")
                info.is_synchronised = "Normal" in value
        return info

    def add_connection(self, spec: NmConnectionSpec) -> NetworkActionResult:
        """
            Add a NetworkManager connection via ``nmcli connection add``.
        """
        existing = self._find_connection(name=spec.name)
        if existing is not None:
            return NetworkActionResult(
                status=NetworkActionStatus.SKIPPED, detail=spec.name,
            )
        command = [
            "nmcli", "connection", "add",
            "type", spec.conn_type,
            "con-name", spec.name,
        ]
        if spec.device is not None:
            command.extend(["ifname", spec.device])
        if spec.ip4 is not None:
            command.extend(["ipv4.addresses", spec.ip4])
            command.extend(["ipv4.method", "manual"])
        if spec.gw4 is not None:
            command.extend(["ipv4.gateway", spec.gw4])
        if len(spec.dns4) > 0:
            command.extend(["ipv4.dns", ",".join(spec.dns4)])
        command.extend([
            "connection.autoconnect", "yes" if spec.autoconnect else "no",
        ])
        return self._dispatch(command=command, detail=spec.name)

    def remove_connection(self, name: str) -> NetworkActionResult:
        """
            Delete a NetworkManager connection.
        """
        existing = self._find_connection(name=name)
        if existing is None:
            return NetworkActionResult(
                status=NetworkActionStatus.SKIPPED, detail=name,
            )
        command = ["nmcli", "connection", "delete", name]
        return self._dispatch(command=command, detail=name)

    def query_connections(self) -> list[NmConnectionInfo]:
        """
            Return NetworkManager connection summaries.
        """
        run_result = self._runner.run(
            ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE,STATE", "connection", "show"],
            elevated=False,
        )
        if run_result.exit_code != 0:
            return []
        connections: list[NmConnectionInfo] = []
        for raw in run_result.stdout.splitlines():
            line = raw.strip()
            if len(line) == 0:
                continue
            fields = line.split(":")
            if len(fields) < 4:
                continue
            connections.append(NmConnectionInfo(
                name=fields[0],
                conn_type=fields[1],
                device=fields[2],
                state=fields[3],
            ))
        return connections

    def _find_connection(self, *, name: str) -> Optional[NmConnectionInfo]:
        """
            Return the :class:`NmConnectionInfo` matching ``name`` or None.
        """
        for conn in self.query_connections():
            if conn.name == name:
                return conn
        return None

    def _read(self, path: str) -> str:
        """
            Read a file via ``cat`` returning empty on missing.
        """
        run_result = self._runner.run(["cat", path], elevated=False)
        if run_result.exit_code != 0:
            return ""
        return run_result.stdout

    def _write(
        self,
        *,
        path: str,
        content: str,
        detail: str,
    ) -> NetworkActionResult:
        """
            Write content via ``tee`` honouring dry-run.
        """
        command = ["tee", path]
        if self._config.dry_run:
            return NetworkActionResult(
                status=NetworkActionStatus.DRY_RUN, detail=detail,
                command=command,
            )
        run_result = self._runner.run(
            command, elevated=self._config.elevated, stdin=content,
        )
        status = NetworkActionStatus.SUCCESS if run_result.exit_code == 0 else NetworkActionStatus.FAILED
        return NetworkActionResult(
            status=status, detail=detail,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )

    def _dispatch(
        self,
        *,
        command: list[str],
        detail: str,
    ) -> NetworkActionResult:
        """
            Dispatch a non-file command honouring dry-run.
        """
        if self._config.dry_run:
            return NetworkActionResult(
                status=NetworkActionStatus.DRY_RUN, detail=detail,
                command=command,
            )
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = NetworkActionStatus.SUCCESS if run_result.exit_code == 0 else NetworkActionStatus.FAILED
        return NetworkActionResult(
            status=status, detail=detail,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )


def _upsert_hosts_entry(
    *,
    content: str,
    ip: str,
    hostnames: list[str],
) -> str:
    """
        Ensure ``/etc/hosts`` contains a line mapping ``ip`` to ``hostnames``.
    """
    new_line = "{}\t{}".format(ip, " ".join(hostnames))
    updated: list[str] = []
    replaced = False
    for raw in content.splitlines():
        stripped = raw.strip()
        if len(stripped) == 0 or stripped.startswith("#"):
            updated.append(raw)
            continue
        first_field = stripped.split()[0]
        if first_field == ip:
            updated.append(new_line)
            replaced = True
            continue
        updated.append(raw)
    if not replaced:
        updated.append(new_line)
    body = "\n".join(updated)
    if not body.endswith("\n"):
        body = body + "\n"
    return body


def _remove_hosts_entry(*, content: str, ip: str) -> str:
    """
        Remove any /etc/hosts lines whose first field matches ``ip``.
    """
    kept: list[str] = []
    for raw in content.splitlines():
        stripped = raw.strip()
        if len(stripped) == 0 or stripped.startswith("#"):
            kept.append(raw)
            continue
        first_field = stripped.split()[0]
        if first_field == ip:
            continue
        kept.append(raw)
    body = "\n".join(kept)
    if len(body) > 0 and not body.endswith("\n"):
        body = body + "\n"
    return body


def _replace_resolv_directives(
    *,
    content: str,
    directive: str,
    values: list[str],
) -> str:
    """
        Replace all occurrences of ``directive`` lines with the given values.
    """
    kept: list[str] = []
    seen_any = False
    for raw in content.splitlines():
        stripped = raw.strip()
        if stripped.startswith("{} ".format(directive)):
            seen_any = True
            continue
        kept.append(raw)
    for value in values:
        kept.append("{} {}".format(directive, value))
    body = "\n".join(kept)
    if not body.endswith("\n"):
        body = body + "\n"
    return body


def _replace_resolv_search(
    *,
    content: str,
    domains: list[str],
) -> str:
    """
        Replace the ``search`` directive in resolv.conf with a single line.
    """
    kept: list[str] = []
    for raw in content.splitlines():
        stripped = raw.strip()
        if stripped.startswith("search "):
            continue
        kept.append(raw)
    kept.append("search {}".format(" ".join(domains)))
    body = "\n".join(kept)
    if not body.endswith("\n"):
        body = body + "\n"
    return body


def _upsert_chrony_server(*, content: str, address: str) -> str:
    """
        Ensure ``server <address> iburst`` is present in chrony.conf.
    """
    needle = "server {}".format(address)
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith(needle):
            return content
    new_line = "server {} iburst".format(address)
    body = content
    if len(body) > 0 and not body.endswith("\n"):
        body = body + "\n"
    body = body + new_line + "\n"
    return body


def _remove_chrony_server(*, content: str, address: str) -> str:
    """
        Remove ``server <address>`` lines from chrony.conf.
    """
    kept: list[str] = []
    needle = "server {}".format(address)
    removed = False
    for raw in content.splitlines():
        stripped = raw.strip()
        if stripped.startswith(needle):
            removed = True
            continue
        kept.append(raw)
    if not removed:
        return content
    body = "\n".join(kept)
    if len(body) > 0 and not body.endswith("\n"):
        body = body + "\n"
    return body
