# Requirements

## Introduction

The process management feature provides the `codecraftai-sysman-linux-redhat` package
with the ability to enumerate, inspect, signal, and adjust running processes on Red
Hat Linux systems. It covers process listing, attribute queries, signal delivery,
priority and CPU affinity adjustment, and owner-scoped operations.

A maintenance coordinator uses this feature to identify runaway or stale processes,
terminate them, or adjust their scheduling priority. All system commands (`ps`,
`pgrep`, `pkill`, `kill`, `renice`, `ionice`, `taskset`, `lsof`) and `/proc` reads
are executed through the `codecraftai-interfaces` command-runner and file-operator
abstractions. All mutating operations respect dry-run mode.

This feature does not manage systemd services; service lifecycle remains the
responsibility of the `service-management` feature.

---

## Requirements

### Requirement 1: Enumerate Processes

**User Story:** As a maintenance coordinator, I want to enumerate running processes
with a declared set of attributes, so that I can make decisions about which processes
to signal or adjust.

#### Acceptance Criteria

1. WHEN the coordinator requests the process list THE SYSTEM SHALL invoke
   `ps -eo pid,ppid,uid,user,pgid,sid,stat,nice,pri,pcpu,pmem,rss,vsz,etime,comm,args --no-headers`.
2. THE SYSTEM SHALL return a list of `ProcessInfo` records, one per line, with
   whitespace-split columns mapped to typed fields. The `args` column SHALL preserve
   embedded whitespace.
3. WHEN the coordinator supplies a user filter THE SYSTEM SHALL invoke
   `ps -U <user> -o ...` and return only matching entries.
4. WHEN the command fails THE SYSTEM SHALL raise a `ProcessQueryError` with the exit
   code and captured stderr.

---

### Requirement 2: Query a Single Process by PID

**User Story:** As a maintenance coordinator, I want to query a specific process by
PID, so that I can check whether it is still running and fetch its attributes.

#### Acceptance Criteria

1. WHEN the coordinator requests information for a PID THE SYSTEM SHALL invoke
   `ps -p <pid> -o pid,ppid,uid,user,pgid,sid,stat,nice,pri,pcpu,pmem,rss,vsz,etime,comm,args --no-headers`.
2. WHEN the process exists THE SYSTEM SHALL return a `ProcessInfo` record.
3. WHEN the process does not exist THE SYSTEM SHALL return `None`.
4. WHEN the command fails for any other reason THE SYSTEM SHALL raise a
   `ProcessQueryError`.

---

### Requirement 3: Find Processes by Name or Pattern

**User Story:** As a maintenance coordinator, I want to find processes whose command
or args match a regex or fixed string, so that I can locate target processes by name
without first enumerating all PIDs.

#### Acceptance Criteria

1. WHEN the coordinator supplies a pattern THE SYSTEM SHALL invoke
   `pgrep -f <pattern>` and return the resulting list of PIDs.
2. WHEN the coordinator requests that only the basename be matched THE SYSTEM SHALL
   invoke `pgrep <pattern>` without `-f`.
3. WHEN the coordinator supplies a user filter THE SYSTEM SHALL pass `-u <user>`.
4. WHEN no processes match THE SYSTEM SHALL return an empty list.
5. WHEN `pgrep` fails for any other reason THE SYSTEM SHALL raise a
   `ProcessQueryError`.

---

### Requirement 4: Send a Signal to a Specific PID

**User Story:** As a maintenance coordinator, I want to send a named POSIX signal to
a specific PID, so that I can gracefully terminate or otherwise control a process.

#### Acceptance Criteria

1. WHEN the coordinator supplies a PID and a signal name (e.g. `TERM`, `HUP`,
   `USR1`, `KILL`) THE SYSTEM SHALL invoke `kill -s <signal> <pid>`.
2. WHEN the coordinator's effective UID does not own the target process AND the
   system is configured for sudo elevation THE SYSTEM SHALL use elevation;
   otherwise the SYSTEM SHALL call `kill` directly.
3. WHEN the PID does not exist THE SYSTEM SHALL return a `ProcessActionResult` with
   status `SKIPPED`.
4. WHEN the signal is delivered THE SYSTEM SHALL return a `ProcessActionResult`
   with status `SUCCESS`.
5. WHEN the kill fails for any other reason THE SYSTEM SHALL return a
   `ProcessActionResult` with status `FAILED`, exit code, and captured stderr.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT deliver any signal.

---

### Requirement 5: Send a Signal to Processes by Name

**User Story:** As a maintenance coordinator, I want to signal all processes matching
a pattern, so that a service or cluster of helper processes can be terminated in one
call.

#### Acceptance Criteria

1. WHEN the coordinator supplies a pattern THE SYSTEM SHALL invoke
   `pkill -s <signal> -f <pattern>`.
2. WHEN no processes match THE SYSTEM SHALL return a `ProcessActionResult` with
   status `SKIPPED`.
3. WHEN at least one process is signaled THE SYSTEM SHALL return a
   `ProcessActionResult` with status `SUCCESS` and the count of matched processes.
4. WHEN the coordinator requests a graceful terminate-then-kill sequence THE SYSTEM
   SHALL first deliver `TERM`, wait up to the supplied grace period polling
   `pgrep -f <pattern>`, and then deliver `KILL` only to remaining PIDs.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT deliver any signal.

---

### Requirement 6: Change Process Priority (nice)

**User Story:** As a maintenance coordinator, I want to renice a process, so that
its CPU scheduling priority reflects its current workload importance.

#### Acceptance Criteria

