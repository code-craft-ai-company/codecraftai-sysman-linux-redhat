# automojo.sysman.linux.redhat.virtualizationmanagement
#
# VirtualizationManager wraps libvirt's virsh / virt-install command
# surface for KVM on Red Hat Enterprise Linux through the
# ICommandRunner abstraction.
#
# Domains, virtual networks, storage pools, storage volumes, and
# snapshots are all managed through ``virsh``.  Convenience VM
# provisioning routes through ``virt-install``.  XML definitions for
# domains, networks, and pools are written to a temporary path via
# ``tee`` (through the runner) and removed in a ``finally`` block.
#
# All mutating operations honour ``dry_run`` and ``elevated`` from the
# manager configuration.  Read operations never elevate.

import os
import tempfile

from dataclasses import dataclass
from typing import Optional

from automojo.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


# ----------------------------------------------------------------------
# Module-level command tokens and defaults
# ----------------------------------------------------------------------


_VIRSH = "virsh"
_VIRT_INSTALL = "virt-install"

_DEFAULT_CONNECTION_URI = "qemu:///system"

# Common libvirt domain-state strings.  ``virsh dominfo`` uses the
# human form ``shut off``; ``virsh snapshot-list`` collapses the space
# to ``shutoff``.
_LIBVIRT_RUNNING = "running"
_LIBVIRT_PAUSED = "paused"
_LIBVIRT_SHUT_OFF = "shut off"
_LIBVIRT_SHUTOFF_COLLAPSED = "shutoff"
_LIBVIRT_PMSUSPENDED = "pmsuspended"


# ----------------------------------------------------------------------
# Aliases / config / result types
# ----------------------------------------------------------------------


VirtActionStatus = ActionStatus


@dataclass
class VirtualizationManagerConfig(FeatureConfig):
    """
        Configuration for :class:`VirtualizationManager`.  Inherits
        ``dry_run`` and ``elevated`` from :class:`FeatureConfig`.

        :param uri: libvirt connection URI passed via ``--connect``.
                    Defaults to ``"qemu:///system"``.  An empty string
                    suppresses the flag entirely so libvirt's default
                    URI-resolution rules apply.
        :param virsh_bin: Absolute path or PATH-relative name of the
                          ``virsh`` binary.
        :param virt_install_bin: Absolute path or PATH-relative name of
                                 the ``virt-install`` binary.
    """
    uri: str = _DEFAULT_CONNECTION_URI
    virsh_bin: str = _VIRSH
    virt_install_bin: str = _VIRT_INSTALL


@dataclass
class VirtActionResult(FeatureActionResult):
    """
        Outcome of a virtualization-management action.  Adds the
        targeted domain / network / pool / volume / snapshot name where
        applicable.

        :param name: Primary target identifier (domain, network, pool,
                     volume, or snapshot name).
    """
    name: Optional[str] = None


# ----------------------------------------------------------------------
# Data models
# ----------------------------------------------------------------------


@dataclass
class DomainInfo:
    """
        Parsed view of a libvirt domain (VM) as reported by
        ``virsh dominfo``.

        :param name: Domain name.
        :param uuid: Domain UUID.
        :param state: Coarse state string (``"running"``, ``"shut off"``,
                      ``"paused"``, ...).
        :param autostart: Whether the domain is set to autostart at
                          libvirtd boot.
        :param persistent: Whether the domain has a persistent
                           definition (vs. transient).
        :param max_memory_kib: Configured maximum memory in KiB.
        :param used_memory_kib: Currently used memory in KiB.
        :param vcpus: Configured vCPU count.
    """
    name: str
    uuid: Optional[str] = None
    state: str = ""
    autostart: bool = False
    persistent: bool = False
    max_memory_kib: Optional[int] = None
    used_memory_kib: Optional[int] = None
    vcpus: Optional[int] = None


@dataclass
class VirtInstallSpec:
    """
        Specification for :meth:`VirtualizationManager.create_vm`.

        :param name: Domain name (becomes ``--name``).
        :param memory_mib: Memory in mebibytes (becomes ``--memory``).
        :param vcpus: Number of virtual CPUs (becomes ``--vcpus``).
        :param disk: Value for ``--disk`` (e.g.
                     ``"pool=default,size=20,format=qcow2"`` or
                     ``"path=/var/lib/libvirt/images/x.qcow2"``).
        :param network: Value for ``--network`` (e.g.
                        ``"network=default"`` or ``"bridge=br0"``).
        :param os_variant: Value for ``--os-variant`` (e.g.
                           ``"rhel9.0"``, ``"fedora40"``).
        :param location: Optional install-tree URL passed via
                         ``--location``.
        :param cdrom: Optional ISO path passed via ``--cdrom``.
        :param graphics: Value for ``--graphics``.  Defaults to
                         ``"none"`` for headless installs.
        :param noautoconsole: Append ``--noautoconsole`` when True.
        :param extra_args: Optional extra arguments appended verbatim.
    """
    name: str
    memory_mib: int
    vcpus: int
    disk: str
    network: str
    os_variant: str
    location: Optional[str] = None
    cdrom: Optional[str] = None
    graphics: str = "none"
    noautoconsole: bool = True
    extra_args: Optional[list[str]] = None


@dataclass
class NetworkInfo:
    """
        Parsed view of a libvirt virtual network as reported by
        ``virsh net-list --all`` and ``virsh net-info``.

        :param name: Network name.
        :param uuid: Network UUID, when reported.
        :param active: Whether the network is currently active.
        :param persistent: Whether the network has a persistent
                           definition.
        :param autostart: Whether the network autostarts.
    """
    name: str
    uuid: Optional[str] = None
    active: bool = False
    persistent: bool = False
    autostart: bool = False


@dataclass
class StoragePoolInfo:
    """
        Parsed view of a libvirt storage pool as reported by
        ``virsh pool-list --all --details``.

        :param name: Pool name.
        :param uuid: Pool UUID, when reported.
        :param state: Pool state (``"running"`` / ``"inactive"``).
        :param autostart: Whether the pool autostarts.
        :param capacity_bytes: Total capacity in bytes.
        :param allocation_bytes: Currently allocated bytes.
        :param available_bytes: Currently free bytes.
    """
    name: str
    uuid: Optional[str] = None
    state: str = ""
    autostart: bool = False
    capacity_bytes: Optional[int] = None
    allocation_bytes: Optional[int] = None
    available_bytes: Optional[int] = None


@dataclass
class StorageVolumeInfo:
    """
        Parsed view of a libvirt storage volume as reported by
        ``virsh vol-list --pool <pool> --details``.

        :param name: Volume name.
        :param path: Backing-file path on the host.
        :param capacity_bytes: Volume capacity in bytes.
        :param allocation_bytes: Currently allocated bytes.
    """
    name: str
    path: str = ""
    capacity_bytes: Optional[int] = None
    allocation_bytes: Optional[int] = None


