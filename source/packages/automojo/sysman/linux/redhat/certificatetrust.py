# automojo.sysman.linux.redhat.certificatetrust
#
# CertificateTrustManager wraps update-ca-trust and trust-anchor placement
# through the ICommandRunner abstraction.

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.runners.base import ICommandRunner


class TrustActionStatus(Enum):
    """
        Outcome status for CA trust management actions.
    """
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class CertificateTrustConfig:
    """
        Configuration for :class:`CertificateTrustManager`.

        :param dry_run: When True, no commands execute.
        :param elevated: When True, write commands run elevated.
        :param anchors_dir: Directory containing trust anchor files.
    """
    dry_run: bool = False
    elevated: bool = False
    anchors_dir: str = "/etc/pki/ca-trust/source/anchors"


@dataclass
class TrustActionResult:
    """
        Outcome of a trust store action.
    """
    status: TrustActionStatus
    detail: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


class CertificateTrustManager:
    """
        Facade for the RHEL system-wide CA trust store.
    """

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[CertificateTrustConfig] = None,
    ) -> None:
        """
            :param runner: Command runner.
            :param config: Manager configuration.
        """
        if config is None:
            config = CertificateTrustConfig()
        self._runner: ICommandRunner = runner
        self._config: CertificateTrustConfig = config
        return

    @property
    def config(self) -> CertificateTrustConfig:
        """
            The active :class:`CertificateTrustConfig`.
        """
        return self._config

    def add_anchor(
        self,
        name: str,
        pem_content: str,
    ) -> TrustActionResult:
        """
            Install a CA trust anchor PEM file and refresh the trust store.
        """
        path = "{}/{}.pem".format(self._config.anchors_dir, name)
        current = self._read(path=path)
        if current == pem_content:
            return TrustActionResult(
                status=TrustActionStatus.SKIPPED, detail=path,
            )
        if self._config.dry_run:
            return TrustActionResult(
                status=TrustActionStatus.DRY_RUN, detail=path,
                command=["tee", path],
            )
        write_result = self._runner.run(
            ["tee", path], elevated=self._config.elevated, stdin=pem_content,
        )
        if write_result.exit_code != 0:
            return TrustActionResult(
                status=TrustActionStatus.FAILED,
                detail=path,
                exit_code=write_result.exit_code,
                stdout=write_result.stdout,
                stderr=write_result.stderr,
                command=list(write_result.command),
            )
        refresh_result = self._runner.run(
            ["update-ca-trust", "extract"], elevated=self._config.elevated,
        )
        status = TrustActionStatus.SUCCESS if refresh_result.exit_code == 0 else TrustActionStatus.FAILED
        return TrustActionResult(
            status=status, detail=path,
            exit_code=refresh_result.exit_code,
            stdout=refresh_result.stdout,
            stderr=refresh_result.stderr,
            command=list(refresh_result.command),
        )

    def remove_anchor(self, name: str) -> TrustActionResult:
        """
            Remove a trust anchor and refresh the trust store.
        """
        path = "{}/{}.pem".format(self._config.anchors_dir, name)
        existing = self._read(path=path)
        if len(existing) == 0:
            return TrustActionResult(
                status=TrustActionStatus.SKIPPED, detail=path,
            )
        if self._config.dry_run:
            return TrustActionResult(
                status=TrustActionStatus.DRY_RUN, detail=path,
                command=["rm", "-f", path],
            )
        self._runner.run(["rm", "-f", path], elevated=self._config.elevated)
        refresh_result = self._runner.run(
            ["update-ca-trust", "extract"], elevated=self._config.elevated,
        )
        status = TrustActionStatus.SUCCESS if refresh_result.exit_code == 0 else TrustActionStatus.FAILED
        return TrustActionResult(
            status=status, detail=path,
            exit_code=refresh_result.exit_code,
            stdout=refresh_result.stdout,
            stderr=refresh_result.stderr,
            command=list(refresh_result.command),
        )

    def refresh(self) -> TrustActionResult:
        """
            Run ``update-ca-trust extract`` directly.
        """
        command = ["update-ca-trust", "extract"]
        if self._config.dry_run:
            return TrustActionResult(
                status=TrustActionStatus.DRY_RUN, command=command,
            )
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = TrustActionStatus.SUCCESS if run_result.exit_code == 0 else TrustActionStatus.FAILED
        return TrustActionResult(
            status=status,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )

    def _read(self, *, path: str) -> str:
        """
            Read a file via ``cat`` returning empty on missing.
        """
        run_result = self._runner.run(["cat", path], elevated=False)
        if run_result.exit_code != 0:
            return ""
        return run_result.stdout
