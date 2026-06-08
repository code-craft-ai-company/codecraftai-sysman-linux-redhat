# ccai.sysman.linux.redhat.nfsservermanagement
#
# NfsServerManager wraps the RHEL nfs-utils tool surface (exportfs,
# nfsstat, showmount) and the on-disk content of /etc/exports,
# /etc/exports.d/, /etc/idmapd.conf, and /etc/nfs.conf through the
# ICommandRunner abstraction.
#
# Service lifecycle (nfs-server.service, nfs-idmapd.service) is owned
# by 3-service-management; this module delegates restart() to an
# injected ServiceManager and never spells systemctl directly.

import os

from dataclasses import dataclass, field
from typing import Any, Optional

from ccai.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)
from ccai.sysman.linux.redhat.runners.base import ICommandRunner
from ccai.sysman.linux.redhat.runners.models import RunResult


# ----------------------------------------------------------------------
# Module-level constants
# ----------------------------------------------------------------------

_CAT = "cat"
_TEE = "tee"
_RM = "rm"
_LS = "ls"
_EXPORTFS = "exportfs"
_SHOWMOUNT = "showmount"
_NFSSTAT = "nfsstat"

_DEFAULT_EXPORTS_MAIN = "/etc/exports"
_DEFAULT_EXPORTS_DIR = "/etc/exports.d"
_DEFAULT_EXPORTS_DROPIN = "ccai.exports"
_DEFAULT_IDMAPD_CONF = "/etc/idmapd.conf"
_DEFAULT_NFS_CONF = "/etc/nfs.conf"

_NFS_SERVER_UNIT = "nfs-server"
_NFS_IDMAPD_UNIT = "nfs-idmapd"


NfsActionStatus = ActionStatus


# ----------------------------------------------------------------------
# Config / data models
# ----------------------------------------------------------------------


@dataclass
class NfsServerManagerConfig(FeatureConfig):
    """
        Configuration for :class:`NfsServerManager`.  Inherits
        ``dry_run`` and ``elevated`` from :class:`FeatureConfig`.

        :param exports_main: Absolute path to the primary exports
                             file.  Default ``/etc/exports``.
        :param exports_dir: Absolute path to the drop-in directory.
                            Default ``/etc/exports.d``.
        :param exports_dropin: Drop-in file basename inside
                               ``exports_dir``.  Default
                               ``ccai.exports``.
        :param idmapd_conf: Absolute path to the idmapd config.
                            Default ``/etc/idmapd.conf``.
        :param nfs_conf: Absolute path to the nfs.conf config.
                         Default ``/etc/nfs.conf``.
    """
    exports_main: str = _DEFAULT_EXPORTS_MAIN
    exports_dir: str = _DEFAULT_EXPORTS_DIR
    exports_dropin: str = _DEFAULT_EXPORTS_DROPIN
    idmapd_conf: str = _DEFAULT_IDMAPD_CONF
    nfs_conf: str = _DEFAULT_NFS_CONF


@dataclass
class NfsActionResult(FeatureActionResult):
    """
        Outcome of an NFS server management action.

        :param path: The export path the action targeted, or None for
                     verbs (e.g., ``reload``, ``unexport_all``) that
                     do not target a single export.
    """
    path: Optional[str] = None


@dataclass
class ClientSpec:
    """
        A single client matcher for an export.

        :param client: Client address matcher (hostname, CIDR,
                       wildcard, or ``"*"``).
        :param options: Per-client export options.
    """
    client: str = "*"
    options: list[str] = field(default_factory=list)


@dataclass
class ExportEntry:
    """
        One parsed export entry.

        :param path: The exported filesystem path.
        :param clients: The list of :class:`ClientSpec` matchers.
        :param source_file: Path to the file the entry was read from
                            (``/etc/exports`` or a ``.d`` drop-in)
                            or ``None`` when constructed for a write.
    """
    path: str
    clients: list[ClientSpec] = field(default_factory=list)
    source_file: Optional[str] = None


@dataclass
class NfsServerStats:
    """
        Parsed statistics from ``nfsstat -s``.

        :param rpc_calls: Total RPC calls handled by the server.
        :param rpc_badcalls: Total bad RPC calls reported.
        :param nfs_v3_op_counts: Per-operation counts for NFSv3.
        :param nfs_v4_op_counts: Per-operation counts for NFSv4.
        :param raw: Raw captured stdout.
    """
    rpc_calls: int
    rpc_badcalls: int
    nfs_v3_op_counts: dict[str, int]
    nfs_v4_op_counts: dict[str, int]
    raw: str