@dataclass
class SnapshotInfo:
    """
        Parsed view of a libvirt VM snapshot as reported by
        ``virsh snapshot-list``.

        :param domain: Owning domain name.
        :param name: Snapshot name.
        :param parent: Parent snapshot name, when reported.
        :param creation_time: Creation timestamp as reported by
                              libvirt.  ``None`` when unparseable.
        :param state: Domain state captured by the snapshot.
    """
    domain: str
    name: str
    parent: Optional[str] = None
    creation_time: Optional[str] = None
    state: Optional[str] = None


# ----------------------------------------------------------------------
# Exceptions
# ----------------------------------------------------------------------


class VirtualizationQueryError(Exception):
    """
        Raised when a virtualization read fails for a reason other than
        the target simply being absent.
    """

    def __init__(
        self,
        *,
        command: list[str],
        exit_code: Optional[int],
        stderr: str,
    ) -> None:
        """
            :param command: The argv that failed.
            :param exit_code: The runner's exit code.
            :param stderr: The runner's captured stderr.
        """
        self.command: list[str] = list(command)
        self.exit_code: Optional[int] = exit_code
        self.stderr: str = stderr

        err_msg_lines = ["Virtualization query command failed."]
        err_msg_lines.append("    COMMAND: {}".format(" ".join(command)))
        err_msg_lines.append("    EXIT_CODE: {}".format(exit_code))
        err_msg_lines.append("    STDERR: {}".format(stderr))
        err_msg = os.linesep.join(err_msg_lines)
        super().__init__(err_msg)
        return


# ----------------------------------------------------------------------
# VirtualizationManager
# ----------------------------------------------------------------------


