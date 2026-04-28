# Tests for the ResourceControlManager facade.

from dataclasses import dataclass, field
from typing import Callable, Optional

from automojo import testbase

from automojo.sysman.linux.redhat.resourcecontrolmanagement import (
    CgroupUsage,
    ResourceActionResult,
    ResourceActionStatus,
    ResourceControlManager,
    ResourceControlManagerConfig,
    SliceSpec,
    _cpu_quota_matches,
    _dropin_is_effectively_empty,
    _merge_dropin_properties,
    _parse_cgtop_output,
    _parse_show_output,
    _render_slice_unit_body,
    _strip_dropin_property,
    _value_for_key_in_dropin,
)
from automojo.sysman.linux.redhat.runners.models import RunResult


# ----------------------------------------------------------------------
# Mock runner and service manager
# ----------------------------------------------------------------------


@dataclass
class _MockRunner:
    """
        Minimal ICommandRunner that captures every call and
        replays pre-seeded RunResult values keyed by argv tuple.
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
        call_record = {
            "args": list(args),
            "elevated": elevated,
            "stdin": stdin,
        }
        self.calls.append(call_record)
        key = tuple(args)
        if key in self.results:
            return self.results[key]
        if self.default_result is not None:
            return self.default_result
        return RunResult(
            exit_code=0, stdout="", stderr="", command=list(args),
        )

    def add_aspect(self, aspect) -> None:
        return

    def remove_aspect(self, aspect) -> None:
        return

    def get_metrics(self):
        return None


@dataclass
class _ReloadResult:
    """
        Lightweight stand-in for ServiceManager's reload result.
    """
    status: object = ResourceActionStatus.SUCCESS
    exit_code: Optional[int] = 0
    stdout: Optional[str] = ""
    stderr: Optional[str] = ""
    command: Optional[list] = None


@dataclass
class _MockServiceManager:
    """
        Minimal ServiceManager stand-in capturing daemon_reload
        / enable invocations.
    """

    reload_calls: int = 0
    enable_calls: list = field(default_factory=list)
    reload_result: _ReloadResult = field(default_factory=_ReloadResult)
    enable_result: _ReloadResult = field(default_factory=_ReloadResult)

    def daemon_reload(self) -> _ReloadResult:
        self.reload_calls = self.reload_calls + 1
        return self.reload_result

    def enable(self, name: str) -> _ReloadResult:
        self.enable_calls.append(name)
        return self.enable_result


def _seed(
    runner: _MockRunner,
    *,
    args: list[str],
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> None:
    """
        Helper to seed a runner result keyed by argv tuple.
    """
    runner.results[tuple(args)] = RunResult(
        exit_code=exit_code, stdout=stdout, stderr=stderr, command=list(args),
    )
    return


def _make_manager(
    runner: _MockRunner,
    *,
    config: Optional[ResourceControlManagerConfig] = None,
    sm: Optional[_MockServiceManager] = None,
) -> ResourceControlManager:
    """
        Helper to construct a manager with sensible defaults.
    """
    if config is None:
        config = ResourceControlManagerConfig(elevated=True)
    if sm is None:
        sm = _MockServiceManager()
    rtnval = ResourceControlManager(
        runner, config, service_manager=sm,
    )
    return rtnval


# ----------------------------------------------------------------------
# Parser unit tests
# ----------------------------------------------------------------------


def test_parse_show_output_basic():
    """
        _parse_show_output() turns key=value lines into a dict.
    """
    payload = (
        "CPUQuotaPerSecUSec=infinity\n"
        "MemoryMax=536870912\n"
        "Slice=system.slice\n"
        "\n"
        "# comment line\n"
    )
    parsed = _parse_show_output(stdout=payload)
    testbase.assert_equal(parsed["CPUQuotaPerSecUSec"], "infinity")
    testbase.assert_equal(parsed["MemoryMax"], "536870912")
    testbase.assert_equal(parsed["Slice"], "system.slice")
    return


def test_parse_cgtop_output_records():
    """
        _parse_cgtop_output() builds CgroupUsage records, with
        '-' tokens parsed as None.
    """
    payload = (
        "Control Group        Tasks   %CPU   Memory  Input/s Output/s\n"
        "/                    100     5.0    1048576 -       -\n"
        "/system.slice        50      2.5    524288  1024    2048\n"
    )
    rows = _parse_cgtop_output(stdout=payload)
    testbase.assert_equal(len(rows), 2)
    testbase.assert_equal(rows[0].path, "/")
    testbase.assert_equal(rows[0].tasks, 100)
    testbase.assert_equal(rows[0].cpu_percent, 5.0)
    testbase.assert_equal(rows[0].memory_bytes, 1048576)
    testbase.assert_true(rows[0].input_per_s is None)
    testbase.assert_true(rows[0].output_per_s is None)
    testbase.assert_equal(rows[1].path, "/system.slice")
    testbase.assert_equal(rows[1].input_per_s, 1024)
    testbase.assert_equal(rows[1].output_per_s, 2048)
    return


def test_render_slice_unit_body_includes_properties():
    """
        _render_slice_unit_body() emits [Unit] and [Slice]
        sections including every directive.
    """
    spec = SliceSpec(
        name="workers",
        description="Worker pool",
        before=["graphical.target"],
        wants=["network.target"],
        properties={"CPUQuota": "200%", "MemoryMax": "2G"},
    )
    body = _render_slice_unit_body(spec=spec)
    testbase.assert_true("[Unit]" in body)
    testbase.assert_true("Description=Worker pool" in body)
    testbase.assert_true("Before=graphical.target" in body)
    testbase.assert_true("Wants=network.target" in body)
    testbase.assert_true("[Slice]" in body)
    testbase.assert_true("CPUQuota=200%" in body)
    testbase.assert_true("MemoryMax=2G" in body)
    testbase.assert_true(body.endswith("\n"))
    return


def test_merge_dropin_creates_new_section():
    """
        _merge_dropin_properties() inserts a new section when
        none exists and reports every key as changed.
    """
    merged, changed, skipped = _merge_dropin_properties(
        existing_content="",
        section_name="Service",
        properties={"CPUQuota": "50%", "MemoryMax": "1G"},
    )
    testbase.assert_true("[Service]" in merged)
    testbase.assert_true("CPUQuota=50%" in merged)
    testbase.assert_true("MemoryMax=1G" in merged)
    testbase.assert_equal(sorted(changed), ["CPUQuota", "MemoryMax"])
    testbase.assert_equal(skipped, [])
    return


def test_merge_dropin_skips_matching_keys():
    """
        _merge_dropin_properties() reports a key as skipped when
        the existing value matches.
    """
    existing = (
        "[Service]\n"
        "CPUQuota=50%\n"
        "MemoryMax=1G\n"
    )
    _, changed, skipped = _merge_dropin_properties(
        existing_content=existing,
        section_name="Service",
        properties={"CPUQuota": "50%", "MemoryMax": "2G"},
    )
    testbase.assert_equal(changed, ["MemoryMax"])
    testbase.assert_equal(skipped, ["CPUQuota"])
    return


def test_merge_dropin_preserves_other_directives():
    """
        _merge_dropin_properties() preserves untouched lines.
    """
    existing = (
        "[Service]\n"
        "CPUQuota=50%\n"
        "Restart=always\n"
    )
    merged, _, _ = _merge_dropin_properties(
        existing_content=existing,
        section_name="Service",
        properties={"CPUQuota": "75%"},
    )
    testbase.assert_true("Restart=always" in merged)
    testbase.assert_true("CPUQuota=75%" in merged)
    return


def test_strip_dropin_property_removes_line():
    """
        _strip_dropin_property() drops the targeted directive.
    """
    existing = (
        "[Service]\n"
        "CPUQuota=50%\n"
        "MemoryMax=1G\n"
    )
    new_content, removed = _strip_dropin_property(
        existing_content=existing,
        section_name="Service",
        key="CPUQuota",
    )
    testbase.assert_true(removed)
    testbase.assert_true("CPUQuota" not in new_content)
    testbase.assert_true("MemoryMax=1G" in new_content)
    return


def test_strip_dropin_property_returns_false_when_absent():
    """
        _strip_dropin_property() returns removed=False when the
        key is not present.
    """
    existing = "[Service]\nMemoryMax=1G\n"
    _, removed = _strip_dropin_property(
        existing_content=existing,
        section_name="Service",
        key="CPUQuota",
    )
    testbase.assert_false(removed)
    return


def test_dropin_is_effectively_empty_skips_headers_only():
    """
        _dropin_is_effectively_empty() returns True for content
        that has only headers, comments, and whitespace.
    """
    payload = (
        "[Service]\n"
        "\n"
        "# only headers and comments\n"
    )
    is_empty = _dropin_is_effectively_empty(content=payload)
    testbase.assert_true(is_empty)
    return


def test_value_for_key_in_dropin_round_trip():
    """
        _value_for_key_in_dropin() finds an existing value.
    """
    existing = (
        "[Service]\n"
        "Slice=workers.slice\n"
    )
    value = _value_for_key_in_dropin(
        content=existing, section_name="Service", key="Slice",
    )
    testbase.assert_equal(value, "workers.slice")
    return


def test_cpu_quota_matches_handles_usec_form():
    """
        _cpu_quota_matches() treats "50%" as equivalent to
        500000us.
    """
    matches = _cpu_quota_matches(requested="50%", current="500000us")
    testbase.assert_true(matches)
    return


# ----------------------------------------------------------------------
# query_properties
# ----------------------------------------------------------------------


def test_query_properties_returns_parsed_dict():
    """
        query_properties() invokes systemctl show and returns
        a parsed dict.
    """
    runner = _MockRunner()
    args = [
        "systemctl", "show", "nginx.service",
        "--property=CPUQuotaPerSecUSec,MemoryMax",
    ]
    _seed(
        runner, args=args,
        stdout="CPUQuotaPerSecUSec=infinity\nMemoryMax=536870912\n",
    )
    mgr = _make_manager(runner)
    parsed = mgr.query_properties(
        unit="nginx.service",
        keys=["CPUQuotaPerSecUSec", "MemoryMax"],
    )
    testbase.assert_equal(parsed["CPUQuotaPerSecUSec"], "infinity")
    testbase.assert_equal(parsed["MemoryMax"], "536870912")
    last_call = runner.calls[-1]
    testbase.assert_equal(last_call["args"], args)
    testbase.assert_false(last_call["elevated"])
    return


def test_query_properties_returns_empty_on_failure():
    """
        query_properties() returns {} when systemctl show exits
        non-zero (unit does not exist).
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=1, stdout="", stderr="No such unit", command=["systemctl"],
    )
    mgr = _make_manager(runner)
    parsed = mgr.query_properties(
        unit="missing.service", keys=["MemoryMax"],
    )
    testbase.assert_equal(parsed, {})
    return


