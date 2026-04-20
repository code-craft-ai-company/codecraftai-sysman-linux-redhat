# automojo.sysman.linux.redhat.scheduledtasks
#
# ScheduledTaskManager manages cron drop-in files and systemd timers
# through the ICommandRunner abstraction.

import os
import re

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.featurebase import (
    BaseFeatureManager,
    FeatureConfig,
)


class TaskType(Enum):
    """
        Categories of scheduled tasks managed by this feature.
    """
    CRON = "cron"
    SYSTEMD = "systemd"


@dataclass
class ScheduledTaskConfig(FeatureConfig):
    """
        Configuration for :class:`ScheduledTaskManager`.  Inherits
        ``dry_run`` and ``elevated`` from :class:`FeatureConfig`.

        :param cron_dir: Directory for cron drop-in files.
        :param unit_dir: Directory for systemd unit files.
    """
    cron_dir: str = "/etc/cron.d"
    unit_dir: str = "/etc/systemd/system"


@dataclass
class CronJobEntry:
    """
        Specification for a cron drop-in file.
    """
    name: str
    schedule: str
    command: str
    user: str
    comment: Optional[str] = None


@dataclass
class SystemdTimerEntry:
    """
        Specification for a systemd timer + service pair.
    """
    name: str
    description: str
    on_calendar: str
    exec_start: str
    enabled: bool = True
    after: Optional[str] = None
    wants_by: Optional[str] = None


@dataclass
class ScheduledTaskInfo:
    """
        Summary of a scheduled task's current state.
    """
    name: str
    task_type: TaskType
    schedule: Optional[str] = None
    command: Optional[str] = None
    user: Optional[str] = None
    enabled: Optional[bool] = None
    active: Optional[bool] = None
    readable: bool = True


@dataclass
class ScheduledTaskResult:
    """
        Outcome of a scheduled task mutation.  NOTE: this feature
        uses a non-standard result shape (``success`` / ``name`` /
        ``dry_run`` / ``error``) for historical compatibility; it
        does NOT subclass :class:`FeatureActionResult`.  See
        `specs/3-scheduled-tasks/tasks.md` for a future-normalisation
        task.
    """
    success: bool
    name: str
    dry_run: bool = False
    error: Optional[str] = None


class ScheduledTaskQueryError(Exception):
    """
        Raised when a query operation fails for reasons other than missing task.
    """


_VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-_]*$")


