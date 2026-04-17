# automojo.sysman.linux.redhat.containermanagement
#
# ContainerManager wraps podman through the ICommandRunner abstraction to
# manage containers, images, and pods.

import json

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from automojo.sysman.linux.redhat.runners.base import ICommandRunner


class ContainerActionStatus(Enum):
    """
        Outcome status for container management actions.
    """
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class ContainerManagerConfig:
    """
        Configuration for :class:`ContainerManager`.

        :param dry_run: When True, no commands execute.
        :param elevated: When True, podman commands run elevated.
        :param engine: Container engine binary to invoke.
    """
    dry_run: bool = False
    elevated: bool = False
    engine: str = "podman"


@dataclass
class ContainerSpec:
    """
        Specification for running a container.

        :param image: Container image reference.
        :param name: Container name.
        :param command: Optional command to run.
        :param ports: Port mappings in ``host:container`` form.
        :param env: Environment variable overrides.
        :param volumes: Bind mount declarations in ``src:dest`` form.
        :param detach: Whether to run detached.
    """
    image: str
    name: str
    command: list[str] = field(default_factory=list)
    ports: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    volumes: list[str] = field(default_factory=list)
    detach: bool = True


@dataclass
class ContainerActionResult:
    """
        Outcome of a container management action.
    """
    status: ContainerActionStatus
    name: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


@dataclass
class ContainerInfo:
    """
        Summary of a container from ``podman ps``.
    """
    id: str
    name: str
    image: str
    state: str


class ContainerManager:
    """
        Facade for podman container management.
    """

    def __init__(
        self,
        runner: ICommandRunner,
        config: Optional[ContainerManagerConfig] = None,
    ) -> None:
        """
            :param runner: Command runner.
            :param config: Manager configuration.
        """
        if config is None:
            config = ContainerManagerConfig()
        self._runner: ICommandRunner = runner
        self._config: ContainerManagerConfig = config
        return

    @property
    def config(self) -> ContainerManagerConfig:
        """
            The active :class:`ContainerManagerConfig`.
        """
        return self._config

    def pull_image(self, image: str) -> ContainerActionResult:
        """
            Pull ``image`` from its registry.
        """
        command = [self._config.engine, "pull", image]
        return self._dispatch(command=command, name=image)

    def run_container(self, spec: ContainerSpec) -> ContainerActionResult:
        """
            Run a container described by ``spec``.
        """
        command = [self._config.engine, "run", "--name", spec.name]
        if spec.detach:
            command.append("-d")
        for port in spec.ports:
            command.extend(["-p", port])
        for key, value in spec.env.items():
            command.extend(["-e", "{}={}".format(key, value)])
        for volume in spec.volumes:
            command.extend(["-v", volume])
        command.append(spec.image)
        if len(spec.command) > 0:
            command.extend(spec.command)
        return self._dispatch(command=command, name=spec.name)

    def stop_container(self, name: str) -> ContainerActionResult:
        """
            Stop a container.
        """
        command = [self._config.engine, "stop", name]
        return self._dispatch(command=command, name=name)

    def remove_container(self, name: str, *, force: bool = False) -> ContainerActionResult:
        """
            Remove a container via ``podman rm``.
        """
        command = [self._config.engine, "rm"]
        if force:
            command.append("-f")
        command.append(name)
        return self._dispatch(command=command, name=name)

    def list_containers(self, *, include_all: bool = True) -> list[ContainerInfo]:
        """
            List containers via ``podman ps --format=json``.
        """
        command = [self._config.engine, "ps", "--format=json"]
        if include_all:
            command.insert(2, "-a")
        run_result = self._runner.run(command, elevated=self._config.elevated)
        if run_result.exit_code != 0:
            return []
        try:
            data = json.loads(run_result.stdout)
        except json.JSONDecodeError:
            return []
        containers: list[ContainerInfo] = []
        for entry in data:
            names_field = entry.get("Names") or []
            name = names_field[0] if len(names_field) > 0 else entry.get("Name", "")
            containers.append(ContainerInfo(
                id=entry.get("Id", entry.get("ID", "")),
                name=name,
                image=entry.get("Image", ""),
                state=entry.get("State", ""),
            ))
        return containers

    def _dispatch(
        self,
        *,
        command: list[str],
        name: str,
    ) -> ContainerActionResult:
        """
            Dispatch a podman command honouring dry-run.
        """
        if self._config.dry_run:
            return ContainerActionResult(
                status=ContainerActionStatus.DRY_RUN,
                name=name, command=command,
            )
        run_result = self._runner.run(command, elevated=self._config.elevated)
        status = ContainerActionStatus.SUCCESS if run_result.exit_code == 0 else ContainerActionStatus.FAILED
        return ContainerActionResult(
            status=status, name=name,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=list(run_result.command),
        )
