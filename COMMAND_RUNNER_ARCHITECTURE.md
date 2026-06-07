# Command Runner Architecture - Design Summary

## Overview

This document provides a comprehensive architectural design for the command execution layer of the `codecraftai-sysman-linux-redhat` package. The architecture introduces support for **SSH-based remote execution**, **measured reliability tracking**, and **aspect-oriented execution control** while maintaining backward compatibility with existing features.

## Key Concepts

### 1. Unified Command Runner Interface (`ICommandRunner`)

All system management features (package management, service management, environment configuration, etc.) depend on the `ICommandRunner` protocol, which provides a transport-agnostic interface for command execution.

**Implementations:**
- **LocalCommandRunner**: Executes commands via local subprocess
- **SSHCommandRunner**: Executes commands over SSH to remote systems

**Benefits:**
- Features work identically for local and remote systems
- Easy testing with mock runners
- No feature code changes needed to support SSH

### 2. Measured Reliability for SSH Operations

The `SSHCommandRunner` automatically tracks detailed reliability metrics:

- **Success/Failure Counts**: Number of successful vs failed executions
- **Timeout Tracking**: Commands that exceeded timeout limits
- **Connection Errors**: Network, authentication, and host key failures
- **Retry Statistics**: Number of connection retries and reasons
- **Performance Metrics**: Min, max, and average execution duration

**Error Classification:**
- `NETWORK_ERROR`: Socket errors, connection refused
- `AUTH_ERROR`: Authentication failures
- `HOST_KEY_ERROR`: Host key verification failures
- `TIMEOUT_ERROR`: Command or connection timeouts
- `CHANNEL_ERROR`: SSH channel failures

### 3. Execution Aspects for Cross-Cutting Concerns

Aspects provide a composable way to add behaviors to command execution without modifying feature code:

**PersistenceAspect:**
- Logs all command executions to a persistent store (SQLite, JSON, etc.)
- Records command, arguments, environment, working directory, timestamp
- Updates log with exit code, output, duration after execution
- Enables audit trails and command replay

**RetryAspect:**
- Automatically retries commands on transient failures
- Configurable retry policies (max retries, backoff strategy)
- Retryable error detection (exit codes, stderr patterns)
- Updates result with retry count

**TimeoutAspect:**
- Enforces global timeout policy across all commands
- Allows per-command timeout overrides
- Prevents hung processes from blocking workflows

**Aspect Composition:**
Multiple aspects can be combined, executing in registration order:
```
PersistenceAspect → RetryAspect → TimeoutAspect → [COMMAND] → TimeoutAspect → RetryAspect → PersistenceAspect
```

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner (Local or SSH)                      │
│  - Configures aspects (Persistence, Retry, Timeout)        │
│  - Injects runner into features                            │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              System Management Features                     │
│  PackageManager, ServiceManager, EnvironmentConfigurator    │
│  - Depend on ICommandRunner interface                       │
│  - Agnostic to transport (local vs SSH)                    │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                   ICommandRunner                            │
│  run(args, elevated, cwd, env, timeout) → RunResult         │
└────────────────────────┬────────────────────────────────────┘
                         │
          ┌──────────────┴──────────────┐
          ▼                             ▼
┌──────────────────────┐      ┌──────────────────────┐
│ LocalCommandRunner   │      │ SSHCommandRunner     │
│ - subprocess.run()   │      │ - paramiko/fabric    │
│ - sudo for elevation │      │ - connection pool    │
│ - direct env/cwd     │      │ - keepalive          │
│                      │      │ - reliability metrics│
└──────────────────────┘      └──────────────────────┘
           │                             │
           ▼                             ▼
┌─────────────────────────────────────────────────────────────┐
│                    Execution Aspects                        │
│  - PersistenceAspect: logs commands to storage              │
│  - RetryAspect: retries transient failures                  │
│  - TimeoutAspect: enforces global timeout policy            │
└─────────────────────────────────────────────────────────────┘
```

## Usage Examples

### Local Execution (Current Behavior)

```python
from ccai.sysman.linux.redhat.runners import LocalCommandRunner, LocalRunnerConfig
from ccai.sysman.linux.redhat.packagemanagement import PackageManager, PackageManagerConfig

# Create local runner
runner = LocalCommandRunner(LocalRunnerConfig())

# Inject into package manager
pkg_manager = PackageManager(runner, PackageManagerConfig(elevated=True))

