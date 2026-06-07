# ccai.sysman.linux.redhat.aspects.persistence
#
# Execution aspect that logs command executions to a storage backend.

import logging
import time
import uuid

from dataclasses import dataclass
from typing import Optional

from ccai.sysman.linux.redhat.runners.models import ExecutionContext, RunResult
from ccai.sysman.linux.redhat.storage.base import CommandLogEntry, ICommandStorage


LOGGER = logging.getLogger(__name__)

_METADATA_ENTRY_ID_KEY = "persistence.entry_id"
_METADATA_START_TIME_KEY = "persistence.start_time"


@dataclass
class PersistenceAspectConfig:
    """
        Configuration for :class:`PersistenceAspect`.

        :param storage_backend: Backend used to persist log entries.
        :param log_environment: Whether to include the command environment in the log.
        :param log_output: Whether to include stdout/stderr in the log.
        :param fail_on_storage_error: When True, storage failures abort execution.
    """

    storage_backend: ICommandStorage
    log_environment: bool = False
    log_output: bool = True
    fail_on_storage_error: bool = False


class PersistenceAspect:
    """
        Execution aspect that records each command invocation to a
        :class:`ICommandStorage` backend.
    """

    def __init__(self, config: PersistenceAspectConfig) -> None:
        """
            :param config: Persistence configuration.
        """
        self._config: PersistenceAspectConfig = config
        self._enabled: bool = True
        return

    @property
    def enabled(self) -> bool:
        """
            Whether this aspect is active.
        """
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value
        return

    @property
    def config(self) -> PersistenceAspectConfig:
        """
            The active :class:`PersistenceAspectConfig`.
        """
        return self._config

    def before_execute(self, context: ExecutionContext) -> None:
        """
            Record a new log entry representing the pending command.
        """
        entry_id = str(uuid.uuid4())
        start_time = time.time()

        logged_env: Optional[dict[str, str]] = None
        if self._config.log_environment and context.env is not None:
            logged_env = dict(context.env)

        entry = CommandLogEntry(
            entry_id=entry_id,
            command=list(context.args),
            cwd=context.cwd,
            env=logged_env,
            timestamp_start=start_time,
            transport=context.transport,
            elevated=context.elevated,
            dry_run=context.dry_run,
        )

        try:
            assigned_id = self._config.storage_backend.log_command(entry)
            if assigned_id is not None and len(assigned_id) > 0:
                entry_id = assigned_id
        except Exception as ex:
            if self._config.fail_on_storage_error:
                raise
            LOGGER.warning("PersistenceAspect: log_command failed: %s", ex)

        context.metadata[_METADATA_ENTRY_ID_KEY] = entry_id
        context.metadata[_METADATA_START_TIME_KEY] = start_time
        return

    def after_execute(
        self,
        context: ExecutionContext,
        result: RunResult,
    ) -> RunResult:
        """
            Update the previously recorded log entry with the execution result.
        """
        entry_id = context.metadata.get(_METADATA_ENTRY_ID_KEY)
        if entry_id is None:
            LOGGER.warning("PersistenceAspect: missing entry id in metadata")
            return result

        logged_result = result
        if not self._config.log_output:
            logged_result = RunResult(
                exit_code=result.exit_code,
                stdout="",
                stderr="",
                command=result.command,
                timed_out=result.timed_out,
                dry_run=result.dry_run,
                duration_ms=result.duration_ms,
                retry_count=result.retry_count,
                transport=result.transport,
            )

        try:
            self._config.storage_backend.update_command(entry_id, logged_result)
        except Exception as ex:
            if self._config.fail_on_storage_error:
                raise
            LOGGER.warning("PersistenceAspect: update_command failed: %s", ex)

        return result