1. WHEN the coordinator supplies a PID and a new nice value THE SYSTEM SHALL invoke
   `renice -n <value> -p <pid>`.
2. WHEN the coordinator requests a raise in priority (more-negative nice) AND is not
   root THE SYSTEM SHALL use sudo elevation if configured.
3. WHEN the PID does not exist THE SYSTEM SHALL return a `ProcessActionResult` with
   status `SKIPPED`.
4. WHEN the operation succeeds THE SYSTEM SHALL return a `ProcessActionResult` with
   status `SUCCESS` and the resulting nice value.
5. WHEN the operation fails THE SYSTEM SHALL return a `ProcessActionResult` with
   status `FAILED`, exit code, and captured stderr.
6. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 7: Change I/O Scheduling Class

**User Story:** As a maintenance coordinator, I want to set the I/O scheduling class
and priority of a process, so that I/O-bound batch work does not starve interactive
workloads.

#### Acceptance Criteria

1. WHEN the coordinator supplies a PID, class (`none`, `realtime`, `best-effort`,
   `idle`), and priority THE SYSTEM SHALL invoke
   `ionice -c <class-num> -n <priority> -p <pid>`.
2. WHEN the PID does not exist THE SYSTEM SHALL return a `ProcessActionResult` with
   status `SKIPPED`.
3. WHEN the operation succeeds THE SYSTEM SHALL return a `ProcessActionResult` with
   status `SUCCESS`.
4. WHEN the operation fails THE SYSTEM SHALL return a `ProcessActionResult` with
   status `FAILED`, exit code, and captured stderr.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any command.

---

### Requirement 8: Query and Set CPU Affinity

**User Story:** As a maintenance coordinator, I want to query and set the CPU
affinity of a running process, so that its threads can be pinned to specific cores.

#### Acceptance Criteria

1. WHEN the coordinator queries the affinity of a PID THE SYSTEM SHALL invoke
   `taskset -p <pid>` and parse the returned mask. THE SYSTEM SHALL return a
   `CpuAffinity` record containing the hex mask and the expanded CPU id list.
2. WHEN the coordinator sets an affinity THE SYSTEM SHALL invoke
   `taskset -p <mask-or-list> <pid>` using the `-c` form when a CPU list is
   supplied.
3. WHEN the supplied mask matches the current mask THE SYSTEM SHALL skip execution
   and return a `ProcessActionResult` with status `SKIPPED`.
4. WHEN the operation succeeds THE SYSTEM SHALL return a `ProcessActionResult` with
   status `SUCCESS`.
5. WHEN dry-run mode is active THE SYSTEM SHALL NOT execute any mutating command.

---

### Requirement 9: Enumerate Open Files of a Process

**User Story:** As a maintenance coordinator, I want to enumerate the open files,
sockets, and deleted-but-held file handles of a process, so that resource leaks or
stale references can be diagnosed.

#### Acceptance Criteria

1. WHEN the coordinator requests the open file list for a PID THE SYSTEM SHALL
   invoke `lsof -p <pid> -F pcuftDsidrno` (field-output mode) via the command
   runner.
2. THE SYSTEM SHALL return a list of `OpenFile` records containing `fd`, `type`,
   `device`, `size`, `node`, and `name`.
3. WHEN `lsof` is not installed THE SYSTEM SHALL fall back to reading
   `/proc/<pid>/fd/` and `/proc/<pid>/maps` through the file operator, producing
   records with the subset of fields available there.
4. WHEN the PID does not exist THE SYSTEM SHALL return an empty list.
5. WHEN the command fails for any other reason THE SYSTEM SHALL raise a
   `ProcessQueryError`.

---

### Requirement 10: Wait for Process Exit

**User Story:** As a maintenance coordinator, I want to wait for a process to exit
with a timeout, so that orchestrated shutdowns can proceed only after the target is
confirmed gone.

#### Acceptance Criteria

1. WHEN the coordinator supplies a PID and a timeout THE SYSTEM SHALL poll
   `/proc/<pid>` existence at a capped interval of at most 500 ms until either the
   PID disappears or the timeout elapses.
2. WHEN the process exits within the timeout THE SYSTEM SHALL return a
   `ProcessWaitResult` with `exited=True` and the observed elapsed duration.
3. WHEN the timeout elapses before the process exits THE SYSTEM SHALL return a
   `ProcessWaitResult` with `exited=False` and the observed elapsed duration.
4. WHEN dry-run mode is active THE SYSTEM SHALL NOT poll the filesystem and SHALL
   return a `ProcessWaitResult` with `exited=True` and an elapsed duration of
   zero.

---

### Requirement 11: Elevated Privilege Execution

**User Story:** As a maintenance coordinator, I want mutating operations against
processes owned by other users to run with the privileges required by the kernel,
so that operations succeed on standard systems where the process does not run as
root.

#### Acceptance Criteria

1. WHEN the coordinator's effective UID matches the target process owner's UID THE
   SYSTEM SHALL NOT use elevation.
2. WHEN the coordinator's effective UID differs AND the system is configured for
   sudo elevation THE SYSTEM SHALL prefix `kill`, `pkill`, `renice`, `ionice`,
   `taskset`, and signal-delivery commands with the command runner's elevation
   mechanism.
3. WHEN the system is running as root THE SYSTEM SHALL NOT prefix commands with
   `sudo`.
4. WHEN read-only commands (`ps`, `pgrep`, `lsof`, `/proc` reads) are invoked THE
   SYSTEM SHALL NOT require elevation, though the command runner MAY use
   elevation if a target process's `/proc` entry is not readable.
