# ccai.sysman.linux.redhat.filesystemquotas
#
# FilesystemQuotaManager wraps the Linux quota family (`quota`,
# `repquota`, `setquota`, `quotaon`, `quotaoff`, `quotacheck`) and
# the XFS-specific `xfs_quota` tool through the ICommandRunner
# abstraction.  Auto-dispatches between ext-family and XFS based on
# the filesystem type detected via `findmnt`.
#
# Also reads and writes ``/etc/projects`` / ``/etc/projid`` so XFS
# project quotas can be defined declaratively.

import os

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from ccai.errors.exceptions import SemanticError

from ccai.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


# ----------------------------------------------------------------------
# Module-level command constants
# ----------------------------------------------------------------------

_FINDMNT = "findmnt"
_QUOTA = "quota"
_QUOTAON = "quotaon"
_QUOTAOFF = "quotaoff"
_QUOTACHECK = "quotacheck"
_REPQUOTA = "repquota"
_SETQUOTA = "setquota"
_XFS_QUOTA = "xfs_quota"

_XFS_FSTYPE = "xfs"


QuotaActionStatus = ActionStatus


# ----------------------------------------------------------------------
# Enums
# ----------------------------------------------------------------------


class QuotaTarget(Enum):
    """
        Identifies the quota-target dimension for an operation.
    """
    USER = "user"
    GROUP = "group"
    PROJECT = "project"


# ----------------------------------------------------------------------
# Configuration / result models
# ----------------------------------------------------------------------


@dataclass
class FilesystemQuotaManagerConfig(FeatureConfig):
    """
        Configuration for :class:`FilesystemQuotaManager`.  Inherits
        ``dry_run`` and ``elevated`` from :class:`FeatureConfig`.

        :param projects_file: Filesystem path to the XFS project-id
                              path-binding file.
        :param projid_file:   Filesystem path to the XFS project-id
                              name-binding file.
    """
    projects_file: str = "/etc/projects"
    projid_file: str = "/etc/projid"


@dataclass
class FilesystemQuotaActionResult(FeatureActionResult):
    """
        Outcome of a filesystem-quotas mutation.  Adds the
        ``mount`` field on top of :class:`FeatureActionResult` so
        callers can correlate the result with the mountpoint that
        was acted upon.
    """
    mount: Optional[str] = None


# ----------------------------------------------------------------------
# Parsed-data models
# ----------------------------------------------------------------------


@dataclass
class QuotaLimits:
    """
        Soft / hard block / inode limits for a quota record.

        ``block_*`` are in 1-KiB blocks for ext-family filesystems
        (the unit ``setquota`` accepts on the command line) and in
        512-byte blocks for XFS (the unit ``xfs_quota report``
        emits).  Callers that want to interoperate with the
        higher-level cross-OS contract are responsible for any
        bytes-vs-blocks conversion at the layer above.

        A value of ``0`` means "no limit" (the underlying tool's
        own convention).
    """
    block_soft: int
    block_hard: int
    inode_soft: int
    inode_hard: int


@dataclass
class QuotaEntry:
    """
        Per-target quota record on a single mountpoint.

        :param mount: Filesystem mountpoint the record applies to.
        :param target: USER / GROUP / PROJECT.
        :param name: Username, group name, or project name.
        :param blocks_used: Current block usage in the tool's
                            native block unit.
        :param block_soft: Soft block limit.
        :param block_hard: Hard block limit.
        :param inodes_used: Current inode usage.
        :param inode_soft: Soft inode limit.
        :param inode_hard: Hard inode limit.
        :param grace_blocks: Native grace-period token for block
                             overage (``"none"``, ``"7days"``,
                             ``"6:23"``, ...) or ``None``.
        :param grace_inodes: Native grace-period token for inode
                             overage or ``None``.
    """
    mount: str
    target: QuotaTarget
    name: str
    blocks_used: int
    block_soft: int
    block_hard: int
    inodes_used: int
    inode_soft: int
    inode_hard: int
    grace_blocks: Optional[str] = None
    grace_inodes: Optional[str] = None


@dataclass
class QuotaEnforcement:
    """
        Mountpoint-wide enforcement posture.

        :param mount: Filesystem mountpoint.
        :param fstype: Detected filesystem type (``"ext4"``,
                       ``"xfs"``, ...).
        :param user_enforced: True when user-quota enforcement is
                              active.
        :param group_enforced: True when group-quota enforcement is
                               active.
        :param project_enforced: True when project-quota
                                 enforcement is active.  Always
                                 False on filesystems that do not
                                 support project quotas.
    """
    mount: str
    fstype: str
    user_enforced: bool
    group_enforced: bool
    project_enforced: bool


@dataclass
class GracePeriod:
    """
        Per-(mount, target) grace-period record.

        :param mount: Filesystem mountpoint.
        :param target: USER / GROUP / PROJECT.
        :param block_seconds: Grace seconds for block overage.
        :param inode_seconds: Grace seconds for inode overage.
    """
    mount: str
    target: QuotaTarget
    block_seconds: int
    inode_seconds: int


@dataclass
class ProjectDefinition:
    """
        XFS project definition merged from ``/etc/projects`` (id ->
        path) and ``/etc/projid`` (name -> id).

        :param project_id: Numeric project identifier.
        :param project_name: Symbolic project name.
        :param paths: One entry per ``/etc/projects`` line bound to
                      this id.
    """
    project_id: int
    project_name: str
    paths: list[str]


# ----------------------------------------------------------------------
# Exceptions
# ----------------------------------------------------------------------


