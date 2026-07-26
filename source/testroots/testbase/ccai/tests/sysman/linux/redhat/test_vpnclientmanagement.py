# Tests for the RedHatVPNClientManager facade.

import json
import unittest

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from ccai.testbase.properties import given
from ccai.testbase.properties import strategies as st

from ccai.aspects.aspects import Aspects

from ccai.sysman.common.vpnactionstatus import VPNActionStatus
from ccai.sysman.common.vpnprovider import VPNProvider
from ccai.sysman.common.vpnclientqueryerror import VPNClientQueryError

from ccai.sysman.linux.redhat.vpnclientmanagement import (
    RedHatVPNClientManager,
    RedHatVPNClientManagerConfig,
    _parse_tailscale_status_json,
    _parse_twingate_status_text,
    _render_twingate_conf,
)


# ----------------------------------------------------------------------
# Mock runner
# ----------------------------------------------------------------------


@dataclass
class _RunResult:
    """
        Minimal RunResult double matching the fields read by the manager.
    """
    exit_code: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class _MockRunner:
    """
        Programmable command runner double.

        Each call is matched against ``results`` keyed by argv tuple
        (or argv prefix). The first matching entry is consumed and
        returned. When no entry matches the runner returns a default
        ``exit_code=0`` result so quiet, uninstrumented paths in the
        SUT do not blow up.
    """

    exact: Dict[Tuple[str, ...], _RunResult] = field(default_factory=dict)
    prefix: List[Tuple[Tuple[str, ...], _RunResult]] = field(default_factory=list)
    default_result: Optional[_RunResult] = None
    calls: List[Dict] = field(default_factory=list)

    def run(
        self,
        args: List[str],
        *,
        elevated: bool = False,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        stdin: Optional[str] = None,
        timeout: Optional[float] = None,
        on_output: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        capture_output: bool = True,
    ) -> _RunResult:
        self.calls.append({
            "args": list(args),
            "elevated": elevated,
            "stdin": stdin,
        })
        key = tuple(args)
        if key in self.exact:
            return self.exact[key]
        for prefix_key, value in self.prefix:
            if key[: len(prefix_key)] == prefix_key:
                return value
        if self.default_result is not None:
            return self.default_result
        rtnval = _RunResult(exit_code=0, stdout="", stderr="")
        return rtnval


def _aspects() -> Aspects:
    """
        Build a fresh empty :class:`Aspects` carrier for tests.
    """
    rtnval = Aspects()
    return rtnval


def _make_manager(
    *,
    runner: _MockRunner,
    dry_run: bool = False,
    elevated: bool = False,
    package_manager: str = "dnf",
) -> RedHatVPNClientManager:
    """
        Build a manager wired to the supplied mock runner.
    """
    config = RedHatVPNClientManagerConfig(
        dry_run=dry_run,
        elevated=elevated,
        package_manager=package_manager,
    )
    # Pre-seed pm detection so tests can opt out of the probe sequence.
    manager = RedHatVPNClientManager(runner=runner, config=config)
    manager._detected_pm = package_manager
    return manager


# ----------------------------------------------------------------------
# Package manager detection
# ----------------------------------------------------------------------


class TestPackageManagerDetection(unittest.TestCase):

    def test_detect_package_manager_prefers_dnf(self) -> None:
        runner = _MockRunner()
        runner.exact[("command", "-v", "dnf")] = _RunResult(exit_code=0, stdout="/usr/bin/dnf")
        runner.exact[("command", "-v", "yum")] = _RunResult(exit_code=0, stdout="/usr/bin/yum")

        manager = RedHatVPNClientManager(
            runner=runner,
            config=RedHatVPNClientManagerConfig(),
        )
        # Force re-detection.
        manager._detected_pm = None

        result = manager._detect_package_manager(aspects=_aspects())
        self.assertEqual(result, "dnf")
        return

    def test_detect_package_manager_falls_back_to_yum(self) -> None:
        runner = _MockRunner()
        runner.exact[("command", "-v", "dnf")] = _RunResult(exit_code=1)
        runner.exact[("command", "-v", "yum")] = _RunResult(exit_code=0, stdout="/usr/bin/yum")

        manager = RedHatVPNClientManager(
            runner=runner,
            config=RedHatVPNClientManagerConfig(),
        )
        manager._detected_pm = None

        result = manager._detect_package_manager(aspects=_aspects())
        self.assertEqual(result, "yum")
        return


