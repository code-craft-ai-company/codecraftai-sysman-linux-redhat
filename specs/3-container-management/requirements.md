# Requirements

## Introduction

The container management feature provides the `codecraftai-sysman-linux-redhat` package
with the ability to manage containers and images using Podman — the default container
engine on Red Hat Enterprise Linux. It covers image lifecycle, container lifecycle,
container registry configuration, registry authentication, and the generation of
systemd units for containers (Quadlet).

A maintenance coordinator uses this feature to deploy and manage containerized
workloads on the target system. All system commands (`podman`, `skopeo` where noted)
are executed through the `codecraftai-interfaces` command-runner abstraction. All reads
and writes against `/etc/containers/*` and systemd unit locations use the
`codecraftai-interfaces` file-operator abstraction. All mutating operations respect
dry-run mode.

Container dependencies delivered as RPMs remain the responsibility of the
`package-management` feature. Firewall ports needed by published containers remain
the responsibility of the `firewall-management` feature. systemd lifecycle control of
generated units remains the responsibility of the `service-management` feature.

---

## Requirements

### Requirement 1: Pull an Image

**User Story:** As a maintenance coordinator, I want to pull a container image by
reference, so that a specific image version is present locally before any container
run.

#### Acceptance Criteria

1. WHEN the coordinator requests an image pull THE SYSTEM SHALL invoke
   `podman pull <image-reference>` via the command runner.
2. WHEN the coordinator runs rootful podman THE SYSTEM SHALL use elevated privileges;
   when the coordinator runs rootless podman THE SYSTEM SHALL NOT elevate.
3. WHEN the coordinator supplies `--creds user:pass` or `--authfile <path>` THE SYSTEM
   SHALL pass the flag through, and SHALL redact credentials in any audit log record.
4. WHEN the image at the exact digest reference is already present THE SYSTEM SHALL
   skip execution and return a `ContainerActionResult` with status `SKIPPED`.
5. WHEN the pull succeeds THE SYSTEM SHALL return a `ContainerActionResult` with
   status `SUCCESS` and the resulting image ID and digest.
6. WHEN the pull fails THE SYSTEM SHALL return a `ContainerActionResult` with status
   `FAILED`, exit code, and captured stderr.
7. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 2: List and Remove Images

**User Story:** As a maintenance coordinator, I want to list and remove images, so
that unused images can be reclaimed.

#### Acceptance Criteria

1. WHEN the coordinator requests a list THE SYSTEM SHALL invoke
   `podman images --format json`.
2. THE SYSTEM SHALL return a list of `ContainerImage` records containing `id`,
   `names`, `digest`, `created`, and `size`.
3. WHEN the coordinator requests removal of an image by id or reference THE SYSTEM
   SHALL invoke `podman rmi <id-or-ref>`, and SHALL pass `-f` only if
   `force=True`.
4. WHEN the image has dependent containers AND `force=False` THE SYSTEM SHALL NOT
   execute removal and SHALL return a `ContainerActionResult` with status `FAILED`
   naming the dependent containers.
5. WHEN the image does not exist THE SYSTEM SHALL skip execution and return a
   `ContainerActionResult` with status `SKIPPED`.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any mutating command.

---

### Requirement 3: Run a Container

**User Story:** As a maintenance coordinator, I want to start a container from an
image with a declared set of arguments, so that a workload is deployed onto the host.

#### Acceptance Criteria

1. WHEN the coordinator supplies a container name, image reference, and a structured
   argument set (env vars, published ports, volume mounts, network, restart policy,
   extra args) THE SYSTEM SHALL invoke
   `podman run -d --name <name> <structured-args> <image>`.
2. WHEN a container with the same name already exists THE SYSTEM SHALL NOT execute
   the run and SHALL return a `ContainerActionResult` with status `FAILED` unless
   the coordinator supplied `replace=True`, in which case the SYSTEM SHALL first
   invoke `podman rm -f <name>`.
3. WHEN the coordinator requests an interactive run THE SYSTEM SHALL pass `-it`
   and SHALL stream stdio through the command runner's pass-through interface when
   available.
4. WHEN the run succeeds THE SYSTEM SHALL return a `ContainerActionResult` with
   status `SUCCESS` and the new container ID.
5. WHEN the run fails THE SYSTEM SHALL return a `ContainerActionResult` with status
   `FAILED`, exit code, and captured stderr.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 4: Stop, Start, Restart, and Remove Containers

