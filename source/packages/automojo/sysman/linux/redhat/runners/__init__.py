# automojo.sysman.linux.redhat.runners
#
# Command runner implementations for the Red Hat Linux system management
# package.  Runners provide a transport-agnostic interface for command
# execution through the ICommandRunner protocol.

from automojo.sysman.linux.redhat.runners.base import ICommandRunner
from automojo.sysman.linux.redhat.runners.local import LocalCommandRunner, LocalRunnerConfig
from automojo.sysman.linux.redhat.runners.models import (
    ExecutionContext,
    RunResult,
    SSHErrorType,
    SSHReliabilityMetrics,
)
from automojo.sysman.linux.redhat.runners.ssh import SSHCommandRunner, SSHRunnerConfig

__all__ = [
    "ExecutionContext",
    "ICommandRunner",
    "LocalCommandRunner",
    "LocalRunnerConfig",
    "RunResult",
    "SSHCommandRunner",
    "SSHErrorType",
    "SSHReliabilityMetrics",
    "SSHRunnerConfig",
]