# ----------------------------------------------------------------------
# install_tailscale
# ----------------------------------------------------------------------


class TestInstallTailscale(unittest.TestCase):

    def test_install_tailscale_runs_dnf_install(self) -> None:
        runner = _MockRunner()
        # rpm -q : not installed
        runner.prefix.append((("rpm", "-q"), _RunResult(exit_code=1, stdout="package tailscale is not installed")))
        # tee write succeeds
        runner.prefix.append((("tee",), _RunResult(exit_code=0)))
        # dnf install succeeds
        runner.prefix.append((("dnf", "install"), _RunResult(exit_code=0, stdout="installed")))

        manager = _make_manager(runner=runner)
        result = manager.install_tailscale(
            version=None, channel="stable", aspects=_aspects(),
        )

        self.assertEqual(result.status, VPNActionStatus.SUCCESS)
        self.assertEqual(result.provider, VPNProvider.TAILSCALE)
        installed_calls = [c for c in runner.calls if c["args"][:2] == ["dnf", "install"]]
        self.assertEqual(len(installed_calls), 1)
        self.assertIn("tailscale", installed_calls[0]["args"])
        return

    def test_install_tailscale_skipped_when_present(self) -> None:
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(exit_code=0, stdout="1.66.0")))

        manager = _make_manager(runner=runner)
        result = manager.install_tailscale(
            version=None, channel="stable", aspects=_aspects(),
        )

        self.assertEqual(result.status, VPNActionStatus.SKIPPED)
        # No dnf install should have been issued.
        for call in runner.calls:
            self.assertNotEqual(call["args"][:2], ["dnf", "install"])
        return

    def test_install_tailscale_failed_on_dnf_error(self) -> None:
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(exit_code=1)))
        runner.prefix.append((("tee",), _RunResult(exit_code=0)))
        runner.prefix.append((("dnf", "install"), _RunResult(exit_code=2, stderr="boom")))

        manager = _make_manager(runner=runner)
        result = manager.install_tailscale(
            version=None, channel="stable", aspects=_aspects(),
        )
        self.assertEqual(result.status, VPNActionStatus.FAILED)
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.stderr, "boom")
        return

    def test_install_tailscale_dry_run_does_not_invoke_runner(self) -> None:
        runner = _MockRunner()
        manager = _make_manager(runner=runner, dry_run=True)
        result = manager.install_tailscale(
            version=None, channel="stable", aspects=_aspects(),
        )
        self.assertEqual(result.status, VPNActionStatus.DRY_RUN)
        self.assertEqual(runner.calls, [])
        return


# ----------------------------------------------------------------------
# install_twingate_connector
# ----------------------------------------------------------------------


class TestInstallTwingate(unittest.TestCase):

    def test_install_twingate_connector_runs_dnf_install(self) -> None:
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(exit_code=1)))
        runner.prefix.append((("tee",), _RunResult(exit_code=0)))
        runner.prefix.append((("dnf", "install"), _RunResult(exit_code=0)))

        manager = _make_manager(runner=runner)
        result = manager.install_twingate_connector(
            version=None, aspects=_aspects(),
        )
        self.assertEqual(result.status, VPNActionStatus.SUCCESS)
        installed_calls = [c for c in runner.calls if c["args"][:2] == ["dnf", "install"]]
        self.assertEqual(len(installed_calls), 1)
        self.assertIn("twingate-connector", installed_calls[0]["args"])
        return

    def test_install_twingate_connector_uses_yum_when_configured(self) -> None:
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(exit_code=1)))
        runner.prefix.append((("tee",), _RunResult(exit_code=0)))
        runner.prefix.append((("yum", "install"), _RunResult(exit_code=0)))

        manager = _make_manager(runner=runner, package_manager="yum")
        result = manager.install_twingate_connector(
            version=None, aspects=_aspects(),
        )
        self.assertEqual(result.status, VPNActionStatus.SUCCESS)
        yum_calls = [c for c in runner.calls if c["args"][:2] == ["yum", "install"]]
        self.assertEqual(len(yum_calls), 1)
        return


# ----------------------------------------------------------------------
# configure
# ----------------------------------------------------------------------


