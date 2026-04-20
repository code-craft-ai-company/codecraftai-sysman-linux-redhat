# automojo.sysman.linux.redhat.kernelparameters
#
# KernelParamManager and KernelModuleManager manage sysctl runtime and
# persisted values, plus kernel module load/unload through the
# ICommandRunner abstraction.

from dataclasses import dataclass
from typing import Optional

from automojo.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureActionResult,
    FeatureConfig,
)


# Backwards-compatible aliases.
KernelParamActionStatus = ActionStatus
KernelModuleActionStatus = ActionStatus


@dataclass
class KernelManagerConfig(FeatureConfig):
    """
        Configuration shared by :class:`KernelParamManager` and
        :class:`KernelModuleManager`.  Inherits ``dry_run`` and
        ``elevated`` from :class:`FeatureConfig`.

        :param sysctl_dir: Drop-in directory for sysctl persistence.
        :param modules_load_dir: Drop-in directory for module persistence.
    """
    sysctl_dir: str = "/etc/sysctl.d"
    modules_load_dir: str = "/etc/modules-load.d"


@dataclass
class KernelParamInfo:
    """
        A sysctl key/value pair.
    """
    key: str
    value: str


@dataclass
class KernelParamActionResult(FeatureActionResult):
    """
        Outcome of a kernel-parameter management action.  Extends
        :class:`FeatureActionResult` with sysctl-specific fields.
    """
    key: str = ""
    current_value: Optional[str] = None
    applied_value: Optional[str] = None
    filename: Optional[str] = None
    diff: Optional[str] = None


@dataclass
class KernelModuleInfo:
    """
        A loaded kernel module description.
    """
    name: str
    size_bytes: int
    use_count: int


@dataclass
class KernelModuleActionResult(FeatureActionResult):
    """
        Outcome of a kernel-module management action.  Extends
        :class:`FeatureActionResult` with module-specific fields.
    """
    module_name: str = ""
    filename: Optional[str] = None


class KernelParamManager(BaseFeatureManager):
    """
        Facade for sysctl parameter management.
    """

    result_class = KernelParamActionResult
    feature_name = "kernel-parameters"

    def read(self, key: str) -> Optional[KernelParamInfo]:
        """
            Return the current runtime value of ``key`` via ``sysctl -n``.
        """
        run_result = self._runner.run(["sysctl", "-n", key], elevated=False)
        if run_result.exit_code != 0:
            return None
        value = run_result.stdout.strip()
        info = KernelParamInfo(key=key, value=value)
        return info

    def write(self, key: str, value: str) -> KernelParamActionResult:
        """
            Set ``key=value`` at runtime via ``sysctl -w``.
        """
        current = self.read(key)
        current_value = current.value if current is not None else None
        if current_value == value:
            skipped = KernelParamActionResult(
                status=KernelParamActionStatus.SKIPPED,
                key=key,
                current_value=current_value,
                applied_value=value,
            )
            return skipped
        command = ["sysctl", "-w", "{}={}".format(key, value)]
        return self._dispatch(
            command=command,
            detail=key,
            operation="write",
            key=key,
            current_value=current_value,
            applied_value=value,
        )

    def persist(
        self,
        key: str,
        value: str,
        filename: str,
    ) -> KernelParamActionResult:
        """
            Write ``key=value`` to a drop-in file under ``sysctl_dir``.
        """
        path = "{}/{}".format(self._config.sysctl_dir, filename)
        current_content = self._read_file(path=path)
        new_content = _set_sysctl_line(
            content=current_content, key=key, value=value,
        )
        if new_content == current_content:
            return KernelParamActionResult(
                status=KernelParamActionStatus.SKIPPED,
                key=key, applied_value=value, filename=filename,
            )
        return self._dispatch(
            command=["tee", path],
            detail=key,
            stdin=new_content,
            operation="persist",
            key=key,
            applied_value=value,
            filename=filename,
        )

    def remove_persisted(
        self,
        key: str,
        filename: str,
    ) -> KernelParamActionResult:
        """
            Remove ``key`` from a drop-in file.
        """
        path = "{}/{}".format(self._config.sysctl_dir, filename)
        current_content = self._read_file(path=path)
        if len(current_content) == 0:
            return KernelParamActionResult(
                status=KernelParamActionStatus.SKIPPED,
                key=key, filename=filename,
            )
        new_content = _remove_sysctl_line(content=current_content, key=key)
        if new_content == current_content:
            return KernelParamActionResult(
                status=KernelParamActionStatus.SKIPPED,
                key=key, filename=filename,
            )
        return self._dispatch(
            command=["tee", path],
            detail=key,
            stdin=new_content,
            operation="remove_persisted",
            key=key,
            filename=filename,
        )

    def apply_all(self) -> KernelParamActionResult:
        """
            Apply all persisted sysctl settings via ``sysctl --system``.
        """
        command = ["sysctl", "--system"]
        return self._dispatch(
            command=command,
            detail="<all>",
            operation="apply_all",
            key="<all>",
        )

    def query_all(self) -> list[KernelParamInfo]:
        """
            Return all current sysctl key/value pairs.
        """
        run_result = self._runner.run(["sysctl", "-a"], elevated=False)
        if run_result.exit_code != 0:
            return []
        params = _parse_sysctl_dump(stdout=run_result.stdout)
        return params

    def _read_file(self, *, path: str) -> str:
        """
            Read a drop-in file via ``cat``, returning empty on missing.
        """
        run_result = self._runner.run(["cat", path], elevated=False)
        if run_result.exit_code != 0:
            return ""
        return run_result.stdout