# ----------------------------------------------------------------------
# set_properties_transient
# ----------------------------------------------------------------------


def _seed_show(
    runner: _MockRunner,
    *,
    unit: str,
    keys: list[str],
    values: dict[str, str],
) -> None:
    """
        Seed a systemctl show response for ``keys`` returning
        ``values``.
    """
    args = [
        "systemctl", "show", unit,
        "--property={}".format(",".join(keys)),
    ]
    body_parts = []
    for key in keys:
        body_parts.append("{}={}".format(key, values.get(key, "")))
    payload = "\n".join(body_parts) + "\n"
    _seed(runner, args=args, stdout=payload)
    return


def test_set_properties_transient_happy_path():
    """
        set_properties_transient() dispatches systemctl
        set-property --runtime when keys differ.
    """
    runner = _MockRunner()
    _seed_show(
        runner, unit="nginx.service", keys=["MemoryMax", "CPUQuota"],
        values={"MemoryMax": "infinity", "CPUQuota": "50%"},
    )
    mgr = _make_manager(runner)
    result = mgr.set_properties_transient(
        unit="nginx.service",
        properties={"MemoryMax": "536870912", "CPUQuota": "75%"},
    )
    testbase.assert_equal(result.status, ResourceActionStatus.SUCCESS)
    set_calls = [
        c for c in runner.calls
        if len(c["args"]) >= 3 and c["args"][1] == "set-property"
    ]
    testbase.assert_equal(len(set_calls), 1)
    testbase.assert_true("--runtime" in set_calls[0]["args"])
    testbase.assert_true("nginx.service" in set_calls[0]["args"])
    testbase.assert_true("MemoryMax=536870912" in set_calls[0]["args"])
    testbase.assert_true("CPUQuota=75%" in set_calls[0]["args"])
    testbase.assert_true(set_calls[0]["elevated"])
    testbase.assert_equal(sorted(result.changed_keys), ["CPUQuota", "MemoryMax"])
    return