**User Story:** As a maintenance coordinator, I want to control the lifecycle of a
named container, so that running containers can be managed without pre-knowledge of
their ID.

#### Acceptance Criteria

1. WHEN the coordinator requests a lifecycle action (`start`, `stop`, `restart`,
   `pause`, `unpause`, `kill`) THE SYSTEM SHALL invoke `podman <action> <name>`.
2. WHEN the coordinator requests a stop with a custom timeout THE SYSTEM SHALL pass
   `-t <n>`.
3. WHEN the coordinator requests removal THE SYSTEM SHALL invoke `podman rm <name>`,
   passing `-f` only if `force=True`.
4. WHEN the container is already in the requested state THE SYSTEM SHALL skip the
   command and return a `ContainerActionResult` with status `SKIPPED`.
5. WHEN the container does not exist AND the action is not a create/run THE SYSTEM
   SHALL return a `ContainerActionResult` with status `SKIPPED`.
6. WHEN the operation succeeds THE SYSTEM SHALL return a `ContainerActionResult`
   with status `SUCCESS`.
7. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 5: Query Container State

**User Story:** As a maintenance coordinator, I want to query the state of containers
individually or collectively, so that I can compare runtime state against the
declared desired state.

#### Acceptance Criteria

1. WHEN the coordinator requests the container list THE SYSTEM SHALL invoke
   `podman ps -a --format json`.
2. WHEN the coordinator requests inspection of a single container THE SYSTEM SHALL
   invoke `podman inspect --format json <name>`.
3. THE SYSTEM SHALL return a list of `ContainerInfo` records (or a single record)
   containing `id`, `name`, `image`, `status`, `created`, `started`, `restart_count`,
   published ports, mounts, and environment variables.
4. WHEN the container does not exist AND a single inspect is requested THE SYSTEM
   SHALL return `None`.
5. WHEN the command fails for any other reason THE SYSTEM SHALL raise a
   `ContainerQueryError`.

---

### Requirement 6: Login and Logout of a Registry

**User Story:** As a maintenance coordinator, I want to log in and out of a container
registry with credentials, so that private images can be pulled and pushed.

#### Acceptance Criteria

1. WHEN the coordinator requests login THE SYSTEM SHALL invoke
   `podman login <registry>` and pass credentials via `--username` and
   `--password-stdin` (reading the password from a pipe, not from argv).
2. WHEN the coordinator requests logout THE SYSTEM SHALL invoke
   `podman logout <registry>`.
3. THE SYSTEM SHALL NEVER include the password on the command line, and SHALL redact
   the `--username` value in the audit log unless the coordinator explicitly opts in
   to including it.
4. WHEN login or logout succeeds THE SYSTEM SHALL return a `ContainerActionResult`
   with status `SUCCESS`.
5. WHEN login fails THE SYSTEM SHALL return a `ContainerActionResult` with status
   `FAILED` and the captured error text, with all credential material redacted.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 7: Manage Registry Configuration

**User Story:** As a maintenance coordinator, I want to manage
`/etc/containers/registries.conf` and drop-ins under
`/etc/containers/registries.conf.d/`, so that the set of searched and blocked
registries matches the declared site policy.

#### Acceptance Criteria

1. WHEN the coordinator requests reading the effective configuration THE SYSTEM SHALL
   read `/etc/containers/registries.conf` and merge in drop-ins from
   `/etc/containers/registries.conf.d/*.conf`, returning a `RegistryConfig` record
   containing the unqualified-search list and per-registry entries.
2. WHEN the coordinator supplies a named drop-in with a TOML body THE SYSTEM SHALL
   write it to `/etc/containers/registries.conf.d/<name>.conf` with mode `0644`,
   owner `root`, group `root`, and elevated privileges.
3. WHEN the existing drop-in content matches the requested content byte-for-byte THE
   SYSTEM SHALL skip the write and return a `ContainerActionResult` with status
   `SKIPPED`.
4. WHEN the coordinator requests removal of a drop-in THE SYSTEM SHALL delete the
   file, or skip if not present.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT perform any write or delete.

---

### Requirement 8: Generate Systemd Unit for a Container

**User Story:** As a maintenance coordinator, I want to generate and install a
systemd unit for a container (via Quadlet `.container` files), so that the container
lifecycle is managed by systemd after a reboot.

