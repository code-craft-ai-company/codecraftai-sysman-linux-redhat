# automojo.sysman.linux.redhat.storage
#
# Storage backends used by aspects that persist command-execution data.

from automojo.sysman.linux.redhat.storage.base import CommandLogEntry, ICommandStorage
from automojo.sysman.linux.redhat.storage.json_store import JSONStorage
from automojo.sysman.linux.redhat.storage.sqlite import SQLiteStorage

__all__ = [
    "CommandLogEntry",
    "ICommandStorage",
    "JSONStorage",
    "SQLiteStorage",
]
