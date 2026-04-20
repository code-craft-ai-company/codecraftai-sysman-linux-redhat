# automojo.sysman.linux.redhat.sshserverconfiguration
#
# SshServerConfigurator manages sshd configuration drop-ins, host keys,
# validation, and reload through the ICommandRunner abstraction.

from dataclasses import dataclass
from typing import Optional

from automojo.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


SshdActionStatus = ActionStatus


def _as_sshd_result(base_result: FeatureActionResult) -> "SshdActionResult":
    """
        Convert a :class:`FeatureActionResult` produced by a delegated
        :class:`ServiceManager` call into a :class:`SshdActionResult`.
    """
    return SshdActionResult(
        status=base_result.status,
        detail=base_result.detail,
        exit_code=base_result.exit_code,
        stdout=base_result.stdout,
        stderr=base_result.stderr,
        command=base_result.command,
    )


@dataclass
class SshServerConfig(FeatureConfig):
    """
        Configuration for :class:`SshServerConfigurator`.  Inherits
        ``dry_run`` and ``elevated`` from :class:`FeatureConfig`.

        :param dropin_dir: Directory containing sshd drop-in files.
    """
    dropin_dir: str = "/etc/ssh/sshd_config.d"


@dataclass
class SshdActionResult(FeatureActionResult):
    """
        Outcome of an sshd management action.  No feature-specific
        extras beyond the base fields.
    """
    pass


@dataclass
class SshdValidateResult:
    """
        Outcome of ``sshd -t`` validation.
    """
    valid: bool
    stderr: str


class SshServerConfigurator(BaseFeatureManager):
    """
        Facade for sshd configuration via drop-ins.  The sshd service
        reload is delegated to :class:`ServiceManager` (Part C seam).
    """

    result_class = SshdActionResult
    feature_name = "ssh-server-configuration"

    def __init__(
        self,
        runner,
        config: Optional[SshServerConfig] = None,
        *,
        service_manager=None,
        report=None,
    ) -> None:
        super().__init__(runner=runner, config=config, report=report)
        if service_manager is None:
            from automojo.sysman.linux.redhat.servicemanagement import (
                ServiceManager,
                ServiceManagerConfig,
            )
            service_manager = ServiceManager(
                runner,
                ServiceManagerConfig(
                    dry_run=self._config.dry_run,
                    elevated=self._config.elevated,
                ),
            )
        self._service_manager = service_manager
        return

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
            Validate then reload sshd via :class:`ServiceManager`.
            Aborts if validation fails.
        """
        validation = self.validate_config()
        if not validation.valid:
            return SshdActionResult(
                status=SshdActionStatus.FAILED,
                detail="validation failed",
                stderr=validation.stderr,
            )
        svc_res = self._service_manager.reload("sshd")
        result = _as_sshd_result(svc_res)
        if result.detail is None:
            result.detail = "systemctl reload sshd"
        return result

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
