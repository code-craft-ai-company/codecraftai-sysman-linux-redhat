# ccai.sysman.linux.redhat.backuprestore
#
# BackupRestoreManager wraps tar/rsync-style backup and restore operations
# through the ICommandRunner abstraction.

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ccai.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


BackupActionStatus = ActionStatus


class Compression(Enum):
    """
        Supported compression formats for tar archives.
    """
    NONE = "none"
    GZIP = "gzip"
    BZIP2 = "bzip2"
    XZ = "xz"


@dataclass
class BackupManagerConfig(FeatureConfig):
    """
        Configuration for :class:`BackupRestoreManager`.  Inherits
        ``dry_run`` and ``elevated`` from :class:`FeatureConfig`.
    """
    pass


@dataclass
class BackupSpec:
    """
        Specification for a tar-based backup.
    """
    source_paths: list[str]
    archive_path: str
    compression: Compression = Compression.GZIP
    exclude_patterns: list[str] = field(default_factory=list)


@dataclass
class BackupActionResult(FeatureActionResult):
    """
        Outcome of a backup or restore action.  Adds ``archive_path``.
    """
    archive_path: Optional[str] = None


class BackupRestoreManager(BaseFeatureManager):
    """
        Facade for backup and restore operations.
    """

    result_class = BackupActionResult
    feature_name = "backup-restore"

    def create_archive(self, spec: BackupSpec) -> BackupActionResult:
        """
            Create a tar archive described by ``spec``.
        """
        compression_flag = _compression_flag(compression=spec.compression)
        command = ["tar"]
        if compression_flag is not None:
            command.append(compression_flag)
        command.extend(["-cf", spec.archive_path])
        for pattern in spec.exclude_patterns:
            command.extend(["--exclude", pattern])
        command.extend(spec.source_paths)
        return self._dispatch(command=command, archive_path=spec.archive_path)

    def extract_archive(
        self,
        archive_path: str,
        destination: str,
    ) -> BackupActionResult:
        """
            Extract ``archive_path`` into ``destination``.
        """
        command = ["tar", "-xf", archive_path, "-C", destination]
        return self._dispatch(command=command, archive_path=archive_path)

    def sync_paths(
        self,
        source: str,
        destination: str,
        *,
        delete: bool = False,
    ) -> BackupActionResult:
        """
            Synchronise ``source`` to ``destination`` using rsync.
        """
        command = ["rsync", "-a"]
        if delete:
            command.append("--delete")
        command.extend([source, destination])
        return self._dispatch(command=command, archive_path=destination)

    def _dispatch(
        self,
        *,
        command: list[str],
        archive_path: str,
    ) -> BackupActionResult:
        """
            Dispatch a backup-related command honouring dry-run.
        """
        if self._config.dry_run:
            return BackupActionResult(
                status=BackupActionStatus.DRY_RUN,
                archive_path=archive_path, command=command,
            )
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = BackupActionStatus.SUCCESS if run_result.exit_code == 0 else BackupActionStatus.FAILED
        return BackupActionResult(
            status=status,
            archive_path=archive_path,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )


def _compression_flag(*, compression: Compression) -> Optional[str]:
    """
        Return the tar flag for the requested compression.
    """
    mapping = {
        Compression.NONE: None,
        Compression.GZIP: "-z",
        Compression.BZIP2: "-j",
        Compression.XZ: "-J",
    }
    return mapping[compression]
