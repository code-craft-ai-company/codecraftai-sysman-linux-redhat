# Implementation Plan

- [ ] 1. Create the `cockpitmanagement` module file
  - Create `source/packages/automojo/sysman/linux/redhat/cockpitmanagement.py`
  - Add module-level docstring describing purpose and coordinator-driven design
  - Document that RPM install is delegated to `package-management`, firewall
    port-opening to `firewall-management`, and `daemon-reload` + socket restart to
    `service-management`
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9_

- [ ] 2. Define status enum and result/state dataclasses
  - Implement `CockpitActionStatus` enum with values: `SUCCESS`, `FAILED`,
    `SKIPPED`, `DRY_RUN`
  - Implement `CockpitActionResult` dataclass with fields: `status`, `target`,
    `exit_code`, `stdout`, `stderr`, `command`, `path`, `note`
  - Implement `CockpitSocketState` dataclass with fields: `present`, `active`,
    `enabled`, `listen`
  - Implement `CockpitModule` dataclass with fields: `name`, `version`, `priority`,
    `requires`, `path`, `valid`, `error`
  - Implement `CockpitListen` dataclass with field: `entries` (list of
    `(address, port)` tuples)
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8_

- [ ] 3. Define typed exceptions
  - Implement `CockpitQueryError` with `exit_code` and `stderr` attributes
  - Implement `CockpitNotInstalledError` with a descriptive message and optional
    `command` attribute
  - _Requirements: 1.4, 4.4, 8.3, 8.4_

- [ ] 4. Implement `CockpitManagerConfig` dataclass
  - Add `dry_run: bool = False` field
  - Add `elevated: bool = False` field
  - _Requirements: 2.5, 5.5, 6.6, 7.4, 9_

- [ ] 5. Implement `CockpitManager.__init__`, `get_socket_state()`, `list_modules()`, `get_version()`
  - Accept `ICommandRunner`, `IFileOperator`, and `CockpitManagerConfig` as
    constructor arguments; initialise all instance variables in `__init__`
  - Implement `get_socket_state()` using `systemctl is-active cockpit.socket` and
    `systemctl is-enabled cockpit.socket`; detect `present=False` when both report
    the unit as missing; embed the result of `get_listen_port()` in the record
  - Implement `list_modules()` using `IFileOperator.glob()` on
    `/usr/share/cockpit/*/manifest.json`; parse each manifest and tolerate parse
    errors by producing records with `valid=False` and `error` populated
  - Return an empty list when `/usr/share/cockpit/` does not exist
  - Implement `get_version()` using `cockpit-bridge --version`; raise
    `CockpitNotInstalledError` for not-found signals and `CockpitQueryError` for
    all other non-zero exit codes
  - Ensure read operations never pass `elevated=True`
  - _Requirements: 1, 3, 8, 9.3_

- [ ] 6. Implement `lifecycle_action()`
  - Accept `action` parameter restricted to `start`, `stop`, `restart`, `enable`,
    `disable`
  - Call `get_socket_state()` to check the current state; return `SKIPPED` when the
    socket is already in the requested state (except for `restart`, which is always
    performed when the socket is active)
  - Build `systemctl <action> cockpit.socket` command
  - Honour `dry_run` flag; return `DRY_RUN` result without invoking the runner
  - Pass `elevated` flag to the runner when the config requests elevation
  - Parse the runner result into `CockpitActionResult`
  - _Requirements: 2, 9.1_

