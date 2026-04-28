# automojo.sysman.linux.redhat.resourcecontrolmanagement
#
# ResourceControlManager wraps systemd cgroups-v2 resource control
# on Red Hat Enterprise Linux through the ICommandRunner abstraction.
#
# The feature provides idempotent, dry-run-aware operations for:
#   * Querying ``systemctl show <unit> --property=...`` values.
#   * Setting transient (``--runtime``) and persistent (drop-in)
#     resource directives.
#   * Defining and removing custom slices.
#   * Attaching units to slices.
#   * Running transient scopes via ``systemd-run``.
#   * Introspecting the cgroup tree via ``systemd-cgls`` and
#     ``systemd-cgtop`` and reading per-cgroup attribute files
#     under ``/sys/fs/cgroup``.
#
# All ``daemon-reload`` calls are delegated to an injected
# :class:`ServiceManager` (or compatible facade) — this module never
# spells ``systemctl daemon-reload`` directly.

import os

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.featurebase import (
    ActionStatus,
    FeatureActionResult,
    FeatureConfig,
)
from automojo.sysman.linux.redhat.runners.base import ICommandRunner


# ----------------------------------------------------------------------
# Module-level constants
# ----------------------------------------------------------------------

_SYSTEMCTL = "systemctl"
_SYSTEMD_RUN = "systemd-run"
_SYSTEMD_CGLS = "systemd-cgls"
_SYSTEMD_CGTOP = "systemd-cgtop"
_CAT = "cat"
_TEE = "tee"
_RM = "rm"
_MKDIR = "mkdir"

_DEFAULT_SHOW_PROPERTIES = (
    "CPUQuotaPerSecUSec",
    "CPUWeight",
    "MemoryMax",
    "MemoryHigh",
    "TasksMax",
    "IOWeight",
    "IPAccounting",
    "Slice",
    "ControlGroup",
)


ResourceActionStatus = ActionStatus


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------


@dataclass
class ResourceControlManagerConfig(FeatureConfig):
    """
        Configuration for :class:`ResourceControlManager`.  Inherits
        ``dry_run`` and ``elevated`` from :class:`FeatureConfig`.

        :param systemd_etc_dir: Root directory for systemd unit
                                drop-ins and slice unit files.
        :param dropin_basename: Basename for the resource-control
                                drop-in file written under
                                ``<systemd_etc_dir>/<unit>.d/``.
        :param cgroup_root: Root of the cgroup-v2 unified
                            hierarchy.
        :param default_scope_slice: Slice used for transient
                                    scopes when the caller does
                                    not pass one explicitly.
    """
    systemd_etc_dir: str = "/etc/systemd/system"
    dropin_basename: str = "10-resource-control.conf"
    cgroup_root: str = "/sys/fs/cgroup"
    default_scope_slice: str = "system.slice"


# ----------------------------------------------------------------------
# Data models
# ----------------------------------------------------------------------


@dataclass
class ResourceActionResult(FeatureActionResult):
    """
        Outcome of a resource-control action.  Extends
        :class:`FeatureActionResult` with resource-control fields.

        :param unit: The systemd unit / slice acted on.
        :param changed_keys: Keys whose value was actually
                             modified.
        :param skipped_keys: Keys whose current value already
                             matched the requested value.
    """
    unit: Optional[str] = None
    changed_keys: list[str] = field(default_factory=list)
    skipped_keys: list[str] = field(default_factory=list)


@dataclass
class CgroupUsage:
    """
        One row returned by ``systemd-cgtop --raw``.

        :param path: cgroup path.
        :param tasks: Number of tasks in the cgroup.
        :param cpu_percent: CPU percent at sample time.
        :param memory_bytes: Resident memory in bytes.
        :param input_per_s: IO read bytes per second.
        :param output_per_s: IO write bytes per second.
    """
    path: str
    tasks: Optional[int] = None
    cpu_percent: Optional[float] = None
    memory_bytes: Optional[int] = None
    input_per_s: Optional[int] = None
    output_per_s: Optional[int] = None


def _empty_str_list() -> list[str]:
    """
        Default-factory for :class:`SliceSpec` list fields.

        :returns: A fresh empty list per call.
    """
    rtnval: list[str] = []
    return rtnval


def _empty_str_dict() -> dict[str, str]:
    """
        Default-factory for :class:`SliceSpec` properties dict.

        :returns: A fresh empty dict per call.
    """
    rtnval: dict[str, str] = {}
    return rtnval


@dataclass
class SliceSpec:
    """
        Declarative specification used by
        :meth:`ResourceControlManager.define_slice`.

        :param name: Slice identifier, without the ``.slice``
                     suffix.
        :param description: Optional description for the
                            ``[Unit]`` section.
        :param before: Optional ``Before=`` entries.
        :param wants: Optional ``Wants=`` entries.
        :param properties: Resource-control directives written
                           into the ``[Slice]`` section.
    """
    name: str
    description: Optional[str] = None
    before: list[str] = field(default_factory=_empty_str_list)
    wants: list[str] = field(default_factory=_empty_str_list)
    properties: dict[str, str] = field(default_factory=_empty_str_dict)


# ----------------------------------------------------------------------
# ServiceManager protocol — minimal duck-typed contract
# ----------------------------------------------------------------------


