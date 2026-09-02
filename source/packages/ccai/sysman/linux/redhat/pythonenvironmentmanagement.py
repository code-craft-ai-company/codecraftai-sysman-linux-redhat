"""
    Red Hat python-environment-management feature.

    Implements the cross-OS python-environment-management contract for
    RHEL-family hosts.  All venv / pip behaviour is inherited from
    :class:`PythonEnvironmentManagementBase`; this module only selects
    the POSIX layout (``<venv>/bin/python``, ``python3`` launcher,
    ``test -d`` / ``rm -rf`` probes) and adds the RHEL prerequisite step.

    Prerequisites:
        RHEL 8+ ships ``python3`` with the ``venv`` module intact but
        minimal images may omit the interpreter and ``pip`` entirely.
        :meth:`RedHatPythonEnvironmentManager.ensure_prerequisites`
        installs ``python3`` and ``python3-pip`` through the existing
        :class:`PackageManager` (dnf) feature rather than re-implementing
        dnf calls here.  Callers targeting a specific interpreter stream
        (``python3.12``) set ``prerequisite_packages`` and
        ``base_interpreter`` together.

    See:
        * :doc:`/specs/contracts/python-environment-management/contract.md`
        * :doc:`/specs/python-environment-management-base/design.md`
        * :doc:`/specs/3-python-environment-management/design.md`
"""

from dataclasses import dataclass
from typing import List, Optional

from ccai.sysman.common.pythonenvironmentlayout import (
    PosixPythonEnvironmentLayout,
    PythonEnvironmentLayout,
)
from ccai.sysman.common.pythonenvironmentmanagementbase import (
    PythonEnvActionResult,
    PythonEnvActionStatus,
    PythonEnvDeployResult,
    PythonEnvInfo,
    PythonEnvManagerConfig,
    PythonEnvQueryError,
    PythonEnvSpec,
    PythonEnvironmentManagementBase,
    PythonPackageInfo,
)

from ccai.sysman.linux.redhat.packagemanagement import (
    PackageActionResult,
    PackageActionStatus,
    PackageManager,
    PackageManagerConfig,
)


RedHatPythonEnvActionStatus = PythonEnvActionStatus
RedHatPythonEnvActionResult = PythonEnvActionResult
RedHatPythonEnvDeployResult = PythonEnvDeployResult
RedHatPythonEnvInfo = PythonEnvInfo
RedHatPythonEnvQueryError = PythonEnvQueryError
RedHatPythonEnvSpec = PythonEnvSpec
RedHatPythonPackageInfo = PythonPackageInfo


_DEFAULT_PREREQUISITE_PACKAGES = ["python3", "python3-pip"]


@dataclass
class RedHatPythonEnvManagerConfig(PythonEnvManagerConfig):
    """
        Configuration for :class:`RedHatPythonEnvironmentManager`.
        Inherits every contract field from :class:`PythonEnvManagerConfig`.

        :param prerequisite_packages: dnf packages installed by
                                      :meth:`ensure_prerequisites`.
                                      ``None`` (the default) means
                                      ``python3`` and ``python3-pip``.
    """
    prerequisite_packages: Optional[List[str]] = None


class RedHatPythonEnvironmentManager(PythonEnvironmentManagementBase):
    """
        Red Hat concrete implementation of the
        python-environment-management contract.
    """

    def _build_layout(self) -> PythonEnvironmentLayout:
        """
            :returns: The POSIX layout.
        """
        rtnval = PosixPythonEnvironmentLayout()
        return rtnval

    def ensure_prerequisites(
        self,
        packages: Optional[List[str]] = None,
    ) -> List[PackageActionResult]:
        """
            Install the dnf packages needed before ``python3 -m venv``
            can succeed, delegating to :class:`PackageManager`.

            Each package is installed in order; the sequence stops at the
            first ``FAILED`` result.  Dry-run and elevation follow this
            manager's config.

            :param packages: Override of the package list; ``None`` uses
                             the config, then the module default.

            :returns: One :class:`PackageActionResult` per package
                      attempted, in order.
        """
        names = self._resolve_prerequisite_packages(packages=packages)
        package_manager = self._build_package_manager()

        rtnval: List[PackageActionResult] = []
        for name in names:
            result = package_manager.install(name=name)
            rtnval.append(result)
            if result.status == PackageActionStatus.FAILED:
                break
        return rtnval

    def _resolve_prerequisite_packages(
        self,
        *,
        packages: Optional[List[str]],
    ) -> List[str]:
        """
            Pick the prerequisite list: explicit argument, then config,
            then the module default.

            :param packages: Caller-supplied override or ``None``.

            :returns: A copy of the chosen list.
        """
        if packages is not None and len(packages) > 0:
            rtnval = list(packages)
            return rtnval

        config = self._typed_config()
        if config.prerequisite_packages is not None and len(config.prerequisite_packages) > 0:
            rtnval = list(config.prerequisite_packages)
            return rtnval

        rtnval = list(_DEFAULT_PREREQUISITE_PACKAGES)
        return rtnval

    def _build_package_manager(self) -> PackageManager:
        """
            Construct the :class:`PackageManager` used for prerequisite
            installs, sharing this manager's runner and its ``dry_run`` /
            ``elevated`` flags.

            :returns: The package manager.
        """
        package_config = PackageManagerConfig(
            dry_run=self._config.dry_run,
            elevated=self._config.elevated,
        )
        rtnval = PackageManager(runner=self._runner, config=package_config)
        return rtnval

    def _typed_config(self) -> RedHatPythonEnvManagerConfig:
        """
            Return the config as :class:`RedHatPythonEnvManagerConfig`,
            upgrading a plain :class:`PythonEnvManagerConfig` so the
            RHEL-only fields read as their defaults.

            :returns: The typed config.
        """
        if isinstance(self._config, RedHatPythonEnvManagerConfig):
            rtnval = self._config
            return rtnval

        rtnval = RedHatPythonEnvManagerConfig(
            dry_run=self._config.dry_run,
            elevated=self._config.elevated,
            base_interpreter=self._config.base_interpreter,
            index_url=self._config.index_url,
            extra_index_urls=self._config.extra_index_urls,
            trusted_hosts=self._config.trusted_hosts,
            pip_options=self._config.pip_options,
            upgrade_pip=self._config.upgrade_pip,
            system_site_packages=self._config.system_site_packages,
        )
        return rtnval


__all__ = [
    "RedHatPythonEnvActionResult",
    "RedHatPythonEnvActionStatus",
    "RedHatPythonEnvDeployResult",
    "RedHatPythonEnvInfo",
    "RedHatPythonEnvManagerConfig",
    "RedHatPythonEnvQueryError",
    "RedHatPythonEnvSpec",
    "RedHatPythonEnvironmentManager",
    "RedHatPythonPackageInfo",
]