class VirtualizationManager(BaseFeatureManager):
    """
        Facade for libvirt / KVM management on Red Hat Enterprise
        Linux through ``virsh`` and ``virt-install``.  Every operation
        is dispatched through the injected :class:`ICommandRunner`;
        this module never constructs its own subprocess.
    """

    result_class = VirtActionResult
    feature_name = "virtualization-management"

    # ------------------------------------------------------------------
    # Domain reads
    # ------------------------------------------------------------------

    def list_domains(self) -> list[DomainInfo]:
        """
            Enumerate every defined libvirt domain.

            Dispatches ``virsh list --all --name`` to gather the names,
            then ``virsh dominfo`` per name to collect detailed state.

            :returns: One :class:`DomainInfo` per defined domain.  Empty
                      list when no domains exist.
            :raises VirtualizationQueryError: When the ``virsh list``
                                              call fails for an
                                              unexpected reason.
        """
        list_command = self._virsh(args=["list", "--all", "--name"])
        list_run = self._runner.run(list(list_command), elevated=False)
        if list_run.exit_code != 0:
            raise VirtualizationQueryError(
                command=list_command,
                exit_code=list_run.exit_code,
                stderr=list_run.stderr,
            )

        names = _parse_virsh_list_names(stdout=list_run.stdout)
        rtnval: list[DomainInfo] = []
        for name in names:
            info = self._read_dominfo(name=name)
            if info is not None:
                rtnval.append(info)
        return rtnval

    def query_domain(self, name: str) -> Optional[DomainInfo]:
        """
            Return the :class:`DomainInfo` for ``name``, or ``None``
            when the domain is not defined.

            :param name: Domain name.

            :returns: The matching :class:`DomainInfo`, or ``None``.
        """
        rtnval = self._read_dominfo(name=name)
        return rtnval

    def dumpxml(self, name: str) -> Optional[str]:
        """
            Return the raw XML definition for ``name`` via
            ``virsh dumpxml``.  Returns ``None`` when the domain is
            not defined.

            :param name: Domain name.

            :returns: The raw XML text, or ``None``.
        """
        command = self._virsh(args=["dumpxml", name])
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: Optional[str] = None
            return rtnval
        rtnval = run_result.stdout
        return rtnval

    # ------------------------------------------------------------------
    # Domain lifecycle
    # ------------------------------------------------------------------

    def start_domain(self, name: str) -> VirtActionResult:
        """
            Power on a defined domain via ``virsh start``.

            Idempotent: returns ``SKIPPED`` when the domain is already
            running.

            :param name: Domain name.

            :returns: The :class:`VirtActionResult`.
        """
        command = self._virsh(args=["start", name])
        if self._config.dry_run:
            rtnval = self._build_result(
                status=VirtActionStatus.DRY_RUN,
                command=command, name=name,
            )
            return rtnval
        info = self._read_dominfo(name=name)
        if info is not None and _is_running_state(state=info.state):
            rtnval = self._build_result(
                status=VirtActionStatus.SKIPPED,
                command=command, name=name,
                detail="domain {!r} is already running".format(name),
            )
            return rtnval
        rtnval = self._dispatch_virt(
            command=command, name=name, operation="start_domain",
        )
        return rtnval

    def shutdown_domain(self, name: str) -> VirtActionResult:
        """
            Issue a graceful shutdown via ``virsh shutdown``.

            Idempotent: returns ``SKIPPED`` when the domain is already
            shut off.

            :param name: Domain name.

            :returns: The :class:`VirtActionResult`.
        """
        command = self._virsh(args=["shutdown", name])
        if self._config.dry_run:
            rtnval = self._build_result(
                status=VirtActionStatus.DRY_RUN,
                command=command, name=name,
            )
            return rtnval
        info = self._read_dominfo(name=name)
        if info is not None and _is_shutoff_state(state=info.state):
            rtnval = self._build_result(
                status=VirtActionStatus.SKIPPED,
                command=command, name=name,
                detail="domain {!r} is already shut off".format(name),
            )
            return rtnval
        rtnval = self._dispatch_virt(
            command=command, name=name, operation="shutdown_domain",
        )
        return rtnval

    def destroy_domain(self, name: str) -> VirtActionResult:
        """
            Forcibly power off a running domain via ``virsh destroy``.
            (Libvirt's ``destroy`` is a hard power-off; the persistent
            definition stays intact.)

            Idempotent: returns ``SKIPPED`` when the domain is already
            shut off.

            :param name: Domain name.

            :returns: The :class:`VirtActionResult`.
        """
        command = self._virsh(args=["destroy", name])
        if self._config.dry_run:
            rtnval = self._build_result(
                status=VirtActionStatus.DRY_RUN,
                command=command, name=name,
            )
            return rtnval
        info = self._read_dominfo(name=name)
        if info is not None and _is_shutoff_state(state=info.state):
            rtnval = self._build_result(
                status=VirtActionStatus.SKIPPED,
                command=command, name=name,
                detail="domain {!r} is already shut off".format(name),
            )
            return rtnval
        rtnval = self._dispatch_virt(
            command=command, name=name, operation="destroy_domain",
        )
        return rtnval

    def reboot_domain(self, name: str) -> VirtActionResult:
        """
            Reboot a domain via ``virsh reboot``.

            :param name: Domain name.

            :returns: The :class:`VirtActionResult`.
        """
        command = self._virsh(args=["reboot", name])
        if self._config.dry_run:
            rtnval = self._build_result(
                status=VirtActionStatus.DRY_RUN,
                command=command, name=name,
            )
            return rtnval
        rtnval = self._dispatch_virt(
            command=command, name=name, operation="reboot_domain",
        )
        return rtnval

    def set_autostart(
        self,
        name: str,
        enabled: bool,
    ) -> VirtActionResult:
        """
            Enable or disable autostart for ``name`` via
            ``virsh autostart [--disable] <name>``.

            Idempotent: returns ``SKIPPED`` when the domain is already
            in the requested autostart state.

            :param name: Domain name.
            :param enabled: When True, enable autostart; when False,
                            disable autostart.

            :returns: The :class:`VirtActionResult`.
        """
        if enabled:
            args = ["autostart", name]
        else:
            args = ["autostart", "--disable", name]
        command = self._virsh(args=args)

        if self._config.dry_run:
            rtnval = self._build_result(
                status=VirtActionStatus.DRY_RUN,
                command=command, name=name,
            )
            return rtnval

        info = self._read_dominfo(name=name)
        if info is not None and info.autostart == enabled:
            rtnval = self._build_result(
                status=VirtActionStatus.SKIPPED,
                command=command, name=name,
                detail="domain {!r} autostart already matches".format(name),
            )
            return rtnval

        rtnval = self._dispatch_virt(
            command=command, name=name, operation="set_autostart",
        )
        return rtnval

    # ------------------------------------------------------------------
    # Domain definition
    # ------------------------------------------------------------------

    def define_domain(self, xml_content: str) -> VirtActionResult:
        """
            Define a domain from XML content via ``virsh define``.

            The XML is written to a host-side temporary file.  The
            temp file is unlinked in a ``finally`` block whether the
            ``virsh define`` call succeeded or failed.

            :param xml_content: The full domain XML.

            :returns: The :class:`VirtActionResult`.
        """
        rtnval = self._define_from_xml(
            xml_content=xml_content,
            verb="define",
            operation="define_domain",
        )
        return rtnval

    def undefine_domain(
        self,
        name: str,
        remove_storage: bool = False,
        nvram: bool = False,
    ) -> VirtActionResult:
        """
            Remove the persistent definition of ``name`` via
            ``virsh undefine``.

            Idempotent: returns ``SKIPPED`` when the domain is not
            defined.

            :param name: Domain name.
            :param remove_storage: Append ``--remove-all-storage`` to
                                   wipe backing volumes.
            :param nvram: Append ``--nvram`` to remove the NVRAM file
                          (UEFI guests).

            :returns: The :class:`VirtActionResult`.
        """
        args = ["undefine", name]
        if remove_storage:
            args.append("--remove-all-storage")
        if nvram:
            args.append("--nvram")
        command = self._virsh(args=args)

        if self._config.dry_run:
            rtnval = self._build_result(
                status=VirtActionStatus.DRY_RUN,
                command=command, name=name,
            )
            return rtnval

        info = self._read_dominfo(name=name)
        if info is None:
            rtnval = self._build_result(
                status=VirtActionStatus.SKIPPED,
                command=command, name=name,
                detail="domain {!r} is not defined".format(name),
            )
            return rtnval

        rtnval = self._dispatch_virt(
            command=command, name=name, operation="undefine_domain",
        )
        return rtnval

    # ------------------------------------------------------------------
    # virt-install
    # ------------------------------------------------------------------

    def create_vm(
        self,
        spec: VirtInstallSpec,
        replace: bool = False,
    ) -> VirtActionResult:
        """
            Provision a fresh VM via ``virt-install``.

            When a domain with ``spec.name`` already exists and
            ``replace=False`` the call returns ``SKIPPED``.  When
            ``replace=True`` the existing domain is undefined first
            (``virsh undefine --remove-all-storage``) before
            ``virt-install`` is dispatched.

            :param spec: The :class:`VirtInstallSpec`.
            :param replace: When True, undefine an existing domain of
                            the same name before installing.

            :returns: The :class:`VirtActionResult`.
        """
        command = self._build_virt_install_argv(spec=spec)

        if self._config.dry_run:
            rtnval = self._build_result(
                status=VirtActionStatus.DRY_RUN,
                command=command, name=spec.name,
            )
            return rtnval

        existing = self._read_dominfo(name=spec.name)
        if existing is not None:
            if not replace:
                rtnval = self._build_result(
                    status=VirtActionStatus.SKIPPED,
                    command=command, name=spec.name,
                    detail="domain {!r} already exists".format(spec.name),
                )
                return rtnval
            undefine_result = self.undefine_domain(
                name=spec.name, remove_storage=True,
            )
            if undefine_result.status == VirtActionStatus.FAILED:
                rtnval = self._build_result(
                    status=VirtActionStatus.FAILED,
                    command=command, name=spec.name,
                    detail="undefine of existing domain failed",
                    exit_code=undefine_result.exit_code,
                    stderr=undefine_result.stderr,
                )
                return rtnval

        rtnval = self._dispatch_virt(
            command=command, name=spec.name, operation="create_vm",
        )
        return rtnval

    # ------------------------------------------------------------------
    # Networks
    # ------------------------------------------------------------------

    def list_networks(self) -> list[NetworkInfo]:
        """
            Enumerate libvirt virtual networks via
            ``virsh net-list --all --name`` plus per-network
            ``virsh net-info``.

            :returns: One :class:`NetworkInfo` per network.
            :raises VirtualizationQueryError: When ``virsh net-list``
                                              fails for an unexpected
                                              reason.
        """
        list_command = self._virsh(args=["net-list", "--all", "--name"])
        list_run = self._runner.run(list(list_command), elevated=False)
        if list_run.exit_code != 0:
            raise VirtualizationQueryError(
                command=list_command,
                exit_code=list_run.exit_code,
                stderr=list_run.stderr,
            )
        names = _parse_virsh_list_names(stdout=list_run.stdout)
        rtnval: list[NetworkInfo] = []
        for name in names:
            info = self._read_netinfo(name=name)
            if info is not None:
                rtnval.append(info)
        return rtnval

    def query_network(self, name: str) -> Optional[NetworkInfo]:
        """
            Return the :class:`NetworkInfo` for ``name``, or ``None``
            when the network is not defined.

            :param name: Network name.

            :returns: The matching :class:`NetworkInfo`, or ``None``.
        """
        rtnval = self._read_netinfo(name=name)
        return rtnval

    def define_network(self, xml_content: str) -> VirtActionResult:
        """
            Define a virtual network from XML content via
            ``virsh net-define``.  The XML temp file is cleaned up in
            a ``finally`` block.

            :param xml_content: The full network XML.

            :returns: The :class:`VirtActionResult`.
        """
        rtnval = self._define_from_xml(
            xml_content=xml_content,
            verb="net-define",
            operation="define_network",
        )
        return rtnval

    def undefine_network(self, name: str) -> VirtActionResult:
        """
            Undefine a virtual network via ``virsh net-undefine``.

            Idempotent: returns ``SKIPPED`` when the network is not
            defined.

            :param name: Network name.

            :returns: The :class:`VirtActionResult`.
        """
        command = self._virsh(args=["net-undefine", name])
        if self._config.dry_run:
            rtnval = self._build_result(
                status=VirtActionStatus.DRY_RUN,
                command=command, name=name,
            )
            return rtnval
        info = self._read_netinfo(name=name)
        if info is None:
            rtnval = self._build_result(
                status=VirtActionStatus.SKIPPED,
                command=command, name=name,
                detail="network {!r} is not defined".format(name),
            )
            return rtnval
        rtnval = self._dispatch_virt(
            command=command, name=name, operation="undefine_network",
        )
        return rtnval

    def start_network(self, name: str) -> VirtActionResult:
        """
            Start a virtual network via ``virsh net-start``.

            Idempotent: returns ``SKIPPED`` when the network is
            already active.

            :param name: Network name.

            :returns: The :class:`VirtActionResult`.
        """
        command = self._virsh(args=["net-start", name])
        if self._config.dry_run:
            rtnval = self._build_result(
                status=VirtActionStatus.DRY_RUN,
                command=command, name=name,
            )
            return rtnval
        info = self._read_netinfo(name=name)
        if info is not None and info.active:
            rtnval = self._build_result(
                status=VirtActionStatus.SKIPPED,
                command=command, name=name,
                detail="network {!r} is already active".format(name),
            )
            return rtnval
        rtnval = self._dispatch_virt(
            command=command, name=name, operation="start_network",
        )
        return rtnval

    def destroy_network(self, name: str) -> VirtActionResult:
        """
            Stop a virtual network via ``virsh net-destroy``.

            Idempotent: returns ``SKIPPED`` when the network is
            already inactive.

            :param name: Network name.

            :returns: The :class:`VirtActionResult`.
        """
        command = self._virsh(args=["net-destroy", name])
        if self._config.dry_run:
            rtnval = self._build_result(
                status=VirtActionStatus.DRY_RUN,
                command=command, name=name,
            )
            return rtnval
        info = self._read_netinfo(name=name)
        if info is not None and not info.active:
            rtnval = self._build_result(
                status=VirtActionStatus.SKIPPED,
                command=command, name=name,
                detail="network {!r} is already inactive".format(name),
            )
            return rtnval
        rtnval = self._dispatch_virt(
            command=command, name=name, operation="destroy_network",
        )
        return rtnval

    def set_network_autostart(
        self,
        name: str,
        enabled: bool,
    ) -> VirtActionResult:
        """
            Enable or disable autostart for a virtual network.

            Idempotent: returns ``SKIPPED`` when the network is
            already in the requested state.

            :param name: Network name.
            :param enabled: When True, enable autostart.

            :returns: The :class:`VirtActionResult`.
        """
        if enabled:
            args = ["net-autostart", name]
        else:
            args = ["net-autostart", "--disable", name]
        command = self._virsh(args=args)

        if self._config.dry_run:
            rtnval = self._build_result(
                status=VirtActionStatus.DRY_RUN,
                command=command, name=name,
            )
            return rtnval

        info = self._read_netinfo(name=name)
        if info is not None and info.autostart == enabled:
            rtnval = self._build_result(
                status=VirtActionStatus.SKIPPED,
                command=command, name=name,
                detail="network {!r} autostart already matches".format(name),
            )
            return rtnval

        rtnval = self._dispatch_virt(
            command=command, name=name, operation="set_network_autostart",
        )
        return rtnval

    # ------------------------------------------------------------------
    # Pools and volumes
    # ------------------------------------------------------------------

    def list_pools(self) -> list[StoragePoolInfo]:
        """
            Enumerate libvirt storage pools via
            ``virsh pool-list --all --details``.

            :returns: One :class:`StoragePoolInfo` per pool.
            :raises VirtualizationQueryError: When the call fails for
                                              an unexpected reason.
        """
        command = self._virsh(args=["pool-list", "--all", "--details"])
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            raise VirtualizationQueryError(
                command=command,
                exit_code=run_result.exit_code,
                stderr=run_result.stderr,
            )
        rtnval = _parse_virsh_pool_list_details(stdout=run_result.stdout)
        return rtnval

    def define_pool(self, xml_content: str) -> VirtActionResult:
        """
            Define a storage pool from XML content via
            ``virsh pool-define``.  The XML temp file is cleaned up in
            a ``finally`` block.

            :param xml_content: The full pool XML.

            :returns: The :class:`VirtActionResult`.
        """
        rtnval = self._define_from_xml(
            xml_content=xml_content,
            verb="pool-define",
            operation="define_pool",
        )
        return rtnval

    def start_pool(self, name: str) -> VirtActionResult:
        """
            Start a storage pool via ``virsh pool-start``.

            Idempotent: returns ``SKIPPED`` when the pool is already
            running.

            :param name: Pool name.

            :returns: The :class:`VirtActionResult`.
        """
        command = self._virsh(args=["pool-start", name])
        if self._config.dry_run:
            rtnval = self._build_result(
                status=VirtActionStatus.DRY_RUN,
                command=command, name=name,
            )
            return rtnval
        info = self._read_poolinfo(name=name)
        if info is not None and _is_running_state(state=info.state):
            rtnval = self._build_result(
                status=VirtActionStatus.SKIPPED,
                command=command, name=name,
                detail="pool {!r} is already running".format(name),
            )
            return rtnval
        rtnval = self._dispatch_virt(
            command=command, name=name, operation="start_pool",
        )
        return rtnval

    def set_pool_autostart(
        self,
        name: str,
        enabled: bool,
    ) -> VirtActionResult:
        """
            Enable or disable autostart for a storage pool.

            Idempotent: returns ``SKIPPED`` when the pool is already
            in the requested state.

            :param name: Pool name.
            :param enabled: When True, enable autostart.

            :returns: The :class:`VirtActionResult`.
        """
        if enabled:
            args = ["pool-autostart", name]
        else:
            args = ["pool-autostart", "--disable", name]
        command = self._virsh(args=args)

        if self._config.dry_run:
            rtnval = self._build_result(
                status=VirtActionStatus.DRY_RUN,
                command=command, name=name,
            )
            return rtnval

        info = self._read_poolinfo(name=name)
        if info is not None and info.autostart == enabled:
            rtnval = self._build_result(
                status=VirtActionStatus.SKIPPED,
                command=command, name=name,
                detail="pool {!r} autostart already matches".format(name),
            )
            return rtnval

        rtnval = self._dispatch_virt(
            command=command, name=name, operation="set_pool_autostart",
        )
        return rtnval

    def list_volumes(self, pool: str) -> list[StorageVolumeInfo]:
        """
            Enumerate volumes in ``pool`` via
            ``virsh vol-list --pool <pool> --details``.

            :param pool: Pool name.

            :returns: One :class:`StorageVolumeInfo` per volume.
            :raises VirtualizationQueryError: When the call fails for
                                              an unexpected reason.
        """
        command = self._virsh(
            args=["vol-list", "--pool", pool, "--details"],
        )
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            raise VirtualizationQueryError(
                command=command,
                exit_code=run_result.exit_code,
                stderr=run_result.stderr,
            )
        rtnval = _parse_virsh_vol_list_details(stdout=run_result.stdout)
        return rtnval

    def create_volume(
        self,
        pool: str,
        name: str,
        size: str,
        format_: str = "qcow2",
    ) -> VirtActionResult:
        """
            Create a volume in ``pool`` via ``virsh vol-create-as``.

            Idempotent: returns ``SKIPPED`` when a volume with the
            same name already exists in the pool.

            :param pool: Pool name.
            :param name: Volume name.
            :param size: Size string (e.g. ``"20G"``, ``"512M"``).
            :param format_: Disk format (e.g. ``"qcow2"``, ``"raw"``).

            :returns: The :class:`VirtActionResult`.
        """
        args = ["vol-create-as", pool, name, size]
        if format_ is not None and len(format_) > 0:
            args.append("--format")
            args.append(format_)
        command = self._virsh(args=args)

        if self._config.dry_run:
            rtnval = self._build_result(
                status=VirtActionStatus.DRY_RUN,
                command=command, name=name,
            )
            return rtnval

        if self._volume_exists(pool=pool, name=name):
            rtnval = self._build_result(
                status=VirtActionStatus.SKIPPED,
                command=command, name=name,
                detail="volume {!r} already exists in pool {!r}".format(
                    name, pool,
                ),
            )
            return rtnval

        rtnval = self._dispatch_virt(
            command=command, name=name, operation="create_volume",
        )
        return rtnval

    def delete_volume(
        self,
        pool: str,
        name: str,
    ) -> VirtActionResult:
        """
            Delete a volume via ``virsh vol-delete --pool <pool>
            <name>``.

            Idempotent: returns ``SKIPPED`` when no volume with
            ``name`` is present in ``pool``.

            :param pool: Pool name.
            :param name: Volume name.

            :returns: The :class:`VirtActionResult`.
        """
        command = self._virsh(
            args=["vol-delete", "--pool", pool, name],
        )
        if self._config.dry_run:
            rtnval = self._build_result(
                status=VirtActionStatus.DRY_RUN,
                command=command, name=name,
            )
            return rtnval

        if not self._volume_exists(pool=pool, name=name):
            rtnval = self._build_result(
                status=VirtActionStatus.SKIPPED,
                command=command, name=name,
                detail="volume {!r} not present in pool {!r}".format(
                    name, pool,
                ),
            )
            return rtnval

        rtnval = self._dispatch_virt(
            command=command, name=name, operation="delete_volume",
        )
        return rtnval

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def create_snapshot(
        self,
        domain: str,
        name: str,
        description: Optional[str] = None,
    ) -> VirtActionResult:
        """
            Create a libvirt snapshot via
            ``virsh snapshot-create-as <domain> <name> [description]``.

            :param domain: Domain name.
            :param name: Snapshot name.
            :param description: Optional snapshot description.

            :returns: The :class:`VirtActionResult`.
        """
        args = ["snapshot-create-as", domain, name]
        if description is not None and len(description) > 0:
            args.append(description)
        command = self._virsh(args=args)

        if self._config.dry_run:
            rtnval = self._build_result(
                status=VirtActionStatus.DRY_RUN,
                command=command, name=name,
            )
            return rtnval

        rtnval = self._dispatch_virt(
            command=command, name=name, operation="create_snapshot",
        )
        return rtnval

    def list_snapshots(self, domain: str) -> list[SnapshotInfo]:
        """
            List snapshots for ``domain`` via
            ``virsh snapshot-list <domain> --tree``.

            :param domain: Domain name.

            :returns: One :class:`SnapshotInfo` per row.
            :raises VirtualizationQueryError: When the call fails for
                                              an unexpected reason.
        """
        command = self._virsh(args=["snapshot-list", domain, "--tree"])
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            raise VirtualizationQueryError(
                command=command,
                exit_code=run_result.exit_code,
                stderr=run_result.stderr,
            )
        rtnval = _parse_virsh_snapshot_tree(
            stdout=run_result.stdout, domain=domain,
        )
        return rtnval

    def revert_snapshot(
        self,
        domain: str,
        name: str,
    ) -> VirtActionResult:
        """
            Revert ``domain`` to ``name`` via ``virsh snapshot-revert``.

            Unconditional — caller decides whether to revert.  On
            ``SUCCESS`` ``detail`` carries a data-loss advisory.

            :param domain: Domain name.
            :param name: Snapshot name.

            :returns: The :class:`VirtActionResult`.
        """
        command = self._virsh(
            args=["snapshot-revert", domain, name],
        )
        if self._config.dry_run:
            rtnval = self._build_result(
                status=VirtActionStatus.DRY_RUN,
                command=command, name=name,
            )
            return rtnval

        result = self._dispatch_virt(
            command=command, name=name, operation="revert_snapshot",
        )
        if result.status == VirtActionStatus.SUCCESS:
            advisory = (
                "domain {!r} reverted to snapshot {!r}; any state "
                "written after the snapshot was discarded".format(
                    domain, name,
                )
            )
            result.detail = advisory
        rtnval = result
        return rtnval

    def delete_snapshot(
        self,
        domain: str,
        name: str,
    ) -> VirtActionResult:
        """
            Delete the snapshot ``name`` of ``domain`` via
            ``virsh snapshot-delete``.

            Idempotent: returns ``SKIPPED`` when no snapshot with
            ``name`` exists for ``domain``.

            :param domain: Domain name.
            :param name: Snapshot name.

            :returns: The :class:`VirtActionResult`.
        """
        command = self._virsh(
            args=["snapshot-delete", domain, name],
        )
        if self._config.dry_run:
            rtnval = self._build_result(
                status=VirtActionStatus.DRY_RUN,
                command=command, name=name,
            )
            return rtnval

        if not self._snapshot_exists(domain=domain, name=name):
            rtnval = self._build_result(
                status=VirtActionStatus.SKIPPED,
                command=command, name=name,
                detail="snapshot {!r} not present on {!r}".format(
                    name, domain,
                ),
            )
            return rtnval

        rtnval = self._dispatch_virt(
            command=command, name=name, operation="delete_snapshot",
        )
        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _typed_config(self) -> VirtualizationManagerConfig:
        """
            Return ``self._config`` typed.  Falls back to a default
            config when the caller passed a plain
            :class:`FeatureConfig`.
        """
        if isinstance(self._config, VirtualizationManagerConfig):
            rtnval = self._config
        else:
            rtnval = VirtualizationManagerConfig(
                dry_run=self._config.dry_run,
                elevated=self._config.elevated,
            )
        return rtnval

    def _virsh(self, *, args: list[str]) -> list[str]:
        """
            Build the ``virsh`` argv prefix and append ``args``.

            :param args: Trailing argv segments.

            :returns: Full argv as a fresh list.
        """
        config = self._typed_config()
        prefix = [config.virsh_bin]
        if len(config.uri) > 0:
            prefix.append("--connect")
            prefix.append(config.uri)
        rtnval = prefix + list(args)
        return rtnval

    def _build_virt_install_argv(
        self,
        *,
        spec: VirtInstallSpec,
    ) -> list[str]:
        """
            Render the ``virt-install`` argv for ``spec``.
        """
        config = self._typed_config()
        argv: list[str] = [config.virt_install_bin]
        if len(config.uri) > 0:
            argv.append("--connect")
            argv.append(config.uri)

        argv.append("--name")
        argv.append(spec.name)
        argv.append("--memory")
        argv.append(str(spec.memory_mib))
        argv.append("--vcpus")
        argv.append(str(spec.vcpus))
        argv.append("--disk")
        argv.append(spec.disk)
        argv.append("--network")
        argv.append(spec.network)
        argv.append("--os-variant")
        argv.append(spec.os_variant)

        if spec.location is not None and len(spec.location) > 0:
            argv.append("--location")
            argv.append(spec.location)
        if spec.cdrom is not None and len(spec.cdrom) > 0:
            argv.append("--cdrom")
            argv.append(spec.cdrom)

        argv.append("--graphics")
        argv.append(spec.graphics)

        if spec.noautoconsole:
            argv.append("--noautoconsole")

        if spec.extra_args is not None and len(spec.extra_args) > 0:
            argv.extend(list(spec.extra_args))

        rtnval = argv
        return rtnval

    def _read_dominfo(self, *, name: str) -> Optional[DomainInfo]:
        """
            Probe ``virsh dominfo`` and parse the output.

            Returns ``None`` when the domain is not defined or the
            call fails.
        """
        command = self._virsh(args=["dominfo", name])
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: Optional[DomainInfo] = None
            return rtnval
        rtnval = _parse_virsh_dominfo(
            stdout=run_result.stdout, name=name,
        )
        return rtnval

    def _read_netinfo(self, *, name: str) -> Optional[NetworkInfo]:
        """
            Probe ``virsh net-info`` and parse the output.

            Returns ``None`` when the network is not defined or the
            call fails.
        """
        command = self._virsh(args=["net-info", name])
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: Optional[NetworkInfo] = None
            return rtnval
        rtnval = _parse_virsh_netinfo(
            stdout=run_result.stdout, name=name,
        )
        return rtnval

    def _read_poolinfo(self, *, name: str) -> Optional[StoragePoolInfo]:
        """
            Probe ``virsh pool-info`` and parse the output.

            Returns ``None`` when the pool is not defined or the call
            fails.
        """
        command = self._virsh(args=["pool-info", name])
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: Optional[StoragePoolInfo] = None
            return rtnval
        rtnval = _parse_virsh_poolinfo(
            stdout=run_result.stdout, name=name,
        )
        return rtnval

    def _volume_exists(self, *, pool: str, name: str) -> bool:
        """
            Return True when ``virsh vol-list --pool <pool>`` reports a
            volume named ``name``.
        """
        command = self._virsh(
            args=["vol-list", "--pool", pool, "--details"],
        )
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval = False
            return rtnval
        volumes = _parse_virsh_vol_list_details(stdout=run_result.stdout)
        rtnval = False
        for entry in volumes:
            if entry.name == name:
                rtnval = True
                break
        return rtnval

    def _snapshot_exists(self, *, domain: str, name: str) -> bool:
        """
            Return True when ``virsh snapshot-list <domain>`` reports a
            snapshot named ``name``.
        """
        command = self._virsh(args=["snapshot-list", domain])
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval = False
            return rtnval
        snapshots = _parse_virsh_snapshot_list_table(
            stdout=run_result.stdout, domain=domain,
        )
        rtnval = False
        for entry in snapshots:
            if entry.name == name:
                rtnval = True
                break
        return rtnval

    def _define_from_xml(
        self,
        *,
        xml_content: str,
        verb: str,
        operation: str,
    ) -> VirtActionResult:
        """
            Shared helper for ``virsh <verb> <path>`` flows.

            Writes ``xml_content`` to a host-side ``NamedTemporaryFile``
            and unlinks it in a ``finally`` block, regardless of the
            ``virsh`` outcome.

            :param xml_content: XML payload.
            :param verb: ``virsh`` verb (``"define"``, ``"net-define"``,
                         ``"pool-define"``).
            :param operation: Operation label for reporting.
        """
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".xml",
            delete=False,
            encoding="utf-8",
        )
        tmp_path = tmp.name
        try:
            tmp.write(xml_content)
            tmp.flush()
            tmp.close()

            command = self._virsh(args=[verb, tmp_path])

            if self._config.dry_run:
                rtnval = self._build_result(
                    status=VirtActionStatus.DRY_RUN,
                    command=command, name=None,
                )
            else:
                rtnval = self._dispatch_virt(
                    command=command, name=None, operation=operation,
                )
        finally:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        return rtnval

    def _dispatch_virt(
        self,
        *,
        command: list[str],
        name: Optional[str],
        operation: str,
    ) -> VirtActionResult:
        """
            Dispatch a write command and wrap the result with the
            virtualization-scope ``name`` field.
        """
        rtnval = self._dispatch(
            command=command,
            detail=name,
            operation=operation,
            name=name,
        )
        return rtnval

    def _build_result(
        self,
        *,
        status: VirtActionStatus,
        command: list[str],
        name: Optional[str],
        detail: Optional[str] = None,
        exit_code: Optional[int] = None,
        stdout: Optional[str] = None,
        stderr: Optional[str] = None,
    ) -> VirtActionResult:
        """
            Construct a :class:`VirtActionResult` without invoking the
            runner.
        """
        if detail is not None:
            detail_value: Optional[str] = detail
        else:
            detail_value = name
        rtnval = VirtActionResult(
            status=status,
            detail=detail_value,
            command=list(command),
            name=name,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )
        return rtnval