class QuotaQueryError(Exception):
    """
        Raised when a read-side quota command fails for a reason
        other than the principal simply being absent.
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

        err_msg_lines = ["Quota query command failed."]
        err_msg_lines.append("    COMMAND: {}".format(" ".join(command)))
        err_msg_lines.append("    EXIT_CODE: {}".format(exit_code))
        err_msg_lines.append("    STDERR: {}".format(stderr))
        err_msg = os.linesep.join(err_msg_lines)
        super().__init__(err_msg)
        return


class QuotaFilesystemNotMountedError(SemanticError):
    """
        Raised when a quota verb is invoked against a path that is
        not currently a mountpoint (per ``findmnt``).
    """

    def __init__(
        self,
        *,
        mount: str,
        command: list[str],
        exit_code: Optional[int],
        stderr: str,
    ) -> None:
        """
            :param mount: The mountpoint that failed validation.
            :param command: The ``findmnt`` argv that failed.
            :param exit_code: The runner's exit code.
            :param stderr: The runner's captured stderr.
        """
        self.mount = mount
        self.command = list(command)
        self.exit_code = exit_code
        self.stderr = stderr

        err_msg_lines = [
            "Filesystem-quotas operation requires a mounted target.",
        ]
        err_msg_lines.append("    MOUNT: {}".format(mount))
        err_msg_lines.append("    PROBE: {}".format(" ".join(command)))
        err_msg_lines.append("    EXIT_CODE: {}".format(exit_code))
        err_msg_lines.append("    STDERR: {}".format(stderr))
        err_msg = os.linesep.join(err_msg_lines)
        super().__init__(err_msg)
        return


class QuotaUnsupportedOperationError(SemanticError):
    """
        Raised when a verb is invoked against a filesystem that
        does not support it (for example ``quotacheck`` on XFS or
        project-quota plumbing on an ext-family mount).
    """

    def __init__(
        self,
        *,
        operation: str,
        fstype: str,
        mount: str,
    ) -> None:
        """
            :param operation: Operation name (e.g. ``"quotacheck"``).
            :param fstype: The detected filesystem type.
            :param mount: The mountpoint.
        """
        self.operation = operation
        self.fstype = fstype
        self.mount = mount

        err_msg_lines = [
            "Filesystem-quotas operation not supported on this filesystem.",
        ]
        err_msg_lines.append("    OPERATION: {}".format(operation))
        err_msg_lines.append("    FSTYPE: {}".format(fstype))
        err_msg_lines.append("    MOUNT: {}".format(mount))
        err_msg = os.linesep.join(err_msg_lines)
        super().__init__(err_msg)
        return


# ----------------------------------------------------------------------
# Manager
# ----------------------------------------------------------------------


class FilesystemQuotaManager(BaseFeatureManager):
    """
        Facade for filesystem-quota management on RHEL.  Wraps
        the standard Linux quota family and XFS's ``xfs_quota``
        through the injected :class:`ICommandRunner`.
    """

    result_class = FilesystemQuotaActionResult
    status_class = QuotaActionStatus
    feature_name = "filesystem-quotas"

    # ------------------------------------------------------------------
    # Enforcement-state verbs
    # ------------------------------------------------------------------

    def query_enforcement(
        self,
        mount: str,
    ) -> QuotaEnforcement:
        """
            Return the current quota-enforcement posture for
            ``mount``.

            :param mount: Filesystem mountpoint.

            :returns: A :class:`QuotaEnforcement` record.

            :raises QuotaFilesystemNotMountedError: when ``mount``
                is not currently a mountpoint.
            :raises QuotaQueryError: when the underlying tool
                returns non-zero.
        """
        fstype = self._fstype_of(mount=mount)

        if fstype == _XFS_FSTYPE:
            command = [_XFS_QUOTA, "-x", "-c", "state", mount]
            run_result = self._runner.run(list(command), elevated=False)
            if run_result.exit_code != 0:
                raise QuotaQueryError(
                    command=command,
                    exit_code=run_result.exit_code,
                    stderr=run_result.stderr,
                )
            user_on, group_on, project_on = _parse_xfs_state(
                stdout=run_result.stdout,
            )
            rtnval = QuotaEnforcement(
                mount=mount,
                fstype=fstype,
                user_enforced=user_on,
                group_enforced=group_on,
                project_enforced=project_on,
            )
            return rtnval

        command = [_QUOTAON, "-p", mount]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            raise QuotaQueryError(
                command=command,
                exit_code=run_result.exit_code,
                stderr=run_result.stderr,
            )
        user_on, group_on = _parse_quotaon_status(stdout=run_result.stdout)
        rtnval = QuotaEnforcement(
            mount=mount,
            fstype=fstype,
            user_enforced=user_on,
            group_enforced=group_on,
            project_enforced=False,
        )
        return rtnval

    def enable(
        self,
        mount: str,
        *,
        user: bool = True,
        group: bool = True,
        project: bool = False,
    ) -> FilesystemQuotaActionResult:
        """
            Turn quota enforcement ON for the requested target
            kinds on ``mount``.

            :param mount: Filesystem mountpoint.
            :param user: Enable user-quota enforcement.
            :param group: Enable group-quota enforcement.
            :param project: Enable project-quota enforcement (XFS).

            :returns: A :class:`FilesystemQuotaActionResult`.

            :raises QuotaFilesystemNotMountedError: when ``mount``
                is not currently mounted.
        """
        rtnval = self._toggle_enforcement(
            mount=mount,
            user=user,
            group=group,
            project=project,
            enable=True,
            operation="enable",
        )
        return rtnval

    def disable(
        self,
        mount: str,
        *,
        user: bool = True,
        group: bool = True,
        project: bool = False,
    ) -> FilesystemQuotaActionResult:
        """
            Turn quota enforcement OFF for the requested target
            kinds on ``mount``.

            :param mount: Filesystem mountpoint.
            :param user: Disable user-quota enforcement.
            :param group: Disable group-quota enforcement.
            :param project: Disable project-quota enforcement (XFS).

            :returns: A :class:`FilesystemQuotaActionResult`.

            :raises QuotaFilesystemNotMountedError: when ``mount``
                is not currently mounted.
        """
        rtnval = self._toggle_enforcement(
            mount=mount,
            user=user,
            group=group,
            project=project,
            enable=False,
            operation="disable",
        )
        return rtnval

    def quotacheck(
        self,
        mount: str,
        *,
        remount_ro: bool = True,
    ) -> FilesystemQuotaActionResult:
        """
            Rebuild the on-disk quota database for an ext-family
            filesystem.

            :param mount: Filesystem mountpoint.
            :param remount_ro: When True, allow ``quotacheck`` to
                               remount read-only while it runs
                               (the default safe path).  When
                               False the ``-m`` flag is added so
                               ``quotacheck`` may run on a busy
                               filesystem.

            :returns: A :class:`FilesystemQuotaActionResult`.

            :raises QuotaFilesystemNotMountedError: when ``mount``
                is not currently mounted.
            :raises QuotaUnsupportedOperationError: when ``mount``
                is XFS — XFS does not use ``quotacheck``.
        """
        fstype = self._fstype_of(mount=mount)
        if fstype == _XFS_FSTYPE:
            raise QuotaUnsupportedOperationError(
                operation="quotacheck",
                fstype=fstype,
                mount=mount,
            )

        if remount_ro:
            flags = "-ug"
        else:
            flags = "-ugm"

        command = [_QUOTACHECK, flags, mount]
        rtnval = self._dispatch(
            command=command,
            detail=mount,
            mount=mount,
            operation="quotacheck",
        )
        return rtnval

    # ------------------------------------------------------------------
    # Limit verbs
    # ------------------------------------------------------------------

    def set_user_quota(
        self,
        mount: str,
        username: str,
        limits: QuotaLimits,
    ) -> FilesystemQuotaActionResult:
        """
            Set block / inode soft / hard limits for ``username``
            on ``mount``.

            :param mount: Filesystem mountpoint.
            :param username: Target username.
            :param limits: Requested limits.

            :returns: A :class:`FilesystemQuotaActionResult`.

            :raises QuotaFilesystemNotMountedError: when ``mount``
                is not currently mounted.
        """
        rtnval = self._set_quota(
            mount=mount,
            name=username,
            target=QuotaTarget.USER,
            limits=limits,
            operation="set_user_quota",
        )
        return rtnval

    def set_group_quota(
        self,
        mount: str,
        groupname: str,
        limits: QuotaLimits,
    ) -> FilesystemQuotaActionResult:
        """
            Set block / inode soft / hard limits for ``groupname``
            on ``mount``.

            :param mount: Filesystem mountpoint.
            :param groupname: Target group name.
            :param limits: Requested limits.

            :returns: A :class:`FilesystemQuotaActionResult`.

            :raises QuotaFilesystemNotMountedError: when ``mount``
                is not currently mounted.
        """
        rtnval = self._set_quota(
            mount=mount,
            name=groupname,
            target=QuotaTarget.GROUP,
            limits=limits,
            operation="set_group_quota",
        )
        return rtnval

    def set_project_quota(
        self,
        mount: str,
        project_name: str,
        limits: QuotaLimits,
    ) -> FilesystemQuotaActionResult:
        """
            Set block / inode soft / hard limits for ``project_name``
            on an XFS mount.  Raises
            :class:`QuotaUnsupportedOperationError` on non-XFS
            filesystems.

            :param mount: Filesystem mountpoint (must be XFS).
            :param project_name: Project name (already declared in
                                 ``/etc/projid`` and activated).
            :param limits: Requested limits.

            :returns: A :class:`FilesystemQuotaActionResult`.

            :raises QuotaFilesystemNotMountedError: when ``mount``
                is not currently mounted.
            :raises QuotaUnsupportedOperationError: when ``mount``
                is not XFS.
        """
        rtnval = self._set_quota(
            mount=mount,
            name=project_name,
            target=QuotaTarget.PROJECT,
            limits=limits,
            operation="set_project_quota",
        )
        return rtnval

    # ------------------------------------------------------------------
    # Query verbs
    # ------------------------------------------------------------------

    def query_user(
        self,
        mount: str,
        username: str,
    ) -> Optional[QuotaEntry]:
        """
            Return the :class:`QuotaEntry` for ``username`` on
            ``mount`` or ``None`` when the principal has no record.

            :param mount: Filesystem mountpoint.
            :param username: Target username.
        """
        rtnval = self._query_principal(
            mount=mount,
            name=username,
            target=QuotaTarget.USER,
        )
        return rtnval

    def query_group(
        self,
        mount: str,
        groupname: str,
    ) -> Optional[QuotaEntry]:
        """
            Return the :class:`QuotaEntry` for ``groupname`` on
            ``mount`` or ``None`` when the principal has no record.

            :param mount: Filesystem mountpoint.
            :param groupname: Target group name.
        """
        rtnval = self._query_principal(
            mount=mount,
            name=groupname,
            target=QuotaTarget.GROUP,
        )
        return rtnval

    def query_project(
        self,
        mount: str,
        project_name: str,
    ) -> Optional[QuotaEntry]:
        """
            Return the :class:`QuotaEntry` for ``project_name`` on
            an XFS mount or ``None`` when the project has no record.

            :param mount: Filesystem mountpoint (must be XFS).
            :param project_name: Project name.

            :raises QuotaUnsupportedOperationError: when ``mount``
                is not XFS.
        """
        fstype = self._fstype_of(mount=mount)
        if fstype != _XFS_FSTYPE:
            raise QuotaUnsupportedOperationError(
                operation="query_project",
                fstype=fstype,
                mount=mount,
            )

        command = [_XFS_QUOTA, "-x", "-c", "report -p", mount]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            raise QuotaQueryError(
                command=command,
                exit_code=run_result.exit_code,
                stderr=run_result.stderr,
            )

        entries = _parse_xfs_report(
            stdout=run_result.stdout,
            mount=mount,
            target=QuotaTarget.PROJECT,
        )
        rtnval: Optional[QuotaEntry] = None
        for entry in entries:
            if entry.name == project_name:
                rtnval = entry
                break
        return rtnval

    def list_all(
        self,
        mount: str,
        target: QuotaTarget = QuotaTarget.USER,
    ) -> list[QuotaEntry]:
        """
            Enumerate every quota record for the given target on
            ``mount``.

            :param mount: Filesystem mountpoint.
            :param target: USER / GROUP / PROJECT.

            :returns: One :class:`QuotaEntry` per principal.

            :raises QuotaQueryError: when the underlying tool exits
                non-zero.
        """
        fstype = self._fstype_of(mount=mount)

        if fstype == _XFS_FSTYPE:
            xfs_target_flag = _xfs_target_flag(target=target)
            command = [
                _XFS_QUOTA,
                "-x",
                "-c",
                "report {}".format(xfs_target_flag),
                mount,
            ]
            run_result = self._runner.run(list(command), elevated=False)
            if run_result.exit_code != 0:
                raise QuotaQueryError(
                    command=command,
                    exit_code=run_result.exit_code,
                    stderr=run_result.stderr,
                )
            rtnval = _parse_xfs_report(
                stdout=run_result.stdout,
                mount=mount,
                target=target,
            )
            return rtnval

        if target is QuotaTarget.PROJECT:
            raise QuotaUnsupportedOperationError(
                operation="list_all(project)",
                fstype=fstype,
                mount=mount,
            )

        kind_flag = "-u" if target is QuotaTarget.USER else "-g"
        command = [_REPQUOTA, kind_flag, mount]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            raise QuotaQueryError(
                command=command,
                exit_code=run_result.exit_code,
                stderr=run_result.stderr,
            )

        rtnval = _parse_repquota_output(
            stdout=run_result.stdout,
            mount=mount,
            target=target,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Grace-period verbs
    # ------------------------------------------------------------------

    def get_grace_period(
        self,
        mount: str,
        target: QuotaTarget = QuotaTarget.USER,
    ) -> GracePeriod:
        """
            Return the per-(mount, target) grace period.

            :param mount: Filesystem mountpoint.
            :param target: USER / GROUP / PROJECT.

            :raises QuotaQueryError: on tool failure.
        """
        fstype = self._fstype_of(mount=mount)

        if fstype == _XFS_FSTYPE:
            xfs_target_flag = _xfs_target_flag(target=target)
            command = [
                _XFS_QUOTA,
                "-x",
                "-c",
                "timer {}".format(xfs_target_flag),
                mount,
            ]
            run_result = self._runner.run(list(command), elevated=False)
            if run_result.exit_code != 0:
                raise QuotaQueryError(
                    command=command,
                    exit_code=run_result.exit_code,
                    stderr=run_result.stderr,
                )
            block_seconds, inode_seconds = _parse_xfs_timer(
                stdout=run_result.stdout,
            )
            rtnval = GracePeriod(
                mount=mount,
                target=target,
                block_seconds=block_seconds,
                inode_seconds=inode_seconds,
            )
            return rtnval

        kind_flag = "-u" if target is QuotaTarget.USER else "-g"
        command = [_REPQUOTA, kind_flag, "-t", mount]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            raise QuotaQueryError(
                command=command,
                exit_code=run_result.exit_code,
                stderr=run_result.stderr,
            )
        block_seconds, inode_seconds = _parse_repquota_grace(
            stdout=run_result.stdout,
        )
        rtnval = GracePeriod(
            mount=mount,
            target=target,
            block_seconds=block_seconds,
            inode_seconds=inode_seconds,
        )
        return rtnval

    def set_grace_period(
        self,
        mount: str,
        target: QuotaTarget,
        block_seconds: int,
        inode_seconds: int,
    ) -> FilesystemQuotaActionResult:
        """
            Set the per-(mount, target) grace period.

            :param mount: Filesystem mountpoint.
            :param target: USER / GROUP / PROJECT.
            :param block_seconds: Block grace in seconds.
            :param inode_seconds: Inode grace in seconds.

            :raises QuotaFilesystemNotMountedError: when ``mount``
                is not currently mounted.
        """
        fstype = self._fstype_of(mount=mount)

        if fstype == _XFS_FSTYPE:
            xfs_target_flag = _xfs_target_flag(target=target)
            xfs_command_text = "timer {} {} {}".format(
                xfs_target_flag,
                block_seconds,
                inode_seconds,
            )
            command = [_XFS_QUOTA, "-x", "-c", xfs_command_text, mount]
        else:
            kind_flag = "-u" if target is QuotaTarget.USER else "-g"
            command = [
                _SETQUOTA,
                kind_flag,
                "-t",
                str(block_seconds),
                str(inode_seconds),
                mount,
            ]

        rtnval = self._dispatch(
            command=command,
            detail=mount,
            mount=mount,
            operation="set_grace_period",
        )
        return rtnval

    # ------------------------------------------------------------------
    # XFS project plumbing
    # ------------------------------------------------------------------

    def define_project(
        self,
        project_id: int,
        project_name: str,
        paths: list[str],
    ) -> FilesystemQuotaActionResult:
        """
            Upsert a project definition into ``/etc/projects`` (one
            line per path) and ``/etc/projid`` (single
            ``name:id`` line).

            :param project_id: Numeric project identifier.
            :param project_name: Symbolic project name.
            :param paths: One path per ``/etc/projects`` line.

            :returns: A :class:`FilesystemQuotaActionResult`.
        """
        projects_existing = self._read_file(
            path=self._typed_config().projects_file,
        )
        projid_existing = self._read_file(
            path=self._typed_config().projid_file,
        )

        new_projects = _upsert_projects_lines(
            existing=projects_existing,
            project_id=project_id,
            paths=list(paths),
        )
        new_projid = _upsert_projid_lines(
            existing=projid_existing,
            project_id=project_id,
            project_name=project_name,
        )

        already_correct = (
            new_projects == projects_existing
            and new_projid == projid_existing
        )
        if already_correct:
            skipped = FilesystemQuotaActionResult(
                status=QuotaActionStatus.SKIPPED,
                detail=project_name,
                mount=None,
            )
            return skipped

        config = self._typed_config()
        if self._config.dry_run:
            dry_result = FilesystemQuotaActionResult(
                status=QuotaActionStatus.DRY_RUN,
                detail=project_name,
                mount=None,
                command=["tee", config.projects_file],
            )
            return dry_result

        write_projects_result = self._write_file(
            path=config.projects_file,
            content=new_projects,
        )
        if write_projects_result.exit_code != 0:
            failed = FilesystemQuotaActionResult(
                status=QuotaActionStatus.FAILED,
                detail=project_name,
                mount=None,
                exit_code=write_projects_result.exit_code,
                stdout=write_projects_result.stdout,
                stderr=write_projects_result.stderr,
                command=list(write_projects_result.command),
            )
            return failed

        write_projid_result = self._write_file(
            path=config.projid_file,
            content=new_projid,
        )
        if write_projid_result.exit_code != 0:
            failed = FilesystemQuotaActionResult(
                status=QuotaActionStatus.FAILED,
                detail=project_name,
                mount=None,
                exit_code=write_projid_result.exit_code,
                stdout=write_projid_result.stdout,
                stderr=write_projid_result.stderr,
                command=list(write_projid_result.command),
            )
            return failed

        rtnval = FilesystemQuotaActionResult(
            status=QuotaActionStatus.SUCCESS,
            detail=project_name,
            mount=None,
            exit_code=0,
            command=["tee", config.projects_file],
        )
        return rtnval

    def remove_project(
        self,
        project_name: str,
    ) -> FilesystemQuotaActionResult:
        """
            Remove the ``project_name`` definition from both
            ``/etc/projects`` (every line whose id matches) and
            ``/etc/projid`` (the single ``name:id`` line).

            :param project_name: The project to remove.

            :returns: A :class:`FilesystemQuotaActionResult`.
        """
        config = self._typed_config()
        projects_existing = self._read_file(path=config.projects_file)
        projid_existing = self._read_file(path=config.projid_file)

        existing_id = _find_project_id_for_name(
            projid_content=projid_existing,
            project_name=project_name,
        )
        if existing_id is None:
            skipped = FilesystemQuotaActionResult(
                status=QuotaActionStatus.SKIPPED,
                detail=project_name,
                mount=None,
            )
            return skipped

        new_projects = _strip_projects_lines_for_id(
            existing=projects_existing,
            project_id=existing_id,
        )
        new_projid = _strip_projid_line_for_name(
            existing=projid_existing,
            project_name=project_name,
        )

        if self._config.dry_run:
            dry_result = FilesystemQuotaActionResult(
                status=QuotaActionStatus.DRY_RUN,
                detail=project_name,
                mount=None,
                command=["tee", config.projects_file],
            )
            return dry_result

        write_projects_result = self._write_file(
            path=config.projects_file,
            content=new_projects,
        )
        if write_projects_result.exit_code != 0:
            failed = FilesystemQuotaActionResult(
                status=QuotaActionStatus.FAILED,
                detail=project_name,
                mount=None,
                exit_code=write_projects_result.exit_code,
                stdout=write_projects_result.stdout,
                stderr=write_projects_result.stderr,
                command=list(write_projects_result.command),
            )
            return failed

        write_projid_result = self._write_file(
            path=config.projid_file,
            content=new_projid,
        )
        if write_projid_result.exit_code != 0:
            failed = FilesystemQuotaActionResult(
                status=QuotaActionStatus.FAILED,
                detail=project_name,
                mount=None,
                exit_code=write_projid_result.exit_code,
                stdout=write_projid_result.stdout,
                stderr=write_projid_result.stderr,
                command=list(write_projid_result.command),
            )
            return failed

        rtnval = FilesystemQuotaActionResult(
            status=QuotaActionStatus.SUCCESS,
            detail=project_name,
            mount=None,
            exit_code=0,
            command=["tee", config.projects_file],
        )
        return rtnval

    def activate_project(
        self,
        mount: str,
        project_name: str,
    ) -> FilesystemQuotaActionResult:
        """
            Activate ``project_name`` on the XFS ``mount`` so its
            recorded paths inherit the project id.

            :param mount: XFS mountpoint.
            :param project_name: Project name (already declared via
                                 :meth:`define_project`).

            :raises QuotaFilesystemNotMountedError: when ``mount``
                is not mounted.
            :raises QuotaUnsupportedOperationError: when ``mount``
                is not XFS.
        """
        fstype = self._fstype_of(mount=mount)
        if fstype != _XFS_FSTYPE:
            raise QuotaUnsupportedOperationError(
                operation="activate_project",
                fstype=fstype,
                mount=mount,
            )

        xfs_command_text = "project -s {}".format(project_name)
        command = [_XFS_QUOTA, "-x", "-c", xfs_command_text, mount]
        rtnval = self._dispatch(
            command=command,
            detail=project_name,
            mount=mount,
            operation="activate_project",
        )
        return rtnval

    def list_projects(self) -> list[ProjectDefinition]:
        """
            Read ``/etc/projects`` + ``/etc/projid`` and return the
            merged project definitions.

            :returns: One :class:`ProjectDefinition` per declared
                      project.
        """
        config = self._typed_config()
        projects_content = self._read_file(path=config.projects_file)
        projid_content = self._read_file(path=config.projid_file)
        rtnval = _merge_project_files(
            projects_content=projects_content,
            projid_content=projid_content,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _typed_config(self) -> FilesystemQuotaManagerConfig:
        """
            Return ``self._config`` as a
            :class:`FilesystemQuotaManagerConfig`, falling back to
            defaults when the caller passed a plain
            :class:`FeatureConfig`.
        """
        config = self._config
        if isinstance(config, FilesystemQuotaManagerConfig):
            rtnval = config
            return rtnval
        rtnval = FilesystemQuotaManagerConfig(
            dry_run=config.dry_run,
            elevated=config.elevated,
        )
        return rtnval

    def _fstype_of(
        self,
        *,
        mount: str,
    ) -> str:
        """
            Probe ``findmnt -n -o FSTYPE <mount>`` and return the
            stripped fstype.

            :param mount: Filesystem mountpoint.

            :raises QuotaFilesystemNotMountedError: when ``findmnt``
                exits non-zero or returns an empty body.
        """
        command = [_FINDMNT, "-n", "-o", "FSTYPE", mount]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            raise QuotaFilesystemNotMountedError(
                mount=mount,
                command=command,
                exit_code=run_result.exit_code,
                stderr=run_result.stderr,
            )
        body = run_result.stdout.strip()
        if len(body) == 0:
            raise QuotaFilesystemNotMountedError(
                mount=mount,
                command=command,
                exit_code=run_result.exit_code,
                stderr="findmnt returned empty FSTYPE",
            )
        rtnval = body
        return rtnval

    def _read_file(
        self,
        *,
        path: str,
    ) -> str:
        """
            Read a file via ``cat``.  Returns an empty string on
            non-zero exit (mirrors the convention used elsewhere
            in this package: a missing drop-in is an empty file).

            :param path: Filesystem path.
        """
        run_result = self._runner.run(
            [_CAT_TOOL, path],
            elevated=False,
        )
        if run_result.exit_code != 0:
            rtnval = ""
            return rtnval
        rtnval = run_result.stdout
        return rtnval

    def _write_file(
        self,
        *,
        path: str,
        content: str,
    ):
        """
            Write ``content`` to ``path`` via ``tee`` so the I/O
            path goes through the runner (and therefore through
            elevation / SSH transport / aspects).

            :param path: Filesystem path.
            :param content: New file content.
        """
        rtnval = self._runner.run(
            [_TEE_TOOL, path],
            elevated=self._config.elevated,
            stdin=content,
        )
        return rtnval

    def _toggle_enforcement(
        self,
        *,
        mount: str,
        user: bool,
        group: bool,
        project: bool,
        enable: bool,
        operation: str,
    ) -> FilesystemQuotaActionResult:
        """
            Shared body of :meth:`enable` and :meth:`disable`.
        """
        fstype = self._fstype_of(mount=mount)

        if fstype == _XFS_FSTYPE:
            command = _build_xfs_enforcement_command(
                mount=mount,
                user=user,
                group=group,
                project=project,
                enable=enable,
            )
        else:
            command = _build_extfamily_enforcement_command(
                mount=mount,
                user=user,
                group=group,
                project=project,
                enable=enable,
            )

        # SKIPPED guard: only when not in dry-run, so dry-run
        # always returns a planned-command receipt.
        if not self._config.dry_run:
            current = self.query_enforcement(mount=mount)
            if _enforcement_matches(
                current=current,
                user=user,
                group=group,
                project=project,
                enable=enable,
            ):
                skipped = FilesystemQuotaActionResult(
                    status=QuotaActionStatus.SKIPPED,
                    detail=mount,
                    mount=mount,
                    command=list(command),
                )
                return skipped

        rtnval = self._dispatch(
            command=command,
            detail=mount,
            mount=mount,
            operation=operation,
        )
        return rtnval

    def _set_quota(
        self,
        *,
        mount: str,
        name: str,
        target: QuotaTarget,
        limits: QuotaLimits,
        operation: str,
    ) -> FilesystemQuotaActionResult:
        """
            Shared body of :meth:`set_user_quota`,
            :meth:`set_group_quota`, and
            :meth:`set_project_quota`.
        """
        fstype = self._fstype_of(mount=mount)

        if target is QuotaTarget.PROJECT and fstype != _XFS_FSTYPE:
            raise QuotaUnsupportedOperationError(
                operation=operation,
                fstype=fstype,
                mount=mount,
            )

        if fstype == _XFS_FSTYPE:
            xfs_target_flag = _xfs_target_flag(target=target)
            xfs_command_text = (
                "limit {flag} bsoft={bs} bhard={bh} isoft={is_} ihard={ih} {name}"
            ).format(
                flag=xfs_target_flag,
                bs=limits.block_soft,
                bh=limits.block_hard,
                is_=limits.inode_soft,
                ih=limits.inode_hard,
                name=name,
            )
            command = [_XFS_QUOTA, "-x", "-c", xfs_command_text, mount]
        else:
            kind_flag = "-u" if target is QuotaTarget.USER else "-g"
            command = [
                _SETQUOTA,
                kind_flag,
                name,
                str(limits.block_soft),
                str(limits.block_hard),
                str(limits.inode_soft),
                str(limits.inode_hard),
                mount,
            ]

        # SKIPPED guard: only when not in dry-run.
        if not self._config.dry_run:
            current = self._query_principal(
                mount=mount,
                name=name,
                target=target,
            )
            if current is not None:
                if _limits_match(current=current, limits=limits):
                    skipped = FilesystemQuotaActionResult(
                        status=QuotaActionStatus.SKIPPED,
                        detail=name,
                        mount=mount,
                        command=list(command),
                    )
                    return skipped

        rtnval = self._dispatch(
            command=command,
            detail=name,
            mount=mount,
            operation=operation,
        )
        return rtnval

    def _query_principal(
        self,
        *,
        mount: str,
        name: str,
        target: QuotaTarget,
    ) -> Optional[QuotaEntry]:
        """
            Shared body of :meth:`query_user`, :meth:`query_group`,
            and :meth:`query_project`.
        """
        fstype = self._fstype_of(mount=mount)

        if fstype == _XFS_FSTYPE:
            xfs_target_flag = _xfs_target_flag(target=target)
            command = [
                _XFS_QUOTA,
                "-x",
                "-c",
                "report {}".format(xfs_target_flag),
                mount,
            ]
            run_result = self._runner.run(list(command), elevated=False)
            if run_result.exit_code != 0:
                raise QuotaQueryError(
                    command=command,
                    exit_code=run_result.exit_code,
                    stderr=run_result.stderr,
                )
            entries = _parse_xfs_report(
                stdout=run_result.stdout,
                mount=mount,
                target=target,
            )
            rtnval: Optional[QuotaEntry] = None
            for entry in entries:
                if entry.name == name:
                    rtnval = entry
                    break
            return rtnval

        if target is QuotaTarget.PROJECT:
            raise QuotaUnsupportedOperationError(
                operation="query_project",
                fstype=fstype,
                mount=mount,
            )

        kind_flag = "-u" if target is QuotaTarget.USER else "-g"
        command = [_QUOTA, "-v", kind_flag, name]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code not in (0, 1, 2):
            raise QuotaQueryError(
                command=command,
                exit_code=run_result.exit_code,
                stderr=run_result.stderr,
            )

        rtnval = _parse_quota_v_output(
            stdout=run_result.stdout,
            mount=mount,
            name=name,
            target=target,
        )
        return rtnval


# ----------------------------------------------------------------------
# Module-level helper command tools (kept separate to ease test
# imports and to centralise tool names).
# ----------------------------------------------------------------------

_CAT_TOOL = "cat"
_TEE_TOOL = "tee"


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------


def _xfs_target_flag(*, target: QuotaTarget) -> str:
    """
        Return the ``xfs_quota`` flag for ``target``: ``-u``, ``-g``,
        or ``-p``.

        :param target: Quota target kind.
    """
    if target is QuotaTarget.USER:
        rtnval = "-u"
        return rtnval
    if target is QuotaTarget.GROUP:
        rtnval = "-g"
        return rtnval
    rtnval = "-p"
    return rtnval


def _build_extfamily_enforcement_command(
    *,
    mount: str,
    user: bool,
    group: bool,
    project: bool,
    enable: bool,
) -> list[str]:
    """
        Build the ``quotaon`` / ``quotaoff`` argv for an ext-family
        toggle.

        :param mount: Mountpoint.
        :param user: Include ``-u``.
        :param group: Include ``-g``.
        :param project: Include ``-P``.
        :param enable: True for ``quotaon``, False for ``quotaoff``.
    """
    if enable:
        program = _QUOTAON
    else:
        program = _QUOTAOFF

    command: list[str] = [program]
    if user:
        command.append("-u")
    if group:
        command.append("-g")
    if project:
        command.append("-P")
    command.append(mount)
    return command


def _build_xfs_enforcement_command(
    *,
    mount: str,
    user: bool,
    group: bool,
    project: bool,
    enable: bool,
) -> list[str]:
    """
        Build the ``xfs_quota`` argv for an XFS toggle.

        :param mount: Mountpoint.
        :param user: Include ``-u``.
        :param group: Include ``-g``.
        :param project: Include ``-p``.
        :param enable: True for ``enable``, False for ``disable``.
    """
    flag_chars: list[str] = []
    if user:
        flag_chars.append("u")
    if group:
        flag_chars.append("g")
    if project:
        flag_chars.append("p")

    if len(flag_chars) > 0:
        flag_text = "-" + "".join(flag_chars)
    else:
        flag_text = ""

    if enable:
        verb = "enable"
    else:
        verb = "disable"

    command_text_parts = [verb]
    if len(flag_text) > 0:
        command_text_parts.append(flag_text)
    inner = " ".join(command_text_parts)

    rtnval = [_XFS_QUOTA, "-x", "-c", inner, mount]
    return rtnval


def _enforcement_matches(
    *,
    current: QuotaEnforcement,
    user: bool,
    group: bool,
    project: bool,
    enable: bool,
) -> bool:
    """
        Return True when ``current`` enforcement state already
        matches the request (so the toggle is a no-op).
    """
    if enable:
        target_state = True
    else:
        target_state = False

    if user is True:
        if current.user_enforced != target_state:
            rtnval = False
            return rtnval
    if group is True:
        if current.group_enforced != target_state:
            rtnval = False
            return rtnval
    if project is True:
        if current.project_enforced != target_state:
            rtnval = False
            return rtnval

    rtnval = True
    return rtnval


def _limits_match(
    *,
    current: QuotaEntry,
    limits: QuotaLimits,
) -> bool:
    """
        Return True when ``current`` already carries the requested
        ``limits`` values.
    """
    if current.block_soft != limits.block_soft:
        rtnval = False
        return rtnval
    if current.block_hard != limits.block_hard:
        rtnval = False
        return rtnval
    if current.inode_soft != limits.inode_soft:
        rtnval = False
        return rtnval
    if current.inode_hard != limits.inode_hard:
        rtnval = False
        return rtnval
    rtnval = True
    return rtnval


# ----------------------------------------------------------------------
# Parsers (pure, module-scoped)
# ----------------------------------------------------------------------


def _parse_quotaon_status(*, stdout: str) -> tuple[bool, bool]:
    """
        Parse ``quotaon -p <mount>`` output and return
        ``(user_active, group_active)``.

        Output rows look like::

            user quota on /mnt/data (/dev/sda1) is on
            group quota on /mnt/data (/dev/sda1) is off

        :param stdout: Raw stdout from ``quotaon``.
    """
    user_active = False
    group_active = False

    for raw_line in stdout.splitlines():
        stripped = raw_line.strip().lower()
        if len(stripped) == 0:
            continue

        tokens = stripped.split()
        if len(tokens) == 0:
            continue
        last_token = tokens[-1]
        is_on = last_token == "on"

        if stripped.startswith("user quota"):
            user_active = is_on
            continue
        if stripped.startswith("group quota"):
            group_active = is_on
            continue

    rtnval = (user_active, group_active)
    return rtnval


def _parse_xfs_state(*, stdout: str) -> tuple[bool, bool, bool]:
    """
        Parse ``xfs_quota -x -c "state" <mount>`` output and return
        ``(user_on, group_on, project_on)``.  Both "accounting" and
        "enforcement" must report ``ON`` for the corresponding
        flag.

        :param stdout: Raw stdout from ``xfs_quota state``.
    """
    user_acct = False
    user_enf = False
    group_acct = False
    group_enf = False
    project_acct = False
    project_enf = False
    current_section: Optional[str] = None

    for raw_line in stdout.splitlines():
        stripped = raw_line.strip()
        if len(stripped) == 0:
            current_section = None
            continue

        lowered = stripped.lower()

        if lowered.startswith("user quota state"):
            current_section = "user"
            continue
        if lowered.startswith("group quota state"):
            current_section = "group"
            continue
        if lowered.startswith("project quota state"):
            current_section = "project"
            continue

        if "accounting:" in lowered:
            value_on = lowered.split("accounting:", 1)[1].strip().startswith("on")
            if current_section == "user":
                user_acct = value_on
            elif current_section == "group":
                group_acct = value_on
            elif current_section == "project":
                project_acct = value_on
            continue

        if "enforcement:" in lowered:
            value_on = lowered.split("enforcement:", 1)[1].strip().startswith("on")
            if current_section == "user":
                user_enf = value_on
            elif current_section == "group":
                group_enf = value_on
            elif current_section == "project":
                project_enf = value_on
            continue

    user_on = user_acct and user_enf
    group_on = group_acct and group_enf
    project_on = project_acct and project_enf

    rtnval = (user_on, group_on, project_on)
    return rtnval


def _parse_quota_v_output(
    *,
    stdout: str,
    mount: str,
    name: str,
    target: QuotaTarget,
) -> Optional[QuotaEntry]:
    """
        Parse ``quota -v -u|-g <name>`` output and return the
        :class:`QuotaEntry` for ``mount`` or ``None`` when the
        principal has no row matching ``mount``.

        :param stdout: Raw quota stdout.
        :param mount: Mountpoint to record.
        :param name: Principal name.
        :param target: USER / GROUP.
    """
    rows: list[list[str]] = []
    for raw_line in stdout.splitlines():
        stripped = raw_line.strip()
        if len(stripped) == 0:
            continue
        if stripped.startswith("Disk quotas"):
            continue
        if stripped.startswith("Filesystem"):
            continue
        if stripped.startswith("none"):
            continue
        if stripped.lower().startswith("blocks"):
            continue

        tokens = stripped.split()
        rows.append(tokens)

    if len(rows) == 0:
        rtnval: Optional[QuotaEntry] = None
        return rtnval

    matched: Optional[list[str]] = None
    for tokens in rows:
        if len(tokens) == 0:
            continue
        first = tokens[0]
        if first == mount:
            matched = tokens
            break
        if first.endswith(mount):
            matched = tokens
            break
        if len(rows) == 1:
            matched = tokens
            break

    if matched is None:
        rtnval = None
        return rtnval

    rtnval = _build_quota_entry_from_quota_row(
        row=matched,
        mount=mount,
        name=name,
        target=target,
    )
    return rtnval


def _build_quota_entry_from_quota_row(
    *,
    row: list[str],
    mount: str,
    name: str,
    target: QuotaTarget,
) -> QuotaEntry:
    """
        Map a ``quota -v`` token row into a :class:`QuotaEntry`.

        Token order is::

            <filesystem> <bused> <bsoft> <bhard> [<bgrace>]
                         <iused> <isoft> <ihard> [<igrace>]

        ``*`` suffixes on the used columns indicate a soft-limit
        breach; they are stripped.

        :param row: The token list.
        :param mount: Mountpoint to record.
        :param name: Principal name.
        :param target: USER / GROUP.
    """
    blocks_used = _strip_warning_marker(token=row[1] if len(row) > 1 else "")
    block_soft = _strip_warning_marker(token=row[2] if len(row) > 2 else "")
    block_hard = _strip_warning_marker(token=row[3] if len(row) > 3 else "")

    blocks_used_int = _int_or_zero(token=blocks_used)
    block_soft_int = _int_or_zero(token=block_soft)
    block_hard_int = _int_or_zero(token=block_hard)

    grace_blocks: Optional[str] = None
    inodes_used = 0
    inode_soft = 0
    inode_hard = 0
    grace_inodes: Optional[str] = None

    # Walk remaining tokens; the next three integer-looking tokens
    # are inode used/soft/hard and any non-integer token is treated
    # as a grace value.
    remaining = row[4:]
    int_tokens: list[str] = []
    grace_tokens: list[str] = []
    for token in remaining:
        clean = _strip_warning_marker(token=token)
        if _is_integer_token(token=clean):
            int_tokens.append(clean)
        else:
            grace_tokens.append(clean)

    if len(int_tokens) >= 3:
        inodes_used = _int_or_zero(token=int_tokens[0])
        inode_soft = _int_or_zero(token=int_tokens[1])
        inode_hard = _int_or_zero(token=int_tokens[2])

    if len(grace_tokens) >= 1:
        grace_blocks = grace_tokens[0]
    if len(grace_tokens) >= 2:
        grace_inodes = grace_tokens[1]

    rtnval = QuotaEntry(
        mount=mount,
        target=target,
        name=name,
        blocks_used=blocks_used_int,
        block_soft=block_soft_int,
        block_hard=block_hard_int,
        inodes_used=inodes_used,
        inode_soft=inode_soft,
        inode_hard=inode_hard,
        grace_blocks=grace_blocks,
        grace_inodes=grace_inodes,
    )
    return rtnval


def _parse_repquota_output(
    *,
    stdout: str,
    mount: str,
    target: QuotaTarget,
) -> list[QuotaEntry]:
    """
        Parse ``repquota -u`` or ``repquota -g`` output into a list
        of :class:`QuotaEntry`.  The single-target variant of
        ``repquota`` does not emit ``*** Report for ...`` section
        markers; we simply read every data row.

        :param stdout: Raw repquota stdout.
        :param mount: Mountpoint to record on every entry.
        :param target: USER / GROUP (only those map to repquota).
    """
    rtnval: list[QuotaEntry] = []

    for raw_line in stdout.splitlines():
        stripped = raw_line.strip()
        if len(stripped) == 0:
            continue
        if stripped.startswith("***"):
            continue
        lowered = stripped.lower()
        if "block grace" in lowered or "inode grace" in lowered:
            continue
        if lowered.startswith("user") and "used" in lowered:
            continue
        if lowered.startswith("group") and "used" in lowered:
            continue
        if stripped.startswith("---"):
            continue
        if "Block limits" in stripped or "File limits" in stripped:
            continue

        tokens = stripped.split()
        if len(tokens) < 5:
            continue

        info = _build_quota_entry_from_repquota_row(
            row=tokens,
            mount=mount,
            target=target,
        )
        if info is not None:
            rtnval.append(info)

    return rtnval


def _build_quota_entry_from_repquota_row(
    *,
    row: list[str],
    mount: str,
    target: QuotaTarget,
) -> Optional[QuotaEntry]:
    """
        Map a single repquota data row into a :class:`QuotaEntry`.

        Repquota row shape::

            <name> <flags> <bused> <bsoft> <bhard> [<bgrace>]
                          <iused> <isoft> <ihard> [<igrace>]

        :param row: Token list.
        :param mount: Mountpoint to record.
        :param target: USER / GROUP.
    """
    principal_name = row[0]

    if len(row) >= 6 and _is_repquota_flag_token(token=row[1]):
        offset = 2
    else:
        offset = 1

    if len(row) < offset + 3:
        rtnval: Optional[QuotaEntry] = None
        return rtnval

    bused_token = row[offset]
    bsoft_token = row[offset + 1]
    bhard_token = row[offset + 2]

    blocks_used = _int_or_zero(token=bused_token)
    block_soft = _int_or_zero(token=bsoft_token)
    block_hard = _int_or_zero(token=bhard_token)

    remaining = row[offset + 3:]
    int_tokens: list[str] = []
    grace_tokens: list[str] = []
    for token in remaining:
        if _is_integer_token(token=token):
            int_tokens.append(token)
        else:
            grace_tokens.append(token)

    inodes_used = 0
    inode_soft = 0
    inode_hard = 0
    if len(int_tokens) >= 3:
        inodes_used = _int_or_zero(token=int_tokens[0])
        inode_soft = _int_or_zero(token=int_tokens[1])
        inode_hard = _int_or_zero(token=int_tokens[2])

    grace_blocks: Optional[str] = None
    grace_inodes: Optional[str] = None
    if len(grace_tokens) >= 1:
        grace_blocks = grace_tokens[0]
    if len(grace_tokens) >= 2:
        grace_inodes = grace_tokens[1]

    rtnval = QuotaEntry(
        mount=mount,
        target=target,
        name=principal_name,
        blocks_used=blocks_used,
        block_soft=block_soft,
        block_hard=block_hard,
        inodes_used=inodes_used,
        inode_soft=inode_soft,
        inode_hard=inode_hard,
        grace_blocks=grace_blocks,
        grace_inodes=grace_inodes,
    )
    return rtnval


def _parse_xfs_report(
    *,
    stdout: str,
    mount: str,
    target: QuotaTarget,
) -> list[QuotaEntry]:
    """
        Parse ``xfs_quota -x -c "report -u|-g|-p" <mount>`` output
        into a list of :class:`QuotaEntry`.

        Representative output::

            User quota on /mnt/data (/dev/sda1)
                                    Blocks
            User ID      Used   Soft   Hard Warn/Grace
            ---------- --------------------------------
            root          512      0      0  00 [------]
            alice      102400 204800 409600  00 [------]

        We tolerate a few formatting variants — the parser keys off
        the dashed-separator row and reads any subsequent
        whitespace-separated token row as data.

        :param stdout: Raw xfs_quota report stdout.
        :param mount: Mountpoint to record.
        :param target: USER / GROUP / PROJECT.
    """
    rtnval: list[QuotaEntry] = []

    in_data = False
    for raw_line in stdout.splitlines():
        stripped = raw_line.rstrip()
        if len(stripped) == 0:
            in_data = False
            continue

        body = stripped.strip()
        if body.startswith("---"):
            in_data = True
            continue
        if not in_data:
            continue

        tokens = body.split()
        if len(tokens) < 4:
            continue

        principal_name = tokens[0]
        blocks_used = _int_or_zero(token=tokens[1])
        block_soft = _int_or_zero(token=tokens[2])
        block_hard = _int_or_zero(token=tokens[3])

        entry = QuotaEntry(
            mount=mount,
            target=target,
            name=principal_name,
            blocks_used=blocks_used,
            block_soft=block_soft,
            block_hard=block_hard,
            inodes_used=0,
            inode_soft=0,
            inode_hard=0,
            grace_blocks=None,
            grace_inodes=None,
        )
        rtnval.append(entry)

    return rtnval


def _parse_xfs_timer(*, stdout: str) -> tuple[int, int]:
    """
        Parse ``xfs_quota -x -c "timer ..."`` output and return
        ``(block_seconds, inode_seconds)``.  Defaults to ``(0, 0)``
        when the output cannot be parsed.

        :param stdout: Raw xfs_quota timer stdout.
    """
    block_seconds = 0
    inode_seconds = 0

    for raw_line in stdout.splitlines():
        stripped = raw_line.strip().lower()
        if len(stripped) == 0:
            continue
        if "blocks grace time" in stripped or "blocks time" in stripped:
            block_seconds = _extract_seconds(line=stripped)
            continue
        if "inodes grace time" in stripped or "inodes time" in stripped:
            inode_seconds = _extract_seconds(line=stripped)
            continue

    rtnval = (block_seconds, inode_seconds)
    return rtnval


def _extract_seconds(*, line: str) -> int:
    """
        Extract an integer-second value from a free-form line like
        ``"blocks grace time: 7days"``.  Returns ``0`` when no
        recognisable token is present.

        :param line: The raw line.
    """
    suffix_map = {
        "days": 86400,
        "day": 86400,
        "hours": 3600,
        "hour": 3600,
        "minutes": 60,
        "minute": 60,
        "seconds": 1,
        "second": 1,
    }
    tokens = line.split()
    for token in tokens:
        for suffix, multiplier in suffix_map.items():
            if token.endswith(suffix):
                number_part = token[: -len(suffix)]
                if _is_integer_token(token=number_part):
                    rtnval = int(number_part) * multiplier
                    return rtnval
    rtnval = 0
    return rtnval


def _parse_repquota_grace(*, stdout: str) -> tuple[int, int]:
    """
        Parse ``repquota -u -t`` (or ``-g -t``) grace-time output
        and return ``(block_seconds, inode_seconds)``.  The
        traditional ``-t`` line looks like::

            Block grace time: 7days; Inode grace time: 7days

        :param stdout: Raw repquota stdout.
    """
    block_seconds = 0
    inode_seconds = 0

    for raw_line in stdout.splitlines():
        stripped = raw_line.strip().lower()
        if len(stripped) == 0:
            continue
        if "block grace time" not in stripped and "inode grace time" not in stripped:
            continue

        # Split on ';' so we can handle "Block grace time: X; Inode grace time: Y"
        segments = stripped.split(";")
        for segment in segments:
            segment_clean = segment.strip()
            if "block grace time" in segment_clean:
                block_seconds = _extract_seconds(line=segment_clean)
            elif "inode grace time" in segment_clean:
                inode_seconds = _extract_seconds(line=segment_clean)

    rtnval = (block_seconds, inode_seconds)
    return rtnval


# ----------------------------------------------------------------------
# /etc/projects + /etc/projid editors
# ----------------------------------------------------------------------


def _upsert_projects_lines(
    *,
    existing: str,
    project_id: int,
    paths: list[str],
) -> str:
    """
        Return new ``/etc/projects`` content with one
        ``<id>:<path>`` line per ``paths`` entry, replacing every
        existing line whose id equals ``project_id``.

        :param existing: Current content.
        :param project_id: Project id.
        :param paths: New paths.
    """
    kept_lines: list[str] = []
    for raw_line in existing.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if len(stripped) == 0:
            kept_lines.append(line)
            continue
        if stripped.startswith("#"):
            kept_lines.append(line)
            continue
        if ":" not in stripped:
            kept_lines.append(line)
            continue
        id_token, _path = stripped.split(":", 1)
        if id_token.strip() == str(project_id):
            continue
        kept_lines.append(line)

    for path in paths:
        kept_lines.append("{}:{}".format(project_id, path))

    rtnval = "\n".join(kept_lines)
    if len(rtnval) > 0 and not rtnval.endswith("\n"):
        rtnval = rtnval + "\n"
    return rtnval


def _upsert_projid_lines(
    *,
    existing: str,
    project_id: int,
    project_name: str,
) -> str:
    """
        Return new ``/etc/projid`` content with a single
        ``<name>:<id>`` line, replacing any prior line for either
        ``project_name`` or ``project_id``.

        :param existing: Current content.
        :param project_id: Project id.
        :param project_name: Project name.
    """
    kept_lines: list[str] = []
    for raw_line in existing.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if len(stripped) == 0:
            kept_lines.append(line)
            continue
        if stripped.startswith("#"):
            kept_lines.append(line)
            continue
        if ":" not in stripped:
            kept_lines.append(line)
            continue
        name_token, id_token = stripped.split(":", 1)
        if name_token.strip() == project_name:
            continue
        if id_token.strip() == str(project_id):
            continue
        kept_lines.append(line)

    kept_lines.append("{}:{}".format(project_name, project_id))

    rtnval = "\n".join(kept_lines)
    if len(rtnval) > 0 and not rtnval.endswith("\n"):
        rtnval = rtnval + "\n"
    return rtnval


def _strip_projects_lines_for_id(
    *,
    existing: str,
    project_id: int,
) -> str:
    """
        Return new ``/etc/projects`` content with every line whose
        id matches ``project_id`` removed.

        :param existing: Current content.
        :param project_id: Project id.
    """
    kept_lines: list[str] = []
    for raw_line in existing.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if len(stripped) == 0:
            kept_lines.append(line)
            continue
        if stripped.startswith("#"):
            kept_lines.append(line)
            continue
        if ":" not in stripped:
            kept_lines.append(line)
            continue
        id_token, _path = stripped.split(":", 1)
        if id_token.strip() == str(project_id):
            continue
        kept_lines.append(line)

    rtnval = "\n".join(kept_lines)
    if len(rtnval) > 0 and not rtnval.endswith("\n"):
        rtnval = rtnval + "\n"
    return rtnval


def _strip_projid_line_for_name(
    *,
    existing: str,
    project_name: str,
) -> str:
    """
        Return new ``/etc/projid`` content with the line for
        ``project_name`` removed.

        :param existing: Current content.
        :param project_name: Project name.
    """
    kept_lines: list[str] = []
    for raw_line in existing.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if len(stripped) == 0:
            kept_lines.append(line)
            continue
        if stripped.startswith("#"):
            kept_lines.append(line)
            continue
        if ":" not in stripped:
            kept_lines.append(line)
            continue
        name_token, _id = stripped.split(":", 1)
        if name_token.strip() == project_name:
            continue
        kept_lines.append(line)

    rtnval = "\n".join(kept_lines)
    if len(rtnval) > 0 and not rtnval.endswith("\n"):
        rtnval = rtnval + "\n"
    return rtnval


def _find_project_id_for_name(
    *,
    projid_content: str,
    project_name: str,
) -> Optional[int]:
    """
        Return the integer project id for ``project_name`` from
        ``/etc/projid`` content, or ``None`` when the name is not
        defined.

        :param projid_content: File content.
        :param project_name: Name to look up.
    """
    for raw_line in projid_content.splitlines():
        stripped = raw_line.strip()
        if len(stripped) == 0:
            continue
        if stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        name_token, id_token = stripped.split(":", 1)
        if name_token.strip() == project_name:
            id_value = id_token.strip()
            if _is_integer_token(token=id_value):
                rtnval: Optional[int] = int(id_value)
                return rtnval
    rtnval = None
    return rtnval


def _merge_project_files(
    *,
    projects_content: str,
    projid_content: str,
) -> list[ProjectDefinition]:
    """
        Merge ``/etc/projects`` (id -> path) and ``/etc/projid``
        (name -> id) into a list of :class:`ProjectDefinition`.

        :param projects_content: Content of ``/etc/projects``.
        :param projid_content: Content of ``/etc/projid``.
    """
    id_to_paths: dict[int, list[str]] = {}
    for raw_line in projects_content.splitlines():
        stripped = raw_line.strip()
        if len(stripped) == 0:
            continue
        if stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        id_token, path_token = stripped.split(":", 1)
        id_clean = id_token.strip()
        if not _is_integer_token(token=id_clean):
            continue
        id_value = int(id_clean)
        if id_value not in id_to_paths:
            id_to_paths[id_value] = []
        id_to_paths[id_value].append(path_token.strip())

    rtnval: list[ProjectDefinition] = []
    for raw_line in projid_content.splitlines():
        stripped = raw_line.strip()
        if len(stripped) == 0:
            continue
        if stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        name_token, id_token = stripped.split(":", 1)
        name_clean = name_token.strip()
        id_clean = id_token.strip()
        if not _is_integer_token(token=id_clean):
            continue
        id_value = int(id_clean)
        paths_for_id = id_to_paths.get(id_value, [])
        definition = ProjectDefinition(
            project_id=id_value,
            project_name=name_clean,
            paths=list(paths_for_id),
        )
        rtnval.append(definition)

    return rtnval


# ----------------------------------------------------------------------
# Tiny shared parsing utilities
# ----------------------------------------------------------------------


def _strip_warning_marker(*, token: str) -> str:
    """
        Strip the trailing ``*`` that ``quota`` appends when the
        soft limit is currently breached.

        :param token: Raw token.
    """
    if token.endswith("*"):
        rtnval = token[:-1]
        return rtnval
    rtnval = token
    return rtnval


def _is_integer_token(*, token: str) -> bool:
    """
        Return True when ``token`` parses as a plain integer
        (optionally signed).

        :param token: Token to test.
    """
    if len(token) == 0:
        rtnval = False
        return rtnval

    if token[0] in "+-":
        body = token[1:]
    else:
        body = token

    if len(body) == 0:
        rtnval = False
        return rtnval

    rtnval = True
    for ch in body:
        if not ch.isdigit():
            rtnval = False
            break
    return rtnval


def _is_repquota_flag_token(*, token: str) -> bool:
    """
        Return True when ``token`` looks like the 2-char repquota
        over-grace flag column (``"--"`` / ``"+-"`` / ``"-+"`` /
        ``"++"``).

        :param token: Token to test.
    """
    if len(token) != 2:
        rtnval = False
        return rtnval
    rtnval = True
    for ch in token:
        if ch not in "+-":
            rtnval = False
            break
    return rtnval


def _int_or_zero(*, token: str) -> int:
    """
        Parse ``token`` as an integer; return 0 for non-numeric
        tokens.

        :param token: Token to convert.
    """
    if len(token) == 0:
        rtnval = 0
        return rtnval
    try:
        rtnval = int(token)
    except ValueError:
        rtnval = 0
    return rtnval
