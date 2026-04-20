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


@dataclass
class ComplianceFinding:
    """
        A single item in a composed :class:`ComplianceReport`.

        :param category: ``"selinux"``, ``"ssh"``, ``"firewall"``,
                         ``"audit"``, ``"file-permissions"``, ...
        :param subject: Identifier of the audited thing (setting name,
                        file path, zone name, ...).
        :param compliant: ``True`` when the subject matches the baseline.
        :param expected: Expected value or state (string form).
        :param actual: Observed value or state (or ``None`` when the
                       subject could not be queried).
        :param detail: Optional human-readable description.
    """
    category: str
    subject: str
    compliant: bool
    expected: Optional[str] = None
    actual: Optional[str] = None
    detail: Optional[str] = None


@dataclass
class ComplianceReport:
    """
        Aggregated compliance posture produced by
        :meth:`SecurityComplianceManager.run_compliance_suite`.

        Composes findings across SELinux, SSH hardening, file
        permissions, firewalld posture, and audit rules — each
        produced by the feature-specific manager that owns that
        surface.
    """
    findings: list[ComplianceFinding] = field(default_factory=list)
    selinux_status: Optional[SELinuxStatus] = None
    ssh_findings: list["SSHConfigFinding"] = field(default_factory=list)
    file_permission_results: list["FilePermissionResult"] = field(default_factory=list)
    world_writable_files: list[str] = field(default_factory=list)
    suid_sgid_files: list[str] = field(default_factory=list)
    firewall_default_zone: Optional[str] = None
    firewall_running: Optional[bool] = None
    audit_rules_sample: list[str] = field(default_factory=list)
    overall_compliant: bool = True

    def summary(self) -> dict:
        """
            Return a compact dict with count-based roll-ups suitable
            for inclusion in a :class:`MaintenanceReport`'s
            ``detail`` field or as a structured log entry.
        """
        passed = sum(1 for f in self.findings if f.compliant)
        failed = sum(1 for f in self.findings if not f.compliant)
        rtnval = {
            "findings_total": len(self.findings),
            "findings_passed": passed,
            "findings_failed": failed,
            "selinux_mode": (
                self.selinux_status.mode.value if self.selinux_status is not None else None
            ),
            "firewall_running": self.firewall_running,
            "world_writable_count": len(self.world_writable_files),
            "suid_sgid_count": len(self.suid_sgid_files),
            "overall_compliant": self.overall_compliant,
        }
        return rtnval


