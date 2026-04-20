# automojo.sysman.linux.redhat.repositorymanagement
#
# RepositoryManager handles dnf repository definitions and metadata
# operations through the ICommandRunner abstraction.

import configparser
import io

from dataclasses import dataclass
from typing import Optional

from automojo.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureConfig,
)


RepoActionStatus = ActionStatus


@dataclass
class RepositoryManagerConfig(FeatureConfig):
    """
        Configuration for :class:`RepositoryManager`.  Inherits
        ``dry_run`` and ``elevated`` from :class:`FeatureConfig`.

        :param repos_dir: Directory containing ``.repo`` files.
    """
    repos_dir: str = "/etc/yum.repos.d"


@dataclass
class RepoDefinition:
    """
        Specification for a dnf repository.

        :param repo_id: Unique repository identifier.
        :param name: Human-readable repository name.
        :param enabled: Initial enabled state.
        :param gpg_check: Whether to enforce GPG signature checking.
        :param base_url: Base URL for package downloads.
        :param mirror_list: URL of a mirror list.
        :param gpg_key: URL or path to the GPG key.
        :param priority: Optional repository priority.
    """
    repo_id: str
    name: str
    enabled: bool
    gpg_check: bool
    base_url: Optional[str] = None
    mirror_list: Optional[str] = None
    gpg_key: Optional[str] = None
    priority: Optional[int] = None


@dataclass
class RepoInfo:
    """
        Summary information for a configured repository.
    """
    repo_id: str
    name: str
    enabled: bool


@dataclass
class RepoActionResult:
    """
        Outcome of a repository management action.
    """
    status: RepoActionStatus
    repo_id: str
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None
    file_path: Optional[str] = None


