# Implementation Plan

- [ ] 1. Create the `processmanagement` module file
  - Create `source/packages/automojo/sysman/linux/redhat/processmanagement.py`
  - Add module-level docstring describing purpose, coordinator-driven design,
    and the explicit boundary with `service-management`
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11_

- [ ] 2. Define status enum and result dataclasses
  - Implement `ProcessActionStatus` enum with values: `SUCCESS`, `FAILED`,
    `SKIPPED`, `DRY_RUN`
  - Implement `ProcessActionResult` dataclass with fields: `status`, `pid`,
    `pattern`, `matched_count`, `resulting_value`, `exit_code`, `stdout`,
    `stderr`, `command`
  - Implement `ProcessInfo` dataclass with fields: `pid`, `ppid`, `uid`,
    `user`, `pgid`, `sid`, `stat`, `nice`, `pri`, `pcpu`, `pmem`, `rss`,
    `vsz`, `etime`, `comm`, `args`
  - Implement `OpenFile` dataclass with fields: `fd`, `type`, `device`,
    `size`, `node`, `name`
  - Implement `CpuAffinity` dataclass with fields: `mask`, `cpus`
  - Implement `ProcessWaitResult` dataclass with fields: `exited`, `elapsed`
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10_

- [ ] 3. Define typed exceptions
  - Implement `ProcessQueryError` with `exit_code` and `stderr` attributes
  - Build multi-line error messages using a list of lines joined with
    `os.linesep.join()` per project coding standards
  - _Requirements: 1.4, 2.4, 3.5, 9.5_

- [ ] 4. Implement `ProcessManagerConfig` dataclass
  - Add `dry_run: bool = False` field
  - Add `elevated: bool = False` field
  - _Requirements: 4.6, 5.5, 6.6, 7.5, 8.5, 10.4, 11_

- [ ] 5. Implement `ProcessManager.__init__`, `list_processes()`, and `query_process()`
  - Accept `ICommandRunner`, `IFileOperator`, and `ProcessManagerConfig`
    as constructor arguments
  - Initialize all instance variables in `__init__` only
  - Implement `list_processes(user=None)` using
    `ps -eo pid,ppid,uid,user,pgid,sid,stat,nice,pri,pcpu,pmem,rss,vsz,etime,comm,args --no-headers`
    or `ps -U <user> -o ...` when `user` is not `None`
  - Parse each non-empty output line into a `ProcessInfo`, preserving
    embedded whitespace in `args`
  - Implement `query_process(pid)` using
    `ps -p <pid> -o pid,ppid,uid,user,pgid,sid,stat,nice,pri,pcpu,pmem,rss,vsz,etime,comm,args --no-headers`
  - Return `None` when `ps` exits `1` (PID not found)
  - Raise `ProcessQueryError` for any other non-zero exit code
  - _Requirements: 1, 2_

- [ ] 6. Implement `find_processes()`
  - Build `pgrep -f <pattern>` by default; `pgrep <pattern>` when
    `full=False`
  - Add `-u <user>` when a user filter is supplied
  - Treat exit code `1` (no match) as an empty list; raise
    `ProcessQueryError` for any other non-zero exit code
  - Parse stdout as one PID per line
  - _Requirements: 3_

- [ ] 7. Implement `signal_pid()`
  - Resolve target process owner via `query_process()`; if PID is absent
    return `ProcessActionResult(status=SKIPPED, pid=<pid>)`
  - Build `kill -s <signal> <pid>` command
  - Determine elevation per the UID-compare strategy: elevate only when
    effective UID differs from target owner and `config.elevated=True`
  - Honour `dry_run` flag; return `DRY_RUN` result without calling runner
  - Map runner result: exit code `0` → `SUCCESS`; ESRCH → `SKIPPED`;
    any other non-zero → `FAILED`
  - _Requirements: 4, 11_

- [ ] 8. Implement `signal_by_pattern()` with graceful terminate-then-kill
  - Build `pkill -s <signal> -f <pattern>` command for the non-graceful
    path
  - Treat exit code `1` (no match) as `ProcessActionResult(status=SKIPPED,
    pattern=<pat>, matched_count=0)`
  - For the graceful path: first deliver `TERM` via `pkill`, then poll
    `find_processes(pattern, full=True)` at intervals no greater than
    500 ms until either the set is empty or `grace_period` elapses;
    deliver `KILL` only to the remaining PIDs via `signal_pid()` for
    each survivor
  - Honour `dry_run` flag on both `TERM` and `KILL` legs
  - Populate `matched_count` in the final result based on the PIDs that
    were actually signaled
  - Determine elevation per the UID-compare strategy
  - _Requirements: 5, 11_

