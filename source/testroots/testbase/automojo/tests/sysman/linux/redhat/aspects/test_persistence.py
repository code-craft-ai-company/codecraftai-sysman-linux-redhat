# Tests for the PersistenceAspect.

from dataclasses import dataclass, field
from typing import Optional

from automojo import testbase

from automojo.sysman.linux.redhat.aspects.persistence import (
    PersistenceAspect,
    PersistenceAspectConfig,
)
from automojo.sysman.linux.redhat.runners.models import ExecutionContext, RunResult
from automojo.sysman.linux.redhat.storage.base import CommandLogEntry


@dataclass
class _InMemoryStorage:
    """
        Storage stub that keeps log entries in-memory for inspection.
    """

    entries: list[CommandLogEntry] = field(default_factory=list)
    updates: dict[str, RunResult] = field(default_factory=dict)
    raise_on_log: bool = False
    raise_on_update: bool = False

    def log_command(self, entry: CommandLogEntry) -> str:
        if self.raise_on_log:
            raise RuntimeError("log failure")
        self.entries.append(entry)
        return entry.entry_id

    def update_command(self, entry_id: str, result: RunResult) -> None:
        if self.raise_on_update:
            raise RuntimeError("update failure")
        self.updates[entry_id] = result
        return


def _make_context(*, env: Optional[dict[str, str]] = None) -> ExecutionContext:
    """
        Build a minimal execution context for persistence-aspect tests.
    """
    context = ExecutionContext(
        args=["ls", "-l"],
        elevated=False,
        cwd="/tmp",
        env=env,
        timeout=None,
        transport="local",
        dry_run=False,
    )
    return context


def test_before_execute_records_entry():
    """
        before_execute must append a log entry for the pending command.
    """
    storage = _InMemoryStorage()
    aspect = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
    context = _make_context()
    aspect.before_execute(context)
    testbase.assert_equal(len(storage.entries), 1)
    entry_id = context.metadata["persistence.entry_id"]
    testbase.assert_equal(storage.entries[0].entry_id, entry_id)
    testbase.assert_equal(storage.entries[0].command, ["ls", "-l"])
    testbase.assert_equal(storage.entries[0].cwd, "/tmp")
    testbase.assert_equal(storage.entries[0].env, None)
    return


def test_after_execute_updates_entry():
    """
        after_execute must update the previously logged entry with the result.
    """
    storage = _InMemoryStorage()
    aspect = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
    context = _make_context()
    aspect.before_execute(context)
    result = RunResult(
        exit_code=0, stdout="ok", stderr="", command=["ls"], duration_ms=12.0
    )
    aspect.after_execute(context, result)
    entry_id = context.metadata["persistence.entry_id"]
    testbase.assert_true(entry_id in storage.updates)
    testbase.assert_equal(storage.updates[entry_id].stdout, "ok")
    return


def test_fail_on_storage_error_propagates_on_log():
    """
        With fail_on_storage_error=True, a storage failure aborts before_execute.
    """
    storage = _InMemoryStorage(raise_on_log=True)
    aspect = PersistenceAspect(PersistenceAspectConfig(
        storage_backend=storage, fail_on_storage_error=True,
    ))
    raised = False
    try:
        aspect.before_execute(_make_context())
    except RuntimeError:
        raised = True
    testbase.assert_true(raised)
    return


def test_storage_error_swallowed_by_default():
    """
        Without fail_on_storage_error, a storage failure is logged but not raised.
    """
    storage = _InMemoryStorage(raise_on_log=True)
    aspect = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
    context = _make_context()
    aspect.before_execute(context)
    testbase.assert_true("persistence.entry_id" in context.metadata)
    return


def test_env_logged_when_enabled():
    """
        When log_environment is enabled, the recorded entry must carry env vars.
    """
    storage = _InMemoryStorage()
    aspect = PersistenceAspect(PersistenceAspectConfig(
        storage_backend=storage, log_environment=True,
    ))
    context = _make_context(env={"A": "1"})
    aspect.before_execute(context)
    testbase.assert_equal(storage.entries[0].env, {"A": "1"})
    return


def test_log_output_false_strips_stdout_stderr():
    """
        When log_output is disabled, the updated entry must not contain stdout/stderr.
    """
    storage = _InMemoryStorage()
    aspect = PersistenceAspect(PersistenceAspectConfig(
        storage_backend=storage, log_output=False,
    ))
    context = _make_context()
    aspect.before_execute(context)
    result = RunResult(
        exit_code=0, stdout="secret", stderr="also secret", command=["x"]
    )
    aspect.after_execute(context, result)
    entry_id = context.metadata["persistence.entry_id"]
    testbase.assert_equal(storage.updates[entry_id].stdout, "")
    testbase.assert_equal(storage.updates[entry_id].stderr, "")
    return
