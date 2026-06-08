# ccai.sysman.linux.redhat.dnsservermanagement
#
# DnsServerManager wraps BIND (`named-checkconf`, `named-checkzone`,
# `rndc`), Unbound (`unbound-checkconf`, `unbound-control`), and
# dnsmasq (`dnsmasq --test`) through the ICommandRunner abstraction.
# Manages BIND zone files and zone declarations, Unbound forward /
# stub-zone drop-ins, and dnsmasq static-host / forwarder-domain
# drop-ins.  Service lifecycle (full restarts) delegates to an
# injected ServiceManager.

import os

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ccai.errors.exceptions import SemanticError

from ccai.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


# ----------------------------------------------------------------------
# Module-level command constants
# ----------------------------------------------------------------------

_CAT = "cat"
_TEE = "tee"
_RM = "rm"
_TEST = "test"
_COMMAND = "command"

_RNDC = "rndc"
_NAMED_CHECKCONF = "named-checkconf"
_NAMED_CHECKZONE = "named-checkzone"

_UNBOUND_CHECKCONF = "unbound-checkconf"
_UNBOUND_CONTROL = "unbound-control"

_DNSMASQ = "dnsmasq"

_BIND_BIN = "named"
_UNBOUND_BIN = "unbound"
_DNSMASQ_BIN = "dnsmasq"

_DEFAULT_BIND_ZONES_DIR = "/var/named"
_DEFAULT_BIND_CONF_DROPIN_DIR = "/etc/named/zones.d"
_DEFAULT_UNBOUND_DROPIN_DIR = "/etc/unbound/conf.d"
_DEFAULT_UNBOUND_DROPIN_BASENAME = "ccai.conf"
_DEFAULT_DNSMASQ_DROPIN_DIR = "/etc/dnsmasq.d"
_DEFAULT_DNSMASQ_DROPIN_BASENAME = "ccai.conf"

_BIND_SERVICE_NAME = "named"
_UNBOUND_SERVICE_NAME = "unbound"
_DNSMASQ_SERVICE_NAME = "dnsmasq"


DnsActionStatus = ActionStatus


# ----------------------------------------------------------------------
# Enums
# ----------------------------------------------------------------------


class DnsServerBackend(Enum):
    """
        Supported DNS-server backend families on Red Hat Linux.
    """
    BIND    = "bind"
    UNBOUND = "unbound"
    DNSMASQ = "dnsmasq"


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------


@dataclass
class DnsServerManagerConfig(FeatureConfig):
    """
        Configuration for :class:`DnsServerManager`.

        Inherits ``dry_run`` and ``elevated`` from
        :class:`FeatureConfig`.

        :param backend: Override auto-detection of the backend.
                        ``None`` triggers ``command -v`` probing for
                        ``named``, ``unbound``, and ``dnsmasq``
                        (in that order) on the first operation that
                        requires backend dispatch.
        :param bind_zones_dir: Directory holding BIND zone-data files.
                               Defaults to ``/var/named``.
        :param bind_conf_dropin_dir: Directory holding BIND
                                     ``zone "<name>" {}`` drop-in
                                     declarations.  Defaults to
                                     ``/etc/named/zones.d``.
        :param unbound_dropin_dir: Directory holding Unbound
                                   drop-in configs.  Defaults to
                                   ``/etc/unbound/conf.d``.
        :param unbound_dropin_basename: File name of the
                                        managed Unbound drop-in.
                                        Defaults to ``ccai.conf``.
        :param dnsmasq_dropin_dir: Directory holding dnsmasq drop-in
                                   configs.  Defaults to
                                   ``/etc/dnsmasq.d``.
        :param dnsmasq_dropin_basename: File name of the managed
                                        dnsmasq drop-in.  Defaults
                                        to ``ccai.conf``.
    """
    backend: Optional[DnsServerBackend] = None
    bind_zones_dir: str = _DEFAULT_BIND_ZONES_DIR
    bind_conf_dropin_dir: str = _DEFAULT_BIND_CONF_DROPIN_DIR
    unbound_dropin_dir: str = _DEFAULT_UNBOUND_DROPIN_DIR
    unbound_dropin_basename: str = _DEFAULT_UNBOUND_DROPIN_BASENAME
    dnsmasq_dropin_dir: str = _DEFAULT_DNSMASQ_DROPIN_DIR
    dnsmasq_dropin_basename: str = _DEFAULT_DNSMASQ_DROPIN_BASENAME


# ----------------------------------------------------------------------
# Data models
# ----------------------------------------------------------------------


@dataclass
class DnsActionResult(FeatureActionResult):
    """
        Outcome of a :class:`DnsServerManager` write verb.

        :param zone: The zone name the action targeted, when
                     applicable.
    """
    zone: Optional[str] = None


@dataclass
class ZoneEntry:
    """
        A managed BIND zone declaration.

        :param name: Zone name (e.g. ``example.com``).
        :param type: BIND zone-type token (``master``, ``slave``,
                     ``forward``, ``stub``).
        :param file_path: Absolute path to the zone-data file when
                          declared, or empty string when not
                          applicable.
        :param file_content: Contents of the zone-data file when
                             read; ``None`` when not loaded or not
                             applicable.
        :param source_conf: Absolute path of the drop-in file the
                            zone declaration was read from.
    """
    name: str
    type: str
    file_path: str
    file_content: Optional[str] = None
    source_conf: Optional[str] = None


@dataclass
class DnsValidateResult:
    """
        Outcome of :meth:`DnsServerManager.validate`.

        :param backend: The backend that was validated.
        :param ok: ``True`` when the validator (and every
                   per-zone check) reported success.
        :param exit_code: Exit code of the primary validator.
        :param stderr: Captured stderr from the primary validator.
        :param zone_check_results: For BIND, one tuple per zone
                                   subjected to
                                   ``named-checkzone``, in the
                                   form ``(zone_name, ok, stderr)``.
                                   Empty for other backends.
    """
    backend: DnsServerBackend
    ok: bool
    exit_code: int
    stderr: str
    zone_check_results: list = field(default_factory=list)


@dataclass
class DnsServerStatus:
    """
        Outcome of :meth:`DnsServerManager.status`.

        :param running: ``True`` when the daemon is reported active.
        :param version: Version string when surfaced; ``None``
                        otherwise.
        :param raw: Captured stdout from the status command.
    """
    running: bool
    version: Optional[str]
    raw: str


# ----------------------------------------------------------------------
# Exceptions
# ----------------------------------------------------------------------


class DnsBackendNotAvailableError(SemanticError):
    """
        Raised when no supported DNS-server backend
        (``named`` / ``unbound`` / ``dnsmasq``) is installed and
        the configuration did not pin one explicitly.
    """


class DnsBackendMismatchError(SemanticError):
    """
        Raised when a backend-specific verb is invoked against the
        wrong backend (e.g. ``define_zone`` while the active
        backend is ``UNBOUND``).
    """


# ----------------------------------------------------------------------
# DnsServerManager
# ----------------------------------------------------------------------


