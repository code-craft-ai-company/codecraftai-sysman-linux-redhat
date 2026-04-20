# automojo.sysman.linux.redhat.packagemanagement
#
# PackageManager that wraps dnf/rpm commands through the ICommandRunner
# abstraction.  The class never invokes subprocess directly; all system
# interaction is performed through the injected runner.

import os

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from automojo.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureConfig,
)
from automojo.sysman.linux.redhat.runners.models import RunResult


PackageActionStatus = ActionStatus


@dataclass
class PackageManagerConfig(FeatureConfig):
    """
        Configuration for :class:`PackageManager`.  Inherits
        ``dry_run`` and ``elevated`` from :class:`FeatureConfig`.
    """
    pass


@dataclass
class PackageInfo:
    """
        Basic information about an installed package as reported by rpm.

        :param name: Package name.
        :param version: Upstream version string.
        :param release: Distribution release string.
        :param arch: Architecture identifier.
    """
    name: str
    version: str
    release: str
    arch: str


@dataclass
class PackageUpdateInfo:
    """
        Update record describing an available upgrade for an installed package.

        :param name: Package name.
        :param installed_version: Currently installed version.
        :param available_version: Version available in the enabled repositories.
        :param arch: Architecture identifier.
    """
    name: str
    installed_version: str
    available_version: str
    arch: str


@dataclass
class PackageActionResult:
    """
        Result returned by write operations such as install, remove, update,
        hold, and group install/remove.

        :param status: Outcome of the action.
        :param package_name: Package the action targeted.
        :param version: Resolved version after the action, if known.
        :param exit_code: Raw exit code from the command runner.
        :param stdout: Standard output captured from the runner.
        :param stderr: Standard error captured from the runner.
        :param command: The command that was (or would have been) executed.
    """

    status: PackageActionStatus
    package_name: str
    version: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


class PackageQueryError(Exception):
    """
        Raised when a query command returns an unexpected exit code.

        :param command: The command that was executed.
        :param exit_code: The exit code returned by the runner.
        :param stderr: Standard error captured from the runner.
    """

    def __init__(
        self,
        *,
        command: list[str],
        exit_code: int,
        stderr: str,
    ) -> None:
        self.command = command
        self.exit_code = exit_code
        self.stderr = stderr

        err_msg_lines = [
            "Package query command failed."
        ]
        err_msg_lines.append("    COMMAND: {}".format(" ".join(command)))
        err_msg_lines.append("    EXIT_CODE: {}".format(exit_code))
        err_msg_lines.append("    STDERR: {}".format(stderr))
        err_msg = os.linesep.join(err_msg_lines)
        super().__init__(err_msg)
        return


class PackageFileNotFoundError(Exception):
    """
        Raised when :meth:`PackageManager.install_local` is given a non-existent path.

        :param filepath: The path that was not found.
    """

    def __init__(self, *, filepath: str) -> None:
        self.filepath = filepath
        err_msg_lines = [
            "Local package file not found."
        ]
        err_msg_lines.append("    FILEPATH: {}".format(filepath))
        err_msg = os.linesep.join(err_msg_lines)
        super().__init__(err_msg)
        return


_DNF = "dnf"
_RPM = "rpm"
_QUERY_FORMAT = "%{NAME} %{VERSION} %{RELEASE} %{ARCH}\n"


