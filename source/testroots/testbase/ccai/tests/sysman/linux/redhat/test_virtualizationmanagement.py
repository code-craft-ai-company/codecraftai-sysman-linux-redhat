# Tests for the VirtualizationManager facade.

import os

from dataclasses import dataclass, field
from typing import Callable, Optional

from ccai import testbase

from ccai.sysman.linux.redhat.runners.models import RunResult
from ccai.sysman.linux.redhat.virtualizationmanagement import (
    DomainInfo,
    NetworkInfo,
    SnapshotInfo,
    StoragePoolInfo,
    StorageVolumeInfo,
    VirtActionStatus,
    VirtInstallSpec,
    VirtualizationManager,
    VirtualizationManagerConfig,
    VirtualizationQueryError,
)


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
        self.calls.append({
            "args": list(args),
            "elevated": elevated,
            "stdin": stdin,
        })
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


# ----------------------------------------------------------------------
# Canned virsh / virt-install outputs
# ----------------------------------------------------------------------


_DOMINFO_RUNNING = (
    "Id:             3\n"
    "Name:           webvm\n"
    "UUID:           11111111-2222-3333-4444-555555555555\n"
    "OS Type:        hvm\n"
    "State:          running\n"
    "CPU(s):         2\n"
    "CPU time:       12.3s\n"
    "Max memory:     2097152 KiB\n"
    "Used memory:    1048576 KiB\n"
    "Persistent:     yes\n"
    "Autostart:      disable\n"
    "Managed save:   no\n"
)

_DOMINFO_SHUTOFF = (
    "Id:             -\n"
    "Name:           webvm\n"
    "UUID:           11111111-2222-3333-4444-555555555555\n"
    "OS Type:        hvm\n"
    "State:          shut off\n"
    "CPU(s):         2\n"
    "Max memory:     2097152 KiB\n"
    "Used memory:    0 KiB\n"
    "Persistent:     yes\n"
    "Autostart:      enable\n"
)

_NETINFO_DEFAULT_ACTIVE = (
    "Name:           default\n"
    "UUID:           aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee\n"
    "Active:         yes\n"
    "Persistent:     yes\n"
    "Autostart:      yes\n"
    "Bridge:         virbr0\n"
)

_NETINFO_DEFAULT_INACTIVE = (
    "Name:           default\n"
    "UUID:           aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee\n"
    "Active:         no\n"
    "Persistent:     yes\n"
    "Autostart:      no\n"
)

_POOLINFO_RUNNING = (
    "Name:           default\n"
    "UUID:           99999999-9999-9999-9999-999999999999\n"
    "State:          running\n"
    "Persistent:     yes\n"
    "Autostart:      yes\n"
    "Capacity:       100.00 GiB\n"
    "Allocation:     10.00 GiB\n"
    "Available:      90.00 GiB\n"
)

_POOL_LIST_DETAILS = (
    " Name      State    Autostart  Persistent   Capacity     Allocation   Available\n"
    "----------------------------------------------------------------------------------\n"
    " default   running  yes        yes          100.00 GiB   10.00 GiB    90.00 GiB\n"
    " scratch   inactive no         yes          50.00 GiB    0.00 GiB     50.00 GiB\n"
)

_VOL_LIST_DETAILS = (
    " Name        Path                                       Type  Capacity    Allocation\n"
    "----------------------------------------------------------------------------------------\n"
    " web.qcow2   /var/lib/libvirt/images/web.qcow2          file  20.00 GiB   5.00 GiB\n"
    " batch.img   /var/lib/libvirt/images/batch.img          file  10.00 GiB   1.00 GiB\n"
)

_VOL_LIST_EMPTY = (
    " Name        Path                                       Type  Capacity    Allocation\n"
    "----------------------------------------------------------------------------------------\n"
)

_SNAPSHOT_LIST_TABLE = (
    " Name        Creation Time              State\n"
    "------------------------------------------------------------\n"
    " preupgrade  2024-01-01 12:00:00 +0000  shutoff\n"
    " checkpoint  2024-02-15 09:30:11 +0000  running\n"
)

