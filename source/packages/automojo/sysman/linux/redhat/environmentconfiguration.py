# automojo.sysman.linux.redhat.environmentconfiguration
#
# EnvironmentConfigurator manages environment variables set via
# /etc/profile.d/*.sh, /etc/environment, and user rc files.

import os
import re

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.runners.base import ICommandRunner


class EnvActionStatus(Enum):
    """
        Outcome status for environment configuration actions.
    """
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class EnvironmentConfiguratorConfig:
    """
        Configuration for :class:`EnvironmentConfigurator`.

        :param dry_run: When True, no files are written.
        :param elevated: When True, system-wide writes run elevated.
    """
    dry_run: bool = False
    elevated: bool = False


@dataclass
class EnvActionResult:
    """
        Outcome of an environment configuration action.
    """
    status: EnvActionStatus
    path: Optional[str] = None
    name: Optional[str] = None
    value: Optional[str] = None
    message: Optional[str] = None


class EnvConfigError(Exception):
    """
        Raised when a query operation cannot complete (e.g., unknown user).
    """
    def __init__(self, *, message: str) -> None:
        err_msg_lines = ["Environment configuration error."]
        err_msg_lines.append("    DETAIL: {}".format(message))
        err_msg = os.linesep.join(err_msg_lines)
        super().__init__(err_msg)
        return


