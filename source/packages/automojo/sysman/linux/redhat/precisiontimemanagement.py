# automojo.sysman.linux.redhat.precisiontimemanagement
#
# PrecisionTimeManager wraps the linuxptp stack (ptp4l, phc2sys, pmc)
# through the ICommandRunner abstraction.  It also manages
# /etc/linuxptp/ptp4l.conf, /etc/sysconfig/ptp4l, and
# /etc/sysconfig/phc2sys via cat / tee.
#
# Service lifecycle (start / stop / enable / disable for ptp4l.service
# and phc2sys.service) is delegated to an injected ServiceManager
# rather than re-spelling systemctl locally.

from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

from automojo.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


PtpActionStatus = ActionStatus


_CAT = "cat"
_TEE = "tee"
_PMC = "pmc"

_DEFAULT_PTP4L_CONF = "/etc/linuxptp/ptp4l.conf"
_DEFAULT_PTP4L_SYSCONFIG = "/etc/sysconfig/ptp4l"
_DEFAULT_PHC2SYS_SYSCONFIG = "/etc/sysconfig/phc2sys"

_PTP4L_SERVICE = "ptp4l"
_PHC2SYS_SERVICE = "phc2sys"


@dataclass
class PrecisionTimeManagerConfig(FeatureConfig):
    """
        Configuration for :class:`PrecisionTimeManager`.

        Inherits ``dry_run`` and ``elevated`` from :class:`FeatureConfig`.

        :param ptp4l_conf: Absolute path to the ptp4l configuration
                           file used for directive mutations.
        :param ptp4l_sysconfig: Absolute path to the ptp4l systemd-unit
                                environment override file.
        :param phc2sys_sysconfig: Absolute path to the phc2sys
                                  systemd-unit environment override
                                  file.
    """
    ptp4l_conf: str = _DEFAULT_PTP4L_CONF
    ptp4l_sysconfig: str = _DEFAULT_PTP4L_SYSCONFIG
    phc2sys_sysconfig: str = _DEFAULT_PHC2SYS_SYSCONFIG


@dataclass
class PtpActionResult(FeatureActionResult):
    """
        Outcome of a precision-time-management action.

        Inherits ``status`` / ``detail`` / ``exit_code`` / ``stdout`` /
        ``stderr`` / ``command`` from :class:`FeatureActionResult`.
    """
    pass


@dataclass
class PtpCurrentDataSet:
    """
        ``CURRENT_DATA_SET`` snapshot from ``pmc``.

        :param steps_removed: Number of steps from the grandmaster.
                              ``-1`` indicates the daemon was not
                              reachable.
        :param offset_from_master_ns: Offset from the grandmaster in
                                      nanoseconds.
        :param mean_path_delay_ns: Mean path delay in nanoseconds.
        :param raw: Raw stdout (or captured stderr when the daemon was
                    not reachable).
    """
    steps_removed: int
    offset_from_master_ns: int
    mean_path_delay_ns: int
    raw: str


@dataclass
class PtpParentDataSet:
    """
        ``PARENT_DATA_SET`` snapshot from ``pmc``.

        :param parent_port_identity: Parent port identity string.
        :param grandmaster_identity: Grandmaster identity string.
        :param grandmaster_priority_1: ``priority1`` value.
        :param grandmaster_priority_2: ``priority2`` value.
        :param raw: Raw stdout (or captured stderr).
    """
    parent_port_identity: str
    grandmaster_identity: str
    grandmaster_priority_1: int
    grandmaster_priority_2: int
    raw: str


@dataclass
class PtpPortDataSet:
    """
        One ``PORT_DATA_SET`` row from ``pmc``.

        :param port_identity: Port identity string.
        :param port_state: ``MASTER`` / ``SLAVE`` / ``PASSIVE`` / ...
        :param log_announce_interval: ``logAnnounceInterval``.
        :param log_sync_interval: ``logSyncInterval``.
        :param delay_mechanism: ``E2E`` or ``P2P``.
        :param raw: Raw block text.
    """
    port_identity: str
    port_state: str
    log_announce_interval: int
    log_sync_interval: int
    delay_mechanism: str
    raw: str


# ----------------------------------------------------------------------
# Service-manager protocol expected for delegation.
# ----------------------------------------------------------------------
#
# The injected ``service_manager`` MUST expose at minimum:
#   start(name) / stop(name) / enable(name) / disable(name) / restart(name)
# returning a result object with a ``status`` attribute compatible with
# :class:`PtpActionStatus`.  The standard
# :class:`automojo.sysman.linux.redhat.servicemanagement.ServiceManager`
# satisfies this contract.


