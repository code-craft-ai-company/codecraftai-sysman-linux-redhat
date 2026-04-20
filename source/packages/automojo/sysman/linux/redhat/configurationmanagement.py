# automojo.sysman.linux.redhat.configurationmanagement
#
# ConfigurationManager handles read/write/diff/backup/restore/validation of
# system configuration files through the ICommandRunner abstraction.  No
# direct filesystem access is performed inside this module; every operation
# is dispatched as a command to the runner.

import configparser
import difflib
import io
import os

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from string import Template
from typing import Callable, Optional

from automojo.sysman.linux.redhat.runners.base import ICommandRunner
from automojo.sysman.linux.redhat.runners.models import RunResult


class ConfigActionStatus(Enum):
    """
        Outcome status for configuration-management actions.
    """
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class ConfigurationManagerConfig:
    """
        Configuration for :class:`ConfigurationManager`.

        :param dry_run: When True, no mutating commands are executed.
        :param elevated: When True, write commands run with elevated privileges.
    """
    dry_run: bool = False
    elevated: bool = False


@dataclass
class ConfigActionResult:
    """
        Outcome of a single configuration action.
    """
    status: ConfigActionStatus
    path: str
    backup_path: Optional[str] = None
    diff: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


@dataclass
class ConfigDiffResult:
    """
        Diff between the current file contents and a desired payload.
    """
    path: str
    has_diff: bool
    diff: str


@dataclass
class ConfigValidationResult:
    """
        Outcome of a syntax-validator invocation against a config file.
    """
    path: str
    validator_id: str
    valid: bool
    exit_code: int
    stdout: str
    stderr: str


class ConfigTemplateError(Exception):
    """
        Raised when a template substitution references an unknown variable.
    """

    def __init__(self, *, missing_keys: list[str]) -> None:
        self.missing_keys = list(missing_keys)
        err_msg_lines = ["Template substitution failed."]
        err_msg_lines.append("    MISSING_KEYS: {}".format(", ".join(missing_keys)))
        err_msg = os.linesep.join(err_msg_lines)
        super().__init__(err_msg)
        return


class ConfigValidatorNotRegisteredError(Exception):
    """
        Raised when a request targets an unregistered validator id.
    """

    def __init__(self, *, validator_id: str) -> None:
        self.validator_id = validator_id
        err_msg_lines = ["Validator is not registered."]
        err_msg_lines.append("    VALIDATOR_ID: {}".format(validator_id))
        err_msg = os.linesep.join(err_msg_lines)
        super().__init__(err_msg)
        return


_DEFAULT_VALIDATORS: dict[str, Callable[[str], list[str]]] = {
    "nginx":   lambda path: ["nginx", "-t", "-c", path],
    "sshd":    lambda path: ["sshd", "-t", "-f", path],
    "httpd":   lambda path: ["httpd", "-t", "-f", path],
    "named":   lambda path: ["named-checkconf", path],
    "rsyslog": lambda path: ["rsyslogd", "-N1", "-f", path],
    "sudoers": lambda path: ["visudo", "-c", "-f", path],
}