- [ ] 9. Implement `renice()` with no-op detection
  - Call `query_process()`; return `SKIPPED` if the PID is absent
  - Return `SKIPPED` if the current `nice` value already equals
    `nice_value`
  - Build `renice -n <value> -p <pid>` command
  - Honour `dry_run` flag
  - Determine elevation per the UID-compare strategy; when requesting a
    priority raise (more-negative nice) and the effective user is not
    root, use elevation if `config.elevated=True`
  - Map runner result to `ProcessActionResult`; set `resulting_value` to
    the target nice value on `SUCCESS`
  - _Requirements: 6, 11_

- [ ] 10. Implement `ionice()`
  - Call `query_process()`; return `SKIPPED` if the PID is absent
  - Translate the class name (`none`, `realtime`, `best-effort`, `idle`)
    to its numeric class argument
  - Build `ionice -c <class-num> -n <priority> -p <pid>` command
  - Honour `dry_run` flag
  - Determine elevation per the UID-compare strategy
  - Map runner result to `ProcessActionResult`
  - _Requirements: 7, 11_

- [ ] 11. Implement `get_cpu_affinity()` and `set_cpu_affinity()` with idempotency
  - Implement `get_cpu_affinity(pid)` using `taskset -p <pid>`
  - Parse the hex mask from stdout and expand it to a CPU id list;
    return a `CpuAffinity` record
  - Return `None` when `taskset` reports the PID is missing
  - Implement `set_cpu_affinity(pid, mask=None, cpu_list=None)`
  - Before mutating, call `get_cpu_affinity()` and compare; when the
    supplied mask or CPU list is equivalent, return `SKIPPED` without
    invoking the runner
  - Build `taskset -p <mask> <pid>` when `mask` is supplied, or
    `taskset -pc <list> <pid>` when `cpu_list` is supplied
  - Honour `dry_run` flag
  - Determine elevation per the UID-compare strategy
  - Map runner result to `ProcessActionResult`
  - _Requirements: 8, 11_

- [ ] 12. Implement `list_open_files()` with `/proc` fallback
  - First attempt `lsof -p <pid> -F pcuftDsidrno`
  - Parse the field-mode output into `OpenFile` records
  - When `lsof` is reported missing ("command not found" style exit)
    fall back to the file operator:
    - list entries under `/proc/<pid>/fd/` via `IFileOperator.list_dir()`
    - resolve each entry via `IFileOperator.read_link()`
    - read `/proc/<pid>/maps` via `IFileOperator.read_text()` and emit
      synthesised `OpenFile` records with the subset of fields
      available
  - Return `[]` when the PID is not found (either via `lsof` exit `1`
    or missing `/proc/<pid>` directory)
  - Raise `ProcessQueryError` for any other non-zero exit code
  - _Requirements: 9_

- [ ] 13. Implement `wait_for_exit()` with 500 ms polling cap
  - When `config.dry_run=True`, return
    `ProcessWaitResult(exited=True, elapsed=0.0)` without polling
  - Otherwise poll `IFileOperator.exists("/proc/<pid>")` in a loop
  - Cap the sleep interval at 500 ms; a shorter interval is permitted
    but never longer
  - Track elapsed time using a monotonic clock
  - Exit the loop and return `exited=True` as soon as the path is absent
  - Exit the loop and return `exited=False` once `timeout` has elapsed
  - _Requirements: 10_

- [ ] 14. Create test module and mock infrastructure
  - Create
    `source/testroots/testbase/automojo/tests/sysman/linux/redhat/test_processmanagement.py`
  - Implement `MockCommandRunner` that returns configurable `RunResult`
    values and records every call (args, elevated flag)
  - Implement `MockFileOperator` with scripted responses for `exists()`,
    `read_text()`, `list_dir()`, and `read_link()`, and a deterministic
    clock hook for `wait_for_exit` tests
  - Set up hypothesis testing framework configuration
  - Create test fixtures for common `ps`, `pgrep`, `lsof`, and `taskset`
    output strings
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11_

- [ ] 15. Write property-based test for list returns parsed records
  - **Property 1: List Returns Parsed Records**
  - Generate random valid `ps` output lines with whitespace-rich `args`
    columns
  - Verify each line maps to a `ProcessInfo` and that `args` whitespace
    is preserved
  - **Validates: Requirements 1.1, 1.2, 1.3**
  - _Requirements: 1_

- [ ] 16. Write property-based test for query returns None for non-existent PID
  - **Property 2: Query Returns None for Non-Existent PID**
  - Generate random PIDs and drive the mock runner to return exit code `1`
  - Verify `query_process()` returns `None` rather than raising
  - **Validates: Requirements 2.2, 2.3**
  - _Requirements: 2_