def test_set_properties_transient_partial_skipped():
    """
        Partial-skip path: one key already matches, the other
        differs.  Result is SUCCESS with both changed_keys and
        skipped_keys populated.
    """
    runner = _MockRunner()
    _seed_show(
        runner, unit="nginx.service", keys=["MemoryMax", "CPUQuota"],
        values={"MemoryMax": "536870912", "CPUQuota": "50%"},
    )
    mgr = _make_manager(runner)
    result = mgr.set_properties_transient(
        unit="nginx.service",
        properties={"MemoryMax": "536870912", "CPUQuota": "75%"},
    )
    testbase.assert_equal(result.status, ResourceActionStatus.SUCCESS)
    testbase.assert_equal(result.skipped_keys, ["MemoryMax"])
    testbase.assert_equal(result.changed_keys, ["CPUQuota"])
    return


def test_set_properties_transient_fully_skipped():
    """
        SKIPPED when every requested key already matches.
    """
    runner = _MockRunner()
    _seed_show(
        runner, unit="nginx.service", keys=["MemoryMax"],
        values={"MemoryMax": "536870912"},
    )
    mgr = _make_manager(runner)
    result = mgr.set_properties_transient(
        unit="nginx.service",
        properties={"MemoryMax": "536870912"},
    )
    testbase.assert_equal(result.status, ResourceActionStatus.SKIPPED)
    testbase.assert_equal(result.skipped_keys, ["MemoryMax"])
    testbase.assert_equal(result.changed_keys, [])
    set_calls = [
        c for c in runner.calls
        if len(c["args"]) >= 3 and c["args"][1] == "set-property"
    ]
    testbase.assert_equal(len(set_calls), 0)
    return


