# automojo.sysman.linux.redhat.securitycompliance
#
# SecurityComplianceManager performs SELinux state queries, file permission
# audits, sshd hardening checks, and world-writable/SUID file discovery
# through the ICommandRunner abstraction.

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.featurebase import (
    BaseFeatureManager,
    FeatureConfig,
)


class SELinuxMode(Enum):
    """
        Recognised SELinux modes.
    """
    ENFORCING = "enforcing"
    PERMISSIVE = "permissive"
    DISABLED = "disabled"


@dataclass
class SecurityComplianceConfig(FeatureConfig):
    """
        Configuration for :class:`SecurityComplianceManager`.  Inherits
        ``dry_run`` and ``elevated`` from :class:`FeatureConfig`.

        :param ssh_config_path: Path to the sshd configuration file.
    """
    ssh_config_path: str = "/etc/ssh/sshd_config"


@dataclass
class SELinuxStatus:
    """
        SELinux mode and policy reported by getenforce/sestatus.
    """
    mode: SELinuxMode
    policy: Optional[str] = None


@dataclass
class FilePermissionCheck:
    """
        Expected mode/owner/group for a file.
    """
    path: str
    expected_mode: str
    expected_owner: str
    expected_group: str


@dataclass
class FilePermissionResult:
    """
        Outcome of a file permission check.
    """
    path: str
    compliant: bool
    expected_mode: str
    expected_owner: str
    expected_group: str
    actual_mode: Optional[str] = None
    actual_owner: Optional[str] = None
    actual_group: Optional[str] = None
    error: Optional[str] = None


@dataclass
class SSHConfigFinding:
    """
        Single finding from an sshd_config audit.
    """
    setting: str
    expected: str
    actual: Optional[str]
    compliant: bool


@dataclass
class ComplianceActionResult:
    """
        Outcome of a mutating compliance action.
    """
    success: bool
    detail: Optional[str] = None
    dry_run: bool = False
    error: Optional[str] = None


