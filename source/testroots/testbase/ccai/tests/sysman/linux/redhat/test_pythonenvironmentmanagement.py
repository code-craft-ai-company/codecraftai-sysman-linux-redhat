# Tests for the RedHatPythonEnvironmentManager facade.
#
# The venv / pip verbs are exercised in depth by the shared base tests in
# codecraftai-sysman-common; these tests cover what the RHEL subclass adds
# (POSIX layout selection, the dnf prerequisite step) plus one end-to-end
# deploy on RHEL argv.  No live dnf, python3 or pip is invoked.

import json

from dataclasses import dataclass, field
from typing import Callable, Optional

from ccai import testbase

from ccai.sysman.common.pythonenvironmentlayout import PosixPythonEnvironmentLayout
from ccai.sysman.common.pythonenvironmentmanagementbase import PythonEnvManagerConfig

from ccai.sysman.linux.redhat.packagemanagement import PackageActionStatus
from ccai.sysman.linux.redhat.pythonenvironmentmanagement import (
    RedHatPythonEnvActionStatus,
    RedHatPythonEnvManagerConfig,
    RedHatPythonEnvSpec,
    RedHatPythonEnvironmentManager,
)
from ccai.sysman.linux.redhat.runners.models import RunResult


_VENV = "/opt/venvs/app"
_VENV_PYTHON = "/opt/venvs/app/bin/python"
_PIP_PREFIX = [_VENV_PYTHON, "-m", "pip", "--disable-pip-version-check", "--no-input"]
_VERSION_SNIPPET = "import platform; print(platform.python_version())"


@dataclass
class _MockRunner:
    """
        Stub runner keyed by argv tuple, with an optional list of argv
        prefixes that fail.
    """

    results: dict = field(default_factory=dict)
    fail_prefixes: list = field(default_factory=list)
    calls: list = field(default_factory=list)

    def run(
        self,
        args: list,
        *,
        elevated: bool = False,
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        stdin: Optional[str] = None,
        timeout: Optional[float] = None,
        on_output: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        capture_output: bool = True,
    ) -> RunResult:
        self.calls.append({"args": list(args), "elevated": elevated})

        key = tuple(args)
        if key in self.results:
            return self.results[key]

        for prefix in self.fail_prefixes:
            if tuple(args[:len(prefix)]) == tuple(prefix):
                return _rr(args, exit_code=1, stderr="dnf failure")

        return _rr(args, exit_code=0)


def _rr(args: list, *, exit_code: int, stdout: str = "", stderr: str = "") -> RunResult:
    """
        Build the RHEL :class:`RunResult`, which requires ``command``.
    """
    rtnval = RunResult(exit_code=exit_code, stdout=stdout, stderr=stderr, command=list(args))
    return rtnval


def _dnf_install_calls(runner: _MockRunner) -> list:
    rtnval = [c for c in runner.calls if c["args"][:3] == ["dnf", "install", "-y"]]
    return rtnval


def _pip_argv(*pip_args: str) -> list:
    rtnval = list(_PIP_PREFIX)
    rtnval.extend(pip_args)
    return rtnval


def test_layout_is_posix():
    """
        The RHEL manager selects the POSIX layout.
    """
    manager = RedHatPythonEnvironmentManager(runner=_MockRunner())
    testbase.assert_true(isinstance(manager.layout, PosixPythonEnvironmentLayout))
    testbase.assert_equal(manager.layout.venv_python_path(path=_VENV), _VENV_PYTHON)
    return


def test_ensure_prerequisites_installs_default_dnf_packages():
    """
        The default prerequisite set is python3 and python3-pip via dnf
        with the manager's elevation flag.
    """
    runner = _MockRunner()
    manager = RedHatPythonEnvironmentManager(
        runner=runner, config=RedHatPythonEnvManagerConfig(elevated=True),
    )

    results = manager.ensure_prerequisites()

    testbase.assert_equal([r.status for r in results], [PackageActionStatus.SUCCESS] * 2)
    installs = _dnf_install_calls(runner)
    testbase.assert_equal(
        [c["args"] for c in installs],
        [["dnf", "install", "-y", "python3"], ["dnf", "install", "-y", "python3-pip"]],
    )
    testbase.assert_equal(all(c["elevated"] is True for c in installs), True)
    return