class ScheduledTaskManager(BaseFeatureManager):
    """
        Facade for cron and systemd timer management.

        This feature uses its own :class:`ScheduledTaskResult` shape
        and therefore does not go through the base class's
        ``_dispatch`` / ``_wrap_result`` helpers.  It still inherits
        the standard ``__init__`` / ``config`` / ``runner`` surface
        and can be passed a :class:`MaintenanceReport` for symmetry,
        although auto-reporting from :meth:`_emit_record` is not
        wired in from this manager's methods (future work).
    """

    feature_name = "scheduled-tasks"

    def add_cron_job(self, entry: CronJobEntry) -> ScheduledTaskResult:
        """
            Write a cron drop-in file for ``entry``.
        """
        _ensure_valid_name(entry.name)
        path = os.path.join(self._config.cron_dir, entry.name)
        desired = _render_cron(entry=entry)
        current = self._read(path=path)
        if current == desired:
            return ScheduledTaskResult(success=True, name=entry.name)
        if self._config.dry_run:
            return ScheduledTaskResult(
                success=True, name=entry.name, dry_run=True,
            )
        run_result = self._runner.run(
            ["tee", path], elevated=self._config.elevated, stdin=desired,
        )
        if run_result.exit_code != 0:
            return ScheduledTaskResult(
                success=False, name=entry.name, error=run_result.stderr,
            )
        return ScheduledTaskResult(success=True, name=entry.name)

    def remove_cron_job(self, name: str) -> ScheduledTaskResult:
        """
            Delete a cron drop-in file.
        """
        _ensure_valid_name(name)
        path = os.path.join(self._config.cron_dir, name)
        current = self._read(path=path)
        if len(current) == 0:
            return ScheduledTaskResult(success=True, name=name)
        if self._config.dry_run:
            return ScheduledTaskResult(success=True, name=name, dry_run=True)
        run_result = self._runner.run(
            ["rm", "-f", path], elevated=self._config.elevated,
        )
        if run_result.exit_code != 0:
            return ScheduledTaskResult(
                success=False, name=name, error=run_result.stderr,
            )
        return ScheduledTaskResult(success=True, name=name)

    def add_systemd_timer(
        self,
        entry: SystemdTimerEntry,
    ) -> ScheduledTaskResult:
        """
            Write a systemd timer + service pair and optionally enable it.
        """
        _ensure_valid_name(entry.name)
        service_path = os.path.join(
            self._config.unit_dir, "{}.service".format(entry.name),
        )
        timer_path = os.path.join(
            self._config.unit_dir, "{}.timer".format(entry.name),
        )
        service_body, timer_body = _render_systemd_unit(entry=entry)
        service_current = self._read(path=service_path)
        timer_current = self._read(path=timer_path)

        needs_write = (
            service_current != service_body or timer_current != timer_body
        )

        if not needs_write and not entry.enabled:
            return ScheduledTaskResult(success=True, name=entry.name)

        if self._config.dry_run:
            return ScheduledTaskResult(
                success=True, name=entry.name, dry_run=True,
            )

        if needs_write:
            service_result = self._runner.run(
                ["tee", service_path],
                elevated=self._config.elevated,
                stdin=service_body,
            )
            if service_result.exit_code != 0:
                return ScheduledTaskResult(
                    success=False, name=entry.name,
                    error=service_result.stderr,
                )
            timer_result = self._runner.run(
                ["tee", timer_path],
                elevated=self._config.elevated,
                stdin=timer_body,
            )
            if timer_result.exit_code != 0:
                return ScheduledTaskResult(
                    success=False, name=entry.name,
                    error=timer_result.stderr,
                )
            self._runner.run(
                ["systemctl", "daemon-reload"],
                elevated=self._config.elevated,
            )

        if entry.enabled:
            enable_result = self._runner.run(
                ["systemctl", "enable", "--now", "{}.timer".format(entry.name)],
                elevated=self._config.elevated,
            )
            if enable_result.exit_code != 0:
                return ScheduledTaskResult(
                    success=False, name=entry.name,
                    error=enable_result.stderr,
                )

        return ScheduledTaskResult(success=True, name=entry.name)

    def remove_systemd_timer(self, name: str) -> ScheduledTaskResult:
        """
            Delete a systemd timer + service pair.
        """
        _ensure_valid_name(name)
        timer_unit = "{}.timer".format(name)
        service_unit = "{}.service".format(name)
        timer_path = os.path.join(self._config.unit_dir, timer_unit)
        service_path = os.path.join(self._config.unit_dir, service_unit)
        if self._config.dry_run:
            return ScheduledTaskResult(success=True, name=name, dry_run=True)
        self._runner.run(
            ["systemctl", "disable", "--now", timer_unit],
            elevated=self._config.elevated,
        )
        self._runner.run(
            ["rm", "-f", timer_path, service_path],
            elevated=self._config.elevated,
        )
        self._runner.run(
            ["systemctl", "daemon-reload"],
            elevated=self._config.elevated,
        )
        return ScheduledTaskResult(success=True, name=name)

    def query_cron(self, name: str) -> Optional[ScheduledTaskInfo]:
        """
            Return :class:`ScheduledTaskInfo` for a cron drop-in or None.
        """
        _ensure_valid_name(name)
        path = os.path.join(self._config.cron_dir, name)
        content = self._read(path=path)
        if len(content) == 0:
            return None
        parsed = _parse_cron_content(content=content)
        info = ScheduledTaskInfo(
            name=name,
            task_type=TaskType.CRON,
            schedule=parsed.get("schedule"),
            command=parsed.get("command"),
            user=parsed.get("user"),
            enabled=True,
        )
        return info

    def query_systemd(self, name: str) -> Optional[ScheduledTaskInfo]:
        """
            Return :class:`ScheduledTaskInfo` for a systemd timer or None.
        """
        _ensure_valid_name(name)
        timer_unit = "{}.timer".format(name)
        path = os.path.join(
            self._config.unit_dir, "{}.service".format(name),
        )
        service_content = self._read(path=path)
        if len(service_content) == 0:
            return None
        timer_path = os.path.join(self._config.unit_dir, timer_unit)
        timer_content = self._read(path=timer_path)
        parsed_service = _parse_ini_section(
            content=service_content, section="Service",
        )
        parsed_timer = _parse_ini_section(
            content=timer_content, section="Timer",
        )
        info = ScheduledTaskInfo(
            name=name,
            task_type=TaskType.SYSTEMD,
            schedule=parsed_timer.get("OnCalendar"),
            command=parsed_service.get("ExecStart"),
        )
        show_result = self._runner.run(
            ["systemctl", "show", "--no-page", timer_unit,
             "--property=ActiveState,UnitFileState"],
            elevated=False,
        )
        if show_result.exit_code == 0:
            status_map = _parse_show_output(stdout=show_result.stdout)
            info.active = status_map.get("ActiveState") == "active"
            info.enabled = status_map.get("UnitFileState") == "enabled"
        return info

    def _read(self, *, path: str) -> str:
        """
            Read a file via ``cat``, returning empty on missing.
        """
        run_result = self._runner.run(["cat", path], elevated=False)
        if run_result.exit_code != 0:
            return ""
        return run_result.stdout