_SNAPSHOT_LIST_TREE = (
    "preupgrade\n"
    "  |\n"
    "  +- checkpoint\n"
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _virsh_argv(*tokens: str) -> tuple:
    """
        Produce a tuple suitable for indexing ``runner.results`` for a
        ``virsh`` invocation that includes the default ``--connect``
        flag.
    """
    rtnval = ("virsh", "--connect", "qemu:///system") + tuple(tokens)
    return rtnval


def _make_manager(
    *,
    runner: _MockRunner,
    elevated: bool = True,
    dry_run: bool = False,
    uri: str = "qemu:///system",
) -> VirtualizationManager:
    """
        Construct a manager with the canonical config used in tests.
    """
    config = VirtualizationManagerConfig(
        dry_run=dry_run,
        elevated=elevated,
        uri=uri,
    )
    rtnval = VirtualizationManager(runner=runner, config=config)
    return rtnval


def _seed_dominfo_running(runner: _MockRunner, name: str) -> None:
    """
        Seed ``virsh dominfo <name>`` to report a running VM.
    """
    runner.results[_virsh_argv("dominfo", name)] = RunResult(
        exit_code=0, stdout=_DOMINFO_RUNNING, stderr="",
        command=["virsh"],
    )
    return


def _seed_dominfo_shutoff(runner: _MockRunner, name: str) -> None:
    """
        Seed ``virsh dominfo <name>`` to report a shut-off VM.
    """
    runner.results[_virsh_argv("dominfo", name)] = RunResult(
        exit_code=0, stdout=_DOMINFO_SHUTOFF, stderr="",
        command=["virsh"],
    )
    return


def _seed_dominfo_missing(runner: _MockRunner, name: str) -> None:
    """
        Seed ``virsh dominfo <name>`` to report a missing VM.
    """
    runner.results[_virsh_argv("dominfo", name)] = RunResult(
        exit_code=1,
        stdout="",
        stderr="error: failed to get domain '{}'".format(name),
        command=["virsh"],
    )
    return


# ----------------------------------------------------------------------
# Domain reads
# ----------------------------------------------------------------------


def test_list_domains_parses_virsh_output():
    """
        list_domains() parses ``virsh list --all --name`` and joins per-VM
        ``virsh dominfo`` payloads.
    """
    runner = _MockRunner()
    runner.results[_virsh_argv("list", "--all", "--name")] = RunResult(
        exit_code=0, stdout="webvm\nbatchvm\n\n", stderr="",
        command=["virsh"],
    )
    runner.results[_virsh_argv("dominfo", "webvm")] = RunResult(
        exit_code=0, stdout=_DOMINFO_RUNNING, stderr="",
        command=["virsh"],
    )
    runner.results[_virsh_argv("dominfo", "batchvm")] = RunResult(
        exit_code=0, stdout=_DOMINFO_SHUTOFF, stderr="",
        command=["virsh"],
    )

    manager = _make_manager(runner=runner)
    rows = manager.list_domains()

    testbase.assert_equal(len(rows), 2)
    by_name = {row.name: row for row in rows}
    testbase.assert_equal(by_name["webvm"].state, "running")
    testbase.assert_equal(by_name["webvm"].vcpus, 2)
    testbase.assert_equal(by_name["webvm"].max_memory_kib, 2097152)
    testbase.assert_equal(
        by_name["webvm"].uuid,
        "11111111-2222-3333-4444-555555555555",
    )
    testbase.assert_true(by_name["webvm"].persistent)
    testbase.assert_true(not by_name["webvm"].autostart)
    testbase.assert_equal(by_name["batchvm"].state, "shut off")
    testbase.assert_true(by_name["batchvm"].autostart)
    return


def test_list_domains_raises_on_unexpected_failure():
    """
        list_domains() raises VirtualizationQueryError when ``virsh
        list`` exits non-zero (libvirtd-not-running fallback).
    """
    runner = _MockRunner()
    runner.results[_virsh_argv("list", "--all", "--name")] = RunResult(
        exit_code=1, stdout="",
        stderr="error: failed to connect to the hypervisor",
        command=["virsh"],
    )

    manager = _make_manager(runner=runner)
    raised = False
    try:
        manager.list_domains()
    except VirtualizationQueryError:
        raised = True
    testbase.assert_true(raised)
    return


def test_query_domain_returns_none_for_unknown():
    """
        query_domain() returns None when the domain is not defined.
    """
    runner = _MockRunner()
    _seed_dominfo_missing(runner=runner, name="ghostvm")

    manager = _make_manager(runner=runner)
    result = manager.query_domain(name="ghostvm")
    testbase.assert_equal(result, None)
    return


def test_query_domain_parses_dominfo():
    """
        query_domain() parses dominfo into a DomainInfo instance.
    """
    runner = _MockRunner()
    _seed_dominfo_running(runner=runner, name="webvm")

    manager = _make_manager(runner=runner)
    result = manager.query_domain(name="webvm")
    testbase.assert_true(isinstance(result, DomainInfo))
    testbase.assert_equal(result.state, "running")
    testbase.assert_equal(result.vcpus, 2)
    return


def test_dumpxml_returns_text():
    """
        dumpxml() returns the captured stdout from ``virsh dumpxml``.
    """
    runner = _MockRunner()
    runner.results[_virsh_argv("dumpxml", "webvm")] = RunResult(
        exit_code=0,
        stdout="<domain type='kvm'><name>webvm</name></domain>",
        stderr="",
        command=["virsh"],
    )
    manager = _make_manager(runner=runner)
    rtnval = manager.dumpxml(name="webvm")
    testbase.assert_true(rtnval is not None)
    testbase.assert_true("<name>webvm</name>" in rtnval)
    return


def test_dumpxml_returns_none_when_missing():
    """
        dumpxml() returns None when the domain is not defined.
    """
    runner = _MockRunner()
    runner.results[_virsh_argv("dumpxml", "ghostvm")] = RunResult(
        exit_code=1, stdout="", stderr="error: domain not found",
        command=["virsh"],
    )
    manager = _make_manager(runner=runner)
    rtnval = manager.dumpxml(name="ghostvm")
    testbase.assert_equal(rtnval, None)
    return


# ----------------------------------------------------------------------
# Domain lifecycle
# ----------------------------------------------------------------------


def test_start_domain_skipped_when_running():
    """
        start_domain() returns SKIPPED when the VM is already running.
    """
    runner = _MockRunner()
    _seed_dominfo_running(runner=runner, name="webvm")

    manager = _make_manager(runner=runner)
    result = manager.start_domain(name="webvm")
    testbase.assert_equal(result.status, VirtActionStatus.SKIPPED)
    start_calls = [c for c in runner.calls if "start" in c["args"]]
    testbase.assert_equal(start_calls, [])
    return


def test_start_domain_invokes_virsh_when_shutoff():
    """
        start_domain() dispatches ``virsh start`` when the VM is shut off
        and elevates when configured.
    """
    runner = _MockRunner()
    _seed_dominfo_shutoff(runner=runner, name="webvm")

    manager = _make_manager(runner=runner, elevated=True)
    result = manager.start_domain(name="webvm")
    testbase.assert_equal(result.status, VirtActionStatus.SUCCESS)
    start_calls = [
        c for c in runner.calls
        if c["args"] == list(_virsh_argv("start", "webvm"))
    ]
    testbase.assert_equal(len(start_calls), 1)
    testbase.assert_true(start_calls[0]["elevated"])
    return


def test_start_domain_dry_run_does_not_dispatch():
    """
        start_domain() returns DRY_RUN and never invokes the runner.
    """
    runner = _MockRunner()
    manager = _make_manager(runner=runner, dry_run=True)
    result = manager.start_domain(name="webvm")
    testbase.assert_equal(result.status, VirtActionStatus.DRY_RUN)
    testbase.assert_equal(len(runner.calls), 0)
    testbase.assert_equal(result.command[-1], "webvm")
    return


def test_shutdown_domain_skipped_when_already_off():
    """
        shutdown_domain() returns SKIPPED when the VM is already shut off.
    """
    runner = _MockRunner()
    _seed_dominfo_shutoff(runner=runner, name="webvm")

    manager = _make_manager(runner=runner)
    result = manager.shutdown_domain(name="webvm")
    testbase.assert_equal(result.status, VirtActionStatus.SKIPPED)
    return


def test_shutdown_domain_dispatches_when_running():
    """
        shutdown_domain() dispatches ``virsh shutdown`` when running.
    """
    runner = _MockRunner()
    _seed_dominfo_running(runner=runner, name="webvm")

    manager = _make_manager(runner=runner, elevated=True)
    result = manager.shutdown_domain(name="webvm")
    testbase.assert_equal(result.status, VirtActionStatus.SUCCESS)
    sd_calls = [
        c for c in runner.calls
        if c["args"] == list(_virsh_argv("shutdown", "webvm"))
    ]
    testbase.assert_equal(len(sd_calls), 1)
    testbase.assert_true(sd_calls[0]["elevated"])
    return


def test_destroy_domain_dispatches_virsh_destroy():
    """
        destroy_domain() dispatches ``virsh destroy`` when running.
    """
    runner = _MockRunner()
    _seed_dominfo_running(runner=runner, name="webvm")

    manager = _make_manager(runner=runner, elevated=True)
    result = manager.destroy_domain(name="webvm")
    testbase.assert_equal(result.status, VirtActionStatus.SUCCESS)
    destroy_calls = [
        c for c in runner.calls
        if c["args"] == list(_virsh_argv("destroy", "webvm"))
    ]
    testbase.assert_equal(len(destroy_calls), 1)
    return


def test_destroy_domain_skipped_when_shutoff():
    """
        destroy_domain() returns SKIPPED when already shut off.
    """
    runner = _MockRunner()
    _seed_dominfo_shutoff(runner=runner, name="webvm")

    manager = _make_manager(runner=runner)
    result = manager.destroy_domain(name="webvm")
    testbase.assert_equal(result.status, VirtActionStatus.SKIPPED)
    return


def test_reboot_domain_dispatches_command():
    """
        reboot_domain() dispatches ``virsh reboot``.
    """
    runner = _MockRunner()
    manager = _make_manager(runner=runner, elevated=True)
    result = manager.reboot_domain(name="webvm")
    testbase.assert_equal(result.status, VirtActionStatus.SUCCESS)
    reboot_calls = [
        c for c in runner.calls
        if c["args"] == list(_virsh_argv("reboot", "webvm"))
    ]
    testbase.assert_equal(len(reboot_calls), 1)
    return


def test_set_autostart_skipped_when_already_matches():
    """
        set_autostart() returns SKIPPED when the current autostart state
        already matches the request.  ``_DOMINFO_RUNNING`` reports
        ``Autostart: disable`` so requesting False short-circuits.
    """
    runner = _MockRunner()
    _seed_dominfo_running(runner=runner, name="webvm")

    manager = _make_manager(runner=runner)
    result = manager.set_autostart(name="webvm", enabled=False)
    testbase.assert_equal(result.status, VirtActionStatus.SKIPPED)
    return


def test_set_autostart_enables_when_requested():
    """
        set_autostart(enabled=True) dispatches ``virsh autostart`` when
        the domain is currently set to disabled.
    """
    runner = _MockRunner()
    _seed_dominfo_running(runner=runner, name="webvm")

    manager = _make_manager(runner=runner, elevated=True)
    result = manager.set_autostart(name="webvm", enabled=True)
    testbase.assert_equal(result.status, VirtActionStatus.SUCCESS)
    auto_calls = [
        c for c in runner.calls
        if c["args"] == list(_virsh_argv("autostart", "webvm"))
    ]
    testbase.assert_equal(len(auto_calls), 1)
    return


# ----------------------------------------------------------------------
# Domain definition
# ----------------------------------------------------------------------


def test_define_domain_runs_virsh_define_and_cleans_temp_file():
    """
        define_domain() writes the XML to a temp path, dispatches
        ``virsh define <path>``, and removes the temp file even after
        success.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="Domain webvm defined", stderr="",
        command=["virsh"],
    ))
    manager = _make_manager(runner=runner, elevated=True)
    result = manager.define_domain(
        xml_content="<domain type='kvm'><name>webvm</name></domain>",
    )
    testbase.assert_equal(result.status, VirtActionStatus.SUCCESS)

    define_calls = [
        c for c in runner.calls if "define" in c["args"]
    ]
    testbase.assert_equal(len(define_calls), 1)
    argv = define_calls[0]["args"]
    # The last token is the temp XML path.
    tmp_path = argv[-1]
    testbase.assert_true(tmp_path.endswith(".xml"))
    testbase.assert_true(not os.path.exists(tmp_path))
    return


def test_define_domain_cleans_temp_file_on_failure():
    """
        define_domain() removes the temp file even when ``virsh define``
        fails.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=1, stdout="", stderr="error: invalid XML",
        command=["virsh"],
    ))
    manager = _make_manager(runner=runner, elevated=True)
    result = manager.define_domain(xml_content="<bad/>")
    testbase.assert_equal(result.status, VirtActionStatus.FAILED)

    define_calls = [
        c for c in runner.calls if "define" in c["args"]
    ]
    testbase.assert_equal(len(define_calls), 1)
    tmp_path = define_calls[0]["args"][-1]
    testbase.assert_true(not os.path.exists(tmp_path))
    return


