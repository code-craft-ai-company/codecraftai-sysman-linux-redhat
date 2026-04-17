# Tests for the ConfigurationManager facade.

from dataclasses import dataclass, field
from typing import Callable, Optional

from automojo import testbase

from automojo.sysman.linux.redhat.configurationmanagement import (
    ConfigActionStatus,
    ConfigTemplateError,
    ConfigValidatorNotRegisteredError,
    ConfigurationManager,
    ConfigurationManagerConfig,
)
from automojo.sysman.linux.redhat.runners.models import RunResult


@dataclass
class _MockRunner:
    """
        Scripted runner: the first matching (args, stdin) entry returns the
        queued result.  Unmatched calls fall back to the default result.
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
        stdin: Optional[str] = None,
    ) -> RunResult:
        self.calls.append({"args": list(args), "stdin": stdin, "elevated": elevated})
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


def test_write_skipped_when_content_matches():
    """
        write() returns SKIPPED when current content already matches.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/app.conf")] = RunResult(
        exit_code=0, stdout="hello\n", stderr="", command=["cat"],
    )
    cm = ConfigurationManager(runner, ConfigurationManagerConfig())
    result = cm.write("/etc/app.conf", "hello\n")
    testbase.assert_equal(result.status, ConfigActionStatus.SKIPPED)
    write_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(write_calls, [])
    return


def test_write_dispatches_tee_with_stdin():
    """
        write() calls ``tee <path>`` with the content on stdin.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/app.conf")] = RunResult(
        exit_code=1, stdout="", stderr="", command=["cat"],
    )
    cm = ConfigurationManager(runner, ConfigurationManagerConfig(elevated=True))
    result = cm.write("/etc/app.conf", "new content\n")
    testbase.assert_equal(result.status, ConfigActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_equal(tee_calls[0]["args"], ["tee", "/etc/app.conf"])
    testbase.assert_equal(tee_calls[0]["stdin"], "new content\n")
    testbase.assert_true(tee_calls[0]["elevated"])
    return


def test_write_dry_run_produces_diff():
    """
        dry_run write() must populate the diff field and not call tee.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/app.conf")] = RunResult(
        exit_code=0, stdout="old\n", stderr="", command=["cat"],
    )
    cm = ConfigurationManager(runner, ConfigurationManagerConfig(dry_run=True))
    result = cm.write("/etc/app.conf", "new\n")
    testbase.assert_equal(result.status, ConfigActionStatus.DRY_RUN)
    testbase.assert_true("-old" in result.diff)
    testbase.assert_true("+new" in result.diff)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(tee_calls, [])
    return


def test_write_template_renders_and_writes():
    """
        write_template() renders the template and delegates to write().
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/motd")] = RunResult(
        exit_code=1, stdout="", stderr="", command=["cat"],
    )
    cm = ConfigurationManager(runner, ConfigurationManagerConfig())
    cm.write_template(
        "/etc/motd",
        "hello $NAME\n",
        {"NAME": "world"},
    )
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(tee_calls[0]["stdin"], "hello world\n")
    return


def test_write_template_raises_on_missing_variable():
    """
        write_template() raises ConfigTemplateError when a variable is missing.
    """
    runner = _MockRunner()
    cm = ConfigurationManager(runner, ConfigurationManagerConfig())
    raised = False
    try:
        cm.write_template("/etc/motd", "hello $NAME\n", {})
    except ConfigTemplateError:
        raised = True
    testbase.assert_true(raised)
    return


def test_backup_produces_cp_command():
    """
        backup() runs ``cp -p <path> <timestamped>``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["cp"],
    ))
    cm = ConfigurationManager(runner, ConfigurationManagerConfig(elevated=True))
    result = cm.backup("/etc/app.conf")
    testbase.assert_equal(result.status, ConfigActionStatus.SUCCESS)
    testbase.assert_true(result.backup_path is not None)
    testbase.assert_true(result.backup_path.startswith("/etc/app.conf.bak."))
    testbase.assert_equal(runner.calls[-1]["args"][0], "cp")
    return