class PrecisionTimeManager(BaseFeatureManager):
    """
        Facade for linuxptp (ptp4l, phc2sys, pmc) management.

        Reads/writes config files via cat / tee through the runner.
        Service lifecycle is delegated to an injected
        :class:`ServiceManager`.
    """

    result_class = PtpActionResult
    feature_name = "precision-time-management"

    def __init__(
        self,
        runner,
        config: Optional[PrecisionTimeManagerConfig] = None,
        *,
        service_manager=None,
        report=None,
    ) -> None:
        """
            :param runner: The injected :class:`ICommandRunner`.
            :param config: Manager configuration; defaults to
                           :class:`PrecisionTimeManagerConfig` with all
                           defaults.
            :param service_manager: Optional service-manager instance
                                    used to delegate start / stop /
                                    enable / disable for
                                    ``ptp4l.service`` and
                                    ``phc2sys.service``.  Required for
                                    the lifecycle verbs; query / config
                                    verbs work without it.
            :param report: Optional :class:`MaintenanceReport` for
                           auto-emitted action records.
        """
        if config is None:
            config = PrecisionTimeManagerConfig()
        super().__init__(runner=runner, config=config, report=report)
        self._service_manager = service_manager
        return

    @property
    def service_manager(self):
        """
            The injected service manager, or ``None``.
        """
        return self._service_manager

    # ------------------------------------------------------------------
    # ptp4l.conf — directive-level reads and writes
    # ------------------------------------------------------------------

    def get_directives(self) -> dict:
        """
            Return the parsed ``ptp4l.conf`` body.

            :returns: ``dict[str, dict[str, str]]`` keyed by section
                      name (e.g. ``"global"``, ``"eth0"``).  Empty dict
                      when the file is missing.
        """
        text = self._read_file(path=self._config.ptp4l_conf)
        sections = _parse_ptp4l_conf(text=text)
        rtnval = _sections_to_plain_dict(sections=sections)
        return rtnval

    def set_directive(
        self, section: str, key: str, value: str,
    ) -> PtpActionResult:
        """
            Upsert ``<key> <value>`` in ``[section]`` of ``ptp4l.conf``.

            Idempotent: ``SKIPPED`` when the directive already has the
            requested value.

            :param section: Section name (without brackets), e.g.
                            ``"global"`` or ``"eth0"``.
            :param key: Directive key.
            :param value: Directive value (rendered as a string).

            :returns: The :class:`PtpActionResult`.
        """
        action_detail = "set_directive:[{}]{}={}".format(section, key, value)
        path = self._config.ptp4l_conf
        write_command = [_TEE, path]

        if self._config.dry_run:
            dry = PtpActionResult(
                status=PtpActionStatus.DRY_RUN,
                detail=action_detail,
                command=list(write_command),
            )
            return dry

        existing_text = self._read_file(path=path)
        sections = _parse_ptp4l_conf(text=existing_text)

        target_section = sections.get(section)
        if target_section is not None:
            existing_value = target_section.get(key)
            if existing_value is not None and existing_value == str(value):
                skipped = PtpActionResult(
                    status=PtpActionStatus.SKIPPED,
                    detail=action_detail,
                    command=list(write_command),
                )
                return skipped

        if target_section is None:
            sections[section] = OrderedDict()
        sections[section][key] = str(value)

        new_text = _render_ptp4l_conf(sections=sections)
        rtnval = self._write_config(
            path=path, content=new_text, detail=action_detail,
        )
        return rtnval

    def remove_directive(
        self, section: str, key: str,
    ) -> PtpActionResult:
        """
            Remove ``key`` from ``[section]`` in ``ptp4l.conf``.

            Idempotent: ``SKIPPED`` when the directive is absent.

            :param section: Section name (without brackets).
            :param key: Directive key.

            :returns: The :class:`PtpActionResult`.
        """
        action_detail = "remove_directive:[{}]{}".format(section, key)
        path = self._config.ptp4l_conf
        write_command = [_TEE, path]

        if self._config.dry_run:
            dry = PtpActionResult(
                status=PtpActionStatus.DRY_RUN,
                detail=action_detail,
                command=list(write_command),
            )
            return dry

        existing_text = self._read_file(path=path)
        sections = _parse_ptp4l_conf(text=existing_text)

        target_section = sections.get(section)
        present = False
        if target_section is not None:
            if key in target_section:
                present = True

        if present is False:
            skipped = PtpActionResult(
                status=PtpActionStatus.SKIPPED,
                detail=action_detail,
                command=list(write_command),
            )
            return skipped

        del sections[section][key]
        if len(sections[section]) == 0:
            del sections[section]

        new_text = _render_ptp4l_conf(sections=sections)
        rtnval = self._write_config(
            path=path, content=new_text, detail=action_detail,
        )
        return rtnval

    # ------------------------------------------------------------------
    # ptp4l.conf — preset helpers
    # ------------------------------------------------------------------

    def set_domain(self, domain_number: int) -> PtpActionResult:
        """
            Set ``domainNumber <n>`` in ``[global]``.

            :param domain_number: PTP domain number (0..127).

            :returns: The :class:`PtpActionResult`.
        """
        rtnval = self.set_directive(
            section="global",
            key="domainNumber",
            value=str(domain_number),
        )
        return rtnval

    def set_transport(self, mode: str) -> PtpActionResult:
        """
            Set ``network_transport <mode>`` in ``[global]``.

            :param mode: One of ``"L2"``, ``"UDPv4"``, ``"UDPv6"``.

            :returns: The :class:`PtpActionResult`.
        """
        rtnval = self.set_directive(
            section="global",
            key="network_transport",
            value=mode,
        )
        return rtnval

    def set_delay_mechanism(self, mode: str) -> PtpActionResult:
        """
            Set ``delay_mechanism <mode>`` in ``[global]``.

            :param mode: One of ``"E2E"``, ``"P2P"``.

            :returns: The :class:`PtpActionResult`.
        """
        rtnval = self.set_directive(
            section="global",
            key="delay_mechanism",
            value=mode,
        )
        return rtnval

    def set_interfaces(
        self,
        interfaces: list[str],
        per_interface: Optional[dict] = None,
    ) -> PtpActionResult:
        """
            Synchronise per-interface sections in ``ptp4l.conf``.

            Adds an ``[<iface>]`` section for every name in
            ``interfaces`` not already present, removes existing
            interface sections that are not in ``interfaces``, and
            preserves ``[global]`` plus caller-supplied per-interface
            overrides.

            :param interfaces: Desired interface name list (e.g.
                               ``["eth0", "ens5f0"]``).
            :param per_interface: Optional mapping of
                                  ``interface_name -> {key: value}``
                                  used to populate the per-interface
                                  section when it is created.

            :returns: The :class:`PtpActionResult`.
        """
        action_detail = "set_interfaces:{}".format(",".join(interfaces))
        path = self._config.ptp4l_conf
        write_command = [_TEE, path]

        if self._config.dry_run:
            dry = PtpActionResult(
                status=PtpActionStatus.DRY_RUN,
                detail=action_detail,
                command=list(write_command),
            )
            return dry

        existing_text = self._read_file(path=path)
        sections = _parse_ptp4l_conf(text=existing_text)

        # Build the list of currently-present interface sections —
        # everything that is not the [global] section.
        current_iface_sections: list[str] = []
        for section_name in list(sections.keys()):
            if section_name == "global":
                continue
            current_iface_sections.append(section_name)

        desired = list(interfaces)

        per_iface_map: dict = {}
        if per_interface is not None:
            per_iface_map = dict(per_interface)

        # Strip stale interface sections.
        changed = False
        for section_name in current_iface_sections:
            if section_name not in desired:
                del sections[section_name]
                changed = True

        # Add missing interface sections.
        for iface in desired:
            if iface not in sections:
                new_section: "OrderedDict[str, str]" = OrderedDict()
                overrides = per_iface_map.get(iface)
                if overrides is not None:
                    for ikey, ival in overrides.items():
                        new_section[ikey] = str(ival)
                sections[iface] = new_section
                changed = True
                continue
            # Section already exists — apply caller overrides idempotently.
            overrides = per_iface_map.get(iface)
            if overrides is None:
                continue
            for ikey, ival in overrides.items():
                str_val = str(ival)
                existing_val = sections[iface].get(ikey)
                if existing_val == str_val:
                    continue
                sections[iface][ikey] = str_val
                changed = True

        if changed is False:
            skipped = PtpActionResult(
                status=PtpActionStatus.SKIPPED,
                detail=action_detail,
                command=list(write_command),
            )
            return skipped

        new_text = _render_ptp4l_conf(sections=sections)
        rtnval = self._write_config(
            path=path, content=new_text, detail=action_detail,
        )
        return rtnval

    # ------------------------------------------------------------------
    # /etc/sysconfig/ptp4l and /etc/sysconfig/phc2sys
    # ------------------------------------------------------------------

    def get_ptp4l_options(self) -> Optional[str]:
        """
            Read the ``OPTIONS=`` value from ``/etc/sysconfig/ptp4l``.

            :returns: The stripped value, or ``None`` when the variable
                      is absent / the file is missing.
        """
        rtnval = self._get_sysconfig_options(
            path=self._config.ptp4l_sysconfig,
        )
        return rtnval

    def set_ptp4l_options(self, value: str) -> PtpActionResult:
        """
            Upsert ``OPTIONS="<value>"`` in ``/etc/sysconfig/ptp4l``.

            :param value: Replacement options string.

            :returns: The :class:`PtpActionResult`; ``detail`` carries
                      a notice that ``ptp4l.service`` must be restarted
                      for the change to take effect.
        """
        rtnval = self._set_sysconfig_options(
            path=self._config.ptp4l_sysconfig,
            value=value,
            service_label=_PTP4L_SERVICE,
        )
        return rtnval

    def get_phc2sys_options(self) -> Optional[str]:
        """
            Read the ``OPTIONS=`` value from
            ``/etc/sysconfig/phc2sys``.

            :returns: The stripped value, or ``None`` when absent.
        """
        rtnval = self._get_sysconfig_options(
            path=self._config.phc2sys_sysconfig,
        )
        return rtnval

    def set_phc2sys_options(self, value: str) -> PtpActionResult:
        """
            Upsert ``OPTIONS="<value>"`` in
            ``/etc/sysconfig/phc2sys``.

            :param value: Replacement options string.

            :returns: The :class:`PtpActionResult`; ``detail`` carries
                      a notice that ``phc2sys.service`` must be
                      restarted for the change to take effect.
        """
        rtnval = self._set_sysconfig_options(
            path=self._config.phc2sys_sysconfig,
            value=value,
            service_label=_PHC2SYS_SERVICE,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Service lifecycle (delegated to the injected ServiceManager)
    # ------------------------------------------------------------------

    def start_ptp4l(self) -> PtpActionResult:
        """
            Start ``ptp4l.service`` via the injected service manager.
        """
        rtnval = self._delegate_service_op(
            op="start",
            service=_PTP4L_SERVICE,
        )
        return rtnval

    def stop_ptp4l(self) -> PtpActionResult:
        """
            Stop ``ptp4l.service`` via the injected service manager.
        """
        rtnval = self._delegate_service_op(
            op="stop",
            service=_PTP4L_SERVICE,
        )
        return rtnval

    def enable_ptp4l(self) -> PtpActionResult:
        """
            Enable ``ptp4l.service`` via the injected service manager.
        """
        rtnval = self._delegate_service_op(
            op="enable",
            service=_PTP4L_SERVICE,
        )
        return rtnval

    def disable_ptp4l(self) -> PtpActionResult:
        """
            Disable ``ptp4l.service`` via the injected service manager.
        """
        rtnval = self._delegate_service_op(
            op="disable",
            service=_PTP4L_SERVICE,
        )
        return rtnval

    def start_phc2sys(self) -> PtpActionResult:
        """
            Start ``phc2sys.service`` after verifying that
            ``ptp4l.service`` is in an active or enabled posture.

            :returns: The :class:`PtpActionResult`; FAILED with a
                      descriptive ``detail`` when the ptp4l dependency
                      is not satisfied.
        """
        if self._ptp4l_posture_ok() is False:
            failed = PtpActionResult(
                status=PtpActionStatus.FAILED,
                detail=(
                    "phc2sys requires ptp4l.service to be active or "
                    "enabled-on-boot before it can start."
                ),
                command=None,
            )
            return failed
        rtnval = self._delegate_service_op(
            op="start",
            service=_PHC2SYS_SERVICE,
        )
        return rtnval

    def stop_phc2sys(self) -> PtpActionResult:
        """
            Stop ``phc2sys.service`` via the injected service manager.
        """
        rtnval = self._delegate_service_op(
            op="stop",
            service=_PHC2SYS_SERVICE,
        )
        return rtnval

    def enable_phc2sys(self) -> PtpActionResult:
        """
            Enable ``phc2sys.service`` after verifying that
            ``ptp4l.service`` is in an active or enabled posture.
        """
        if self._ptp4l_posture_ok() is False:
            failed = PtpActionResult(
                status=PtpActionStatus.FAILED,
                detail=(
                    "phc2sys requires ptp4l.service to be active or "
                    "enabled-on-boot before it can be enabled."
                ),
                command=None,
            )
            return failed
        rtnval = self._delegate_service_op(
            op="enable",
            service=_PHC2SYS_SERVICE,
        )
        return rtnval

    def disable_phc2sys(self) -> PtpActionResult:
        """
            Disable ``phc2sys.service`` via the injected service manager.
        """
        rtnval = self._delegate_service_op(
            op="disable",
            service=_PHC2SYS_SERVICE,
        )
        return rtnval

    # ------------------------------------------------------------------
    # pmc queries
    # ------------------------------------------------------------------

    def current_data_set(self) -> PtpCurrentDataSet:
        """
            Query ``CURRENT_DATA_SET`` from ``pmc``.

            Never raises.  When the daemon is unreachable the returned
            record carries ``steps_removed=-1`` and ``raw=<stderr>``.

            :returns: The :class:`PtpCurrentDataSet`.
        """
        command = [_PMC, "-u", "-b", "0", "GET CURRENT_DATA_SET"]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            stderr_text = run_result.stderr or ""
            stub = PtpCurrentDataSet(
                steps_removed=-1,
                offset_from_master_ns=0,
                mean_path_delay_ns=0,
                raw=stderr_text,
            )
            return stub
        if run_result.stdout is None or len(run_result.stdout.strip()) == 0:
            stub = PtpCurrentDataSet(
                steps_removed=-1,
                offset_from_master_ns=0,
                mean_path_delay_ns=0,
                raw="",
            )
            return stub
        rtnval = _parse_pmc_current_data_set(text=run_result.stdout)
        return rtnval

    def parent_data_set(self) -> PtpParentDataSet:
        """
            Query ``PARENT_DATA_SET`` from ``pmc``.

            Never raises.  When the daemon is unreachable the returned
            record carries empty identity strings and ``raw=<stderr>``.

            :returns: The :class:`PtpParentDataSet`.
        """
        command = [_PMC, "-u", "-b", "0", "GET PARENT_DATA_SET"]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            stderr_text = run_result.stderr or ""
            stub = PtpParentDataSet(
                parent_port_identity="",
                grandmaster_identity="",
                grandmaster_priority_1=0,
                grandmaster_priority_2=0,
                raw=stderr_text,
            )
            return stub
        if run_result.stdout is None or len(run_result.stdout.strip()) == 0:
            stub = PtpParentDataSet(
                parent_port_identity="",
                grandmaster_identity="",
                grandmaster_priority_1=0,
                grandmaster_priority_2=0,
                raw="",
            )
            return stub
        rtnval = _parse_pmc_parent_data_set(text=run_result.stdout)
        return rtnval

    def port_data_sets(self) -> list[PtpPortDataSet]:
        """
            Query ``PORT_DATA_SET`` from ``pmc``.

            Never raises.  When the daemon is unreachable the method
            returns an empty list.

            :returns: One :class:`PtpPortDataSet` per port.
        """
        command = [_PMC, "-u", "-b", "0", "GET PORT_DATA_SET"]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            empty: list[PtpPortDataSet] = []
            return empty
        if run_result.stdout is None or len(run_result.stdout.strip()) == 0:
            empty = []
            return empty
        rtnval = _parse_pmc_port_data_sets(text=run_result.stdout)
        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_file(self, *, path: str) -> str:
        """
            Read a file via ``cat``.

            :param path: Absolute path.

            :returns: File contents on success; empty string otherwise.
        """
        command = [_CAT, path]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval = ""
            return rtnval
        rtnval = run_result.stdout
        return rtnval

    def _write_config(
        self, *, path: str, content: str, detail: str,
    ) -> PtpActionResult:
        """
            Write ``content`` to ``path`` via ``tee``.

            :param path: Absolute path.
            :param content: New file body.
            :param detail: ``detail`` value to record on the result.

            :returns: The :class:`PtpActionResult`.
        """
        command = [_TEE, path]
        run_result = self._runner.run(
            list(command),
            elevated=self._config.elevated,
            stdin=content,
        )
        if run_result.exit_code == 0:
            mapped_status = PtpActionStatus.SUCCESS
        else:
            mapped_status = PtpActionStatus.FAILED
        rtnval = PtpActionResult(
            status=mapped_status,
            detail=detail,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(command),
        )
        return rtnval

    def _get_sysconfig_options(self, *, path: str) -> Optional[str]:
        """
            Read the ``OPTIONS`` variable out of a sysconfig file.

            :param path: Absolute path.

            :returns: Stripped value or ``None``.
        """
        text = self._read_file(path=path)
        rtnval = _parse_sysconfig_options(text=text)
        return rtnval

    def _set_sysconfig_options(
        self,
        *,
        path: str,
        value: str,
        service_label: str,
    ) -> PtpActionResult:
        """
            Upsert ``OPTIONS="<value>"`` in a sysconfig file.

            :param path: Absolute path.
            :param value: Replacement options string.
            :param service_label: Bare unit label (e.g. ``"ptp4l"``)
                                  used in the restart-required notice.

            :returns: The :class:`PtpActionResult`.
        """
        action_detail = "set_options:{}={}".format(service_label, value)
        restart_notice = "{}.service restart required".format(service_label)
        write_command = [_TEE, path]

        if self._config.dry_run:
            dry = PtpActionResult(
                status=PtpActionStatus.DRY_RUN,
                detail=restart_notice,
                command=list(write_command),
            )
            return dry

        existing_text = self._read_file(path=path)
        existing_value = _parse_sysconfig_options(text=existing_text)
        if existing_value is not None and existing_value == value:
            skipped = PtpActionResult(
                status=PtpActionStatus.SKIPPED,
                detail=action_detail,
                command=list(write_command),
            )
            return skipped

        new_text = _render_sysconfig_options(
            existing_text=existing_text, value=value,
        )

        run_result = self._runner.run(
            list(write_command),
            elevated=self._config.elevated,
            stdin=new_text,
        )
        if run_result.exit_code == 0:
            mapped_status = PtpActionStatus.SUCCESS
        else:
            mapped_status = PtpActionStatus.FAILED

        rtnval = PtpActionResult(
            status=mapped_status,
            detail=restart_notice,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(write_command),
        )
        return rtnval

    def _delegate_service_op(
        self, *, op: str, service: str,
    ) -> PtpActionResult:
        """
            Call the injected service manager's ``op`` method for
            ``service`` and translate the returned object to a
            :class:`PtpActionResult`.

            :param op: One of ``"start"``, ``"stop"``, ``"enable"``,
                       ``"disable"``.
            :param service: Bare unit label (e.g. ``"ptp4l"``).

            :returns: The :class:`PtpActionResult`.
        """
        if self._service_manager is None:
            failed = PtpActionResult(
                status=PtpActionStatus.FAILED,
                detail=(
                    "No ServiceManager injected; cannot {} {}.service"
                ).format(op, service),
                command=None,
            )
            return failed

        method = getattr(self._service_manager, op, None)
        if method is None:
            failed = PtpActionResult(
                status=PtpActionStatus.FAILED,
                detail=(
                    "Injected ServiceManager does not expose {}() "
                    "(needed for {}.service)"
                ).format(op, service),
                command=None,
            )
            return failed

        sm_result = method(service)
        rtnval = _result_from_service_op(
            sm_result=sm_result,
            op=op,
            service=service,
        )
        return rtnval

    def _ptp4l_posture_ok(self) -> bool:
        """
            Probe whether ``ptp4l.service`` is currently active or
            declared active-on-boot.

            :returns: True when posture is OK; False otherwise.
        """
        if self._service_manager is None:
            rtnval = False
            return rtnval

        query = getattr(self._service_manager, "query", None)
        if query is None:
            rtnval = False
            return rtnval

        info = query(_PTP4L_SERVICE)
        active_state = getattr(info, "active_state", "")
        enabled_state = getattr(info, "enabled_state", "")
        if active_state == "active":
            rtnval = True
            return rtnval
        if enabled_state == "enabled":
            rtnval = True
            return rtnval
        rtnval = False
        return rtnval


# ----------------------------------------------------------------------
# Module-level parsers and renderers
# ----------------------------------------------------------------------


def _parse_ptp4l_conf(*, text: str) -> "OrderedDict[str, OrderedDict[str, str]]":
    """
        Parse a ``ptp4l.conf`` body into ordered ``section -> {key: value}``.

        Whitespace-separated ``key value`` syntax (NOT ``key = value``).
        Lines outside any section are placed in ``"global"``.  Blank
        lines and ``#`` comments are ignored at parse time; they are
        re-emitted by :func:`_render_ptp4l_conf` only when they belong
        to a fully-preserved section block — but since this parser
        retains only directives, comments are not round-tripped.  The
        renderer compensates by emitting clean section blocks; this is
        the agreed trade-off in the per-OS design (idempotent rewrite
        is the primary correctness property).

        :param text: Raw file body.

        :returns: Ordered mapping of sections to their directive maps.
    """
    rtnval: "OrderedDict[str, OrderedDict[str, str]]" = OrderedDict()
    rtnval["global"] = OrderedDict()
    current_section = "global"

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if len(line) == 0:
            continue
        if line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section_name = line[1:-1].strip()
            if len(section_name) == 0:
                continue
            current_section = section_name
            if current_section not in rtnval:
                rtnval[current_section] = OrderedDict()
            continue
        # Directive: key value (whitespace-separated, key value may
        # contain further whitespace as part of the value).
        tokens = line.split(None, 1)
        if len(tokens) == 0:
            continue
        key = tokens[0]
        if len(tokens) == 1:
            value = ""
        else:
            value = tokens[1].strip()
        if current_section not in rtnval:
            rtnval[current_section] = OrderedDict()
        rtnval[current_section][key] = value

    # Drop the implicit empty [global] when the file actually has no
    # uncommented global directives — but only if some other section
    # was present.
    if len(rtnval["global"]) == 0 and len(rtnval) > 1:
        del rtnval["global"]

    return rtnval


def _render_ptp4l_conf(
    *, sections: "OrderedDict[str, OrderedDict[str, str]]",
) -> str:
    """
        Render ordered sections back into a ``ptp4l.conf`` body.

        Emits ``[section]`` headers followed by whitespace-separated
        ``key value`` directive lines.  ``[global]`` (when present) is
        always rendered first; remaining sections preserve their input
        order.

        :param sections: Ordered mapping of sections to directives.

        :returns: New file body, terminated with a single newline.
    """
    ordered_keys: list[str] = []
    if "global" in sections:
        ordered_keys.append("global")
    for name in sections.keys():
        if name == "global":
            continue
        ordered_keys.append(name)

    lines: list[str] = []
    for idx, name in enumerate(ordered_keys):
        if idx > 0:
            lines.append("")
        lines.append("[{}]".format(name))
        directives = sections[name]
        for key, value in directives.items():
            if len(value) == 0:
                lines.append(key)
            else:
                lines.append("{} {}".format(key, value))

    body = "\n".join(lines)
    if len(body) > 0 and not body.endswith("\n"):
        body = body + "\n"
    return body


def _sections_to_plain_dict(
    *, sections: "OrderedDict[str, OrderedDict[str, str]]",
) -> dict:
    """
        Convert the ordered-mapping representation of a parsed config
        into plain ``dict[str, dict[str, str]]`` for callers.

        :param sections: Ordered mapping.

        :returns: Plain nested dict.
    """
    rtnval: dict = {}
    for section_name, directives in sections.items():
        rtnval[section_name] = dict(directives)
    return rtnval


def _parse_sysconfig_options(*, text: str) -> Optional[str]:
    """
        Parse the ``OPTIONS=...`` value out of a sysconfig file body.

        Strips surrounding double or single quotes when present.

        :param text: Raw body.

        :returns: Value string or ``None``.
    """
    rtnval: Optional[str] = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if len(stripped) == 0:
            continue
        if stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        head, _, tail = stripped.partition("=")
        key = head.strip()
        if key != "OPTIONS":
            continue
        value = tail.strip()
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1]
        elif len(value) >= 2 and value[0] == "'" and value[-1] == "'":
            value = value[1:-1]
        rtnval = value
    return rtnval