- [ ] 7. Implement `get_listen_port()` and `set_listen_port()` / `remove_listen_dropin()`
  - Implement `get_listen_port()` using
    `systemctl show cockpit.socket --property=Listen`; parse `Listen=Stream:<address>:<port>`
    entries; when no `Listen=Stream` property is reported fall back to reading
    `/usr/lib/systemd/system/cockpit.socket` and any drop-ins under
    `/etc/systemd/system/cockpit.socket.d/` via the file operator; raise
    `CockpitQueryError` when both sources fail
  - Return a `CockpitListen` record of `(address, port)` tuples
  - Implement `set_listen_port(address, port)` by building the exact drop-in body
    (`[Socket]` + `ListenStream=` reset + `ListenStream=<address>:<port>`)
  - Byte-compare the existing drop-in file contents; return `SKIPPED` when
    byte-identical
  - Honour `dry_run`; write via `IFileOperator.write()` with mode `0644`, owner
    `root`, group `root`, and `elevated=True` when the config requests elevation
  - Populate `CockpitActionResult.note` with a reminder that
    `service-management.daemon_reload()` and a restart of `cockpit.socket` are
    required for the change to take effect
  - Implement `remove_listen_dropin()`; return `SKIPPED` when the file does not
    exist; honour `dry_run`; delete via `IFileOperator.delete()` with elevation
  - Do NOT perform `systemctl daemon-reload` or restart here; this is the
    responsibility of the `service-management` feature
  - _Requirements: 4, 5, 9.1_

- [ ] 8. Implement `get_config()`, `set_config()`, `delete_config()`
  - Implement `get_config(section, key)` by reading `/etc/cockpit/cockpit.conf`
    through the file operator and parsing it as an INI file; return the string
    value or `None` when the section/key is absent
  - Implement `set_config(section, key, value)` with a comment- and ordering-preserving
    INI editor; read the current value and return `SKIPPED` when it already matches
  - When the file does not exist, create `/etc/cockpit/` via
    `IFileOperator.makedirs()` with mode `0755` before writing
  - Write via `IFileOperator.write()` with mode `0644`, owner `root`, group `root`,
    and elevation when the config requests it; populate
    `CockpitActionResult.note` with a reminder that a Cockpit session reconnect or
    `systemctl restart cockpit.socket` is required for some keys to take effect
  - Implement `delete_config(section, key)`; remove the matching line and, when
    the section becomes empty, remove the `[section]` header as well
  - Honour `dry_run` on both `set_config()` and `delete_config()`
  - _Requirements: 6, 9.1_

- [ ] 9. Implement `install_banner()`
  - Accept `text` and `path` parameters
  - Read the existing file through the file operator and byte-compare; return
    `SKIPPED` when the content matches byte-for-byte
  - Honour `dry_run`; when not in dry-run, write via `IFileOperator.write()` with
    mode `0644`, owner `root`, group `root`, and elevation when the config requests
    it
  - Do NOT update `cockpit.conf` from this method; the coordinator is expected to
    call `set_config(section="Session", key="Banner", value=path)` separately
  - _Requirements: 7, 9.1_

- [ ] 10. Create test module and mock infrastructure
  - Create
    `source/testroots/testbase/automojo/tests/sysman/linux/redhat/test_cockpitmanagement.py`
  - Implement `MockCommandRunner` that returns configurable `RunResult` values
    keyed on the command argv
  - Implement `MockFileOperator` with in-memory file storage that records
    `write`/`delete`/`makedirs`/`chmod`/`chown` invocations and their parameters
    (including `elevated`)
  - Set up hypothesis testing framework configuration
  - Create test fixtures for common test scenarios (installed modules, well-formed
    `cockpit.conf`, well-formed listen drop-in)
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9_

- [ ] 11. Write property-based test for lifecycle idempotency
  - **Property 1: Lifecycle Idempotency**
  - Generate random actions and current socket states
  - Verify `lifecycle_action()` returns `SKIPPED` when the socket is already in
    the requested state; verify `restart` is never skipped when active
  - **Validates: Requirements 2.2**
  - _Requirements: 2_

- [ ] 12. Write property-based test for listen drop-in byte-identical skip
  - **Property 2: Listen Drop-In Byte-Identical Skip**
  - Generate random address/port pairs and seed the mock file operator with the
    corresponding byte-identical drop-in content
  - Verify `set_listen_port()` returns `SKIPPED` without invoking `write()`
  - **Validates: Requirements 5.2**
  - _Requirements: 5_

