# ccai.sysman.linux.redhat.filesystemmanagement
#
# FilesystemManager provides directory, symlink, mount, and fstab operations
# through the ICommandRunner abstraction.

import json

from dataclasses import dataclass
from typing import Optional

from ccai.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureConfig,
)
from ccai.sysman.linux.redhat.runners.models import RunResult


# Backwards-compatible alias.
FilesystemActionStatus = ActionStatus


@dataclass
class FilesystemManagerConfig(FeatureConfig):
    """
        Configuration for :class:`FilesystemManager`.  Inherits
        ``dry_run`` and ``elevated`` from :class:`FeatureConfig`.

        :param fstab_path: Filesystem path to the fstab file.
    """
    fstab_path: str = "/etc/fstab"


@dataclass
class MountSpec:
    """
        Specification for a mount invocation.

        :param device: Source device or URL.
        :param mountpoint: Target mount directory.
        :param fstype: Filesystem type (optional).
        :param options: Mount options (optional).
    """
    device: str
    mountpoint: str
    fstype: Optional[str] = None
    options: Optional[str] = None


@dataclass
class FstabEntry:
    """
        Representation of a single fstab entry.

        :param device: Device field.
        :param mountpoint: Mountpoint field.
        :param fstype: Filesystem type.
        :param options: Options field.
        :param dump: Dump frequency (default 0).
        :param passno: Pass number for fsck (default 0).
    """
    device: str
    mountpoint: str
    fstype: str
    options: str = "defaults"
    dump: int = 0
    passno: int = 0


@dataclass
class FilesystemActionResult:
    """
        Outcome of a mutating filesystem operation.
    """
    status: FilesystemActionStatus
    path: str
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None
    detail: Optional[str] = None


@dataclass
class PathInfo:
    """
        Stat information for a filesystem path.
    """
    path: str
    path_type: str
    mode: str
    owner: str
    group: str
    size_bytes: int


@dataclass
class DiskUsageInfo:
    """
        Disk usage statistics for a mountpoint.
    """
    path: str
    total_bytes: int
    used_bytes: int
    available_bytes: int
    use_percent: int


@dataclass
class MountInfo:
    """
        Information about a currently mounted filesystem.
    """
    device: str
    mountpoint: str
    fstype: str
    options: str


