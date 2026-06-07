# Tests for the RepositoryManager facade.

from dataclasses import dataclass, field
from typing import Callable, Optional

from ccai import testbase

from ccai.sysman.linux.redhat.repositorymanagement import (
    RepoActionStatus,
    RepoDefinition,
    RepositoryManager,
    RepositoryManagerConfig,
)
from ccai.sysman.linux.redhat.runners.models import RunResult


@dataclass
class _MockRunner:

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
        stdin: Optional[str] = None,
    ) -> RunResult:
        self.calls.append({"args": list(args), "stdin": stdin})
        key = tuple(args)
        if key in self.results:
            return self.results[key]
        if self.default_result is not None:
            return self.default_result
        return RunResult(exit_code=0, stdout="", stderr="", command=list(args))

    def add_aspect(self, aspect) -> None:
        return

    def remove_aspect(self, aspect) -> None:
        return

    def get_metrics(self):
        return None


def test_add_writes_new_repo_file():
    """
        add() writes a new .repo file via tee when absent.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/yum.repos.d/app.repo")] = RunResult(
        exit_code=1, stdout="", stderr="", command=["cat"],
    )
    rm = RepositoryManager(runner, RepositoryManagerConfig(elevated=True))
    result = rm.add(RepoDefinition(
        repo_id="app",
        name="Application Repository",
        enabled=True,
        gpg_check=True,
        base_url="https://example.com/el9",
        gpg_key="https://example.com/RPM-GPG-KEY",
    ))
    testbase.assert_equal(result.status, RepoActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true("[app]" in tee_calls[0]["stdin"])
    testbase.assert_true("enabled=1" in tee_calls[0]["stdin"])
    testbase.assert_true("gpgcheck=1" in tee_calls[0]["stdin"])
    return


def test_add_skipped_when_content_matches():
    """
        add() returns SKIPPED when the desired file already exists.
    """
    runner = _MockRunner()
    existing = (
        "[app]\nname=App\nbaseurl=https://example.com/el9\n"
        "enabled=1\ngpgcheck=0\n"
    )
    runner.results[("cat", "/etc/yum.repos.d/app.repo")] = RunResult(
        exit_code=0, stdout=existing, stderr="", command=["cat"],
    )
    rm = RepositoryManager(runner, RepositoryManagerConfig())
    result = rm.add(RepoDefinition(
        repo_id="app",
        name="App",
        enabled=True,
        gpg_check=False,
        base_url="https://example.com/el9",
    ))
    testbase.assert_equal(result.status, RepoActionStatus.SKIPPED)
    return


def test_remove_skipped_when_file_absent():
    """
        remove() returns SKIPPED when the .repo file does not exist.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/yum.repos.d/ghost.repo")] = RunResult(
        exit_code=1, stdout="", stderr="", command=["cat"],
    )
    rm = RepositoryManager(runner, RepositoryManagerConfig())
    result = rm.remove("ghost")
    testbase.assert_equal(result.status, RepoActionStatus.SKIPPED)
    return


def test_enable_dispatches_config_manager():
    """
        enable() runs ``dnf config-manager --set-enabled``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["dnf"],
    ))
    rm = RepositoryManager(runner, RepositoryManagerConfig(elevated=True))
    result = rm.enable("app")
    testbase.assert_equal(result.status, RepoActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"],
        ["dnf", "config-manager", "--set-enabled", "app"],
    )
    return


def test_refresh_cleans_and_makecaches():
    """
        refresh() dispatches clean + makecache sequentially.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["dnf"],
    ))
    rm = RepositoryManager(runner, RepositoryManagerConfig(elevated=True))
    result = rm.refresh()
    testbase.assert_equal(result.status, RepoActionStatus.SUCCESS)
    cmd_kinds = [c["args"][:2] for c in runner.calls]
    testbase.assert_true(["dnf", "clean"] in cmd_kinds)
    testbase.assert_true(["dnf", "makecache"] in cmd_kinds)
    return


def test_list_repos_parses_repolist_output():
    """
        list_repos() parses ``dnf repolist --all`` output.
    """
    output = (
        "repo id   repo name   status\n"
        "baseos    CentOS BaseOS  enabled\n"
        "appstream CentOS AppStream  disabled\n"
    )
    runner = _MockRunner()
    runner.results[("dnf", "repolist", "--all")] = RunResult(
        exit_code=0, stdout=output, stderr="", command=["dnf"],
    )
    rm = RepositoryManager(runner, RepositoryManagerConfig())
    repos = rm.list_repos()
    testbase.assert_equal(len(repos), 2)
    testbase.assert_equal(repos[0].repo_id, "baseos")
    testbase.assert_true(repos[0].enabled)
    testbase.assert_equal(repos[1].repo_id, "appstream")
    testbase.assert_false(repos[1].enabled)
    return


def test_import_gpg_key_runs_rpm_import():
    """
        import_gpg_key() runs ``rpm --import``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["rpm"],
    ))
    rm = RepositoryManager(runner, RepositoryManagerConfig(elevated=True))
    result = rm.import_gpg_key("https://example.com/RPM-GPG-KEY")
    testbase.assert_equal(result.status, RepoActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"],
        ["rpm", "--import", "https://example.com/RPM-GPG-KEY"],
    )
    return