class RepositoryManager(BaseFeatureManager):
    """
        Facade for dnf repository management through the command runner.
        Uses its own :class:`RepoActionResult` (``repo_id`` required,
        ``file_path`` extra) so does not leverage the base class's
        ``_dispatch`` helper; inherits only the standard ``__init__``
        / ``config`` / ``runner`` surface.
    """

    feature_name = "repository-management"

    def add(self, repo_def: RepoDefinition) -> RepoActionResult:
        """
            Write a ``.repo`` file for ``repo_def``.  Skipped when identical.
        """
        desired_content = _render_repo_file(repo_def=repo_def)
        path = self._repo_file_path(repo_id=repo_def.repo_id)
        existing = self._read_file(path=path)
        if existing == desired_content:
            skipped = RepoActionResult(
                status=RepoActionStatus.SKIPPED,
                repo_id=repo_def.repo_id,
                file_path=path,
            )
            return skipped
        return self._write_file(
            path=path,
            content=desired_content,
            repo_id=repo_def.repo_id,
        )

    def remove(self, repo_id: str) -> RepoActionResult:
        """
            Delete the ``.repo`` file for ``repo_id``.
        """
        path = self._repo_file_path(repo_id=repo_id)
        existing = self._read_file(path=path)
        if len(existing) == 0:
            skipped = RepoActionResult(
                status=RepoActionStatus.SKIPPED,
                repo_id=repo_id,
                file_path=path,
            )
            return skipped

        command = ["rm", "-f", path]
        if self._config.dry_run:
            dry = RepoActionResult(
                status=RepoActionStatus.DRY_RUN,
                repo_id=repo_id,
                command=command,
                file_path=path,
            )
            return dry
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = RepoActionStatus.SUCCESS if run_result.exit_code == 0 else RepoActionStatus.FAILED
        result = RepoActionResult(
            status=status,
            repo_id=repo_id,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
            file_path=path,
        )
        return result

    def enable(self, repo_id: str) -> RepoActionResult:
        """
            Enable a repository with ``dnf config-manager --set-enabled``.
        """
        command = ["dnf", "config-manager", "--set-enabled", repo_id]
        return self._run_config_manager(command=command, repo_id=repo_id)

    def disable(self, repo_id: str) -> RepoActionResult:
        """
            Disable a repository with ``dnf config-manager --set-disabled``.
        """
        command = ["dnf", "config-manager", "--set-disabled", repo_id]
        return self._run_config_manager(command=command, repo_id=repo_id)

    def refresh(self, repo_id: Optional[str] = None) -> RepoActionResult:
        """
            Clean and rebuild dnf metadata cache.
        """
        clean_command: list[str] = ["dnf", "clean", "metadata"]
        if repo_id is not None:
            clean_command = ["dnf", "--disablerepo=*", "--enablerepo", repo_id,
                             "clean", "metadata"]
        makecache_command: list[str] = ["dnf", "makecache"]
        if repo_id is not None:
            makecache_command = ["dnf", "--disablerepo=*", "--enablerepo",
                                 repo_id, "makecache"]

        if self._config.dry_run:
            dry = RepoActionResult(
                status=RepoActionStatus.DRY_RUN,
                repo_id=repo_id if repo_id is not None else "*",
                command=makecache_command,
            )
            return dry

        clean_result = self._runner.run(
            clean_command, elevated=self._config.elevated,
        )
        if clean_result.exit_code != 0:
            fail = RepoActionResult(
                status=RepoActionStatus.FAILED,
                repo_id=repo_id if repo_id is not None else "*",
                exit_code=clean_result.exit_code,
                stdout=clean_result.stdout,
                stderr=clean_result.stderr,
                command=list(clean_result.command),
            )
            return fail

        make_result = self._runner.run(
            makecache_command, elevated=self._config.elevated,
        )
        status = RepoActionStatus.SUCCESS if make_result.exit_code == 0 else RepoActionStatus.FAILED
        result = RepoActionResult(
            status=status,
            repo_id=repo_id if repo_id is not None else "*",
            exit_code=make_result.exit_code,
            stdout=make_result.stdout,
            stderr=make_result.stderr,
            command=list(make_result.command),
        )
        return result

    def list_repos(self) -> list[RepoInfo]:
        """
            List all configured repositories and their state.
        """
        command = ["dnf", "repolist", "--all"]
        run_result = self._runner.run(command, elevated=False)
        if run_result.exit_code != 0:
            return []
        repos = _parse_repolist_output(stdout=run_result.stdout)
        return repos

    def set_priority(self, repo_id: str, priority: int) -> RepoActionResult:
        """
            Update the ``priority=`` field in the ``.repo`` file for ``repo_id``.
        """
        path = self._repo_file_path(repo_id=repo_id)
        existing = self._read_file(path=path)
        if len(existing) == 0:
            fail = RepoActionResult(
                status=RepoActionStatus.FAILED,
                repo_id=repo_id,
                stderr="repository file not found",
                file_path=path,
            )
            return fail

        parser = configparser.ConfigParser()
        parser.read_string(existing)
        if not parser.has_section(repo_id):
            fail = RepoActionResult(
                status=RepoActionStatus.FAILED,
                repo_id=repo_id,
                stderr="repository section missing",
                file_path=path,
            )
            return fail
        current_priority = parser.get(repo_id, "priority", fallback=None)
        new_value = str(priority)
        if current_priority == new_value:
            return RepoActionResult(
                status=RepoActionStatus.SKIPPED,
                repo_id=repo_id,
                file_path=path,
            )
        parser.set(repo_id, "priority", new_value)
        buf = io.StringIO()
        parser.write(buf, space_around_delimiters=False)
        new_content = buf.getvalue()
        return self._write_file(
            path=path, content=new_content, repo_id=repo_id,
        )

    def import_gpg_key(self, key_source: str) -> RepoActionResult:
        """
            Import a GPG key using ``rpm --import``.
        """
        command = ["rpm", "--import", key_source]
        if self._config.dry_run:
            dry = RepoActionResult(
                status=RepoActionStatus.DRY_RUN,
                repo_id=key_source,
                command=command,
            )
            return dry
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = RepoActionStatus.SUCCESS if run_result.exit_code == 0 else RepoActionStatus.FAILED
        result = RepoActionResult(
            status=status,
            repo_id=key_source,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
        return result

    def _repo_file_path(self, *, repo_id: str) -> str:
        """
            Build the absolute ``.repo`` file path for ``repo_id``.
        """
        return "{}/{}.repo".format(self._config.repos_dir, repo_id)

    def _read_file(self, *, path: str) -> str:
        """
            Read a file via ``cat``, returning empty string when missing.
        """
        run_result = self._runner.run(["cat", path], elevated=False)
        if run_result.exit_code != 0:
            return ""
        return run_result.stdout

    def _write_file(
        self,
        *,
        path: str,
        content: str,
        repo_id: str,
    ) -> RepoActionResult:
        """
            Write content to ``path`` via ``tee`` honouring dry-run.
        """
        command = ["tee", path]
        if self._config.dry_run:
            dry = RepoActionResult(
                status=RepoActionStatus.DRY_RUN,
                repo_id=repo_id,
                command=command,
                file_path=path,
            )
            return dry
        run_result = self._runner.run(
            command,
            elevated=self._config.elevated,
            stdin=content,
        )
        status = RepoActionStatus.SUCCESS if run_result.exit_code == 0 else RepoActionStatus.FAILED
        result = RepoActionResult(
            status=status,
            repo_id=repo_id,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
            file_path=path,
        )
        return result

    def _run_config_manager(
        self,
        *,
        command: list[str],
        repo_id: str,
    ) -> RepoActionResult:
        """
            Dispatch a ``dnf config-manager`` command.
        """
        if self._config.dry_run:
            dry = RepoActionResult(
                status=RepoActionStatus.DRY_RUN,
                repo_id=repo_id,
                command=command,
            )
            return dry
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = RepoActionStatus.SUCCESS if run_result.exit_code == 0 else RepoActionStatus.FAILED
        result = RepoActionResult(
            status=status,
            repo_id=repo_id,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
        return result


def _render_repo_file(*, repo_def: RepoDefinition) -> str:
    """
        Render a :class:`RepoDefinition` into a ``.repo`` file body.
    """
    lines = ["[{}]".format(repo_def.repo_id)]
    lines.append("name={}".format(repo_def.name))
    if repo_def.base_url is not None:
        lines.append("baseurl={}".format(repo_def.base_url))
    if repo_def.mirror_list is not None:
        lines.append("mirrorlist={}".format(repo_def.mirror_list))
    lines.append("enabled={}".format(1 if repo_def.enabled else 0))
    lines.append("gpgcheck={}".format(1 if repo_def.gpg_check else 0))
    if repo_def.gpg_key is not None:
        lines.append("gpgkey={}".format(repo_def.gpg_key))
    if repo_def.priority is not None:
        lines.append("priority={}".format(repo_def.priority))
    body = "\n".join(lines) + "\n"
    return body


def _parse_repolist_output(*, stdout: str) -> list[RepoInfo]:
    """
        Parse ``dnf repolist --all`` output into a list of :class:`RepoInfo`.
    """
    repos: list[RepoInfo] = []
    data_started = False
    for raw_line in stdout.splitlines():
        line = raw_line.rstrip()
        if len(line) == 0:
            continue
        lowered = line.strip().lower()
        if lowered.startswith("repo id") and "status" in lowered:
            data_started = True
            continue
        if not data_started:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        status_token = parts[-1].lower()
        enabled = status_token == "enabled"
        repo_id = parts[0]
        name_tokens = parts[1:-1]
        name = " ".join(name_tokens) if len(name_tokens) > 0 else repo_id
        repos.append(RepoInfo(repo_id=repo_id, name=name, enabled=enabled))
    return repos