def test_set_properties_transient_dry_run():
    """
        DRY_RUN short-circuits the mutation but still queries
        current values for the diff.
    """
    runner = _MockRunner()
    _seed_show(
        runner, unit="nginx.service", keys=["CPUQuota"],
        values={"CPUQuota": "50%"},
    )
    config = ResourceControlManagerConfig(dry_run=True, elevated=True)
    mgr = _make_manager(runner, config=config)
    result = mgr.set_properties_transient(
        unit="nginx.service", properties={"CPUQuota": "75%"},
    )
    testbase.assert_equal(result.status, ResourceActionStatus.DRY_RUN)
    set_calls = [
        c for c in runner.calls
        if len(c["args"]) >= 3 and c["args"][1] == "set-property"
    ]
    testbase.assert_equal(len(set_calls), 0)
    return


def test_set_properties_transient_failure_propagates():
    """
        FAILED bubbles up the runner exit code / stderr.
    """
    runner = _MockRunner()
    _seed_show(
        runner, unit="nginx.service", keys=["MemoryMax"],
        values={"MemoryMax": "infinity"},
    )
    set_args = [
        "systemctl", "set-property", "--runtime", "nginx.service",
        "MemoryMax=536870912",
    ]
    _seed(
        runner, args=set_args, exit_code=1,
        stderr="permission denied",
    )
    mgr = _make_manager(runner)
    result = mgr.set_properties_transient(
        unit="nginx.service",
        properties={"MemoryMax": "536870912"},
    )
    testbase.assert_equal(result.status, ResourceActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 1)
    testbase.assert_equal(result.stderr, "permission denied")
    return


# ----------------------------------------------------------------------
# set_properties_persistent
# ----------------------------------------------------------------------


def test_set_properties_persistent_writes_dropin():
    """
        Writes via tee, ensures parent directory, delegates
        daemon-reload.
    """
    runner = _MockRunner()
    # cat returns non-zero -> file does not yet exist
    runner.default_result = RunResult(
        exit_code=1, stdout="", stderr="No such file", command=["cat"],
    )
    mkdir_args = ["mkdir", "-p", "/etc/systemd/system/nginx.service.d"]
    _seed(runner, args=mkdir_args, exit_code=0)
    tee_args = [
        "tee", "/etc/systemd/system/nginx.service.d/10-resource-control.conf",
    ]
    _seed(runner, args=tee_args, exit_code=0)
    sm = _MockServiceManager()
    mgr = _make_manager(runner, sm=sm)
    result = mgr.set_properties_persistent(
        unit="nginx.service",
        properties={"CPUQuota": "50%"},
    )
    testbase.assert_equal(result.status, ResourceActionStatus.SUCCESS)
    testbase.assert_equal(result.changed_keys, ["CPUQuota"])
    testbase.assert_equal(sm.reload_calls, 1)
    tee_calls = [c for c in runner.calls if c["args"][:1] == ["tee"]]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true("CPUQuota=50%" in tee_calls[0]["stdin"])
    return


def test_set_properties_persistent_skipped_when_match():
    """
        SKIPPED when the on-disk drop-in already declares the
        requested directive.  daemon-reload is NOT invoked.
    """
    runner = _MockRunner()
    cat_args = [
        "cat", "/etc/systemd/system/nginx.service.d/10-resource-control.conf",
    ]
    _seed(
        runner, args=cat_args,
        stdout="[Service]\nCPUQuota=50%\n",
    )
    sm = _MockServiceManager()
    mgr = _make_manager(runner, sm=sm)
    result = mgr.set_properties_persistent(
        unit="nginx.service",
        properties={"CPUQuota": "50%"},
    )
    testbase.assert_equal(result.status, ResourceActionStatus.SKIPPED)
    testbase.assert_equal(sm.reload_calls, 0)
    return