class TestConfigure(unittest.TestCase):

    def test_configure_tailscale_returns_skipped(self) -> None:
        runner = _MockRunner()
        manager = _make_manager(runner=runner)
        result = manager.configure(
            provider=VPNProvider.TAILSCALE,
            settings={},
            aspects=_aspects(),
        )
        self.assertEqual(result.status, VPNActionStatus.SKIPPED)
        self.assertEqual(runner.calls, [])
        return

    def test_configure_twingate_writes_conf_via_tee(self) -> None:
        runner = _MockRunner()
        # cat returns non-zero (file missing)
        runner.exact[("cat", "/etc/twingate/connector.conf")] = _RunResult(exit_code=1)
        runner.prefix.append((("tee",), _RunResult(exit_code=0)))

        manager = _make_manager(runner=runner)
        result = manager.configure(
            provider=VPNProvider.TWINGATE,
            settings={"network": "demo", "access_token": "abc"},
            aspects=_aspects(),
        )
        self.assertEqual(result.status, VPNActionStatus.SUCCESS)
        tee_calls = [c for c in runner.calls if c["args"][:1] == ["tee"]]
        self.assertEqual(len(tee_calls), 1)
        self.assertIn("access_token=abc", tee_calls[0]["stdin"])
        self.assertIn("network=demo", tee_calls[0]["stdin"])
        return

    def test_configure_twingate_skipped_when_content_matches(self) -> None:
        settings = {"network": "demo", "access_token": "abc"}
        body = _render_twingate_conf(settings=settings)

        runner = _MockRunner()
        runner.exact[("cat", "/etc/twingate/connector.conf")] = _RunResult(
            exit_code=0, stdout=body,
        )

        manager = _make_manager(runner=runner)
        result = manager.configure(
            provider=VPNProvider.TWINGATE,
            settings=settings,
            aspects=_aspects(),
        )
        self.assertEqual(result.status, VPNActionStatus.SKIPPED)
        tee_calls = [c for c in runner.calls if c["args"][:1] == ["tee"]]
        self.assertEqual(tee_calls, [])
        return


# ----------------------------------------------------------------------
# bring_up
# ----------------------------------------------------------------------


def _tailscale_status_stopped() -> str:
    body = {
        "BackendState": "Stopped",
        "Self": {"HostName": "", "TailscaleIPs": []},
        "Peer": {},
    }
    return json.dumps(body)


def _tailscale_status_running(*, hostname: str = "myhost") -> str:
    body = {
        "BackendState": "Running",
        "Self": {
            "HostName": hostname,
            "TailscaleIPs": ["100.64.0.1"],
        },
        "MagicDNSSuffix": "tail-abc.ts.net",
        "Version": "1.66.0",
        "Peer": {
            "peer1key": {
                "HostName": "peer1",
                "TailscaleIPs": ["100.64.0.2"],
            },
        },
    }
    return json.dumps(body)


class TestBringUp(unittest.TestCase):

    def test_bring_up_tailscale_runs_systemctl_then_up(self) -> None:
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(exit_code=0, stdout="1.66.0")))
        runner.prefix.append((("tailscale", "status"), _RunResult(
            exit_code=0, stdout=_tailscale_status_stopped(),
        )))
        runner.prefix.append((("systemctl", "enable", "--now", "tailscaled"), _RunResult(exit_code=0)))
        runner.prefix.append((("tailscale", "up"), _RunResult(exit_code=0)))

        manager = _make_manager(runner=runner)
        result = manager.bring_up(
            provider=VPNProvider.TAILSCALE,
            authkey="key123",
            hostname="myhost",
            advertise_tags=["tag:server"],
            advertise_routes=None,
            exit_node=False,
            aspects=_aspects(),
        )
        self.assertEqual(result.status, VPNActionStatus.SUCCESS)
        sysd = [c for c in runner.calls if c["args"][:3] == ["systemctl", "enable", "--now"]]
        up = [c for c in runner.calls if c["args"][:2] == ["tailscale", "up"]]
        self.assertEqual(len(sysd), 1)
        self.assertEqual(len(up), 1)
        self.assertIn("--authkey=key123", up[0]["args"])
        self.assertIn("--hostname=myhost", up[0]["args"])
        return

    def test_bring_up_tailscale_skipped_when_running_with_matching_state(self) -> None:
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(exit_code=0, stdout="1.66.0")))
        runner.prefix.append((("tailscale", "status"), _RunResult(
            exit_code=0, stdout=_tailscale_status_running(hostname="myhost"),
        )))

        manager = _make_manager(runner=runner)
        result = manager.bring_up(
            provider=VPNProvider.TAILSCALE,
            authkey="key123",
            hostname="myhost",
            advertise_tags=None,
            advertise_routes=None,
            exit_node=False,
            aspects=_aspects(),
        )
        self.assertEqual(result.status, VPNActionStatus.SKIPPED)
        up = [c for c in runner.calls if c["args"][:2] == ["tailscale", "up"]]
        self.assertEqual(up, [])
        return

    def test_bring_up_twingate_runs_systemctl_enable_now(self) -> None:
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(exit_code=0, stdout="0.1.0")))
        runner.prefix.append((("twingate", "status"), _RunResult(
            exit_code=0, stdout="not running",
        )))
        runner.prefix.append((("systemctl", "enable", "--now", "twingate-connector"), _RunResult(exit_code=0)))

        manager = _make_manager(runner=runner)
        result = manager.bring_up(
            provider=VPNProvider.TWINGATE,
            authkey="",
            hostname=None,
            advertise_tags=None,
            advertise_routes=None,
            exit_node=False,
            aspects=_aspects(),
        )
        self.assertEqual(result.status, VPNActionStatus.SUCCESS)
        return