# ----------------------------------------------------------------------
# Module-level parsers / helpers
# ----------------------------------------------------------------------


def _is_running_state(*, state: str) -> bool:
    """
        Return True when ``state`` matches libvirt's ``running`` value.
    """
    text = state.strip().lower()
    rtnval = (text == _LIBVIRT_RUNNING)
    return rtnval


def _is_shutoff_state(*, state: str) -> bool:
    """
        Return True when ``state`` matches one of libvirt's ``shut off``
        spellings.
    """
    text = state.strip().lower()
    rtnval = text in (_LIBVIRT_SHUT_OFF, _LIBVIRT_SHUTOFF_COLLAPSED)
    return rtnval


def _parse_virsh_list_names(*, stdout: str) -> list[str]:
    """
        Parse ``virsh list --all --name`` (or ``virsh net-list --name``)
        output into a list of names.

        ``--name`` emits one name per line.  Blank lines are ignored.

        :param stdout: Raw stdout text.

        :returns: A list of names in the order reported.
    """
    rtnval: list[str] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if len(line) == 0:
            continue
        rtnval.append(line)
    return rtnval


def _parse_virsh_dominfo(
    *,
    stdout: str,
    name: str,
) -> Optional[DomainInfo]:
    """
        Parse ``virsh dominfo`` output into a :class:`DomainInfo`.

        Returns ``None`` when the output is empty.
    """
    stripped = stdout.strip()
    if len(stripped) == 0:
        rtnval: Optional[DomainInfo] = None
        return rtnval

    fields: dict[str, str] = {}
    for raw in stdout.splitlines():
        line = raw.rstrip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()

    if name is not None and len(name) > 0:
        info_name = name
    else:
        info_name = fields.get("Name", "")

    state_text = fields.get("State", "")
    uuid_value = fields.get("UUID", "") or None

    autostart_text = fields.get("Autostart", "").strip().lower()
    if autostart_text == "enable":
        autostart = True
    else:
        autostart = False

    persistent_text = fields.get("Persistent", "").strip().lower()
    if persistent_text == "yes":
        persistent = True
    else:
        persistent = False

    cpu_text = fields.get("CPU(s)", "")
    if len(cpu_text) == 0:
        vcpus: Optional[int] = None
    else:
        try:
            vcpus = int(cpu_text)
        except ValueError:
            vcpus = None

    max_memory_kib = _parse_memory_field(text=fields.get("Max memory", ""))
    used_memory_kib = _parse_memory_field(text=fields.get("Used memory", ""))

    rtnval = DomainInfo(
        name=info_name,
        uuid=uuid_value,
        state=state_text,
        autostart=autostart,
        persistent=persistent,
        max_memory_kib=max_memory_kib,
        used_memory_kib=used_memory_kib,
        vcpus=vcpus,
    )
    return rtnval


