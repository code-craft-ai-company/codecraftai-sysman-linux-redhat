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
        self.calls.append({"args": list(args)})
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