def _render_sysconfig_options(
    *, existing_text: str, value: str,
) -> str:
    """
        Rewrite a sysconfig body so that ``OPTIONS="<value>"`` is the
        active assignment.

        Existing uncommented ``OPTIONS=`` lines are replaced in-place;
        when none is present a fresh line is appended.  Quoting style
        is normalised to double-quotes.

        :param existing_text: Raw existing body.
        :param value: Replacement options string.

        :returns: New body, terminated with a single newline.
    """
    new_assignment = 'OPTIONS="{}"'.format(value)
    kept: list[str] = []
    replaced = False
    for raw_line in existing_text.splitlines():
        stripped = raw_line.strip()
        if len(stripped) == 0:
            kept.append(raw_line)
            continue
        if stripped.startswith("#"):
            kept.append(raw_line)
            continue
        if "=" in stripped:
            head, _, _ = stripped.partition("=")
            if head.strip() == "OPTIONS":
                if replaced is False:
                    kept.append(new_assignment)
                    replaced = True
                continue
        kept.append(raw_line)

    if replaced is False:
        if len(kept) > 0 and len(kept[-1].strip()) != 0:
            kept.append("")
        kept.append(new_assignment)

    body = "\n".join(kept)
    if not body.endswith("\n"):
        body = body + "\n"
    return body


