# automojo.sysman.linux.redhat.remotelogforwarding
#
# RemoteLogForwardingManager wraps the rsyslog forward-rule
# drop-ins under ``/etc/rsyslog.d/``, the
# ``/etc/systemd/journal-upload.conf`` upload configuration, and
# ``/etc/systemd/journald.conf`` ``ForwardTo*`` directives through
# the ICommandRunner abstraction.
#
# Service-lifecycle calls (rsyslog reload,
# systemd-journal-upload.service enable / disable) delegate to a
# ServiceManager.  This module does NOT auto-restart
# systemd-journald — a journald restart drops in-memory journal
# records, so the spec routes that through 3-service-management.
#
# File I/O for /etc/rsyslog.d/, /etc/systemd/journal-upload.conf,
# /etc/systemd/journald.conf, and /etc/systemd/journald.conf.d/ is
# performed via ``cat`` / ``tee`` / ``rm -f`` / ``ls -1`` /
# ``test -e`` / ``install`` through the runner.

import os

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


# ----------------------------------------------------------------------
# Module-level constants
# ----------------------------------------------------------------------

_TEE = "tee"
_CAT = "cat"
_RM = "rm"
_LS = "ls"
_TEST = "test"
_INSTALL = "install"
_RSYSLOGD = "rsyslogd"
_OPENSSL = "openssl"
_TIMEOUT = "timeout"
_BASH = "bash"

_DEFAULT_RSYSLOG_DROPIN_DIR = "/etc/rsyslog.d"
_DEFAULT_JOURNAL_UPLOAD_CONF = "/etc/systemd/journal-upload.conf"
_DEFAULT_JOURNALD_CONF = "/etc/systemd/journald.conf"
_DEFAULT_JOURNALD_CONF_DROPIN_DIR = "/etc/systemd/journald.conf.d"
_DEFAULT_QUEUE_SPOOL = "/var/spool/rsyslog"

_DEFAULT_RSYSLOG_SERVICE = "rsyslog"
_DEFAULT_UPLOAD_SERVICE = "systemd-journal-upload"

_PROBE_TIMEOUT_SECONDS = "5"

_CONF_SUFFIX = ".conf"

_PROTOCOL_TCP = "tcp"
_PROTOCOL_UDP = "udp"

_VALID_PROTOCOLS = frozenset({_PROTOCOL_TCP, _PROTOCOL_UDP})

_JOURNALD_RESTART_HINT = (
    "systemd-journald.service restart required - route through "
    "3-service-management"
)


LogForwardingActionStatus = ActionStatus


# ----------------------------------------------------------------------
# Data Models
# ----------------------------------------------------------------------


class ForwardingProtocol(Enum):
    """
        Wire protocol used by an rsyslog forward action.

        Plain TCP and UDP variants are supported by the canonical
        omfwd action.  TLS is layered on top of TCP via the gtls
        StreamDriver.
    """
    TCP = "tcp"
    UDP = "udp"


@dataclass
class RemoteLogForwardingConfig(FeatureConfig):
    """
        Configuration for :class:`RemoteLogForwardingManager`.

        Inherits ``dry_run`` and ``elevated`` from
        :class:`FeatureConfig`.

        :param rsyslog_dropin_dir: Directory holding rsyslog
                                   forward drop-ins.
        :param rsyslog_service: ``systemctl`` unit name of the
                                rsyslog daemon to reload after
                                mutations.
        :param journal_upload_conf: Path to the
                                    systemd-journal-upload
                                    configuration file.
        :param journal_upload_service: ``systemctl`` unit name of
                                       the systemd-journal-upload
                                       service.
        :param journald_conf: Path to the journald configuration
                              file.
        :param journald_conf_dropin_dir: Drop-in directory used
                                         for journald configuration
                                         overrides.
        :param default_queue_spool: Default spool directory
                                    referenced when a forward rule
                                    enables an on-disk queue.
    """
    rsyslog_dropin_dir: str = _DEFAULT_RSYSLOG_DROPIN_DIR
    rsyslog_service: str = _DEFAULT_RSYSLOG_SERVICE
    journal_upload_conf: str = _DEFAULT_JOURNAL_UPLOAD_CONF
    journal_upload_service: str = _DEFAULT_UPLOAD_SERVICE
    journald_conf: str = _DEFAULT_JOURNALD_CONF
    journald_conf_dropin_dir: str = _DEFAULT_JOURNALD_CONF_DROPIN_DIR
    default_queue_spool: str = _DEFAULT_QUEUE_SPOOL


# Backwards-compatible alias used in the prompt and feature
# overview.  Mirrors the design.md primary name.
RemoteLogForwardingManagerConfig = RemoteLogForwardingConfig


@dataclass
class LogForwardingActionResult(FeatureActionResult):
    """
        Outcome of a remote-log-forwarding action.

        :param rule_name: The rsyslog forward-rule name targeted,
                          when applicable.
        :param path: The on-disk path the action targeted, when
                     applicable.
    """
    rule_name: Optional[str] = None
    path: Optional[str] = None


# Alias matching the prompt's class-name list.
RemoteLogForwardingActionResult = LogForwardingActionResult