# ----------------------------------------------------------------------
# bring_down
# ----------------------------------------------------------------------


class TestBringDown(unittest.TestCase):

    def test_bring_down_tailscale_runs_tailscale_down(self) -> None:
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(exit_code=0, stdout="1.66.0")))
        runner.prefix.append((("tailscale", "status"), _RunResult(
            exit_code=0, stdout=_tailscale_status_running(),
        )))
        runner.prefix.append((("tailscale", "down"), _RunResult(exit_code=0)))

        manager = _make_manager(runner=runner)
        result = manager.bring_down(
            provider=VPNProvider.TAILSCALE,
            aspects=_aspects(),
        )
        self.assertEqual(result.status, VPNActionStatus.SUCCESS)
        return

    def test_bring_down_twingate_runs_systemctl_stop(self) -> None:
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(exit_code=0, stdout="0.1.0")))
        runner.prefix.append((("twingate", "status"), _RunResult(
            exit_code=0, stdout="running",
        )))
        runner.prefix.append((("systemctl", "stop", "twingate-connector"), _RunResult(exit_code=0)))

        manager = _make_manager(runner=runner)
        result = manager.bring_down(
            provider=VPNProvider.TWINGATE,
            aspects=_aspects(),
        )
        self.assertEqual(result.status, VPNActionStatus.SUCCESS)
        return

    def test_bring_down_skipped_when_already_down(self) -> None:
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(exit_code=0, stdout="1.66.0")))
        runner.prefix.append((("tailscale", "status"), _RunResult(
            exit_code=0, stdout=_tailscale_status_stopped(),
        )))

        manager = _make_manager(runner=runner)
        result = manager.bring_down(
            provider=VPNProvider.TAILSCALE,
            aspects=_aspects(),
        )
        self.assertEqual(result.status, VPNActionStatus.SKIPPED)
        return


# ----------------------------------------------------------------------
# status
# ----------------------------------------------------------------------


class TestStatus(unittest.TestCase):

    def test_status_tailscale_parses_running_json(self) -> None:
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(exit_code=0, stdout="1.66.0")))
        runner.prefix.append((("tailscale", "status"), _RunResult(
            exit_code=0, stdout=_tailscale_status_running(hostname="myhost"),
        )))

        manager = _make_manager(runner=runner)
        result = manager.status(
            provider=VPNProvider.TAILSCALE,
            aspects=_aspects(),
        )
        self.assertTrue(result.installed)
        self.assertTrue(result.running)
        self.assertEqual(result.self_address, "100.64.0.1")
        self.assertEqual(result.hostname, "myhost")
        self.assertEqual(result.peer_addresses, {"peer1": "100.64.0.2"})
        return

    def test_status_tailscale_returns_installed_false_when_missing(self) -> None:
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(exit_code=1)))

        manager = _make_manager(runner=runner)
        result = manager.status(
            provider=VPNProvider.TAILSCALE,
            aspects=_aspects(),
        )
        self.assertFalse(result.installed)
        self.assertFalse(result.running)
        return

    def test_status_twingate_parses_text(self) -> None:
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(exit_code=0, stdout="0.1.0")))
        runner.prefix.append((("twingate", "status"), _RunResult(
            exit_code=0, stdout="Connector is running and connected.",
        )))

        manager = _make_manager(runner=runner)
        result = manager.status(
            provider=VPNProvider.TWINGATE,
            aspects=_aspects(),
        )
        self.assertTrue(result.installed)
        self.assertTrue(result.running)
        self.assertIsNone(result.self_address)
        self.assertEqual(result.peer_addresses, {})
        return

    def test_status_tailscale_raises_on_unexpected_failure(self) -> None:
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(exit_code=0, stdout="1.66.0")))
        runner.prefix.append((("tailscale", "status"), _RunResult(
            exit_code=2, stderr="permission denied",
        )))

        manager = _make_manager(runner=runner)
        with self.assertRaises(VPNClientQueryError):
            manager.status(provider=VPNProvider.TAILSCALE, aspects=_aspects())
        return