def _parse_pmc_current_data_set(*, text: str) -> PtpCurrentDataSet:
    """
        Parse ``pmc -u -b 0 'GET CURRENT_DATA_SET'`` output.

        Looks for ``stepsRemoved``, ``offsetFromMaster``, and
        ``meanPathDelay`` regardless of their order or surrounding
        decoration.

        :param text: Raw stdout.

        :returns: The :class:`PtpCurrentDataSet`.
    """
    fields = _parse_pmc_keyed_lines(text=text)

    steps = _parse_int_token(value=fields.get("stepsRemoved", ""))
    if steps is None:
        steps = 0
    offset = _parse_int_token(value=fields.get("offsetFromMaster", ""))
    if offset is None:
        offset = 0
    delay = _parse_int_token(value=fields.get("meanPathDelay", ""))
    if delay is None:
        delay = 0

    rtnval = PtpCurrentDataSet(
        steps_removed=steps,
        offset_from_master_ns=offset,
        mean_path_delay_ns=delay,
        raw=text,
    )
    return rtnval


def _parse_pmc_parent_data_set(*, text: str) -> PtpParentDataSet:
    """
        Parse ``pmc -u -b 0 'GET PARENT_DATA_SET'`` output.

        :param text: Raw stdout.

        :returns: The :class:`PtpParentDataSet`.
    """
    fields = _parse_pmc_keyed_lines(text=text)

    parent_id = fields.get("parentPortIdentity", "").strip()
    gm_id = fields.get("grandmasterIdentity", "").strip()

    pri1 = _parse_int_token(value=fields.get("grandmaster.priority1", ""))
    if pri1 is None:
        pri1 = _parse_int_token(value=fields.get("gm.priority1", ""))
    if pri1 is None:
        pri1 = 0

    pri2 = _parse_int_token(value=fields.get("grandmaster.priority2", ""))
    if pri2 is None:
        pri2 = _parse_int_token(value=fields.get("gm.priority2", ""))
    if pri2 is None:
        pri2 = 0

    rtnval = PtpParentDataSet(
        parent_port_identity=parent_id,
        grandmaster_identity=gm_id,
        grandmaster_priority_1=pri1,
        grandmaster_priority_2=pri2,
        raw=text,
    )
    return rtnval