def test_set_properties_persistent_never_invokes_systemctl_daemon_reload():
    """
        The feature never spells systemctl daemon-reload directly;
        it always routes through ServiceManager.daemon_reload.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=1, stdout="", stderr="", command=["cat"],
    )
    mkdir_args = ["mkdir", "-p", "/etc/systemd/system/nginx.service.d"]
    _seed(runner, args=mkdir_args, exit_code=0)
    tee_args = [
        "tee", "/etc/systemd/system/nginx.service.d/10-resource-control.conf",
    ]
    _seed(runner, args=tee_args, exit_code=0)
    sm = _MockServiceManager()
    mgr = _make_manager(runner, sm=sm)
    mgr.set_properties_persistent(
        unit="nginx.service", properties={"CPUQuota": "50%"},
    )
    raw_reloads = [
        c for c in runner.calls
        if c["args"][:2] == ["systemctl", "daemon-reload"]
    ]
    testbase.assert_equal(raw_reloads, [])
    testbase.assert_equal(sm.reload_calls, 1)
    return


def test_set_properties_persistent_dry_run():
    """
        Dry-run short-circuits the write and the daemon-reload
        delegation.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=1, stdout="", stderr="", command=["cat"],
    )
    config = ResourceControlManagerConfig(dry_run=True, elevated=True)
    sm = _MockServiceManager()
    mgr = _make_manager(runner, config=config, sm=sm)
    result = mgr.set_properties_persistent(
        unit="nginx.service", properties={"CPUQuota": "50%"},
    )
    testbase.assert_equal(result.status, ResourceActionStatus.DRY_RUN)
    tee_calls = [c for c in runner.calls if c["args"][:1] == ["tee"]]
    testbase.assert_equal(tee_calls, [])
    testbase.assert_equal(sm.reload_calls, 0)
    return


# ----------------------------------------------------------------------
# remove_property
# ----------------------------------------------------------------------


def test_remove_property_strips_directive_and_writes_back():
    """
        remove_property() rewrites the drop-in without the
        targeted directive when other directives remain, then
        delegates daemon-reload.
    """
    runner = _MockRunner()
    cat_args = [
        "cat", "/etc/systemd/system/nginx.service.d/10-resource-control.conf",
    ]
    _seed(
        runner, args=cat_args,
        stdout="[Service]\nCPUQuota=50%\nMemoryMax=1G\n",
    )
    mkdir_args = ["mkdir", "-p", "/etc/systemd/system/nginx.service.d"]
    _seed(runner, args=mkdir_args, exit_code=0)
    tee_args = [
        "tee", "/etc/systemd/system/nginx.service.d/10-resource-control.conf",
    ]
    _seed(runner, args=tee_args, exit_code=0)
    sm = _MockServiceManager()
    mgr = _make_manager(runner, sm=sm)
    result = mgr.remove_property(
        unit="nginx.service", key="CPUQuota",
    )
    testbase.assert_equal(result.status, ResourceActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][:1] == ["tee"]]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true("CPUQuota" not in tee_calls[0]["stdin"])
    testbase.assert_true("MemoryMax=1G" in tee_calls[0]["stdin"])
    testbase.assert_equal(sm.reload_calls, 1)
    return


def test_remove_property_rms_empty_dropin():
    """
        remove_property() rm -f's the drop-in file when the
        last directive is removed.
    """
    runner = _MockRunner()
    cat_args = [
        "cat", "/etc/systemd/system/nginx.service.d/10-resource-control.conf",
    ]
    _seed(
        runner, args=cat_args,
        stdout="[Service]\nCPUQuota=50%\n",
    )
    rm_args = [
        "rm", "-f",
        "/etc/systemd/system/nginx.service.d/10-resource-control.conf",
    ]
    _seed(runner, args=rm_args, exit_code=0)
    sm = _MockServiceManager()
    mgr = _make_manager(runner, sm=sm)
    result = mgr.remove_property(
        unit="nginx.service", key="CPUQuota",
    )
    testbase.assert_equal(result.status, ResourceActionStatus.SUCCESS)
    rm_calls = [c for c in runner.calls if c["args"][:2] == ["rm", "-f"]]
    testbase.assert_equal(len(rm_calls), 1)
    testbase.assert_equal(sm.reload_calls, 1)
    return


def test_remove_property_skipped_when_absent():
    """
        SKIPPED when the drop-in does not contain the directive.
    """
    runner = _MockRunner()
    cat_args = [
        "cat", "/etc/systemd/system/nginx.service.d/10-resource-control.conf",
    ]
    _seed(
        runner, args=cat_args,
        stdout="[Service]\nMemoryMax=1G\n",
    )
    sm = _MockServiceManager()
    mgr = _make_manager(runner, sm=sm)
    result = mgr.remove_property(
        unit="nginx.service", key="CPUQuota",
    )
    testbase.assert_equal(result.status, ResourceActionStatus.SKIPPED)
    testbase.assert_equal(sm.reload_calls, 0)
    return