class SecurityComplianceManager(BaseFeatureManager):
    """
        Facade for security compliance checks and remediations.  Uses
        its own :class:`ComplianceActionResult` (``success``/``detail``/
        ``dry_run``/``error`` — not compatible with
        FeatureActionResult) so does not use the base class's
        ``_dispatch`` helper.
    """

    feature_name = "security-compliance"

    def __init__(
        self,
        runner,
        config: Optional[SecurityComplianceConfig] = None,
    ) -> None:
        """
            :param runner: Command runner.
            :param config: Manager configuration.
        """
        super().__init__(runner=runner, config=config)
        return

    def check_selinux(self) -> SELinuxStatus:
        """
            Query the current SELinux mode and policy.
        """
        mode = SELinuxMode.DISABLED
        policy: Optional[str] = None
        getenforce = self._runner.run(["getenforce"], elevated=False)
        if getenforce.exit_code == 0:
            mode_text = getenforce.stdout.strip().lower()
            if mode_text in ("enforcing", "permissive", "disabled"):
                mode = SELinuxMode(mode_text)
        sestatus = self._runner.run(["sestatus"], elevated=False)
        if sestatus.exit_code == 0:
            for raw in sestatus.stdout.splitlines():
                line = raw.strip()
                if line.lower().startswith("loaded policy name:"):
                    _, _, value = line.partition(":")
                    policy = value.strip()
        status = SELinuxStatus(mode=mode, policy=policy)
        return status

    def set_selinux_mode(
        self,
        mode: SELinuxMode,
    ) -> ComplianceActionResult:
        """
            Set the SELinux runtime mode via ``setenforce``.
        """
        if mode == SELinuxMode.DISABLED:
            return ComplianceActionResult(
                success=False,
                detail="setenforce cannot disable SELinux at runtime",
                error="disable requires kernel boot flag",
            )
        value = "1" if mode == SELinuxMode.ENFORCING else "0"
        command = ["setenforce", value]
        if self._config.dry_run:
            return ComplianceActionResult(
                success=True, detail=mode.value, dry_run=True,
            )
        run_result = self._runner.run(command, elevated=self._config.elevated)
        return ComplianceActionResult(
            success=run_result.exit_code == 0,
            detail=mode.value,
            error=run_result.stderr if run_result.exit_code != 0 else None,
        )

    def check_file_permissions(
        self,
        checks: list[FilePermissionCheck],
    ) -> list[FilePermissionResult]:
        """
            Run ``stat`` for each check and compare against expectations.
        """
        results: list[FilePermissionResult] = []
        for check in checks:
            run_result = self._runner.run(
                ["stat", "-c", "%a|%U|%G", check.path], elevated=False,
            )
            if run_result.exit_code != 0:
                results.append(FilePermissionResult(
                    path=check.path,
                    compliant=False,
                    expected_mode=check.expected_mode,
                    expected_owner=check.expected_owner,
                    expected_group=check.expected_group,
                    error=run_result.stderr,
                ))
                continue
            fields = run_result.stdout.strip().split("|")
            if len(fields) < 3:
                results.append(FilePermissionResult(
                    path=check.path,
                    compliant=False,
                    expected_mode=check.expected_mode,
                    expected_owner=check.expected_owner,
                    expected_group=check.expected_group,
                    error="unexpected stat output",
                ))
                continue
            actual_mode = fields[0]
            actual_owner = fields[1]
            actual_group = fields[2]
            compliant = (
                actual_mode == check.expected_mode
                and actual_owner == check.expected_owner
                and actual_group == check.expected_group
            )
            results.append(FilePermissionResult(
                path=check.path,
                compliant=compliant,
                expected_mode=check.expected_mode,
                expected_owner=check.expected_owner,
                expected_group=check.expected_group,
                actual_mode=actual_mode,
                actual_owner=actual_owner,
                actual_group=actual_group,
            ))
        return results

    def remediate_file_permissions(
        self,
        results: list[FilePermissionResult],
    ) -> list[ComplianceActionResult]:
        """
            Apply chmod/chown to bring files into compliance.
        """
        outcomes: list[ComplianceActionResult] = []
        for result in results:
            if result.compliant:
                continue
            if self._config.dry_run:
                outcomes.append(ComplianceActionResult(
                    success=True, detail=result.path, dry_run=True,
                ))
                continue
            chmod_result = self._runner.run(
                ["chmod", result.expected_mode, result.path],
                elevated=self._config.elevated,
            )
            chown_result = self._runner.run(
                ["chown",
                 "{}:{}".format(result.expected_owner, result.expected_group),
                 result.path],
                elevated=self._config.elevated,
            )
            success = chmod_result.exit_code == 0 and chown_result.exit_code == 0
            error = None
            if not success:
                error = chmod_result.stderr or chown_result.stderr
            outcomes.append(ComplianceActionResult(
                success=success, detail=result.path, error=error,
            ))
        return outcomes

    def check_ssh_config(
        self,
        baseline: Optional[dict[str, str]] = None,
    ) -> list[SSHConfigFinding]:
        """
            Audit sshd_config against ``baseline`` (uses defaults if None).
        """
        desired = baseline if baseline is not None else _default_ssh_baseline()
        content = self._read(self._config.ssh_config_path)
        parsed = _parse_ssh_config(content=content)
        findings: list[SSHConfigFinding] = []
        for setting, expected in desired.items():
            actual = parsed.get(setting)
            compliant = actual is not None and actual.lower() == expected.lower()
            findings.append(SSHConfigFinding(
                setting=setting,
                expected=expected,
                actual=actual,
                compliant=compliant,
            ))
        return findings

    def apply_ssh_hardening(
        self,
        settings: dict[str, str],
    ) -> ComplianceActionResult:
        """
            Patch sshd_config with ``settings`` and restart sshd.
        """
        current = self._read(self._config.ssh_config_path)
        new_content = _apply_ssh_settings(content=current, settings=settings)
        if new_content == current:
            return ComplianceActionResult(success=True, detail="no changes needed")
        if self._config.dry_run:
            return ComplianceActionResult(
                success=True, detail=self._config.ssh_config_path, dry_run=True,
            )
        write_result = self._runner.run(
            ["tee", self._config.ssh_config_path],
            elevated=self._config.elevated,
            stdin=new_content,
        )
        if write_result.exit_code != 0:
            return ComplianceActionResult(
                success=False, error=write_result.stderr,
            )
        restart_result = self._runner.run(
            ["systemctl", "restart", "sshd"],
            elevated=self._config.elevated,
        )
        if restart_result.exit_code != 0:
            return ComplianceActionResult(
                success=False, error=restart_result.stderr,
            )
        return ComplianceActionResult(
            success=True, detail=self._config.ssh_config_path,
        )

    def find_world_writable_files(
        self,
        search_paths: list[str],
    ) -> list[str]:
        """
            Run ``find -perm -0002 -type f`` across ``search_paths``.
        """
        if len(search_paths) == 0:
            return []
        command = ["find"] + list(search_paths) + [
            "-xdev", "-type", "f", "-perm", "-0002",
        ]
        run_result = self._runner.run(command, elevated=self._config.elevated)
        if run_result.exit_code != 0:
            return []
        return _split_lines(stdout=run_result.stdout)

    def find_suid_sgid_files(
        self,
        search_paths: list[str],
    ) -> list[str]:
        """
            Run ``find`` across ``search_paths`` to list SUID/SGID binaries.
        """
        if len(search_paths) == 0:
            return []
        command = ["find"] + list(search_paths) + [
            "-xdev", "-type", "f",
            "(", "-perm", "-4000", "-o", "-perm", "-2000", ")",
        ]
        run_result = self._runner.run(command, elevated=self._config.elevated)
        if run_result.exit_code != 0:
            return []
        return _split_lines(stdout=run_result.stdout)

    def _read(self, path: str) -> str:
        """
            Read a file via ``cat``, returning empty on missing.
        """
        run_result = self._runner.run(["cat", path], elevated=False)
        if run_result.exit_code != 0:
            return ""
        return run_result.stdout