_EXPORT_LINE_PATTERN = re.compile(r"^export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_ENV_LINE_PATTERN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


class EnvironmentConfigurator:
    """
        Facade for environment variable management across system-wide and
        per-user files.
    """

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[EnvironmentConfiguratorConfig] = None,
    ) -> None:
        """
            :param runner: Command runner for file operations.
            :param config: Configurator configuration.
        """
        if config is None:
            config = EnvironmentConfiguratorConfig()
        self._runner: ICommandRunner = runner
        self._config: EnvironmentConfiguratorConfig = config
        return

    @property
    def config(self) -> EnvironmentConfiguratorConfig:
        """
            The active :class:`EnvironmentConfiguratorConfig`.
        """
        return self._config

    def set_system_var(
        self,
        name: str,
        value: str,
        filename: str,
    ) -> EnvActionResult:
        """
            Set ``export NAME="value"`` in ``/etc/profile.d/<filename>``.
        """
        path = _profile_d_path(filename=filename)
        current = self._read(path=path, elevated=self._config.elevated)
        new_content = _set_export_line(content=current, name=name, value=value)
        if new_content == current and len(current) > 0:
            return EnvActionResult(
                status=EnvActionStatus.SKIPPED,
                path=path, name=name, value=value,
            )
        return self._write(path=path, content=new_content, name=name, value=value)

    def unset_system_var(
        self,
        name: str,
        filename: str,
    ) -> EnvActionResult:
        """
            Remove ``export NAME=...`` from ``/etc/profile.d/<filename>``.
            If the file becomes empty the file is deleted.
        """
        path = _profile_d_path(filename=filename)
        current = self._read(path=path, elevated=self._config.elevated)
        if len(current) == 0:
            return EnvActionResult(
                status=EnvActionStatus.SKIPPED,
                path=path, name=name,
            )
        new_content = _remove_export_line(content=current, name=name)
        if new_content == current:
            return EnvActionResult(
                status=EnvActionStatus.SKIPPED,
                path=path, name=name,
            )

        if _is_empty_profile(content=new_content):
            return self._delete(path=path, name=name)
        return self._write(path=path, content=new_content, name=name, value=None)

    def set_etc_env_var(self, name: str, value: str) -> EnvActionResult:
        """
            Set ``NAME="value"`` in ``/etc/environment``.
        """
        path = "/etc/environment"
        current = self._read(path=path, elevated=self._config.elevated)
        new_content = _set_env_line(content=current, name=name, value=value)
        if new_content == current and len(current) > 0:
            return EnvActionResult(
                status=EnvActionStatus.SKIPPED,
                path=path, name=name, value=value,
            )
        return self._write(path=path, content=new_content, name=name, value=value)

    def unset_etc_env_var(self, name: str) -> EnvActionResult:
        """
            Remove ``NAME=...`` from ``/etc/environment``.
        """
        path = "/etc/environment"
        current = self._read(path=path, elevated=self._config.elevated)
        if len(current) == 0:
            return EnvActionResult(
                status=EnvActionStatus.SKIPPED,
                path=path, name=name,
            )
        new_content = _remove_env_line(content=current, name=name)
        if new_content == current:
            return EnvActionResult(
                status=EnvActionStatus.SKIPPED,
                path=path, name=name,
            )
        return self._write(path=path, content=new_content, name=name, value=None)

    def set_user_var(
        self,
        username: str,
        name: str,
        value: str,
        rc_file: str,
    ) -> EnvActionResult:
        """
            Set ``export NAME="value"`` in ``~<username>/<rc_file>``.
        """
        path = _user_rc_path(username=username, rc_file=rc_file, runner=self._runner)
        current = self._read(path=path, elevated=False)
        new_content = _set_export_line(content=current, name=name, value=value)
        if new_content == current and len(current) > 0:
            return EnvActionResult(
                status=EnvActionStatus.SKIPPED,
                path=path, name=name, value=value,
            )
        return self._write(path=path, content=new_content, name=name, value=value)

    def unset_user_var(
        self,
        username: str,
        name: str,
        rc_file: str,
    ) -> EnvActionResult:
        """
            Remove ``export NAME=...`` from ``~<username>/<rc_file>``.
        """
        path = _user_rc_path(username=username, rc_file=rc_file, runner=self._runner)
        current = self._read(path=path, elevated=False)
        if len(current) == 0:
            return EnvActionResult(
                status=EnvActionStatus.SKIPPED,
                path=path, name=name,
            )
        new_content = _remove_export_line(content=current, name=name)
        if new_content == current:
            return EnvActionResult(
                status=EnvActionStatus.SKIPPED,
                path=path, name=name,
            )
        return self._write(path=path, content=new_content, name=name, value=None)

    def query_system(self, filename: str) -> dict[str, str]:
        """
            Read environment variables from ``/etc/profile.d/<filename>``.
        """
        path = _profile_d_path(filename=filename)
        content = self._read(path=path, elevated=self._config.elevated)
        return _parse_export_lines(text=content)

    def query_etc_env(self) -> dict[str, str]:
        """
            Read environment variables from ``/etc/environment``.
        """
        content = self._read(path="/etc/environment", elevated=self._config.elevated)
        return _parse_env_lines(text=content)

    def query_user(self, username: str, rc_file: str) -> dict[str, str]:
        """
            Read environment variables from ``~<username>/<rc_file>``.
        """
        path = _user_rc_path(username=username, rc_file=rc_file, runner=self._runner)
        content = self._read(path=path, elevated=False)
        return _parse_export_lines(text=content)

    def _read(self, *, path: str, elevated: bool) -> str:
        """
            Read a file via ``cat`` and return its contents (or empty).
        """
        run_result = self._runner.run(["cat", path], elevated=elevated)
        if run_result.exit_code != 0:
            return ""
        return run_result.stdout

    def _write(
        self,
        *,
        path: str,
        content: str,
        name: Optional[str],
        value: Optional[str],
    ) -> EnvActionResult:
        """
            Write a file via ``tee`` with content on stdin.
        """
        if self._config.dry_run:
            return EnvActionResult(
                status=EnvActionStatus.DRY_RUN,
                path=path, name=name, value=value,
            )
        run_result = self._runner.run(
            ["tee", path],
            elevated=self._config.elevated,
            stdin=content,
        )
        if run_result.exit_code != 0:
            return EnvActionResult(
                status=EnvActionStatus.FAILED,
                path=path, name=name, value=value,
                message=run_result.stderr,
            )
        return EnvActionResult(
            status=EnvActionStatus.SUCCESS,
            path=path, name=name, value=value,
        )

    def _delete(
        self,
        *,
        path: str,
        name: str,
    ) -> EnvActionResult:
        """
            Delete the specified file via ``rm -f``.
        """
        if self._config.dry_run:
            return EnvActionResult(
                status=EnvActionStatus.DRY_RUN,
                path=path, name=name,
            )
        run_result = self._runner.run(
            ["rm", "-f", path],
            elevated=self._config.elevated,
        )
        if run_result.exit_code != 0:
            return EnvActionResult(
                status=EnvActionStatus.FAILED,
                path=path, name=name,
                message=run_result.stderr,
            )
        return EnvActionResult(
            status=EnvActionStatus.SUCCESS,
            path=path, name=name,
        )


def _profile_d_path(*, filename: str) -> str:
    """
        Build the absolute profile.d path for the given filename.
    """
    return "/etc/profile.d/{}".format(filename)