@dataclass
class RsyslogForwardRule:
    """
        Declarative specification for a single rsyslog forward
        rule rendered into ``/etc/rsyslog.d/<name>.conf``.

        :param name: Drop-in basename and idempotency key.  The
                     drop-in is written to
                     ``<rsyslog_dropin_dir>/<name>.conf``.
        :param target_host: Collector hostname or IP.
        :param target_port: Collector port.  Defaults to 514.
        :param protocol: ``"tcp"`` or ``"udp"``.  TLS is layered
                         on top of TCP via the StreamDriver
                         block; callers selecting TLS leave
                         ``protocol`` at ``"tcp"`` and set
                         ``tls=True``.
        :param selector: rsyslog facility.severity selector.
                         Defaults to ``"*.*"``.
        :param tls: When True the renderer emits the gtls
                    StreamDriver block on the action.
        :param tls_ca_file: Path to the CA bundle.  Required when
                            ``tls=True``.
        :param tls_cert_file: Optional client certificate path.
        :param tls_key_file: Optional client key path.
        :param tls_auth_mode: rsyslog StreamDriverAuthMode value.
                              Defaults to ``"x509/name"``.
        :param tls_permitted_peer: Optional permittedPeer value
                                   passed through verbatim to the
                                   action.
        :param queue_filename: Queue file-name prefix.  When set
                               together with ``queue_spool_dir`` /
                               ``queue_max_disk_bytes`` the
                               renderer emits the on-disk queue
                               block.
        :param queue_spool_dir: Spool directory hosting the queue
                                files.  When set the manager
                                ``install``s the directory before
                                writing the drop-in.
        :param queue_max_disk_bytes: Maximum on-disk queue size in
                                     bytes.
        :param queue_save_on_shutdown: When True (default) the
                                       queue is preserved across
                                       rsyslog restarts.
        :param template: Optional named rsyslog template included
                         on the action line.
        :param raw_extra: Free-form rsyslog snippet appended to
                          the rendered drop-in below the action
                          line.  Escape hatch for rarely-used
                          options.
    """
    name: str
    target_host: str
    target_port: int = 514
    protocol: str = _PROTOCOL_TCP
    selector: str = "*.*"
    tls: bool = False
    tls_ca_file: Optional[str] = None
    tls_cert_file: Optional[str] = None
    tls_key_file: Optional[str] = None
    tls_auth_mode: str = "x509/name"
    tls_permitted_peer: Optional[str] = None
    queue_filename: Optional[str] = None
    queue_spool_dir: Optional[str] = None
    queue_max_disk_bytes: Optional[int] = None
    queue_save_on_shutdown: bool = True
    template: Optional[str] = None
    raw_extra: Optional[str] = None


@dataclass
class ForwardingTarget:
    """
        Parsed view of a forward target derived from a drop-in
        body or a legacy ``@@host:port`` action line.

        :param name: Rule name (drop-in basename without
                     ``.conf``).
        :param host: Collector host parsed from the action.
        :param port: Collector port parsed from the action.
        :param protocol: ``"tcp"`` or ``"udp"`` parsed from the
                         action line.
        :param tls: True when the parsed action carries the gtls
                    StreamDriver block.
        :param selector: rsyslog selector, e.g. ``"*.*"``.
        :param path: Absolute path of the drop-in this target was
                     parsed from, when applicable.
    """
    name: str
    host: str
    port: int
    protocol: str
    tls: bool
    selector: str
    path: Optional[str] = None


@dataclass
class RsyslogConfigState:
    """
        Result of :meth:`RemoteLogForwardingManager.validate_rsyslog`.

        :param ok: True when ``rsyslogd -N1`` exited zero.
        :param exit_code: Captured exit code.
        :param stdout: Captured stdout from the validate run.
        :param stderr: Captured stderr from the validate run.
        :param command: The argv that was dispatched.
    """
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    command: list[str] = field(default_factory=list)


@dataclass
class LogForwardingHealth:
    """
        Result of :meth:`RemoteLogForwardingManager.probe_collector`.

        :param host: Collector host probed.
        :param port: Collector port probed.
        :param reachable: True when the probe completed without a
                          transport-level failure.  Probe timeouts
                          surface as ``reachable=False`` rather
                          than raising.
        :param tls_verified: TLS verification outcome.  ``None``
                             for non-TLS probes.
        :param raw: Captured probe stdout / stderr concatenation
                    for diagnostics.
    """
    host: str
    port: int
    reachable: bool
    tls_verified: Optional[bool]
    raw: str


# Alias matching the prompt's class-name list.
ProbeResult = LogForwardingHealth


# ----------------------------------------------------------------------
# Exceptions
# ----------------------------------------------------------------------