def test_undefine_domain_skipped_when_not_defined():
    """
        undefine_domain() returns SKIPPED when the domain is not defined.
    """
    runner = _MockRunner()
    _seed_dominfo_missing(runner=runner, name="ghostvm")

    manager = _make_manager(runner=runner)
    result = manager.undefine_domain(name="ghostvm")
    testbase.assert_equal(result.status, VirtActionStatus.SKIPPED)
    return


def test_undefine_domain_appends_remove_storage():
    """
        undefine_domain(remove_storage=True) appends
        ``--remove-all-storage``.
    """
    runner = _MockRunner()
    _seed_dominfo_shutoff(runner=runner, name="webvm")

    manager = _make_manager(runner=runner, elevated=True)
    result = manager.undefine_domain(
        name="webvm", remove_storage=True, nvram=True,
    )
    testbase.assert_equal(result.status, VirtActionStatus.SUCCESS)
    undef_calls = [
        c for c in runner.calls
        if c["args"][:1] == ["virsh"] and "undefine" in c["args"]
    ]
    testbase.assert_equal(len(undef_calls), 1)
    testbase.assert_true("--remove-all-storage" in undef_calls[0]["args"])
    testbase.assert_true("--nvram" in undef_calls[0]["args"])
    return


# ----------------------------------------------------------------------
# virt-install
# ----------------------------------------------------------------------


