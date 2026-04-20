# automojo.sysman.linux.redhat.certificatetrust
#
# CertificateTrustManager wraps update-ca-trust and trust-anchor placement
# through the ICommandRunner abstraction.

from dataclasses import dataclass

from automojo.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


TrustActionStatus = ActionStatus


@dataclass
class CertificateTrustConfig(FeatureConfig):
    """
        Configuration for :class:`CertificateTrustManager`.  Inherits
        ``dry_run`` and ``elevated`` from :class:`FeatureConfig`.

        :param anchors_dir: Directory containing trust anchor files.
    """
    anchors_dir: str = "/etc/pki/ca-trust/source/anchors"


@dataclass
class TrustActionResult(FeatureActionResult):
    """
        Outcome of a trust store action.  No feature-specific extras.
    """
    pass


class CertificateTrustManager(BaseFeatureManager):
    """
        Facade for the RHEL system-wide CA trust store.
    """

    result_class = TrustActionResult
    feature_name = "certificate-trust-management"

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
        # Chain the refresh; emit only the refresh's result as before.
        return self._dispatch(
            command=["update-ca-trust", "extract"],
            detail=path,
            operation="add_anchor",
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
        return self._dispatch(
            command=["update-ca-trust", "extract"],
            detail=path,
            operation="remove_anchor",
        )

    def refresh(self) -> TrustActionResult:
        """
            Run ``update-ca-trust extract`` directly.
        """
        return self._dispatch(
            command=["update-ca-trust", "extract"],
            operation="refresh",
        )

    def _read(self, *, path: str) -> str:
        """
            Read a file via ``cat`` returning empty on missing.
        """
        run_result = self._runner.run(["cat", path], elevated=False)
        if run_result.exit_code != 0:
            return ""
        return run_result.stdout