def _ensure_valid_name(name: str) -> None:
    """
        Raise ValueError when ``name`` contains disallowed characters.
    """
    if _VALID_NAME.match(name) is None:
        raise ValueError("invalid task name: {}".format(name))
    return


def _render_cron(*, entry: CronJobEntry) -> str:
    """
        Render a :class:`CronJobEntry` into a drop-in body.
    """
    lines: list[str] = []
    if entry.comment is not None:
        lines.append("# {}".format(entry.comment))
    lines.append("SHELL=/bin/bash")
    lines.append("PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin")
    lines.append("{} {} {}".format(
        entry.schedule, entry.user, entry.command,
    ))
    body = "\n".join(lines) + "\n"
    return body


def _render_systemd_unit(
    *,
    entry: SystemdTimerEntry,
) -> tuple[str, str]:
    """
        Render a :class:`SystemdTimerEntry` into service+timer bodies.
    """
    wants_by = entry.wants_by if entry.wants_by is not None else "timers.target"

    service_lines: list[str] = [
        "[Unit]",
        "Description={}".format(entry.description),
    ]
    if entry.after is not None:
        service_lines.append("After={}".format(entry.after))
    service_lines.append("")
    service_lines.append("[Service]")
    service_lines.append("Type=oneshot")
    service_lines.append("ExecStart={}".format(entry.exec_start))
    service_body = "\n".join(service_lines) + "\n"

    timer_lines: list[str] = [
        "[Unit]",
        "Description={} timer".format(entry.description),
        "",
        "[Timer]",
        "OnCalendar={}".format(entry.on_calendar),
        "Persistent=true",
        "Unit={}.service".format(entry.name),
        "",
        "[Install]",
        "WantedBy={}".format(wants_by),
    ]
    timer_body = "\n".join(timer_lines) + "\n"

    return service_body, timer_body


def _parse_cron_content(*, content: str) -> dict:
    """
        Parse a cron drop-in body into its schedule/user/command components.
    """
    for raw in content.splitlines():
        line = raw.strip()
        if len(line) == 0 or line.startswith("#"):
            continue
        if line.startswith("SHELL=") or line.startswith("PATH="):
            continue
        tokens = line.split(None, 6)
        if len(tokens) < 7:
            if line.startswith("@"):
                at_tokens = line.split(None, 2)
                if len(at_tokens) >= 3:
                    return {
                        "schedule": at_tokens[0],
                        "user": at_tokens[1],
                        "command": at_tokens[2],
                    }
            continue
        schedule = " ".join(tokens[0:5])
        user = tokens[5]
        command = tokens[6]
        return {
            "schedule": schedule,
            "user": user,
            "command": command,
        }
    return {}


def _parse_ini_section(*, content: str, section: str) -> dict:
    """
        Parse ``[<section>]`` key/value pairs from an INI-style body.
    """
    entries: dict[str, str] = {}
    current_section: Optional[str] = None
    for raw in content.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1]
            continue
        if current_section != section:
            continue
        if "=" not in line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        entries[key.strip()] = value.strip()
    return entries


def _parse_show_output(*, stdout: str) -> dict:
    """
        Parse ``systemctl show`` key=value output into a dict.
    """
    parsed: dict[str, str] = {}
    for raw in stdout.splitlines():
        line = raw.strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        parsed[key.strip()] = value.strip()
    return parsed