class FilesystemManager(BaseFeatureManager):
    """
        Facade for filesystem management operations through the command
        runner.  Uses its own :class:`FilesystemActionResult` shape
        (``path`` is a required field) so does not leverage the base
        class's ``_dispatch`` helper; inherits only the standard
        ``__init__`` / ``config`` / ``runner`` surface.
    """

    feature_name = "filesystem-management"

    def create_directory(
        self,
        path: str,
        *,
        mode: Optional[str] = None,
        owner: Optional[str] = None,
        group: Optional[str] = None,
    ) -> FilesystemActionResult:
        """
            Create a directory with optional permissions and ownership.
        """
        command = ["mkdir", "-p", path]
        if self._config.dry_run:
            dry = FilesystemActionResult(
                status=FilesystemActionStatus.DRY_RUN,
                path=path,
                command=command,
            )
            return dry

        run_result = self._runner.run(command, elevated=self._config.elevated)
        if run_result.exit_code != 0:
            return _failed(path=path, run_result=run_result)

        if mode is not None:
            self._runner.run(
                ["chmod", mode, path], elevated=self._config.elevated,
            )
        if owner is not None or group is not None:
            spec = _owner_spec(owner=owner, group=group)
            self._runner.run(
                ["chown", spec, path], elevated=self._config.elevated,
            )

        success = FilesystemActionResult(
            status=FilesystemActionStatus.SUCCESS,
            path=path,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
        return success

    def remove_directory(
        self,
        path: str,
        *,
        recursive: bool = False,
    ) -> FilesystemActionResult:
        """
            Remove a directory.  Uses ``rmdir`` unless ``recursive=True``.
        """
        if recursive:
            command = ["rm", "-rf", path]
        else:
            command = ["rmdir", path]
        return self._mutate(command=command, path=path)

    def create_symlink(self, link: str, target: str) -> FilesystemActionResult:
        """
            Create a symlink at ``link`` pointing at ``target``.
        """
        existing = self._read_symlink(link=link)
        if existing is not None and existing == target:
            skipped = FilesystemActionResult(
                status=FilesystemActionStatus.SKIPPED,
                path=link,
            )
            return skipped

        command = ["ln", "-s", target, link]
        return self._mutate(command=command, path=link)

    def remove_symlink(self, link: str) -> FilesystemActionResult:
        """
            Remove a symlink using ``unlink``.
        """
        info = self.query_path(link)
        if info is None:
            skipped = FilesystemActionResult(
                status=FilesystemActionStatus.SKIPPED,
                path=link,
            )
            return skipped
        command = ["unlink", link]
        return self._mutate(command=command, path=link)

    def query_path(self, path: str) -> Optional[PathInfo]:
        """
            Return :class:`PathInfo` for an existing path or None.
        """
        command = ["stat", "-c", "%F|%a|%U|%G|%s", path]
        run_result = self._runner.run(command, elevated=False)
        if run_result.exit_code != 0:
            return None
        info = _parse_stat_output(stdout=run_result.stdout, path=path)
        return info

    def query_disk_usage(self, path: str) -> DiskUsageInfo:
        """
            Return :class:`DiskUsageInfo` for ``path`` using ``df -B1``.
        """
        command = [
            "df", "-B1", "--output=size,used,avail,pcent", path,
        ]
        run_result = self._runner.run(command, elevated=False)
        info = _parse_df_output(stdout=run_result.stdout, path=path)
        return info

    def mount(self, spec: MountSpec) -> FilesystemActionResult:
        """
            Mount a filesystem using the given :class:`MountSpec`.
        """
        if self._is_mounted(mountpoint=spec.mountpoint):
            skipped = FilesystemActionResult(
                status=FilesystemActionStatus.SKIPPED,
                path=spec.mountpoint,
            )
            return skipped

        command = ["mount"]
        if spec.fstype is not None:
            command.extend(["-t", spec.fstype])
        if spec.options is not None:
            command.extend(["-o", spec.options])
        command.extend([spec.device, spec.mountpoint])
        return self._mutate(command=command, path=spec.mountpoint)

    def unmount(
        self,
        mountpoint: str,
        *,
        lazy: bool = False,
    ) -> FilesystemActionResult:
        """
            Unmount a filesystem.  When ``lazy=True`` passes the ``-l`` flag.
        """
        if not self._is_mounted(mountpoint=mountpoint):
            skipped = FilesystemActionResult(
                status=FilesystemActionStatus.SKIPPED,
                path=mountpoint,
            )
            return skipped

        command = ["umount"]
        if lazy:
            command.append("-l")
        command.append(mountpoint)
        return self._mutate(command=command, path=mountpoint)

    def list_mounts(self) -> list[MountInfo]:
        """
            List all active mounts via ``findmnt --json``.
        """
        command = ["findmnt", "--json"]
        run_result = self._runner.run(command, elevated=False)
        if run_result.exit_code != 0:
            return []
        mounts = _parse_findmnt(stdout=run_result.stdout)
        return mounts

    def add_fstab_entry(self, entry: FstabEntry) -> FilesystemActionResult:
        """
            Append an fstab entry.  Skipped when an entry already matches.
        """
        current = self._read_fstab()
        for line in current.splitlines():
            stripped = line.strip()
            if len(stripped) == 0 or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if len(parts) < 2:
                continue
            if parts[0] == entry.device and parts[1] == entry.mountpoint:
                skipped = FilesystemActionResult(
                    status=FilesystemActionStatus.SKIPPED,
                    path=entry.mountpoint,
                    detail="entry already present",
                )
                return skipped

        new_content = current
        if not new_content.endswith("\n") and len(new_content) > 0:
            new_content = new_content + "\n"
        new_content = new_content + "{}\t{}\t{}\t{}\t{}\t{}\n".format(
            entry.device, entry.mountpoint, entry.fstype, entry.options,
            entry.dump, entry.passno,
        )
        return self._write_fstab(content=new_content, path=entry.mountpoint)

    def remove_fstab_entry(
        self,
        device_or_mountpoint: str,
    ) -> FilesystemActionResult:
        """
            Remove the fstab line matching ``device_or_mountpoint``.
        """
        current = self._read_fstab()
        kept_lines: list[str] = []
        removed = False
        for line in current.splitlines():
            stripped = line.strip()
            if len(stripped) == 0 or stripped.startswith("#"):
                kept_lines.append(line)
                continue
            parts = stripped.split()
            if len(parts) >= 2:
                if parts[0] == device_or_mountpoint or parts[1] == device_or_mountpoint:
                    removed = True
                    continue
            kept_lines.append(line)

        if not removed:
            skipped = FilesystemActionResult(
                status=FilesystemActionStatus.SKIPPED,
                path=device_or_mountpoint,
                detail="entry not present",
            )
            return skipped

        new_content = "\n".join(kept_lines)
        if len(new_content) > 0 and not new_content.endswith("\n"):
            new_content = new_content + "\n"
        return self._write_fstab(
            content=new_content, path=device_or_mountpoint,
        )

    def _read_symlink(self, *, link: str) -> Optional[str]:
        """
            Return the existing symlink target or None.
        """
        run_result = self._runner.run(["readlink", link], elevated=False)
        if run_result.exit_code != 0:
            return None
        return run_result.stdout.strip()

    def _is_mounted(self, *, mountpoint: str) -> bool:
        """
            Return True when ``mountpoint`` appears in ``findmnt --json``.
        """
        mounts = self.list_mounts()
        for mount in mounts:
            if mount.mountpoint == mountpoint:
                return True
        return False

    def _read_fstab(self) -> str:
        """
            Read the fstab file via ``cat``.
        """
        run_result = self._runner.run(
            ["cat", self._config.fstab_path], elevated=self._config.elevated,
        )
        if run_result.exit_code != 0:
            return ""
        return run_result.stdout

    def _write_fstab(
        self,
        *,
        content: str,
        path: str,
    ) -> FilesystemActionResult:
        """
            Write new fstab content using ``tee``.
        """
        command = ["tee", self._config.fstab_path]
        if self._config.dry_run:
            dry = FilesystemActionResult(
                status=FilesystemActionStatus.DRY_RUN,
                path=path,
                command=command,
            )
            return dry
        run_result = self._runner.run(
            command,
            elevated=self._config.elevated,
            stdin=content,
        )
        status = (
            FilesystemActionStatus.SUCCESS if run_result.exit_code == 0
            else FilesystemActionStatus.FAILED
        )
        result = FilesystemActionResult(
            status=status,
            path=path,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
        return result

    def _mutate(
        self,
        *,
        command: list[str],
        path: str,
    ) -> FilesystemActionResult:
        """
            Dispatch a mutating command honouring dry-run mode.
        """
        if self._config.dry_run:
            dry = FilesystemActionResult(
                status=FilesystemActionStatus.DRY_RUN,
                path=path,
                command=command,
            )
            return dry
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = (
            FilesystemActionStatus.SUCCESS if run_result.exit_code == 0
            else FilesystemActionStatus.FAILED
        )
        result = FilesystemActionResult(
            status=status,
            path=path,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
        return result


def _owner_spec(*, owner: Optional[str], group: Optional[str]) -> str:
    """
        Build chown ``user:group`` spec, permitting partial assignment.
    """
    if owner is not None and group is not None:
        return "{}:{}".format(owner, group)
    if owner is not None:
        return owner
    return ":{}".format(group)


def _failed(
    *,
    path: str,
    run_result: RunResult,
) -> FilesystemActionResult:
    """
        Build a FAILED FilesystemActionResult from a RunResult.
    """
    result = FilesystemActionResult(
        status=FilesystemActionStatus.FAILED,
        path=path,
        exit_code=run_result.exit_code,
        stdout=run_result.stdout,
        stderr=run_result.stderr,
        command=list(run_result.command),
    )
    return result


def _parse_stat_output(*, stdout: str, path: str) -> Optional[PathInfo]:
    """
        Parse ``stat -c "%F|%a|%U|%G|%s"`` output into a :class:`PathInfo`.
    """
    line = stdout.strip()
    if len(line) == 0:
        return None
    fields = line.split("|")
    if len(fields) < 5:
        return None
    try:
        size_value = int(fields[4])
    except ValueError:
        size_value = 0
    info = PathInfo(
        path=path,
        path_type=fields[0],
        mode=fields[1],
        owner=fields[2],
        group=fields[3],
        size_bytes=size_value,
    )
    return info


def _parse_df_output(*, stdout: str, path: str) -> DiskUsageInfo:
    """
        Parse ``df -B1 --output=size,used,avail,pcent`` output.
    """
    total = 0
    used = 0
    avail = 0
    percent = 0
    lines = stdout.strip().splitlines()
    if len(lines) >= 2:
        parts = lines[1].split()
        if len(parts) >= 4:
            try:
                total = int(parts[0])
                used = int(parts[1])
                avail = int(parts[2])
                pct_token = parts[3].rstrip("%")
                percent = int(pct_token)
            except ValueError:
                # Best-effort parse: df reports '-' for columns it cannot compute (e.g. pseudo
                # filesystems), so non-numeric columns leave the zero defaults in place.
                pass
    info = DiskUsageInfo(
        path=path,
        total_bytes=total,
        used_bytes=used,
        available_bytes=avail,
        use_percent=percent,
    )
    return info


def _parse_findmnt(*, stdout: str) -> list[MountInfo]:
    """
        Parse ``findmnt --json`` output into a list of :class:`MountInfo`.
    """
    mounts: list[MountInfo] = []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return mounts
    filesystems = data.get("filesystems", [])
    stack: list[dict] = list(filesystems)
    while len(stack) > 0:
        entry = stack.pop()
        mount = MountInfo(
            device=entry.get("source", ""),
            mountpoint=entry.get("target", ""),
            fstype=entry.get("fstype", ""),
            options=entry.get("options", ""),
        )
        mounts.append(mount)
        children = entry.get("children", [])
        for child in children:
            stack.append(child)
    return mounts