def _default_ssh_baseline() -> dict[str, str]:
    """
        Default hardening baseline for sshd_config.
    """
    baseline = {
        "PermitRootLogin": "no",
        "PasswordAuthentication": "no",
        "PermitEmptyPasswords": "no",
        "X11Forwarding": "no",
        "MaxAuthTries": "4",
        "IgnoreRhosts": "yes",
        "HostbasedAuthentication": "no",
        "UsePAM": "yes",
    }
    return baseline


def _parse_ssh_config(*, content: str) -> dict[str, str]:
    """
        Parse sshd_config into a setting -> value dict, honouring case insensitivity.
    """
    parsed: dict[str, str] = {}
    for raw in content.splitlines():
        line = raw.strip()
        if len(line) == 0 or line.startswith("#"):
            continue
        tokens = line.split(None, 1)
        if len(tokens) < 2:
            continue
        parsed[tokens[0]] = tokens[1].strip()
    return parsed


def _apply_ssh_settings(
    *,
    content: str,
    settings: dict[str, str],
) -> str:
    """
        Return sshd_config content with ``settings`` applied (replacing or appending).
    """
    lower_targets = {k.lower(): k for k in settings.keys()}
    remaining = dict(settings)
    updated: list[str] = []
    for raw in content.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if len(stripped) == 0 or stripped.startswith("#"):
            updated.append(line)
            continue
        tokens = stripped.split(None, 1)
        if len(tokens) < 2:
            updated.append(line)
            continue
        key_lower = tokens[0].lower()
        if key_lower in lower_targets:
            canonical = lower_targets[key_lower]
            updated.append("{} {}".format(canonical, remaining[canonical]))
            remaining.pop(canonical, None)
        else:
            updated.append(line)
    for key, value in remaining.items():
        updated.append("{} {}".format(key, value))
    body = "\n".join(updated)
    if not body.endswith("\n"):
        body = body + "\n"
    return body


def _split_lines(*, stdout: str) -> list[str]:
    """
        Split command stdout into non-empty lines.
    """
    lines: list[str] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if len(line) > 0:
            lines.append(line)
    return lines