def test_create_vm_invokes_virt_install():
    """
        create_vm() dispatches ``virt-install`` with the rendered argv.
    """
    runner = _MockRunner()
    _seed_dominfo_missing(runner=runner, name="webvm")

    manager = _make_manager(runner=runner, elevated=True)
    spec = VirtInstallSpec(
        name="webvm",
        memory_mib=2048,
        vcpus=2,
        disk="size=20",
        network="network=default",
        os_variant="rhel9.0",
    )
    result = manager.create_vm(spec=spec)
    testbase.assert_equal(result.status, VirtActionStatus.SUCCESS)

    install_calls = [
        c for c in runner.calls if c["args"][0] == "virt-install"
    ]
    testbase.assert_equal(len(install_calls), 1)
    argv = install_calls[0]["args"]
    testbase.assert_true("--name" in argv)
    testbase.assert_equal(argv[argv.index("--name") + 1], "webvm")
    testbase.assert_equal(argv[argv.index("--memory") + 1], "2048")
    testbase.assert_equal(argv[argv.index("--vcpus") + 1], "2")
    testbase.assert_equal(argv[argv.index("--disk") + 1], "size=20")
    testbase.assert_equal(
        argv[argv.index("--os-variant") + 1], "rhel9.0",
    )
    testbase.assert_equal(
        argv[argv.index("--network") + 1], "network=default",
    )
    testbase.assert_equal(argv[argv.index("--graphics") + 1], "none")
    testbase.assert_true("--noautoconsole" in argv)
    testbase.assert_true(install_calls[0]["elevated"])
    return


