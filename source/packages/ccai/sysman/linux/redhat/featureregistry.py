# ccai.sysman.linux.redhat.featureregistry
#
# Name-keyed registry of feature-manager classes in this package.
# Callers use the registry to enumerate available features, look up a
# manager by string name, or build a declared maintenance plan from a
# JSON / YAML document keyed by feature name.
#
# The registry does NOT import manager classes eagerly — it holds lazy
# (module_name, class_name) targets and imports on first resolution.
# This keeps import cost small and avoids circular-import hazards in
# the presence of optional SSH dependencies (see ssh.py).

import importlib

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FeatureDescriptor:
    """
        Registration record for a single feature manager.

        :param name: Canonical feature name (matches the spec folder
                     name without the layer prefix, e.g.
                     ``"package-management"``).
        :param layer: Architecture layer number (1 / 2 / 3).
        :param module: Dotted module path of the implementation.
        :param class_name: Manager class name within ``module``.
        :param summary: One-line description suitable for ``--list`` output.
    """
    name: str
    layer: int
    module: str
    class_name: str
    summary: str


class FeatureNotRegisteredError(Exception):
    """
        Raised when a lookup targets an unregistered feature name.
    """

    def __init__(self, *, name: str) -> None:
        self.name = name
        err_msg_lines = ["Feature is not registered."]
        err_msg_lines.append("    NAME: {}".format(name))
        err_msg = "\n".join(err_msg_lines)
        super().__init__(err_msg)
        return


class FeatureRegistry:
    """
        In-memory registry mapping feature names to
        :class:`FeatureDescriptor` records.

        The registry is instance-based rather than a global singleton so
        tests and multi-tenant callers can hold independent registries
        without interference.
    """

    def __init__(self) -> None:
        self._descriptors: dict[str, FeatureDescriptor] = {}
        self._classes: dict[str, type] = {}
        return

    def register(self, descriptor: FeatureDescriptor) -> None:
        """
            Register a feature descriptor.

            :param descriptor: The descriptor to add.
            :raises ValueError: When ``descriptor.name`` is already
                                registered.
        """
        if descriptor.name in self._descriptors:
            err_msg_lines = ["Feature is already registered."]
            err_msg_lines.append("    NAME: {}".format(descriptor.name))
            err_msg = "\n".join(err_msg_lines)
            raise ValueError(err_msg)
        self._descriptors[descriptor.name] = descriptor
        return

    def unregister(self, name: str) -> None:
        """
            Remove a previously-registered feature.  No error is raised
            when the name is unknown.
        """
        if name in self._descriptors:
            del self._descriptors[name]
        if name in self._classes:
            del self._classes[name]
        return

    def describe(self, name: str) -> FeatureDescriptor:
        """
            Return the descriptor for ``name``.

            :raises FeatureNotRegisteredError: When ``name`` is unknown.
        """
        if name not in self._descriptors:
            raise FeatureNotRegisteredError(name=name)
        descriptor = self._descriptors[name]
        return descriptor

    def resolve(self, name: str) -> type:
        """
            Import and return the manager class registered under ``name``.

            The class is cached after first resolution.

            :raises FeatureNotRegisteredError: When ``name`` is unknown.
        """
        if name in self._classes:
            cached = self._classes[name]
            return cached

        descriptor = self.describe(name=name)
        module_obj = importlib.import_module(descriptor.module)
        cls = getattr(module_obj, descriptor.class_name)
        self._classes[name] = cls
        return cls

    def names(self) -> list[str]:
        """
            Return the registered feature names in registration order.
        """
        rtnval = list(self._descriptors.keys())
        return rtnval

    def by_layer(self, layer: int) -> list[FeatureDescriptor]:
        """
            Return every descriptor whose ``layer`` matches.
        """
        rtnval = [d for d in self._descriptors.values() if d.layer == layer]
        return rtnval

    def all_descriptors(self) -> list[FeatureDescriptor]:
        """
            Return every registered descriptor in registration order.
        """
        rtnval = list(self._descriptors.values())
        return rtnval