def _parse_memory_field(*, text: str) -> Optional[int]:
    """
        Parse a libvirt memory field of the form ``"1048576 KiB"`` and
        return the integer KiB value.  Returns ``None`` when unparseable.
    """
    if text is None:
        rtnval: Optional[int] = None
        return rtnval
    stripped = text.strip()
    if len(stripped) == 0:
        rtnval = None
        return rtnval
    tokens = stripped.split()
    if len(tokens) == 0:
        rtnval = None
        return rtnval
    try:
        rtnval = int(tokens[0])
    except ValueError:
        rtnval = None
    return rtnval


def _parse_virsh_netinfo(
    *,
    stdout: str,
    name: str,
) -> Optional[NetworkInfo]:
    """
        Parse ``virsh net-info`` output into a :class:`NetworkInfo`.

        Returns ``None`` when the output is empty.
    """
    stripped = stdout.strip()
    if len(stripped) == 0:
        rtnval: Optional[NetworkInfo] = None
        return rtnval

    fields: dict[str, str] = {}
    for raw in stdout.splitlines():
        line = raw.rstrip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()

    if name is not None and len(name) > 0:
        info_name = name
    else:
        info_name = fields.get("Name", "")

    uuid_value = fields.get("UUID", "") or None
    active_text = fields.get("Active", "").strip().lower()
    persistent_text = fields.get("Persistent", "").strip().lower()
    autostart_text = fields.get("Autostart", "").strip().lower()

    rtnval = NetworkInfo(
        name=info_name,
        uuid=uuid_value,
        active=(active_text == "yes"),
        persistent=(persistent_text == "yes"),
        autostart=(autostart_text == "yes"),
    )
    return rtnval