# ----------------------------------------------------------------------
# uninstall
# ----------------------------------------------------------------------


class TestUninstall(unittest.TestCase):

    def test_uninstall_tailscale_purge_removes_repo_and_state(self) -> None:
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(exit_code=0, stdout="1.66.0")))
        runner.prefix.append((("systemctl", "stop"), _RunResult(exit_code=0)))
        runner.prefix.append((("dnf", "remove"), _RunResult(exit_code=0)))
        runner.prefix.append((("rm", "-rf"), _RunResult(exit_code=0)))

        manager = _make_manager(runner=runner)
        result = manager.uninstall(
            provider=VPNProvider.TAILSCALE,
            purge=True,
            aspects=_aspects(),
        )
        self.assertEqual(result.status, VPNActionStatus.SUCCESS)
        rm_calls = [c for c in runner.calls if c["args"][:2] == ["rm", "-rf"]]
        self.assertEqual(len(rm_calls), 1)
        self.assertIn("/var/lib/tailscale", rm_calls[0]["args"])
        self.assertIn("/etc/yum.repos.d/tailscale.repo", rm_calls[0]["args"])
        return

    def test_uninstall_skipped_when_absent(self) -> None:
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(exit_code=1)))

        manager = _make_manager(runner=runner)
        result = manager.uninstall(
            provider=VPNProvider.TAILSCALE,
            purge=False,
            aspects=_aspects(),
        )
        self.assertEqual(result.status, VPNActionStatus.SKIPPED)
        # No dnf remove.
        for call in runner.calls:
            self.assertNotEqual(call["args"][:2], ["dnf", "remove"])
        return


# ----------------------------------------------------------------------
# Elevation and dry-run
# ----------------------------------------------------------------------


class TestElevationAndDryRun(unittest.TestCase):

    def test_dry_run_short_circuits_every_write_verb(self) -> None:
        runner = _MockRunner()
        manager = _make_manager(runner=runner, dry_run=True)

        r1 = manager.install_tailscale(version=None, channel="stable", aspects=_aspects())
        r2 = manager.install_twingate_connector(version=None, aspects=_aspects())
        r3 = manager.configure(
            provider=VPNProvider.TWINGATE, settings={"x": "y"}, aspects=_aspects(),
        )
        r4 = manager.bring_up(
            provider=VPNProvider.TWINGATE, authkey="", hostname=None,
            advertise_tags=None, advertise_routes=None, exit_node=False,
            aspects=_aspects(),
        )
        r5 = manager.bring_down(provider=VPNProvider.TWINGATE, aspects=_aspects())
        r6 = manager.uninstall(provider=VPNProvider.TWINGATE, purge=False, aspects=_aspects())

        for result in [r1, r2, r3, r4, r5, r6]:
            self.assertEqual(result.status, VPNActionStatus.DRY_RUN)

        self.assertEqual(runner.calls, [])
        return

    def test_writes_pass_elevated_when_configured(self) -> None:
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(exit_code=1)))
        runner.prefix.append((("tee",), _RunResult(exit_code=0)))
        runner.prefix.append((("dnf", "install"), _RunResult(exit_code=0)))

        manager = _make_manager(runner=runner, elevated=True)
        manager.install_tailscale(version=None, channel="stable", aspects=_aspects())

        write_calls = [
            c for c in runner.calls
            if c["args"][:1] == ["tee"] or c["args"][:2] == ["dnf", "install"]
        ]
        self.assertGreater(len(write_calls), 0)
        for call in write_calls:
            self.assertTrue(call["elevated"])
        return

    def test_reads_never_elevate(self) -> None:
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(exit_code=0, stdout="1.66.0")))
        runner.prefix.append((("tailscale", "status"), _RunResult(
            exit_code=0, stdout=_tailscale_status_running(),
        )))

        manager = _make_manager(runner=runner, elevated=True)
        manager.status(provider=VPNProvider.TAILSCALE, aspects=_aspects())

        for call in runner.calls:
            self.assertFalse(call["elevated"])
        return