class KernelModuleManager(BaseFeatureManager):
    """
        Facade for kernel module management.
    """

    result_class = KernelModuleActionResult
    feature_name = "kernel-parameters"       # same spec folder as KernelParamManager

    def load(
        self,
        name: str,
        params: Optional[list[str]] = None,
    ) -> KernelModuleActionResult:
        """
            Load a kernel module via ``modprobe``.
        """
        if self._is_loaded(name=name):
            return KernelModuleActionResult(
                status=KernelModuleActionStatus.SKIPPED,
                module_name=name,
            )
        command = ["modprobe", name]
        if params is not None:
            command.extend(params)
        return self._dispatch(
            command=command,
            detail=name,
            operation="load",
            module_name=name,
        )

    def unload(self, name: str) -> KernelModuleActionResult:
        """
            Unload a kernel module via ``modprobe -r``.
        """
        if not self._is_loaded(name=name):
            return KernelModuleActionResult(
                status=KernelModuleActionStatus.SKIPPED,
                module_name=name,
            )
        command = ["modprobe", "-r", name]
        return self._dispatch(
            command=command,
            detail=name,
            operation="unload",
            module_name=name,
        )

    def query_loaded(self) -> list[KernelModuleInfo]:
        """
            List loaded modules via ``lsmod``.
        """
        run_result = self._runner.run(["lsmod"], elevated=False)
        if run_result.exit_code != 0:
            return []
        return _parse_lsmod_output(stdout=run_result.stdout)

    def persist_load(
        self,
        name: str,
        filename: str,
    ) -> KernelModuleActionResult:
        """
            Add ``name`` to ``<modules_load_dir>/<filename>`` for boot loading.
        """
        path = "{}/{}".format(self._config.modules_load_dir, filename)
        current = self._read_file(path=path)
        existing_modules = _parse_modules_load_file(content=current)
        if name in existing_modules:
            return KernelModuleActionResult(
                status=KernelModuleActionStatus.SKIPPED,
                module_name=name, filename=filename,
            )
        new_modules = list(existing_modules)
        new_modules.append(name)
        new_content = "\n".join(new_modules) + "\n"
        return self._dispatch(
            command=["tee", path],
            detail=name,
            stdin=new_content,
            operation="persist_load",
            module_name=name,
            filename=filename,
        )

    def remove_persisted_load(
        self,
        name: str,
        filename: str,
    ) -> KernelModuleActionResult:
        """
            Remove ``name`` from ``<modules_load_dir>/<filename>``.
        """
        path = "{}/{}".format(self._config.modules_load_dir, filename)
        current = self._read_file(path=path)
        existing_modules = _parse_modules_load_file(content=current)
        if name not in existing_modules:
            return KernelModuleActionResult(
                status=KernelModuleActionStatus.SKIPPED,
                module_name=name, filename=filename,
            )
        new_modules = [m for m in existing_modules if m != name]
        new_content = ""
        if len(new_modules) > 0:
            new_content = "\n".join(new_modules) + "\n"
        return self._dispatch(
            command=["tee", path],
            detail=name,
            stdin=new_content,
            operation="remove_persisted_load",
            module_name=name,
            filename=filename,
        )

    def _is_loaded(self, *, name: str) -> bool:
        """
            Return True when ``name`` appears in ``lsmod`` output.
        """
        modules = self.query_loaded()
        for module in modules:
            if module.name == name:
                return True
        return False

    def _read_file(self, *, path: str) -> str:
        """
            Read a drop-in file via ``cat``.
        """
        run_result = self._runner.run(["cat", path], elevated=False)
        if run_result.exit_code != 0:
            return ""
        return run_result.stdout


