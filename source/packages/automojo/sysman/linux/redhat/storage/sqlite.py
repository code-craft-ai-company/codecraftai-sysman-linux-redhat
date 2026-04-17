# automojo.sysman.linux.redhat.storage.sqlite
#
# SQLite-backed implementation of ICommandStorage.

import json
import sqlite3
import threading
import time

from typing import Optional

from automojo.sysman.linux.redhat.runners.models import RunResult
from automojo.sysman.linux.redhat.storage.base import CommandLogEntry


_SCHEMA = """
CREATE TABLE IF NOT EXISTS command_log (
    entry_id TEXT PRIMARY KEY,
    command TEXT NOT NULL,
    cwd TEXT,
    env TEXT,
    timestamp_start REAL NOT NULL,
    timestamp_end REAL,
    transport TEXT NOT NULL,
    elevated INTEGER NOT NULL,
    exit_code INTEGER,
    stdout TEXT,
    stderr TEXT,
    duration_ms REAL,
    timed_out INTEGER NOT NULL DEFAULT 0,
    dry_run INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0
);
"""


class SQLiteStorage:
    """
        SQLite-based command log storage.  A single connection is held per
        instance and serialised with an internal lock so callers may share a
        storage instance across threads.
    """

    def __init__(self, database_path: str) -> None:
        """
            :param database_path: Filesystem path to the SQLite database.
        """
        self._database_path: str = database_path
        self._connection_lock: threading.Lock = threading.Lock()
        self._connection: sqlite3.Connection = sqlite3.connect(
            database_path,
            check_same_thread=False,
        )
        self._initialize_schema()
        return

    @property
    def database_path(self) -> str:
        """
            Filesystem path to the SQLite database file.
        """
        return self._database_path

    def close(self) -> None:
        """
            Close the underlying SQLite connection.
        """
        with self._connection_lock:
            self._connection.close()
        return

    def log_command(self, entry: CommandLogEntry) -> str:
        """
            Insert a new command log entry.

            :param entry: The entry to persist.

            :returns: The entry identifier used in the database.
        """
        command_json = json.dumps(entry.command)
        env_json: Optional[str] = None
        if entry.env is not None:
            env_json = json.dumps(entry.env)

        with self._connection_lock:
            cursor = self._connection.cursor()
            cursor.execute(
                """
                INSERT INTO command_log (
                    entry_id, command, cwd, env, timestamp_start, timestamp_end,
                    transport, elevated, exit_code, stdout, stderr, duration_ms,
                    timed_out, dry_run, retry_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.entry_id,
                    command_json,
                    entry.cwd,
                    env_json,
                    entry.timestamp_start,
                    entry.timestamp_end,
                    entry.transport,
                    1 if entry.elevated else 0,
                    entry.exit_code,
                    entry.stdout,
                    entry.stderr,
                    entry.duration_ms,
                    1 if entry.timed_out else 0,
                    1 if entry.dry_run else 0,
                    entry.retry_count,
                ),
            )
            self._connection.commit()

        return entry.entry_id

    def update_command(self, entry_id: str, result: RunResult) -> None:
        """
            Update an existing log entry with the execution result.
        """
        timestamp_end = time.time()
        with self._connection_lock:
            cursor = self._connection.cursor()
            cursor.execute(
                """
                UPDATE command_log SET
                    timestamp_end = ?,
                    exit_code = ?,
                    stdout = ?,
                    stderr = ?,
                    duration_ms = ?,
                    timed_out = ?,
                    dry_run = ?,
                    retry_count = ?
                WHERE entry_id = ?
                """,
                (
                    timestamp_end,
                    result.exit_code,
                    result.stdout,
                    result.stderr,
                    result.duration_ms,
                    1 if result.timed_out else 0,
                    1 if result.dry_run else 0,
                    result.retry_count,
                    entry_id,
                ),
            )
            self._connection.commit()
        return

    def _initialize_schema(self) -> None:
        """
            Create the command log table if it does not already exist.
        """
        with self._connection_lock:
            cursor = self._connection.cursor()
            cursor.executescript(_SCHEMA)
            self._connection.commit()
        return