def test_create_vm_skipped_when_present():
    """
        create_vm() returns SKIPPED when a domain with the same name
        already exists and replace=False.
    """
    runner = _MockRunner()
    _seed_dominfo_running(runner=runner, name="webvm")

    manager = _make_manager(runner=runner)
    spec = VirtInstallSpec(
        name="webvm", memory_mib=2048, vcpus=2,
        disk="size=20", network="network=default",
        os_variant="rhel9.0",
    )
    result = manager.create_vm(spec=spec)
    testbase.assert_equal(result.status, VirtActionStatus.SKIPPED)

    install_calls = [
        c for c in runner.calls if c["args"][0] == "virt-install"
    ]
    testbase.assert_equal(install_calls, [])
    return


def test_create_vm_replace_undefines_first():
    """
        create_vm(replace=True) undefines the existing domain before
        dispatching virt-install.
    """
    runner = _MockRunner()
    _seed_dominfo_running(runner=runner, name="webvm")

    manager = _make_manager(runner=runner, elevated=True)
    spec = VirtInstallSpec(
        name="webvm", memory_mib=1024, vcpus=1,
        disk="size=10", network="network=default",
        os_variant="rhel9.0",
    )
    result = manager.create_vm(spec=spec, replace=True)
    testbase.assert_equal(result.status, VirtActionStatus.SUCCESS)

    undefine_calls = [
        c for c in runner.calls
        if c["args"][:1] == ["virsh"] and "undefine" in c["args"]
    ]
    install_calls = [
        c for c in runner.calls if c["args"][0] == "virt-install"
    ]
    testbase.assert_equal(len(undefine_calls), 1)
    testbase.assert_true(
        "--remove-all-storage" in undefine_calls[0]["args"],
    )
    testbase.assert_equal(len(install_calls), 1)
    return


def test_create_vm_dry_run_does_not_dispatch():
    """
        create_vm() returns DRY_RUN and never invokes the runner.
    """
    runner = _MockRunner()
    manager = _make_manager(runner=runner, dry_run=True)
    spec = VirtInstallSpec(
        name="webvm", memory_mib=1024, vcpus=1,
        disk="size=10", network="network=default",
        os_variant="rhel9.0",
    )
    result = manager.create_vm(spec=spec)
    testbase.assert_equal(result.status, VirtActionStatus.DRY_RUN)
    testbase.assert_equal(len(runner.calls), 0)
    testbase.assert_equal(result.command[0], "virt-install")
    return


# ----------------------------------------------------------------------
# Networks
# ----------------------------------------------------------------------


def test_list_networks_parses_virsh_net_list():
    """
        list_networks() joins ``virsh net-list --name`` and per-net
        ``virsh net-info`` into NetworkInfo rows.
    """
    runner = _MockRunner()
    runner.results[_virsh_argv("net-list", "--all", "--name")] = RunResult(
        exit_code=0, stdout="default\nadminnet\n", stderr="",
        command=["virsh"],
    )
    runner.results[_virsh_argv("net-info", "default")] = RunResult(
        exit_code=0, stdout=_NETINFO_DEFAULT_ACTIVE, stderr="",
        command=["virsh"],
    )
    runner.results[_virsh_argv("net-info", "adminnet")] = RunResult(
        exit_code=0, stdout=_NETINFO_DEFAULT_INACTIVE, stderr="",
        command=["virsh"],
    )

    manager = _make_manager(runner=runner)
    rows = manager.list_networks()
    testbase.assert_equal(len(rows), 2)
    by_name = {row.name: row for row in rows}
    testbase.assert_true(isinstance(by_name["default"], NetworkInfo))
    testbase.assert_true(by_name["default"].active)
    testbase.assert_true(by_name["default"].autostart)
    testbase.assert_true(not by_name["adminnet"].active)
    return