def test_remove_property_skipped_when_dropin_missing():
    """
        SKIPPED when the drop-in file does not exist at all.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=1, stdout="", stderr="No such file", command=["cat"],
    )
    sm = _MockServiceManager()
    mgr = _make_manager(runner, sm=sm)
    result = mgr.remove_property(
        unit="nginx.service", key="CPUQuota",
    )
    testbase.assert_equal(result.status, ResourceActionStatus.SKIPPED)
    return


# ----------------------------------------------------------------------
# define_slice / set_slice_properties / attach_to_slice
# ----------------------------------------------------------------------


def test_define_slice_writes_unit_file_and_reloads():
    """
        define_slice() writes the slice unit file via tee and
        delegates daemon-reload.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=1, stdout="", stderr="", command=["cat"],
    )
    mkdir_args = ["mkdir", "-p", "/etc/systemd/system"]
    _seed(runner, args=mkdir_args, exit_code=0)
    tee_args = ["tee", "/etc/systemd/system/workers.slice"]
    _seed(runner, args=tee_args, exit_code=0)
    sm = _MockServiceManager()
    mgr = _make_manager(runner, sm=sm)
    result = mgr.define_slice(
        spec=SliceSpec(
            name="workers",
            description="Worker pool",
            properties={"CPUQuota": "200%"},
        ),
    )
    testbase.assert_equal(result.status, ResourceActionStatus.SUCCESS)
    testbase.assert_equal(sm.reload_calls, 1)
    tee_calls = [c for c in runner.calls if c["args"][:1] == ["tee"]]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true("[Slice]" in tee_calls[0]["stdin"])
    testbase.assert_true("CPUQuota=200%" in tee_calls[0]["stdin"])
    return


def test_define_slice_skipped_when_content_matches():
    """
        define_slice() returns SKIPPED when the on-disk file
        already matches.
    """
    spec = SliceSpec(
        name="workers",
        description="Worker pool",
        properties={"CPUQuota": "200%"},
    )
    rendered = _render_slice_unit_body(spec=spec)
    runner = _MockRunner()
    cat_args = ["cat", "/etc/systemd/system/workers.slice"]
    _seed(runner, args=cat_args, stdout=rendered)
    sm = _MockServiceManager()
    mgr = _make_manager(runner, sm=sm)
    result = mgr.define_slice(spec=spec)
    testbase.assert_equal(result.status, ResourceActionStatus.SKIPPED)
    testbase.assert_equal(sm.reload_calls, 0)
    return


def test_define_slice_auto_enable_calls_service_manager():
    """
        define_slice(auto_enable=True) calls ServiceManager.enable
        on success.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=1, stdout="", stderr="", command=["cat"],
    )
    _seed(runner, args=["mkdir", "-p", "/etc/systemd/system"], exit_code=0)
    _seed(runner, args=["tee", "/etc/systemd/system/workers.slice"], exit_code=0)
    sm = _MockServiceManager()
    mgr = _make_manager(runner, sm=sm)
    mgr.define_slice(
        spec=SliceSpec(name="workers", properties={"CPUQuota": "100%"}),
        auto_enable=True,
    )
    testbase.assert_equal(sm.enable_calls, ["workers.slice"])
    return


def test_set_slice_properties_uses_dropin_pattern():
    """
        set_slice_properties() routes through the persistent
        drop-in pattern with [Slice] section.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=1, stdout="", stderr="", command=["cat"],
    )
    _seed(
        runner,
        args=["mkdir", "-p", "/etc/systemd/system/workers.slice.d"],
        exit_code=0,
    )
    _seed(
        runner,
        args=[
            "tee",
            "/etc/systemd/system/workers.slice.d/10-resource-control.conf",
        ],
        exit_code=0,
    )
    sm = _MockServiceManager()
    mgr = _make_manager(runner, sm=sm)
    result = mgr.set_slice_properties(
        name="workers", properties={"MemoryMax": "1G"},
    )
    testbase.assert_equal(result.status, ResourceActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][:1] == ["tee"]]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true("[Slice]" in tee_calls[0]["stdin"])
    testbase.assert_true("MemoryMax=1G" in tee_calls[0]["stdin"])
    return


