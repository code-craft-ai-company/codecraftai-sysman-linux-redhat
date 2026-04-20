# Requirements

## Introduction

The resource control management feature provides the
`automojo-sysman-linux-redhat` package with the ability to manage
systemd-cgroup-based resource limits on Red Hat Linux systems.  It
covers setting transient and persistent `CPUQuota`, `MemoryMax`,
`IOWeight`, `TasksMax`, and related directives on services and
slices; managing slice definitions under
`/etc/systemd/system/<slice>.slice.d/`; launching transient scopes
via `systemd-run`; and querying current resource usage via
`systemd-cgtop` and `systemd-cgls`.

All system commands are executed through the `automojo-interfaces`
command-runner abstraction.  Config-file reads and writes route
through the runner (`cat`/`tee`).  All mutating operations respect
dry-run mode.

This feature complements — and does not duplicate — the systemd
lifecycle surface owned by `3-service-management` (start / stop /
enable / disable / restart / daemon-reload); the performance-profile
surface owned by `3-tuned-management`; and the container-side
resource controls that Podman applies via `3-container-management`.

---

## Requirements

### Requirement 1: Query Current Resource Properties

**User Story:** As a maintenance coordinator, I want to query the
current resource-control properties of a service or slice, so that
I can compare against the declared desired state.

#### Acceptance Criteria

1. WHEN the coordinator queries a unit's properties THE SYSTEM
   SHALL invoke `systemctl show <unit> --property <P1,P2,...>`
   and return a `dict[str, str]` keyed by property name.
2. WHEN the coordinator queries all resource-control properties THE
   SYSTEM SHALL use the documented set: `CPUQuotaPerSecUSec`,
   `CPUWeight`, `MemoryMax`, `MemoryHigh`, `TasksMax`, `IOWeight`,
   `IPAccounting`, `Slice`, `ControlGroup`.
3. WHEN the unit does not exist THE SYSTEM SHALL return an empty
   dict rather than raising.

---

### Requirement 2: Set Transient Properties

**User Story:** As a maintenance coordinator, I want to change a
running unit's resource limits without persisting them across
reboots, so that I can respond to immediate load without committing
a change.

#### Acceptance Criteria

1. WHEN the coordinator sets a transient property THE SYSTEM SHALL
   invoke `systemctl set-property --runtime <unit> <KEY>=<VALUE>
   [<KEY2>=<VALUE2> ...]`.
2. WHEN the current value already matches THE SYSTEM SHALL return
   `SKIPPED` for that key (the aggregate result SKIPS only when
   every requested key is already in the desired state).
3. WHEN any property fails THE SYSTEM SHALL return `FAILED` with
   `exit_code` and `stderr` from the runner; partial success is
   reported via the aggregated action result rather than raising.

---

### Requirement 3: Set Persistent Properties (Drop-In Files)

**User Story:** As a maintenance coordinator, I want to persist
resource-control directives so that they survive reboots and
unit-file upgrades.

#### Acceptance Criteria

1. WHEN the coordinator sets a persistent property THE SYSTEM SHALL
   write (or update) a drop-in file at
   `/etc/systemd/system/<unit>.d/10-resource-control.conf`
   containing the `[Service]` (or `[Slice]`, depending on unit
   type) block with the requested directives.
2. WHEN the drop-in already contains the requested directives THE
   SYSTEM SHALL return `SKIPPED`.
3. WHEN the write succeeds THE SYSTEM SHALL invoke a
   `daemon-reload` via the injected `ServiceManager` (rather than
   spelling `systemctl daemon-reload` directly).
4. THE SYSTEM SHALL preserve every other directive in the drop-in
   file when updating; only the targeted keys change.

---

### Requirement 4: Remove Persistent Properties

**User Story:** As a maintenance coordinator, I want to remove
resource-control directives from a unit, reverting it to the
vendor-supplied defaults.

#### Acceptance Criteria

1. WHEN the coordinator removes a directive THE SYSTEM SHALL strip
   the matching line from the drop-in file.