def test_define_network_writes_temp_xml_and_dispatches():
    """
        define_network() dispatches ``virsh net-define <path>`` and
        cleans up the temp file.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="Network default defined", stderr="",
        command=["virsh"],
    ))
    manager = _make_manager(runner=runner, elevated=True)
    result = manager.define_network(
        xml_content="<network><name>x</name></network>",
    )
    testbase.assert_equal(result.status, VirtActionStatus.SUCCESS)
    define_calls = [
        c for c in runner.calls if "net-define" in c["args"]
    ]
    testbase.assert_equal(len(define_calls), 1)
    tmp_path = define_calls[0]["args"][-1]
    testbase.assert_true(not os.path.exists(tmp_path))
    return


def test_undefine_network_skipped_when_absent():
    """
        undefine_network() returns SKIPPED when the network is not
        defined.
    """
    runner = _MockRunner()
    runner.results[_virsh_argv("net-info", "ghost")] = RunResult(
        exit_code=1, stdout="", stderr="error: network not found",
        command=["virsh"],
    )
    manager = _make_manager(runner=runner)
    result = manager.undefine_network(name="ghost")
    testbase.assert_equal(result.status, VirtActionStatus.SKIPPED)
    return


def test_start_network_skipped_when_active():
    """
        start_network() returns SKIPPED when the network is already
        active.
    """
    runner = _MockRunner()
    runner.results[_virsh_argv("net-info", "default")] = RunResult(
        exit_code=0, stdout=_NETINFO_DEFAULT_ACTIVE, stderr="",
        command=["virsh"],
    )
    manager = _make_manager(runner=runner)
    result = manager.start_network(name="default")
    testbase.assert_equal(result.status, VirtActionStatus.SKIPPED)
    return


def test_destroy_network_skipped_when_inactive():
    """
        destroy_network() returns SKIPPED when the network is already
        inactive.
    """
    runner = _MockRunner()
    runner.results[_virsh_argv("net-info", "default")] = RunResult(
        exit_code=0, stdout=_NETINFO_DEFAULT_INACTIVE, stderr="",
        command=["virsh"],
    )
    manager = _make_manager(runner=runner)
    result = manager.destroy_network(name="default")
    testbase.assert_equal(result.status, VirtActionStatus.SKIPPED)
    return


def test_set_network_autostart_dispatches_when_disabled():
    """
        set_network_autostart(True) dispatches ``virsh net-autostart``
        when the current state is False.
    """
    runner = _MockRunner()
    runner.results[_virsh_argv("net-info", "default")] = RunResult(
        exit_code=0, stdout=_NETINFO_DEFAULT_INACTIVE, stderr="",
        command=["virsh"],
    )
    manager = _make_manager(runner=runner, elevated=True)
    result = manager.set_network_autostart(name="default", enabled=True)
    testbase.assert_equal(result.status, VirtActionStatus.SUCCESS)
    auto_calls = [
        c for c in runner.calls
        if c["args"] == list(_virsh_argv("net-autostart", "default"))
    ]
    testbase.assert_equal(len(auto_calls), 1)
    return


# ----------------------------------------------------------------------
# Pools and volumes
# ----------------------------------------------------------------------


def test_list_pools_parses_pool_list_details():
    """
        list_pools() parses ``virsh pool-list --details`` rows into
        StoragePoolInfo entries.
    """
    runner = _MockRunner()
    runner.results[_virsh_argv("pool-list", "--all", "--details")] = RunResult(
        exit_code=0, stdout=_POOL_LIST_DETAILS, stderr="",
        command=["virsh"],
    )
    manager = _make_manager(runner=runner)
    pools = manager.list_pools()
    testbase.assert_equal(len(pools), 2)
    by_name = {p.name: p for p in pools}
    testbase.assert_true(isinstance(by_name["default"], StoragePoolInfo))
    testbase.assert_equal(by_name["default"].state, "running")
    testbase.assert_true(by_name["default"].autostart)
    testbase.assert_equal(
        by_name["default"].capacity_bytes, int(100.00 * 1024 ** 3),
    )
    testbase.assert_equal(by_name["scratch"].state, "inactive")
    testbase.assert_true(not by_name["scratch"].autostart)
    return


def test_define_pool_writes_temp_xml():
    """
        define_pool() dispatches ``virsh pool-define <path>`` and cleans
        up the temp file.
    """
    runner = _MockRunner(default_result=RunResult(
        exit_code=0, stdout="Pool default defined", stderr="",
        command=["virsh"],
    ))
    manager = _make_manager(runner=runner, elevated=True)
    result = manager.define_pool(
        xml_content="<pool type='dir'><name>default</name></pool>",
    )
    testbase.assert_equal(result.status, VirtActionStatus.SUCCESS)
    pool_calls = [
        c for c in runner.calls if "pool-define" in c["args"]
    ]
    testbase.assert_equal(len(pool_calls), 1)
    tmp_path = pool_calls[0]["args"][-1]
    testbase.assert_true(not os.path.exists(tmp_path))
    return


def test_start_pool_skipped_when_running():
    """
        start_pool() returns SKIPPED when the pool is already running.
    """
    runner = _MockRunner()
    runner.results[_virsh_argv("pool-info", "default")] = RunResult(
        exit_code=0, stdout=_POOLINFO_RUNNING, stderr="",
        command=["virsh"],
    )
    manager = _make_manager(runner=runner)
    result = manager.start_pool(name="default")
    testbase.assert_equal(result.status, VirtActionStatus.SKIPPED)
    return


def test_set_pool_autostart_skipped_when_already_matches():
    """
        set_pool_autostart() returns SKIPPED when the current state
        matches.  ``_POOLINFO_RUNNING`` reports ``Autostart: yes`` so
        requesting True short-circuits.
    """
    runner = _MockRunner()
    runner.results[_virsh_argv("pool-info", "default")] = RunResult(
        exit_code=0, stdout=_POOLINFO_RUNNING, stderr="",
        command=["virsh"],
    )
    manager = _make_manager(runner=runner)
    result = manager.set_pool_autostart(name="default", enabled=True)
    testbase.assert_equal(result.status, VirtActionStatus.SKIPPED)
    return


def test_list_volumes_parses_vol_list_details():
    """
        list_volumes() parses ``virsh vol-list --pool <pool> --details``
        rows.
    """
    runner = _MockRunner()
    runner.results[_virsh_argv(
        "vol-list", "--pool", "default", "--details",
    )] = RunResult(
        exit_code=0, stdout=_VOL_LIST_DETAILS, stderr="",
        command=["virsh"],
    )
    manager = _make_manager(runner=runner)
    vols = manager.list_volumes(pool="default")
    testbase.assert_equal(len(vols), 2)
    by_name = {v.name: v for v in vols}
    testbase.assert_true(isinstance(by_name["web.qcow2"], StorageVolumeInfo))
    testbase.assert_equal(
        by_name["web.qcow2"].path,
        "/var/lib/libvirt/images/web.qcow2",
    )
    testbase.assert_equal(
        by_name["web.qcow2"].capacity_bytes, int(20.00 * 1024 ** 3),
    )
    return


def test_create_volume_skipped_when_present():
    """
        create_volume() returns SKIPPED when the named volume already
        exists in the pool.
    """
    runner = _MockRunner()
    runner.results[_virsh_argv(
        "vol-list", "--pool", "default", "--details",
    )] = RunResult(
        exit_code=0, stdout=_VOL_LIST_DETAILS, stderr="",
        command=["virsh"],
    )
    manager = _make_manager(runner=runner)
    result = manager.create_volume(
        pool="default", name="web.qcow2", size="20G",
    )
    testbase.assert_equal(result.status, VirtActionStatus.SKIPPED)
    return


def test_create_volume_dispatches_when_absent():
    """
        create_volume() dispatches ``virsh vol-create-as`` with
        ``--format <format>`` when the volume is absent.
    """
    runner = _MockRunner()
    runner.results[_virsh_argv(
        "vol-list", "--pool", "default", "--details",
    )] = RunResult(
        exit_code=0, stdout=_VOL_LIST_EMPTY, stderr="",
        command=["virsh"],
    )
    manager = _make_manager(runner=runner, elevated=True)
    result = manager.create_volume(
        pool="default", name="new.qcow2", size="10G",
    )
    testbase.assert_equal(result.status, VirtActionStatus.SUCCESS)
    create_calls = [
        c for c in runner.calls if "vol-create-as" in c["args"]
    ]
    testbase.assert_equal(len(create_calls), 1)
    argv = create_calls[0]["args"]
    testbase.assert_true("default" in argv)
    testbase.assert_true("new.qcow2" in argv)
    testbase.assert_true("10G" in argv)
    testbase.assert_true("--format" in argv)
    testbase.assert_equal(argv[argv.index("--format") + 1], "qcow2")
    testbase.assert_true(create_calls[0]["elevated"])
    return


def test_delete_volume_skipped_when_absent():
    """
        delete_volume() returns SKIPPED when the volume is not present.
    """
    runner = _MockRunner()
    runner.results[_virsh_argv(
        "vol-list", "--pool", "default", "--details",
    )] = RunResult(
        exit_code=0, stdout=_VOL_LIST_EMPTY, stderr="",
        command=["virsh"],
    )
    manager = _make_manager(runner=runner)
    result = manager.delete_volume(pool="default", name="ghost.qcow2")
    testbase.assert_equal(result.status, VirtActionStatus.SKIPPED)
    return


def test_delete_volume_dispatches_when_present():
    """
        delete_volume() dispatches ``virsh vol-delete`` with the
        correct ``--pool`` flag.
    """
    runner = _MockRunner()
    runner.results[_virsh_argv(
        "vol-list", "--pool", "default", "--details",
    )] = RunResult(
        exit_code=0, stdout=_VOL_LIST_DETAILS, stderr="",
        command=["virsh"],
    )
    manager = _make_manager(runner=runner, elevated=True)
    result = manager.delete_volume(pool="default", name="web.qcow2")
    testbase.assert_equal(result.status, VirtActionStatus.SUCCESS)
    delete_calls = [
        c for c in runner.calls if "vol-delete" in c["args"]
    ]
    testbase.assert_equal(len(delete_calls), 1)
    argv = delete_calls[0]["args"]
    testbase.assert_true("--pool" in argv)
    testbase.assert_equal(argv[argv.index("--pool") + 1], "default")
    return


# ----------------------------------------------------------------------
# Snapshots
# ----------------------------------------------------------------------


def test_create_snapshot_dispatches_with_description():
    """
        create_snapshot() appends the description as a positional arg.
    """
    runner = _MockRunner()
    manager = _make_manager(runner=runner, elevated=True)
    result = manager.create_snapshot(
        domain="webvm", name="preupgrade",
        description="before patching",
    )
    testbase.assert_equal(result.status, VirtActionStatus.SUCCESS)
    snap_calls = [
        c for c in runner.calls if "snapshot-create-as" in c["args"]
    ]
    testbase.assert_equal(len(snap_calls), 1)
    argv = snap_calls[0]["args"]
    testbase.assert_true("webvm" in argv)
    testbase.assert_true("preupgrade" in argv)
    testbase.assert_true("before patching" in argv)
    return


def test_list_snapshots_parses_tree_output():
    """
        list_snapshots() parses tree output into SnapshotInfo entries
        with parent linkage.
    """
    runner = _MockRunner()
    runner.results[_virsh_argv("snapshot-list", "webvm", "--tree")] = RunResult(
        exit_code=0, stdout=_SNAPSHOT_LIST_TREE, stderr="",
        command=["virsh"],
    )
    manager = _make_manager(runner=runner)
    rows = manager.list_snapshots(domain="webvm")
    testbase.assert_equal(len(rows), 2)
    by_name = {r.name: r for r in rows}
    testbase.assert_true(isinstance(by_name["preupgrade"], SnapshotInfo))
    testbase.assert_equal(by_name["preupgrade"].parent, None)
    testbase.assert_equal(by_name["checkpoint"].parent, "preupgrade")
    return


def test_revert_snapshot_dispatches_and_carries_advisory():
    """
        revert_snapshot() dispatches ``virsh snapshot-revert`` and
        records a data-loss advisory in ``detail`` on success.
    """
    runner = _MockRunner()
    manager = _make_manager(runner=runner, elevated=True)
    result = manager.revert_snapshot(domain="webvm", name="preupgrade")
    testbase.assert_equal(result.status, VirtActionStatus.SUCCESS)
    revert_calls = [
        c for c in runner.calls if "snapshot-revert" in c["args"]
    ]
    testbase.assert_equal(len(revert_calls), 1)
    testbase.assert_true(result.detail is not None)
    testbase.assert_true("reverted" in result.detail)
    return


def test_delete_snapshot_skipped_when_absent():
    """
        delete_snapshot() returns SKIPPED when no snapshot with the
        name exists for the domain.
    """
    runner = _MockRunner()
    runner.results[_virsh_argv("snapshot-list", "webvm")] = RunResult(
        exit_code=0,
        stdout=" Name        Creation Time              State\n"
               "------------------------------------------------------------\n",
        stderr="", command=["virsh"],
    )
    manager = _make_manager(runner=runner)
    result = manager.delete_snapshot(domain="webvm", name="ghost")
    testbase.assert_equal(result.status, VirtActionStatus.SKIPPED)
    return


def test_delete_snapshot_dispatches_when_present():
    """
        delete_snapshot() dispatches ``virsh snapshot-delete`` when the
        snapshot exists.
    """
    runner = _MockRunner()
    runner.results[_virsh_argv("snapshot-list", "webvm")] = RunResult(
        exit_code=0, stdout=_SNAPSHOT_LIST_TABLE, stderr="",
        command=["virsh"],
    )
    manager = _make_manager(runner=runner, elevated=True)
    result = manager.delete_snapshot(domain="webvm", name="preupgrade")
    testbase.assert_equal(result.status, VirtActionStatus.SUCCESS)
    del_calls = [
        c for c in runner.calls if "snapshot-delete" in c["args"]
    ]
    testbase.assert_equal(len(del_calls), 1)
    return


# ----------------------------------------------------------------------
# Error / dry-run / URI tests
# ----------------------------------------------------------------------


def test_failure_propagates_exit_code_and_stderr():
    """
        A non-zero exit from the runner surfaces FAILED with stderr,
        stdout, and exit_code populated.
    """
    runner = _MockRunner()
    _seed_dominfo_shutoff(runner=runner, name="webvm")
    runner.results[_virsh_argv("start", "webvm")] = RunResult(
        exit_code=1, stdout="",
        stderr="error: failed to start domain webvm",
        command=["virsh"],
    )
    manager = _make_manager(runner=runner, elevated=True)
    result = manager.start_domain(name="webvm")
    testbase.assert_equal(result.status, VirtActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 1)
    testbase.assert_equal(
        result.stderr, "error: failed to start domain webvm",
    )
    return


def test_uri_override_suppresses_connect_flag():
    """
        Setting ``uri=""`` removes ``--connect`` from every dispatched
        argv.
    """
    runner = _MockRunner()
    runner.results[("virsh", "dominfo", "webvm")] = RunResult(
        exit_code=0, stdout=_DOMINFO_SHUTOFF, stderr="",
        command=["virsh"],
    )
    manager = _make_manager(runner=runner, uri="", elevated=True)
    result = manager.start_domain(name="webvm")
    testbase.assert_equal(result.status, VirtActionStatus.SUCCESS)
    for call in runner.calls:
        testbase.assert_true("--connect" not in call["args"])
    return


def test_libvirtd_not_running_query_raises():
    """
        list_pools() raises VirtualizationQueryError when libvirtd is
        unreachable (the runner reports the typical ``failed to
        connect`` stderr).
    """
    runner = _MockRunner()
    runner.results[_virsh_argv("pool-list", "--all", "--details")] = RunResult(
        exit_code=1, stdout="",
        stderr="error: failed to connect to the hypervisor",
        command=["virsh"],
    )
    manager = _make_manager(runner=runner)
    raised = False
    try:
        manager.list_pools()
    except VirtualizationQueryError:
        raised = True
    testbase.assert_true(raised)
    return
