# ccai.sysman.linux.redhat.mailtransportmanagement
#
# MailTransportManager wraps Postfix administration commands
# (postconf, postmap, newaliases, postqueue, mailq, postsuper) and
# direct edits of /etc/aliases and /etc/postfix/* lookup tables
# through the ICommandRunner abstraction.
#
# Service-lifecycle (postfix.service) is delegated to an injected
# ServiceManager.  This module never spells systemctl directly.

from dataclasses import dataclass, field
from typing import Optional

from ccai.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


# Re-export the canonical status enum under the feature-local name so
# tests and callers can import ``MailActionStatus`` from this module.
MailActionStatus = ActionStatus


# ----------------------------------------------------------------------
# Module-level constants
# ----------------------------------------------------------------------


_CAT = "cat"
_TEE = "tee"
_RM = "rm"
_STAT = "stat"
_POSTCONF = "postconf"
_POSTMAP = "postmap"
_POSTQUEUE = "postqueue"
_POSTSUPER = "postsuper"
_NEWALIASES = "newaliases"

_DEFAULT_POSTFIX_DIR = "/etc/postfix"
_DEFAULT_ALIASES_PATH = "/etc/aliases"
_DEFAULT_SERVICE_NAME = "postfix"


# ----------------------------------------------------------------------
# Status / config / result types
# ----------------------------------------------------------------------


@dataclass
class MailTransportManagerConfig(FeatureConfig):
    """
        Configuration for :class:`MailTransportManager`.

        :param postfix_dir: Directory containing Postfix lookup-table
                            map sources (``transport``, ``canonical``,
                            ``virtual``, ``access``, ...).  Default is
                            ``/etc/postfix``.
        :param aliases_path: Absolute path to the system aliases file.
                             Default is ``/etc/aliases``.
        :param autoreload: When True, every successful mutation of
                           ``main.cf`` or ``master.cf`` triggers a
                           ``service_manager.reload(service_name)`` so
                           that the running daemon picks up the change.
        :param service_name: Service name handed to the injected
                             :class:`ServiceManager`.  Default is
                             ``"postfix"``.
    """
    postfix_dir: str = _DEFAULT_POSTFIX_DIR
    aliases_path: str = _DEFAULT_ALIASES_PATH
    autoreload: bool = False
    service_name: str = _DEFAULT_SERVICE_NAME


@dataclass
class MailActionResult(FeatureActionResult):
    """
        Outcome of a single mail-transport-management action.

        Extends :class:`FeatureActionResult` with optional context
        fields used across the various verb families (directive name,
        master.cf service identifier, map file name, alias name, queue
        id).
    """
    directive: Optional[str] = None
    service: Optional[str] = None
    map_name: Optional[str] = None
    alias_name: Optional[str] = None
    queue_id: Optional[str] = None


# ----------------------------------------------------------------------
# Parsed-data dataclasses
# ----------------------------------------------------------------------


@dataclass
class MasterCfService:
    """
        A single ``master.cf`` service definition.

        :param service: Service name (``smtp``, ``submission``,
                        ``pickup``, ...).
        :param type: One of ``inet`` / ``unix`` / ``fifo`` / ``pass``.
        :param private: Privacy field (``-`` / ``y`` / ``n``).
        :param unpriv: Unprivileged field.
        :param chroot: Chroot field.
        :param wakeup: Wakeup field.
        :param maxproc: Max-process field.
        :param command: Command field (``smtpd`` / ``cleanup`` / ...).
        :param directives: Per-service ``-o key=value`` directives.
    """
    service: str
    type: str
    private: str = "-"
    unpriv: str = "-"
    chroot: str = "-"
    wakeup: str = "-"
    maxproc: str = "-"
    command: str = ""
    directives: dict = field(default_factory=dict)


@dataclass
class QueuedMessage:
    """
        A single record returned by ``postqueue -p`` / ``mailq``.

        :param queue_id: Postfix queue identifier.
        :param arrival_time: Free-form arrival timestamp text as
                             reported by Postfix.
        :param size_bytes: Reported size in bytes, or ``None`` when
                           parsing fails.
        :param sender: Envelope sender address.
        :param recipients: Envelope recipient list.
        :param state: ``*`` for active, ``!`` for hold, empty string
                      for deferred.
        :param last_error: Most-recent delivery error text, when
                           present in the queue dump.
    """
    queue_id: str
    arrival_time: str
    size_bytes: Optional[int]
    sender: str
    recipients: list = field(default_factory=list)
    state: str = ""
    last_error: Optional[str] = None


@dataclass
class MailQueueState:
    """
        Aggregated mail-queue state reported by ``postqueue -p``.

        :param empty: True when Postfix reported the queue as empty.
        :param messages: Parsed list of :class:`QueuedMessage` records.
    """
    empty: bool = True
    messages: list = field(default_factory=list)


# ----------------------------------------------------------------------
# Manager
# ----------------------------------------------------------------------


