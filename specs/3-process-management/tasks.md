# Implementation Plan — Process Management

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `processmanagement` module

- [x] 2. Define `ProcessActionStatus` enum (SUCCESS / FAILED / SKIPPED
  / DRY_RUN)

- [x] 3. Define `ProcessManagerConfig` (dry_run, elevated)

- [x] 4. Define `ProcessInfo`, `CpuAffinity`, `ProcessActionResult`,
  `ProcessWaitResult` dataclasses

## ProcessManager — Implemented

- [x] 5. Implement `__init__` accepting runner + optional config

- [x] 6. Implement `list_processes(filter=None)` — `ps -eo
  pid,ppid,user,%cpu,%mem,stat,cmd` parsed via `_parse_ps_output`

- [x] 7. Implement `query_process(pid)` — `ps -p <pid> -o ...`
  returning `Optional[ProcessInfo]`

- [x] 8. Implement `find_processes(pattern, ...)` — `pgrep -a
  [options] <pattern>` parsed into `ProcessInfo` list

- [x] 9. Implement `signal_pid(pid, signal)` — `kill -<signal>
  <pid>`

- [x] 10. Implement `signal_by_pattern(pattern, signal, ...)` —
  `pkill` wrapper; SKIPPED when no matching processes exist

- [x] 11. Implement `renice(pid, nice_value)` — `renice <nice>
  <pid>`

- [x] 12. Implement `ionice(pid, class, value)` — `ionice -c
  <class> -n <value> -p <pid>`

- [x] 13. Implement `get_cpu_affinity(pid)` — `taskset -p <pid>`;
  parses hex mask and CPU list via `_parse_taskset_value`

- [x] 14. Implement `set_cpu_affinity(pid, cpus)` — `taskset -pc
  <list> <pid>` or `taskset -p <mask> <pid>`

## ProcessManager — Gaps (specified but not implemented)

- [ ] 15. `wait_for_process(pid, timeout)` — return
  `ProcessWaitResult` when process exits or timeout elapses

- [ ] 16. `query_open_files(pid)` — `lsof -p <pid>` wrapper

- [ ] 17. Support `/proc/<pid>/*` direct reads for richer
  introspection (cmdline, environ, status) beyond what `ps` shows

- [ ] 18. Owner-scoped variant of `signal_by_pattern` that operates
  only within a user's process tree

- [ ] 19. `query_cgroup(pid)` — parse `/proc/<pid>/cgroup`

## Tests — Implemented

- [x] 20. `test_list_processes_parses_ps_output`
- [x] 21. `test_find_processes_parses_pgrep_output`
- [x] 22. `test_signal_pid_dispatches_kill`
- [x] 23. `test_renice_dispatches_renice`
- [x] 24. `test_ionice_dispatches_ionice`
- [x] 25. `test_signal_by_pattern_skipped_when_no_match`
- [x] 26. `test_set_cpu_affinity_with_cpu_list`

## Tests — Gaps

- [ ] 27. Test `query_process` dispatch + `None` on non-zero exit

- [ ] 28. Test `signal_by_pattern` dispatch with matching processes

- [ ] 29. Test `get_cpu_affinity` parser (hex mask + CPU list)

- [ ] 30. Test `set_cpu_affinity` with raw hex mask (not just CPU
  list)

- [ ] 31. Test dry-run short-circuits for every mutating method

## Follow-ups

- [ ] 32. **automojo-* reuse check**: `automojo-interop` may expose
  `/proc` parsing helpers; verify before extending the raw-`ps`
  approach.