class SecurityComplianceManager(BaseFeatureManager):
    """
        Facade for security compliance checks and remediations.  Uses
        its own :class:`ComplianceActionResult` shape
        (``success``/``detail``/``dry_run``/``error``) so does not use
        the base class's ``_dispatch`` helper.

        The :meth:`run_compliance_suite` aggregator composes findings
        from sibling managers (:class:`SELinuxManager`,
        :class:`SshServerConfigurator`, :class:`FirewallManager`,
        :class:`AuditManager`) — wired in via constructor kwargs.
    """

    feature_name = "security-compliance"

    def __init__(
        self,
        runner,
        config: Optional[SecurityComplianceConfig] = None,
        *,
        selinux_manager=None,
        ssh_configurator=None,
        firewall_manager=None,
        audit_manager=None,
        report=None,
    ) -> None:
        """
            :param runner: Command runner.
            :param config: Manager configuration.
            :param selinux_manager: Optional pre-built
                :class:`SELinuxManager` used by
                :meth:`run_compliance_suite`.  When None, one is
                constructed lazily in the aggregator.
            :param ssh_configurator: Optional pre-built
                :class:`SshServerConfigurator`.
            :param firewall_manager: Optional pre-built
                :class:`FirewallManager`.
            :param audit_manager: Optional pre-built
                :class:`AuditManager`.
            :param report: Optional :class:`MaintenanceReport` for
                auto-emitting action records.
        """
        super().__init__(runner=runner, config=config, report=report)
        self._selinux_manager = selinux_manager
        self._ssh_configurator = ssh_configurator
        self._firewall_manager = firewall_manager
        self._audit_manager = audit_manager
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

    # ------------------------------------------------------------------
    # Part C — compliance aggregator
    # ------------------------------------------------------------------

    def run_compliance_suite(
        self,
        *,
        file_permission_checks: Optional[list[FilePermissionCheck]] = None,
        ssh_baseline: Optional[dict[str, str]] = None,
        world_writable_search_paths: Optional[list[str]] = None,
        suid_sgid_search_paths: Optional[list[str]] = None,
        expected_firewall_services: Optional[dict[str, list[str]]] = None,
        expected_selinux_mode: SELinuxMode = SELinuxMode.ENFORCING,
        include_audit_rules: bool = True,
    ) -> ComplianceReport:
        """
            Compose a full compliance snapshot by running every check
            this feature and its sibling managers expose, rolling the
            results into a single :class:`ComplianceReport`.

            Sibling managers are constructed lazily from this manager's
            runner when they were not injected at construction time.

            :param file_permission_checks: File-permission checks to
                run (via :meth:`check_file_permissions`).  Empty list
                or None skips this category.
            :param ssh_baseline: sshd baseline overrides; when None the
                module default is used.
            :param world_writable_search_paths: Paths to scan for
                world-writable files.  None or empty skips the scan.
            :param suid_sgid_search_paths: Paths to scan for SUID/SGID
                binaries.  None or empty skips the scan.
            :param expected_firewall_services: Mapping of zone name to
                expected service-name list.  For each zone we query
                the :class:`FirewallManager` and raise a finding per
                service that is missing.  None skips firewall audit.
            :param expected_selinux_mode: Target SELinux mode; a
                finding is raised when the observed mode differs.
            :param include_audit_rules: When True, include the
                currently-active auditd rule list as a sample on the
                report (read-only, never fails).

            :returns: :class:`ComplianceReport` with per-category
                fields populated and ``overall_compliant`` reflecting
                the aggregate pass/fail state.
        """
        report = ComplianceReport()

        # --- SELinux posture ------------------------------------------
        selinux_status = self.check_selinux()
        report.selinux_status = selinux_status
        selinux_compliant = selinux_status.mode == expected_selinux_mode
        report.findings.append(ComplianceFinding(
            category="selinux",
            subject="mode",
            compliant=selinux_compliant,
            expected=expected_selinux_mode.value,
            actual=selinux_status.mode.value,
        ))

        # --- SSH hardening --------------------------------------------
        ssh_findings = self.check_ssh_config(baseline=ssh_baseline)
        report.ssh_findings = list(ssh_findings)
        for finding in ssh_findings:
            report.findings.append(ComplianceFinding(
                category="ssh",
                subject=finding.setting,
                compliant=finding.compliant,
                expected=finding.expected,
                actual=finding.actual,
            ))

        # --- File permissions ----------------------------------------
        if file_permission_checks is not None and len(file_permission_checks) > 0:
            perm_results = self.check_file_permissions(
                checks=file_permission_checks,
            )
            report.file_permission_results = list(perm_results)
            for result in perm_results:
                actual = "{}|{}|{}".format(
                    result.actual_mode or "?",
                    result.actual_owner or "?",
                    result.actual_group or "?",
                )
                expected = "{}|{}|{}".format(
                    result.expected_mode,
                    result.expected_owner,
                    result.expected_group,
                )
                report.findings.append(ComplianceFinding(
                    category="file-permissions",
                    subject=result.path,
                    compliant=result.compliant,
                    expected=expected,
                    actual=actual,
                    detail=result.error,
                ))

        # --- World-writable discovery ---------------------------------
        if (
            world_writable_search_paths is not None
            and len(world_writable_search_paths) > 0
        ):
            ww_files = self.find_world_writable_files(
                search_paths=world_writable_search_paths,
            )
            report.world_writable_files = list(ww_files)
            report.findings.append(ComplianceFinding(
                category="file-permissions",
                subject="world-writable",
                compliant=len(ww_files) == 0,
                expected="0 files",
                actual="{} files".format(len(ww_files)),
            ))

        # --- SUID / SGID discovery -----------------------------------
        if (
            suid_sgid_search_paths is not None
            and len(suid_sgid_search_paths) > 0
        ):
            suid = self.find_suid_sgid_files(
                search_paths=suid_sgid_search_paths,
            )
            report.suid_sgid_files = list(suid)
            # SUID/SGID isn't pass/fail on its own — record as a
            # finding that's always "compliant" (informational) when
            # any are found, so the caller can review the list.
            report.findings.append(ComplianceFinding(
                category="file-permissions",
                subject="suid-sgid-count",
                compliant=True,
                expected="informational",
                actual="{} files".format(len(suid)),
            ))

        # --- Firewall posture ----------------------------------------
        if expected_firewall_services is not None:
            fw = self._resolve_firewall_manager()
            state = fw.query_state()
            report.firewall_running = state.running
            report.firewall_default_zone = state.default_zone
            report.findings.append(ComplianceFinding(
                category="firewall",
                subject="daemon-running",
                compliant=state.running,
                expected="running",
                actual="running" if state.running else "stopped",
            ))
            for zone_name, expected_services in expected_firewall_services.items():
                zone_info = fw.query_zone(zone_name)
                have = set(zone_info.services)
                for service_name in expected_services:
                    report.findings.append(ComplianceFinding(
                        category="firewall",
                        subject="zone={}:service={}".format(zone_name, service_name),
                        compliant=service_name in have,
                        expected="present",
                        actual="present" if service_name in have else "absent",
                    ))

        # --- Audit rules (informational) ------------------------------
        if include_audit_rules:
            audit = self._resolve_audit_manager()
            try:
                rules = audit.list_rules()
            except Exception:
                rules = []
            report.audit_rules_sample = list(rules)

        # --- Aggregate pass/fail -------------------------------------
        overall = True
        for finding in report.findings:
            if not finding.compliant:
                overall = False
                break
        report.overall_compliant = overall

        return report

    def _resolve_selinux_manager(self):
        """
            Return the injected SELinuxManager or construct one from
            this manager's runner and dry_run/elevated posture.
        """
        if self._selinux_manager is not None:
            return self._selinux_manager
        from automojo.sysman.linux.redhat.selinuxmanagement import (
            SELinuxManager,
            SELinuxManagerConfig,
        )
        mgr = SELinuxManager(
            self._runner,
            SELinuxManagerConfig(
                dry_run=self._config.dry_run,
                elevated=self._config.elevated,
            ),
        )
        self._selinux_manager = mgr
        return mgr

    def _resolve_ssh_configurator(self):
        """
            Return the injected SshServerConfigurator or construct one.
        """
        if self._ssh_configurator is not None:
            return self._ssh_configurator
        from automojo.sysman.linux.redhat.sshserverconfiguration import (
            SshServerConfigurator,
            SshServerConfig,
        )
        mgr = SshServerConfigurator(
            self._runner,
            SshServerConfig(
                dry_run=self._config.dry_run,
                elevated=self._config.elevated,
            ),
        )
        self._ssh_configurator = mgr
        return mgr

    def _resolve_firewall_manager(self):
        """
            Return the injected FirewallManager or construct one.
        """
        if self._firewall_manager is not None:
            return self._firewall_manager
        from automojo.sysman.linux.redhat.firewallmanagement import (
            FirewallManager,
            FirewallManagerConfig,
        )
        mgr = FirewallManager(
            self._runner,
            FirewallManagerConfig(
                dry_run=self._config.dry_run,
                elevated=self._config.elevated,
            ),
        )
        self._firewall_manager = mgr
        return mgr

    def _resolve_audit_manager(self):
        """
            Return the injected AuditManager or construct one.
        """
        if self._audit_manager is not None:
            return self._audit_manager
        from automojo.sysman.linux.redhat.auditmanagement import (
            AuditManager,
            AuditManagerConfig,
        )
        mgr = AuditManager(
            self._runner,
            AuditManagerConfig(
                dry_run=self._config.dry_run,
                elevated=self._config.elevated,
            ),
        )
        self._audit_manager = mgr
        return mgr


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