def test_attach_to_slice_writes_slice_directive():
    """
        attach_to_slice() upserts Slice=<name>.slice into the
        unit's drop-in.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=1, stdout="", stderr="", command=["cat"],
    )
    _seed(
        runner,
        args=["mkdir", "-p", "/etc/systemd/system/nginx.service.d"],
        exit_code=0,
    )
    _seed(
        runner,
        args=[
            "tee",
            "/etc/systemd/system/nginx.service.d/10-resource-control.conf",
        ],
        exit_code=0,
    )
    sm = _MockServiceManager()
    mgr = _make_manager(runner, sm=sm)
    result = mgr.attach_to_slice(
        unit="nginx.service", slice_name="workers",
    )
    testbase.assert_equal(result.status, ResourceActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][:1] == ["tee"]]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true("Slice=workers.slice" in tee_calls[0]["stdin"])
    return


# ----------------------------------------------------------------------
# remove_slice
# ----------------------------------------------------------------------


def test_remove_slice_strict_raises_when_attached_units_present():
    """
        remove_slice(strict=True) returns FAILED without
        mutation when attached units exist.
    """
    runner = _MockRunner()
    # ls returns one .d directory
    _seed(
        runner,
        args=["ls", "-1", "/etc/systemd/system"],
        stdout="nginx.service.d\nworkers.slice\n",
    )
    # Reading the drop-in for nginx.service shows it's attached.
    cat_args_nginx = [
        "cat", "/etc/systemd/system/nginx.service.d/10-resource-control.conf",
    ]
    _seed(
        runner, args=cat_args_nginx,
        stdout="[Service]\nSlice=workers.slice\n",
    )
    # The slice file itself exists.
    _seed(
        runner,
        args=["cat", "/etc/systemd/system/workers.slice"],
        stdout="[Unit]\n[Slice]\n",
    )
    sm = _MockServiceManager()
    mgr = _make_manager(runner, sm=sm)
    result = mgr.remove_slice(name="workers", strict=True)
    testbase.assert_equal(result.status, ResourceActionStatus.FAILED)
    rm_calls = [c for c in runner.calls if c["args"][:2] == ["rm", "-f"]]
    testbase.assert_equal(rm_calls, [])
    testbase.assert_equal(sm.reload_calls, 0)
    return


def test_remove_slice_skipped_when_nothing_to_do():
    """
        remove_slice() returns SKIPPED when the slice file is
        absent and no attached units exist.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=1, stdout="", stderr="", command=["cat"],
    )
    _seed(
        runner,
        args=["ls", "-1", "/etc/systemd/system"],
        stdout="",
    )
    sm = _MockServiceManager()
    mgr = _make_manager(runner, sm=sm)
    result = mgr.remove_slice(name="ghost")
    testbase.assert_equal(result.status, ResourceActionStatus.SKIPPED)
    testbase.assert_equal(sm.reload_calls, 0)
    return


# ----------------------------------------------------------------------
# run_scoped
# ----------------------------------------------------------------------


def test_run_scoped_assembles_command_with_default_slice():
    """
        run_scoped() builds the systemd-run argv with the
        default scope slice.
    """
    runner = _MockRunner()
    mgr = _make_manager(runner)
    result = mgr.run_scoped(
        command=["/usr/bin/heavy", "--flag"],
        properties={"MemoryMax": "1G"},
    )
    testbase.assert_equal(result.status, ResourceActionStatus.SUCCESS)
    last_call = runner.calls[-1]
    expected_prefix = [
        "systemd-run", "--scope", "--slice", "system.slice",
        "--property=MemoryMax=1G", "/usr/bin/heavy", "--flag",
    ]
    testbase.assert_equal(last_call["args"], expected_prefix)
    testbase.assert_true(last_call["elevated"])
    return


def test_run_scoped_uses_supplied_slice():
    """
        run_scoped() honours the explicit slice argument.
    """
    runner = _MockRunner()
    mgr = _make_manager(runner)
    mgr.run_scoped(command=["/bin/true"], slice_="workers.slice")
    last_call = runner.calls[-1]
    testbase.assert_true("workers.slice" in last_call["args"])
    return


def test_run_scoped_dry_run():
    """
        run_scoped() short-circuits in dry-run.
    """
    runner = _MockRunner()
    config = ResourceControlManagerConfig(dry_run=True)
    mgr = _make_manager(runner, config=config)
    result = mgr.run_scoped(command=["/bin/true"])
    testbase.assert_equal(result.status, ResourceActionStatus.DRY_RUN)
    testbase.assert_equal(runner.calls, [])
    return


def test_run_scoped_failure_propagates():
    """
        run_scoped() returns FAILED with exit code / stderr on
        non-zero runner exit.
    """
    runner = _MockRunner()
    args = ["systemd-run", "--scope", "--slice", "system.slice", "/bin/false"]
    _seed(runner, args=args, exit_code=1, stderr="boom")
    mgr = _make_manager(runner)
    result = mgr.run_scoped(command=["/bin/false"])
    testbase.assert_equal(result.status, ResourceActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 1)
    testbase.assert_equal(result.stderr, "boom")
    return


# ----------------------------------------------------------------------
# cgls / cgtop / read_cgroup_attr
# ----------------------------------------------------------------------