# ----------------------------------------------------------------------
# Parser unit tests
# ----------------------------------------------------------------------


class TestParsers(unittest.TestCase):

    def test_parse_tailscale_status_json_running(self) -> None:
        result = _parse_tailscale_status_json(
            payload=_tailscale_status_running(),
            installed_version="1.66.0",
        )
        self.assertTrue(result.running)
        self.assertEqual(result.self_address, "100.64.0.1")
        self.assertEqual(result.version, "1.66.0")
        return

    def test_parse_tailscale_status_json_stopped(self) -> None:
        result = _parse_tailscale_status_json(
            payload=_tailscale_status_stopped(),
            installed_version="1.66.0",
        )
        self.assertFalse(result.running)
        return

    def test_parse_twingate_status_text_running(self) -> None:
        result = _parse_twingate_status_text(
            payload="Connector is online and connected",
            installed_version="0.1.0",
        )
        self.assertTrue(result.running)
        return

    def test_parse_twingate_status_text_not_running(self) -> None:
        result = _parse_twingate_status_text(
            payload="connector is not running",
            installed_version="0.1.0",
        )
        self.assertFalse(result.running)
        return


# ----------------------------------------------------------------------
# Property tests (hypothesis)
# ----------------------------------------------------------------------


class TestPropertyIdempotency(unittest.TestCase):

    @given(channel=st.sampled_from(["stable", "unstable"]))
    @hyp_settings(max_examples=10, deadline=None)
    def test_property_install_tailscale_idempotent(self, channel: str) -> None:
        # First call: not installed -> SUCCESS. Then "package exists" -> SKIPPED.
        runner = _MockRunner()
        installed_state = {"installed": False}

        def _rpm_response() -> _RunResult:
            if installed_state["installed"] is True:
                return _RunResult(exit_code=0, stdout="1.66.0")
            return _RunResult(exit_code=1)

        # Programmable: rpm responses driven by a closure
        original_run = _MockRunner.run

        def custom_run(self, args, **kw):  # type: ignore[no-redef]
            self.calls.append({"args": list(args), "elevated": kw.get("elevated", False), "stdin": kw.get("stdin")})
            tup = tuple(args)
            if tup[:2] == ("rpm", "-q"):
                return _rpm_response()
            if tup[:1] == ("tee",):
                return _RunResult(exit_code=0)
            if tup[:2] == ("dnf", "install"):
                installed_state["installed"] = True
                return _RunResult(exit_code=0)
            return _RunResult(exit_code=0)

        runner.run = custom_run.__get__(runner, _MockRunner)  # type: ignore[attr-defined]

        manager = _make_manager(runner=runner)
        first = manager.install_tailscale(version=None, channel=channel, aspects=_aspects())
        second = manager.install_tailscale(version=None, channel=channel, aspects=_aspects())

        self.assertEqual(first.status, VPNActionStatus.SUCCESS)
        self.assertEqual(second.status, VPNActionStatus.SKIPPED)
        return

    @given(provider=st.sampled_from([VPNProvider.TAILSCALE, VPNProvider.TWINGATE]))
    @hyp_settings(max_examples=10, deadline=None)
    def test_property_uninstall_idempotent(self, provider: VPNProvider) -> None:
        runner = _MockRunner()
        installed_state = {"installed": True}

        def custom_run(self, args, **kw):  # type: ignore[no-redef]
            self.calls.append({"args": list(args), "elevated": kw.get("elevated", False), "stdin": kw.get("stdin")})
            tup = tuple(args)
            if tup[:2] == ("rpm", "-q"):
                if installed_state["installed"]:
                    return _RunResult(exit_code=0, stdout="1.0.0")
                return _RunResult(exit_code=1)
            if tup[:2] in (("dnf", "remove"), ("yum", "remove")):
                installed_state["installed"] = False
                return _RunResult(exit_code=0)
            return _RunResult(exit_code=0)

        runner.run = custom_run.__get__(runner, _MockRunner)  # type: ignore[attr-defined]

        manager = _make_manager(runner=runner)
        first = manager.uninstall(provider=provider, purge=False, aspects=_aspects())
        second = manager.uninstall(provider=provider, purge=False, aspects=_aspects())

        self.assertEqual(first.status, VPNActionStatus.SUCCESS)
        self.assertEqual(second.status, VPNActionStatus.SKIPPED)
        return

    @given(hostname=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=1, max_size=20))
    @hyp_settings(max_examples=10, deadline=None)
    def test_property_bring_up_idempotent_on_matching_state(self, hostname: str) -> None:
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(exit_code=0, stdout="1.66.0")))
        runner.prefix.append((("tailscale", "status"), _RunResult(
            exit_code=0, stdout=_tailscale_status_running(hostname=hostname),
        )))

        manager = _make_manager(runner=runner)
        result = manager.bring_up(
            provider=VPNProvider.TAILSCALE,
            authkey="key",
            hostname=hostname,
            advertise_tags=None,
            advertise_routes=None,
            exit_node=False,
            aspects=_aspects(),
        )
        self.assertEqual(result.status, VPNActionStatus.SKIPPED)
        return

    @given(version=st.one_of(st.none(), st.sampled_from(["1.0.0", "1.66.0", "2.0.0"])))
    @hyp_settings(max_examples=10, deadline=None)
    def test_property_dry_run_never_invokes_runner(self, version: Optional[str]) -> None:
        runner = _MockRunner()
        manager = _make_manager(runner=runner, dry_run=True)
        manager.install_tailscale(version=version, channel="stable", aspects=_aspects())
        manager.install_twingate_connector(version=version, aspects=_aspects())
        manager.uninstall(provider=VPNProvider.TAILSCALE, purge=True, aspects=_aspects())
        self.assertEqual(runner.calls, [])
        return