# Use normally
result = pkg_manager.install("nginx")
```

### SSH Execution with Reliability Tracking

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.packagemanagement import PackageManager, PackageManagerConfig

# Create SSH runner for remote system
ssh_config = SSHRunnerConfig(
    host="192.168.1.100",
    username="admin",
    key_filename="/home/user/.ssh/id_rsa",
    max_connection_retries=5,
    keepalive_interval=60.0
)
runner = SSHCommandRunner(ssh_config)

# Inject into package manager (same code as local!)
pkg_manager = PackageManager(runner, PackageManagerConfig(elevated=True))

# Use normally - package manager doesn't know it's SSH
result = pkg_manager.install("nginx")

# Check reliability metrics
metrics = runner.get_metrics()
print(f"Success rate: {metrics.success_count / (metrics.success_count + metrics.failure_count):.2%}")
print(f"Average duration: {metrics.avg_duration_ms:.2f}ms")
print(f"Connection retries: {metrics.retry_count}")
```

### SSH Execution with Aspects

```python
from ccai.sysman.linux.redhat.runners import SSHCommandRunner, SSHRunnerConfig
from ccai.sysman.linux.redhat.aspects import PersistenceAspect, RetryAspect, TimeoutAspect
from ccai.sysman.linux.redhat.storage import SQLiteStorage
from ccai.sysman.linux.redhat.servicemanagement import ServiceManager, ServiceManagerConfig

# Create SSH runner
runner = SSHCommandRunner(SSHRunnerConfig(host="remote-server", username="admin"))

# Add persistence aspect for audit logging
storage = SQLiteStorage("/var/log/maintenance/commands.db")
persistence = PersistenceAspect(PersistenceAspectConfig(storage_backend=storage))
runner.add_aspect(persistence)

# Add retry aspect for transient failures
retry = RetryAspect(RetryAspectConfig(
    max_retries=3,
    retryable_exit_codes=[1, 255],  # Common transient SSH errors
    retryable_error_patterns=["Connection reset", "Broken pipe"]
))
runner.add_aspect(retry)

# Add timeout aspect for global policy
timeout = TimeoutAspect(TimeoutAspectConfig(default_timeout=300.0))
runner.add_aspect(timeout)

# Inject into service manager
svc_manager = ServiceManager(runner, ServiceManagerConfig(elevated=True))

# All commands now have persistence, retry, and timeout applied automatically
result = svc_manager.restart("nginx")
```

## Impact on Existing Features

### No Breaking Changes

All existing features continue to work with `LocalCommandRunner` exactly as before. The `ICommandRunner` interface is unchanged.

### Opt-In SSH Support

Features automatically support SSH execution when injected with an `SSHCommandRunner` instead of `LocalCommandRunner`. No feature code changes required.

### Opt-In Aspects

Aspects are configured on the runner, not in features. Features remain unaware of aspects, keeping them simple and focused.

## Implementation Roadmap

### Phase 1: Core Infrastructure (Weeks 1-2)
- Implement `LocalCommandRunner` and `SSHCommandRunner`
- Implement `RunResult` and `SSHReliabilityMetrics` models
- Implement SSH connection management with retry logic
- Implement error classification for SSH operations

### Phase 2: Aspect Framework (Weeks 3-4)
- Implement `IExecutionAspect` protocol
- Implement `PersistenceAspect` with SQLite and JSON storage
- Implement `RetryAspect` with configurable policies
- Implement `TimeoutAspect` with global defaults

### Phase 3: Feature Integration (Weeks 5-6)
- Update all feature specs to reference command runner architecture
- Update feature designs to show SSH and aspect usage examples
- Add correctness properties for SSH reliability and aspect behavior
- Update implementation tasks to use new runner abstractions

### Phase 4: Testing and Documentation (Weeks 7-8)
- Comprehensive unit tests for runners and aspects
- Property-based tests for transport equivalence and reliability
- Integration tests with real SSH connections
- User documentation and migration guide

## Next Steps

1. **Review this architecture document** - Ensure it meets your requirements for SSH, reliability, and aspects
2. **Update feature specs** - Systematically update all 15 feature specs to reference this architecture
3. **Implement core infrastructure** - Build runners, aspects, and storage backends
4. **Integrate with features** - Update features to use new runner abstractions
5. **Test and validate** - Comprehensive testing across local and SSH transports

## Files Created

- `.kiro/specs/command-runner-architecture/requirements.md` - Detailed requirements with EARS patterns
- `.kiro/specs/command-runner-architecture/design.md` - Comprehensive design with implementation details
- `.kiro/specs/command-runner-architecture/tasks.md` - Implementation plan with 35 tasks
- `COMMAND_RUNNER_ARCHITECTURE.md` - This summary document

## Questions for Review

1. **SSH Library**: Should we use `paramiko` (pure Python) or `fabric` (higher-level wrapper)?
2. **Storage Backends**: Are SQLite and JSON sufficient, or do you need other backends (PostgreSQL, MongoDB)?
3. **Aspect Priorities**: Are there other aspects you need beyond Persistence, Retry, and Timeout?
4. **Metrics Reporting**: How should reliability metrics be exposed? (API, logs, dashboard?)
5. **Connection Pooling**: Should we support connection pooling for multiple SSH targets?

---

**Status**: Architecture design complete, ready for review and feature spec updates.
