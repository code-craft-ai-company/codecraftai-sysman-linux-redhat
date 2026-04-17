# automojo.sysman.linux.redhat.sshserverconfiguration
#
# SshServerConfigurator manages sshd configuration drop-ins, host keys,
# validation, and reload through the ICommandRunner abstraction.

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.runners.base import ICommandRunner


class SshdActionStatus(Enum):
    """
        Outcome status for sshd management actions.
    """
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class SshServerConfig:
    """
        Configuration for :class:`SshServerConfigurator`.

        :param dry_run: When True, no commands execute.
        :param elevated: When True, write/validate commands run elevated.
        :param dropin_dir: Directory containing sshd drop-in files.
    """
    dry_run: bool = False
    elevated: bool = False
    dropin_dir: str = "/etc/ssh/sshd_config.d"


@dataclass
class SshdActionResult:
    """
        Outcome of an sshd management action.
    """
    status: SshdActionStatus
    detail: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


@dataclass
class SshdValidateResult:
    """
        Outcome of ``sshd -t`` validation.
    """
    valid: bool
    stderr: str


class SshServerConfigurator:
    """
        Facade for sshd configuration via drop-ins and systemctl.
    """

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[SshServerConfig] = None,
    ) -> None:
        """
            :param runner: Command runner.
            :param config: Manager configuration.
        """
        if config is None:
            config = SshServerConfig()
        self._runner: ICommandRunner = runner
        self._config: SshServerConfig = config
        return

    @property
    def config(self) -> SshServerConfig:
        """
            The active :class:`SshServerConfig`.
        """
        return self._config

    def get_effective_directive(self, name: str) -> Optional[str]:
        """
            Query the effective value of a single directive via ``sshd -T``.
        """
        full = self.get_full_effective_config()
        return full.get(name.lower())

    def get_full_effective_config(self) -> dict[str, str]:
        """
            Return the full effective directive map via ``sshd -T``.
        """
        run_result = self._runner.run(["sshd", "-T"], elevated=self._config.elevated)
        if run_result.exit_code != 0:
            return {}
        parsed: dict[str, str] = {}
        for raw in run_result.stdout.splitlines():
            line = raw.strip()
            if len(line) == 0 or line.startswith("#"):
                continue
            tokens = line.split(None, 1)
            if len(tokens) < 2:
                continue
            parsed[tokens[0].lower()] = tokens[1].strip()
        return parsed

    def set_directive_dropin(
        self,
        dropin_name: str,
        directive: str,
        value: str,
    ) -> SshdActionResult:
        """
            Set ``directive`` inside ``<dropin_dir>/<dropin_name>.conf``.
        """
        path = "{}/{}.conf".format(self._config.dropin_dir, dropin_name)
        current = self._read(path=path)
        new_content = _apply_directive(
            content=current, directive=directive, value=value,
        )
        if current == new_content:
            return SshdActionResult(
                status=SshdActionStatus.SKIPPED, detail=path,
            )
        return self._write(path=path, content=new_content, detail=path)

    def delete_directive_dropin(
        self,
        dropin_name: str,
        directive: str,
    ) -> SshdActionResult:
        """
            Remove a directive from a drop-in file.  Deletes the file when empty.
        """
        path = "{}/{}.conf".format(self._config.dropin_dir, dropin_name)
        current = self._read(path=path)
        if len(current) == 0:
            return SshdActionResult(
                status=SshdActionStatus.SKIPPED, detail=path,
            )
        new_content = _remove_directive(
            content=current, directive=directive,
        )
        if new_content == current:
            return SshdActionResult(
                status=SshdActionStatus.SKIPPED, detail=path,
            )
        if len(new_content.strip()) == 0:
            if self._config.dry_run:
                return SshdActionResult(
                    status=SshdActionStatus.DRY_RUN, detail=path,
                )
            run_result = self._runner.run(
                ["rm", "-f", path], elevated=self._config.elevated,
            )
            status = SshdActionStatus.SUCCESS if run_result.exit_code == 0 else SshdActionStatus.FAILED
            return SshdActionResult(
                status=status, detail=path,
                exit_code=run_result.exit_code,
                stdout=run_result.stdout,
                stderr=run_result.stderr,
                command=list(run_result.command),
            )
        return self._write(path=path, content=new_content, detail=path)

    def validate_config(self) -> SshdValidateResult:
        """
            Run ``sshd -t`` and capture the outcome.
        """
        run_result = self._runner.run(
            ["sshd", "-t"], elevated=self._config.elevated,
        )
        return SshdValidateResult(
            valid=run_result.exit_code == 0,
            stderr=run_result.stderr,
        )

    def reload_config(self) -> SshdActionResult:
        """
            Validate then reload sshd.  Aborts if validation fails.
        """
        validation = self.validate_config()
        if not validation.valid:
            return SshdActionResult(
                status=SshdActionStatus.FAILED,
                detail="validation failed",
                stderr=validation.stderr,
            )
        if self._config.dry_run:
            return SshdActionResult(
                status=SshdActionStatus.DRY_RUN, detail="systemctl reload sshd",
            )
        run_result = self._runner.run(
            ["systemctl", "reload", "sshd"], elevated=self._config.elevated,
        )
        status = SshdActionStatus.SUCCESS if run_result.exit_code == 0 else SshdActionStatus.FAILED
        return SshdActionResult(
            status=status, detail="systemctl reload sshd",
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )

    def _read(self, *, path: str) -> str:
        """
            Read a drop-in file via ``cat``, returning empty when missing.
        """
        run_result = self._runner.run(["cat", path], elevated=False)
        if run_result.exit_code != 0:
            return ""
        return run_result.stdout

    def _write(
        self,
        *,
        path: str,
        content: str,
        detail: str,
    ) -> SshdActionResult:
        """
            Write content via ``tee`` honouring dry-run.
        """
        command = ["tee", path]
        if self._config.dry_run:
            return SshdActionResult(
                status=SshdActionStatus.DRY_RUN, detail=detail, command=command,
            )
        run_result = self._runner.run(
            command, elevated=self._config.elevated, stdin=content,
        )
        status = SshdActionStatus.SUCCESS if run_result.exit_code == 0 else SshdActionStatus.FAILED
        return SshdActionResult(
            status=status, detail=detail,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )


def _apply_directive(*, content: str, directive: str, value: str) -> str:
    """
        Replace or append ``directive <value>`` inside the given content.
    """
    lines = content.splitlines()
    replaced = False
    updated: list[str] = []
    target_lower = directive.lower()
    new_line = "{} {}".format(directive, value)
    for line in lines:
        stripped = line.strip()
        if len(stripped) == 0 or stripped.startswith("#"):
            updated.append(line)
            continue
        tokens = stripped.split(None, 1)
        if len(tokens) < 2:
            updated.append(line)
            continue
        if tokens[0].lower() == target_lower:
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


def _remove_directive(*, content: str, directive: str) -> str:
    """
        Remove lines matching ``directive`` from the content.
    """
    target_lower = directive.lower()
    lines = content.splitlines()
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if len(stripped) == 0 or stripped.startswith("#"):
            kept.append(line)
            continue
        tokens = stripped.split(None, 1)
        if len(tokens) < 2:
            kept.append(line)
            continue
        if tokens[0].lower() == target_lower:
            continue
        kept.append(line)
    body = "\n".join(kept)
    if len(body) > 0 and not body.endswith("\n"):
        body = body + "\n"
    return body