- [ ] 17. Write property-based test for signal-to-non-existent-PID returns SKIPPED
  - **Property 3: Signal to Non-Existent PID Returns SKIPPED**
  - Generate random missing PIDs and patterns with zero matches
  - Verify both `signal_pid()` and `signal_by_pattern()` return status
    `SKIPPED` and never `FAILED`
  - **Validates: Requirements 4.3, 5.2**
  - _Requirements: 4, 5_

- [ ] 18. Write property-based test for graceful terminate-then-kill escalation
  - **Property 4: Graceful Terminate-Then-Kill Escalation**
  - Generate mixed sets of "exits-on-TERM" and "survives-TERM" PIDs for
    a given pattern
  - Simulate `pgrep` responses that progressively shrink across polls
  - Verify `TERM` is delivered first, polling occurs at intervals
    ≤ 500 ms, and `KILL` is delivered only to survivors after the
    grace period
  - **Validates: Requirements 5.4**
  - _Requirements: 5_

- [ ] 19. Write property-based test for renice no-op when already at value
  - **Property 5: Renice No-Op When Already at Value**
  - Generate random target nice values; drive `query_process()` to
    report the same current value
  - Verify the runner is never invoked and the result is `SKIPPED`
  - **Validates: Requirements 6.1, 6.4**
  - _Requirements: 6_

- [ ] 20. Write property-based test for set_cpu_affinity idempotency
  - **Property 6: Set CPU Affinity Idempotency**
  - Generate random current masks and equivalent target masks / CPU lists
  - Verify `set_cpu_affinity()` returns `SKIPPED` without invoking the
    runner when current and target are equivalent
  - **Validates: Requirements 8.3**
  - _Requirements: 8_

- [ ] 21. Write property-based test for wait_for_exit polling cap
  - **Property 7: Wait-For-Exit Polling Cap**
  - Generate random timeouts and exit moments
  - Instrument the mock clock and record every sleep interval
  - Verify the maximum interval between successive
    `IFileOperator.exists()` calls is always ≤ 500 ms
  - **Validates: Requirements 10.1**
  - _Requirements: 10_

- [ ] 22. Write property-based test for lsof fallback to /proc
  - **Property 8: Lsof Fallback to /proc**
  - Drive the mock runner to report `lsof` as missing
  - Generate random `/proc/<pid>/fd/` directory listings and
    `/proc/<pid>/maps` contents in the file operator
  - Verify `list_open_files()` returns `OpenFile` records synthesised
    from the `/proc` data
  - **Validates: Requirements 9.3**
  - _Requirements: 9_

- [ ] 23. Write property-based test for dry-run safety
  - **Property 9: Dry-Run Safety**
  - Iterate every write method (`signal_pid`, `signal_by_pattern`,
    `renice`, `ionice`, `set_cpu_affinity`) plus `wait_for_exit` with
    `dry_run=True`
  - Verify the mock runner is never invoked with a mutating command
  - Verify each write result has status `DRY_RUN`
  - Verify `wait_for_exit` returns `exited=True, elapsed=0.0` without
    touching the file operator
  - **Validates: Requirements 4.6, 5.5, 6.6, 7.5, 8.5, 10.4**
  - _Requirements: 4, 5, 6, 7, 8, 10_

- [ ] 24. Write property-based test for elevation consistency
  - **Property 10: Elevation Consistency (UID Match Does Not Elevate)**
  - Generate random effective UID vs target owner UID pairs
  - Verify `elevated=True` is passed to the runner only when the UIDs
    differ and `config.elevated=True`
  - Verify read-only commands (`ps`, `pgrep`, `lsof`, `/proc` reads)
    always use `elevated=False`
  - **Validates: Requirements 11.1, 11.2, 11.3, 11.4**
  - _Requirements: 11_

- [ ] 25. Write property-based test for status mapping correctness
  - **Property 11: Status Mapping Correctness**
  - Generate random exit codes (including ESRCH) from the mock runner
  - Verify `ProcessActionResult.status` is `SUCCESS` for `0`, `SKIPPED`
    for ESRCH, and `FAILED` for any other non-zero code
  - **Validates: Requirements 4.4, 4.5, 5.3, 6.4, 6.5, 7.3, 7.4, 8.4**
  - _Requirements: 4, 5, 6, 7, 8_

- [ ] 26. Write property-based test for empty pattern match returns empty list
  - **Property 12: Empty Pattern Match Returns Empty List**
  - Generate random patterns for which the mock runner reports no match
  - Verify `find_processes()` returns `[]` (length `0`) and raises no
    exception
  - **Validates: Requirements 3.4**
  - _Requirements: 3_