def _parse_pmc_port_data_sets(*, text: str) -> list[PtpPortDataSet]:
    """
        Parse ``pmc -u -b 0 'GET PORT_DATA_SET'`` output into one
        :class:`PtpPortDataSet` per port block.

        Port blocks are delimited by lines containing
        ``portIdentity`` — each new ``portIdentity`` opens a new
        block and any subsequent keyed values up to the next
        ``portIdentity`` belong to that block.

        :param text: Raw stdout.

        :returns: Ports in declaration order.
    """
    rtnval: list[PtpPortDataSet] = []
    blocks: list[list[str]] = []
    current_block: list[str] = []

    for raw_line in text.splitlines():
        if "portIdentity" in raw_line and "=" not in raw_line:
            # New block starts on the portIdentity line.
            tokens = raw_line.strip().split(None, 1)
            if len(tokens) >= 1 and tokens[0] == "portIdentity":
                if len(current_block) > 0:
                    blocks.append(current_block)
                current_block = [raw_line]
                continue
        current_block.append(raw_line)

    if len(current_block) > 0:
        blocks.append(current_block)

    for block_lines in blocks:
        block_text = "\n".join(block_lines)
        fields = _parse_pmc_keyed_lines(text=block_text)
        if "portIdentity" not in fields and "portState" not in fields:
            continue
        port_identity = fields.get("portIdentity", "").strip()
        port_state = fields.get("portState", "").strip()
        log_announce = _parse_int_token(
            value=fields.get("logAnnounceInterval", ""),
        )
        if log_announce is None:
            log_announce = 0
        log_sync = _parse_int_token(
            value=fields.get("logSyncInterval", ""),
        )
        if log_sync is None:
            log_sync = 0
        delay_mech = fields.get("delayMechanism", "").strip()

        info = PtpPortDataSet(
            port_identity=port_identity,
            port_state=port_state,
            log_announce_interval=log_announce,
            log_sync_interval=log_sync,
            delay_mechanism=delay_mech,
            raw=block_text,
        )
        rtnval.append(info)

    return rtnval