def _parse_virsh_poolinfo(
    *,
    stdout: str,
    name: str,
) -> Optional[StoragePoolInfo]:
    """
        Parse ``virsh pool-info`` output into a :class:`StoragePoolInfo`.

        Returns ``None`` when the output is empty.
    """
    stripped = stdout.strip()
    if len(stripped) == 0:
        rtnval: Optional[StoragePoolInfo] = None
        return rtnval

    fields: dict[str, str] = {}
    for raw in stdout.splitlines():
        line = raw.rstrip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()

    if name is not None and len(name) > 0:
        info_name = name
    else:
        info_name = fields.get("Name", "")

    uuid_value = fields.get("UUID", "") or None
    state_value = fields.get("State", "")
    autostart_text = fields.get("Autostart", "").strip().lower()

    capacity_bytes = _parse_size_field(text=fields.get("Capacity", ""))
    allocation_bytes = _parse_size_field(text=fields.get("Allocation", ""))
    available_bytes = _parse_size_field(text=fields.get("Available", ""))

    rtnval = StoragePoolInfo(
        name=info_name,
        uuid=uuid_value,
        state=state_value,
        autostart=(autostart_text == "yes"),
        capacity_bytes=capacity_bytes,
        allocation_bytes=allocation_bytes,
        available_bytes=available_bytes,
    )
    return rtnval


