# ccai.sysman.linux.redhat.storage.json_store
#
# JSON-file-backed implementation of ICommandStorage.  Entries are serialised
# as newline-delimited JSON objects to simplify appending and line-level
# updates.

import json
import os
import threading
import time

from dataclasses import asdict

from ccai.sysman.linux.redhat.runners.models import RunResult
from ccai.sysman.linux.redhat.storage.base import CommandLogEntry


class JSONStorage:
    """
        File-based JSON storage that writes one entry per line.  Read and
        write access is serialised with an internal lock to protect against
        concurrent access within a single process.
    """

    def __init__(self, file_path: str) -> None:
        """
            :param file_path: Path to the JSON-lines file used for persistence.
        """
        self._file_path: str = file_path
        self._file_lock: threading.Lock = threading.Lock()
        self._ensure_file()
        return

    @property
    def file_path(self) -> str:
        """
            Filesystem path to the storage file.
        """
        return self._file_path

    def log_command(self, entry: CommandLogEntry) -> str:
        """
            Append a new JSON record representing the pending command.
        """
        record = self._entry_to_record(entry)
        encoded = json.dumps(record)

        with self._file_lock:
            with open(self._file_path, "a", encoding="utf-8") as outfile:
                outfile.write(encoded)
                outfile.write(os.linesep)

        return entry.entry_id

    def update_command(self, entry_id: str, result: RunResult) -> None:
        """
            Rewrite the file with an updated record for the given entry id.
        """
        updated_at = time.time()

        with self._file_lock:
            rewritten_records: list[str] = []
            with open(self._file_path, "r", encoding="utf-8") as infile:
                for line in infile:
                    stripped = line.strip()
                    if len(stripped) == 0:
                        continue
                    record = json.loads(stripped)
                    if record.get("entry_id") == entry_id:
                        record["timestamp_end"] = updated_at
                        record["exit_code"] = result.exit_code
                        record["stdout"] = result.stdout
                        record["stderr"] = result.stderr
                        record["duration_ms"] = result.duration_ms
                        record["timed_out"] = result.timed_out
                        record["dry_run"] = result.dry_run
                        record["retry_count"] = result.retry_count
                    rewritten_records.append(json.dumps(record))

            with open(self._file_path, "w", encoding="utf-8") as outfile:
                for rec in rewritten_records:
                    outfile.write(rec)
                    outfile.write(os.linesep)

        return

    def read_entries(self) -> list[dict]:
        """
            Read all currently persisted entries as dictionaries.  Primarily
            useful for tests and audit tooling.

            :returns: List of records in the order they were persisted.
        """
        entries: list[dict] = []
        with self._file_lock:
            with open(self._file_path, "r", encoding="utf-8") as infile:
                for line in infile:
                    stripped = line.strip()
                    if len(stripped) == 0:
                        continue
                    entries.append(json.loads(stripped))
        return entries

    def _entry_to_record(self, entry: CommandLogEntry) -> dict:
        """
            Convert a :class:`CommandLogEntry` into a JSON-serialisable dict.
        """
        record = asdict(entry)
        return record

    def _ensure_file(self) -> None:
        """
            Ensure that the target file exists so future append operations
            succeed without special-casing creation.
        """
        parent = os.path.dirname(self._file_path)
        if parent is not None and len(parent) > 0:
            if not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)
        if not os.path.exists(self._file_path):
            with open(self._file_path, "w", encoding="utf-8") as outfile:
                outfile.write("")
        return