class PackageManager(BaseFeatureManager):
    """
        High-level facade for Red Hat package management.  Keeps its
        own :class:`PackageActionResult` shape (``package_name``
        required) so does not use the base class's ``_dispatch``;
        inherits only the standard ``__init__`` / ``config`` /
        ``runner`` surface.
    """

    feature_name = "package-management"

    def install(self, name: str, version: Optional[str] = None) -> PackageActionResult:
        """
            Install a package, optionally at a specified version.

            :param name: Package name.
            :param version: Optional version string.
        """
        existing = self.query(name)
        if existing is not None:
            if version is None or existing.version == version:
                already_installed = PackageActionResult(
                    status=PackageActionStatus.SKIPPED,
                    package_name=name,
                    version=existing.version,
                )
                return already_installed

        target = name
        if version is not None:
            target = "{}-{}".format(name, version)
        command = [_DNF, "install", "-y", target]

        if self._config.dry_run:
            dry = PackageActionResult(
                status=PackageActionStatus.DRY_RUN,
                package_name=name,
                version=version,
                command=command,
            )
            return dry

        run_result = self._runner.run(command, elevated=self._config.elevated)
        install_result = self._result_from_run(
            name=name, version=version, run_result=run_result
        )
        return install_result

    def remove(self, name: str) -> PackageActionResult:
        """
            Remove an installed package.
        """
        existing = self.query(name)
        if existing is None:
            skipped = PackageActionResult(
                status=PackageActionStatus.SKIPPED,
                package_name=name,
            )
            return skipped

        command = [_DNF, "remove", "-y", name]

        if self._config.dry_run:
            dry = PackageActionResult(
                status=PackageActionStatus.DRY_RUN,
                package_name=name,
                command=command,
            )
            return dry

        run_result = self._runner.run(command, elevated=self._config.elevated)
        remove_result = self._result_from_run(
            name=name, version=None, run_result=run_result
        )
        return remove_result

    def update(self, name: str, version: Optional[str] = None) -> PackageActionResult:
        """
            Update a package, optionally to a specified version.
        """
        existing = self.query(name)
        if existing is not None and version is not None and existing.version == version:
            already_at_version = PackageActionResult(
                status=PackageActionStatus.SKIPPED,
                package_name=name,
                version=version,
            )
            return already_at_version

        if version is None:
            command = [_DNF, "update", "-y", name]
        else:
            target = "{}-{}".format(name, version)
            command = [_DNF, "install", "-y", target]

        if self._config.dry_run:
            dry = PackageActionResult(
                status=PackageActionStatus.DRY_RUN,
                package_name=name,
                version=version,
                command=command,
            )
            return dry

        run_result = self._runner.run(command, elevated=self._config.elevated)
        update_result = self._result_from_run(
            name=name, version=version, run_result=run_result
        )
        return update_result

    def query(self, name: str) -> Optional[PackageInfo]:
        """
            Return :class:`PackageInfo` for an installed package, or None.

            :raises PackageQueryError: When rpm fails for reasons other than
                                       the package being absent.
        """
        command = [_RPM, "-q", "--queryformat", _QUERY_FORMAT, name]
        run_result = self._runner.run(command, elevated=False)

        if run_result.exit_code != 0:
            stdout_lower = run_result.stdout.lower()
            stderr_lower = run_result.stderr.lower()
            is_missing = (
                "not installed" in stdout_lower
                or "not installed" in stderr_lower
            )
            if is_missing:
                return None
            raise PackageQueryError(
                command=command,
                exit_code=run_result.exit_code,
                stderr=run_result.stderr,
            )

        info = self._parse_query_output(run_result.stdout)
        return info

    def list_updates(self) -> list[PackageUpdateInfo]:
        """
            List packages with available updates as reported by dnf.

            :raises PackageQueryError: When dnf returns an unexpected exit code.
        """
        command = [_DNF, "check-update"]
        run_result = self._runner.run(command, elevated=False)

        if run_result.exit_code not in (0, 100):
            raise PackageQueryError(
                command=command,
                exit_code=run_result.exit_code,
                stderr=run_result.stderr,
            )

        if run_result.exit_code == 0:
            return []

        updates = self._parse_check_update_output(run_result.stdout)
        return updates

    def install_local(self, filepath: str) -> PackageActionResult:
        """
            Install a package from a local RPM file path.

            :raises PackageFileNotFoundError: When the file does not exist.
        """
        path = Path(filepath)
        if not path.exists():
            raise PackageFileNotFoundError(filepath=filepath)

        command = [_DNF, "install", "-y", filepath]

        if self._config.dry_run:
            dry = PackageActionResult(
                status=PackageActionStatus.DRY_RUN,
                package_name=filepath,
                command=command,
            )
            return dry

        run_result = self._runner.run(command, elevated=self._config.elevated)
        result = self._result_from_run(
            name=filepath, version=None, run_result=run_result
        )
        return result

    def hold(self, name: str) -> PackageActionResult:
        """
            Pin a package version using dnf versionlock.
        """
        if self._is_held(name):
            already_held = PackageActionResult(
                status=PackageActionStatus.SKIPPED,
                package_name=name,
            )
            return already_held

        command = [_DNF, "versionlock", "add", name]

        if self._config.dry_run:
            dry = PackageActionResult(
                status=PackageActionStatus.DRY_RUN,
                package_name=name,
                command=command,
            )
            return dry

        run_result = self._runner.run(command, elevated=self._config.elevated)
        hold_result = self._result_from_run(
            name=name, version=None, run_result=run_result
        )
        return hold_result

    def release_hold(self, name: str) -> PackageActionResult:
        """
            Remove a dnf versionlock pin.
        """
        if not self._is_held(name):
            skipped = PackageActionResult(
                status=PackageActionStatus.SKIPPED,
                package_name=name,
            )
            return skipped

        command = [_DNF, "versionlock", "delete", name]

        if self._config.dry_run:
            dry = PackageActionResult(
                status=PackageActionStatus.DRY_RUN,
                package_name=name,
                command=command,
            )
            return dry

        run_result = self._runner.run(command, elevated=self._config.elevated)
        release_result = self._result_from_run(
            name=name, version=None, run_result=run_result
        )
        return release_result

    def group_install(self, group_name: str) -> PackageActionResult:
        """
            Install a dnf package group.
        """
        command = [_DNF, "groupinstall", "-y", group_name]

        if self._config.dry_run:
            dry = PackageActionResult(
                status=PackageActionStatus.DRY_RUN,
                package_name=group_name,
                command=command,
            )
            return dry

        run_result = self._runner.run(command, elevated=self._config.elevated)
        install_result = self._result_from_run(
            name=group_name, version=None, run_result=run_result
        )
        return install_result

    def group_remove(self, group_name: str) -> PackageActionResult:
        """
            Remove a dnf package group.
        """
        command = [_DNF, "groupremove", "-y", group_name]

        if self._config.dry_run:
            dry = PackageActionResult(
                status=PackageActionStatus.DRY_RUN,
                package_name=group_name,
                command=command,
            )
            return dry

        run_result = self._runner.run(command, elevated=self._config.elevated)
        remove_result = self._result_from_run(
            name=group_name, version=None, run_result=run_result
        )
        return remove_result

    def _is_held(self, name: str) -> bool:
        """
            Determine whether a versionlock entry exists for the given package.
        """
        command = [_DNF, "versionlock", "list"]
        run_result = self._runner.run(command, elevated=False)
        if run_result.exit_code != 0:
            return False

        for raw_line in run_result.stdout.splitlines():
            line = raw_line.strip()
            if len(line) == 0:
                continue
            if line.startswith(name):
                return True
        return False

    def _parse_query_output(self, stdout: str) -> Optional[PackageInfo]:
        """
            Parse rpm -q queryformat output into a :class:`PackageInfo`.
        """
        for raw_line in stdout.splitlines():
            line = raw_line.strip()
            if len(line) == 0:
                continue
            parts = line.split()
            if len(parts) != 4:
                continue
            pkg_name, pkg_version, pkg_release, pkg_arch = parts
            info = PackageInfo(
                name=pkg_name,
                version=pkg_version,
                release=pkg_release,
                arch=pkg_arch,
            )
            return info
        return None

    def _parse_check_update_output(self, stdout: str) -> list[PackageUpdateInfo]:
        """
            Parse the output of 'dnf check-update' into update records.
        """
        updates: list[PackageUpdateInfo] = []
        for raw_line in stdout.splitlines():
            line = raw_line.strip()
            if len(line) == 0:
                continue
            if line.startswith("Last metadata"):
                continue
            if line.startswith("Obsoleting"):
                break
            parts = line.split()
            if len(parts) < 3:
                continue
            name_arch = parts[0]
            available_version = parts[1]
            if "." not in name_arch:
                continue
            name_part, _, arch_part = name_arch.rpartition(".")
            update = PackageUpdateInfo(
                name=name_part,
                installed_version="",
                available_version=available_version,
                arch=arch_part,
            )
            updates.append(update)
        return updates

    def _result_from_run(
        self,
        *,
        name: str,
        version: Optional[str],
        run_result: RunResult,
    ) -> PackageActionResult:
        """
            Map a :class:`RunResult` to a :class:`PackageActionResult`.
        """
        if run_result.exit_code == 0:
            status = PackageActionStatus.SUCCESS
        else:
            status = PackageActionStatus.FAILED

        action_result = PackageActionResult(
            status=status,
            package_name=name,
            version=version,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
        return action_result