class DnsServerManager(BaseFeatureManager):
    """
        Facade for DNS-server management on Red Hat Linux.

        Dispatches to BIND, Unbound, or dnsmasq depending on the
        active backend.  Service-lifecycle restarts delegate to an
        injected ``service_manager`` (the package's
        ``ServiceManager``) so that this feature does not duplicate
        the service-management surface.

        Backend auto-detection is deferred until the first
        operation that requires a backend so that constructing the
        manager in dry-run mode never raises.
    """

    result_class = DnsActionResult
    status_class = DnsActionStatus
    feature_name = "dns-server-management"

    def __init__(
        self,
        runner,
        config: Optional[DnsServerManagerConfig] = None,
        service_manager=None,
    ) -> None:
        """
            :param runner: The command runner used for every
                           dispatch.
            :param config: Manager configuration.  Defaults to a
                           fresh :class:`DnsServerManagerConfig`.
            :param service_manager: Optional ``ServiceManager``
                                    instance.  Required by
                                    :meth:`reload` (for dnsmasq) and
                                    :meth:`restart` (for every
                                    backend).
        """
        if config is None:
            config = DnsServerManagerConfig()

        super().__init__(runner=runner, config=config)
        self._service_manager = service_manager
        self._backend_resolved: Optional[DnsServerBackend] = config.backend
        return

    @property
    def service_manager(self):
        """
            The injected service manager, or ``None``.
        """
        return self._service_manager

    # ------------------------------------------------------------------
    # Backend resolution
    # ------------------------------------------------------------------

    def _typed_config(self) -> DnsServerManagerConfig:
        """
            Return the manager's config typed as
            :class:`DnsServerManagerConfig`.

            :returns: The active config typed.
        """
        if isinstance(self._config, DnsServerManagerConfig):
            rtnval = self._config
            return rtnval
        rtnval = DnsServerManagerConfig(
            dry_run=self._config.dry_run,
            elevated=self._config.elevated,
        )
        return rtnval

    def _ensure_backend(self) -> DnsServerBackend:
        """
            Resolve the active backend, probing for ``named`` /
            ``unbound`` / ``dnsmasq`` via ``command -v`` when no
            explicit backend was configured.

            :returns: The resolved :class:`DnsServerBackend`.

            :raises DnsBackendNotAvailableError: When auto-detect
                                                 finds no candidate.
        """
        if self._backend_resolved is not None:
            rtnval = self._backend_resolved
            return rtnval

        for candidate_bin, candidate_backend in (
            (_BIND_BIN, DnsServerBackend.BIND),
            (_UNBOUND_BIN, DnsServerBackend.UNBOUND),
            (_DNSMASQ_BIN, DnsServerBackend.DNSMASQ),
        ):
            command = [_COMMAND, "-v", candidate_bin]
            run_result = self._runner.run(list(command), elevated=False)
            if run_result.exit_code == 0:
                self._backend_resolved = candidate_backend
                rtnval = candidate_backend
                return rtnval

        err_msg_lines = ["No supported DNS-server backend is installed."]
        err_msg_lines.append("    PROBED: named, unbound, dnsmasq")
        err_msg = os.linesep.join(err_msg_lines)
        raise DnsBackendNotAvailableError(err_msg)

    def _require_backend(
        self,
        *,
        expected: DnsServerBackend,
        operation: str,
    ) -> None:
        """
            Validate that the active backend matches ``expected``;
            raise :class:`DnsBackendMismatchError` when it does not.

            :param expected: The backend the caller requires.
            :param operation: The verb name to embed in the error.

            :raises DnsBackendMismatchError: When the active backend
                                             differs from
                                             ``expected``.
        """
        active = self._ensure_backend()
        if active == expected:
            return

        err_msg_lines = [
            "DNS verb '{}' requires backend {}.".format(
                operation, expected.value,
            ),
        ]
        err_msg_lines.append("    ACTIVE_BACKEND: {}".format(active.value))
        err_msg = os.linesep.join(err_msg_lines)
        raise DnsBackendMismatchError(err_msg)

    # ------------------------------------------------------------------
    # File I/O helpers
    # ------------------------------------------------------------------

    def _read_file(self, *, path: str) -> Optional[str]:
        """
            Read a file via ``cat``.

            :param path: Absolute path to read.

            :returns: File contents on success; ``None`` when the
                      file cannot be read.
        """
        command = [_CAT, path]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: Optional[str] = None
            return rtnval
        rtnval = run_result.stdout
        return rtnval

    def _write_file(self, *, path: str, content: str) -> "FeatureActionResult":
        """
            Write a file via ``tee``.

            :param path: Absolute path to write.
            :param content: New file content.

            :returns: A :class:`FeatureActionResult` populated with
                      the runner's outcome.
        """
        command = [_TEE, path]
        run_result = self._runner.run(
            list(command),
            elevated=self._config.elevated,
            stdin=content,
        )
        mapped_status = (
            DnsActionStatus.SUCCESS
            if run_result.exit_code == 0
            else DnsActionStatus.FAILED
        )
        rtnval = DnsActionResult(
            status=mapped_status,
            detail=path,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(command),
        )
        return rtnval

    def _path_exists(self, *, path: str) -> bool:
        """
            Probe a path's existence via ``test -e``.

            :param path: Absolute path to probe.

            :returns: True when ``test -e`` reports the path.
        """
        command = [_TEST, "-e", path]
        run_result = self._runner.run(list(command), elevated=False)
        rtnval = run_result.exit_code == 0
        return rtnval

    def _rm_file(self, *, path: str) -> "FeatureActionResult":
        """
            Remove a file via ``rm -f``.

            :param path: Absolute path.

            :returns: A :class:`FeatureActionResult`.
        """
        command = [_RM, "-f", path]
        run_result = self._runner.run(
            list(command),
            elevated=self._config.elevated,
        )
        mapped_status = (
            DnsActionStatus.SUCCESS
            if run_result.exit_code == 0
            else DnsActionStatus.FAILED
        )
        rtnval = DnsActionResult(
            status=mapped_status,
            detail=path,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(command),
        )
        return rtnval

    # ------------------------------------------------------------------
    # Validate / reload / restart / status
    # ------------------------------------------------------------------

    def validate(
        self,
        *,
        zone_files: Optional[list] = None,
    ) -> DnsValidateResult:
        """
            Validate the current DNS-server configuration.

            For BIND, dispatches ``named-checkconf`` and (when
            ``zone_files`` is supplied) ``named-checkzone`` per
            ``(zone_name, file_path)`` tuple.  For Unbound,
            dispatches ``unbound-checkconf``.  For dnsmasq,
            dispatches ``dnsmasq --test``.

            :param zone_files: Optional list of
                               ``(zone_name, file_path)`` tuples
                               to fold into BIND validation.

            :returns: The :class:`DnsValidateResult`.
        """
        backend = self._ensure_backend()

        if backend == DnsServerBackend.BIND:
            rtnval = self._validate_bind(zone_files=zone_files)
            return rtnval
        if backend == DnsServerBackend.UNBOUND:
            rtnval = self._validate_unbound()
            return rtnval
        rtnval = self._validate_dnsmasq()
        return rtnval

    def _validate_bind(
        self,
        *,
        zone_files: Optional[list],
    ) -> DnsValidateResult:
        """
            Run ``named-checkconf`` and (optionally) per-zone
            ``named-checkzone`` checks.
        """
        command = [_NAMED_CHECKCONF]
        run_result = self._runner.run(list(command), elevated=False)
        primary_ok = run_result.exit_code == 0
        primary_stderr = run_result.stderr

        zone_results: list = []
        every_zone_ok = True
        if zone_files is not None and len(zone_files) > 0:
            for zone_pair in zone_files:
                zone_name, zone_path = zone_pair
                check_command = [
                    _NAMED_CHECKZONE, zone_name, zone_path,
                ]
                check_result = self._runner.run(
                    list(check_command), elevated=False,
                )
                check_ok = check_result.exit_code == 0
                if not check_ok:
                    every_zone_ok = False
                zone_results.append(
                    (zone_name, check_ok, check_result.stderr),
                )

        overall_ok = primary_ok and every_zone_ok
        rtnval = DnsValidateResult(
            backend=DnsServerBackend.BIND,
            ok=overall_ok,
            exit_code=run_result.exit_code,
            stderr=primary_stderr,
            zone_check_results=zone_results,
        )
        return rtnval

    def _validate_unbound(self) -> DnsValidateResult:
        """
            Run ``unbound-checkconf``.
        """
        command = [_UNBOUND_CHECKCONF]
        run_result = self._runner.run(list(command), elevated=False)
        rtnval = DnsValidateResult(
            backend=DnsServerBackend.UNBOUND,
            ok=run_result.exit_code == 0,
            exit_code=run_result.exit_code,
            stderr=run_result.stderr,
            zone_check_results=[],
        )
        return rtnval

    def _validate_dnsmasq(self) -> DnsValidateResult:
        """
            Run ``dnsmasq --test``.
        """
        command = [_DNSMASQ, "--test"]
        run_result = self._runner.run(list(command), elevated=False)
        rtnval = DnsValidateResult(
            backend=DnsServerBackend.DNSMASQ,
            ok=run_result.exit_code == 0,
            exit_code=run_result.exit_code,
            stderr=run_result.stderr,
            zone_check_results=[],
        )
        return rtnval

    def reload(
        self,
        *,
        zone_files: Optional[list] = None,
    ) -> DnsActionResult:
        """
            Reload the DNS server.

            Validates the configuration first; on validation
            failure, returns ``FAILED`` without dispatching the
            reload.  For BIND, dispatches ``rndc reload``.  For
            Unbound, dispatches ``unbound-control reload``.  For
            dnsmasq (which has no signal-free reload), delegates a
            restart through ``service_manager``.

            :param zone_files: Optional list of
                               ``(zone_name, file_path)`` tuples to
                               fold into BIND validation.

            :returns: The :class:`DnsActionResult`.
        """
        backend = self._ensure_backend()

        if self._config.dry_run:
            rtnval = DnsActionResult(
                status=DnsActionStatus.DRY_RUN,
                detail="reload",
                command=_reload_command(backend=backend),
                zone=None,
            )
            return rtnval

        validate_result = self.validate(zone_files=zone_files)
        if not validate_result.ok:
            rtnval = DnsActionResult(
                status=DnsActionStatus.FAILED,
                detail="validation_failed",
                exit_code=validate_result.exit_code,
                stderr=validate_result.stderr,
                command=_reload_command(backend=backend),
                zone=None,
            )
            return rtnval

        if backend == DnsServerBackend.BIND:
            command = [_RNDC, "reload"]
            run_result = self._runner.run(
                list(command),
                elevated=self._config.elevated,
            )
            rtnval = _wrap_run_result(
                run_result=run_result, command=command, zone=None,
            )
            return rtnval

        if backend == DnsServerBackend.UNBOUND:
            command = [_UNBOUND_CONTROL, "reload"]
            run_result = self._runner.run(
                list(command),
                elevated=self._config.elevated,
            )
            rtnval = _wrap_run_result(
                run_result=run_result, command=command, zone=None,
            )
            return rtnval

        # dnsmasq has no runtime reload — delegate restart.
        rtnval = self._delegate_service_restart(
            service_name=_DNSMASQ_SERVICE_NAME,
        )
        return rtnval

    def restart(self) -> DnsActionResult:
        """
            Restart the DNS-server daemon by delegating to the
            injected service manager.

            :returns: The :class:`DnsActionResult`.

            :raises DnsBackendNotAvailableError: When backend
                                                 auto-detect fails.
        """
        backend = self._ensure_backend()
        service_name = _service_name_for_backend(backend=backend)

        if self._config.dry_run:
            rtnval = DnsActionResult(
                status=DnsActionStatus.DRY_RUN,
                detail="restart",
                command=["systemctl", "restart", service_name],
                zone=None,
            )
            return rtnval

        rtnval = self._delegate_service_restart(service_name=service_name)
        return rtnval

    def _delegate_service_restart(
        self,
        *,
        service_name: str,
    ) -> DnsActionResult:
        """
            Delegate a daemon restart through the injected
            ``service_manager``.

            :param service_name: The systemd unit name.

            :returns: The :class:`DnsActionResult`.
        """
        if self._service_manager is None:
            rtnval = DnsActionResult(
                status=DnsActionStatus.FAILED,
                detail="service_manager_not_injected",
                command=["systemctl", "restart", service_name],
                zone=None,
                stderr="service_manager_not_injected",
            )
            return rtnval

        delegated_result = self._service_manager.restart(service_name)
        delegated_status = _coerce_action_status(
            value=getattr(delegated_result, "status", None),
        )
        rtnval = DnsActionResult(
            status=delegated_status,
            detail="restart:{}".format(service_name),
            exit_code=getattr(delegated_result, "exit_code", None),
            stdout=getattr(delegated_result, "stdout", None),
            stderr=getattr(delegated_result, "stderr", None),
            command=getattr(
                delegated_result,
                "command",
                ["systemctl", "restart", service_name],
            ),
            zone=None,
        )
        return rtnval

    def status(self) -> DnsServerStatus:
        """
            Query the DNS-server daemon status.

            For BIND, dispatches ``rndc status``.  For Unbound,
            dispatches ``unbound-control status``.  For dnsmasq,
            delegates ``is_active`` through ``service_manager``.

            :returns: The :class:`DnsServerStatus`.
        """
        backend = self._ensure_backend()

        if backend == DnsServerBackend.BIND:
            rtnval = self._status_bind()
            return rtnval
        if backend == DnsServerBackend.UNBOUND:
            rtnval = self._status_unbound()
            return rtnval
        rtnval = self._status_dnsmasq()
        return rtnval

    def _status_bind(self) -> DnsServerStatus:
        """
            Run ``rndc status`` and parse the output.
        """
        command = [_RNDC, "status"]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval = DnsServerStatus(
                running=False, version=None, raw=run_result.stdout,
            )
            return rtnval
        version_token = _parse_version_from_rndc_status(
            text=run_result.stdout,
        )
        rtnval = DnsServerStatus(
            running=True, version=version_token, raw=run_result.stdout,
        )
        return rtnval

    def _status_unbound(self) -> DnsServerStatus:
        """
            Run ``unbound-control status`` and parse the output.
        """
        command = [_UNBOUND_CONTROL, "status"]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval = DnsServerStatus(
                running=False, version=None, raw=run_result.stdout,
            )
            return rtnval
        version_token = _parse_version_from_unbound_status(
            text=run_result.stdout,
        )
        rtnval = DnsServerStatus(
            running=True, version=version_token, raw=run_result.stdout,
        )
        return rtnval

    def _status_dnsmasq(self) -> DnsServerStatus:
        """
            Delegate ``is_active`` through the injected service
            manager.
        """
        if self._service_manager is None:
            rtnval = DnsServerStatus(
                running=False, version=None, raw="",
            )
            return rtnval

        is_active_callable = getattr(
            self._service_manager, "is_active", None,
        )
        if is_active_callable is None:
            rtnval = DnsServerStatus(
                running=False, version=None, raw="",
            )
            return rtnval

        is_active_value = is_active_callable(_DNSMASQ_SERVICE_NAME)
        running_flag = bool(is_active_value)
        rtnval = DnsServerStatus(
            running=running_flag, version=None, raw="",
        )
        return rtnval

    # ------------------------------------------------------------------
    # BIND zone management
    # ------------------------------------------------------------------

    def write_zone_file(
        self,
        *,
        zone: str,
        content: str,
    ) -> DnsActionResult:
        """
            Write a BIND zone-data file at
            ``{bind_zones_dir}/<zone>.db``.

            Idempotent: when the existing file content already
            matches ``content``, returns ``SKIPPED`` without
            dispatching ``tee``.

            :param zone: The zone name.
            :param content: The new zone-data file body.

            :returns: The :class:`DnsActionResult`.
        """
        self._require_backend(
            expected=DnsServerBackend.BIND,
            operation="write_zone_file",
        )
        cfg = self._typed_config()
        zone_file_path = "{}/{}.db".format(cfg.bind_zones_dir, zone)
        command = [_TEE, zone_file_path]

        if self._config.dry_run:
            rtnval = DnsActionResult(
                status=DnsActionStatus.DRY_RUN,
                detail=zone_file_path,
                command=list(command),
                zone=zone,
            )
            return rtnval

        existing = self._read_file(path=zone_file_path)
        if existing is not None and existing == content:
            rtnval = DnsActionResult(
                status=DnsActionStatus.SKIPPED,
                detail="zone_file_unchanged",
                command=list(command),
                zone=zone,
            )
            return rtnval

        write_result = self._write_file(
            path=zone_file_path, content=content,
        )
        rtnval = DnsActionResult(
            status=write_result.status,
            detail=zone_file_path,
            exit_code=write_result.exit_code,
            stdout=write_result.stdout,
            stderr=write_result.stderr,
            command=write_result.command,
            zone=zone,
        )
        return rtnval

    def define_zone(
        self,
        *,
        entry: ZoneEntry,
    ) -> DnsActionResult:
        """
            Declare a BIND zone in a drop-in file under
            ``bind_conf_dropin_dir``.

            Renders the canonical
            ``zone "<name>" { type <type>; file "<file_path>"; };``
            block; idempotent against the existing drop-in
            content; calls :meth:`reload` on a successful write.

            :param entry: The :class:`ZoneEntry` describing the
                          zone declaration.

            :returns: The :class:`DnsActionResult`.
        """
        self._require_backend(
            expected=DnsServerBackend.BIND,
            operation="define_zone",
        )
        cfg = self._typed_config()
        dropin_path = "{}/{}.conf".format(
            cfg.bind_conf_dropin_dir, entry.name,
        )
        command = [_TEE, dropin_path]
        rendered_block = _render_bind_zone_block(entry=entry)

        if self._config.dry_run:
            rtnval = DnsActionResult(
                status=DnsActionStatus.DRY_RUN,
                detail=dropin_path,
                command=list(command),
                zone=entry.name,
            )
            return rtnval

        existing = self._read_file(path=dropin_path)
        if existing is not None and existing == rendered_block:
            rtnval = DnsActionResult(
                status=DnsActionStatus.SKIPPED,
                detail="zone_definition_unchanged",
                command=list(command),
                zone=entry.name,
            )
            return rtnval

        write_result = self._write_file(
            path=dropin_path, content=rendered_block,
        )
        if write_result.status != DnsActionStatus.SUCCESS:
            rtnval = DnsActionResult(
                status=write_result.status,
                detail=dropin_path,
                exit_code=write_result.exit_code,
                stdout=write_result.stdout,
                stderr=write_result.stderr,
                command=write_result.command,
                zone=entry.name,
            )
            return rtnval

        # Trigger a reload so the new declaration becomes live.
        zone_files: list = []
        if entry.file_path is not None and len(entry.file_path) > 0:
            zone_files.append((entry.name, entry.file_path))
        reload_result = self.reload(zone_files=zone_files)
        rtnval = DnsActionResult(
            status=reload_result.status,
            detail=dropin_path,
            exit_code=reload_result.exit_code,
            stdout=reload_result.stdout,
            stderr=reload_result.stderr,
            command=reload_result.command,
            zone=entry.name,
        )
        return rtnval

    def remove_zone(self, *, name: str) -> DnsActionResult:
        """
            Remove a BIND zone declaration and its zone-data file.

            Strips the matching drop-in file under
            ``bind_conf_dropin_dir`` and ``rm -f``s the
            corresponding ``{bind_zones_dir}/<name>.db``.  When at
            least one of the two artefacts was present, calls
            :meth:`reload`; when both were absent, returns
            ``SKIPPED``.

            :param name: The zone name to remove.

            :returns: The :class:`DnsActionResult`.
        """
        self._require_backend(
            expected=DnsServerBackend.BIND,
            operation="remove_zone",
        )
        cfg = self._typed_config()
        dropin_path = "{}/{}.conf".format(
            cfg.bind_conf_dropin_dir, name,
        )
        zone_file_path = "{}/{}.db".format(cfg.bind_zones_dir, name)
        command = [_RM, "-f", zone_file_path]

        if self._config.dry_run:
            rtnval = DnsActionResult(
                status=DnsActionStatus.DRY_RUN,
                detail=name,
                command=list(command),
                zone=name,
            )
            return rtnval

        dropin_present = self._path_exists(path=dropin_path)
        zone_file_present = self._path_exists(path=zone_file_path)

        if not dropin_present and not zone_file_present:
            rtnval = DnsActionResult(
                status=DnsActionStatus.SKIPPED,
                detail="zone_already_absent",
                command=list(command),
                zone=name,
            )
            return rtnval

        if dropin_present:
            dropin_rm = self._rm_file(path=dropin_path)
            if dropin_rm.status != DnsActionStatus.SUCCESS:
                rtnval = DnsActionResult(
                    status=DnsActionStatus.FAILED,
                    detail=dropin_path,
                    exit_code=dropin_rm.exit_code,
                    stdout=dropin_rm.stdout,
                    stderr=dropin_rm.stderr,
                    command=dropin_rm.command,
                    zone=name,
                )
                return rtnval

        if zone_file_present:
            data_rm = self._rm_file(path=zone_file_path)
            if data_rm.status != DnsActionStatus.SUCCESS:
                rtnval = DnsActionResult(
                    status=DnsActionStatus.FAILED,
                    detail=zone_file_path,
                    exit_code=data_rm.exit_code,
                    stdout=data_rm.stdout,
                    stderr=data_rm.stderr,
                    command=data_rm.command,
                    zone=name,
                )
                return rtnval

        reload_result = self.reload()
        rtnval = DnsActionResult(
            status=reload_result.status,
            detail=name,
            exit_code=reload_result.exit_code,
            stdout=reload_result.stdout,
            stderr=reload_result.stderr,
            command=reload_result.command,
            zone=name,
        )
        return rtnval

    def query_zone(self, *, name: str) -> Optional[ZoneEntry]:
        """
            Return the :class:`ZoneEntry` for ``name`` parsed from
            its drop-in declaration, or ``None`` when no drop-in
            exists.

            :param name: The zone name.

            :returns: The :class:`ZoneEntry`, or ``None``.
        """
        self._require_backend(
            expected=DnsServerBackend.BIND,
            operation="query_zone",
        )
        cfg = self._typed_config()
        dropin_path = "{}/{}.conf".format(
            cfg.bind_conf_dropin_dir, name,
        )
        dropin_content = self._read_file(path=dropin_path)
        if dropin_content is None:
            rtnval: Optional[ZoneEntry] = None
            return rtnval

        parsed_type, parsed_file = _parse_bind_zone_block(
            content=dropin_content,
        )
        file_body: Optional[str] = None
        if parsed_file is not None and len(parsed_file) > 0:
            file_body = self._read_file(path=parsed_file)

        rtnval = ZoneEntry(
            name=name,
            type=parsed_type if parsed_type is not None else "",
            file_path=parsed_file if parsed_file is not None else "",
            file_content=file_body,
            source_conf=dropin_path,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Unbound forward / stub zones
    # ------------------------------------------------------------------

    def add_forward_zone(
        self,
        *,
        name: str,
        forwarders: list,
    ) -> DnsActionResult:
        """
            Upsert a ``forward-zone:`` block in the Unbound
            drop-in.  Idempotent.

            :param name: The forward-zone name (e.g.
                         ``corp.example.com.``).
            :param forwarders: List of forwarder IP-address
                               strings.

            :returns: The :class:`DnsActionResult`.
        """
        self._require_backend(
            expected=DnsServerBackend.UNBOUND,
            operation="add_forward_zone",
        )
        rtnval = self._upsert_unbound_block(
            block_kind="forward-zone",
            name=name,
            addresses=forwarders,
            address_directive="forward-addr",
        )
        return rtnval

    def add_stub_zone(
        self,
        *,
        name: str,
        addresses: list,
    ) -> DnsActionResult:
        """
            Upsert a ``stub-zone:`` block in the Unbound drop-in.
            Idempotent.

            :param name: The stub-zone name.
            :param addresses: List of authoritative-server
                              IP-address strings.

            :returns: The :class:`DnsActionResult`.
        """
        self._require_backend(
            expected=DnsServerBackend.UNBOUND,
            operation="add_stub_zone",
        )
        rtnval = self._upsert_unbound_block(
            block_kind="stub-zone",
            name=name,
            addresses=addresses,
            address_directive="stub-addr",
        )
        return rtnval

    def remove_forward_zone(self, *, name: str) -> DnsActionResult:
        """
            Strip the matching ``forward-zone:`` block from the
            Unbound drop-in.  Returns ``SKIPPED`` when absent.

            :param name: The forward-zone name to remove.

            :returns: The :class:`DnsActionResult`.
        """
        self._require_backend(
            expected=DnsServerBackend.UNBOUND,
            operation="remove_forward_zone",
        )
        rtnval = self._remove_unbound_block(
            block_kind="forward-zone", name=name,
        )
        return rtnval

    def remove_stub_zone(self, *, name: str) -> DnsActionResult:
        """
            Strip the matching ``stub-zone:`` block from the
            Unbound drop-in.  Returns ``SKIPPED`` when absent.

            :param name: The stub-zone name to remove.

            :returns: The :class:`DnsActionResult`.
        """
        self._require_backend(
            expected=DnsServerBackend.UNBOUND,
            operation="remove_stub_zone",
        )
        rtnval = self._remove_unbound_block(
            block_kind="stub-zone", name=name,
        )
        return rtnval

    def _upsert_unbound_block(
        self,
        *,
        block_kind: str,
        name: str,
        addresses: list,
        address_directive: str,
    ) -> DnsActionResult:
        """
            Upsert a block of type ``block_kind`` keyed by
            ``name`` in the Unbound drop-in.

            :param block_kind: Either ``"forward-zone"`` or
                               ``"stub-zone"``.
            :param name: The zone name.
            :param addresses: Forwarder / stub-target addresses.
            :param address_directive: The per-address directive
                                      (``forward-addr`` or
                                      ``stub-addr``).

            :returns: The :class:`DnsActionResult`.
        """
        cfg = self._typed_config()
        dropin_path = "{}/{}".format(
            cfg.unbound_dropin_dir, cfg.unbound_dropin_basename,
        )
        command = [_TEE, dropin_path]
        rendered_block = _render_unbound_block(
            block_kind=block_kind,
            name=name,
            addresses=addresses,
            address_directive=address_directive,
        )

        if self._config.dry_run:
            rtnval = DnsActionResult(
                status=DnsActionStatus.DRY_RUN,
                detail=dropin_path,
                command=list(command),
                zone=name,
            )
            return rtnval

        existing = self._read_file(path=dropin_path)
        existing_text = "" if existing is None else existing
        new_content = _upsert_unbound_block_text(
            content=existing_text,
            block_kind=block_kind,
            name=name,
            block_body=rendered_block,
        )
        if existing is not None and new_content == existing:
            rtnval = DnsActionResult(
                status=DnsActionStatus.SKIPPED,
                detail="block_unchanged",
                command=list(command),
                zone=name,
            )
            return rtnval

        write_result = self._write_file(
            path=dropin_path, content=new_content,
        )
        if write_result.status != DnsActionStatus.SUCCESS:
            rtnval = DnsActionResult(
                status=write_result.status,
                detail=dropin_path,
                exit_code=write_result.exit_code,
                stdout=write_result.stdout,
                stderr=write_result.stderr,
                command=write_result.command,
                zone=name,
            )
            return rtnval

        reload_result = self.reload()
        rtnval = DnsActionResult(
            status=reload_result.status,
            detail=dropin_path,
            exit_code=reload_result.exit_code,
            stdout=reload_result.stdout,
            stderr=reload_result.stderr,
            command=reload_result.command,
            zone=name,
        )
        return rtnval

    def _remove_unbound_block(
        self,
        *,
        block_kind: str,
        name: str,
    ) -> DnsActionResult:
        """
            Strip a block of type ``block_kind`` keyed by ``name``
            from the Unbound drop-in.

            :param block_kind: Either ``"forward-zone"`` or
                               ``"stub-zone"``.
            :param name: The zone name.

            :returns: The :class:`DnsActionResult`.
        """
        cfg = self._typed_config()
        dropin_path = "{}/{}".format(
            cfg.unbound_dropin_dir, cfg.unbound_dropin_basename,
        )
        command = [_TEE, dropin_path]

        if self._config.dry_run:
            rtnval = DnsActionResult(
                status=DnsActionStatus.DRY_RUN,
                detail=dropin_path,
                command=list(command),
                zone=name,
            )
            return rtnval

        existing = self._read_file(path=dropin_path)
        if existing is None:
            rtnval = DnsActionResult(
                status=DnsActionStatus.SKIPPED,
                detail="dropin_absent",
                command=list(command),
                zone=name,
            )
            return rtnval

        new_content = _remove_unbound_block_text(
            content=existing, block_kind=block_kind, name=name,
        )
        if new_content == existing:
            rtnval = DnsActionResult(
                status=DnsActionStatus.SKIPPED,
                detail="block_absent",
                command=list(command),
                zone=name,
            )
            return rtnval

        write_result = self._write_file(
            path=dropin_path, content=new_content,
        )
        if write_result.status != DnsActionStatus.SUCCESS:
            rtnval = DnsActionResult(
                status=write_result.status,
                detail=dropin_path,
                exit_code=write_result.exit_code,
                stdout=write_result.stdout,
                stderr=write_result.stderr,
                command=write_result.command,
                zone=name,
            )
            return rtnval

        reload_result = self.reload()
        rtnval = DnsActionResult(
            status=reload_result.status,
            detail=dropin_path,
            exit_code=reload_result.exit_code,
            stdout=reload_result.stdout,
            stderr=reload_result.stderr,
            command=reload_result.command,
            zone=name,
        )
        return rtnval

    # ------------------------------------------------------------------
    # dnsmasq static / forwarder records
    # ------------------------------------------------------------------

    def set_static_host(
        self,
        *,
        host: str,
        ip: str,
    ) -> DnsActionResult:
        """
            Upsert a ``host-record=<host>,<ip>`` directive in the
            dnsmasq drop-in.  Idempotent.

            :param host: The host (FQDN or short name).
            :param ip: The IP address to bind.

            :returns: The :class:`DnsActionResult`.
        """
        self._require_backend(
            expected=DnsServerBackend.DNSMASQ,
            operation="set_static_host",
        )
        directive_line = "host-record={},{}".format(host, ip)
        rtnval = self._upsert_dnsmasq_line(
            directive_line=directive_line,
            line_prefix="host-record={},".format(host),
            zone_label=host,
        )
        return rtnval

    def remove_static_host(self, *, host: str) -> DnsActionResult:
        """
            Strip every ``host-record=<host>,*`` line from the
            dnsmasq drop-in.

            :param host: The host name to strip.

            :returns: The :class:`DnsActionResult`.
        """
        self._require_backend(
            expected=DnsServerBackend.DNSMASQ,
            operation="remove_static_host",
        )
        rtnval = self._remove_dnsmasq_lines(
            line_prefix="host-record={},".format(host),
            zone_label=host,
        )
        return rtnval

    def set_forwarder_domain(
        self,
        *,
        domain: str,
        servers: list,
    ) -> DnsActionResult:
        """
            Upsert ``server=/<domain>/<server>`` directives in the
            dnsmasq drop-in.  Idempotent.

            :param domain: The DNS domain to forward.
            :param servers: List of upstream server IP addresses.

            :returns: The :class:`DnsActionResult`.
        """
        self._require_backend(
            expected=DnsServerBackend.DNSMASQ,
            operation="set_forwarder_domain",
        )
        directive_lines: list = []
        for server in servers:
            directive_lines.append(
                "server=/{}/{}".format(domain, server),
            )
        directive_block = "\n".join(directive_lines)
        rtnval = self._upsert_dnsmasq_block(
            directive_block=directive_block,
            line_prefix="server=/{}/".format(domain),
            zone_label=domain,
        )
        return rtnval

    def remove_forwarder_domain(self, *, domain: str) -> DnsActionResult:
        """
            Strip every ``server=/<domain>/*`` line from the
            dnsmasq drop-in.

            :param domain: The DNS domain to strip.

            :returns: The :class:`DnsActionResult`.
        """
        self._require_backend(
            expected=DnsServerBackend.DNSMASQ,
            operation="remove_forwarder_domain",
        )
        rtnval = self._remove_dnsmasq_lines(
            line_prefix="server=/{}/".format(domain),
            zone_label=domain,
        )
        return rtnval

    def _upsert_dnsmasq_line(
        self,
        *,
        directive_line: str,
        line_prefix: str,
        zone_label: str,
    ) -> DnsActionResult:
        """
            Upsert a single dnsmasq directive line.
        """
        cfg = self._typed_config()
        dropin_path = "{}/{}".format(
            cfg.dnsmasq_dropin_dir, cfg.dnsmasq_dropin_basename,
        )
        command = [_TEE, dropin_path]

        if self._config.dry_run:
            rtnval = DnsActionResult(
                status=DnsActionStatus.DRY_RUN,
                detail=dropin_path,
                command=list(command),
                zone=zone_label,
            )
            return rtnval

        existing = self._read_file(path=dropin_path)
        existing_text = "" if existing is None else existing
        new_content = _upsert_dnsmasq_lines(
            content=existing_text,
            line_prefix=line_prefix,
            replacement_block=directive_line,
        )
        if existing is not None and new_content == existing:
            rtnval = DnsActionResult(
                status=DnsActionStatus.SKIPPED,
                detail="directive_unchanged",
                command=list(command),
                zone=zone_label,
            )
            return rtnval

        write_result = self._write_file(
            path=dropin_path, content=new_content,
        )
        if write_result.status != DnsActionStatus.SUCCESS:
            rtnval = DnsActionResult(
                status=write_result.status,
                detail=dropin_path,
                exit_code=write_result.exit_code,
                stdout=write_result.stdout,
                stderr=write_result.stderr,
                command=write_result.command,
                zone=zone_label,
            )
            return rtnval

        restart_result = self._delegate_service_restart(
            service_name=_DNSMASQ_SERVICE_NAME,
        )
        rtnval = DnsActionResult(
            status=restart_result.status,
            detail=dropin_path,
            exit_code=restart_result.exit_code,
            stdout=restart_result.stdout,
            stderr=restart_result.stderr,
            command=restart_result.command,
            zone=zone_label,
        )
        return rtnval

    def _upsert_dnsmasq_block(
        self,
        *,
        directive_block: str,
        line_prefix: str,
        zone_label: str,
    ) -> DnsActionResult:
        """
            Upsert a multi-line dnsmasq directive block (e.g. one
            ``server=`` line per upstream).
        """
        cfg = self._typed_config()
        dropin_path = "{}/{}".format(
            cfg.dnsmasq_dropin_dir, cfg.dnsmasq_dropin_basename,
        )
        command = [_TEE, dropin_path]

        if self._config.dry_run:
            rtnval = DnsActionResult(
                status=DnsActionStatus.DRY_RUN,
                detail=dropin_path,
                command=list(command),
                zone=zone_label,
            )
            return rtnval

        existing = self._read_file(path=dropin_path)
        existing_text = "" if existing is None else existing
        new_content = _upsert_dnsmasq_lines(
            content=existing_text,
            line_prefix=line_prefix,
            replacement_block=directive_block,
        )
        if existing is not None and new_content == existing:
            rtnval = DnsActionResult(
                status=DnsActionStatus.SKIPPED,
                detail="directive_unchanged",
                command=list(command),
                zone=zone_label,
            )
            return rtnval

        write_result = self._write_file(
            path=dropin_path, content=new_content,
        )
        if write_result.status != DnsActionStatus.SUCCESS:
            rtnval = DnsActionResult(
                status=write_result.status,
                detail=dropin_path,
                exit_code=write_result.exit_code,
                stdout=write_result.stdout,
                stderr=write_result.stderr,
                command=write_result.command,
                zone=zone_label,
            )
            return rtnval

        restart_result = self._delegate_service_restart(
            service_name=_DNSMASQ_SERVICE_NAME,
        )
        rtnval = DnsActionResult(
            status=restart_result.status,
            detail=dropin_path,
            exit_code=restart_result.exit_code,
            stdout=restart_result.stdout,
            stderr=restart_result.stderr,
            command=restart_result.command,
            zone=zone_label,
        )
        return rtnval

    def _remove_dnsmasq_lines(
        self,
        *,
        line_prefix: str,
        zone_label: str,
    ) -> DnsActionResult:
        """
            Strip every line in the dnsmasq drop-in starting with
            ``line_prefix``.
        """
        cfg = self._typed_config()
        dropin_path = "{}/{}".format(
            cfg.dnsmasq_dropin_dir, cfg.dnsmasq_dropin_basename,
        )
        command = [_TEE, dropin_path]

        if self._config.dry_run:
            rtnval = DnsActionResult(
                status=DnsActionStatus.DRY_RUN,
                detail=dropin_path,
                command=list(command),
                zone=zone_label,
            )
            return rtnval

        existing = self._read_file(path=dropin_path)
        if existing is None:
            rtnval = DnsActionResult(
                status=DnsActionStatus.SKIPPED,
                detail="dropin_absent",
                command=list(command),
                zone=zone_label,
            )
            return rtnval

        new_content = _strip_dnsmasq_lines(
            content=existing, line_prefix=line_prefix,
        )
        if new_content == existing:
            rtnval = DnsActionResult(
                status=DnsActionStatus.SKIPPED,
                detail="directive_absent",
                command=list(command),
                zone=zone_label,
            )
            return rtnval

        write_result = self._write_file(
            path=dropin_path, content=new_content,
        )
        if write_result.status != DnsActionStatus.SUCCESS:
            rtnval = DnsActionResult(
                status=write_result.status,
                detail=dropin_path,
                exit_code=write_result.exit_code,
                stdout=write_result.stdout,
                stderr=write_result.stderr,
                command=write_result.command,
                zone=zone_label,
            )
            return rtnval

        restart_result = self._delegate_service_restart(
            service_name=_DNSMASQ_SERVICE_NAME,
        )
        rtnval = DnsActionResult(
            status=restart_result.status,
            detail=dropin_path,
            exit_code=restart_result.exit_code,
            stdout=restart_result.stdout,
            stderr=restart_result.stderr,
            command=restart_result.command,
            zone=zone_label,
        )
        return rtnval


# ----------------------------------------------------------------------
# Module-level helpers
# ----------------------------------------------------------------------


def _service_name_for_backend(*, backend: DnsServerBackend) -> str:
    """
        Map a backend to its systemd unit name.

        :param backend: The backend.

        :returns: The unit name.
    """
    if backend == DnsServerBackend.BIND:
        rtnval = _BIND_SERVICE_NAME
        return rtnval
    if backend == DnsServerBackend.UNBOUND:
        rtnval = _UNBOUND_SERVICE_NAME
        return rtnval
    rtnval = _DNSMASQ_SERVICE_NAME
    return rtnval


def _reload_command(*, backend: DnsServerBackend) -> list:
    """
        Render the canonical reload argv for a backend.

        :param backend: The backend.

        :returns: The argv as a list of strings.
    """
    if backend == DnsServerBackend.BIND:
        rtnval = [_RNDC, "reload"]
        return rtnval
    if backend == DnsServerBackend.UNBOUND:
        rtnval = [_UNBOUND_CONTROL, "reload"]
        return rtnval
    rtnval = ["systemctl", "restart", _DNSMASQ_SERVICE_NAME]
    return rtnval


def _wrap_run_result(
    *,
    run_result,
    command: list,
    zone: Optional[str],
) -> DnsActionResult:
    """
        Map a :class:`RunResult` to a :class:`DnsActionResult`.

        :param run_result: The :class:`RunResult`.
        :param command: The argv that produced it.
        :param zone: The zone to echo onto the result.

        :returns: The :class:`DnsActionResult`.
    """
    if run_result.exit_code == 0:
        mapped_status = DnsActionStatus.SUCCESS
    else:
        mapped_status = DnsActionStatus.FAILED
    rtnval = DnsActionResult(
        status=mapped_status,
        detail=zone,
        exit_code=run_result.exit_code,
        stdout=run_result.stdout,
        stderr=run_result.stderr,
        command=list(command),
        zone=zone,
    )
    return rtnval


def _coerce_action_status(*, value) -> DnsActionStatus:
    """
        Coerce a foreign ``ActionStatus``-like value into our
        :class:`DnsActionStatus` enum, falling back to
        ``SUCCESS`` for ``None`` and ``FAILED`` for unknown
        values.

        :param value: The foreign status value.

        :returns: The mapped :class:`DnsActionStatus`.
    """
    if value is None:
        rtnval = DnsActionStatus.SUCCESS
        return rtnval
    if isinstance(value, DnsActionStatus):
        rtnval = value
        return rtnval
    raw_value = getattr(value, "value", value)
    if raw_value == DnsActionStatus.SUCCESS.value:
        rtnval = DnsActionStatus.SUCCESS
        return rtnval
    if raw_value == DnsActionStatus.SKIPPED.value:
        rtnval = DnsActionStatus.SKIPPED
        return rtnval
    if raw_value == DnsActionStatus.DRY_RUN.value:
        rtnval = DnsActionStatus.DRY_RUN
        return rtnval
    rtnval = DnsActionStatus.FAILED
    return rtnval


# ----------------------------------------------------------------------
# BIND zone block helpers
# ----------------------------------------------------------------------


def _render_bind_zone_block(*, entry: ZoneEntry) -> str:
    """
        Render the canonical ``zone "<name>" {}`` declaration for
        a BIND drop-in file.

        :param entry: The :class:`ZoneEntry`.

        :returns: The rendered block content.
    """
    body_lines: list = []
    body_lines.append("zone \"{}\" {{".format(entry.name))
    body_lines.append("    type {};".format(entry.type))
    if entry.file_path is not None and len(entry.file_path) > 0:
        body_lines.append("    file \"{}\";".format(entry.file_path))
    body_lines.append("};")
    rtnval = "\n".join(body_lines) + "\n"
    return rtnval


def _parse_bind_zone_block(*, content: str) -> tuple:
    """
        Parse a BIND ``zone "<name>" {}`` drop-in for ``type``
        and ``file``.

        :param content: The drop-in body.

        :returns: A tuple ``(zone_type, zone_file)``.  Either
                  field may be ``None``.
    """
    zone_type: Optional[str] = None
    zone_file: Optional[str] = None
    for raw_line in content.splitlines():
        stripped = raw_line.strip().rstrip(";").strip()
        if stripped.startswith("type "):
            zone_type = stripped[len("type "):].strip()
            continue
        if stripped.startswith("file "):
            file_value = stripped[len("file "):].strip()
            zone_file = file_value.strip("\"")
            continue
    rtnval = (zone_type, zone_file)
    return rtnval


# ----------------------------------------------------------------------
# Unbound block helpers
# ----------------------------------------------------------------------


def _render_unbound_block(
    *,
    block_kind: str,
    name: str,
    addresses: list,
    address_directive: str,
) -> str:
    """
        Render an Unbound ``forward-zone:`` or ``stub-zone:``
        block.

        :param block_kind: ``"forward-zone"`` or ``"stub-zone"``.
        :param name: The zone name.
        :param addresses: List of upstream addresses.
        :param address_directive: ``"forward-addr"`` or
                                  ``"stub-addr"``.

        :returns: The rendered block (no trailing newline).
    """
    body_lines: list = []
    body_lines.append("{}:".format(block_kind))
    body_lines.append("    name: \"{}\"".format(name))
    for address in addresses:
        body_lines.append(
            "    {}: {}".format(address_directive, address),
        )
    rtnval = "\n".join(body_lines)
    return rtnval


def _upsert_unbound_block_text(
    *,
    content: str,
    block_kind: str,
    name: str,
    block_body: str,
) -> str:
    """
        Replace any existing block of type ``block_kind`` keyed
        by ``name`` in ``content`` with ``block_body``, or append
        a new block when none is present.

        :param content: The current drop-in text.
        :param block_kind: ``"forward-zone"`` or ``"stub-zone"``.
        :param name: The zone name.
        :param block_body: The replacement block.

        :returns: The new drop-in text.
    """
    blocks = _parse_unbound_blocks(content=content)
    matched_index: Optional[int] = None
    for index, block_record in enumerate(blocks):
        if (
            block_record["kind"] == block_kind
            and block_record["name"] == name
        ):
            matched_index = index
            break

    if matched_index is None:
        new_blocks = list(blocks)
        new_blocks.append({
            "kind": block_kind,
            "name": name,
            "body": block_body,
        })
    else:
        new_blocks = list(blocks)
        new_blocks[matched_index] = {
            "kind": block_kind,
            "name": name,
            "body": block_body,
        }
    rtnval = _render_unbound_blocks(blocks=new_blocks)
    return rtnval


def _remove_unbound_block_text(
    *,
    content: str,
    block_kind: str,
    name: str,
) -> str:
    """
        Strip any block of type ``block_kind`` keyed by ``name``
        from ``content``.

        :param content: The current drop-in text.
        :param block_kind: ``"forward-zone"`` or ``"stub-zone"``.
        :param name: The zone name.

        :returns: The new drop-in text.
    """
    blocks = _parse_unbound_blocks(content=content)
    new_blocks: list = []
    for block_record in blocks:
        if (
            block_record["kind"] == block_kind
            and block_record["name"] == name
        ):
            continue
        new_blocks.append(block_record)
    rtnval = _render_unbound_blocks(blocks=new_blocks)
    return rtnval


def _parse_unbound_blocks(*, content: str) -> list:
    """
        Parse an Unbound drop-in into a list of block records.

        Recognises ``forward-zone:`` and ``stub-zone:`` blocks
        starting at column 0.  Other content is preserved as a
        single ``"raw"`` block at the head of the list so that
        operator-authored content survives a round-trip.

        :param content: The drop-in body.

        :returns: A list of block records, each a dict with keys
                  ``kind``, ``name`` (``None`` for raw), ``body``.
    """
    rtnval: list = []
    raw_lines: list = []
    current_kind: Optional[str] = None
    current_name: Optional[str] = None
    current_body: list = []

    def _flush_current() -> None:
        if current_kind is None:
            return
        block_text = "\n".join(current_body)
        rtnval.append({
            "kind": current_kind,
            "name": current_name if current_name is not None else "",
            "body": block_text,
        })
        return

    for raw_line in content.splitlines():
        if raw_line.startswith("forward-zone:") or raw_line.startswith(
            "stub-zone:",
        ):
            if current_kind is None and len(raw_lines) > 0:
                rtnval.append({
                    "kind": "raw",
                    "name": None,
                    "body": "\n".join(raw_lines),
                })
                raw_lines = []
            else:
                _flush_current()
            stripped_kind = raw_line.split(":", 1)[0]
            current_kind = stripped_kind
            current_name = None
            current_body = [raw_line]
            continue

        if current_kind is not None:
            current_body.append(raw_line)
            stripped_line = raw_line.strip()
            if stripped_line.startswith("name:") and current_name is None:
                value_part = stripped_line[len("name:"):].strip()
                current_name = value_part.strip("\"")
            continue

        raw_lines.append(raw_line)

    if current_kind is not None:
        _flush_current()
    elif len(raw_lines) > 0:
        rtnval.append({
            "kind": "raw",
            "name": None,
            "body": "\n".join(raw_lines),
        })
    return rtnval


def _render_unbound_blocks(*, blocks: list) -> str:
    """
        Re-render a list of Unbound block records.

        :param blocks: The block records.

        :returns: The drop-in text.
    """
    if len(blocks) == 0:
        rtnval = ""
        return rtnval
    pieces: list = []
    for block_record in blocks:
        body = block_record["body"]
        pieces.append(body)
    rtnval = "\n".join(pieces)
    if len(rtnval) > 0 and not rtnval.endswith("\n"):
        rtnval = rtnval + "\n"
    return rtnval


# ----------------------------------------------------------------------
# dnsmasq line helpers
# ----------------------------------------------------------------------


def _upsert_dnsmasq_lines(
    *,
    content: str,
    line_prefix: str,
    replacement_block: str,
) -> str:
    """
        Strip every line in ``content`` whose body starts with
        ``line_prefix`` and append ``replacement_block`` at the
        end.

        :param content: The current drop-in text.
        :param line_prefix: The prefix that identifies managed
                            lines.
        :param replacement_block: The new line(s) to append.

        :returns: The new drop-in text.
    """
    kept_lines: list = []
    for raw_line in content.splitlines():
        if raw_line.startswith(line_prefix):
            continue
        kept_lines.append(raw_line)

    new_block_lines = replacement_block.splitlines()
    final_lines = list(kept_lines) + list(new_block_lines)
    rtnval = "\n".join(final_lines)
    if len(rtnval) > 0 and not rtnval.endswith("\n"):
        rtnval = rtnval + "\n"
    return rtnval


def _strip_dnsmasq_lines(
    *,
    content: str,
    line_prefix: str,
) -> str:
    """
        Strip every line in ``content`` whose body starts with
        ``line_prefix``.

        :param content: The current drop-in text.
        :param line_prefix: The prefix to match.

        :returns: The new drop-in text.
    """
    kept_lines: list = []
    for raw_line in content.splitlines():
        if raw_line.startswith(line_prefix):
            continue
        kept_lines.append(raw_line)
    rtnval = "\n".join(kept_lines)
    if (
        len(rtnval) > 0
        and not rtnval.endswith("\n")
        and content.endswith("\n")
    ):
        rtnval = rtnval + "\n"
    return rtnval


# ----------------------------------------------------------------------
# Status parsers
# ----------------------------------------------------------------------


def _parse_version_from_rndc_status(*, text: str) -> Optional[str]:
    """
        Extract the first ``version:`` token from ``rndc status``
        output.

        ``rndc status`` emits, among other things, a line like
        ``version: BIND 9.16.23-RH``; this parser returns the
        right-hand side.

        :param text: The raw stdout from ``rndc status``.

        :returns: The version string when found; ``None``
                  otherwise.
    """
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.lower().startswith("version:"):
            value_part = stripped[len("version:"):].strip()
            if len(value_part) > 0:
                rtnval: Optional[str] = value_part
                return rtnval
    rtnval = None
    return rtnval


def _parse_version_from_unbound_status(
    *,
    text: str,
) -> Optional[str]:
    """
        Extract the first ``version:`` token from
        ``unbound-control status`` output.

        :param text: The raw stdout.

        :returns: The version string when found; ``None``
                  otherwise.
    """
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.lower().startswith("version:"):
            value_part = stripped[len("version:"):].strip()
            if len(value_part) > 0:
                rtnval: Optional[str] = value_part
                return rtnval
    rtnval = None
    return rtnval