def test_restore_strips_backup_suffix():
    """
        restore() copies the backup over the original path (without ``.bak.*``).
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["cp"],
    ))
    cm = ConfigurationManager(runner, ConfigurationManagerConfig())
    result = cm.restore("/etc/app.conf.bak.20240101T000000")
    testbase.assert_equal(result.status, ConfigActionStatus.SUCCESS)
    testbase.assert_equal(result.path, "/etc/app.conf")
    testbase.assert_equal(
        runner.calls[-1]["args"],
        ["cp", "-p", "/etc/app.conf.bak.20240101T000000", "/etc/app.conf"],
    )
    return


def test_diff_reports_changes():
    """
        diff() reports has_diff=True when current and desired differ.
    """
    runner = _MockRunner()
    runner.results[("cat", "/etc/app.conf")] = RunResult(
        exit_code=0, stdout="a\n", stderr="", command=["cat"],
    )
    cm = ConfigurationManager(runner, ConfigurationManagerConfig())
    result = cm.diff("/etc/app.conf", "b\n")
    testbase.assert_true(result.has_diff)
    testbase.assert_true("+b" in result.diff)
    return


def test_validate_unknown_validator_raises():
    """
        validate() raises ConfigValidatorNotRegisteredError for unknown ids.
    """
    runner = _MockRunner()
    cm = ConfigurationManager(runner, ConfigurationManagerConfig())
    raised = False
    try:
        cm.validate("/etc/nginx.conf", "unknown-validator")
    except ConfigValidatorNotRegisteredError:
        raised = True
    testbase.assert_true(raised)
    return


def test_validate_runs_registered_command():
    """
        validate() dispatches the registered command and maps exit code to valid.
    """
    runner = _MockRunner()
    runner.results[("nginx", "-t", "-c", "/etc/nginx/nginx.conf")] = RunResult(
        exit_code=0, stdout="", stderr="ok", command=["nginx"],
    )
    cm = ConfigurationManager(runner, ConfigurationManagerConfig())
    result = cm.validate("/etc/nginx/nginx.conf", "nginx")
    testbase.assert_true(result.valid)
    testbase.assert_equal(result.exit_code, 0)
    return


def test_set_permissions_dispatches_chmod():
    """
        set_permissions() emits ``chmod <mode> <path>``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["chmod"],
    ))
    cm = ConfigurationManager(runner, ConfigurationManagerConfig(elevated=True))
    result = cm.set_permissions("/etc/app.conf", "0644")
    testbase.assert_equal(result.status, ConfigActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"], ["chmod", "0644", "/etc/app.conf"],
    )
    return


def test_set_ownership_dispatches_chown():
    """
        set_ownership() emits ``chown user:group <path>``.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="", stderr="", command=["chown"],
    ))
    cm = ConfigurationManager(runner, ConfigurationManagerConfig())
    result = cm.set_ownership("/etc/app.conf", user="app", group="app")
    testbase.assert_equal(result.status, ConfigActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"], ["chown", "app:app", "/etc/app.conf"],
    )
    return


def test_set_ini_key_writes_when_changed():
    """
        set_ini_key() writes the updated INI file when the key changes.
    """
    original = "[core]\nkey=oldvalue\n\n"
    runner = _MockRunner()
    runner.results[("cat", "/etc/app.ini")] = RunResult(
        exit_code=0, stdout=original, stderr="", command=["cat"],
    )
    cm = ConfigurationManager(runner, ConfigurationManagerConfig(elevated=True))
    result = cm.set_ini_key("/etc/app.ini", "core", "key", "newvalue")
    testbase.assert_equal(result.status, ConfigActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true("newvalue" in tee_calls[0]["stdin"])
    return


def test_set_ini_key_skipped_when_value_matches():
    """
        set_ini_key() returns SKIPPED when the requested value is already set.
    """
    original = "[core]\nkey=same\n\n"
    runner = _MockRunner()
    runner.results[("cat", "/etc/app.ini")] = RunResult(
        exit_code=0, stdout=original, stderr="", command=["cat"],
    )
    cm = ConfigurationManager(runner, ConfigurationManagerConfig())
    result = cm.set_ini_key("/etc/app.ini", "core", "key", "same")
    testbase.assert_equal(result.status, ConfigActionStatus.SKIPPED)
    return


def test_remove_ini_key_removes_existing_key():
    """
        remove_ini_key() writes the updated file when the key is removed.
    """
    original = "[core]\nkey=value\nother=1\n\n"
    runner = _MockRunner()
    runner.results[("cat", "/etc/app.ini")] = RunResult(
        exit_code=0, stdout=original, stderr="", command=["cat"],
    )
    cm = ConfigurationManager(runner, ConfigurationManagerConfig())
    result = cm.remove_ini_key("/etc/app.ini", "core", "key")
    testbase.assert_equal(result.status, ConfigActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_false("key=value" in tee_calls[0]["stdin"])
    return