2. WHEN the drop-in file becomes empty after removal (only section
   headers or whitespace remain) THE SYSTEM SHALL `rm -f` the file.
3. WHEN the removal completes THE SYSTEM SHALL invoke
   `daemon-reload`.
4. WHEN the drop-in file did not contain the directive THE SYSTEM
   SHALL return `SKIPPED`.

---

### Requirement 5: Slice Management

**User Story:** As a maintenance coordinator, I want to define
custom slices and attach units to them, so that resource ceilings
apply to groups of services collectively.

#### Acceptance Criteria

1. WHEN the coordinator defines a slice THE SYSTEM SHALL write the
   slice unit file to `/etc/systemd/system/<name>.slice` via `tee`,
   ensure it is enabled via the injected `ServiceManager`, and run
   `daemon-reload`.
2. WHEN the coordinator sets resource directives on a slice THE
   SYSTEM SHALL write to
   `/etc/systemd/system/<name>.slice.d/10-resource-control.conf`
   using the same pattern as for services.
3. WHEN the coordinator attaches a unit to a slice THE SYSTEM SHALL
   set `Slice=<name>.slice` in the unit's drop-in file (using the
   persistent-properties code path).
4. WHEN the coordinator removes a slice THE SYSTEM SHALL first
   detach every attached unit (or raise if `strict=True` is set
   and attached units exist), then remove the slice file and
   `daemon-reload`.

---

### Requirement 6: Transient Scope Execution (`systemd-run`)

**User Story:** As a maintenance coordinator, I want to run a
command under a transient systemd scope with declared resource
limits, so that the command cannot exhaust shared resources.

#### Acceptance Criteria

1. WHEN the coordinator requests a scoped run THE SYSTEM SHALL
   invoke `systemd-run --scope --slice <slice> [--property
   KEY=VALUE ...] <command...>` through the runner.
2. WHEN `slice` is not supplied THE SYSTEM SHALL default to
   `system.slice`.
3. WHEN the command returns non-zero THE SYSTEM SHALL surface the
   exit code and stderr as a `FAILED` result.

---

### Requirement 7: Cgroup Introspection

**User Story:** As a maintenance coordinator, I want to query the
current cgroup tree and per-unit usage, so that I can confirm
applied limits are effective.

#### Acceptance Criteria

1. WHEN the coordinator queries the cgroup tree THE SYSTEM SHALL
   invoke `systemd-cgls --no-pager [--unit <unit>]` and return the
   raw output.
2. WHEN the coordinator queries live usage THE SYSTEM SHALL invoke
   `systemd-cgtop --iterations=1 --raw` and parse into
   `list[CgroupUsage]` records (`path`, `tasks`, `cpu_percent`,
   `memory_bytes`, `input_per_s`, `output_per_s`).
3. WHEN the coordinator queries a specific cgroup's direct
   attribute (e.g., `memory.current`) THE SYSTEM SHALL read the
   file at `/sys/fs/cgroup/<path>/<attr>` via `cat` and return the
   stripped value, or `None` when the file does not exist.

---

### Requirement 8: Dry-Run, Elevation, and Error Reporting

**User Story:** As a maintenance coordinator, I want every mutating
operation to respect dry-run, run elevated, and surface failures
structurally.

#### Acceptance Criteria

1. WHEN `config.dry_run=True` THE SYSTEM SHALL NOT dispatch
   `systemctl set-property --runtime`, NOT write drop-in files,
   NOT run `systemd-run`, and NOT invoke `daemon-reload`.  A
   `ResourceActionResult(status=DRY_RUN)` is returned with the
   intended change described in `detail`.
2. WHEN `config.elevated=True` THE SYSTEM SHALL dispatch mutating
   commands elevated.
3. WHEN the runner returns non-zero THE SYSTEM SHALL return
   `ResourceActionResult(status=FAILED, exit_code=<code>,
   stderr=<captured>)`.
