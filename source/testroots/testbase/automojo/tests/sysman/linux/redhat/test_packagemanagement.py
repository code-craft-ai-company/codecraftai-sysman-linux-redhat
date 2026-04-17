# Tests for the PackageManager facade.

from dataclasses import dataclass, field
from typing import Callable, Optional
from unittest import mock

from automojo import testbase

from automojo.sysman.linux.redhat.packagemanagement import (
    PackageActionStatus,
    PackageFileNotFoundError,
    PackageManager,
    PackageManagerConfig,
    PackageQueryError,
)
from automojo.sysman.linux.redhat.runners.models import RunResult


@dataclass
class _MockRunner:
    """
        Stub runner with a command-to-result map so individual tests can
        script the behaviour of successive commands.
    """

    results: dict = field(default_factory=dict)
    default_result: Optional[RunResult] = None
    calls: list = field(default_factory=list)

    def run(
        self,
        args: list[str],
        *,
        elevated: bool = False,
        cwd: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
        timeout: Optional[float] = None,
        on_output: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        capture_output: bool = True,
    ) -> RunResult:
        self.calls.append({
            "args": list(args),
            "elevated": elevated,
        })
        key = tuple(args)
        if key in self.results:
            return self.results[key]
        if self.default_result is not None:
            return self.default_result
        default = RunResult(
            exit_code=0, stdout="", stderr="", command=list(args)
        )
        return default

    def add_aspect(self, aspect) -> None:
        return

    def remove_aspect(self, aspect) -> None:
        return

    def get_metrics(self):
        return None


def _rpm_query_result(name: str, *, installed: bool) -> RunResult:
    """
        Build a canonical ``rpm -q`` RunResult for the given package name.
    """
    if installed:
        stdout = "{} 1.2.3 4.el9 x86_64\n".format(name)
        found_result = RunResult(
            exit_code=0, stdout=stdout, stderr="", command=["rpm"]
        )
        return found_result
    not_found_result = RunResult(
        exit_code=1,
        stdout="",
        stderr="package {} is not installed".format(name),
        command=["rpm"],
    )
    return not_found_result


def test_install_when_not_present_invokes_dnf():
    """
        install() on a missing package must dispatch ``dnf install -y``.
    """
    runner = _MockRunner()
    runner.results[(
        "rpm", "-q", "--queryformat",
        "%{NAME} %{VERSION} %{RELEASE} %{ARCH}\n", "nginx",
    )] = _rpm_query_result("nginx", installed=False)
    runner.results[("dnf", "install", "-y", "nginx")] = RunResult(
        exit_code=0, stdout="done", stderr="", command=["dnf"]
    )
    pm = PackageManager(runner, PackageManagerConfig(elevated=True))
    result = pm.install("nginx")
    testbase.assert_equal(result.status, PackageActionStatus.SUCCESS)
    write_call = runner.calls[-1]
    testbase.assert_equal(write_call["args"], ["dnf", "install", "-y", "nginx"])
    testbase.assert_true(write_call["elevated"])
    return


def test_install_skipped_when_already_installed():
    """
        install() on an already installed package must return SKIPPED.
    """
    runner = _MockRunner()
    runner.results[(
        "rpm", "-q", "--queryformat",
        "%{NAME} %{VERSION} %{RELEASE} %{ARCH}\n", "nginx",
    )] = _rpm_query_result("nginx", installed=True)
    pm = PackageManager(runner, PackageManagerConfig())
    result = pm.install("nginx")
    testbase.assert_equal(result.status, PackageActionStatus.SKIPPED)
    testbase.assert_equal(len(runner.calls), 1)
    return


def test_install_at_specific_version():
    """
        install() with a version must append ``-version`` to the package name.
    """
    runner = _MockRunner()
    runner.results[(
        "rpm", "-q", "--queryformat",
        "%{NAME} %{VERSION} %{RELEASE} %{ARCH}\n", "nginx",
    )] = _rpm_query_result("nginx", installed=False)
    pm = PackageManager(runner, PackageManagerConfig())
    pm.install("nginx", version="1.20")
    write_call = runner.calls[-1]
    testbase.assert_equal(
        write_call["args"], ["dnf", "install", "-y", "nginx-1.20"]
    )
    return