class ConfigurationManager:
    """
        Facade for Red Hat configuration-file management through the
        command runner.
    """

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[ConfigurationManagerConfig] = None,
    ) -> None:
        """
            :param runner: Runner used for every filesystem action.
            :param config: Dry-run/elevation configuration.
        """
        if config is None:
            config = ConfigurationManagerConfig()
        self._runner: ICommandRunner = runner
        self._config: ConfigurationManagerConfig = config
        self._validators: dict[str, Callable[[str], list[str]]] = dict(_DEFAULT_VALIDATORS)
        return

    @property
    def config(self) -> ConfigurationManagerConfig:
        """
            The active :class:`ConfigurationManagerConfig`.
        """
        return self._config

    def register_validator(
        self,
        validator_id: str,
        command_builder: Callable[[str], list[str]],
    ) -> None:
        """
            Register a custom validator callable for ``validate()`` lookups.
        """
        self._validators[validator_id] = command_builder
        return

    def write(
        self,
        path: str,
        content: str,
        *,
        mode: Optional[str] = None,
        owner: Optional[str] = None,
        group: Optional[str] = None,
    ) -> ConfigActionResult:
        """
            Write ``content`` to ``path``.  Skipped when the existing content
            matches.  Mode/ownership updates are applied after the write.
        """
        current_content = self._read_file(path=path)
        diff_text = _unified_diff(
            current=current_content, desired=content, path=path,
        )

        if len(diff_text) == 0:
            return self._ensure_metadata(
                path=path,
                mode=mode,
                owner=owner,
                group=group,
                fall_through_status=ConfigActionStatus.SKIPPED,
            )

        if self._config.dry_run:
            dry = ConfigActionResult(
                status=ConfigActionStatus.DRY_RUN,
                path=path,
                diff=diff_text,
                command=["tee", path],
            )
            return dry

        command = ["tee", path]
        run_result = self._runner.run(
            command,
            elevated=self._config.elevated,
            stdin=content,
        )
        if run_result.exit_code != 0:
            failed = _result_from_run(
                path=path, run_result=run_result,
                status=ConfigActionStatus.FAILED,
            )
            return failed

        self._apply_mode_and_owner(
            path=path, mode=mode, owner=owner, group=group,
        )
        success = _result_from_run(
            path=path, run_result=run_result,
            status=ConfigActionStatus.SUCCESS,
        )
        return success

    def write_template(
        self,
        path: str,
        template: str,
        variables: dict[str, str],
        *,
        mode: Optional[str] = None,
        owner: Optional[str] = None,
        group: Optional[str] = None,
    ) -> ConfigActionResult:
        """
            Render ``template`` using ``variables`` and delegate to :meth:`write`.

            :raises ConfigTemplateError: When the template contains unresolved keys.
        """
        rendered = _render_template(template=template, variables=variables)
        result = self.write(
            path=path,
            content=rendered,
            mode=mode,
            owner=owner,
            group=group,
        )
        return result

    def backup(self, path: str) -> ConfigActionResult:
        """
            Create a timestamped copy of ``path``.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        backup_path = "{}.bak.{}".format(path, timestamp)
        command = ["cp", "-p", path, backup_path]

        if self._config.dry_run:
            dry = ConfigActionResult(
                status=ConfigActionStatus.DRY_RUN,
                path=path,
                backup_path=backup_path,
                command=command,
            )
            return dry

        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = ConfigActionStatus.SUCCESS if run_result.exit_code == 0 else ConfigActionStatus.FAILED
        result = ConfigActionResult(
            status=status,
            path=path,
            backup_path=backup_path if status == ConfigActionStatus.SUCCESS else None,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
        return result

    def restore(self, backup_path: str) -> ConfigActionResult:
        """
            Restore a backup file over its original path (strips ``.bak.*``).
        """
        original_path = _strip_backup_suffix(backup_path=backup_path)
        command = ["cp", "-p", backup_path, original_path]

        if self._config.dry_run:
            dry = ConfigActionResult(
                status=ConfigActionStatus.DRY_RUN,
                path=original_path,
                backup_path=backup_path,
                command=command,
            )
            return dry

        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = ConfigActionStatus.SUCCESS if run_result.exit_code == 0 else ConfigActionStatus.FAILED
        result = ConfigActionResult(
            status=status,
            path=original_path,
            backup_path=backup_path,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
        return result

    def diff(self, path: str, desired_content: str) -> ConfigDiffResult:
        """
            Compute a unified diff between current file content and the desired payload.
        """
        current = self._read_file(path=path)
        diff_text = _unified_diff(
            current=current, desired=desired_content, path=path,
        )
        has_diff = len(diff_text) > 0
        result = ConfigDiffResult(
            path=path,
            has_diff=has_diff,
            diff=diff_text,
        )
        return result

    def validate(self, path: str, validator_id: str) -> ConfigValidationResult:
        """
            Run a registered validator command against ``path``.

            :raises ConfigValidatorNotRegisteredError: When ``validator_id`` is unknown.
        """
        if validator_id not in self._validators:
            raise ConfigValidatorNotRegisteredError(validator_id=validator_id)
        builder = self._validators[validator_id]
        command = builder(path)
        run_result = self._runner.run(command, elevated=self._config.elevated)
        valid = run_result.exit_code == 0
        result = ConfigValidationResult(
            path=path,
            validator_id=validator_id,
            valid=valid,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
        )
        return result

    def set_permissions(self, path: str, mode: str) -> ConfigActionResult:
        """
            Apply a chmod to ``path``.
        """
        command = ["chmod", mode, path]
        return self._run_mutation(command=command, path=path)

    def set_ownership(
        self,
        path: str,
        *,
        user: Optional[str] = None,
        group: Optional[str] = None,
    ) -> ConfigActionResult:
        """
            Apply a chown to ``path``.  Either user, group, or both may be set.
        """
        if user is None and group is None:
            skipped = ConfigActionResult(
                status=ConfigActionStatus.SKIPPED,
                path=path,
            )
            return skipped
        spec = _ownership_spec(user=user, group=group)
        command = ["chown", spec, path]
        return self._run_mutation(command=command, path=path)

    def set_ini_key(
        self,
        path: str,
        section: str,
        key: str,
        value: str,
    ) -> ConfigActionResult:
        """
            Ensure ``section``/``key`` is set to ``value`` in an INI file.
        """
        current_content = self._read_file(path=path)
        new_content = _apply_ini_key(
            content=current_content, section=section, key=key, value=value,
        )
        if new_content == current_content:
            skipped = ConfigActionResult(
                status=ConfigActionStatus.SKIPPED,
                path=path,
            )
            return skipped
        result = self.write(path=path, content=new_content)
        return result

    def remove_ini_key(
        self,
        path: str,
        section: str,
        key: str,
    ) -> ConfigActionResult:
        """
            Ensure ``section``/``key`` is absent from an INI file.
        """
        current_content = self._read_file(path=path)
        new_content = _remove_ini_key(
            content=current_content, section=section, key=key,
        )
        if new_content == current_content:
            skipped = ConfigActionResult(
                status=ConfigActionStatus.SKIPPED,
                path=path,
            )
            return skipped
        result = self.write(path=path, content=new_content)
        return result

    def _read_file(self, *, path: str) -> str:
        """
            Read ``path`` via ``cat`` or return an empty string when missing.
        """
        run_result = self._runner.run(["cat", path], elevated=self._config.elevated)
        if run_result.exit_code != 0:
            return ""
        return run_result.stdout

    def _apply_mode_and_owner(
        self,
        *,
        path: str,
        mode: Optional[str],
        owner: Optional[str],
        group: Optional[str],
    ) -> None:
        """
            Apply optional chmod and chown after a successful write.
        """
        if mode is not None:
            self._runner.run(
                ["chmod", mode, path],
                elevated=self._config.elevated,
            )
        if owner is not None or group is not None:
            spec = _ownership_spec(user=owner, group=group)
            self._runner.run(
                ["chown", spec, path],
                elevated=self._config.elevated,
            )
        return

    def _ensure_metadata(
        self,
        *,
        path: str,
        mode: Optional[str],
        owner: Optional[str],
        group: Optional[str],
        fall_through_status: ConfigActionStatus,
    ) -> ConfigActionResult:
        """
            When the file content already matches, still enforce metadata.
        """
        if self._config.dry_run:
            dry = ConfigActionResult(
                status=ConfigActionStatus.DRY_RUN,
                path=path,
            )
            return dry
        self._apply_mode_and_owner(
            path=path, mode=mode, owner=owner, group=group,
        )
        result = ConfigActionResult(
            status=fall_through_status,
            path=path,
        )
        return result

    def _run_mutation(
        self,
        *,
        command: list[str],
        path: str,
    ) -> ConfigActionResult:
        """
            Dispatch a single mutating command and map its result.
        """
        if self._config.dry_run:
            dry = ConfigActionResult(
                status=ConfigActionStatus.DRY_RUN,
                path=path,
                command=command,
            )
            return dry
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = ConfigActionStatus.SUCCESS if run_result.exit_code == 0 else ConfigActionStatus.FAILED
        result = _result_from_run(
            path=path, run_result=run_result, status=status,
        )
        return result


def _ownership_spec(*, user: Optional[str], group: Optional[str]) -> str:
    """
        Build the chown user:group spec string.
    """
    if user is not None and group is not None:
        return "{}:{}".format(user, group)
    if user is not None:
        return user
    return ":{}".format(group)


def _unified_diff(*, current: str, desired: str, path: str) -> str:
    """
        Return a unified diff string between current and desired content.
    """
    current_lines = current.splitlines(keepends=True)
    desired_lines = desired.splitlines(keepends=True)
    diff_iter = difflib.unified_diff(
        current_lines,
        desired_lines,
        fromfile="current:{}".format(path),
        tofile="desired:{}".format(path),
    )
    diff_text = "".join(diff_iter)
    return diff_text


def _render_template(*, template: str, variables: dict[str, str]) -> str:
    """
        Render ``template`` with :class:`string.Template` and raise on missing vars.
    """
    tpl = Template(template)
    rendered = tpl.safe_substitute(variables)
    missing: list[str] = []
    for identifier in tpl.get_identifiers():
        if identifier not in variables:
            missing.append(identifier)
    if len(missing) > 0:
        raise ConfigTemplateError(missing_keys=missing)
    return rendered


def _strip_backup_suffix(*, backup_path: str) -> str:
    """
        Remove a ``.bak.<timestamp>`` suffix from a backup path.
    """
    marker = ".bak."
    idx = backup_path.rfind(marker)
    if idx < 0:
        return backup_path
    original = backup_path[:idx]
    return original


def _apply_ini_key(
    *,
    content: str,
    section: str,
    key: str,
    value: str,
) -> str:
    """
        Return the INI content with ``section``/``key`` set to ``value``.
    """
    parser = configparser.ConfigParser()
    parser.read_string(content)
    if not parser.has_section(section):
        parser.add_section(section)
    parser.set(section, key, value)
    buf = io.StringIO()
    parser.write(buf, space_around_delimiters=False)
    new_content = buf.getvalue()
    return new_content


def _remove_ini_key(
    *,
    content: str,
    section: str,
    key: str,
) -> str:
    """
        Return the INI content with ``section``/``key`` removed.
    """
    parser = configparser.ConfigParser()
    parser.read_string(content)
    if not parser.has_section(section):
        return content
    if not parser.has_option(section, key):
        return content
    parser.remove_option(section, key)
    buf = io.StringIO()
    parser.write(buf, space_around_delimiters=False)
    new_content = buf.getvalue()
    return new_content


def _result_from_run(
    *,
    path: str,
    run_result: RunResult,
    status: ConfigActionStatus,
) -> ConfigActionResult:
    """
        Map a :class:`RunResult` to a :class:`ConfigActionResult`.
    """
    result = ConfigActionResult(
        status=status,
        path=path,
        exit_code=run_result.exit_code,
        stdout=run_result.stdout,
        stderr=run_result.stderr,
        command=list(run_result.command),
    )
    return result
