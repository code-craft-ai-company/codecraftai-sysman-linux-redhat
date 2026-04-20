# Tests for ContainerManager.

import json

from dataclasses import dataclass, field
from typing import Optional

from automojo import testbase

from automojo.sysman.linux.redhat.containermanagement import (
    ContainerActionStatus,
    ContainerManager,
    ContainerManagerConfig,
    ContainerSpec,
)
from automojo.sysman.linux.redhat.runners.models import RunResult


@dataclass
class _MockRunner:
    results: dict = field(default_factory=dict)
    default_result: Optional[RunResult] = None
    calls: list = field(default_factory=list)

    def run(self, args, *, elevated=False, cwd=None, env=None, timeout=None,
            on_output=None, on_error=None, capture_output=True, stdin=None):
        self.calls.append({"args": list(args), "stdin": stdin})
        key = tuple(args)
        if key in self.results:
            return self.results[key]
        if self.default_result is not None:
            return self.default_result
        return RunResult(exit_code=0, stdout="", stderr="", command=list(args))

    def add_aspect(self, aspect): return
    def remove_aspect(self, aspect): return
    def get_metrics(self): return None


def test_pull_image_dispatches_podman():
    """
        pull_image() runs ``podman pull <image>``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["podman"],
    ))
    mgr = ContainerManager(runner, ContainerManagerConfig(elevated=True))
    result = mgr.pull_image("registry.example.com/app:1.0")
    testbase.assert_equal(result.status, ContainerActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"],
        ["podman", "pull", "registry.example.com/app:1.0"],
    )
    return


def test_run_container_builds_full_command():
    """
        run_container() adds port, env, and volume flags from the spec.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["podman"],
    ))
    mgr = ContainerManager(runner, ContainerManagerConfig(elevated=True))
    spec = ContainerSpec(
        image="registry.example.com/app:1.0",
        name="app01",
        ports=["8080:8080"],
        env={"ENV": "prod"},
        volumes=["/srv/app:/app"],
    )
    result = mgr.run_container(spec)
    testbase.assert_equal(result.status, ContainerActionStatus.SUCCESS)
    args = runner.calls[-1]["args"]
    testbase.assert_true("--name" in args)
    testbase.assert_true("-d" in args)
    testbase.assert_true("-p" in args)
    testbase.assert_true("-e" in args)
    testbase.assert_true("-v" in args)
    return


def test_list_containers_parses_json_output():
    """
        list_containers() parses ``podman ps --format=json`` output.
    """
    body = json.dumps([
        {
            "Id": "abc",
            "Names": ["app01"],
            "Image": "registry.example.com/app:1.0",
            "State": "running",
        },
    ])
    runner = _MockRunner()
    runner.results[(
        "podman", "ps", "-a", "--format=json",
    )] = RunResult(exit_code=0, stdout=body, stderr="", command=["podman"])
    mgr = ContainerManager(runner, ContainerManagerConfig())
    containers = mgr.list_containers()
    testbase.assert_equal(len(containers), 1)
    testbase.assert_equal(containers[0].name, "app01")
    testbase.assert_equal(containers[0].state, "running")
    return


# ---------------------------------------------------------------------
# registry_login — Part C credential routing
# ---------------------------------------------------------------------


class _StubCredential:
    """
        Minimal duck-type of automojo-credentials' BasicCredential.
    """

    def __init__(self, *, username: str, password: str) -> None:
        self.username = username
        self.password = password
        return


def test_registry_login_sends_password_on_stdin():
    """
        registry_login() sends the password on the runner's stdin
        channel; argv contains only the username, registry, and the
        ``--password-stdin`` flag (never the password itself).
    """
    runner = _MockRunner()
    mgr = ContainerManager(runner, ContainerManagerConfig())
    result = mgr.registry_login(
        "quay.io", _StubCredential(username="user", password="s3cret"),
    )
    testbase.assert_equal(result.status, ContainerActionStatus.SUCCESS)
    login_calls = [c for c in runner.calls if c["args"][:2] == ["podman", "login"]]
    testbase.assert_equal(len(login_calls), 1)
    argv = login_calls[0]["args"]
    testbase.assert_true("--username" in argv)
    testbase.assert_true("user" in argv)
    testbase.assert_true("--password-stdin" in argv)
    testbase.assert_true("quay.io" in argv)
    # Critical: the password must NEVER appear in argv.
    testbase.assert_true("s3cret" not in argv)
    # stdin carries the password.
    testbase.assert_equal(login_calls[0]["stdin"], "s3cret")
    # Result.command must not carry the password either.
    testbase.assert_true("s3cret" not in (result.command or []))
    return


def test_registry_login_dry_run_does_not_dispatch():
    """
        registry_login() in dry-run returns DRY_RUN without calling
        the runner.
    """
    runner = _MockRunner()
    mgr = ContainerManager(runner, ContainerManagerConfig(dry_run=True))
    result = mgr.registry_login(
        "quay.io", _StubCredential(username="user", password="s3cret"),
    )
    testbase.assert_equal(result.status, ContainerActionStatus.DRY_RUN)
    testbase.assert_equal(runner.calls, [])
    return


def test_registry_login_requires_credential():
    """
        registry_login() raises ValueError when credential is None.
    """
    runner = _MockRunner()
    mgr = ContainerManager(runner, ContainerManagerConfig())
    raised = False
    try:
        mgr.registry_login("quay.io", None)
    except ValueError:
        raised = True
    testbase.assert_true(raised)
    return


def test_registry_logout_dispatches_podman_logout():
    """
        registry_logout() dispatches ``podman logout <registry>``.
    """
    runner = _MockRunner()
    mgr = ContainerManager(runner, ContainerManagerConfig())
    result = mgr.registry_logout("quay.io")
    testbase.assert_equal(result.status, ContainerActionStatus.SUCCESS)
    logout_calls = [c for c in runner.calls if c["args"][:2] == ["podman", "logout"]]
    testbase.assert_equal(len(logout_calls), 1)
    testbase.assert_equal(logout_calls[0]["args"][2], "quay.io")
    return