_DEFAULT_REGISTRY_SEED: list[FeatureDescriptor] = [
    FeatureDescriptor(
        name="command-execution",
        layer=1,
        module="ccai.sysman.linux.redhat.commandexecution",
        class_name="CommandExecutor",
        summary="Run single commands, ordered sequences, and shell scripts.",
    ),
    FeatureDescriptor(
        name="command-runner-architecture",
        layer=1,
        module="ccai.sysman.linux.redhat.runners.local",
        class_name="LocalCommandRunner",
        summary="Transport-agnostic command runner with aspects + metrics.",
    ),
    FeatureDescriptor(
        name="maintenance-reporting",
        layer=2,
        module="ccai.sysman.linux.redhat.maintenancereporting",
        class_name="MaintenanceReport",
        summary="Structured report of actions performed during a run.",
    ),
    FeatureDescriptor(
        name="acl-management",
        layer=3,
        module="ccai.sysman.linux.redhat.aclmanagement",
        class_name="AclManager",
        summary="POSIX ACLs, ext attrs, file capabilities (setfacl/chattr/setcap).",
    ),
    FeatureDescriptor(
        name="audit-management",
        layer=3,
        module="ccai.sysman.linux.redhat.auditmanagement",
        class_name="AuditManager",
        summary="auditd daemon, rules, and aureport/ausearch tools.",
    ),
    FeatureDescriptor(
        name="authentication-management",
        layer=3,
        module="ccai.sysman.linux.redhat.authenticationmanagement",
        class_name="AuthenticationManager",
        summary="authselect profiles, PAM stacks, faillock, pwquality, limits.",
    ),
    FeatureDescriptor(
        name="backup-restore",
        layer=3,
        module="ccai.sysman.linux.redhat.backuprestore",
        class_name="BackupRestoreManager",
        summary="tar/rsync file-tree archives with manifests + retention.",
    ),
    FeatureDescriptor(
        name="boot-management",
        layer=3,
        module="ccai.sysman.linux.redhat.bootmanagement",
        class_name="BootManager",
        summary="grub2 default kernel, kernel command line, bootctl.",
    ),
    FeatureDescriptor(
        name="certificate-issuance",
        layer=3,
        module="ccai.sysman.linux.redhat.certificateissuance",
        class_name="CertificateIssuanceManager",
        summary="OpenSSL keys/CSRs/self-signed certs + certmonger + ACME (certbot).",
    ),
    FeatureDescriptor(
        name="certificate-trust-management",
        layer=3,
        module="ccai.sysman.linux.redhat.certificatetrustmanagement",
        class_name="CertificateTrustManager",
        summary="CA trust anchors under /etc/pki/ca-trust/.",
    ),
    FeatureDescriptor(
        name="cockpit-management",
        layer=3,
        module="ccai.sysman.linux.redhat.cockpitmanagement",
        class_name="CockpitManager",
        summary="Cockpit web management console socket + firewall.",
    ),
    FeatureDescriptor(
        name="configuration-management",
        layer=3,
        module="ccai.sysman.linux.redhat.configurationmanagement",
        class_name="ConfigurationManager",
        summary="Write, diff, validate system configuration files.",
    ),
    FeatureDescriptor(
        name="container-management",
        layer=3,
        module="ccai.sysman.linux.redhat.containermanagement",
        class_name="ContainerManager",
        summary="Podman container + image lifecycle.",
    ),
    FeatureDescriptor(
        name="dns-server-management",
        layer=3,
        module="ccai.sysman.linux.redhat.dnsservermanagement",
        class_name="DnsServerManager",
        summary="BIND / Unbound / dnsmasq DNS server configuration & lifecycle.",
    ),
    FeatureDescriptor(
        name="encryption-management",
        layer=3,
        module="ccai.sysman.linux.redhat.encryptionmanagement",
        class_name="EncryptionManager",
        summary="LUKS2 disk encryption + TPM2 / Clevis bindings + crypttab.",
    ),
    FeatureDescriptor(
        name="environment-configuration",
        layer=3,
        module="ccai.sysman.linux.redhat.environmentconfiguration",
        class_name="EnvironmentConfigurator",
        summary="Shell environment variables in profile.d + user rc files.",
    ),
    FeatureDescriptor(
        name="filesystem-management",
        layer=3,
        module="ccai.sysman.linux.redhat.filesystemmanagement",
        class_name="FilesystemManager",
        summary="Directories, symlinks, mounts, /etc/fstab.",
    ),
    FeatureDescriptor(
        name="filesystem-quotas",
        layer=3,
        module="ccai.sysman.linux.redhat.filesystemquotas",
        class_name="FilesystemQuotaManager",
        summary="XFS / ext quotas via xfs_quota / setquota / repquota.",
    ),
    FeatureDescriptor(
        name="firewall-management",
        layer=3,
        module="ccai.sysman.linux.redhat.firewallmanagement",
        class_name="FirewallManager",
        summary="firewalld zones, services, ports, rich rules.",
    ),
    FeatureDescriptor(
        name="hardware-inventory",
        layer=3,
        module="ccai.sysman.linux.redhat.hardwareinventory",
        class_name="HardwareInventoryManager",
        summary="Read-only DMI / CPU / memory / block / PCI / USB / NIC snapshot.",
    ),
    FeatureDescriptor(
        name="identity-management",
        layer=3,
        module="ccai.sysman.linux.redhat.identitymanagement",
        class_name="IdentityManager",
        summary="realmd / SSSD / Kerberos client (AD / IPA join, /etc/krb5.conf).",
    ),
    FeatureDescriptor(
        name="insights-management",
        layer=3,
        module="ccai.sysman.linux.redhat.insightsmanagement",
        class_name="InsightsManager",
        summary="Red Hat Insights client registration + uploads.",
    ),
    FeatureDescriptor(
        name="kdump-management",
        layer=3,
        module="ccai.sysman.linux.redhat.kdumpmanagement",
        class_name="KdumpManager",
        summary="Kernel crash-dump service + /etc/kdump.conf.",
    ),
    FeatureDescriptor(
        name="kernel-parameters",
        layer=3,
        module="ccai.sysman.linux.redhat.kernelparameters",
        class_name="KernelParamManager",
        summary="sysctl parameters + /etc/sysctl.d/ drop-ins.",
    ),
    FeatureDescriptor(
        name="localization-management",
        layer=3,
        module="ccai.sysman.linux.redhat.localizationmanagement",
        class_name="LocalizationManager",
        summary="Locale, timezone, keymap via localectl + timedatectl.",
    ),
    FeatureDescriptor(
        name="log-management",
        layer=3,
        module="ccai.sysman.linux.redhat.logmanagement",
        class_name="LogManager",
        summary="journalctl, logrotate drop-ins, journal vacuum.",
    ),
    FeatureDescriptor(
        name="mail-transport-management",
        layer=3,
        module="ccai.sysman.linux.redhat.mailtransportmanagement",
        class_name="MailTransportManager",
        summary="Postfix main.cf, master.cf, lookup tables, queue, aliases.",
    ),
    FeatureDescriptor(
        name="network-configuration",
        layer=3,
        module="ccai.sysman.linux.redhat.networkconfiguration",
        class_name="NetworkConfigurator",
        summary="Hostname, /etc/hosts, DNS resolver, NTP, NetworkManager.",
    ),
    FeatureDescriptor(
        name="nfs-server-management",
        layer=3,
        module="ccai.sysman.linux.redhat.nfsservermanagement",
        class_name="NfsServerManager",
        summary="/etc/exports + drop-ins, exportfs, idmapd, nfs-server lifecycle.",
    ),
    FeatureDescriptor(
        name="package-management",
        layer=3,
        module="ccai.sysman.linux.redhat.packagemanagement",
        class_name="PackageManager",
        summary="RPM install/remove/update/query via dnf + rpm.",
    ),
    FeatureDescriptor(
        name="precision-time-management",
        layer=3,
        module="ccai.sysman.linux.redhat.precisiontimemanagement",
        class_name="PrecisionTimeManager",
        summary="linuxptp (ptp4l, phc2sys, pmc) configuration and lifecycle.",
    ),
    FeatureDescriptor(
        name="process-management",
        layer=3,
        module="ccai.sysman.linux.redhat.processmanagement",
        class_name="ProcessManager",
        summary="Enumerate / signal / reprioritise processes.",
    ),
    FeatureDescriptor(
        name="remote-log-forwarding",
        layer=3,
        module="ccai.sysman.linux.redhat.remotelogforwarding",
        class_name="RemoteLogForwardingManager",
        summary="rsyslog drop-ins + journald-upload remote forwarding.",
    ),
    FeatureDescriptor(
        name="repository-management",
        layer=3,
        module="ccai.sysman.linux.redhat.repositorymanagement",
        class_name="RepositoryManager",
        summary="dnf/yum .repo definitions under /etc/yum.repos.d/.",
    ),
    FeatureDescriptor(
        name="resource-control-management",
        layer=3,
        module="ccai.sysman.linux.redhat.resourcecontrolmanagement",
        class_name="ResourceControlManager",
        summary="systemd resource controls (cgroups v2): CPUQuota, MemoryMax, slices.",
    ),
    FeatureDescriptor(
        name="samba-server-management",
        layer=3,
        module="ccai.sysman.linux.redhat.sambaservermanagement",
        class_name="SambaServerManager",
        summary="smb.conf shares, smbpasswd users, smb/nmb/winbind lifecycle.",
    ),
    FeatureDescriptor(
        name="scheduled-tasks",
        layer=3,
        module="ccai.sysman.linux.redhat.scheduledtasks",
        class_name="ScheduledTaskManager",
        summary="cron jobs under /etc/cron.d/ and systemd timers.",
    ),
    FeatureDescriptor(
        name="security-compliance",
        layer=3,
        module="ccai.sysman.linux.redhat.securitycompliance",
        class_name="SecurityComplianceManager",
        summary="SELinux mode, permissions, SSH hardening, SUID discovery.",
    ),
    FeatureDescriptor(
        name="selinux-management",
        layer=3,
        module="ccai.sysman.linux.redhat.selinuxmanagement",
        class_name="SELinuxManager",
        summary="File/port contexts, booleans, policy modules.",
    ),
    FeatureDescriptor(
        name="service-management",
        layer=3,
        module="ccai.sysman.linux.redhat.servicemanagement",
        class_name="ServiceManager",
        summary="systemd service lifecycle + journal queries.",
    ),
    FeatureDescriptor(
        name="ssh-server-configuration",
        layer=3,
        module="ccai.sysman.linux.redhat.sshserverconfiguration",
        class_name="SshServerConfigurator",
        summary="sshd_config + drop-ins + validate + reload.",
    ),
    FeatureDescriptor(
        name="storage-management",
        layer=3,
        module="ccai.sysman.linux.redhat.storagemanagement",
        class_name="StorageManager",
        summary="Partitioning, filesystems, LVM (PV/VG/LV).",
    ),
    FeatureDescriptor(
        name="subscription-management",
        layer=3,
        module="ccai.sysman.linux.redhat.subscriptionmanagement",
        class_name="SubscriptionManager",
        summary="RHSM registration, entitlements, release pinning.",
    ),
    FeatureDescriptor(
        name="tuned-management",
        layer=3,
        module="ccai.sysman.linux.redhat.tunedmanagement",
        class_name="TunedManager",
        summary="tuned.service active profile + custom profiles.",
    ),
    FeatureDescriptor(
        name="python-environment-management",
        layer=3,
        module="ccai.sysman.linux.redhat.pythonenvironmentmanagement",
        class_name="RedHatPythonEnvironmentManager",
        summary="Python virtual environments: venv create/remove, pip install/uninstall, deploy from spec.",
    ),
    FeatureDescriptor(
        name="user-group-management",
        layer=3,
        module="ccai.sysman.linux.redhat.usergroupmanagement",
        class_name="UserManager",
        summary="Local users, groups, membership, SSH authorized keys.",
    ),
    FeatureDescriptor(
        name="virtualization-management",
        layer=3,
        module="ccai.sysman.linux.redhat.virtualizationmanagement",
        class_name="VirtualizationManager",
        summary="libvirt domains, networks, storage pools/volumes, snapshots.",
    ),
]


def build_default_registry() -> FeatureRegistry:
    """
        Build a :class:`FeatureRegistry` pre-populated with every
        implemented feature in this package (the 47 source-backed
        entries).  Spec-only features are deliberately excluded to
        avoid :class:`ImportError` at resolution time.
    """
    registry = FeatureRegistry()
    for descriptor in _DEFAULT_REGISTRY_SEED:
        registry.register(descriptor=descriptor)
    return registry