- [ ] 13. Write property-based test for listen drop-in remove-when-absent
  - **Property 3: Listen Drop-In Remove When Absent**
  - Generate randomised filesystem states where the drop-in file is absent
  - Verify `remove_listen_dropin()` returns `SKIPPED` without invoking `delete()`
  - **Validates: Requirements 5.4**
  - _Requirements: 5_

- [ ] 14. Write property-based test for config set idempotency with comment preservation
  - **Property 4: Config Set Idempotency With Comment Preservation**
  - Generate random INI documents containing comments, sections, and keys
  - Verify `set_config()` returns `SKIPPED` when the value already matches
  - Verify comments and ordering of unrelated keys are preserved across writes
  - **Validates: Requirements 6.2, 6.4**
  - _Requirements: 6_

- [ ] 15. Write property-based test for config delete with empty-section removal
  - **Property 5: Config Delete With Empty Section Removal**
  - Generate random sections with varying numbers of keys
  - Verify `delete_config()` removes both the key line and the `[section]`
    header when the section becomes empty
  - **Validates: Requirements 6.3**
  - _Requirements: 6_

- [ ] 16. Write property-based test for banner byte-identical skip
  - **Property 6: Banner Byte-Identical Skip**
  - Generate random banner bodies and seed the mock file operator with matching
    content
  - Verify `install_banner()` returns `SKIPPED` without invoking `write()`
  - **Validates: Requirements 7.2**
  - _Requirements: 7_

- [ ] 17. Write property-based test for module manifest parse-error tolerance
  - **Property 7: Module Manifest Parse-Error Tolerance**
  - Generate randomly malformed `manifest.json` payloads alongside valid payloads
  - Verify `list_modules()` produces `CockpitModule` records with `valid=False`
    and a populated `error` field rather than raising
  - Verify enumeration continues across the full glob
  - **Validates: Requirements 3.3**
  - _Requirements: 3_

- [ ] 18. Write property-based test for dry-run safety
  - **Property 8: Dry-Run Safety**
  - Generate random operations with `dry_run=True` across every write method
  - Verify the command runner is never invoked with a mutating command
  - Verify the file operator's `write()`, `delete()`, `chmod()`, `chown()` are
    never invoked
  - Verify all results have `DRY_RUN` status
  - **Validates: Requirements 2.5, 5.5, 6.6, 7.4**
  - _Requirements: 2, 5, 6, 7_

- [ ] 19. Write property-based test for elevation consistency
  - **Property 9: Elevation Consistency**
  - Generate random operations with `elevated=True`/`False`
  - Verify write operations pass `elevated=True` to both the runner and the file
    operator
  - Verify read operations never use elevation
  - **Validates: Requirements 9.1, 9.2, 9.3**
  - _Requirements: 9_

- [ ] 20. Write property-based test for status mapping correctness
  - **Property 10: Status Mapping Correctness**
  - Generate random exit codes from the mock command runner
  - Verify `CockpitActionResult.status` is `SUCCESS` for exit code `0` and
    `FAILED` otherwise (excluding skip/dry-run cases)
  - **Validates: Requirements 2.3, 2.4**
  - _Requirements: 2_

- [ ] 21. Write property-based test for version query not-installed mapping
  - **Property 11: Version Query Not-Installed Mapping**
  - Generate randomised exit codes including `127` / not-found signals
  - Verify `CockpitNotInstalledError` is raised for not-found signals and
    `CockpitQueryError` for any other non-zero exit code
  - **Validates: Requirements 8.3, 8.4**
  - _Requirements: 8_

- [ ] 22. Write unit tests for `get_socket_state()`
  - Test: socket present, active, enabled — returns populated state with listen
    entries
  - Test: socket unit not present — returns `present=False` with other fields
    unset
  - Test: unexpected `systemctl` failure — raises `CockpitQueryError`
  - _Requirements: 1_