def _user_rc_path(
    *,
    username: str,
    rc_file: str,
    runner: ICommandRunner,
) -> str:
    """
        Return the absolute path to a user's rc file, using getent passwd to
        discover the home directory.
    """
    run_result = runner.run(["getent", "passwd", username], elevated=False)
    if run_result.exit_code != 0:
        raise EnvConfigError(message="user not found: {}".format(username))
    for line in run_result.stdout.splitlines():
        stripped = line.strip()
        if len(stripped) == 0:
            continue
        fields = stripped.split(":")
        if len(fields) < 6:
            continue
        home = fields[5]
        return "{}/{}".format(home, rc_file)
    raise EnvConfigError(message="getent returned no row for {}".format(username))


def _set_export_line(*, content: str, name: str, value: str) -> str:
    """
        Ensure the file contains a single ``export NAME="value"`` line.
    """
    escaped_value = value.replace("\\", "\\\\").replace('"', '\\"')
    new_line = 'export {}="{}"'.format(name, escaped_value)

    if len(content) == 0:
        body = "#!/usr/bin/env bash\n{}\n".format(new_line)
        return body

    lines = content.splitlines()
    updated: list[str] = []
    replaced = False
    for line in lines:
        match = _EXPORT_LINE_PATTERN.match(line.strip())
        if match is not None and match.group(1) == name:
            updated.append(new_line)
            replaced = True
            continue
        updated.append(line)

    if not replaced:
        updated.append(new_line)

    body = "\n".join(updated)
    if not body.endswith("\n"):
        body = body + "\n"
    return body


def _remove_export_line(*, content: str, name: str) -> str:
    """
        Return the file content with any matching export line removed.
    """
    lines = content.splitlines()
    kept: list[str] = []
    for line in lines:
        match = _EXPORT_LINE_PATTERN.match(line.strip())
        if match is not None and match.group(1) == name:
            continue
        kept.append(line)

    body = "\n".join(kept)
    if len(body) > 0 and not body.endswith("\n"):
        body = body + "\n"
    return body


def _set_env_line(*, content: str, name: str, value: str) -> str:
    """
        Ensure ``NAME="value"`` is present as a single line in env format.
    """
    escaped_value = value.replace("\\", "\\\\").replace('"', '\\"')
    new_line = '{}="{}"'.format(name, escaped_value)

    if len(content) == 0:
        return new_line + "\n"

    lines = content.splitlines()
    updated: list[str] = []
    replaced = False
    for line in lines:
        match = _ENV_LINE_PATTERN.match(line.strip())
        if match is not None and match.group(1) == name:
            updated.append(new_line)
            replaced = True
            continue
        updated.append(line)

    if not replaced:
        updated.append(new_line)

    body = "\n".join(updated)
    if not body.endswith("\n"):
        body = body + "\n"
    return body


def _remove_env_line(*, content: str, name: str) -> str:
    """
        Return the file content with any matching ``NAME=...`` line removed.
    """
    lines = content.splitlines()
    kept: list[str] = []
    for line in lines:
        match = _ENV_LINE_PATTERN.match(line.strip())
        if match is not None and match.group(1) == name:
            continue
        kept.append(line)

    body = "\n".join(kept)
    if len(body) > 0 and not body.endswith("\n"):
        body = body + "\n"
    return body


def _parse_export_lines(*, text: str) -> dict[str, str]:
    """
        Parse ``export NAME="value"`` lines into a dict.
    """
    parsed: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if len(line) == 0 or line.startswith("#"):
            continue
        match = _EXPORT_LINE_PATTERN.match(line)
        if match is None:
            continue
        name = match.group(1)
        raw_value = match.group(2)
        parsed[name] = _strip_quotes(value=raw_value)
    return parsed


def _parse_env_lines(*, text: str) -> dict[str, str]:
    """
        Parse ``NAME="value"`` lines into a dict.
    """
    parsed: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if len(line) == 0 or line.startswith("#"):
            continue
        match = _ENV_LINE_PATTERN.match(line)
        if match is None:
            continue
        name = match.group(1)
        raw_value = match.group(2)
        parsed[name] = _strip_quotes(value=raw_value)
    return parsed


def _strip_quotes(*, value: str) -> str:
    """
        Strip a single pair of surrounding double quotes from ``value``.
    """
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        inner = value[1:-1]
        unescaped = inner.replace('\\"', '"').replace("\\\\", "\\")
        return unescaped
    return value


def _is_empty_profile(*, content: str) -> bool:
    """
        Return True when a profile.d file contains only the shebang and blanks.
    """
    for raw in content.splitlines():
        line = raw.strip()
        if len(line) == 0:
            continue
        if line.startswith("#!"):
            continue
        if line.startswith("#"):
            continue
        return False
    return True