def test_install_dry_run_does_not_write():
    """
        install() with dry_run=True must avoid invoking dnf install.
    """
    runner = _MockRunner()
    runner.results[(
        "rpm", "-q", "--queryformat",
        "%{NAME} %{VERSION} %{RELEASE} %{ARCH}\n", "nginx",
    )] = _rpm_query_result("nginx", installed=False)
    pm = PackageManager(runner, PackageManagerConfig(dry_run=True))
    result = pm.install("nginx")
    testbase.assert_equal(result.status, PackageActionStatus.DRY_RUN)
    write_attempts = [c for c in runner.calls if c["args"][0] == "dnf"]
    testbase.assert_equal(write_attempts, [])
    return


def test_remove_when_installed():
    """
        remove() on an installed package must dispatch ``dnf remove -y``.
    """
    runner = _MockRunner()
    runner.results[(
        "rpm", "-q", "--queryformat",
        "%{NAME} %{VERSION} %{RELEASE} %{ARCH}\n", "nginx",
    )] = _rpm_query_result("nginx", installed=True)
    runner.results[("dnf", "remove", "-y", "nginx")] = RunResult(
        exit_code=0, stdout="removed", stderr="", command=["dnf"]
    )
    pm = PackageManager(runner, PackageManagerConfig())
    result = pm.remove("nginx")
    testbase.assert_equal(result.status, PackageActionStatus.SUCCESS)
    return


def test_remove_skipped_when_not_installed():
    """
        remove() on a missing package must return SKIPPED without invoking dnf.
    """
    runner = _MockRunner()
    runner.results[(
        "rpm", "-q", "--queryformat",
        "%{NAME} %{VERSION} %{RELEASE} %{ARCH}\n", "nginx",
    )] = _rpm_query_result("nginx", installed=False)
    pm = PackageManager(runner, PackageManagerConfig())
    result = pm.remove("nginx")
    testbase.assert_equal(result.status, PackageActionStatus.SKIPPED)
    return


def test_query_returns_info_when_installed():
    """
        query() must parse rpm -q queryformat output into a PackageInfo.
    """
    runner = _MockRunner()
    runner.results[(
        "rpm", "-q", "--queryformat",
        "%{NAME} %{VERSION} %{RELEASE} %{ARCH}\n", "bash",
    )] = RunResult(
        exit_code=0, stdout="bash 5.1 8.el9 x86_64\n", stderr="",
        command=["rpm"],
    )
    pm = PackageManager(runner, PackageManagerConfig())
    info = pm.query("bash")
    testbase.assert_true(info is not None)
    testbase.assert_equal(info.name, "bash")
    testbase.assert_equal(info.version, "5.1")
    testbase.assert_equal(info.arch, "x86_64")
    return


def test_query_returns_none_when_not_installed():
    """
        query() must return None when the package is not installed.
    """
    runner = _MockRunner()
    runner.results[(
        "rpm", "-q", "--queryformat",
        "%{NAME} %{VERSION} %{RELEASE} %{ARCH}\n", "absent",
    )] = _rpm_query_result("absent", installed=False)
    pm = PackageManager(runner, PackageManagerConfig())
    info = pm.query("absent")
    testbase.assert_equal(info, None)
    return


def test_query_raises_on_unexpected_error():
    """
        query() must raise PackageQueryError on unexpected rpm failures.
    """
    runner = _MockRunner()
    runner.results[(
        "rpm", "-q", "--queryformat",
        "%{NAME} %{VERSION} %{RELEASE} %{ARCH}\n", "err",
    )] = RunResult(
        exit_code=7, stdout="", stderr="unexpected", command=["rpm"]
    )
    pm = PackageManager(runner, PackageManagerConfig())
    raised = False
    try:
        pm.query("err")
    except PackageQueryError:
        raised = True
    testbase.assert_true(raised)
    return


def test_list_updates_no_updates_when_exit_code_zero():
    """
        check-update exit code 0 must map to an empty updates list.
    """
    runner = _MockRunner()
    runner.results[("dnf", "check-update")] = RunResult(
        exit_code=0, stdout="", stderr="", command=["dnf"]
    )
    pm = PackageManager(runner, PackageManagerConfig())
    testbase.assert_equal(pm.list_updates(), [])
    return


