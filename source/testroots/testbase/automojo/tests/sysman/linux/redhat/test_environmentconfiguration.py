# Tests for the EnvironmentConfigurator.

from dataclasses import dataclass, field
from typing import Callable, Optional

from automojo import testbase

from automojo.sysman.linux.redhat.environmentconfiguration import (
    EnvActionStatus,
    EnvironmentConfigurator,
    EnvironmentConfiguratorConfig,
)
from automojo.sysman.linux.redhat.runners.models import RunResult


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


def test_set_system_var_writes_new_file():
    """
        set_system_var() writes a fresh profile.d file with the export line.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/profile.d/app.sh")] = RunResult(
        exit_code=1, stdout="", stderr="", command=["cat"],
    )
    ec = EnvironmentConfigurator(runner, EnvironmentConfiguratorConfig(elevated=True))
    result = ec.set_system_var("APP_ENV", "prod", "app.sh")
    testbase.assert_equal(result.status, EnvActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true('export APP_ENV="prod"' in tee_calls[0]["stdin"])
    testbase.assert_true(tee_calls[0]["stdin"].startswith("#!/usr/bin/env bash"))
    return


def test_set_system_var_skipped_when_value_matches():
    """
        set_system_var() returns SKIPPED when the file already has the value.
    """
    existing = '#!/usr/bin/env bash\nexport APP_ENV="prod"\n'
    runner = _MockRunner()
    runner.results[("cat", "/etc/profile.d/app.sh")] = RunResult(
        exit_code=0, stdout=existing, stderr="", command=["cat"],
    )
    ec = EnvironmentConfigurator(runner, EnvironmentConfiguratorConfig())
    result = ec.set_system_var("APP_ENV", "prod", "app.sh")
    testbase.assert_equal(result.status, EnvActionStatus.SKIPPED)
    return


def test_unset_system_var_removes_line():
    """
        unset_system_var() removes a matching export line and keeps shebang.
    """
    existing = '#!/usr/bin/env bash\nexport APP_ENV="prod"\nexport LOG=debug\n'
    runner = _MockRunner()
    runner.results[("cat", "/etc/profile.d/app.sh")] = RunResult(
        exit_code=0, stdout=existing, stderr="", command=["cat"],
    )
    ec = EnvironmentConfigurator(runner, EnvironmentConfiguratorConfig())
    result = ec.unset_system_var("APP_ENV", "app.sh")
    testbase.assert_equal(result.status, EnvActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_false("APP_ENV" in tee_calls[0]["stdin"])
    testbase.assert_true("LOG=debug" in tee_calls[0]["stdin"])
    return


def test_unset_system_var_deletes_empty_file():
    """
        unset_system_var() deletes the file when no managed lines remain.
    """
    existing = '#!/usr/bin/env bash\nexport APP_ENV="prod"\n'
    runner = _MockRunner()
    runner.results[("cat", "/etc/profile.d/app.sh")] = RunResult(
        exit_code=0, stdout=existing, stderr="", command=["cat"],
    )
    ec = EnvironmentConfigurator(runner, EnvironmentConfiguratorConfig())
    result = ec.unset_system_var("APP_ENV", "app.sh")
    testbase.assert_equal(result.status, EnvActionStatus.SUCCESS)
    rm_calls = [c for c in runner.calls if c["args"][:2] == ["rm", "-f"]]
    testbase.assert_equal(len(rm_calls), 1)
    return


def test_set_etc_env_var_appends_line():
    """
        set_etc_env_var() appends a NAME="value" line to /etc/environment.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/environment")] = RunResult(
        exit_code=0, stdout='LANG="en_US.UTF-8"\n', stderr="", command=["cat"],
    )
    ec = EnvironmentConfigurator(runner, EnvironmentConfiguratorConfig(elevated=True))
    ec.set_etc_env_var("APP_ENV", "prod")
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true('APP_ENV="prod"' in tee_calls[0]["stdin"])
    testbase.assert_true("LANG" in tee_calls[0]["stdin"])
    return


def test_query_etc_env_parses_lines():
    """
        query_etc_env() parses NAME="value" lines into a dict.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/environment")] = RunResult(
        exit_code=0, stdout='LANG="en_US.UTF-8"\nAPP="prod"\n', stderr="",
        command=["cat"],
    )
    ec = EnvironmentConfigurator(runner, EnvironmentConfiguratorConfig())
    parsed = ec.query_etc_env()
    testbase.assert_equal(parsed, {"LANG": "en_US.UTF-8", "APP": "prod"})
    return


def test_query_system_parses_export_lines():
    """
        query_system() parses ``export NAME="value"`` lines into a dict.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/profile.d/app.sh")] = RunResult(
        exit_code=0,
        stdout='#!/usr/bin/env bash\nexport LOG=debug\nexport APP_ENV="prod"\n',
        stderr="",
        command=["cat"],
    )
    ec = EnvironmentConfigurator(runner, EnvironmentConfiguratorConfig())
    parsed = ec.query_system("app.sh")
    testbase.assert_equal(parsed, {"LOG": "debug", "APP_ENV": "prod"})
    return