def _parse_pmc_keyed_lines(*, text: str) -> dict:
    """
        Convert a ``pmc`` output blob into a flat
        ``key -> value`` mapping.

        Lines like ``stepsRemoved              0`` are split on the
        first whitespace run.  Header / footer lines that don't match
        the ``key value`` shape are ignored.  Last write wins.

        :param text: Raw stdout.

        :returns: Mapping from key to value.
    """
    rtnval: dict = {}
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if len(stripped) == 0:
            continue
        # Skip pmc framing lines: "sending: GET ...", separators, and
        # IDENTITY echoes.
        if stripped.lower().startswith("sending:"):
            continue
        if stripped.startswith("\t") and "RESPONSE" in stripped:
            continue
        if stripped.endswith("RESPONSE"):
            continue
        tokens = stripped.split(None, 1)
        if len(tokens) < 2:
            continue
        key = tokens[0]
        value = tokens[1].strip()
        rtnval[key] = value
    return rtnval


def _parse_int_token(*, value: str) -> Optional[int]:
    """
        Parse a leading-integer token, ignoring trailing decoration.

        :param value: Raw string.

        :returns: Integer or ``None``.
    """
    stripped = value.strip()
    if len(stripped) == 0:
        rtnval: Optional[int] = None
        return rtnval
    head = stripped.split()[0]
    try:
        parsed = int(head)
    except ValueError:
        rtnval = None
        return rtnval
    rtnval = parsed
    return rtnval