def test_list_updates_parsed_from_exit_code_100():
    """
        check-update exit code 100 must be parsed into PackageUpdateInfo rows.
    """
    output = (
        "Last metadata expiration check: 2:00:00 ago\n"
        "\n"
        "nginx.x86_64     1.20-2    baseos\n"
        "curl.x86_64      7.80-1    baseos\n"
    )
    runner = _MockRunner()
    runner.results[("dnf", "check-update")] = RunResult(
        exit_code=100, stdout=output, stderr="", command=["dnf"]
    )
    pm = PackageManager(runner, PackageManagerConfig())
    updates = pm.list_updates()
    testbase.assert_equal(len(updates), 2)
    testbase.assert_equal(updates[0].name, "nginx")
    testbase.assert_equal(updates[0].available_version, "1.20-2")
    testbase.assert_equal(updates[0].arch, "x86_64")
    return


def test_list_updates_unexpected_exit_code_raises():
    """
        check-update must raise PackageQueryError on unexpected non-zero exit codes.
    """
    runner = _MockRunner()
    runner.results[("dnf", "check-update")] = RunResult(
        exit_code=1, stdout="", stderr="broken", command=["dnf"]
    )
    pm = PackageManager(runner, PackageManagerConfig())
    raised = False
    try:
        pm.list_updates()
    except PackageQueryError:
        raised = True
    testbase.assert_true(raised)
    return


def test_install_local_missing_file_raises():
    """
        install_local() must raise for a file that does not exist on disk.
    """
    runner = _MockRunner()
    pm = PackageManager(runner, PackageManagerConfig())
    raised = False
    try:
        pm.install_local("/definitely/not/a/real/file.rpm")
    except PackageFileNotFoundError:
        raised = True
    testbase.assert_true(raised)
    return


def test_install_local_invokes_dnf_install_with_filepath():
    """
        install_local() must dispatch ``dnf install -y <path>``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["dnf"]
    ))
    pm = PackageManager(runner, PackageManagerConfig(elevated=True))
    with mock.patch(
        "automojo.sysman.linux.redhat.packagemanagement.Path.exists",
        return_value=True,
    ):
        result = pm.install_local("/tmp/pkg.rpm")
    testbase.assert_equal(result.status, PackageActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"], ["dnf", "install", "-y", "/tmp/pkg.rpm"]
    )
    return


def test_hold_when_none_applies():
    """
        hold() on an unheld package must call ``dnf versionlock add``.
    """
    runner = _MockRunner()
    runner.results[("dnf", "versionlock", "list")] = RunResult(
        exit_code=0, stdout="", stderr="", command=["dnf"]
    )
    runner.results[("dnf", "versionlock", "add", "nginx")] = RunResult(
        exit_code=0, stdout="added", stderr="", command=["dnf"]
    )
    pm = PackageManager(runner, PackageManagerConfig(elevated=True))
    result = pm.hold("nginx")
    testbase.assert_equal(result.status, PackageActionStatus.SUCCESS)
    return


def test_hold_skipped_when_already_held():
    """
        hold() on an already held package must return SKIPPED.
    """
    runner = _MockRunner()
    runner.results[("dnf", "versionlock", "list")] = RunResult(
        exit_code=0, stdout="nginx-1.20-2.*\n", stderr="", command=["dnf"]
    )
    pm = PackageManager(runner, PackageManagerConfig())
    result = pm.hold("nginx")
    testbase.assert_equal(result.status, PackageActionStatus.SKIPPED)
    return


def test_release_hold_skipped_when_not_held():
    """
        release_hold() when no hold exists must return SKIPPED.
    """
    runner = _MockRunner()
    runner.results[("dnf", "versionlock", "list")] = RunResult(
        exit_code=0, stdout="", stderr="", command=["dnf"]
    )
    pm = PackageManager(runner, PackageManagerConfig())
    result = pm.release_hold("nginx")
    testbase.assert_equal(result.status, PackageActionStatus.SKIPPED)
    return


def test_group_install():
    """
        group_install() must dispatch ``dnf groupinstall -y <group>``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["dnf"]
    ))
    pm = PackageManager(runner, PackageManagerConfig(elevated=True))
    result = pm.group_install("Web Server")
    testbase.assert_equal(result.status, PackageActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"], ["dnf", "groupinstall", "-y", "Web Server"]
    )
    return


def test_group_remove_dry_run():
    """
        group_remove() with dry_run=True must not invoke dnf groupremove.
    """
    runner = _MockRunner()
    pm = PackageManager(runner, PackageManagerConfig(dry_run=True))
    result = pm.group_remove("Web Server")
    testbase.assert_equal(result.status, PackageActionStatus.DRY_RUN)
    testbase.assert_equal(runner.calls, [])
    return
