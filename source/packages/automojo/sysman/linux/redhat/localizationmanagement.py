# automojo.sysman.linux.redhat.localizationmanagement
#
# LocalizationManager wraps localectl and timedatectl through the
# ICommandRunner abstraction.

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.runners.base import ICommandRunner


class LocalizationActionStatus(Enum):
    """
        Outcome status for localization actions.
    """
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class LocalizationManagerConfig:
    """
        Configuration for :class:`LocalizationManager`.

        :param dry_run: When True, no commands execute.
        :param elevated: When True, mutating commands run elevated.
    """
    dry_run: bool = False
    elevated: bool = False


@dataclass
class LocalizationActionResult:
    """
        Outcome of a localization management action.
    """
    status: LocalizationActionStatus
    detail: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


@dataclass
class LocaleInfo:
    """
        Summary of the system locale settings.
    """
    lang: Optional[str] = None
    language: Optional[str] = None
    lc_all: Optional[str] = None


@dataclass
class KeymapInfo:
    """
        Summary of the system keymap settings.
    """
    vc_keymap: Optional[str] = None
    x11_layout: Optional[str] = None


@dataclass
class TimezoneInfo:
    """
        Summary of the system timezone settings.
    """
    timezone: str
    ntp_enabled: bool
    local_rtc: bool


class LocalizationManager:
    """
        Facade for localectl and timedatectl operations.
    """

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[LocalizationManagerConfig] = None,
    ) -> None:
        """
            :param runner: Command runner.
            :param config: Manager configuration.
        """
        if config is None:
            config = LocalizationManagerConfig()
        self._runner: ICommandRunner = runner
        self._config: LocalizationManagerConfig = config
        return

    @property
    def config(self) -> LocalizationManagerConfig:
        """
            The active :class:`LocalizationManagerConfig`.
        """
        return self._config

    def query_locale(self) -> LocaleInfo:
        """
            Read the current locale via ``localectl status``.
        """
        run_result = self._runner.run(["localectl", "status"], elevated=False)
        info = LocaleInfo()
        for raw in run_result.stdout.splitlines():
            line = raw.strip()
            if line.startswith("System Locale:"):
                body = line[len("System Locale:"):].strip()
                fields = [b for b in body.split() if "=" in b]
                for field_spec in fields:
                    key, _, value = field_spec.partition("=")
                    if key == "LANG":
                        info.lang = value
                    elif key == "LANGUAGE":
                        info.language = value
                    elif key == "LC_ALL":
                        info.lc_all = value
        return info

    def set_locale(
        self,
        *,
        lang: str,
    ) -> LocalizationActionResult:
        """
            Set the ``LANG`` value via ``localectl set-locale``.
        """
        current = self.query_locale()
        if current.lang == lang:
            return LocalizationActionResult(
                status=LocalizationActionStatus.SKIPPED, detail=lang,
            )
        command = ["localectl", "set-locale", "LANG={}".format(lang)]
        return self._dispatch(command=command, detail=lang)

    def query_keymap(self) -> KeymapInfo:
        """
            Query the current keymap via ``localectl status``.
        """
        run_result = self._runner.run(["localectl", "status"], elevated=False)
        info = KeymapInfo()
        for raw in run_result.stdout.splitlines():
            line = raw.strip()
            if line.startswith("VC Keymap:"):
                info.vc_keymap = line[len("VC Keymap:"):].strip()
            elif line.startswith("X11 Layout:"):
                info.x11_layout = line[len("X11 Layout:"):].strip()
        return info

    def set_keymap(self, keymap: str) -> LocalizationActionResult:
        """
            Set the VC keymap via ``localectl set-keymap``.
        """
        current = self.query_keymap()
        if current.vc_keymap == keymap:
            return LocalizationActionResult(
                status=LocalizationActionStatus.SKIPPED, detail=keymap,
            )
        command = ["localectl", "set-keymap", keymap]
        return self._dispatch(command=command, detail=keymap)

    def query_timezone(self) -> TimezoneInfo:
        """
            Query timezone state via ``timedatectl show``.
        """
        run_result = self._runner.run(
            ["timedatectl", "show"], elevated=False,
        )
        info = TimezoneInfo(timezone="", ntp_enabled=False, local_rtc=False)
        for raw in run_result.stdout.splitlines():
            line = raw.strip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key == "Timezone":
                info.timezone = value
            elif key == "NTP":
                info.ntp_enabled = value.lower() == "yes"
            elif key == "LocalRTC":
                info.local_rtc = value.lower() == "yes"
        return info

    def set_timezone(self, timezone: str) -> LocalizationActionResult:
        """
            Set the system timezone via ``timedatectl set-timezone``.
        """
        current = self.query_timezone()
        if current.timezone == timezone:
            return LocalizationActionResult(
                status=LocalizationActionStatus.SKIPPED, detail=timezone,
            )
        command = ["timedatectl", "set-timezone", timezone]
        return self._dispatch(command=command, detail=timezone)

    def set_ntp(self, enabled: bool) -> LocalizationActionResult:
        """
            Toggle NTP synchronisation via ``timedatectl set-ntp``.
        """
        current = self.query_timezone()
        if current.ntp_enabled == enabled:
            return LocalizationActionResult(
                status=LocalizationActionStatus.SKIPPED,
                detail="true" if enabled else "false",
            )
        command = ["timedatectl", "set-ntp", "true" if enabled else "false"]
        return self._dispatch(
            command=command, detail="true" if enabled else "false",
        )

    def _dispatch(
        self,
        *,
        command: list[str],
        detail: str,
    ) -> LocalizationActionResult:
        """
            Dispatch a localization command honouring dry-run.
        """
        if self._config.dry_run:
            return LocalizationActionResult(
                status=LocalizationActionStatus.DRY_RUN,
                detail=detail, command=command,
            )
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = LocalizationActionStatus.SUCCESS if run_result.exit_code == 0 else LocalizationActionStatus.FAILED
        return LocalizationActionResult(
            status=status, detail=detail,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