- [ ] 23. Write unit tests for `lifecycle_action()`
  - Test: `start` when socket inactive — runner called with
    `systemctl start cockpit.socket`, returns `SUCCESS`
  - Test: `start` when socket already active — returns `SKIPPED`, runner not
    called
  - Test: `enable`/`disable`/`stop` variants cover their respective skip paths
  - Test: `restart` always invokes the runner when the socket is active
  - Test: failure path — returns `FAILED` with exit code and stderr
  - Test: dry-run mode — returns `DRY_RUN`, runner not called
  - Test: elevated mode — runner called with `elevated=True`
  - _Requirements: 2, 9_

- [ ] 24. Write unit tests for `list_modules()`
  - Test: valid manifests — returns populated `CockpitModule` list with correct
    `name`, `version`, `priority`, `requires`, `path`
  - Test: malformed manifest — record produced with `valid=False` and populated
    `error`; other valid records still returned
  - Test: `/usr/share/cockpit/` directory absent — returns empty list
  - _Requirements: 3_

- [ ] 25. Write unit tests for `get_listen_port()`
  - Test: `systemctl show` returns `Listen=Stream:0.0.0.0:9090` — returns
    `CockpitListen` with single entry
  - Test: `systemctl show` returns no `Listen=Stream` — falls back to reading
    `/usr/lib/systemd/system/cockpit.socket` and drop-ins
  - Test: both sources fail — raises `CockpitQueryError`
  - _Requirements: 4_

- [ ] 26. Write unit tests for `set_listen_port()` and `remove_listen_dropin()`
  - Test: drop-in absent — runner not called for restart (delegated out); file
    operator `write()` called with correct path, content, mode, owner, group,
    and elevation; returns `SUCCESS` with a note about `daemon-reload`
  - Test: drop-in already byte-identical — returns `SKIPPED`, `write()` not
    called
  - Test: dry-run mode — returns `DRY_RUN`, `write()`/`delete()` not called
  - Test: `remove_listen_dropin()` with file present — `delete()` called with
    elevation, returns `SUCCESS`
  - Test: `remove_listen_dropin()` with file absent — returns `SKIPPED`,
    `delete()` not called
  - _Requirements: 5, 9_

- [ ] 27. Write unit tests for `get_config()`, `set_config()`, `delete_config()`
  - Test: `get_config` returns value for existing section/key
  - Test: `get_config` returns `None` for missing key or missing section
  - Test: `set_config` creates the file and `/etc/cockpit/` directory when absent
  - Test: `set_config` preserves comments and the ordering of unrelated keys
  - Test: `set_config` returns `SKIPPED` when the current value already matches
  - Test: `delete_config` removes the key line
  - Test: `delete_config` removes the `[section]` header when the section
    becomes empty
  - Test: dry-run mode on set and delete — returns `DRY_RUN`, `write()` not
    called
  - _Requirements: 6, 9_

- [ ] 28. Write unit tests for `install_banner()`
  - Test: banner file absent — `write()` called with correct path, content,
    mode, owner, group, and elevation; returns `SUCCESS`
  - Test: banner file already byte-identical — returns `SKIPPED`, `write()` not
    called
  - Test: dry-run mode — returns `DRY_RUN`, `write()` not called
  - _Requirements: 7, 9_

- [ ] 29. Write unit tests for `get_version()`
  - Test: `cockpit-bridge --version` succeeds — returns captured version string
  - Test: binary not found — raises `CockpitNotInstalledError`
  - Test: other non-zero exit code — raises `CockpitQueryError`
  - _Requirements: 8_

- [ ] 30. Checkpoint - Ensure all tests pass
  - Run all unit tests and property-based tests
  - Verify all properties pass with 100+ iterations
  - Ensure all tests pass, ask the user if questions arise