def test_ensure_prerequisites_honours_override_and_config():
    """
        An explicit package list beats the config list.
    """
    runner = _MockRunner()
    config = RedHatPythonEnvManagerConfig(prerequisite_packages=["python3.12", "python3.12-pip"])
    manager = RedHatPythonEnvironmentManager(runner=runner, config=config)

    manager.ensure_prerequisites()
    manager.ensure_prerequisites(packages=["python3.11"])

    testbase.assert_equal(
        [c["args"][3] for c in _dnf_install_calls(runner)],
        ["python3.12", "python3.12-pip", "python3.11"],
    )
    return


def test_ensure_prerequisites_stops_at_first_failure():
    """
        A failed install aborts the remaining packages.
    """
    runner = _MockRunner(fail_prefixes=[["dnf", "install", "-y", "python3"]])
    manager = RedHatPythonEnvironmentManager(runner=runner)

    results = manager.ensure_prerequisites()

    testbase.assert_equal([r.status for r in results], [PackageActionStatus.FAILED])
    testbase.assert_equal(len(_dnf_install_calls(runner)), 1)
    return


def test_ensure_prerequisites_dry_run_dispatches_nothing():
    """
        Dry-run yields DRY_RUN per package with no dnf install call.
    """
    runner = _MockRunner()
    manager = RedHatPythonEnvironmentManager(
        runner=runner, config=RedHatPythonEnvManagerConfig(dry_run=True),
    )

    results = manager.ensure_prerequisites()

    testbase.assert_equal([r.status for r in results], [PackageActionStatus.DRY_RUN] * 2)
    testbase.assert_equal(_dnf_install_calls(runner), [])
    return


def test_plain_base_config_is_accepted():
    """
        A plain PythonEnvManagerConfig works; RHEL-only fields default.
    """
    runner = _MockRunner()
    manager = RedHatPythonEnvironmentManager(
        runner=runner, config=PythonEnvManagerConfig(elevated=True),
    )

    manager.ensure_prerequisites()

    testbase.assert_equal(len(_dnf_install_calls(runner)), 2)
    return


def test_deploy_end_to_end_on_rhel_argv():
    """
        A fresh deploy issues python3 -m venv, pip upgrade and pip install
        in order through the runner.
    """
    runner = _MockRunner()
    runner.results[("test", "-d", _VENV)] = _rr(["test", "-d", _VENV], exit_code=1)
    runner.results[(_VENV_PYTHON, "-c", _VERSION_SNIPPET)] = _rr(
        [_VENV_PYTHON, "-c", _VERSION_SNIPPET], exit_code=127,
    )
    runner.results[tuple(_pip_argv("list", "--format", "json"))] = _rr(
        _pip_argv("list", "--format", "json"), exit_code=0, stdout=json.dumps([]),
    )
    manager = RedHatPythonEnvironmentManager(
        runner=runner, config=RedHatPythonEnvManagerConfig(elevated=True),
    )

    result = manager.deploy(spec=RedHatPythonEnvSpec(path=_VENV, requirements_files=["/srv/r.txt"]))

    testbase.assert_equal(result.status, RedHatPythonEnvActionStatus.SUCCESS)
    writes = [c["args"] for c in runner.calls if c["elevated"] is True]
    testbase.assert_equal(
        writes,
        [
            ["python3", "-m", "venv", _VENV],
            _pip_argv("install", "--upgrade", "pip"),
            _pip_argv("install", "-r", "/srv/r.txt"),
        ],
    )
    return