#### Acceptance Criteria

1. WHEN the coordinator supplies a Quadlet `.container` body and a target scope
   (`system` or `user`) THE SYSTEM SHALL write it to
   `/etc/containers/systemd/<name>.container` (for system scope) or to
   `~/.config/containers/systemd/<name>.container` (for user scope) through the file
   operator with mode `0644`.
2. WHEN the coordinator requests removal of a Quadlet THE SYSTEM SHALL delete the
   corresponding `.container` file.
3. WHEN identical Quadlet content already exists at the destination THE SYSTEM SHALL
   skip the write and return a `ContainerActionResult` with status `SKIPPED`.
4. WHEN the write succeeds THE SYSTEM SHALL return a `ContainerActionResult` with
   status `SUCCESS` and a note indicating that `systemctl daemon-reload` is required.
   The coordinator SHALL invoke the `service-management` feature to perform the
   daemon-reload; this feature SHALL NOT perform it directly.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT write or delete any file.

---

### Requirement 9: Prune Unused Resources

**User Story:** As a maintenance coordinator, I want to prune stopped containers,
unused images, and dangling volumes, so that host disk space can be reclaimed safely.

#### Acceptance Criteria

1. WHEN the coordinator requests a container prune THE SYSTEM SHALL invoke
   `podman container prune -f`.
2. WHEN the coordinator requests an image prune THE SYSTEM SHALL invoke
   `podman image prune -f` and SHALL pass `-a` only when
   `include_unreferenced=True`.
3. WHEN the coordinator requests a volume prune THE SYSTEM SHALL invoke
   `podman volume prune -f`.
4. WHEN the operation succeeds THE SYSTEM SHALL return a `ContainerActionResult`
   with status `SUCCESS` and the count and byte size of reclaimed items.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 10: Query Podman Info

**User Story:** As a maintenance coordinator, I want to query `podman info` to
determine engine version, graph root, storage driver, and rootless state, so that
other decisions can be made conditionally.

#### Acceptance Criteria

1. WHEN the coordinator requests info THE SYSTEM SHALL invoke
   `podman info --format json`.
2. THE SYSTEM SHALL return a `PodmanInfo` record containing `version`, `api_version`,
   `graph_root`, `storage_driver`, `rootless`, `cgroup_version`, and the
   registries list.
3. WHEN the command fails THE SYSTEM SHALL raise a `ContainerQueryError`.

---

### Requirement 11: Support Rootless and Rootful Operation

**User Story:** As a maintenance coordinator, I want to choose between rootless and
rootful podman for each operation, so that user-scoped workloads and host-scoped
workloads can coexist.

#### Acceptance Criteria

1. WHEN the coordinator specifies `rootless=True` on an operation THE SYSTEM SHALL
   invoke `podman` as the calling user without elevation and SHALL resolve Quadlet
   paths under the user's XDG config directory.
2. WHEN the coordinator specifies `rootless=False` or leaves it unset THE SYSTEM
   SHALL invoke `podman` via the command runner's elevation mechanism and SHALL
   resolve Quadlet paths under `/etc/containers/systemd/`.
3. THE SYSTEM SHALL reject mixing a rootful command with a rootless Quadlet target
   (or vice versa) and SHALL raise a `ContainerScopeMismatchError` before any
   command or write is attempted.

---

### Requirement 12: Elevated Privilege Execution

**User Story:** As a maintenance coordinator, I want all mutating rootful container
commands to run with the privileges required by Podman, so that operations succeed on
standard systems where the process does not run as root.

#### Acceptance Criteria

1. WHEN the coordinator selects rootful execution AND the system is configured to
   use sudo elevation THE SYSTEM SHALL prefix `podman` mutating commands (`pull`,
   `rmi`, `run`, `start/stop/restart/pause/unpause/kill/rm`, `*prune`,
   `login`/`logout`) and writes under `/etc/containers/` with the command runner's
   elevation mechanism.
2. WHEN the system is running as root THE SYSTEM SHALL NOT prefix commands with
   `sudo`.
3. WHEN read-only commands (`podman info`, `podman ps`, `podman images`,
   `podman inspect`) are invoked in rootful mode THE SYSTEM SHALL NOT require
   elevation unless the user lacks access to the rootful podman socket, in which
   case the SYSTEM SHALL use elevation transparently.
