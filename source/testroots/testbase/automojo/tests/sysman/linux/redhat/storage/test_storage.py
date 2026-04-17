# Tests for the storage backends.

import os
import sqlite3
import tempfile
import uuid

from automojo import testbase

from automojo.sysman.linux.redhat.runners.models import RunResult
from automojo.sysman.linux.redhat.storage.base import CommandLogEntry
from automojo.sysman.linux.redhat.storage.json_store import JSONStorage
from automojo.sysman.linux.redhat.storage.sqlite import SQLiteStorage


def _make_entry(entry_id: str) -> CommandLogEntry:
    """
        Build a canonical CommandLogEntry for use across storage tests.
    """
    entry = CommandLogEntry(
        entry_id=entry_id,
        command=["ls", "-l"],
        cwd="/tmp",
        env={"A": "1"},
        timestamp_start=1700000000.0,
        transport="local",
        elevated=False,
    )
    return entry


def test_sqlite_log_and_update():
    """
        SQLiteStorage should insert and later update a log entry.
    """
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "cmdlog.db")
    storage = SQLiteStorage(db_path)
    try:
        entry_id = str(uuid.uuid4())
        returned = storage.log_command(_make_entry(entry_id))
        testbase.assert_equal(returned, entry_id)

        storage.update_command(
            entry_id,
            RunResult(
                exit_code=0, stdout="ok", stderr="", command=["x"], duration_ms=5.0,
            ),
        )

        conn = sqlite3.connect(db_path)
        rows = list(conn.execute(
            "SELECT exit_code, stdout FROM command_log WHERE entry_id = ?",
            (entry_id,),
        ))
        conn.close()
        testbase.assert_equal(len(rows), 1)
        testbase.assert_equal(rows[0], (0, "ok"))
    finally:
        storage.close()

    return


def test_json_log_and_update():
    """
        JSONStorage should append an entry and rewrite it with result fields.
    """
    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, "cmdlog.jsonl")
    storage = JSONStorage(path)

    entry_id = "abc"
    storage.log_command(_make_entry(entry_id))
    storage.update_command(
        entry_id,
        RunResult(
            exit_code=5, stdout="out", stderr="err", command=["x"], duration_ms=7.0,
        ),
    )
    entries = storage.read_entries()
    testbase.assert_equal(len(entries), 1)
    testbase.assert_equal(entries[0]["entry_id"], entry_id)
    testbase.assert_equal(entries[0]["exit_code"], 5)
    testbase.assert_equal(entries[0]["stdout"], "out")
    testbase.assert_equal(entries[0]["duration_ms"], 7.0)
    return


def test_json_multiple_entries():
    """
        JSONStorage should preserve the order of multiple logged entries.
    """
    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, "cmdlog.jsonl")
    storage = JSONStorage(path)

    for idx in range(3):
        storage.log_command(_make_entry("id-{}".format(idx)))
    entries = storage.read_entries()
    testbase.assert_equal(len(entries), 3)
    return