class _NullServiceManager:
    """
        Sentinel ``ServiceManager`` used when the caller did not
        inject one.  Any attempt to delegate raises so that the
        dependency is always made explicit.
    """

    def daemon_reload(self):
        """
            Always raises — this manager exists only to make the
            missing collaborator obvious.

            :raises RuntimeError: Always.
        """
        err_msg_lines = [
            "ResourceControlManager.daemon_reload() invoked without an injected ServiceManager.",
            "Construct ResourceControlManager(runner, config, service_manager=<ServiceManager>).",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise RuntimeError(err_msg)

    def enable(self, name: str):
        """
            Always raises — explicit dependency requirement.

            :param name: Unit name (ignored).
            :raises RuntimeError: Always.
        """
        err_msg_lines = [
            "ResourceControlManager.enable() invoked without an injected ServiceManager.",
            "Construct ResourceControlManager(runner, config, service_manager=<ServiceManager>).",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise RuntimeError(err_msg)


# ----------------------------------------------------------------------
# Manager
# ----------------------------------------------------------------------


class ResourceControlManager:
    """
        Facade for systemd cgroups-v2 resource control.

        Exposed verbs:
            * :meth:`query_properties`
            * :meth:`set_properties_transient`
            * :meth:`set_properties_persistent`
            * :meth:`remove_property`
            * :meth:`define_slice`
            * :meth:`set_slice_properties`
            * :meth:`attach_to_slice`
            * :meth:`remove_slice`
            * :meth:`run_scoped`
            * :meth:`cgls`
            * :meth:`cgtop`
            * :meth:`read_cgroup_attr`

        The class deliberately does not inherit from
        :class:`BaseFeatureManager` because it manages a heterogeneous
        mix of read / write / file-IO operations whose dispatch
        patterns do not map cleanly onto the single-shot
        ``_dispatch`` helper.  Result wrapping is therefore done
        locally by :meth:`_wrap`.
    """

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[ResourceControlManagerConfig] = None,
        *,
        service_manager: Optional[object] = None,
    ) -> None:
        """
            :param runner: The command runner used by every
                           operation.
            :param config: Manager configuration.  Defaults to a
                           fresh
                           :class:`ResourceControlManagerConfig`.
            :param service_manager: A facade exposing
                                    ``daemon_reload()`` (and
                                    optionally ``enable(name)``)
                                    used for any operation that
                                    needs to apply or enable a
                                    unit.  Required for
                                    :meth:`set_properties_persistent`,
                                    :meth:`remove_property`,
                                    :meth:`define_slice`,
                                    :meth:`set_slice_properties`,
                                    :meth:`attach_to_slice`, and
                                    :meth:`remove_slice`.
        """
        if config is None:
            config = ResourceControlManagerConfig()
        if service_manager is None:
            service_manager = _NullServiceManager()

        self._runner: ICommandRunner = runner
        self._config: ResourceControlManagerConfig = config
        self._service_manager: object = service_manager
        return

    @property
    def config(self) -> ResourceControlManagerConfig:
        """
            The active configuration.
        """
        return self._config

    @property
    def runner(self) -> ICommandRunner:
        """
            The injected command runner.
        """
        return self._runner

    @property
    def service_manager(self) -> object:
        """
            The injected service manager (or null sentinel).
        """
        return self._service_manager

    # ------------------------------------------------------------------
    # Read verbs
    # ------------------------------------------------------------------

    def query_properties(
        self,
        unit: str,
        keys: Optional[list[str]] = None,
    ) -> dict[str, str]:
        """
            Query ``systemctl show <unit> --property=<...>`` and
            return a parsed ``dict[str, str]`` keyed by property
            name.

            :param unit: Unit (service or slice) to query.
            :param keys: Optional list of property keys.  When
                         ``None`` the documented
                         :data:`_DEFAULT_SHOW_PROPERTIES` set is
                         used.

            :returns: ``dict[str, str]``.  Empty when the unit
                      does not exist or the command returns
                      non-zero.
        """
        if keys is None:
            effective_keys: list[str] = list(_DEFAULT_SHOW_PROPERTIES)
        else:
            effective_keys = list(keys)

        if len(effective_keys) == 0:
            rtnval: dict[str, str] = {}
            return rtnval

        prop_string = ",".join(effective_keys)
        command = [
            _SYSTEMCTL, "show", unit, "--property={}".format(prop_string),
        ]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval = {}
            return rtnval

        rtnval = _parse_show_output(stdout=run_result.stdout)
        return rtnval

    def cgls(self, unit: Optional[str] = None) -> str:
        """
            Return the raw output of ``systemd-cgls --no-pager
            [--unit <unit>]``.

            :param unit: Optional unit filter.

            :returns: Raw ``stdout``.  Empty string when the
                      command exits non-zero.
        """
        command: list[str] = [_SYSTEMD_CGLS, "--no-pager"]
        if unit is not None:
            command.append("--unit")
            command.append(unit)
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval = ""
            return rtnval
        rtnval = run_result.stdout
        return rtnval

    def cgtop(self) -> list[CgroupUsage]:
        """
            Run ``systemd-cgtop --iterations=1 --raw`` and parse
            its output into :class:`CgroupUsage` records.

            :returns: One :class:`CgroupUsage` per parsed row.
                      Empty list on non-zero exit.
        """
        command = [_SYSTEMD_CGTOP, "--iterations=1", "--raw"]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: list[CgroupUsage] = []
            return rtnval
        rtnval = _parse_cgtop_output(stdout=run_result.stdout)
        return rtnval

    def read_cgroup_attr(self, path: str, attr: str) -> Optional[str]:
        """
            Read an attribute file under
            ``<cgroup_root>/<path>/<attr>`` via ``cat``.

            :param path: cgroup path relative to
                         ``config.cgroup_root``.
            :param attr: Attribute filename
                         (e.g. ``"memory.current"``).

            :returns: Stripped attribute value, or ``None`` when
                      the file is missing or the read fails.
        """
        full_path = "{}/{}/{}".format(
            self._config.cgroup_root.rstrip("/"), path.strip("/"), attr,
        )
        command = [_CAT, full_path]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: Optional[str] = None
            return rtnval
        if len(run_result.stdout) == 0:
            rtnval = ""
            return rtnval
        rtnval = run_result.stdout.strip()
        return rtnval

    # ------------------------------------------------------------------
    # Write verbs — properties
    # ------------------------------------------------------------------

    def set_properties_transient(
        self,
        unit: str,
        properties: dict[str, str],
    ) -> ResourceActionResult:
        """
            Apply transient (``--runtime``) resource-control
            properties via ``systemctl set-property --runtime``.

            Idempotency: when every requested ``(key, value)``
            pair already matches the unit's reported value, the
            method returns ``SKIPPED`` without invoking the
            mutating command.

            :param unit: Service or slice unit name.
            :param properties: ``dict[str, str]`` of directives.

            :returns: :class:`ResourceActionResult`.
        """
        if len(properties) == 0:
            empty_result = self._wrap(
                status=ResourceActionStatus.SKIPPED,
                unit=unit,
                detail="no properties supplied",
                command=None,
                skipped_keys=[],
                changed_keys=[],
            )
            return empty_result

        ordered_keys = list(properties.keys())
        current = self.query_properties(unit=unit, keys=ordered_keys)

        changed_keys: list[str] = []
        skipped_keys: list[str] = []
        for key in ordered_keys:
            requested_value = properties[key]
            current_value = current.get(key, "")
            if _values_match(key=key, requested=requested_value, current=current_value):
                skipped_keys.append(key)
            else:
                changed_keys.append(key)

        kv_args = ["{}={}".format(k, properties[k]) for k in ordered_keys]
        command = [_SYSTEMCTL, "set-property", "--runtime", unit] + kv_args

        if len(changed_keys) == 0:
            skipped_result = self._wrap(
                status=ResourceActionStatus.SKIPPED,
                unit=unit,
                detail="all keys already at requested values",
                command=command,
                skipped_keys=skipped_keys,
                changed_keys=changed_keys,
            )
            return skipped_result

        if self._config.dry_run:
            dry_result = self._wrap(
                status=ResourceActionStatus.DRY_RUN,
                unit=unit,
                detail="set-property --runtime: {}".format(", ".join(kv_args)),
                command=command,
                skipped_keys=skipped_keys,
                changed_keys=changed_keys,
            )
            return dry_result

        run_result = self._runner.run(
            list(command), elevated=self._config.elevated,
        )
        mapped_status = _map_status(exit_code=run_result.exit_code)
        rtnval = self._wrap(
            status=mapped_status,
            unit=unit,
            detail=", ".join(kv_args),
            command=command,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            skipped_keys=skipped_keys,
            changed_keys=changed_keys,
        )
        return rtnval

    def set_properties_persistent(
        self,
        unit: str,
        properties: dict[str, str],
    ) -> ResourceActionResult:
        """
            Persist resource-control directives by writing (or
            updating) a drop-in file at
            ``<systemd_etc_dir>/<unit>.d/<dropin_basename>``.

            On any change a ``daemon-reload`` is delegated to the
            injected :class:`ServiceManager`.

            Idempotency: when every requested ``(key, value)``
            already exists in the drop-in file, the method
            returns ``SKIPPED`` without writing.

            :param unit: Service or slice unit name (e.g.
                         ``"nginx.service"`` or
                         ``"workers.slice"``).
            :param properties: ``dict[str, str]`` of directives.

            :returns: :class:`ResourceActionResult`.
        """
        if len(properties) == 0:
            empty_result = self._wrap(
                status=ResourceActionStatus.SKIPPED,
                unit=unit,
                detail="no properties supplied",
                command=None,
            )
            return empty_result

        section_name = _section_for_unit(unit=unit)
        dropin_path = self._dropin_path(unit=unit)

        existing_content = self._read_file(path=dropin_path)
        if existing_content is None:
            existing_content = ""

        merged_content, changed_keys, skipped_keys = _merge_dropin_properties(
            existing_content=existing_content,
            section_name=section_name,
            properties=properties,
        )

        write_command = [_TEE, dropin_path]

        if len(changed_keys) == 0:
            skipped_result = self._wrap(
                status=ResourceActionStatus.SKIPPED,
                unit=unit,
                detail="all directives already present",
                command=write_command,
                skipped_keys=skipped_keys,
                changed_keys=changed_keys,
            )
            return skipped_result

        if self._config.dry_run:
            dry_result = self._wrap(
                status=ResourceActionStatus.DRY_RUN,
                unit=unit,
                detail="write {} keys to {}".format(
                    len(changed_keys), dropin_path,
                ),
                command=write_command,
                skipped_keys=skipped_keys,
                changed_keys=changed_keys,
            )
            return dry_result

        write_failure = self._write_dropin(
            path=dropin_path, content=merged_content,
        )
        if write_failure is not None:
            return _augment_unit_keys(
                result=write_failure,
                unit=unit,
                changed_keys=changed_keys,
                skipped_keys=skipped_keys,
            )

        reload_failure = self._delegate_daemon_reload()
        if reload_failure is not None:
            return _augment_unit_keys(
                result=reload_failure,
                unit=unit,
                changed_keys=changed_keys,
                skipped_keys=skipped_keys,
            )

        success = self._wrap(
            status=ResourceActionStatus.SUCCESS,
            unit=unit,
            detail="wrote {} directive(s) to {}".format(
                len(changed_keys), dropin_path,
            ),
            command=write_command,
            exit_code=0,
            skipped_keys=skipped_keys,
            changed_keys=changed_keys,
        )
        return success

    def remove_property(
        self,
        unit: str,
        key: str,
    ) -> ResourceActionResult:
        """
            Strip a resource-control directive from the unit's
            drop-in file.

            When stripping leaves the drop-in with no remaining
            directives the file is removed via ``rm -f``.  A
            ``daemon-reload`` is then delegated to the injected
            :class:`ServiceManager`.

            Idempotency: when the drop-in file is missing or
            does not contain ``key``, the method returns
            ``SKIPPED``.

            :param unit: Service or slice unit name.
            :param key: Directive key to remove (e.g.
                        ``"CPUQuota"``).

            :returns: :class:`ResourceActionResult`.
        """
        section_name = _section_for_unit(unit=unit)
        dropin_path = self._dropin_path(unit=unit)
        existing_content = self._read_file(path=dropin_path)

        if existing_content is None or len(existing_content.strip()) == 0:
            skipped_result = self._wrap(
                status=ResourceActionStatus.SKIPPED,
                unit=unit,
                detail="drop-in file not present",
                command=[_RM, "-f", dropin_path],
            )
            return skipped_result

        new_content, removed = _strip_dropin_property(
            existing_content=existing_content,
            section_name=section_name,
            key=key,
        )
        if not removed:
            skipped_result = self._wrap(
                status=ResourceActionStatus.SKIPPED,
                unit=unit,
                detail="directive {} not present".format(key),
                command=[_TEE, dropin_path],
            )
            return skipped_result

        is_now_empty = _dropin_is_effectively_empty(content=new_content)

        if is_now_empty:
            mutating_command: list[str] = [_RM, "-f", dropin_path]
        else:
            mutating_command = [_TEE, dropin_path]

        if self._config.dry_run:
            dry_result = self._wrap(
                status=ResourceActionStatus.DRY_RUN,
                unit=unit,
                detail="remove {} from {}".format(key, dropin_path),
                command=mutating_command,
                changed_keys=[key],
                skipped_keys=[],
            )
            return dry_result

        if is_now_empty:
            rm_run = self._runner.run(
                list(mutating_command), elevated=self._config.elevated,
            )
            if rm_run.exit_code != 0:
                failed = self._wrap(
                    status=ResourceActionStatus.FAILED,
                    unit=unit,
                    detail="failed to remove drop-in {}".format(dropin_path),
                    command=mutating_command,
                    exit_code=rm_run.exit_code,
                    stdout=rm_run.stdout,
                    stderr=rm_run.stderr,
                    changed_keys=[key],
                    skipped_keys=[],
                )
                return failed
        else:
            write_failure = self._write_dropin(
                path=dropin_path, content=new_content,
            )
            if write_failure is not None:
                return _augment_unit_keys(
                    result=write_failure,
                    unit=unit,
                    changed_keys=[key],
                    skipped_keys=[],
                )

        reload_failure = self._delegate_daemon_reload()
        if reload_failure is not None:
            return _augment_unit_keys(
                result=reload_failure,
                unit=unit,
                changed_keys=[key],
                skipped_keys=[],
            )

        success = self._wrap(
            status=ResourceActionStatus.SUCCESS,
            unit=unit,
            detail="removed {} from {}".format(key, dropin_path),
            command=mutating_command,
            exit_code=0,
            changed_keys=[key],
            skipped_keys=[],
        )
        return success

    # ------------------------------------------------------------------
    # Write verbs — slices
    # ------------------------------------------------------------------

    def define_slice(
        self,
        spec: SliceSpec,
        *,
        auto_enable: bool = False,
    ) -> ResourceActionResult:
        """
            Define (or update) a slice unit file at
            ``<systemd_etc_dir>/<spec.name>.slice``.

            Idempotency: when the on-disk slice file matches the
            rendered body byte-for-byte, the method returns
            ``SKIPPED``.

            On any change a ``daemon-reload`` is delegated to the
            injected :class:`ServiceManager`.  When
            ``auto_enable`` is ``True`` the slice is also
            enabled via the same facade after the reload.

            :param spec: The :class:`SliceSpec`.
            :param auto_enable: When True, enable the slice unit
                                via the injected service manager.

            :returns: :class:`ResourceActionResult`.
        """
        slice_unit = "{}.slice".format(spec.name)
        slice_path = "{}/{}".format(
            self._config.systemd_etc_dir.rstrip("/"), slice_unit,
        )
        rendered_body = _render_slice_unit_body(spec=spec)
        write_command = [_TEE, slice_path]

        existing_body = self._read_file(path=slice_path)
        if existing_body is not None and existing_body == rendered_body:
            skipped_result = self._wrap(
                status=ResourceActionStatus.SKIPPED,
                unit=slice_unit,
                detail="slice unit file already up-to-date",
                command=write_command,
            )
            return skipped_result

        if self._config.dry_run:
            dry_result = self._wrap(
                status=ResourceActionStatus.DRY_RUN,
                unit=slice_unit,
                detail="write slice unit body to {}".format(slice_path),
                command=write_command,
            )
            return dry_result

        write_failure = self._write_dropin(
            path=slice_path, content=rendered_body,
        )
        if write_failure is not None:
            write_failure.unit = slice_unit
            return write_failure

        reload_failure = self._delegate_daemon_reload()
        if reload_failure is not None:
            reload_failure.unit = slice_unit
            return reload_failure

        if auto_enable:
            enable_failure = self._delegate_enable(name=slice_unit)
            if enable_failure is not None:
                enable_failure.unit = slice_unit
                return enable_failure

        success = self._wrap(
            status=ResourceActionStatus.SUCCESS,
            unit=slice_unit,
            detail="slice unit written to {}".format(slice_path),
            command=write_command,
            exit_code=0,
        )
        return success

    def set_slice_properties(
        self,
        name: str,
        properties: dict[str, str],
    ) -> ResourceActionResult:
        """
            Apply persistent resource directives to a slice using
            the same drop-in pattern as
            :meth:`set_properties_persistent`.

            :param name: Slice name (without the ``.slice``
                         suffix).
            :param properties: Directives.

            :returns: :class:`ResourceActionResult`.
        """
        slice_unit = "{}.slice".format(name)
        rtnval = self.set_properties_persistent(
            unit=slice_unit, properties=properties,
        )
        return rtnval

    def attach_to_slice(
        self,
        unit: str,
        slice_name: str,
    ) -> ResourceActionResult:
        """
            Attach a unit to a slice persistently by writing
            ``Slice=<slice_name>.slice`` into the unit's drop-in
            file.

            :param unit: Service unit name.
            :param slice_name: Slice name (without the ``.slice``
                               suffix).

            :returns: :class:`ResourceActionResult`.
        """
        properties = {"Slice": "{}.slice".format(slice_name)}
        rtnval = self.set_properties_persistent(
            unit=unit, properties=properties,
        )
        return rtnval

    def remove_slice(
        self,
        name: str,
        *,
        strict: bool = False,
    ) -> ResourceActionResult:
        """
            Remove a slice unit file and any units attached to
            it.

            Steps:
                1. Discover units whose ``Slice`` directive
                   currently points at ``<name>.slice`` by
                   reading every drop-in file in
                   ``<systemd_etc_dir>/*.d/<dropin_basename>``.
                2. When ``strict=True`` and any attached units
                   were found, return ``FAILED`` without making
                   any change.
                3. Otherwise detach each attached unit by
                   calling :meth:`remove_property` for the
                   ``Slice`` key.
                4. ``rm -f`` the slice unit file at
                   ``<systemd_etc_dir>/<name>.slice``.
                5. Delegate ``daemon-reload``.

            Idempotency: when the slice unit file does not exist
            and no attached units were discovered, returns
            ``SKIPPED``.

            :param name: Slice name (without the ``.slice``
                         suffix).
            :param strict: When True, raise on attached units.

            :returns: :class:`ResourceActionResult`.
        """
        slice_unit = "{}.slice".format(name)
        slice_path = "{}/{}".format(
            self._config.systemd_etc_dir.rstrip("/"), slice_unit,
        )
        rm_command = [_RM, "-f", slice_path]

        attached_units = self._find_attached_units(slice_name=name)
        slice_file_exists = self._read_file(path=slice_path) is not None

        if (not slice_file_exists) and len(attached_units) == 0:
            skipped_result = self._wrap(
                status=ResourceActionStatus.SKIPPED,
                unit=slice_unit,
                detail="slice unit not present and no attached units",
                command=rm_command,
            )
            return skipped_result

        if strict and len(attached_units) > 0:
            failed = self._wrap(
                status=ResourceActionStatus.FAILED,
                unit=slice_unit,
                detail="strict=True: {} unit(s) still attached: {}".format(
                    len(attached_units), ", ".join(attached_units),
                ),
                command=rm_command,
                exit_code=None,
                stderr="strict mode forbids removing a slice with attached units",
            )
            return failed

        if self._config.dry_run:
            dry_result = self._wrap(
                status=ResourceActionStatus.DRY_RUN,
                unit=slice_unit,
                detail="detach {} unit(s) and rm {}".format(
                    len(attached_units), slice_path,
                ),
                command=rm_command,
            )
            return dry_result

        for attached in attached_units:
            detach_result = self.remove_property(
                unit=attached, key="Slice",
            )
            if detach_result.status == ResourceActionStatus.FAILED:
                detach_result.unit = slice_unit
                return detach_result

        if slice_file_exists:
            rm_run = self._runner.run(
                list(rm_command), elevated=self._config.elevated,
            )
            if rm_run.exit_code != 0:
                failed = self._wrap(
                    status=ResourceActionStatus.FAILED,
                    unit=slice_unit,
                    detail="failed to remove {}".format(slice_path),
                    command=rm_command,
                    exit_code=rm_run.exit_code,
                    stdout=rm_run.stdout,
                    stderr=rm_run.stderr,
                )
                return failed

        reload_failure = self._delegate_daemon_reload()
        if reload_failure is not None:
            reload_failure.unit = slice_unit
            return reload_failure

        success = self._wrap(
            status=ResourceActionStatus.SUCCESS,
            unit=slice_unit,
            detail="removed slice {} (detached {} unit(s))".format(
                slice_unit, len(attached_units),
            ),
            command=rm_command,
            exit_code=0,
        )
        return success

    # ------------------------------------------------------------------
    # Write verbs — transient scopes
    # ------------------------------------------------------------------

    def run_scoped(
        self,
        command: list[str],
        slice_: Optional[str] = None,
        properties: Optional[dict[str, str]] = None,
    ) -> ResourceActionResult:
        """
            Run a command under a transient ``systemd-run
            --scope`` with declared resource directives.

            :param command: Argv to execute under the scope.
            :param slice_: Optional slice name; defaults to
                           ``config.default_scope_slice``.
            :param properties: Optional ``dict[str, str]`` of
                               ``--property=KEY=VALUE``
                               directives.

            :returns: :class:`ResourceActionResult`.
        """
        if command is None or len(command) == 0:
            empty_result = self._wrap(
                status=ResourceActionStatus.FAILED,
                detail="run_scoped requires a non-empty command",
                command=None,
                stderr="empty command",
            )
            return empty_result

        effective_slice: str = slice_ if slice_ is not None else self._config.default_scope_slice
        scope_args: list[str] = [
            _SYSTEMD_RUN, "--scope", "--slice", effective_slice,
        ]
        if properties is not None:
            for key in properties:
                scope_args.append("--property={}={}".format(key, properties[key]))

        full_command = list(scope_args) + list(command)

        if self._config.dry_run:
            dry_result = self._wrap(
                status=ResourceActionStatus.DRY_RUN,
                detail="systemd-run --scope --slice {}".format(effective_slice),
                command=full_command,
            )
            return dry_result

        run_result = self._runner.run(
            list(full_command), elevated=self._config.elevated,
        )
        mapped_status = _map_status(exit_code=run_result.exit_code)
        rtnval = self._wrap(
            status=mapped_status,
            detail="systemd-run --scope --slice {}".format(effective_slice),
            command=full_command,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _dropin_path(self, *, unit: str) -> str:
        """
            :returns: ``<systemd_etc_dir>/<unit>.d/<dropin_basename>``.
        """
        rtnval = "{}/{}.d/{}".format(
            self._config.systemd_etc_dir.rstrip("/"),
            unit,
            self._config.dropin_basename,
        )
        return rtnval

    def _read_file(self, *, path: str) -> Optional[str]:
        """
            Read a file via ``cat``.  Reads do not elevate.

            :param path: Absolute path.

            :returns: File content on success; ``None`` when
                      ``cat`` exits non-zero.
        """
        command = [_CAT, path]
        run_result = self._runner.run(list(command), elevated=False)
        if run_result.exit_code != 0:
            rtnval: Optional[str] = None
            return rtnval
        rtnval = run_result.stdout
        return rtnval

    def _write_dropin(
        self,
        *,
        path: str,
        content: str,
    ) -> Optional[ResourceActionResult]:
        """
            Ensure the parent directory exists, then write
            ``content`` to ``path`` via ``tee``.

            :param path: Absolute target path.
            :param content: Body to write.

            :returns: ``None`` on success or a populated
                      ``ResourceActionResult`` describing the
                      failure.
        """
        parent = path.rsplit("/", 1)[0]
        mkdir_command = [_MKDIR, "-p", parent]
        mkdir_run = self._runner.run(
            list(mkdir_command), elevated=self._config.elevated,
        )
        if mkdir_run.exit_code != 0:
            failed = self._wrap(
                status=ResourceActionStatus.FAILED,
                detail="failed to create parent directory {}".format(parent),
                command=mkdir_command,
                exit_code=mkdir_run.exit_code,
                stdout=mkdir_run.stdout,
                stderr=mkdir_run.stderr,
            )
            return failed

        write_command = [_TEE, path]
        write_run = self._runner.run(
            list(write_command),
            elevated=self._config.elevated,
            stdin=content,
        )
        if write_run.exit_code != 0:
            failed = self._wrap(
                status=ResourceActionStatus.FAILED,
                detail="failed to write {}".format(path),
                command=write_command,
                exit_code=write_run.exit_code,
                stdout=write_run.stdout,
                stderr=write_run.stderr,
            )
            return failed

        rtnval: Optional[ResourceActionResult] = None
        return rtnval

    def _delegate_daemon_reload(self) -> Optional[ResourceActionResult]:
        """
            Invoke ``daemon_reload()`` on the injected service
            manager.

            :returns: ``None`` on success.  A populated
                      :class:`ResourceActionResult` describing the
                      failure otherwise.
        """
        sm_result = self._service_manager.daemon_reload()
        sm_status_value = _result_status_value(result=sm_result)
        if sm_status_value == "success":
            rtnval: Optional[ResourceActionResult] = None
            return rtnval
        failed = self._wrap(
            status=ResourceActionStatus.FAILED,
            detail="ServiceManager.daemon_reload() failed",
            command=getattr(sm_result, "command", None),
            exit_code=getattr(sm_result, "exit_code", None),
            stdout=getattr(sm_result, "stdout", None),
            stderr=getattr(sm_result, "stderr", None),
        )
        return failed

    def _delegate_enable(self, *, name: str) -> Optional[ResourceActionResult]:
        """
            Invoke ``enable(name)`` on the injected service
            manager.

            :param name: Unit name.

            :returns: ``None`` on success.  A populated
                      :class:`ResourceActionResult` describing the
                      failure otherwise.
        """
        sm_result = self._service_manager.enable(name)
        sm_status_value = _result_status_value(result=sm_result)
        if sm_status_value == "success":
            rtnval: Optional[ResourceActionResult] = None
            return rtnval
        failed = self._wrap(
            status=ResourceActionStatus.FAILED,
            detail="ServiceManager.enable({}) failed".format(name),
            command=getattr(sm_result, "command", None),
            exit_code=getattr(sm_result, "exit_code", None),
            stdout=getattr(sm_result, "stdout", None),
            stderr=getattr(sm_result, "stderr", None),
        )
        return failed

    def _find_attached_units(self, *, slice_name: str) -> list[str]:
        """
            Discover the set of units whose drop-in file declares
            ``Slice=<slice_name>.slice``.

            Strategy: list every directory under
            ``<systemd_etc_dir>`` with a trailing ``.d`` suffix,
            read the drop-in basename inside each, and look for
            an exact ``Slice=`` match.

            :param slice_name: Slice name (without
                               ``.slice``).

            :returns: List of unit names (e.g.
                      ``["foo.service"]``).  Empty when none.
        """
        target_value = "{}.slice".format(slice_name)
        rtnval: list[str] = []

        ls_command = ["ls", "-1", self._config.systemd_etc_dir]
        ls_run = self._runner.run(list(ls_command), elevated=False)
        if ls_run.exit_code != 0:
            return rtnval

        for raw in ls_run.stdout.splitlines():
            entry = raw.strip()
            if len(entry) == 0:
                continue
            if not entry.endswith(".d"):
                continue
            unit_name = entry[: -len(".d")]
            dropin_path = "{}/{}/{}".format(
                self._config.systemd_etc_dir.rstrip("/"),
                entry,
                self._config.dropin_basename,
            )
            content = self._read_file(path=dropin_path)
            if content is None:
                continue
            current_value = _value_for_key_in_dropin(
                content=content,
                section_name=_section_for_unit(unit=unit_name),
                key="Slice",
            )
            if current_value == target_value:
                rtnval.append(unit_name)
        return rtnval

    def _wrap(
        self,
        *,
        status: ResourceActionStatus,
        unit: Optional[str] = None,
        detail: Optional[str] = None,
        command: Optional[list[str]] = None,
        exit_code: Optional[int] = None,
        stdout: Optional[str] = None,
        stderr: Optional[str] = None,
        changed_keys: Optional[list[str]] = None,
        skipped_keys: Optional[list[str]] = None,
    ) -> ResourceActionResult:
        """
            Construct a :class:`ResourceActionResult`.

            :param status: Outcome status.
            :param unit: Targeted unit.
            :param detail: Human-readable description.
            :param command: Captured argv (copied).
            :param exit_code: Runner exit code.
            :param stdout: Captured stdout.
            :param stderr: Captured stderr.
            :param changed_keys: Keys actually modified.
            :param skipped_keys: Keys skipped (already in
                                 desired state).

            :returns: :class:`ResourceActionResult`.
        """
        if changed_keys is None:
            effective_changed: list[str] = []
        else:
            effective_changed = list(changed_keys)
        if skipped_keys is None:
            effective_skipped: list[str] = []
        else:
            effective_skipped = list(skipped_keys)
        if command is None:
            captured_command: Optional[list[str]] = None
        else:
            captured_command = list(command)

        rtnval = ResourceActionResult(
            status=status,
            detail=detail,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            command=captured_command,
            unit=unit,
            changed_keys=effective_changed,
            skipped_keys=effective_skipped,
        )
        return rtnval


# ----------------------------------------------------------------------
# Module-level helpers — parsing, rendering, drop-in manipulation
# ----------------------------------------------------------------------


def _map_status(*, exit_code: int) -> ResourceActionStatus:
    """
        Map a runner exit code to a :class:`ResourceActionStatus`.

        :param exit_code: Process exit code.

        :returns: ``SUCCESS`` when zero, ``FAILED`` otherwise.
    """
    if exit_code == 0:
        rtnval = ResourceActionStatus.SUCCESS
    else:
        rtnval = ResourceActionStatus.FAILED
    return rtnval


def _result_status_value(*, result: object) -> str:
    """
        Extract the canonical lowercase status value from a
        result-like object.

        Handles both :class:`ActionStatus` enum values (with
        ``.value``) and plain strings (so duck-typed mocks work).

        :param result: A result-like object with a ``status``
                       attribute.

        :returns: Lower-case status name; empty string when not
                  determinable.
    """
    raw_status = getattr(result, "status", None)
    if raw_status is None:
        rtnval = ""
        return rtnval
    if isinstance(raw_status, Enum):
        rtnval = str(raw_status.value)
        return rtnval
    rtnval = str(raw_status)
    return rtnval


def _values_match(*, key: str, requested: str, current: str) -> bool:
    """
        Decide whether the runner-reported ``current`` value of
        ``key`` already matches the caller's ``requested`` value.

        Performs key-aware comparisons:
            * ``CPUQuota`` requests are matched against the
              reported ``CPUQuotaPerSecUSec`` (or
              ``CPUQuota``) value via
              :func:`_cpu_quota_percent_matches`.
            * ``MemoryMax`` / ``MemoryHigh`` ``"infinity"`` is
              equivalent to an empty value.
            * Otherwise a plain string compare is used.

        :param key: Property key.
        :param requested: Caller-requested raw value.
        :param current: Runner-reported raw value.

        :returns: True when already in desired state.
    """
    requested_norm = requested.strip()
    current_norm = current.strip()

    if requested_norm == current_norm:
        rtnval = True
        return rtnval

    if key == "CPUQuota":
        rtnval = _cpu_quota_matches(
            requested=requested_norm, current=current_norm,
        )
        return rtnval

    if key in ("MemoryMax", "MemoryHigh", "TasksMax"):
        if (
            (requested_norm == "infinity" and current_norm == "")
            or (requested_norm == "" and current_norm == "infinity")
        ):
            rtnval = True
            return rtnval

    rtnval = False
    return rtnval


def _cpu_quota_matches(*, requested: str, current: str) -> bool:
    """
        Compare a requested ``CPUQuota=N%`` value against
        ``current`` which may be either a percent or systemd's
        microsecond representation.

        :param requested: Requested value (e.g. ``"50%"``).
        :param current: Reported value.

        :returns: True when equivalent.
    """
    requested_clean = requested.strip()
    current_clean = current.strip()

    if requested_clean == current_clean:
        rtnval = True
        return rtnval

    requested_pct = _parse_quota_percent(value=requested_clean)
    current_pct = _parse_quota_percent(value=current_clean)
    if requested_pct is None or current_pct is None:
        rtnval = False
        return rtnval
    rtnval = requested_pct == current_pct
    return rtnval


def _parse_quota_percent(*, value: str) -> Optional[int]:
    """
        Normalise a CPU-quota value to integer percent.

        Accepts either ``"<N>%"`` or systemd's
        ``"<usec>us"`` / ``"<usec>"`` form.

        :param value: Raw value.

        :returns: Percent integer, or ``None`` when unparseable
                  / ``infinity``.
    """
    candidate = value.strip()
    if len(candidate) == 0:
        rtnval: Optional[int] = None
        return rtnval
    if candidate == "infinity":
        rtnval = None
        return rtnval
    if candidate.endswith("%"):
        body = candidate[:-1]
        try:
            rtnval = int(body)
        except ValueError:
            rtnval = None
        return rtnval
    if candidate.endswith("us"):
        body = candidate[:-2]
    else:
        body = candidate
    try:
        usec = int(body)
    except ValueError:
        rtnval = None
        return rtnval
    rtnval = int(round(usec / 10000.0))
    return rtnval


def _parse_show_output(*, stdout: str) -> dict[str, str]:
    """
        Parse ``systemctl show`` ``key=value`` output into a
        ``dict[str, str]``.

        :param stdout: Raw stdout.

        :returns: Parsed dict.
    """
    rtnval: dict[str, str] = {}
    for raw in stdout.splitlines():
        line = raw.strip()
        if len(line) == 0:
            continue
        if line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        rtnval[key.strip()] = value.strip()
    return rtnval


def _parse_cgtop_output(*, stdout: str) -> list[CgroupUsage]:
    """
        Parse ``systemd-cgtop --raw`` output into
        :class:`CgroupUsage` records.

        Each non-empty, non-header line is whitespace-separated
        with columns: ``path tasks cpu_pct memory input_per_s
        output_per_s``.  Missing fields are reported as ``-`` and
        parsed to ``None``.

        :param stdout: Raw stdout.

        :returns: List of records.
    """
    rtnval: list[CgroupUsage] = []
    for raw in stdout.splitlines():
        line = raw.rstrip()
        if len(line.strip()) == 0:
            continue
        if line.lstrip().lower().startswith("control group"):
            continue
        tokens = line.split()
        if len(tokens) < 2:
            continue
        path = tokens[0]
        tasks = _parse_optional_int(token=tokens[1] if len(tokens) > 1 else "-")
        cpu_pct = _parse_optional_float(token=tokens[2] if len(tokens) > 2 else "-")
        mem_bytes = _parse_optional_int(token=tokens[3] if len(tokens) > 3 else "-")
        input_per_s = _parse_optional_int(token=tokens[4] if len(tokens) > 4 else "-")
        output_per_s = _parse_optional_int(token=tokens[5] if len(tokens) > 5 else "-")
        record = CgroupUsage(
            path=path,
            tasks=tasks,
            cpu_percent=cpu_pct,
            memory_bytes=mem_bytes,
            input_per_s=input_per_s,
            output_per_s=output_per_s,
        )
        rtnval.append(record)
    return rtnval


def _parse_optional_int(*, token: str) -> Optional[int]:
    """
        Parse a token to int; return None when missing
        (``"-"``) or unparseable.

        :param token: Raw token.

        :returns: Integer, or ``None``.
    """
    candidate = token.strip()
    if len(candidate) == 0:
        rtnval: Optional[int] = None
        return rtnval
    if candidate == "-":
        rtnval = None
        return rtnval
    try:
        rtnval = int(candidate)
    except ValueError:
        rtnval = None
    return rtnval


def _parse_optional_float(*, token: str) -> Optional[float]:
    """
        Parse a token to float; return None when missing or
        unparseable.

        :param token: Raw token.

        :returns: Float, or ``None``.
    """
    candidate = token.strip()
    if len(candidate) == 0:
        rtnval: Optional[float] = None
        return rtnval
    if candidate == "-":
        rtnval = None
        return rtnval
    try:
        rtnval = float(candidate)
    except ValueError:
        rtnval = None
    return rtnval


def _section_for_unit(*, unit: str) -> str:
    """
        Return the systemd ``[Section]`` body name appropriate
        for ``unit``.

        Slices use ``[Slice]``; every other unit type uses
        ``[Service]``.

        :param unit: Unit name (e.g. ``"nginx.service"`` or
                     ``"workers.slice"``).

        :returns: Section name without brackets.
    """
    if unit.endswith(".slice"):
        rtnval = "Slice"
        return rtnval
    rtnval = "Service"
    return rtnval


def _render_slice_unit_body(*, spec: SliceSpec) -> str:
    """
        Render the on-disk body for a slice unit file.

        :param spec: The :class:`SliceSpec`.

        :returns: Full file body terminated by a newline.
    """
    lines: list[str] = []
    lines.append("[Unit]")
    if spec.description is not None:
        lines.append("Description={}".format(spec.description))
    else:
        lines.append("Description=automojo-managed slice: {}".format(spec.name))
    for entry in spec.before:
        lines.append("Before={}".format(entry))
    for entry in spec.wants:
        lines.append("Wants={}".format(entry))
    lines.append("")
    lines.append("[Slice]")
    for key in spec.properties:
        lines.append("{}={}".format(key, spec.properties[key]))
    body = "\n".join(lines) + "\n"
    return body


def _split_dropin_sections(
    *,
    content: str,
) -> list[tuple]:
    """
        Split ``content`` into ``(section_name_or_None,
        body_lines_list)`` tuples preserving order.

        Lines that appear before the first ``[Section]`` header
        live in the leading entry whose section name is
        ``None``.

        :param content: Raw drop-in body.

        :returns: Ordered list of section tuples.
    """
    sections: list[tuple] = [(None, [])]
    for raw in content.splitlines():
        line = raw.rstrip("\n")
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]") and len(stripped) >= 2:
            section_name = stripped[1:-1]
            sections.append((section_name, []))
            continue
        sections[-1][1].append(line)
    return sections


def _render_dropin_sections(*, sections: list[tuple]) -> str:
    """
        Reverse of :func:`_split_dropin_sections`.

        :param sections: ``(section_name_or_None, body_lines)``
                         tuples.

        :returns: Re-rendered drop-in body.
    """
    output_lines: list[str] = []
    for section_name, body_lines in sections:
        if section_name is not None:
            output_lines.append("[{}]".format(section_name))
        for line in body_lines:
            output_lines.append(line)
    rtnval = "\n".join(output_lines)
    if not rtnval.endswith("\n"):
        rtnval = rtnval + "\n"
    return rtnval


def _merge_dropin_properties(
    *,
    existing_content: str,
    section_name: str,
    properties: dict[str, str],
) -> tuple:
    """
        Upsert ``(key, value)`` pairs into ``[section_name]``
        while preserving every other section, comment, and blank
        line.

        :param existing_content: Current drop-in content
                                 (may be empty).
        :param section_name: Target section name (e.g.
                             ``"Service"``).
        :param properties: Directives to upsert.

        :returns: Tuple ``(merged_content, changed_keys,
                  skipped_keys)``.
    """
    sections = _split_dropin_sections(content=existing_content)

    target_index: Optional[int] = None
    for index in range(len(sections)):
        name, _ = sections[index]
        if name == section_name:
            target_index = index
            break

    if target_index is None:
        sections.append((section_name, []))
        target_index = len(sections) - 1

    body_lines = sections[target_index][1]

    changed_keys: list[str] = []
    skipped_keys: list[str] = []

    for key in properties:
        requested_value = properties[key]
        replaced = False
        for line_index in range(len(body_lines)):
            existing_line = body_lines[line_index]
            stripped = existing_line.strip()
            if "=" not in stripped:
                continue
            existing_key, _, existing_value = stripped.partition("=")
            existing_key_clean = existing_key.strip()
            if existing_key_clean != key:
                continue
            if existing_value.strip() == requested_value:
                skipped_keys.append(key)
            else:
                body_lines[line_index] = "{}={}".format(key, requested_value)
                changed_keys.append(key)
            replaced = True
            break
        if not replaced:
            body_lines.append("{}={}".format(key, requested_value))
            changed_keys.append(key)

    sections[target_index] = (section_name, body_lines)
    merged_content = _render_dropin_sections(sections=sections)
    rtnval = (merged_content, changed_keys, skipped_keys)
    return rtnval


def _strip_dropin_property(
    *,
    existing_content: str,
    section_name: str,
    key: str,
) -> tuple:
    """
        Remove the ``key=...`` line from ``[section_name]`` if
        present.

        :param existing_content: Current drop-in body.
        :param section_name: Target section.
        :param key: Directive key to strip.

        :returns: Tuple ``(new_content, removed)`` where
                  ``removed`` is True iff a matching line was
                  found and stripped.
    """
    sections = _split_dropin_sections(content=existing_content)
    removed = False

    for index in range(len(sections)):
        name, body_lines = sections[index]
        if name != section_name:
            continue
        kept_lines: list[str] = []
        for existing_line in body_lines:
            stripped = existing_line.strip()
            if "=" in stripped:
                existing_key, _, _ = stripped.partition("=")
                if existing_key.strip() == key:
                    removed = True
                    continue
            kept_lines.append(existing_line)
        sections[index] = (name, kept_lines)
        if removed:
            break

    new_content = _render_dropin_sections(sections=sections)
    rtnval = (new_content, removed)
    return rtnval


def _value_for_key_in_dropin(
    *,
    content: str,
    section_name: str,
    key: str,
) -> Optional[str]:
    """
        Look up the value for ``key`` inside ``[section_name]``
        of ``content``.

        :param content: Drop-in body.
        :param section_name: Target section.
        :param key: Directive key.

        :returns: Stripped value, or ``None`` when not found.
    """
    sections = _split_dropin_sections(content=content)
    for name, body_lines in sections:
        if name != section_name:
            continue
        for raw in body_lines:
            stripped = raw.strip()
            if "=" not in stripped:
                continue
            existing_key, _, existing_value = stripped.partition("=")
            if existing_key.strip() == key:
                rtnval: Optional[str] = existing_value.strip()
                return rtnval
    rtnval = None
    return rtnval


def _dropin_is_effectively_empty(*, content: str) -> bool:
    """
        Decide whether ``content`` carries any directive after
        normalising away comments, blank lines, and section
        headers.

        :param content: Drop-in body.

        :returns: True when no actionable directive remains.
    """
    for raw in content.splitlines():
        stripped = raw.strip()
        if len(stripped) == 0:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            continue
        rtnval = False
        return rtnval
    rtnval = True
    return rtnval


def _augment_unit_keys(
    *,
    result: ResourceActionResult,
    unit: str,
    changed_keys: list[str],
    skipped_keys: list[str],
) -> ResourceActionResult:
    """
        Stamp ``unit`` / ``changed_keys`` / ``skipped_keys``
        onto an in-flight failure result without losing its
        existing diagnostic fields.

        :param result: The failure result.
        :param unit: Unit name.
        :param changed_keys: Keys whose mutation was attempted.
        :param skipped_keys: Keys whose state already matched.

        :returns: The same ``result`` instance for caller
                  ergonomics.
    """
    result.unit = unit
    result.changed_keys = list(changed_keys)
    result.skipped_keys = list(skipped_keys)
    return result