class ForwarderQueryError(Exception):
    """
        Raised when a remote-log-forwarding read fails for a
        reason other than the target simply being absent.
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
        self.command = list(command)
        self.exit_code = exit_code
        self.stderr = stderr

        err_msg_lines = ["Forwarder query command failed."]
        err_msg_lines.append("    COMMAND: {}".format(" ".join(command)))
        err_msg_lines.append("    EXIT_CODE: {}".format(exit_code))
        err_msg_lines.append("    STDERR: {}".format(stderr))
        err_msg = os.linesep.join(err_msg_lines)
        super().__init__(err_msg)
        return


# ----------------------------------------------------------------------
# RemoteLogForwardingManager
# ----------------------------------------------------------------------


class RemoteLogForwardingManager(BaseFeatureManager):
    """
        Facade for rsyslog forward drop-ins,
        systemd-journal-upload, and journald ``ForwardTo*``
        directives.

        Every verb dispatches through the injected
        :class:`ICommandRunner`.  Service-lifecycle calls
        delegate to an injected service manager (Part C seam).
        Direct ``systemctl`` calls are not spelled here.
    """

    result_class = LogForwardingActionResult
    feature_name = "remote-log-forwarding"

    def __init__(
        self,
        runner,
        config: Optional[RemoteLogForwardingConfig] = None,
        *,
        service_manager=None,
        report=None,
    ) -> None:
        """
            :param runner: The command runner used by every
                           dispatch.
            :param config: Manager configuration.  Defaults to a
                           fresh
                           :class:`RemoteLogForwardingConfig`
                           when omitted.
            :param service_manager: Optional service-lifecycle
                                    delegate.  When omitted the
                                    manager constructs a default
                                    :class:`ServiceManager` using
                                    the same runner and a
                                    matching dry-run / elevation
                                    posture.
            :param report: Optional :class:`MaintenanceReport` to
                           receive an :class:`ActionRecord` per
                           dispatched mutation.
        """
        if config is None:
            config = RemoteLogForwardingConfig()

        super().__init__(runner=runner, config=config, report=report)

        if service_manager is None:
            from automojo.sysman.linux.redhat.servicemanagement import (
                ServiceManager,
                ServiceManagerConfig,
            )
            service_manager = ServiceManager(
                runner,
                ServiceManagerConfig(
                    dry_run=self._config.dry_run,
                    elevated=self._config.elevated,
                ),
            )

        self._service_manager = service_manager
        return

    # ------------------------------------------------------------------
    # Typed configuration accessor
    # ------------------------------------------------------------------

    def _typed_config(self) -> RemoteLogForwardingConfig:
        """
            Return the manager configuration typed as
            :class:`RemoteLogForwardingConfig`.
        """
        if isinstance(self._config, RemoteLogForwardingConfig):
            rtnval = self._config
            return rtnval
        rtnval = RemoteLogForwardingConfig(
            dry_run=self._config.dry_run,
            elevated=self._config.elevated,
        )
        return rtnval

    # ------------------------------------------------------------------
    # rsyslog forward rules
    # ------------------------------------------------------------------

    def add_forward_rule(
        self,
        rule: RsyslogForwardRule,
    ) -> LogForwardingActionResult:
        """
            Render and write the rsyslog forward drop-in for
            ``rule``.

            Idempotent: returns ``SKIPPED`` when the on-disk
            drop-in body matches the rendered desired body
            byte-for-byte.

            On a content change the manager runs ``rsyslogd -N1``
            to validate the configuration; on a non-zero exit the
            verb returns ``FAILED`` without delegating a service
            reload.  When validation passes the rsyslog service
            is reloaded via the injected service manager.

            :param rule: The :class:`RsyslogForwardRule` to write.

            :returns: The :class:`LogForwardingActionResult`.
        """
        validation_failure = _validate_rule(rule=rule)
        if validation_failure is not None:
            failed = LogForwardingActionResult(
                status=LogForwardingActionStatus.FAILED,
                detail=rule.name,
                rule_name=rule.name,
                stderr=validation_failure,
            )
            return failed

        cfg = self._typed_config()
        path = self._dropin_path(name=rule.name)
        desired_body = _render_forward_rule(rule=rule)
        write_command = [_TEE, path]

        existing = self._read_file(path=path)
        if existing is not None and existing == desired_body:
            skipped = LogForwardingActionResult(
                status=LogForwardingActionStatus.SKIPPED,
                detail=rule.name,
                command=list(write_command),
                rule_name=rule.name,
                path=path,
            )
            return skipped

        if cfg.dry_run:
            dry = LogForwardingActionResult(
                status=LogForwardingActionStatus.DRY_RUN,
                detail=rule.name,
                command=list(write_command),
                rule_name=rule.name,
                path=path,
            )
            return dry

        if rule.queue_spool_dir is not None and len(rule.queue_spool_dir) > 0:
            spool_present = self._path_exists(path=rule.queue_spool_dir)
            if not spool_present:
                install_command = [
                    _INSTALL, "-d",
                    "-o", "syslog",
                    "-g", "syslog",
                    "-m", "0700",
                    rule.queue_spool_dir,
                ]
                install_result = self._runner.run(
                    list(install_command),
                    elevated=cfg.elevated,
                )
                if install_result.exit_code != 0:
                    failed = LogForwardingActionResult(
                        status=LogForwardingActionStatus.FAILED,
                        detail=rule.name,
                        exit_code=install_result.exit_code,
                        stdout=install_result.stdout,
                        stderr=install_result.stderr,
                        command=list(install_command),
                        rule_name=rule.name,
                        path=rule.queue_spool_dir,
                    )
                    return failed

        write_result = self._runner.run(
            list(write_command),
            elevated=cfg.elevated,
            stdin=desired_body,
        )
        if write_result.exit_code != 0:
            failed = LogForwardingActionResult(
                status=LogForwardingActionStatus.FAILED,
                detail=rule.name,
                exit_code=write_result.exit_code,
                stdout=write_result.stdout,
                stderr=write_result.stderr,
                command=list(write_command),
                rule_name=rule.name,
                path=path,
            )
            return failed

        validate_state = self.validate_rsyslog()
        if not validate_state.ok:
            failed = LogForwardingActionResult(
                status=LogForwardingActionStatus.FAILED,
                detail=rule.name,
                exit_code=validate_state.exit_code,
                stdout=validate_state.stdout,
                stderr=validate_state.stderr,
                command=list(validate_state.command),
                rule_name=rule.name,
                path=path,
            )
            return failed

        reload_result = self._service_manager.reload(cfg.rsyslog_service)
        if reload_result.status == LogForwardingActionStatus.FAILED:
            failed = LogForwardingActionResult(
                status=LogForwardingActionStatus.FAILED,
                detail=rule.name,
                exit_code=reload_result.exit_code,
                stdout=reload_result.stdout,
                stderr=reload_result.stderr,
                command=list(reload_result.command) if reload_result.command is not None else None,
                rule_name=rule.name,
                path=path,
            )
            return failed

        rtnval = LogForwardingActionResult(
            status=LogForwardingActionStatus.SUCCESS,
            detail=rule.name,
            exit_code=write_result.exit_code,
            stdout=write_result.stdout,
            stderr=write_result.stderr,
            command=list(write_command),
            rule_name=rule.name,
            path=path,
        )
        return rtnval

    def remove_forward_rule(self, name: str) -> LogForwardingActionResult:
        """
            Delete an rsyslog forward drop-in.

            Idempotent: returns ``SKIPPED`` when the drop-in is
            already absent.  On removal the rsyslog config is
            re-validated before the reload is delegated.

            :param name: The rule name (drop-in basename without
                         ``.conf``).

            :returns: The :class:`LogForwardingActionResult`.
        """
        cfg = self._typed_config()
        path = self._dropin_path(name=name)
        command = [_RM, "-f", path]

        present = self._path_exists(path=path)
        if not present:
            skipped = LogForwardingActionResult(
                status=LogForwardingActionStatus.SKIPPED,
                detail=name,
                command=list(command),
                rule_name=name,
                path=path,
            )
            return skipped

        if cfg.dry_run:
            dry = LogForwardingActionResult(
                status=LogForwardingActionStatus.DRY_RUN,
                detail=name,
                command=list(command),
                rule_name=name,
                path=path,
            )
            return dry

        run_result = self._runner.run(
            list(command),
            elevated=cfg.elevated,
        )
        if run_result.exit_code != 0:
            failed = LogForwardingActionResult(
                status=LogForwardingActionStatus.FAILED,
                detail=name,
                exit_code=run_result.exit_code,
                stdout=run_result.stdout,
                stderr=run_result.stderr,
                command=list(command),
                rule_name=name,
                path=path,
            )
            return failed

        validate_state = self.validate_rsyslog()
        if not validate_state.ok:
            failed = LogForwardingActionResult(
                status=LogForwardingActionStatus.FAILED,
                detail=name,
                exit_code=validate_state.exit_code,
                stdout=validate_state.stdout,
                stderr=validate_state.stderr,
                command=list(validate_state.command),
                rule_name=name,
                path=path,
            )
            return failed

        reload_result = self._service_manager.reload(cfg.rsyslog_service)
        if reload_result.status == LogForwardingActionStatus.FAILED:
            failed = LogForwardingActionResult(
                status=LogForwardingActionStatus.FAILED,
                detail=name,
                exit_code=reload_result.exit_code,
                stdout=reload_result.stdout,
                stderr=reload_result.stderr,
                command=list(reload_result.command) if reload_result.command is not None else None,
                rule_name=name,
                path=path,
            )
            return failed

        rtnval = LogForwardingActionResult(
            status=LogForwardingActionStatus.SUCCESS,
            detail=name,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(command),
            rule_name=name,
            path=path,
        )
        return rtnval

    def list_forward_rules(self) -> list[ForwardingTarget]:
        """
            Enumerate every drop-in under
            ``<rsyslog_dropin_dir>`` whose body parses into a
            forward action.

            Reads only — never elevates.  Drop-ins that do not
            parse (e.g. operator-authored unrelated drop-ins) are
            silently skipped.

            :returns: One :class:`ForwardingTarget` per parseable
                      drop-in.

            :raises ForwarderQueryError: When ``ls`` reports a
                                          non-zero exit with a
                                          non-empty stderr.
        """
        cfg = self._typed_config()
        ls_command = [_LS, "-1", cfg.rsyslog_dropin_dir]
        ls_result = self._runner.run(list(ls_command), elevated=False)

        if ls_result.exit_code != 0 and len(ls_result.stderr.strip()) > 0:
            raise ForwarderQueryError(
                command=ls_command,
                exit_code=ls_result.exit_code,
                stderr=ls_result.stderr,
            )

        rtnval: list[ForwardingTarget] = []
        for raw_entry in ls_result.stdout.splitlines():
            entry = raw_entry.strip()
            if len(entry) == 0:
                continue
            if not entry.endswith(_CONF_SUFFIX):
                continue

            name_value = entry[: -len(_CONF_SUFFIX)]
            full_path = "{}/{}".format(cfg.rsyslog_dropin_dir, entry)
            body = self._read_file(path=full_path)
            if body is None:
                continue

            parsed = _parse_forward_body(body=body)
            if parsed is None:
                continue

            host_value, port_value, protocol_value, tls_value, selector_value = parsed
            target = ForwardingTarget(
                name=name_value,
                host=host_value,
                port=port_value,
                protocol=protocol_value,
                tls=tls_value,
                selector=selector_value,
                path=full_path,
            )
            rtnval.append(target)

        return rtnval

    def validate_rsyslog(self) -> RsyslogConfigState:
        """
            Run ``rsyslogd -N1`` and return the result as a
            :class:`RsyslogConfigState`.

            Reads never elevate; the validate dispatch always
            runs without elevation because ``-N1`` only parses
            the configuration.

            :returns: The :class:`RsyslogConfigState`.
        """
        command = [_RSYSLOGD, "-N", "1"]
        run_result = self._runner.run(list(command), elevated=False)
        ok_value = run_result.exit_code == 0
        rtnval = RsyslogConfigState(
            ok=ok_value,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(command),
        )
        return rtnval

    # ------------------------------------------------------------------
    # systemd-journal-upload
    # ------------------------------------------------------------------

    def get_upload_config(self) -> dict[str, str]:
        """
            Parse the ``[Upload]`` section of
            ``<journal_upload_conf>`` into a flat mapping.

            Reads never elevate.  When the file is missing the
            method returns an empty mapping.

            :returns: ``dict[str, str]`` of parsed directives.
        """
        cfg = self._typed_config()
        body = self._read_file(path=cfg.journal_upload_conf)
        if body is None:
            rtnval: dict[str, str] = {}
            return rtnval
        rtnval = _parse_section(body=body, section="Upload")
        return rtnval

    def set_upload_config(
        self,
        directives: dict[str, str],
    ) -> LogForwardingActionResult:
        """
            Upsert each ``key=value`` pair from ``directives``
            into the ``[Upload]`` section of
            ``<journal_upload_conf>``.

            Idempotent: returns ``SKIPPED`` when every requested
            key already matches the on-disk value.

            :param directives: Mapping of directive names to
                               desired values.

            :returns: The :class:`LogForwardingActionResult`.
        """
        cfg = self._typed_config()
        path = cfg.journal_upload_conf
        write_command = [_TEE, path]

        existing_body = self._read_file(path=path)
        if existing_body is None:
            existing_body = ""

        merged_body = _upsert_section(
            body=existing_body,
            section="Upload",
            directives=directives,
        )

        if merged_body == existing_body:
            skipped = LogForwardingActionResult(
                status=LogForwardingActionStatus.SKIPPED,
                detail="journal-upload",
                command=list(write_command),
                path=path,
            )
            return skipped

        if cfg.dry_run:
            dry = LogForwardingActionResult(
                status=LogForwardingActionStatus.DRY_RUN,
                detail="journal-upload",
                command=list(write_command),
                path=path,
            )
            return dry

        run_result = self._runner.run(
            list(write_command),
            elevated=cfg.elevated,
            stdin=merged_body,
        )
        if run_result.exit_code != 0:
            failed = LogForwardingActionResult(
                status=LogForwardingActionStatus.FAILED,
                detail="journal-upload",
                exit_code=run_result.exit_code,
                stdout=run_result.stdout,
                stderr=run_result.stderr,
                command=list(write_command),
                path=path,
            )
            return failed

        rtnval = LogForwardingActionResult(
            status=LogForwardingActionStatus.SUCCESS,
            detail="journal-upload",
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(write_command),
            path=path,
        )
        return rtnval

    def enable_upload(
        self,
        *,
        start: bool = True,
    ) -> LogForwardingActionResult:
        """
            Enable the ``systemd-journal-upload`` service via the
            injected service manager.

            :param start: When True (default) the service is also
                          started immediately.

            :returns: The :class:`LogForwardingActionResult`.
        """
        cfg = self._typed_config()
        enable_result = self._service_manager.enable(cfg.journal_upload_service)
        if enable_result.status == LogForwardingActionStatus.FAILED:
            failed = _coerce_to_forwarding_result(
                base_result=enable_result,
                detail=cfg.journal_upload_service,
            )
            return failed

        if start:
            start_result = self._service_manager.start(cfg.journal_upload_service)
            rtnval = _coerce_to_forwarding_result(
                base_result=start_result,
                detail=cfg.journal_upload_service,
            )
            return rtnval

        rtnval = _coerce_to_forwarding_result(
            base_result=enable_result,
            detail=cfg.journal_upload_service,
        )
        return rtnval

    def disable_upload(self) -> LogForwardingActionResult:
        """
            Stop and disable the ``systemd-journal-upload``
            service via the injected service manager.

            :returns: The :class:`LogForwardingActionResult`.
        """
        cfg = self._typed_config()
        stop_result = self._service_manager.stop(cfg.journal_upload_service)
        if stop_result.status == LogForwardingActionStatus.FAILED:
            failed = _coerce_to_forwarding_result(
                base_result=stop_result,
                detail=cfg.journal_upload_service,
            )
            return failed

        disable_result = self._service_manager.disable(cfg.journal_upload_service)
        rtnval = _coerce_to_forwarding_result(
            base_result=disable_result,
            detail=cfg.journal_upload_service,
        )
        return rtnval

    # ------------------------------------------------------------------
    # journald.conf ForwardTo* directives
    # ------------------------------------------------------------------

    def get_journald_directives(self) -> dict[str, str]:
        """
            Return the merged ``[Journal]`` directives from the
            main ``journald.conf`` plus every drop-in under
            ``<journald_conf_dropin_dir>``.

            Drop-ins shadow the main file in lexical-sorted
            order; later definitions overwrite earlier ones, the
            same as systemd's own merge semantics.

            :returns: ``dict[str, str]`` of merged directives.
        """
        cfg = self._typed_config()
        merged: dict[str, str] = {}

        main_body = self._read_file(path=cfg.journald_conf)
        if main_body is not None:
            main_directives = _parse_section(body=main_body, section="Journal")
            merged.update(main_directives)

        ls_command = [_LS, "-1", cfg.journald_conf_dropin_dir]
        ls_result = self._runner.run(list(ls_command), elevated=False)
        if ls_result.exit_code == 0:
            entries: list[str] = []
            for raw_entry in ls_result.stdout.splitlines():
                entry = raw_entry.strip()
                if len(entry) == 0:
                    continue
                if not entry.endswith(_CONF_SUFFIX):
                    continue
                entries.append(entry)
            entries.sort()
            for entry in entries:
                full_path = "{}/{}".format(
                    cfg.journald_conf_dropin_dir, entry,
                )
                body = self._read_file(path=full_path)
                if body is None:
                    continue
                directives = _parse_section(body=body, section="Journal")
                merged.update(directives)

        return merged

    def set_journald_directive(
        self,
        key: str,
        value: str,
        *,
        dropin_name: Optional[str] = None,
    ) -> LogForwardingActionResult:
        """
            Upsert a single ``[Journal]`` directive in the main
            journald.conf or, when ``dropin_name`` is supplied,
            in a drop-in under
            ``<journald_conf_dropin_dir>/<dropin_name>.conf``.

            Idempotent: returns ``SKIPPED`` when the on-disk
            value already matches.

            On a successful write the result's ``detail`` carries
            the
            ``"systemd-journald.service restart required ..."``
            hint because this manager does NOT auto-restart
            journald (a restart drops in-memory journal records).

            :param key: The directive name.
            :param value: The desired directive value.
            :param dropin_name: Optional drop-in basename
                                (without ``.conf``).  When
                                ``None`` the main journald.conf
                                is updated.

            :returns: The :class:`LogForwardingActionResult`.
        """
        cfg = self._typed_config()
        if dropin_name is None or len(dropin_name) == 0:
            path = cfg.journald_conf
        else:
            path = "{}/{}{}".format(
                cfg.journald_conf_dropin_dir, dropin_name, _CONF_SUFFIX,
            )

        write_command = [_TEE, path]

        existing_body = self._read_file(path=path)
        if existing_body is None:
            existing_body = ""

        merged_body = _upsert_section(
            body=existing_body,
            section="Journal",
            directives={key: value},
        )

        detail_text = "{} (path={}; {})".format(
            key, path, _JOURNALD_RESTART_HINT,
        )

        if merged_body == existing_body:
            skipped = LogForwardingActionResult(
                status=LogForwardingActionStatus.SKIPPED,
                detail=detail_text,
                command=list(write_command),
                path=path,
            )
            return skipped

        if cfg.dry_run:
            dry = LogForwardingActionResult(
                status=LogForwardingActionStatus.DRY_RUN,
                detail=detail_text,
                command=list(write_command),
                path=path,
            )
            return dry

        run_result = self._runner.run(
            list(write_command),
            elevated=cfg.elevated,
            stdin=merged_body,
        )
        if run_result.exit_code != 0:
            failed = LogForwardingActionResult(
                status=LogForwardingActionStatus.FAILED,
                detail=detail_text,
                exit_code=run_result.exit_code,
                stdout=run_result.stdout,
                stderr=run_result.stderr,
                command=list(write_command),
                path=path,
            )
            return failed

        rtnval = LogForwardingActionResult(
            status=LogForwardingActionStatus.SUCCESS,
            detail=detail_text,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(write_command),
            path=path,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Probe
    # ------------------------------------------------------------------

    def probe_collector(
        self,
        host: str,
        port: int,
        *,
        tls: bool = False,
    ) -> LogForwardingHealth:
        """
            Probe whether ``host:port`` accepts connections.

            For TLS probes the manager dispatches
            ``openssl s_client -connect <host>:<port> -verify 1
            -brief`` and inspects the captured output for
            ``Verification: OK``.

            For plain TCP probes the manager dispatches
            ``timeout 5 bash -c 'cat < /dev/tcp/<host>/<port>'``;
            ``timeout``'s 124 exit code surfaces as
            ``reachable=False`` rather than an error.

            Reads never elevate; the probe never raises.

            :param host: Collector host.
            :param port: Collector port.
            :param tls: True for a TLS probe.

            :returns: The :class:`LogForwardingHealth`.
        """
        if tls:
            command = [
                _OPENSSL, "s_client",
                "-connect", "{}:{}".format(host, port),
                "-verify", "1",
                "-brief",
            ]
            run_result = self._runner.run(list(command), elevated=False)
            raw_output = "{}\n{}".format(run_result.stdout, run_result.stderr)
            reachable_value = run_result.exit_code == 0
            tls_verified_value: Optional[bool] = "Verification: OK" in raw_output
            rtnval = LogForwardingHealth(
                host=host,
                port=port,
                reachable=reachable_value,
                tls_verified=tls_verified_value,
                raw=raw_output,
            )
            return rtnval

        command = [
            _TIMEOUT, _PROBE_TIMEOUT_SECONDS,
            _BASH, "-c",
            "cat < /dev/tcp/{}/{}".format(host, port),
        ]
        run_result = self._runner.run(list(command), elevated=False)
        raw_output = "{}\n{}".format(run_result.stdout, run_result.stderr)
        reachable_value = run_result.exit_code == 0
        rtnval = LogForwardingHealth(
            host=host,
            port=port,
            reachable=reachable_value,
            tls_verified=None,
            raw=raw_output,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _dropin_path(self, *, name: str) -> str:
        """
            Build the absolute path to the drop-in for ``name``.

            :param name: The forward-rule name.

            :returns: ``<rsyslog_dropin_dir>/<name>.conf``.
        """
        cfg = self._typed_config()
        rtnval = "{}/{}{}".format(
            cfg.rsyslog_dropin_dir, name, _CONF_SUFFIX,
        )
        return rtnval

    def _path_exists(self, *, path: str) -> bool:
        """
            Probe a path's existence via ``test -e`` without
            elevation.

            :param path: The absolute path to probe.

            :returns: True when ``test -e`` exits zero.
        """
        command = [_TEST, "-e", path]
        run_result = self._runner.run(list(command), elevated=False)
        rtnval = run_result.exit_code == 0
        return rtnval

    def _read_file(self, *, path: str) -> Optional[str]:
        """
            Read a file via ``cat`` without elevation.

            :param path: The absolute path to read.

            :returns: The captured stdout on success; ``None``
                      when ``cat`` exits non-zero.
        """
        command = [_CAT, path]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: Optional[str] = None
            return rtnval
        rtnval = run_result.stdout
        return rtnval


# ----------------------------------------------------------------------
# Module-level helpers
# ----------------------------------------------------------------------


def _coerce_to_forwarding_result(
    *,
    base_result: FeatureActionResult,
    detail: str,
) -> LogForwardingActionResult:
    """
        Map a delegated :class:`FeatureActionResult` (e.g. one
        returned by :class:`ServiceManager`) onto a
        :class:`LogForwardingActionResult`.

        :param base_result: The delegated result.
        :param detail: The detail string to attach to the
                       forwarding result.

        :returns: The :class:`LogForwardingActionResult`.
    """
    rtnval = LogForwardingActionResult(
        status=base_result.status,
        detail=detail,
        exit_code=base_result.exit_code,
        stdout=base_result.stdout,
        stderr=base_result.stderr,
        command=list(base_result.command) if base_result.command is not None else None,
    )
    return rtnval


def _validate_rule(*, rule: RsyslogForwardRule) -> Optional[str]:
    """
        Validate an :class:`RsyslogForwardRule` before render.

        :param rule: The :class:`RsyslogForwardRule` to validate.

        :returns: ``None`` when valid; an error-message string
                  when not.
    """
    if rule.name is None or len(rule.name) == 0:
        rtnval: Optional[str] = "rule.name is required"
        return rtnval
    if rule.target_host is None or len(rule.target_host) == 0:
        rtnval = "rule.target_host is required"
        return rtnval
    if rule.target_port is None:
        rtnval = "rule.target_port is required"
        return rtnval
    if rule.target_port <= 0:
        rtnval = "rule.target_port must be positive"
        return rtnval
    if rule.protocol not in _VALID_PROTOCOLS:
        rtnval = "rule.protocol must be one of {}".format(
            sorted(_VALID_PROTOCOLS),
        )
        return rtnval
    if rule.tls:
        if rule.tls_ca_file is None or len(rule.tls_ca_file) == 0:
            rtnval = "rule.tls_ca_file is required when tls=True"
            return rtnval
    if rule.queue_filename is not None and len(rule.queue_filename) > 0:
        if rule.queue_spool_dir is None or len(rule.queue_spool_dir) == 0:
            rtnval = "rule.queue_spool_dir is required when queue_filename is set"
            return rtnval

    rtnval = None
    return rtnval


def _render_forward_rule(*, rule: RsyslogForwardRule) -> str:
    """
        Render an :class:`RsyslogForwardRule` into a canonical
        ``action(type="omfwd" ...)`` drop-in body.

        Output is byte-stable for matching inputs so that the
        idempotency comparison in
        :meth:`RemoteLogForwardingManager.add_forward_rule`
        works.

        :param rule: The :class:`RsyslogForwardRule` to render.

        :returns: The rendered drop-in body ending in ``\\n``.
    """
    action_pairs: list[tuple[str, str]] = []
    action_pairs.append(("type", "omfwd"))
    action_pairs.append(("target", rule.target_host))
    action_pairs.append(("port", str(rule.target_port)))
    action_pairs.append(("protocol", rule.protocol))

    if rule.template is not None and len(rule.template) > 0:
        action_pairs.append(("template", rule.template))

    if rule.tls:
        action_pairs.append(("StreamDriver", "gtls"))
        action_pairs.append(("StreamDriverMode", "1"))
        action_pairs.append(("StreamDriverAuthMode", rule.tls_auth_mode))
        if rule.tls_permitted_peer is not None and len(rule.tls_permitted_peer) > 0:
            action_pairs.append(("permittedPeer", rule.tls_permitted_peer))

    if rule.queue_filename is not None and len(rule.queue_filename) > 0:
        action_pairs.append(("queue.type", "LinkedList"))
        action_pairs.append(("queue.filename", rule.queue_filename))
        if rule.queue_spool_dir is not None and len(rule.queue_spool_dir) > 0:
            action_pairs.append(("queue.spoolDirectory", rule.queue_spool_dir))
        if rule.queue_max_disk_bytes is not None:
            action_pairs.append((
                "queue.maxDiskSpace", str(rule.queue_max_disk_bytes),
            ))
        if rule.queue_save_on_shutdown:
            action_pairs.append(("queue.saveOnShutdown", "on"))
        else:
            action_pairs.append(("queue.saveOnShutdown", "off"))

    rendered_pairs: list[str] = []
    for key, value in action_pairs:
        rendered_pairs.append('{}="{}"'.format(key, value))
    action_body = " ".join(rendered_pairs)
    action_line = "{} action({})".format(rule.selector, action_body)

    body_lines: list[str] = []
    body_lines.append("# automojo remote-log-forwarding")
    body_lines.append("# Rule: {}".format(rule.name))

    if rule.tls:
        if rule.tls_ca_file is not None and len(rule.tls_ca_file) > 0:
            body_lines.append(
                'global(DefaultNetstreamDriverCAFile="{}")'.format(
                    rule.tls_ca_file,
                ),
            )
        if rule.tls_cert_file is not None and len(rule.tls_cert_file) > 0:
            body_lines.append(
                'global(DefaultNetstreamDriverCertFile="{}")'.format(
                    rule.tls_cert_file,
                ),
            )
        if rule.tls_key_file is not None and len(rule.tls_key_file) > 0:
            body_lines.append(
                'global(DefaultNetstreamDriverKeyFile="{}")'.format(
                    rule.tls_key_file,
                ),
            )

    body_lines.append(action_line)

    if rule.raw_extra is not None and len(rule.raw_extra) > 0:
        body_lines.append(rule.raw_extra)

    rtnval = "\n".join(body_lines) + "\n"
    return rtnval


def _parse_forward_body(*, body: str) -> Optional[tuple]:
    """
        Parse a drop-in body looking for either an ``omfwd``
        action call or a legacy ``@@host:port`` / ``@host:port``
        action line.

        :param body: The drop-in body.

        :returns: A 5-tuple ``(host, port, protocol, tls,
                  selector)`` on success; ``None`` when no
                  forward action could be located.
    """
    has_gtls_global = False

    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if len(stripped) == 0:
            continue
        if stripped.startswith("#"):
            continue
        if "DefaultNetstreamDriverCAFile" in stripped:
            has_gtls_global = True
            continue

        # Modern omfwd action: <selector> action(type="omfwd" ...)
        action_idx = stripped.find("action(")
        if action_idx > 0 and "omfwd" in stripped:
            selector_part = stripped[:action_idx].strip()
            action_close = stripped.rfind(")")
            if action_close < 0:
                continue
            action_inner = stripped[action_idx + len("action("): action_close]
            kv_map = _parse_action_kv(text=action_inner)
            host_value = kv_map.get("target", "")
            port_text = kv_map.get("port", "0")
            try:
                port_value = int(port_text)
            except ValueError:
                port_value = 0
            protocol_value = kv_map.get("protocol", _PROTOCOL_TCP)
            tls_value = kv_map.get("StreamDriver", "") == "gtls"
            if has_gtls_global:
                tls_value = True
            rtnval: Optional[tuple] = (
                host_value, port_value, protocol_value,
                tls_value, selector_part,
            )
            return rtnval

        # Legacy action: <selector> @@host:port  (TCP)
        # Or:           <selector> @host:port    (UDP)
        tokens = stripped.split(None, 1)
        if len(tokens) < 2:
            continue
        selector_token = tokens[0]
        target_remainder = tokens[1].split(";", 1)[0].strip()

        if target_remainder.startswith("@@"):
            host_port = target_remainder[2:]
            protocol_value = _PROTOCOL_TCP
        elif target_remainder.startswith("@"):
            host_port = target_remainder[1:]
            protocol_value = _PROTOCOL_UDP
        else:
            continue

        host_value, port_value = _split_host_port(host_port=host_port)
        if host_value is None:
            continue

        rtnval = (
            host_value, port_value, protocol_value,
            has_gtls_global, selector_token,
        )
        return rtnval

    rtnval = None
    return rtnval


def _parse_action_kv(*, text: str) -> dict[str, str]:
    """
        Parse the ``key="value" key2="value2"`` body of an
        rsyslog ``action(...)`` construct into a flat mapping.

        :param text: The text between ``action(`` and ``)``.

        :returns: ``dict[str, str]`` of parsed pairs.  Values are
                  stored without their surrounding double quotes.
    """
    rtnval: dict[str, str] = {}
    cursor = 0
    text_len = len(text)
    while cursor < text_len:
        # Skip whitespace
        while cursor < text_len and text[cursor].isspace():
            cursor += 1
        if cursor >= text_len:
            break

        # Read key up to '='
        eq_idx = text.find("=", cursor)
        if eq_idx < 0:
            break
        key_token = text[cursor:eq_idx].strip()
        cursor = eq_idx + 1
        if cursor >= text_len:
            break

        # Expect a double-quoted value
        if text[cursor] != '"':
            # Unquoted value: read up to next whitespace.
            value_start = cursor
            while cursor < text_len and not text[cursor].isspace():
                cursor += 1
            value_token = text[value_start:cursor]
        else:
            cursor += 1
            value_start = cursor
            while cursor < text_len and text[cursor] != '"':
                cursor += 1
            value_token = text[value_start:cursor]
            if cursor < text_len:
                cursor += 1

        if len(key_token) > 0:
            rtnval[key_token] = value_token

    return rtnval


def _split_host_port(*, host_port: str) -> tuple:
    """
        Split a ``host:port`` string into its components.

        :param host_port: The combined token.

        :returns: A two-tuple ``(host, port)``.  ``host`` is
                  ``None`` when the token cannot be parsed.
    """
    if host_port is None or len(host_port) == 0:
        rtnval = (None, 0)
        return rtnval

    if ":" not in host_port:
        rtnval = (host_port, 0)
        return rtnval

    last_colon = host_port.rfind(":")
    host_value = host_port[:last_colon]
    port_text = host_port[last_colon + 1:]
    try:
        port_value = int(port_text)
    except ValueError:
        rtnval = (None, 0)
        return rtnval

    rtnval = (host_value, port_value)
    return rtnval


def _parse_section(*, body: str, section: str) -> dict[str, str]:
    """
        Parse one INI-style section out of an arbitrary
        ``systemd``-style configuration body.

        :param body: The full file body.
        :param section: Section name without surrounding
                        brackets.

        :returns: ``dict[str, str]`` of ``key=value`` pairs found
                  inside ``[section]``.
    """
    rtnval: dict[str, str] = {}
    target_header = "[{}]".format(section)
    in_section = False

    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if len(stripped) == 0:
            continue
        if stripped.startswith("#") or stripped.startswith(";"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            if stripped == target_header:
                in_section = True
            else:
                in_section = False
            continue
        if not in_section:
            continue
        if "=" not in stripped:
            continue
        eq_idx = stripped.find("=")
        key_token = stripped[:eq_idx].strip()
        value_token = stripped[eq_idx + 1:].strip()
        if len(key_token) == 0:
            continue
        rtnval[key_token] = value_token

    return rtnval


def _upsert_section(
    *,
    body: str,
    section: str,
    directives: dict[str, str],
) -> str:
    """
        Upsert each ``key=value`` pair from ``directives`` inside
        the ``[section]`` block of ``body``.

        Sections other than the target are preserved verbatim.
        Comments inside the target section are preserved.  When
        the target section is missing, it is appended at the end
        of the body.

        :param body: The full file body.  Empty bodies are
                     handled by creating the section block.
        :param section: Target section name (no brackets).
        :param directives: Mapping of directives to upsert.

        :returns: The merged file body.
    """
    if directives is None:
        directives = {}

    target_header = "[{}]".format(section)
    raw_lines = body.splitlines()

    section_start: Optional[int] = None
    section_end: Optional[int] = None
    cursor = 0
    line_count = len(raw_lines)
    while cursor < line_count:
        stripped = raw_lines[cursor].strip()
        if stripped == target_header:
            section_start = cursor
            cursor += 1
            while cursor < line_count:
                next_stripped = raw_lines[cursor].strip()
                if next_stripped.startswith("[") and next_stripped.endswith("]"):
                    break
                cursor += 1
            section_end = cursor
            break
        cursor += 1

    if section_start is None:
        # Section absent; append.
        new_lines: list[str] = list(raw_lines)
        if len(new_lines) > 0 and len(new_lines[-1].strip()) > 0:
            new_lines.append("")
        new_lines.append(target_header)
        for key in directives.keys():
            new_lines.append("{}={}".format(key, directives[key]))
        rtnval = "\n".join(new_lines)
        if not rtnval.endswith("\n"):
            rtnval = rtnval + "\n"
        return rtnval

    # Section present: rewrite its body, upserting each directive.
    section_body_lines = raw_lines[section_start + 1: section_end]
    remaining_keys = dict(directives)
    rewritten_section: list[str] = []

    for line in section_body_lines:
        stripped = line.strip()
        if len(stripped) == 0:
            rewritten_section.append(line)
            continue
        if stripped.startswith("#") or stripped.startswith(";"):
            rewritten_section.append(line)
            continue
        if "=" not in stripped:
            rewritten_section.append(line)
            continue
        eq_idx = stripped.find("=")
        key_token = stripped[:eq_idx].strip()
        if key_token in remaining_keys:
            new_value = remaining_keys.pop(key_token)
            rewritten_section.append("{}={}".format(key_token, new_value))
        else:
            rewritten_section.append(line)

    # Append the directives that did not exist yet.
    for key in directives.keys():
        if key in remaining_keys:
            rewritten_section.append(
                "{}={}".format(key, remaining_keys[key]),
            )

    new_lines = (
        list(raw_lines[:section_start])
        + [target_header]
        + rewritten_section
        + list(raw_lines[section_end:])
    )
    rtnval = "\n".join(new_lines)
    if body.endswith("\n") and not rtnval.endswith("\n"):
        rtnval = rtnval + "\n"
    return rtnval