class TestAuditRegressions(unittest.TestCase):
    """
        Regression coverage for the Sysman VPN audit findings:
        C2 (status probe crash), C3 (authkey redaction), S-H4
        (config mutation), S-H5 (version-pin idempotency), S-H6
        (gpgcheck).
    """

    def test_bring_up_survives_transient_status_query_error(self) -> None:
        """
            C2: a transient ``VPNClientQueryError`` from the idempotency
            probe must not crash ``bring_up`` — the probe is best-effort.
        """
        runner = _MockRunner()
        # rpm -q reports tailscale installed.
        runner.prefix.append((("rpm", "-q"), _RunResult(
            exit_code=0, stdout="1.62.1", stderr="",
        )))
        # tailscale status --json fails with an unrecognised error so
        # the status() verb raises VPNClientQueryError.
        runner.exact[("tailscale", "status", "--json")] = _RunResult(
            exit_code=1, stdout="", stderr="transient daemon socket error",
        )
        manager = _make_manager(runner=runner)

        result = manager.bring_up(
            provider=VPNProvider.TAILSCALE,
            authkey="tskey-auth-ABC",
            hostname=None,
            advertise_tags=None,
            advertise_routes=None,
            exit_node=False,
            aspects=_aspects(),
        )

        # No crash: the verb fell through to dispatching the command.
        self.assertIn(
            result.status,
            (VPNActionStatus.SUCCESS, VPNActionStatus.FAILED),
        )
        return

    def test_bring_up_redacts_authkey_in_command(self) -> None:
        """
            C3: the Tailscale auth key must be redacted in the returned
            ``VPNActionResult.command``.
        """
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(
            exit_code=0, stdout="1.62.1", stderr="",
        )))
        runner.exact[("tailscale", "status", "--json")] = _RunResult(
            exit_code=0, stdout=json.dumps({"BackendState": "Stopped"}),
        )
        manager = _make_manager(runner=runner)

        secret = "tskey-auth-SUPERSECRET"
        result = manager.bring_up(
            provider=VPNProvider.TAILSCALE,
            authkey=secret,
            hostname=None,
            advertise_tags=None,
            advertise_routes=None,
            exit_node=False,
            aspects=_aspects(),
        )

        self.assertIsNotNone(result.command)
        joined = " ".join(result.command)
        self.assertNotIn(secret, joined)
        self.assertIn("--authkey=***", joined)
        return

    def test_install_does_not_mutate_shared_config_channel(self) -> None:
        """
            S-H4: ``install_tailscale`` must not mutate the shared
            instance config's ``tailscale_channel``.
        """
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(
            exit_code=1, stdout="", stderr="",
        )))
        config = RedHatVPNClientManagerConfig(tailscale_channel="stable")
        manager = RedHatVPNClientManager(runner=runner, config=config)
        manager._detected_pm = "dnf"

        manager.install_tailscale(
            version=None, channel="unstable", aspects=_aspects(),
        )

        self.assertEqual(config.tailscale_channel, "stable")
        return

    def test_qualified_version_pin_matches_via_version_release(self) -> None:
        """
            S-H5: a fully-qualified ``version-release`` pin must be
            satisfied by the installed RPM (no reinstall loop).
        """
        runner = _MockRunner()
        runner.exact[(
            "rpm", "-q", "--queryformat", "%{VERSION}", "tailscale",
        )] = _RunResult(exit_code=0, stdout="1.62.1")
        runner.exact[(
            "rpm", "-q", "--queryformat", "%{VERSION}-%{RELEASE}",
            "tailscale",
        )] = _RunResult(exit_code=0, stdout="1.62.1-1")
        manager = _make_manager(runner=runner)

        result = manager.install_tailscale(
            version="1.62.1-1", channel="stable", aspects=_aspects(),
        )

        self.assertEqual(result.status, VPNActionStatus.SKIPPED)
        return

    def test_tailscale_repo_enables_gpgcheck(self) -> None:
        """
            S-H6 sibling: the Tailscale ``.repo`` body must enable
            ``gpgcheck``.
        """
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(
            exit_code=1, stdout="", stderr="",
        )))
        manager = _make_manager(runner=runner)

        manager.install_tailscale(
            version=None, channel="stable", aspects=_aspects(),
        )

        tee_calls = [
            c for c in runner.calls
            if c["args"][0] == "tee" and c["args"][-1].endswith(".repo")
        ]
        self.assertEqual(len(tee_calls), 1)
        body = tee_calls[0]["stdin"] or ""
        self.assertIn("gpgcheck=1", body)
        self.assertNotIn("gpgcheck=0", body)
        return

    def test_twingate_repo_enables_gpgcheck(self) -> None:
        """
            S-H6: the Twingate ``.repo`` body must set ``gpgcheck=1``.
        """
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(
            exit_code=1, stdout="", stderr="",
        )))
        manager = _make_manager(runner=runner)

        manager.install_twingate_connector(
            version=None, aspects=_aspects(),
        )

        tee_calls = [
            c for c in runner.calls
            if c["args"][0] == "tee" and c["args"][-1].endswith(".repo")
        ]
        self.assertEqual(len(tee_calls), 1)
        body = tee_calls[0]["stdin"] or ""
        self.assertIn("gpgcheck=1", body)
        self.assertNotIn("gpgcheck=0", body)
        return

    def test_tailscale_repo_uses_default_distro_release(self) -> None:
        """
            By default the Tailscale repo URL targets ``rhel/9``.
        """
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(
            exit_code=1, stdout="", stderr="",
        )))
        manager = _make_manager(runner=runner)

        manager.install_tailscale(
            version=None, channel="stable", aspects=_aspects(),
        )

        tee_calls = [
            c for c in runner.calls
            if c["args"][0] == "tee" and c["args"][-1].endswith(".repo")
        ]
        self.assertEqual(len(tee_calls), 1)
        body = tee_calls[0]["stdin"] or ""
        self.assertIn("pkgs.tailscale.com/stable/rhel/9/", body)
        return

    def test_tailscale_repo_distro_release_are_overridable(self) -> None:
        """
            The Tailscale repo distro / release slugs are config
            parameters; overriding them must change the repo URL so the
            manager is not locked to ``rhel/9``.
        """
        runner = _MockRunner()
        runner.prefix.append((("rpm", "-q"), _RunResult(
            exit_code=1, stdout="", stderr="",
        )))
        config = RedHatVPNClientManagerConfig(
            tailscale_distro="centos",
            tailscale_release="8",
        )
        manager = RedHatVPNClientManager(runner=runner, config=config)
        manager._detected_pm = "dnf"

        manager.install_tailscale(
            version=None, channel="stable", aspects=_aspects(),
        )

        tee_calls = [
            c for c in runner.calls
            if c["args"][0] == "tee" and c["args"][-1].endswith(".repo")
        ]
        self.assertEqual(len(tee_calls), 1)
        body = tee_calls[0]["stdin"] or ""
        self.assertIn("pkgs.tailscale.com/stable/centos/8/", body)
        self.assertNotIn("/rhel/9/", body)
        return


if __name__ == "__main__":
    unittest.main()