def _result_from_service_op(
    *, sm_result, op: str, service: str,
) -> PtpActionResult:
    """
        Translate a service-manager result object into a
        :class:`PtpActionResult` suitable for return from
        :class:`PrecisionTimeManager` lifecycle verbs.

        Recognises ``status`` values whose ``.value`` matches one of
        :class:`PtpActionStatus`'s string codes.

        :param sm_result: The object returned by the service manager.
        :param op: The verb that was invoked.
        :param service: The unit label.

        :returns: The :class:`PtpActionResult`.
    """
    detail = "{}:{}".format(op, service)
    sm_status = getattr(sm_result, "status", None)
    sm_status_value = getattr(sm_status, "value", None)

    mapped_status: PtpActionStatus
    if sm_status_value == PtpActionStatus.SUCCESS.value:
        mapped_status = PtpActionStatus.SUCCESS
    elif sm_status_value == PtpActionStatus.SKIPPED.value:
        mapped_status = PtpActionStatus.SKIPPED
    elif sm_status_value == PtpActionStatus.DRY_RUN.value:
        mapped_status = PtpActionStatus.DRY_RUN
    elif sm_status_value == PtpActionStatus.FAILED.value:
        mapped_status = PtpActionStatus.FAILED
    else:
        mapped_status = PtpActionStatus.FAILED

    rtnval = PtpActionResult(
        status=mapped_status,
        detail=detail,
        exit_code=getattr(sm_result, "exit_code", None),
        stdout=getattr(sm_result, "stdout", None),
        stderr=getattr(sm_result, "stderr", None),
        command=getattr(sm_result, "command", None),
    )
    return rtnval