def _set_sysctl_line(*, content: str, key: str, value: str) -> str:
    """
        Ensure ``key = value`` appears in the drop-in content.
    """
    new_line = "{} = {}".format(key, value)
    lines = content.splitlines()
    updated: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or len(stripped) == 0:
            updated.append(line)
            continue
        equals = stripped.split("=", 1)
        existing_key = equals[0].strip()
        if existing_key == key:
            updated.append(new_line)
            replaced = True
            continue
        updated.append(line)
    if not replaced:
        updated.append(new_line)
    body = "\n".join(updated)
    if not body.endswith("\n"):
        body = body + "\n"
    return body


def _remove_sysctl_line(*, content: str, key: str) -> str:
    """
        Return the drop-in content with any matching key removed.
    """
    lines = content.splitlines()
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or len(stripped) == 0:
            kept.append(line)
            continue
        equals = stripped.split("=", 1)
        existing_key = equals[0].strip()
        if existing_key == key:
            continue
        kept.append(line)
    body = "\n".join(kept)
    if len(body) > 0 and not body.endswith("\n"):
        body = body + "\n"
    return body


def _parse_sysctl_dump(*, stdout: str) -> list[KernelParamInfo]:
    """
        Parse ``sysctl -a`` output into KernelParamInfo records.
    """
    params: list[KernelParamInfo] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if len(line) == 0:
            continue
        if "=" not in line:
            continue
        key_part, _, value_part = line.partition("=")
        params.append(KernelParamInfo(
            key=key_part.strip(),
            value=value_part.strip(),
        ))
    return params


def _parse_lsmod_output(*, stdout: str) -> list[KernelModuleInfo]:
    """
        Parse ``lsmod`` output into KernelModuleInfo records.
    """
    modules: list[KernelModuleInfo] = []
    first_line = True
    for raw in stdout.splitlines():
        if first_line:
            first_line = False
            continue
        line = raw.strip()
        if len(line) == 0:
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            size = int(parts[1])
            use = int(parts[2])
        except ValueError:
            continue
        modules.append(KernelModuleInfo(
            name=parts[0], size_bytes=size, use_count=use,
        ))
    return modules


def _parse_modules_load_file(*, content: str) -> list[str]:
    """
        Parse a modules-load drop-in file into a list of module names.
    """
    names: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        if len(line) == 0 or line.startswith("#"):
            continue
        names.append(line)
    return names