- [ ] 27. Write unit tests for `list_processes()` and `query_process()`
  - Test: successful enumeration — returns populated list of
    `ProcessInfo` with whitespace-preserving `args`
  - Test: user filter — runner receives `-U <user>`
  - Test: `ps` command failure — raises `ProcessQueryError`
  - Test: `query_process()` returns populated record on success
  - Test: `query_process()` returns `None` when `ps` exits `1`
  - Test: `query_process()` raises `ProcessQueryError` on unexpected
    non-zero exit code
  - _Requirements: 1, 2_

- [ ] 28. Write unit tests for `find_processes()`
  - Test: default (`-f`) invocation returns PID list
  - Test: basename (`full=False`) invocation omits `-f`
  - Test: user filter adds `-u <user>`
  - Test: no match (exit code `1`) returns `[]`
  - Test: unexpected exit code raises `ProcessQueryError`
  - _Requirements: 3_

- [ ] 29. Write unit tests for `signal_pid()`
  - Test: existing PID, owner matches effective UID — runner called
    without elevation, returns `SUCCESS`
  - Test: existing PID, owner differs and `elevated=True` — runner
    called with `elevated=True`
  - Test: PID missing — returns `SKIPPED`, runner not called
  - Test: PID vanishes mid-operation (ESRCH) — returns `SKIPPED`
  - Test: signal failure — returns `FAILED` with exit code and stderr
  - Test: dry-run mode — returns `DRY_RUN`, runner not called
  - _Requirements: 4, 11_

- [ ] 30. Write unit tests for `signal_by_pattern()`
  - Test: pattern matches — runner called with correct `pkill` args,
    returns `SUCCESS` with `matched_count`
  - Test: no match — returns `SKIPPED` without a second call
  - Test: graceful terminate-then-kill, all exit on TERM — no `KILL`
    delivered, returns `SUCCESS`
  - Test: graceful terminate-then-kill, some survive TERM — `KILL`
    delivered only to survivors, returns `SUCCESS` with survivor count
  - Test: dry-run mode — returns `DRY_RUN`, runner not called
  - _Requirements: 5_

- [ ] 31. Write unit tests for `renice()`
  - Test: different target value — runner called, returns `SUCCESS`
    with `resulting_value`
  - Test: current value already at target — returns `SKIPPED`, runner
    not called
  - Test: PID missing — returns `SKIPPED`, runner not called
  - Test: renice failure — returns `FAILED`
  - Test: dry-run mode — returns `DRY_RUN`, runner not called
  - Test: priority raise with non-root effective UID and `elevated=True`
    — runner called with `elevated=True`
  - _Requirements: 6, 11_

- [ ] 32. Write unit tests for `ionice()`
  - Test: valid class and priority — runner called, returns `SUCCESS`
  - Test: PID missing — returns `SKIPPED`, runner not called
  - Test: ionice failure — returns `FAILED`
  - Test: dry-run mode — returns `DRY_RUN`, runner not called
  - _Requirements: 7_

- [ ] 33. Write unit tests for `get_cpu_affinity()` and `set_cpu_affinity()`
  - Test: `get_cpu_affinity()` — parses mask and expands CPU list
  - Test: `get_cpu_affinity()` — returns `None` when PID missing
  - Test: `set_cpu_affinity()` with equivalent mask — returns `SKIPPED`,
    runner not called
  - Test: `set_cpu_affinity()` with different mask — runner called,
    returns `SUCCESS`
  - Test: `set_cpu_affinity()` with CPU list — runner called with
    `-pc <list> <pid>`
  - Test: dry-run mode on mutating call — returns `DRY_RUN`, runner not
    called
  - _Requirements: 8_

- [ ] 34. Write unit tests for `list_open_files()`
  - Test: `lsof` available and succeeds — returns parsed `OpenFile`
    records
  - Test: `lsof` missing — falls back to `/proc` and file operator;
    returns synthesised records
  - Test: PID missing — returns `[]`
  - Test: unexpected `lsof` exit code — raises `ProcessQueryError`
  - _Requirements: 9_

- [ ] 35. Write unit tests for `wait_for_exit()`
  - Test: process exits immediately — returns `exited=True` with small
    `elapsed`
  - Test: process exits mid-poll — returns `exited=True`, at least one
    poll observed
  - Test: timeout elapses before exit — returns `exited=False` with
    `elapsed` ≥ `timeout`
  - Test: poll interval never exceeds 500 ms across a long wait
  - Test: dry-run mode — returns `exited=True, elapsed=0.0`, file
    operator not called
  - _Requirements: 10_

- [ ] 36. Checkpoint - Ensure all tests pass
  - Run all unit tests and property-based tests
  - Verify all properties pass with 100+ iterations
  - Ensure all tests pass, ask the user if questions arise