def test_cgls_returns_raw_output():
    """
        cgls() returns systemd-cgls stdout when the command
        exits zero.
    """
    runner = _MockRunner()
    args = ["systemd-cgls", "--no-pager"]
    _seed(runner, args=args, stdout="control-group-tree\n")
    mgr = _make_manager(runner)
    output = mgr.cgls()
    testbase.assert_equal(output, "control-group-tree\n")
    return


def test_cgls_with_unit_filter():
    """
        cgls(unit=) appends --unit <unit>.
    """
    runner = _MockRunner()
    args = ["systemd-cgls", "--no-pager", "--unit", "workers.slice"]
    _seed(runner, args=args, stdout="filtered\n")
    mgr = _make_manager(runner)
    output = mgr.cgls(unit="workers.slice")
    testbase.assert_equal(output, "filtered\n")
    return


def test_cgtop_parses_records():
    """
        cgtop() parses systemd-cgtop --raw output into
        CgroupUsage records.
    """
    runner = _MockRunner()
    args = ["systemd-cgtop", "--iterations=1", "--raw"]
    payload = (
        "/                100  5.0  1048576  -    -\n"
        "/system.slice    50   2.5  524288   1024 2048\n"
    )
    _seed(runner, args=args, stdout=payload)
    mgr = _make_manager(runner)
    rows = mgr.cgtop()
    testbase.assert_equal(len(rows), 2)
    testbase.assert_equal(rows[0].path, "/")
    testbase.assert_equal(rows[1].path, "/system.slice")
    testbase.assert_equal(rows[1].input_per_s, 1024)
    return


def test_read_cgroup_attr_returns_stripped_value():
    """
        read_cgroup_attr() returns the stripped contents of the
        targeted cgroup file.
    """
    runner = _MockRunner()
    args = ["cat", "/sys/fs/cgroup/system.slice/memory.current"]
    _seed(runner, args=args, stdout="1048576\n")
    mgr = _make_manager(runner)
    value = mgr.read_cgroup_attr(
        path="system.slice", attr="memory.current",
    )
    testbase.assert_equal(value, "1048576")
    return


def test_read_cgroup_attr_returns_none_when_missing():
    """
        read_cgroup_attr() returns None when the cgroup file is
        missing.
    """
    runner = _MockRunner()
    args = ["cat", "/sys/fs/cgroup/missing/memory.current"]
    _seed(runner, args=args, exit_code=1, stderr="No such file")
    mgr = _make_manager(runner)
    value = mgr.read_cgroup_attr(path="missing", attr="memory.current")
    testbase.assert_true(value is None)
    return


# ----------------------------------------------------------------------
# Dry-run sweep
# ----------------------------------------------------------------------


def test_dry_run_sweep_does_not_invoke_mutating_commands():
    """
        With dry_run=True every mutating verb returns DRY_RUN
        and the runner is never asked to dispatch tee / rm /
        systemctl set-property / systemd-run.
    """
    runner = _MockRunner()
    runner.default_result = RunResult(
        exit_code=1, stdout="", stderr="", command=["cat"],
    )
    config = ResourceControlManagerConfig(dry_run=True, elevated=True)
    sm = _MockServiceManager()
    mgr = _make_manager(runner, config=config, sm=sm)

    # Seed the show probe used by set_properties_transient so the
    # diff produces a non-empty changed_keys list.
    _seed_show(
        runner, unit="nginx.service", keys=["CPUQuota"],
        values={"CPUQuota": "10%"},
    )

    res_t = mgr.set_properties_transient(
        unit="nginx.service", properties={"CPUQuota": "50%"},
    )
    res_p = mgr.set_properties_persistent(
        unit="nginx.service", properties={"CPUQuota": "50%"},
    )
    res_d = mgr.define_slice(spec=SliceSpec(name="x", properties={}))
    res_s = mgr.run_scoped(command=["/bin/true"])

    testbase.assert_equal(res_t.status, ResourceActionStatus.DRY_RUN)
    testbase.assert_equal(res_p.status, ResourceActionStatus.DRY_RUN)
    testbase.assert_equal(res_d.status, ResourceActionStatus.DRY_RUN)
    testbase.assert_equal(res_s.status, ResourceActionStatus.DRY_RUN)

    forbidden_args = [
        ["tee"],
        ["rm", "-f"],
        ["systemctl", "set-property"],
        ["systemd-run"],
    ]
    for forbidden_prefix in forbidden_args:
        offenders = [
            c for c in runner.calls
            if c["args"][: len(forbidden_prefix)] == forbidden_prefix
        ]
        testbase.assert_equal(offenders, [])

    testbase.assert_equal(sm.reload_calls, 0)
    return