def _parse_size_field(*, text: str) -> Optional[int]:
    """
        Parse a libvirt size field such as ``"107.37 GB"`` or
        ``"1024 MiB"`` and return the value in bytes.  Returns ``None``
        when unparseable.
    """
    if text is None:
        rtnval: Optional[int] = None
        return rtnval
    stripped = text.strip()
    if len(stripped) == 0:
        rtnval = None
        return rtnval
    tokens = stripped.split()
    if len(tokens) < 1:
        rtnval = None
        return rtnval
    number_text = tokens[0]
    if len(tokens) >= 2:
        unit = tokens[1].lower()
    else:
        unit = "b"
    try:
        value = float(number_text)
    except ValueError:
        rtnval = None
        return rtnval
    multiplier = _size_multiplier(unit=unit)
    rtnval = int(value * multiplier)
    return rtnval


def _size_multiplier(*, unit: str) -> int:
    """
        Map a libvirt unit token (``"GiB"``, ``"GB"``, ``"MiB"``, ...)
        onto a byte multiplier.  Unknown units default to bytes.
    """
    text = unit.lower()
    if text in ("b", "bytes", "byte"):
        rtnval = 1
        return rtnval
    if text in ("kib",):
        rtnval = 1024
        return rtnval
    if text in ("kb",):
        rtnval = 1000
        return rtnval
    if text in ("mib",):
        rtnval = 1024 ** 2
        return rtnval
    if text in ("mb",):
        rtnval = 1000 ** 2
        return rtnval
    if text in ("gib",):
        rtnval = 1024 ** 3
        return rtnval
    if text in ("gb",):
        rtnval = 1000 ** 3
        return rtnval
    if text in ("tib",):
        rtnval = 1024 ** 4
        return rtnval
    if text in ("tb",):
        rtnval = 1000 ** 4
        return rtnval
    rtnval = 1
    return rtnval