class MailTransportManager(BaseFeatureManager):
    """
        Postfix mail-transport-management facade.

        Wraps the Postfix admin commands and the on-disk configuration
        files (``main.cf``, ``master.cf``, lookup-table sources,
        ``/etc/aliases``) through the injected
        :class:`ICommandRunner`.  Service lifecycle is delegated to an
        injected :class:`ServiceManager`.
    """

    result_class = MailActionResult
    feature_name = "mail-transport-management"

    def __init__(
        self,
        runner,
        config: Optional[MailTransportManagerConfig] = None,
        *,
        service_manager=None,
        report=None,
    ) -> None:
        """
            :param runner: :class:`ICommandRunner` used by every
                           command dispatch.
            :param config: Manager configuration.  When omitted, the
                           default :class:`MailTransportManagerConfig`
                           is used.
            :param service_manager: Pre-built service manager to use
                                    for ``reload`` / ``restart`` and
                                    autoreload chaining.  When omitted,
                                    a default
                                    :class:`ServiceManager` is created
                                    against the same runner with
                                    matching ``dry_run`` / ``elevated``.
            :param report: Optional maintenance report.  Forwarded to
                           :class:`BaseFeatureManager`.
        """
        if config is None:
            config = MailTransportManagerConfig()

        super().__init__(runner=runner, config=config, report=report)

        if service_manager is None:
            from ccai.sysman.linux.redhat.servicemanagement import (
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
    # main.cf directives
    # ------------------------------------------------------------------

    def get_main_directive(self, key: str) -> Optional[str]:
        """
            Read a single ``main.cf`` directive via ``postconf -h``.

            :param key: The Postfix directive name.

            :returns: The stripped directive value, or ``None`` when
                      ``postconf`` exits non-zero.
        """
        command = [_POSTCONF, "-h", key]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: Optional[str] = None
            return rtnval
        rtnval = run_result.stdout.strip()
        return rtnval

    def get_all_main_directives(self) -> dict:
        """
            Read every ``main.cf`` directive via ``postconf`` (no arg).

            :returns: A ``dict[str, str]`` of directive name to value.
                      Empty when ``postconf`` exits non-zero.
        """
        command = [_POSTCONF]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: dict = {}
            return rtnval
        rtnval = _parse_postconf_output(text=run_result.stdout)
        return rtnval

    def set_main_directive(
        self,
        key: str,
        value: str,
    ) -> MailActionResult:
        """
            Set a ``main.cf`` directive via ``postconf -e``.

            Idempotent: returns :data:`MailActionStatus.SKIPPED` when
            the live value already matches ``value``.

            :param key: The Postfix directive name.
            :param value: The desired directive value.

            :returns: The :class:`MailActionResult`.
        """
        current = self.get_main_directive(key=key)
        if current is not None:
            if current == value:
                skipped = MailActionResult(
                    status=MailActionStatus.SKIPPED,
                    detail="set_main_directive:{}".format(key),
                    directive=key,
                )
                return skipped

        command = [_POSTCONF, "-e", "{}={}".format(key, value)]
        result = self._dispatch(
            command=command,
            detail="set_main_directive:{}".format(key),
            operation="set_main_directive",
            directive=key,
        )
        if result.status == MailActionStatus.SUCCESS:
            self._maybe_autoreload()
        return result

    def remove_main_directive(self, key: str) -> MailActionResult:
        """
            Revert a ``main.cf`` directive to its default via
            ``postconf -X``.

            Idempotent: returns :data:`MailActionStatus.SKIPPED` when
            the directive is already absent (i.e. ``postconf -h``
            returns the default value matching ``postconf -d -h``).

            :param key: The Postfix directive name.

            :returns: The :class:`MailActionResult`.
        """
        current = self.get_main_directive(key=key)
        default_value = self._get_default_directive(key=key)
        if current is not None and default_value is not None:
            if current == default_value:
                skipped = MailActionResult(
                    status=MailActionStatus.SKIPPED,
                    detail="remove_main_directive:{}".format(key),
                    directive=key,
                )
                return skipped

        command = [_POSTCONF, "-X", key]
        result = self._dispatch(
            command=command,
            detail="remove_main_directive:{}".format(key),
            operation="remove_main_directive",
            directive=key,
        )
        if result.status == MailActionStatus.SUCCESS:
            self._maybe_autoreload()
        return result

    # ------------------------------------------------------------------
    # master.cf services
    # ------------------------------------------------------------------

    def get_master_service(
        self,
        service: str,
        type_: str,
    ) -> Optional[MasterCfService]:
        """
            Read a ``master.cf`` service definition via
            ``postconf -Mh <service>/<type>``.

            :param service: The service name.
            :param type_: The service type (``inet`` / ``unix`` / ...).

            :returns: The parsed :class:`MasterCfService`, or ``None``
                      when the service is absent.
        """
        spec = "{}/{}".format(service, type_)
        command = [_POSTCONF, "-Mh", spec]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: Optional[MasterCfService] = None
            return rtnval
        text = run_result.stdout.strip()
        if len(text) == 0:
            rtnval = None
            return rtnval
        rtnval = _parse_master_service_line(line=text)
        return rtnval

    def set_master_service(
        self,
        spec: MasterCfService,
    ) -> MailActionResult:
        """
            Define or update a ``master.cf`` service via
            ``postconf -M``.

            Idempotent: returns :data:`MailActionStatus.SKIPPED` when
            the live service line already matches ``spec``.

            :param spec: The :class:`MasterCfService` to install.

            :returns: The :class:`MailActionResult`.
        """
        existing = self.get_master_service(
            service=spec.service, type_=spec.type,
        )
        rendered = _render_master_service_line(spec=spec)
        if existing is not None:
            existing_rendered = _render_master_service_line(spec=existing)
            if existing_rendered == rendered:
                skipped = MailActionResult(
                    status=MailActionStatus.SKIPPED,
                    detail="set_master_service:{}/{}".format(
                        spec.service, spec.type,
                    ),
                    service="{}/{}".format(spec.service, spec.type),
                )
                return skipped

        argv = [
            _POSTCONF,
            "-M",
            "{}/{}={}".format(spec.service, spec.type, rendered),
        ]
        result = self._dispatch(
            command=argv,
            detail="set_master_service:{}/{}".format(
                spec.service, spec.type,
            ),
            operation="set_master_service",
            service="{}/{}".format(spec.service, spec.type),
        )
        if result.status == MailActionStatus.SUCCESS:
            self._maybe_autoreload()
        return result

    def set_master_directive(
        self,
        service: str,
        type_: str,
        key: str,
        value: str,
    ) -> MailActionResult:
        """
            Set a service-scoped directive via ``postconf -P``.

            Idempotent: returns :data:`MailActionStatus.SKIPPED` when
            the live per-service value already matches ``value``.

            :param service: The service name.
            :param type_: The service type.
            :param key: The directive name.
            :param value: The desired value.

            :returns: The :class:`MailActionResult`.
        """
        scope = "{}/{}/{}".format(service, type_, key)
        # Read current value via postconf -P scope
        query_command = [_POSTCONF, "-P", "-h", scope]
        query_run = self._runner.run(list(query_command), elevated=False)
        current_value: Optional[str] = None
        if query_run.exit_code == 0:
            current_value = query_run.stdout.strip()
        if current_value is not None:
            if current_value == value:
                skipped = MailActionResult(
                    status=MailActionStatus.SKIPPED,
                    detail="set_master_directive:{}".format(scope),
                    service="{}/{}".format(service, type_),
                    directive=key,
                )
                return skipped

        argv = [_POSTCONF, "-P", "{}={}".format(scope, value)]
        result = self._dispatch(
            command=argv,
            detail="set_master_directive:{}".format(scope),
            operation="set_master_directive",
            service="{}/{}".format(service, type_),
            directive=key,
        )
        if result.status == MailActionStatus.SUCCESS:
            self._maybe_autoreload()
        return result

    def remove_master_service(
        self,
        service: str,
        type_: str,
    ) -> MailActionResult:
        """
            Remove a ``master.cf`` service via ``postconf -MX``.

            Idempotent: returns :data:`MailActionStatus.SKIPPED` when
            no such service is currently defined.

            :param service: The service name.
            :param type_: The service type.

            :returns: The :class:`MailActionResult`.
        """
        existing = self.get_master_service(service=service, type_=type_)
        scope = "{}/{}".format(service, type_)
        if existing is None:
            skipped = MailActionResult(
                status=MailActionStatus.SKIPPED,
                detail="remove_master_service:{}".format(scope),
                service=scope,
            )
            return skipped

        argv = [_POSTCONF, "-MX", scope]
        result = self._dispatch(
            command=argv,
            detail="remove_master_service:{}".format(scope),
            operation="remove_master_service",
            service=scope,
        )
        if result.status == MailActionStatus.SUCCESS:
            self._maybe_autoreload()
        return result

    # ------------------------------------------------------------------
    # Lookup-table maps
    # ------------------------------------------------------------------

    def write_map(
        self,
        name: str,
        content: str,
    ) -> MailActionResult:
        """
            Write a Postfix lookup-table source file under
            ``postfix_dir`` and recompile its ``.db`` via ``postmap``.

            Idempotent on content: when the file already matches
            ``content``, the source-file write is skipped.  However,
            ``postmap`` is still invoked when the compiled ``.db``
            timestamp does not post-date the source's (or is missing).

            :param name: The map source filename (e.g. ``"transport"``).
            :param content: The desired text content of the map file.

            :returns: The :class:`MailActionResult`.  ``map_name`` on
                      the result is set to ``name``.
        """
        path = self._map_path(name=name)
        write_command = [_TEE, path]

        existing = self._read_file(path=path)
        if existing is not None and existing == content:
            # Content matches.  Decide whether to recompile.
            if self._db_is_fresh(source_path=path):
                skipped = MailActionResult(
                    status=MailActionStatus.SKIPPED,
                    detail="write_map:{}".format(name),
                    map_name=name,
                )
                return skipped
            # .db is stale or missing -> compile only.
            compile_result = self.compile_map(name=name)
            return compile_result

        if self._config.dry_run:
            dry = MailActionResult(
                status=MailActionStatus.DRY_RUN,
                detail="write_map:{}".format(name),
                command=list(write_command),
                map_name=name,
            )
            return dry

        run_result = self._runner.run(
            list(write_command),
            elevated=self._config.elevated,
            stdin=content,
        )
        if run_result.exit_code != 0:
            failed = MailActionResult(
                status=MailActionStatus.FAILED,
                detail="write_map:{}".format(name),
                exit_code=run_result.exit_code,
                stdout=run_result.stdout,
                stderr=run_result.stderr,
                command=list(write_command),
                map_name=name,
            )
            return failed

        compile_result = self.compile_map(name=name)
        # Surface the compile result, but tag with the write detail.
        if compile_result.status != MailActionStatus.SUCCESS:
            compile_result.detail = "write_map:{}".format(name)
            compile_result.map_name = name
            return compile_result

        rtnval = MailActionResult(
            status=MailActionStatus.SUCCESS,
            detail="write_map:{}".format(name),
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(write_command),
            map_name=name,
        )
        return rtnval

    def compile_map(self, name: str) -> MailActionResult:
        """
            Recompile a lookup-table source via
            ``postmap /etc/postfix/<name>``.

            :param name: The map source filename.

            :returns: The :class:`MailActionResult`.
        """
        path = self._map_path(name=name)
        command = [_POSTMAP, path]
        rtnval = self._dispatch(
            command=command,
            detail="compile_map:{}".format(name),
            operation="compile_map",
            map_name=name,
        )
        return rtnval

    def query_map(
        self,
        name: str,
        key: str,
    ) -> Optional[str]:
        """
            Look up a key in a compiled lookup table via
            ``postmap -q <key> /etc/postfix/<name>``.

            :param name: The map name.
            :param key: The key to look up.

            :returns: The stripped value, or ``None`` when
                      ``postmap -q`` exits non-zero.
        """
        path = self._map_path(name=name)
        command = [_POSTMAP, "-q", key, path]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: Optional[str] = None
            return rtnval
        rtnval = run_result.stdout.strip()
        if len(rtnval) == 0:
            rtnval = None
        return rtnval

    def remove_map(self, name: str) -> MailActionResult:
        """
            Remove the source file and compiled ``.db`` for a map.

            Idempotent: returns :data:`MailActionStatus.SKIPPED` when
            both files are already absent.

            :param name: The map source filename.

            :returns: The :class:`MailActionResult`.
        """
        path = self._map_path(name=name)
        db_path = "{}.db".format(path)
        source_present = self._path_exists(path=path)
        db_present = self._path_exists(path=db_path)
        if not source_present and not db_present:
            skipped = MailActionResult(
                status=MailActionStatus.SKIPPED,
                detail="remove_map:{}".format(name),
                map_name=name,
            )
            return skipped

        command = [_RM, "-f", path, db_path]
        rtnval = self._dispatch(
            command=command,
            detail="remove_map:{}".format(name),
            operation="remove_map",
            map_name=name,
        )
        return rtnval

    # ------------------------------------------------------------------
    # /etc/aliases
    # ------------------------------------------------------------------

    def list_aliases(self) -> dict:
        """
            Parse ``/etc/aliases`` into ``dict[str, list[str]]``.

            :returns: A dict mapping each local alias name to its list
                      of comma-separated targets.  Empty when the file
                      cannot be read.
        """
        path = self._typed_config().aliases_path
        content = self._read_file(path=path)
        if content is None:
            rtnval: dict = {}
            return rtnval
        rtnval = _parse_aliases_text(content=content)
        return rtnval

    def set_alias(
        self,
        name: str,
        targets: list,
    ) -> MailActionResult:
        """
            Upsert an alias entry of the form ``<name>: <targets>`` in
            ``/etc/aliases`` and rebuild the aliases database via
            ``newaliases``.

            Idempotent: returns :data:`MailActionStatus.SKIPPED` when
            the live targets list for ``name`` already matches
            ``targets``.

            :param name: The local alias name.
            :param targets: The list of target addresses.

            :returns: The :class:`MailActionResult`.
        """
        path = self._typed_config().aliases_path
        write_command = [_TEE, path]
        existing_text = self._read_file(path=path)
        if existing_text is None:
            existing_text = ""
        existing_aliases = _parse_aliases_text(content=existing_text)
        current_targets = existing_aliases.get(name)
        if current_targets is not None:
            if current_targets == list(targets):
                skipped = MailActionResult(
                    status=MailActionStatus.SKIPPED,
                    detail="set_alias:{}".format(name),
                    alias_name=name,
                )
                return skipped

        new_text = _upsert_alias_text(
            content=existing_text, name=name, targets=targets,
        )

        if self._config.dry_run:
            dry = MailActionResult(
                status=MailActionStatus.DRY_RUN,
                detail="set_alias:{}".format(name),
                command=list(write_command),
                alias_name=name,
            )
            return dry

        write_run = self._runner.run(
            list(write_command),
            elevated=self._config.elevated,
            stdin=new_text,
        )
        if write_run.exit_code != 0:
            failed = MailActionResult(
                status=MailActionStatus.FAILED,
                detail="set_alias:{}".format(name),
                exit_code=write_run.exit_code,
                stdout=write_run.stdout,
                stderr=write_run.stderr,
                command=list(write_command),
                alias_name=name,
            )
            return failed

        rtnval = self._invoke_newaliases(
            base_action="set_alias:{}".format(name),
            alias_name=name,
            primary_run=write_run,
            primary_command=write_command,
        )
        return rtnval

    def remove_alias(self, name: str) -> MailActionResult:
        """
            Remove the entry whose left-hand side equals ``name`` from
            ``/etc/aliases`` and rebuild the aliases database via
            ``newaliases``.

            Idempotent: returns :data:`MailActionStatus.SKIPPED` when
            no such entry is currently present.

            :param name: The alias name to remove.

            :returns: The :class:`MailActionResult`.
        """
        path = self._typed_config().aliases_path
        write_command = [_TEE, path]
        existing_text = self._read_file(path=path)
        if existing_text is None:
            skipped = MailActionResult(
                status=MailActionStatus.SKIPPED,
                detail="remove_alias:{}".format(name),
                alias_name=name,
            )
            return skipped

        existing_aliases = _parse_aliases_text(content=existing_text)
        if name not in existing_aliases:
            skipped = MailActionResult(
                status=MailActionStatus.SKIPPED,
                detail="remove_alias:{}".format(name),
                alias_name=name,
            )
            return skipped

        new_text = _remove_alias_text(content=existing_text, name=name)

        if self._config.dry_run:
            dry = MailActionResult(
                status=MailActionStatus.DRY_RUN,
                detail="remove_alias:{}".format(name),
                command=list(write_command),
                alias_name=name,
            )
            return dry

        write_run = self._runner.run(
            list(write_command),
            elevated=self._config.elevated,
            stdin=new_text,
        )
        if write_run.exit_code != 0:
            failed = MailActionResult(
                status=MailActionStatus.FAILED,
                detail="remove_alias:{}".format(name),
                exit_code=write_run.exit_code,
                stdout=write_run.stdout,
                stderr=write_run.stderr,
                command=list(write_command),
                alias_name=name,
            )
            return failed

        rtnval = self._invoke_newaliases(
            base_action="remove_alias:{}".format(name),
            alias_name=name,
            primary_run=write_run,
            primary_command=write_command,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Mail queue
    # ------------------------------------------------------------------

    def list_queue(self) -> MailQueueState:
        """
            Read the Postfix mail queue via ``postqueue -p``.

            :returns: The :class:`MailQueueState`.  ``empty`` is True
                      and ``messages`` is the empty list when Postfix
                      reports an empty queue.
        """
        command = [_POSTQUEUE, "-p"]
        run_result = self._runner.run(list(command), elevated=False)
        rtnval = MailQueueState()
        if run_result.exit_code != 0:
            return rtnval
        text = run_result.stdout
        if "Mail queue is empty" in text:
            return rtnval
        parsed = _parse_postqueue_output(text=text)
        if len(parsed) > 0:
            rtnval.empty = False
            rtnval.messages = parsed
        return rtnval

    def flush_queue(self) -> MailActionResult:
        """
            Attempt immediate delivery of every queued message via
            ``postqueue -f``.

            Operational verb: never skips.

            :returns: The :class:`MailActionResult`.
        """
        command = [_POSTQUEUE, "-f"]
        rtnval = self._dispatch(
            command=command,
            detail="flush_queue",
            operation="flush_queue",
        )
        return rtnval

    def delete_message(self, queue_id: str) -> MailActionResult:
        """
            Delete a single queued message via
            ``postsuper -d <queue_id>``.

            Idempotent: returns :data:`MailActionStatus.SKIPPED` when
            no such queue id is present in the current queue listing.

            :param queue_id: The Postfix queue id to delete.

            :returns: The :class:`MailActionResult`.
        """
        state = self.list_queue()
        ids = [m.queue_id for m in state.messages]
        if queue_id not in ids:
            skipped = MailActionResult(
                status=MailActionStatus.SKIPPED,
                detail="delete_message:{}".format(queue_id),
                queue_id=queue_id,
            )
            return skipped

        command = [_POSTSUPER, "-d", queue_id]
        rtnval = self._dispatch(
            command=command,
            detail="delete_message:{}".format(queue_id),
            operation="delete_message",
            queue_id=queue_id,
        )
        return rtnval

    def delete_all_queued(self) -> MailActionResult:
        """
            Delete every queued message via ``postsuper -d ALL``.

            Operational verb: never skips.

            :returns: The :class:`MailActionResult`.
        """
        command = [_POSTSUPER, "-d", "ALL"]
        rtnval = self._dispatch(
            command=command,
            detail="delete_all_queued",
            operation="delete_all_queued",
        )
        return rtnval

    def requeue_message(self, queue_id: str) -> MailActionResult:
        """
            Requeue a single message via ``postsuper -r <queue_id>``.

            :param queue_id: The Postfix queue id to requeue.

            :returns: The :class:`MailActionResult`.
        """
        command = [_POSTSUPER, "-r", queue_id]
        rtnval = self._dispatch(
            command=command,
            detail="requeue_message:{}".format(queue_id),
            operation="requeue_message",
            queue_id=queue_id,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Service lifecycle (delegated)
    # ------------------------------------------------------------------

    def reload(self) -> MailActionResult:
        """
            Reload Postfix via the injected service manager.

            :returns: The :class:`MailActionResult` mirroring the
                      service manager's outcome.
        """
        service_name = self._typed_config().service_name
        delegated = self._service_manager.reload(service_name)
        rtnval = _as_mail_action_result(
            base_result=delegated,
            detail="reload:{}".format(service_name),
        )
        return rtnval

    def restart(self) -> MailActionResult:
        """
            Restart Postfix via the injected service manager.

            :returns: The :class:`MailActionResult` mirroring the
                      service manager's outcome.
        """
        service_name = self._typed_config().service_name
        delegated = self._service_manager.restart(service_name)
        rtnval = _as_mail_action_result(
            base_result=delegated,
            detail="restart:{}".format(service_name),
        )
        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _typed_config(self) -> MailTransportManagerConfig:
        """
            Narrow the base config to the typed subclass.

            :returns: The typed
                      :class:`MailTransportManagerConfig`.
        """
        rtnval = self._config
        return rtnval

    def _map_path(self, *, name: str) -> str:
        """
            Build the absolute path to a Postfix lookup-table source.

            :param name: The map source filename.

            :returns: ``<postfix_dir>/<name>``.
        """
        directory = self._typed_config().postfix_dir
        rtnval = "{}/{}".format(directory, name)
        return rtnval

    def _read_file(self, *, path: str) -> Optional[str]:
        """
            Read a file via ``cat`` (no elevation).

            :param path: The absolute path.

            :returns: The contents on success; ``None`` when ``cat``
                      reports a non-zero exit code.
        """
        command = [_CAT, path]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: Optional[str] = None
            return rtnval
        rtnval = run_result.stdout
        return rtnval

    def _path_exists(self, *, path: str) -> bool:
        """
            Probe path existence via ``stat`` through the runner.

            :param path: The absolute path to probe.

            :returns: True when ``stat`` exits 0.
        """
        command = [_STAT, path]
        run_result = self._runner.run(list(command), elevated=False)
        rtnval = run_result.exit_code == 0
        return rtnval

    def _db_is_fresh(self, *, source_path: str) -> bool:
        """
            Decide whether the compiled ``.db`` is at least as recent
            as the source file.

            :param source_path: Absolute path to the source map file.

            :returns: True when the ``.db`` exists and its mtime is
                      not older than the source's mtime.  False when
                      either path is missing or the ``.db`` is older.
        """
        db_path = "{}.db".format(source_path)
        source_mtime = self._mtime_for(path=source_path)
        db_mtime = self._mtime_for(path=db_path)
        if source_mtime is None:
            rtnval = False
            return rtnval
        if db_mtime is None:
            rtnval = False
            return rtnval
        rtnval = db_mtime >= source_mtime
        return rtnval

    def _mtime_for(self, *, path: str) -> Optional[int]:
        """
            Read the integer mtime of a path via ``stat -c %Y``.

            :param path: Absolute path.

            :returns: The mtime in seconds since epoch, or ``None``
                      when ``stat`` exits non-zero or the output does
                      not parse as an integer.
        """
        command = [_STAT, "-c", "%Y", path]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: Optional[int] = None
            return rtnval
        text = run_result.stdout.strip()
        try:
            parsed = int(text)
        except ValueError:
            parsed = None
        rtnval = parsed
        return rtnval

    def _get_default_directive(self, *, key: str) -> Optional[str]:
        """
            Read the built-in default value of a ``main.cf`` directive
            via ``postconf -d -h <key>``.

            :param key: The directive name.

            :returns: The stripped default value, or ``None`` when the
                      query fails.
        """
        command = [_POSTCONF, "-d", "-h", key]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: Optional[str] = None
            return rtnval
        rtnval = run_result.stdout.strip()
        return rtnval

    def _invoke_newaliases(
        self,
        *,
        base_action: str,
        alias_name: str,
        primary_run,
        primary_command: list,
    ) -> MailActionResult:
        """
            Run ``newaliases`` and assemble the final result.

            When ``newaliases`` fails, the action is reported as
            FAILED with the ``newaliases`` argv as ``command``.  When
            ``newaliases`` succeeds, the result reflects the preceding
            successful write.

            :param base_action: ``set_alias:<name>`` or
                                ``remove_alias:<name>``.
            :param alias_name: The alias name.
            :param primary_run: The :class:`RunResult` from the prior
                                ``tee`` write.
            :param primary_command: The argv of the prior ``tee`` write.

            :returns: The :class:`MailActionResult`.
        """
        newaliases_command = [_NEWALIASES]
        newaliases_run = self._runner.run(
            list(newaliases_command),
            elevated=self._config.elevated,
        )
        if newaliases_run.exit_code != 0:
            failed = MailActionResult(
                status=MailActionStatus.FAILED,
                detail=base_action,
                exit_code=newaliases_run.exit_code,
                stdout=newaliases_run.stdout,
                stderr=newaliases_run.stderr,
                command=list(newaliases_command),
                alias_name=alias_name,
            )
            return failed

        rtnval = MailActionResult(
            status=MailActionStatus.SUCCESS,
            detail=base_action,
            exit_code=primary_run.exit_code,
            stdout=primary_run.stdout,
            stderr=primary_run.stderr,
            command=list(primary_command),
            alias_name=alias_name,
        )
        return rtnval

    def _maybe_autoreload(self) -> None:
        """
            When ``config.autoreload`` is True, reload Postfix via the
            injected service manager.  No-op otherwise.
        """
        if self._typed_config().autoreload is False:
            return
        service_name = self._typed_config().service_name
        self._service_manager.reload(service_name)
        return


# ----------------------------------------------------------------------
# Module-level helpers — postconf parsing / master.cf rendering
# ----------------------------------------------------------------------


def _parse_postconf_output(*, text: str) -> dict:
    """
        Parse ``postconf`` (no arg) output into a directive map.

        :param text: Captured stdout.

        :returns: A ``dict[str, str]`` of directive name to value.
    """
    rtnval: dict = {}
    for raw in text.splitlines():
        stripped = raw.rstrip()
        if len(stripped.strip()) == 0:
            continue
        if stripped.lstrip().startswith("#"):
            continue
        if "=" not in stripped:
            continue
        before, after = stripped.split("=", 1)
        key = before.strip()
        value = after.strip()
        if len(key) == 0:
            continue
        rtnval[key] = value
    return rtnval


def _parse_master_service_line(*, line: str) -> Optional[MasterCfService]:
    """
        Parse a single ``master.cf`` service line.

        ``postconf -Mh <service>/<type>`` prints the canonical 8-field
        line.  Fields are whitespace-separated; the trailing ``command``
        field may itself contain whitespace.

        :param line: The raw line text.

        :returns: A :class:`MasterCfService`, or ``None`` when the
                  line cannot be parsed.
    """
    parts = line.strip().split(None, 7)
    if len(parts) < 8:
        rtnval: Optional[MasterCfService] = None
        return rtnval
    rtnval = MasterCfService(
        service=parts[0],
        type=parts[1],
        private=parts[2],
        unpriv=parts[3],
        chroot=parts[4],
        wakeup=parts[5],
        maxproc=parts[6],
        command=parts[7],
    )
    return rtnval


def _render_master_service_line(*, spec: MasterCfService) -> str:
    """
        Render a :class:`MasterCfService` into the canonical 8-field
        ``master.cf`` text representation.

        :param spec: The service spec.

        :returns: The rendered line.
    """
    fields = [
        spec.service,
        spec.type,
        spec.private,
        spec.unpriv,
        spec.chroot,
        spec.wakeup,
        spec.maxproc,
        spec.command,
    ]
    rtnval = " ".join(fields)
    return rtnval


# ----------------------------------------------------------------------
# Module-level helpers — aliases parser / renderer
# ----------------------------------------------------------------------


def _parse_aliases_text(*, content: str) -> dict:
    """
        Parse ``/etc/aliases`` text into ``dict[str, list[str]]``.

        Comment / blank lines are ignored.  Continuation lines (lines
        starting with whitespace) extend the prior entry's targets.
        Targets are split on commas with surrounding whitespace
        trimmed.

        :param content: The raw file content.

        :returns: A ``dict[str, list[str]]`` of alias name to target
                  list.
    """
    rtnval: dict = {}
    last_name: Optional[str] = None
    for raw in content.splitlines():
        if len(raw.strip()) == 0:
            continue
        if raw.lstrip().startswith("#"):
            continue
        if raw[0] in (" ", "\t") and last_name is not None:
            extra_targets = _split_alias_targets(text=raw.strip())
            existing = rtnval.get(last_name)
            if existing is not None:
                existing.extend(extra_targets)
            continue
        if ":" not in raw:
            continue
        before, after = raw.split(":", 1)
        name = before.strip()
        if len(name) == 0:
            continue
        targets = _split_alias_targets(text=after.strip())
        rtnval[name] = targets
        last_name = name
    return rtnval


def _split_alias_targets(*, text: str) -> list:
    """
        Split a target text into a list of trimmed entries.

        :param text: The right-hand side of an aliases line.

        :returns: A list of trimmed target strings.  Empty entries are
                  dropped.
    """
    rtnval: list = []
    for raw in text.split(","):
        stripped = raw.strip()
        if len(stripped) == 0:
            continue
        rtnval.append(stripped)
    return rtnval


def _upsert_alias_text(
    *,
    content: str,
    name: str,
    targets: list,
) -> str:
    """
        Return ``content`` with ``<name>: <targets>`` upserted.

        :param content: Existing aliases-file text.
        :param name: The alias name.
        :param targets: The target list.

        :returns: The new file text.
    """
    new_lines: list = []
    replaced = False
    rendered = "{}: {}".format(name, ", ".join(targets))
    for raw in content.splitlines():
        stripped = raw.strip()
        if len(stripped) == 0 or stripped.startswith("#"):
            new_lines.append(raw)
            continue
        if ":" not in raw:
            new_lines.append(raw)
            continue
        before, _ = raw.split(":", 1)
        existing_name = before.strip()
        if existing_name == name:
            new_lines.append(rendered)
            replaced = True
            continue
        new_lines.append(raw)

    if not replaced:
        new_lines.append(rendered)

    body = "\n".join(new_lines)
    if not body.endswith("\n"):
        body = body + "\n"
    return body


def _remove_alias_text(*, content: str, name: str) -> str:
    """
        Return ``content`` with every entry whose left-hand side
        equals ``name`` stripped.

        :param content: Existing aliases-file text.
        :param name: The alias name to remove.

        :returns: The new file text.
    """
    new_lines: list = []
    for raw in content.splitlines():
        stripped = raw.strip()
        if len(stripped) == 0 or stripped.startswith("#"):
            new_lines.append(raw)
            continue
        if ":" not in raw:
            new_lines.append(raw)
            continue
        before, _ = raw.split(":", 1)
        existing_name = before.strip()
        if existing_name == name:
            continue
        new_lines.append(raw)

    body = "\n".join(new_lines)
    if len(body) > 0 and not body.endswith("\n"):
        body = body + "\n"
    return body


# ----------------------------------------------------------------------
# Module-level helpers — postqueue parser
# ----------------------------------------------------------------------


def _parse_postqueue_output(*, text: str) -> list:
    """
        Parse ``postqueue -p`` output into a list of
        :class:`QueuedMessage` records.

        Postfix's queue listing has a header row, then per-message
        records.  A record's first line is::

            <qid><marker>  <size>  <arrival>  <sender>

        where ``<marker>`` is ``*`` (active), ``!`` (hold), or empty
        (deferred).  Subsequent lines may contain a parenthesised
        error and one or more recipient addresses.  Records are
        separated by blank lines.  The trailing footer line beginning
        with ``--`` is ignored.

        :param text: Captured stdout.

        :returns: ``list[QueuedMessage]`` in queue order.
    """
    rtnval: list = []
    current: Optional[QueuedMessage] = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if len(line.strip()) == 0:
            if current is not None:
                rtnval.append(current)
                current = None
            continue
        # Header line:
        if line.startswith("-Queue ID"):
            continue
        # Footer:
        if line.lstrip().startswith("--"):
            if current is not None:
                rtnval.append(current)
                current = None
            continue
        if not (line.startswith(" ") or line.startswith("\t")):
            # New record head line.
            if current is not None:
                rtnval.append(current)
                current = None
            parsed = _parse_postqueue_head(line=line)
            if parsed is not None:
                current = parsed
            continue
        # Continuation: error text or recipient.
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith("(") and stripped.endswith(")"):
            current.last_error = stripped[1:-1]
            continue
        # Otherwise treat as recipient.
        current.recipients.append(stripped)

    if current is not None:
        rtnval.append(current)

    return rtnval


def _parse_postqueue_head(*, line: str) -> Optional[QueuedMessage]:
    """
        Parse the head line of a single ``postqueue -p`` record.

        Format: ``<qid><marker><spaces><size><spaces><arrival_text><spaces><sender>``.
        ``arrival_text`` itself contains spaces (e.g. ``Mon May  6 12:34:56``).
        We split out the leading queue id and trailing sender, and
        treat everything in between as arrival text.

        :param line: The raw head line.

        :returns: A :class:`QueuedMessage` (with empty recipients), or
                  ``None`` when the line cannot be parsed.
    """
    parts = line.split()
    if len(parts) < 4:
        rtnval: Optional[QueuedMessage] = None
        return rtnval

    head = parts[0]
    state = ""
    queue_id = head
    if head.endswith("*"):
        state = "*"
        queue_id = head[:-1]
    elif head.endswith("!"):
        state = "!"
        queue_id = head[:-1]

    size_text = parts[1]
    size_bytes: Optional[int]
    try:
        size_bytes = int(size_text)
    except ValueError:
        size_bytes = None

    sender = parts[-1]
    arrival_parts = parts[2:-1]
    arrival_time = " ".join(arrival_parts)

    rtnval = QueuedMessage(
        queue_id=queue_id,
        arrival_time=arrival_time,
        size_bytes=size_bytes,
        sender=sender,
        recipients=[],
        state=state,
        last_error=None,
    )
    return rtnval


# ----------------------------------------------------------------------
# Module-level helpers — service-result conversion
# ----------------------------------------------------------------------


def _as_mail_action_result(
    *,
    base_result,
    detail: Optional[str],
) -> MailActionResult:
    """
        Convert a delegated :class:`FeatureActionResult` (from the
        injected service manager) into a :class:`MailActionResult`.

        :param base_result: The delegated result.
        :param detail: Optional detail override.

        :returns: The :class:`MailActionResult`.
    """
    delegated_status = base_result.status
    # Map by string value so cross-enum delegation remains robust.
    mapped_status = MailActionStatus(delegated_status.value)
    rtnval = MailActionResult(
        status=mapped_status,
        detail=detail if detail is not None else base_result.detail,
        exit_code=base_result.exit_code,
        stdout=base_result.stdout,
        stderr=base_result.stderr,
        command=base_result.command,
    )
    return rtnval