class NfsQueryError(Exception):
    """
        Raised when an NFS read query fails for a reason other than
        the target file simply being absent.
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

        err_msg_lines = ["NFS server query command failed."]
        err_msg_lines.append("    COMMAND: {}".format(" ".join(command)))
        err_msg_lines.append("    EXIT_CODE: {}".format(exit_code))
        err_msg_lines.append("    STDERR: {}".format(stderr))
        err_msg = os.linesep.join(err_msg_lines)
        super().__init__(err_msg)
        return


# ----------------------------------------------------------------------
# Manager
# ----------------------------------------------------------------------


class NfsServerManager(BaseFeatureManager):
    """
        Facade for RHEL NFS-server management through the command
        runner.  Wraps ``exportfs`` / ``showmount`` / ``nfsstat`` and
        edits ``/etc/exports.d/<drop-in>``, ``/etc/idmapd.conf``, and
        ``/etc/nfs.conf``.  Service lifecycle is delegated to an
        injected :class:`ServiceManager`.
    """

    result_class = NfsActionResult
    status_class = NfsActionStatus
    feature_name = "nfs-server-management"

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[NfsServerManagerConfig] = None,
        *,
        service_manager: Optional[Any] = None,
        report: Optional[Any] = None,
    ) -> None:
        """
            :param runner: The command runner used by every dispatch.
            :param config: Manager configuration.  Defaults to a
                           fresh :class:`NfsServerManagerConfig`.
            :param service_manager: Optional injected ``ServiceManager``
                                    used by :meth:`restart` and
                                    :meth:`restart_idmapd`.  When
                                    omitted, ``restart`` returns
                                    ``FAILED`` with an explanatory
                                    detail rather than spelling
                                    ``systemctl`` itself.
            :param report: Optional MaintenanceReport for auto-emitted
                           action records.
        """
        if config is None:
            config = NfsServerManagerConfig()
        super().__init__(runner=runner, config=config, report=report)
        self._service_manager: Optional[Any] = service_manager
        return

    @property
    def service_manager(self) -> Optional[Any]:
        """
            The injected service manager, or ``None``.
        """
        return self._service_manager

    # ------------------------------------------------------------------
    # Read verbs — exports
    # ------------------------------------------------------------------

    def list_active(self) -> list[ExportEntry]:
        """
            List the exports the daemon is currently advertising via
            ``exportfs -v``.

            :returns: A ``list[ExportEntry]``.  Empty when the daemon
                      reports no active exports.

            :raises NfsQueryError: When ``exportfs -v`` exits non-zero
                                   with non-empty stderr.
        """
        command = [_EXPORTFS, "-v"]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0 and len(run_result.stderr.strip()) > 0:
            raise NfsQueryError(
                command=command,
                exit_code=run_result.exit_code,
                stderr=run_result.stderr,
            )
        rtnval = _parse_exportfs_v(text=run_result.stdout)
        return rtnval

    def list_configured(self) -> list[ExportEntry]:
        """
            List the exports configured on disk.  Reads
            ``exports_main`` and every ``*.exports`` file under
            ``exports_dir``.  Each returned entry carries a
            ``source_file`` annotation.

            :returns: A ``list[ExportEntry]``.  Empty when no
                      configured export is present.
        """
        rtnval: list[ExportEntry] = []

        cfg = self._typed_config()

        main_body = self._read_file(path=cfg.exports_main)
        if main_body is not None:
            main_entries = _parse_exports_file_body(content=main_body)
            for entry in main_entries:
                entry.source_file = cfg.exports_main
                rtnval.append(entry)

        dropin_files = self._list_dropin_files()
        for dropin_path in dropin_files:
            body = self._read_file(path=dropin_path)
            if body is None:
                continue
            entries = _parse_exports_file_body(content=body)
            for entry in entries:
                entry.source_file = dropin_path
                rtnval.append(entry)

        return rtnval

    def showmount(
        self,
        *,
        host: str = "localhost",
    ) -> dict[str, list[str]]:
        """
            Run ``showmount -e <host>`` and return a parsed
            export→clients map.

            :param host: The host to query.  Defaults to
                         ``"localhost"``.

            :returns: A ``dict[str, list[str]]`` mapping export path
                      to the list of client matchers.

            :raises NfsQueryError: When ``showmount`` exits non-zero
                                   with non-empty stderr.
        """
        command = [_SHOWMOUNT, "-e", host]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0 and len(run_result.stderr.strip()) > 0:
            raise NfsQueryError(
                command=command,
                exit_code=run_result.exit_code,
                stderr=run_result.stderr,
            )
        rtnval = _parse_showmount_e(text=run_result.stdout)
        return rtnval

    # ------------------------------------------------------------------
    # Write verbs — exports
    # ------------------------------------------------------------------

    def add_export(self, entry: ExportEntry) -> NfsActionResult:
        """
            Add an export to the drop-in file.

            Reads the drop-in, renders the canonical line for
            ``entry``, and appends it when the line is not already
            present.  After a successful write the manager dispatches
            ``exportfs -r``.  Returns ``SKIPPED`` when the rendered
            line is already present verbatim.

            :param entry: The :class:`ExportEntry` to add.

            :returns: The :class:`NfsActionResult`.
        """
        cfg = self._typed_config()
        dropin_path = self._dropin_path()
        write_command = [_TEE, dropin_path]

        existing = self._read_file(path=dropin_path)
        if existing is None:
            existing_content = ""
        else:
            existing_content = existing

        rendered_line = _render_export_line(entry=entry)
        if _line_present(content=existing_content, line=rendered_line):
            skipped = self._build_path_result(
                status=NfsActionStatus.SKIPPED,
                path=entry.path,
                command=write_command,
                detail="export already at desired state",
            )
            return skipped

        new_content = _append_line(content=existing_content, line=rendered_line)

        if self._config.dry_run:
            dry = self._build_path_result(
                status=NfsActionStatus.DRY_RUN,
                path=entry.path,
                command=write_command,
                detail="add_export:{}".format(entry.path),
            )
            return dry

        write_result = self._runner.run(
            list(write_command),
            elevated=self._config.elevated,
            stdin=new_content,
        )
        if write_result.exit_code != 0:
            failed = self._wrap_runner_result(
                run_result=write_result,
                command=write_command,
                path=entry.path,
                detail="add_export:{}".format(entry.path),
            )
            return failed

        rtnval = self._invoke_exportfs_reload(
            base_action="add_export:{}".format(entry.path),
            path=entry.path,
            primary_run=write_result,
            primary_command=write_command,
        )
        return rtnval

    def update_export(self, entry: ExportEntry) -> NfsActionResult:
        """
            Update an export inside the drop-in.  Replaces every line
            whose first token equals ``entry.path`` with a single
            rendered line for ``entry``.  Returns ``SKIPPED`` when the
            rendered line already matches the existing content
            verbatim.

            :param entry: The :class:`ExportEntry` to upsert.

            :returns: The :class:`NfsActionResult`.
        """
        dropin_path = self._dropin_path()
        write_command = [_TEE, dropin_path]

        existing = self._read_file(path=dropin_path)
        if existing is None:
            existing_content = ""
        else:
            existing_content = existing

        rendered_line = _render_export_line(entry=entry)
        new_content = _replace_or_append(
            content=existing_content,
            path=entry.path,
            new_line=rendered_line,
        )
        if new_content == existing_content:
            skipped = self._build_path_result(
                status=NfsActionStatus.SKIPPED,
                path=entry.path,
                command=write_command,
                detail="export already at desired state",
            )
            return skipped

        if self._config.dry_run:
            dry = self._build_path_result(
                status=NfsActionStatus.DRY_RUN,
                path=entry.path,
                command=write_command,
                detail="update_export:{}".format(entry.path),
            )
            return dry

        write_result = self._runner.run(
            list(write_command),
            elevated=self._config.elevated,
            stdin=new_content,
        )
        if write_result.exit_code != 0:
            failed = self._wrap_runner_result(
                run_result=write_result,
                command=write_command,
                path=entry.path,
                detail="update_export:{}".format(entry.path),
            )
            return failed

        rtnval = self._invoke_exportfs_reload(
            base_action="update_export:{}".format(entry.path),
            path=entry.path,
            primary_run=write_result,
            primary_command=write_command,
        )
        return rtnval

    def remove_export(self, path: str) -> NfsActionResult:
        """
            Remove every line whose first token equals ``path`` from
            the drop-in.  When the resulting content has no remaining
            export lines the drop-in file itself is removed via
            ``rm -f``.  Returns ``SKIPPED`` when no line matches.

            :param path: The export path to remove.

            :returns: The :class:`NfsActionResult`.
        """
        dropin_path = self._dropin_path()
        write_command = [_TEE, dropin_path]

        existing = self._read_file(path=dropin_path)
        if existing is None:
            skipped = self._build_path_result(
                status=NfsActionStatus.SKIPPED,
                path=path,
                command=write_command,
                detail="drop-in file does not exist",
            )
            return skipped

        new_content = _strip_lines_for_path(content=existing, path=path)
        if new_content == existing:
            skipped = self._build_path_result(
                status=NfsActionStatus.SKIPPED,
                path=path,
                command=write_command,
                detail="export not present",
            )
            return skipped

        empty_after = _has_no_export_lines(content=new_content)

        if empty_after:
            rm_command = [_RM, "-f", dropin_path]
            if self._config.dry_run:
                dry = self._build_path_result(
                    status=NfsActionStatus.DRY_RUN,
                    path=path,
                    command=rm_command,
                    detail="remove_export:{}".format(path),
                )
                return dry

            rm_result = self._runner.run(
                list(rm_command),
                elevated=self._config.elevated,
            )
            if rm_result.exit_code != 0:
                failed = self._wrap_runner_result(
                    run_result=rm_result,
                    command=rm_command,
                    path=path,
                    detail="remove_export:{}".format(path),
                )
                return failed

            rtnval = self._invoke_exportfs_reload(
                base_action="remove_export:{}".format(path),
                path=path,
                primary_run=rm_result,
                primary_command=rm_command,
            )
            return rtnval

        if self._config.dry_run:
            dry = self._build_path_result(
                status=NfsActionStatus.DRY_RUN,
                path=path,
                command=write_command,
                detail="remove_export:{}".format(path),
            )
            return dry

        write_result = self._runner.run(
            list(write_command),
            elevated=self._config.elevated,
            stdin=new_content,
        )
        if write_result.exit_code != 0:
            failed = self._wrap_runner_result(
                run_result=write_result,
                command=write_command,
                path=path,
                detail="remove_export:{}".format(path),
            )
            return failed

        rtnval = self._invoke_exportfs_reload(
            base_action="remove_export:{}".format(path),
            path=path,
            primary_run=write_result,
            primary_command=write_command,
        )
        return rtnval

    def reload(self) -> NfsActionResult:
        """
            Re-export every entry via ``exportfs -r``.  Operational
            verb — never skips.  Honours dry-run.

            :returns: The :class:`NfsActionResult`.
        """
        command = [_EXPORTFS, "-r"]
        rtnval = self._dispatch(
            command=command,
            detail="reload",
            operation="reload",
            path=None,
        )
        return rtnval

    def unexport_all(self) -> NfsActionResult:
        """
            Unexport every entry via ``exportfs -ua``.  Operational
            verb — never skips.  Honours dry-run.

            :returns: The :class:`NfsActionResult`.
        """
        command = [_EXPORTFS, "-ua"]
        rtnval = self._dispatch(
            command=command,
            detail="unexport_all",
            operation="unexport_all",
            path=None,
        )
        return rtnval

    def restart(self) -> NfsActionResult:
        """
            Restart ``nfs-server.service`` by delegating to the
            injected :class:`ServiceManager`.  Returns a ``FAILED``
            result when no service manager has been injected — this
            module never spells ``systemctl`` itself.

            :returns: The :class:`NfsActionResult`.
        """
        if self._service_manager is None:
            failed = NfsActionResult(
                status=NfsActionStatus.FAILED,
                detail="restart requires an injected ServiceManager",
                exit_code=None,
                stdout=None,
                stderr="no service_manager injected",
                command=None,
                path=None,
            )
            return failed

        sm_result = self._service_manager.restart(_NFS_SERVER_UNIT)
        rtnval = _coerce_service_result(
            sm_result=sm_result,
            detail="restart:{}".format(_NFS_SERVER_UNIT),
            path=None,
        )
        return rtnval

    def restart_idmapd(self) -> NfsActionResult:
        """
            Restart ``nfs-idmapd.service`` by delegating to the
            injected :class:`ServiceManager`.  Returns a ``FAILED``
            result when no service manager has been injected.

            :returns: The :class:`NfsActionResult`.
        """
        if self._service_manager is None:
            failed = NfsActionResult(
                status=NfsActionStatus.FAILED,
                detail="restart_idmapd requires an injected ServiceManager",
                exit_code=None,
                stdout=None,
                stderr="no service_manager injected",
                command=None,
                path=None,
            )
            return failed

        sm_result = self._service_manager.restart(_NFS_IDMAPD_UNIT)
        rtnval = _coerce_service_result(
            sm_result=sm_result,
            detail="restart:{}".format(_NFS_IDMAPD_UNIT),
            path=None,
        )
        return rtnval

    # ------------------------------------------------------------------
    # idmapd.conf — NFSv4 idmap domain
    # ------------------------------------------------------------------

    def get_idmap_domain(self) -> Optional[str]:
        """
            Return the current ``Domain`` directive in the
            ``[General]`` section of ``/etc/idmapd.conf``, or ``None``
            when the file is absent or the directive is unset.

            :returns: The current domain string, or ``None``.
        """
        cfg = self._typed_config()
        body = self._read_file(path=cfg.idmapd_conf)
        if body is None:
            rtnval: Optional[str] = None
            return rtnval

        rtnval = _ini_get_value(
            content=body, section="General", key="Domain",
        )
        return rtnval

    def set_idmap_domain(self, value: str) -> NfsActionResult:
        """
            Upsert ``Domain = <value>`` in ``[General]`` of
            ``/etc/idmapd.conf``.  Returns ``SKIPPED`` when the
            directive already matches.  Sets ``detail`` on the
            returned result to remind the caller that
            ``nfs-idmapd.service`` must be restarted via the
            ``ServiceManager`` for the change to take effect.

            :param value: The new Domain value.

            :returns: The :class:`NfsActionResult`.
        """
        cfg = self._typed_config()
        target_path = cfg.idmapd_conf
        write_command = [_TEE, target_path]

        body = self._read_file(path=target_path)
        if body is None:
            existing_content = ""
        else:
            existing_content = body

        current_value = _ini_get_value(
            content=existing_content, section="General", key="Domain",
        )
        if current_value == value:
            skipped = self._build_path_result(
                status=NfsActionStatus.SKIPPED,
                path=None,
                command=write_command,
                detail="idmap domain already at desired value",
            )
            return skipped

        new_content = _ini_upsert(
            content=existing_content,
            section="General",
            key="Domain",
            value=value,
        )

        if self._config.dry_run:
            dry = self._build_path_result(
                status=NfsActionStatus.DRY_RUN,
                path=None,
                command=write_command,
                detail="set_idmap_domain:{}".format(value),
            )
            return dry

        write_result = self._runner.run(
            list(write_command),
            elevated=self._config.elevated,
            stdin=new_content,
        )
        rtnval = self._wrap_runner_result(
            run_result=write_result,
            command=write_command,
            path=None,
            detail=(
                "nfs-idmapd.service restart required for change"
                " to take effect"
            ),
        )
        return rtnval

    # ------------------------------------------------------------------
    # /etc/nfs.conf directives
    # ------------------------------------------------------------------

    def get_directives(self) -> dict[str, dict[str, str]]:
        """
            Read ``/etc/nfs.conf`` and return its directives as
            ``dict[section, dict[key, value]]``.

            :returns: The parsed directives.  Empty dict when the
                      file is missing.
        """
        cfg = self._typed_config()
        body = self._read_file(path=cfg.nfs_conf)
        if body is None:
            rtnval: dict[str, dict[str, str]] = {}
            return rtnval

        rtnval = _ini_parse_all(content=body)
        return rtnval

    def set_directive(
        self,
        *,
        section: str,
        key: str,
        value: str,
    ) -> NfsActionResult:
        """
            Upsert ``<key> = <value>`` in ``[<section>]`` of
            ``/etc/nfs.conf``, preserving every other line.  Returns
            ``SKIPPED`` when the directive already matches.

            :param section: The section name (without brackets).
            :param key: The key to upsert.
            :param value: The new value.

            :returns: The :class:`NfsActionResult`.
        """
        cfg = self._typed_config()
        target_path = cfg.nfs_conf
        write_command = [_TEE, target_path]

        body = self._read_file(path=target_path)
        if body is None:
            existing_content = ""
        else:
            existing_content = body

        current_value = _ini_get_value(
            content=existing_content, section=section, key=key,
        )
        if current_value == value:
            skipped = self._build_path_result(
                status=NfsActionStatus.SKIPPED,
                path=None,
                command=write_command,
                detail="directive already at desired value",
            )
            return skipped

        new_content = _ini_upsert(
            content=existing_content,
            section=section,
            key=key,
            value=value,
        )

        if self._config.dry_run:
            dry = self._build_path_result(
                status=NfsActionStatus.DRY_RUN,
                path=None,
                command=write_command,
                detail="set_directive:{}.{}".format(section, key),
            )
            return dry

        write_result = self._runner.run(
            list(write_command),
            elevated=self._config.elevated,
            stdin=new_content,
        )
        rtnval = self._wrap_runner_result(
            run_result=write_result,
            command=write_command,
            path=None,
            detail="set_directive:{}.{}".format(section, key),
        )
        return rtnval

    def remove_directive(
        self,
        *,
        section: str,
        key: str,
    ) -> NfsActionResult:
        """
            Strip the ``<key> = <value>`` line from ``[<section>]`` of
            ``/etc/nfs.conf``.  Returns ``SKIPPED`` when no such line
            is present.

            :param section: The section name (without brackets).
            :param key: The key to strip.

            :returns: The :class:`NfsActionResult`.
        """
        cfg = self._typed_config()
        target_path = cfg.nfs_conf
        write_command = [_TEE, target_path]

        body = self._read_file(path=target_path)
        if body is None:
            skipped = self._build_path_result(
                status=NfsActionStatus.SKIPPED,
                path=None,
                command=write_command,
                detail="nfs.conf does not exist",
            )
            return skipped

        new_content = _ini_strip(
            content=body, section=section, key=key,
        )
        if new_content == body:
            skipped = self._build_path_result(
                status=NfsActionStatus.SKIPPED,
                path=None,
                command=write_command,
                detail="directive not present",
            )
            return skipped

        if self._config.dry_run:
            dry = self._build_path_result(
                status=NfsActionStatus.DRY_RUN,
                path=None,
                command=write_command,
                detail="remove_directive:{}.{}".format(section, key),
            )
            return dry

        write_result = self._runner.run(
            list(write_command),
            elevated=self._config.elevated,
            stdin=new_content,
        )
        rtnval = self._wrap_runner_result(
            run_result=write_result,
            command=write_command,
            path=None,
            detail="remove_directive:{}.{}".format(section, key),
        )
        return rtnval

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def server_stats(self) -> NfsServerStats:
        """
            Capture server stats via ``nfsstat -s`` and return a
            parsed :class:`NfsServerStats`.

            :returns: The parsed :class:`NfsServerStats`.

            :raises NfsQueryError: When ``nfsstat -s`` exits non-zero
                                   with non-empty stderr.
        """
        command = [_NFSSTAT, "-s"]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0 and len(run_result.stderr.strip()) > 0:
            raise NfsQueryError(
                command=command,
                exit_code=run_result.exit_code,
                stderr=run_result.stderr,
            )
        rtnval = _parse_nfsstat_s(text=run_result.stdout)
        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _typed_config(self) -> NfsServerManagerConfig:
        """
            Narrow the base config to the typed subclass.

            :returns: The typed :class:`NfsServerManagerConfig`.
        """
        rtnval = self._config
        return rtnval

    def _dropin_path(self) -> str:
        """
            Build the absolute drop-in file path from the config.

            :returns: The drop-in file path.
        """
        cfg = self._typed_config()
        rtnval = "{}/{}".format(
            cfg.exports_dir.rstrip("/"), cfg.exports_dropin,
        )
        return rtnval

    def _read_file(self, *, path: str) -> Optional[str]:
        """
            Read a file via ``cat`` without elevation.

            :param path: The absolute path to read.

            :returns: The file contents on success; ``None`` when
                      ``cat`` reports a non-zero exit code (i.e.
                      the path is missing).
        """
        command = [_CAT, path]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: Optional[str] = None
            return rtnval
        rtnval = run_result.stdout
        return rtnval

    def _list_dropin_files(self) -> list[str]:
        """
            Enumerate ``*.exports`` files under the drop-in directory.

            :returns: A sorted list of absolute paths.  Empty when the
                      directory is missing or contains no matching
                      files.
        """
        cfg = self._typed_config()
        command = [_LS, "-1", cfg.exports_dir]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: list[str] = []
            return rtnval

        rtnval = []
        for raw in run_result.stdout.splitlines():
            name = raw.strip()
            if len(name) == 0:
                continue
            if not name.endswith(".exports"):
                continue
            absolute = "{}/{}".format(cfg.exports_dir.rstrip("/"), name)
            rtnval.append(absolute)
        rtnval.sort()
        return rtnval

    def _invoke_exportfs_reload(
        self,
        *,
        base_action: str,
        path: Optional[str],
        primary_run: RunResult,
        primary_command: list[str],
    ) -> NfsActionResult:
        """
            Dispatch ``exportfs -r`` after a successful primary
            mutation.  When the reload fails, the returned result is
            mapped to ``FAILED`` even though the primary mutation
            succeeded.

            :param base_action: Detail string identifying the primary
                                verb.
            :param path: The export path the primary verb targeted.
            :param primary_run: The :class:`RunResult` of the
                                preceding mutation.
            :param primary_command: The argv that ran first.

            :returns: A :class:`NfsActionResult` whose status reflects
                      the ``exportfs -r`` outcome.
        """
        reload_command = [_EXPORTFS, "-r"]
        reload_run = self._runner.run(
            list(reload_command),
            elevated=self._config.elevated,
        )
        if reload_run.exit_code != 0:
            failed = NfsActionResult(
                status=NfsActionStatus.FAILED,
                detail=base_action,
                exit_code=reload_run.exit_code,
                stdout=reload_run.stdout,
                stderr=reload_run.stderr,
                command=list(reload_command),
                path=path,
            )
            return failed

        rtnval = NfsActionResult(
            status=NfsActionStatus.SUCCESS,
            detail=base_action,
            exit_code=primary_run.exit_code,
            stdout=primary_run.stdout,
            stderr=primary_run.stderr,
            command=list(primary_command),
            path=path,
        )
        return rtnval

    def _build_path_result(
        self,
        *,
        status: NfsActionStatus,
        path: Optional[str],
        command: list[str],
        detail: Optional[str] = None,
    ) -> NfsActionResult:
        """
            Build a result without invoking the runner.

            :param status: Outcome status.
            :param path: Export path targeted, or ``None``.
            :param command: Would-have-been argv.
            :param detail: Optional human-readable detail.

            :returns: The :class:`NfsActionResult`.
        """
        rtnval = NfsActionResult(
            status=status,
            detail=detail,
            command=list(command),
            path=path,
        )
        return rtnval

    def _wrap_runner_result(
        self,
        *,
        run_result: RunResult,
        command: list[str],
        path: Optional[str],
        detail: Optional[str] = None,
    ) -> NfsActionResult:
        """
            Map a :class:`RunResult` to a :class:`NfsActionResult`.

            :param run_result: The runner's result.
            :param command: The argv that ran.
            :param path: The export path targeted, or ``None``.
            :param detail: Optional human-readable detail.

            :returns: The :class:`NfsActionResult`.
        """
        if run_result.exit_code == 0:
            mapped_status = NfsActionStatus.SUCCESS
        else:
            mapped_status = NfsActionStatus.FAILED

        rtnval = NfsActionResult(
            status=mapped_status,
            detail=detail,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(command),
            path=path,
        )
        return rtnval


# ----------------------------------------------------------------------
# Module-level helpers — exports parser / renderer
# ----------------------------------------------------------------------


def _parse_exports_file_body(*, content: str) -> list[ExportEntry]:
    """
        Parse an exports file body into a list of :class:`ExportEntry`.

        Comment and blank lines are skipped.  Each remaining line is
        split on whitespace; the first token is the path, every
        remaining token is parsed as ``<client>(<opts>)``.

        :param content: The raw file content.

        :returns: A ``list[ExportEntry]`` in declaration order.
                  ``source_file`` is left at ``None`` — the caller
                  populates it.
    """
    rtnval: list[ExportEntry] = []
    for raw in content.splitlines():
        stripped = raw.strip()
        if len(stripped) == 0:
            continue
        if stripped.startswith("#"):
            continue
        tokens = stripped.split()
        if len(tokens) < 1:
            continue
        path = tokens[0]
        client_specs: list[ClientSpec] = []
        for client_token in tokens[1:]:
            parsed_client = _parse_client_token(token=client_token)
            client_specs.append(parsed_client)
        entry = ExportEntry(
            path=path,
            clients=client_specs,
            source_file=None,
        )
        rtnval.append(entry)
    return rtnval


def _parse_client_token(*, token: str) -> ClientSpec:
    """
        Parse a single ``<host>(<opts>)`` client token.

        :param token: The client token (e.g. ``"*(rw,sync)"``).

        :returns: A populated :class:`ClientSpec`.  When the token
                  carries no parenthesised options, the spec's
                  ``options`` list is empty.
    """
    if "(" not in token:
        rtnval = ClientSpec(client=token, options=[])
        return rtnval

    open_index = token.index("(")
    close_index = token.rfind(")")
    if close_index <= open_index:
        rtnval = ClientSpec(client=token[:open_index], options=[])
        return rtnval

    host = token[:open_index]
    opts_text = token[open_index + 1:close_index]
    if len(opts_text) == 0:
        options: list[str] = []
    else:
        options = []
        for piece in opts_text.split(","):
            normalised = piece.strip()
            if len(normalised) == 0:
                continue
            options.append(normalised)
    rtnval = ClientSpec(client=host, options=options)
    return rtnval


def _render_client_token(*, client: ClientSpec) -> str:
    """
        Render a :class:`ClientSpec` as a ``<host>(<opt1>,<opt2>,…)``
        token.

        :param client: The client spec.

        :returns: The rendered token.
    """
    if client.options is not None and len(client.options) > 0:
        opts_text = ",".join(client.options)
        rtnval = "{}({})".format(client.client, opts_text)
        return rtnval
    rtnval = "{}()".format(client.client)
    return rtnval


def _render_export_line(*, entry: ExportEntry) -> str:
    """
        Render an :class:`ExportEntry` as a single canonical exports
        line.

        :param entry: The export entry.  Must carry at least one
                      client.

        :returns: The rendered line (no trailing newline).

        :raises ValueError: When ``entry.clients`` is empty.
    """
    if entry.clients is None or len(entry.clients) == 0:
        err_msg_lines = ["Cannot render export line with no clients."]
        err_msg_lines.append("    PATH: {}".format(entry.path))
        err_msg = os.linesep.join(err_msg_lines)
        raise ValueError(err_msg)

    client_tokens: list[str] = []
    for client in entry.clients:
        client_tokens.append(_render_client_token(client=client))
    rtnval = "{} {}".format(entry.path, " ".join(client_tokens))
    return rtnval


def _line_present(*, content: str, line: str) -> bool:
    """
        Return True when ``line`` (modulo surrounding whitespace) is
        already present in ``content``.

        :param content: The file content.
        :param line: The exact rendered line to look for.

        :returns: True when present.
    """
    target = line.strip()
    for raw in content.splitlines():
        if raw.strip() == target:
            rtnval = True
            return rtnval
    rtnval = False
    return rtnval


def _append_line(*, content: str, line: str) -> str:
    """
        Append ``line`` to ``content`` ensuring the result ends with a
        single trailing newline.

        :param content: Existing content.
        :param line: Line to append (no trailing newline).

        :returns: The updated content.
    """
    if len(content) == 0:
        rtnval = line + "\n"
        return rtnval
    if not content.endswith("\n"):
        body = content + "\n"
    else:
        body = content
    rtnval = body + line + "\n"
    return rtnval


def _replace_or_append(
    *,
    content: str,
    path: str,
    new_line: str,
) -> str:
    """
        Return content with the first matching path-line replaced by
        ``new_line`` and any subsequent duplicates dropped.  When no
        line matches, ``new_line`` is appended.

        :param content: Existing content.
        :param path: Export path token to match.
        :param new_line: The new rendered line.

        :returns: The updated content.
    """
    new_lines: list[str] = []
    replaced = False
    for raw in content.splitlines():
        stripped = raw.strip()
        if len(stripped) == 0:
            new_lines.append(raw)
            continue
        if stripped.startswith("#"):
            new_lines.append(raw)
            continue
        tokens = stripped.split()
        if len(tokens) >= 1 and tokens[0] == path and not replaced:
            new_lines.append(new_line)
            replaced = True
            continue
        if len(tokens) >= 1 and tokens[0] == path and replaced:
            continue
        new_lines.append(raw)

    if not replaced:
        new_lines.append(new_line)

    body = "\n".join(new_lines)
    if not body.endswith("\n"):
        body = body + "\n"
    return body


def _strip_lines_for_path(*, content: str, path: str) -> str:
    """
        Return content with every line whose first token equals
        ``path`` stripped.

        :param content: Existing content.
        :param path: Export path token to match.

        :returns: The updated content.
    """
    new_lines: list[str] = []
    for raw in content.splitlines():
        stripped = raw.strip()
        if len(stripped) == 0:
            new_lines.append(raw)
            continue
        if stripped.startswith("#"):
            new_lines.append(raw)
            continue
        tokens = stripped.split()
        if len(tokens) >= 1 and tokens[0] == path:
            continue
        new_lines.append(raw)

    body = "\n".join(new_lines)
    if len(body) > 0 and not body.endswith("\n"):
        body = body + "\n"
    return body


def _has_no_export_lines(*, content: str) -> bool:
    """
        Return True when the content carries no non-comment,
        non-blank lines.

        :param content: The content to inspect.

        :returns: True when no export lines are present.
    """
    for raw in content.splitlines():
        stripped = raw.strip()
        if len(stripped) == 0:
            continue
        if stripped.startswith("#"):
            continue
        rtnval = False
        return rtnval
    rtnval = True
    return rtnval


# ----------------------------------------------------------------------
# Module-level helpers — exportfs -v parser
# ----------------------------------------------------------------------


def _parse_exportfs_v(*, text: str) -> list[ExportEntry]:
    """
        Parse ``exportfs -v`` output into a list of
        :class:`ExportEntry`.  ``exportfs -v`` emits one client line
        per export — the kernel sometimes wraps long lines so the
        path appears alone with the client token on the next indented
        line.  This parser handles both shapes and folds entries on
        the same path into a single :class:`ExportEntry` with multiple
        clients.

        :param text: The captured stdout.

        :returns: A ``list[ExportEntry]``.
    """
    by_path: dict[str, ExportEntry] = {}
    order: list[str] = []
    pending_path: Optional[str] = None

    for raw in text.splitlines():
        if len(raw) == 0:
            continue

        if raw[0] in (" ", "\t"):
            if pending_path is None:
                continue
            client_token = raw.strip()
            parsed_client = _parse_client_token(token=client_token)
            existing_entry = by_path[pending_path]
            existing_entry.clients.append(parsed_client)
            continue

        stripped = raw.strip()
        tokens = stripped.split(None, 1)
        if len(tokens) == 1:
            pending_path = tokens[0]
            if pending_path not in by_path:
                by_path[pending_path] = ExportEntry(
                    path=pending_path,
                    clients=[],
                    source_file=None,
                )
                order.append(pending_path)
            continue

        path = tokens[0]
        client_token = tokens[1].strip()
        if path not in by_path:
            by_path[path] = ExportEntry(
                path=path,
                clients=[],
                source_file=None,
            )
            order.append(path)
        pending_path = path
        parsed_client = _parse_client_token(token=client_token)
        by_path[path].clients.append(parsed_client)

    rtnval: list[ExportEntry] = []
    for path in order:
        rtnval.append(by_path[path])
    return rtnval


# ----------------------------------------------------------------------
# Module-level helpers — showmount -e parser
# ----------------------------------------------------------------------


def _parse_showmount_e(*, text: str) -> dict[str, list[str]]:
    """
        Parse ``showmount -e`` output into a path→clients map.  The
        command emits a header line (e.g. ``Export list for host:``)
        followed by one ``<path> <client>[,<client>…]`` line per
        export.

        :param text: The captured stdout.

        :returns: A ``dict[str, list[str]]``.
    """
    rtnval: dict[str, list[str]] = {}
    first_seen = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if len(line.strip()) == 0:
            continue
        if not first_seen:
            first_seen = True
            if line.lower().startswith("export list"):
                continue
        tokens = line.split(None, 1)
        if len(tokens) < 1:
            continue
        path = tokens[0]
        clients_text = tokens[1] if len(tokens) > 1 else ""
        clients: list[str] = []
        for piece in clients_text.split(","):
            normalised = piece.strip()
            if len(normalised) == 0:
                continue
            clients.append(normalised)
        rtnval[path] = clients
    return rtnval


# ----------------------------------------------------------------------
# Module-level helpers — INI for idmapd.conf and nfs.conf
# ----------------------------------------------------------------------


def _ini_parse_all(*, content: str) -> dict[str, dict[str, str]]:
    """
        Parse a multi-section INI file body into a section→key→value
        mapping.  Comment lines (``#`` or ``;``) and blank lines are
        ignored.  Keys outside of any section are stored under the
        empty-string section name.

        :param content: The file body.

        :returns: The parsed mapping.
    """
    rtnval: dict[str, dict[str, str]] = {}
    current_section = ""
    rtnval[current_section] = {}

    for raw in content.splitlines():
        stripped = raw.strip()
        if len(stripped) == 0:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith(";"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped[1:-1].strip()
            if current_section not in rtnval:
                rtnval[current_section] = {}
            continue
        if "=" not in stripped:
            continue
        eq_index = stripped.index("=")
        key = stripped[:eq_index].strip()
        value = stripped[eq_index + 1:].strip()
        if current_section not in rtnval:
            rtnval[current_section] = {}
        rtnval[current_section][key] = value

    if len(rtnval[""]) == 0:
        del rtnval[""]
    return rtnval


def _ini_get_value(
    *,
    content: str,
    section: str,
    key: str,
) -> Optional[str]:
    """
        Return the current value for ``<section>.<key>`` in
        ``content``, or ``None`` when absent.

        :param content: The file body.
        :param section: The section name.
        :param key: The key name.

        :returns: The current value, or ``None``.
    """
    parsed = _ini_parse_all(content=content)
    if section not in parsed:
        rtnval: Optional[str] = None
        return rtnval
    if key not in parsed[section]:
        rtnval = None
        return rtnval
    rtnval = parsed[section][key]
    return rtnval


def _ini_upsert(
    *,
    content: str,
    section: str,
    key: str,
    value: str,
) -> str:
    """
        Upsert ``<key> = <value>`` inside ``[<section>]`` of
        ``content``.  Existing other keys, comments, and blank lines
        are preserved verbatim.  When the section is absent, it is
        appended to the end of the file together with the directive.

        :param content: Existing content.
        :param section: Section name (without brackets).
        :param key: Key name.
        :param value: New value.

        :returns: The updated content.
    """
    target_line = "{} = {}".format(key, value)

    new_lines: list[str] = []
    in_target_section = False
    seen_target_section = False
    replaced = False
    section_end_index: Optional[int] = None

    for raw in content.splitlines():
        stripped = raw.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section_name = stripped[1:-1].strip()
            if in_target_section and not replaced:
                # We are leaving the target section without finding
                # the key — record where to insert.
                section_end_index = len(new_lines)
            in_target_section = (section_name == section)
            if in_target_section:
                seen_target_section = True
            new_lines.append(raw)
            continue

        if in_target_section and not replaced:
            if len(stripped) > 0 and not stripped.startswith("#") and not stripped.startswith(";"):
                if "=" in stripped:
                    eq_index = stripped.index("=")
                    existing_key = stripped[:eq_index].strip()
                    if existing_key == key:
                        new_lines.append(target_line)
                        replaced = True
                        continue
        new_lines.append(raw)

    if in_target_section and not replaced:
        section_end_index = len(new_lines)

    if not seen_target_section:
        if len(new_lines) > 0 and len(new_lines[-1].strip()) > 0:
            new_lines.append("")
        new_lines.append("[{}]".format(section))
        new_lines.append(target_line)
    elif not replaced:
        if section_end_index is None:
            section_end_index = len(new_lines)
        new_lines.insert(section_end_index, target_line)

    body = "\n".join(new_lines)
    if not body.endswith("\n"):
        body = body + "\n"
    return body


def _ini_strip(
    *,
    content: str,
    section: str,
    key: str,
) -> str:
    """
        Strip ``<key> = …`` from ``[<section>]`` of ``content``.
        Other lines (including the section header itself) are
        preserved.  Returns ``content`` unchanged when no matching
        line is present.

        :param content: Existing content.
        :param section: Section name.
        :param key: Key to remove.

        :returns: The updated content.
    """
    new_lines: list[str] = []
    in_target_section = False
    stripped_any = False

    for raw in content.splitlines():
        stripped = raw.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section_name = stripped[1:-1].strip()
            in_target_section = (section_name == section)
            new_lines.append(raw)
            continue

        if in_target_section and "=" in stripped:
            if not stripped.startswith("#") and not stripped.startswith(";"):
                eq_index = stripped.index("=")
                existing_key = stripped[:eq_index].strip()
                if existing_key == key:
                    stripped_any = True
                    continue
        new_lines.append(raw)

    if not stripped_any:
        rtnval = content
        return rtnval

    body = "\n".join(new_lines)
    if not body.endswith("\n"):
        body = body + "\n"
    rtnval = body
    return rtnval


# ----------------------------------------------------------------------
# Module-level helpers — nfsstat -s parser
# ----------------------------------------------------------------------


def _parse_nfsstat_s(*, text: str) -> NfsServerStats:
    """
        Parse ``nfsstat -s`` output into an :class:`NfsServerStats`.

        ``nfsstat -s`` emits a header line for each section
        (``Server rpc stats:``, ``Server nfs v3:``, ``Server nfs v4:``)
        followed by alternating label-and-value rows.  Labels live on
        an unindented row; values follow on the next row in matching
        column positions.

        :param text: The captured stdout.

        :returns: The parsed :class:`NfsServerStats`.
    """
    rpc_calls = 0
    rpc_badcalls = 0
    v3_counts: dict[str, int] = {}
    v4_counts: dict[str, int] = {}

    section: Optional[str] = None
    pending_labels: Optional[list[str]] = None

    for raw in text.splitlines():
        stripped = raw.rstrip()
        bare = stripped.strip()

        lower = bare.lower()
        if lower.startswith("server rpc stats"):
            section = "rpc"
            pending_labels = None
            continue
        if lower.startswith("server nfs v3"):
            section = "v3"
            pending_labels = None
            continue
        if lower.startswith("server nfs v4") and "operations" not in lower:
            section = "v4"
            pending_labels = None
            continue
        if lower.startswith("server nfs v4 operations"):
            section = "v4"
            pending_labels = None
            continue

        if len(bare) == 0:
            continue

        tokens = bare.split()
        if len(tokens) == 0:
            continue

        if section is None:
            continue

        first_is_number = _is_int_token(token=tokens[0])
        if first_is_number:
            if pending_labels is None:
                continue
            count = min(len(tokens), len(pending_labels))
            target = v3_counts if section == "v3" else (v4_counts if section == "v4" else None)
            for index in range(count):
                label = pending_labels[index]
                value_int = int(tokens[index])
                if section == "rpc":
                    if label == "calls":
                        rpc_calls = value_int
                    elif label == "badcalls":
                        rpc_badcalls = value_int
                else:
                    if target is not None:
                        target[label] = value_int
            pending_labels = None
            continue

        pending_labels = list(tokens)

    rtnval = NfsServerStats(
        rpc_calls=rpc_calls,
        rpc_badcalls=rpc_badcalls,
        nfs_v3_op_counts=v3_counts,
        nfs_v4_op_counts=v4_counts,
        raw=text,
    )
    return rtnval


def _is_int_token(*, token: str) -> bool:
    """
        Return True when ``token`` (with an optional leading sign) is
        a base-10 integer literal.

        :param token: The token to test.

        :returns: True when the token is integer-shaped.
    """
    if len(token) == 0:
        rtnval = False
        return rtnval
    candidate = token
    if candidate[0] in ("+", "-"):
        candidate = candidate[1:]
    if len(candidate) == 0:
        rtnval = False
        return rtnval
    rtnval = candidate.isdigit()
    return rtnval


# ----------------------------------------------------------------------
# Module-level helpers — service-result coercion
# ----------------------------------------------------------------------


def _coerce_service_result(
    *,
    sm_result: Any,
    detail: str,
    path: Optional[str],
) -> NfsActionResult:
    """
        Map a :class:`ServiceActionResult` returned by an injected
        :class:`ServiceManager` into the local
        :class:`NfsActionResult` shape.  The two share a base class
        so the mapping is by-attribute rather than by ``isinstance``.

        :param sm_result: The service-manager result.
        :param detail: Detail string for the wrapped result.
        :param path: The export path targeted, or ``None``.

        :returns: The :class:`NfsActionResult`.
    """
    incoming_status = getattr(sm_result, "status", None)
    if incoming_status is None:
        mapped_status = NfsActionStatus.FAILED
    else:
        status_value = getattr(incoming_status, "value", None)
        if status_value is None:
            mapped_status = NfsActionStatus.FAILED
        else:
            mapped_status = NfsActionStatus(status_value)

    rtnval = NfsActionResult(
        status=mapped_status,
        detail=detail,
        exit_code=getattr(sm_result, "exit_code", None),
        stdout=getattr(sm_result, "stdout", None),
        stderr=getattr(sm_result, "stderr", None),
        command=getattr(sm_result, "command", None),
        path=path,
    )
    return rtnval