def _parse_virsh_pool_list_details(
    *,
    stdout: str,
) -> list[StoragePoolInfo]:
    """
        Parse ``virsh pool-list --details`` tabular output.

        The default output shape is::

             Name      State    Autostart  Persistent   Capacity   Allocation  Available
            ----------------------------------------------------------------------------
             default   running  yes        yes          50.00 GiB  10.00 GiB   40.00 GiB

        Capacity / Allocation / Available are reported as
        ``"<number> <unit>"``.  Each contributes two whitespace-
        separated tokens, so a fixed-position split would lose them; we
        instead stitch back the tail tokens in groups of two.

        :param stdout: Raw stdout text.

        :returns: One :class:`StoragePoolInfo` per parsed row.
    """
    rtnval: list[StoragePoolInfo] = []
    saw_separator = False
    for raw in stdout.splitlines():
        line = raw.rstrip()
        if len(line.strip()) == 0:
            continue
        if line.lstrip().startswith("---"):
            saw_separator = True
            continue
        if not saw_separator:
            continue

        tokens = line.split()
        # Need at least: name, state, autostart, persistent, capacity_n,
        # capacity_u, allocation_n, allocation_u, available_n, available_u
        if len(tokens) < 10:
            continue

        pool_name = tokens[0]
        state = tokens[1]
        autostart_text = tokens[2].lower()
        # tokens[3] is persistent — captured but not exposed.
        capacity_text = " ".join(tokens[4:6])
        allocation_text = " ".join(tokens[6:8])
        available_text = " ".join(tokens[8:10])

        capacity_bytes = _parse_size_field(text=capacity_text)
        allocation_bytes = _parse_size_field(text=allocation_text)
        available_bytes = _parse_size_field(text=available_text)

        info = StoragePoolInfo(
            name=pool_name,
            uuid=None,
            state=state,
            autostart=(autostart_text == "yes"),
            capacity_bytes=capacity_bytes,
            allocation_bytes=allocation_bytes,
            available_bytes=available_bytes,
        )
        rtnval.append(info)
    return rtnval


def _parse_virsh_vol_list_details(
    *,
    stdout: str,
) -> list[StorageVolumeInfo]:
    """
        Parse ``virsh vol-list --details`` tabular output.

        The default output shape is::

             Name        Path                                         Type  Capacity   Allocation
            ---------------------------------------------------------------------------------------
             web.qcow2   /var/lib/libvirt/images/web.qcow2            file  20.00 GiB  5.00 GiB

        Path may contain spaces only when libvirt quotes the field;
        in practice paths are unquoted POSIX paths.  We split on
        whitespace and reconstruct the tail size fields.

        :param stdout: Raw stdout text.

        :returns: One :class:`StorageVolumeInfo` per parsed row.
    """
    rtnval: list[StorageVolumeInfo] = []
    saw_separator = False
    for raw in stdout.splitlines():
        line = raw.rstrip()
        if len(line.strip()) == 0:
            continue
        if line.lstrip().startswith("---"):
            saw_separator = True
            continue
        if not saw_separator:
            continue

        tokens = line.split()
        # Need at least: name, path, type, cap_n, cap_u, alloc_n, alloc_u
        if len(tokens) < 7:
            continue

        vol_name = tokens[0]
        path = tokens[1]
        # tokens[2] is type — captured but not exposed on this dataclass.
        capacity_text = " ".join(tokens[3:5])
        allocation_text = " ".join(tokens[5:7])

        capacity_bytes = _parse_size_field(text=capacity_text)
        allocation_bytes = _parse_size_field(text=allocation_text)

        info = StorageVolumeInfo(
            name=vol_name,
            path=path,
            capacity_bytes=capacity_bytes,
            allocation_bytes=allocation_bytes,
        )
        rtnval.append(info)
    return rtnval


def _parse_virsh_snapshot_list_table(
    *,
    stdout: str,
    domain: str,
) -> list[SnapshotInfo]:
    """
        Parse ``virsh snapshot-list <domain>`` tabular output (no
        ``--tree``).

        The default output shape is::

             Name        Creation Time              State
            ------------------------------------------------------------
             preupgrade  2024-01-01 12:00:00 +0000  shutoff

        :param stdout: Raw stdout text.
        :param domain: Owning domain name.

        :returns: One :class:`SnapshotInfo` per parsed row.
    """
    rtnval: list[SnapshotInfo] = []
    saw_separator = False
    for raw in stdout.splitlines():
        line = raw.rstrip()
        if len(line.strip()) == 0:
            continue
        if line.lstrip().startswith("---"):
            saw_separator = True
            continue
        if not saw_separator:
            continue

        tokens = line.split()
        if len(tokens) < 2:
            continue

        snap_name = tokens[0]
        state_token = tokens[-1]
        timestamp_tokens = tokens[1:-1]
        if len(timestamp_tokens) > 0:
            creation_time: Optional[str] = " ".join(timestamp_tokens)
        else:
            creation_time = None
        if state_token in ("-", ""):
            state_value: Optional[str] = None
        else:
            state_value = state_token
        info = SnapshotInfo(
            domain=domain,
            name=snap_name,
            parent=None,
            creation_time=creation_time,
            state=state_value,
        )
        rtnval.append(info)
    return rtnval


def _parse_virsh_snapshot_tree(
    *,
    stdout: str,
    domain: str,
) -> list[SnapshotInfo]:
    """
        Parse ``virsh snapshot-list <domain> --tree`` output.

        Tree output is a textual indented hierarchy:

        ::

            preupgrade
              |
              +- checkpoint
                  |
                  +- final

        The depth of the leading whitespace indicates the parent /
        child relationship; pipe and ``+-`` glyphs are ornament.  We
        track a stack of ``(indent, name)`` tuples to compute parent
        names.

        :param stdout: Raw stdout text.
        :param domain: Owning domain name.

        :returns: One :class:`SnapshotInfo` per snapshot.
    """
    rtnval: list[SnapshotInfo] = []
    parents: list[tuple[int, str]] = []
    for raw in stdout.splitlines():
        if len(raw.strip()) == 0:
            continue
        # Trim ornament: leading spaces, ``|``, ``+-`` glyphs.
        stripped = raw.rstrip()
        indent_count = len(stripped) - len(stripped.lstrip(" "))
        body = stripped.lstrip(" ")
        # Skip pure ornament lines (just ``|``).
        if body == "|":
            continue
        if body.startswith("+-"):
            body = body[2:].strip()
        if body.startswith("|"):
            body = body[1:].strip()
        if len(body) == 0:
            continue
        # Drop any trailing ornament tokens.
        snap_name = body.split()[0]

        # Pop stack entries that are at >= this indent — they are
        # siblings of the current node, not parents.
        while len(parents) > 0 and parents[-1][0] >= indent_count:
            parents.pop()

        if len(parents) == 0:
            parent_name: Optional[str] = None
        else:
            parent_name = parents[-1][1]

        info = SnapshotInfo(
            domain=domain,
            name=snap_name,
            parent=parent_name,
            creation_time=None,
            state=None,
        )
        rtnval.append(info)

        parents.append((indent_count, snap_name))
    return rtnval
