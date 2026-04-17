# Implementation Plan

- [ ] 1. Create the `containermanagement` module file
  - Create `source/packages/automojo/sysman/linux/redhat/containermanagement.py`
  - Add module-level docstring describing purpose, coordinator-driven design, and
    the relationships to `package-management`, `firewall-management`, and
    `service-management`
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12_

- [ ] 2. Define status enum and result dataclasses
  - Implement `ContainerActionStatus` enum with values: `SUCCESS`, `FAILED`,
    `SKIPPED`, `DRY_RUN`
  - Implement `ContainerActionResult` dataclass with fields: `status`, `target`,
    `image_id`, `container_id`, `reclaimed_count`, `reclaimed_bytes`, `exit_code`,
    `stdout`, `stderr`, `command`
  - _Requirements: 1, 2, 3, 4, 6, 7, 8, 9_

- [ ] 3. Define domain model dataclasses
  - Implement `ContainerImage` with fields: `id`, `names`, `digest`, `created`,
    `size`
  - Implement `ContainerInfo` with fields: `id`, `name`, `image`, `status`,
    `created`, `started`, `restart_count`, `ports`, `mounts`, `env`
  - Implement `PodmanInfo` with fields: `version`, `api_version`, `graph_root`,
    `storage_driver`, `rootless`, `cgroup_version`, `registries`
  - Implement `RegistryEntry` and `RegistryConfig` with unqualified-search and
    per-registry entries
  - _Requirements: 2, 5, 7, 10_

- [ ] 4. Define typed exceptions
  - Implement `ContainerQueryError` with `exit_code`, `stderr`, and raw-stdout
    attributes
  - Implement `ContainerScopeMismatchError` with `rootless` and `scope` attributes
  - _Requirements: 5, 10, 11_

- [ ] 5. Implement `ContainerManagerConfig` dataclass
  - Add `dry_run: bool = False` field
  - Add `elevated: bool = False` field
  - Add `rootless_default: bool = False` field
  - _Requirements: 1.7, 2.6, 3.6, 4.7, 6.6, 7.5, 8.5, 9.5, 11, 12_

- [ ] 6. Implement `ContainerManager.__init__` plus `list_containers`, `list_images`, `query_info`
  - Accept `ICommandRunner`, `IFileOperator`, and `ContainerManagerConfig` as
    constructor arguments; initialise every instance variable in `__init__`
  - Implement `list_containers()` using `podman ps -a --format json` and parse
    into `ContainerInfo` records
  - Implement `list_images()` using `podman images --format json` and parse into
    `ContainerImage` records
  - Implement `query_info()` using `podman info --format json` and parse into a
    `PodmanInfo` record
  - Reclassify `JSONDecodeError` and unexpected exit codes as
    `ContainerQueryError`
  - _Requirements: 2.1, 2.2, 5.1, 5.3, 5.5, 10_

- [ ] 7. Implement image capabilities — `pull_image` and `remove_image`
  - Implement `pull_image()` with optional `creds` and `authfile`; skip when the
    resolved digest is already present; honour `dry_run` and rootless/rootful
    elevation rules; redact credentials on the returned argv
  - Implement `remove_image()`; call `list_containers()` to detect dependents;
    without `force=True` return `FAILED` naming dependents; with `force=True`
    pass `-f`; skip when the image does not exist
  - _Requirements: 1, 2.3, 2.4, 2.5, 2.6_

- [ ] 8. Implement `inspect_container`
  - Build `podman inspect --format json <name>` argv
  - Return `None` when the container is absent (exit code 125 / `no such`)
  - Raise `ContainerQueryError` on any other non-zero exit or JSON parse error
  - Parse the single record into a `ContainerInfo`
  - _Requirements: 5.2, 5.3, 5.4, 5.5_

- [ ] 9. Implement container lifecycle — `run_container` and create/replace logic
  - Build `podman run -d --name <name> <structured-args> <image>` argv from the
    structured argument set (env vars, published ports, mounts, network, restart
    policy, extra args)
  - Detect an existing container with the same name via `inspect_container()`;
    without `replace=True` return `FAILED`; with `replace=True` first invoke
    `podman rm -f <name>`
  - Support `interactive=True` by switching `-d` to `-it` and enabling stdio
    pass-through on the runner when available
  - Honour `dry_run`; propagate elevation per rootless/rootful rules
  - _Requirements: 3_

- [ ] 10. Implement lifecycle actions — `start/stop/restart/pause/unpause/kill/remove_container`
  - Build `podman <action> <name>` argv for each lifecycle verb
  - For `stop_container`, when `timeout` is an integer, insert `-t <n>`
  - For `kill_container`, when `signal` is provided, insert `-s <sig>`
  - For `remove_container`, pass `-f` only when `force=True`
  - Call `inspect_container()` to detect already-in-desired-state and
    not-found cases; return `SKIPPED` in both
  - Honour `dry_run`; propagate elevation per rootless/rootful rules
  - _Requirements: 4_

- [ ] 11. Implement registry login/logout with credential redaction
  - Implement `login_registry()` using
    `podman login --username <user> --password-stdin <registry>`
  - Pass the password via `stdin_text` on `ICommandRunner.run()`; the password
    SHALL NEVER appear on argv
  - Redact username to `***` on the returned `command` unless
    `log_username=True`; scrub password and username substrings out of `stderr`
  - Implement `logout_registry()` using `podman logout <registry>`
  - Honour `dry_run`; propagate elevation per rootless/rootful rules
  - _Requirements: 6_

- [ ] 12. Implement registry configuration reads and drop-in management
  - Implement `read_registry_config()`: read `/etc/containers/registries.conf`,
    enumerate `/etc/containers/registries.conf.d/*.conf`, and merge into one
    `RegistryConfig`
  - Implement `write_registry_dropin()`: compute destination path under
    `/etc/containers/registries.conf.d/<name>.conf`; if existing content is
    byte-for-byte equal return `SKIPPED`; otherwise call
    `IFileOperator.write_text` with `mode=0o644`, `owner="root"`, `group="root"`,
    `elevated=True`
  - Implement `remove_registry_dropin()`: if the file is absent return
    `SKIPPED`; otherwise call `IFileOperator.remove` with `elevated=True`
  - Honour `dry_run` on both mutating methods
  - _Requirements: 7_

- [ ] 13. Implement Quadlet install and remove
  - Implement `install_quadlet(name, body, scope)` where `scope` is `"system"` or
    `"user"`
  - For `scope="system"`: destination is `/etc/containers/systemd/<name>.container`,
    write with `elevated=True`, `mode=0o644`
  - For `scope="user"`: destination is
    `~/.config/containers/systemd/<name>.container`, write with `elevated=False`,
    `mode=0o644`
  - Reject rootful/user and rootless/system combinations by raising
    `ContainerScopeMismatchError` before any write
  - Return `SKIPPED` on byte-identical content; return `SUCCESS` with a note that
    the coordinator must invoke `service-management` for `systemctl daemon-reload`
  - Implement `remove_quadlet(name, scope)`: delete the scope-correct path or
    return `SKIPPED` if absent
  - Honour `dry_run` on both methods
  - _Requirements: 8, 11.3_

- [ ] 14. Implement prune operations — `prune_containers`, `prune_images`, `prune_volumes`
  - Build `podman container prune -f`, `podman image prune -f [-a]`, and
    `podman volume prune -f`
  - For `prune_images()`, append `-a` only when `include_unreferenced=True`
  - Parse the reclaimed count and byte total from stdout and place into
    `reclaimed_count` and `reclaimed_bytes` on the returned result
  - Honour `dry_run`; propagate elevation per rootless/rootful rules
  - _Requirements: 9_

- [ ] 15. Implement the credential redaction helper
  - Add a single module-private function (e.g. `_redact_credentials`) that takes
    the raw argv and the credential material and returns a copy of argv with the
    sensitive fields replaced by `***`
  - Add a second helper that scrubs username and password substrings from an
    arbitrary string (used on `stderr` and aspect payloads)
  - Call the helpers from `pull_image()`, `login_registry()`, and
    `logout_registry()` before any `ContainerActionResult` is returned and before
    any aspect payload is forwarded
  - _Requirements: 1.3, 6.3, 6.5_

- [ ] 16. Create test module and mock infrastructure
  - Create
    `source/testroots/testbase/automojo/tests/sysman/linux/redhat/test_containermanagement.py`
  - Implement `MockCommandRunner` that records every argv, `elevated` flag, and
    `stdin_text` value and returns configurable `RunResult` values per argv shape
  - Implement `MockFileOperator` that records reads, writes, and deletes with
    the `elevated` flag and returns configurable content per path
  - Set up hypothesis testing framework configuration
  - Create test fixtures for local-rootful, local-rootless, and SSH-rootful
    scenarios
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12_

- [ ] 17. Write property-based test for pull-at-digest idempotency
  - **Property 1: Pull-at-Digest Idempotency**
  - Generate random `repo@sha256:<hex>` digests; seed `list_images()` so the
    digest is already present
  - Verify `pull_image()` returns `SKIPPED` and the runner is not invoked
  - **Validates: Requirements 1.4**
  - _Requirements: 1_

- [ ] 18. Write property-based test for remove image without dependents
  - **Property 2: Remove Image Without Dependents**
  - Generate image ids with and without dependents
  - Verify `force=False` skips removal and returns `FAILED` when dependents
    exist, and that `-f` is never present on the argv
  - Verify `force=True` produces argv containing `-f`
  - **Validates: Requirements 2.3, 2.4**
  - _Requirements: 2_

- [ ] 19. Write property-based test for run conflict detection
  - **Property 3: Run Conflict Detection**
  - Generate container names where a same-named container already exists
  - Verify `replace=False` returns `FAILED` and runner is not asked to run
  - Verify `replace=True` first issues `podman rm -f <name>` then `podman run`
  - **Validates: Requirements 3.2**
  - _Requirements: 3_

- [ ] 20. Write property-based test for lifecycle idempotency
  - **Property 4: Lifecycle Idempotency**
  - Generate pairs of (requested action, current state) for all lifecycle verbs
  - Verify no-op pairs and absent-container cases return `SKIPPED` without
    invoking the runner
  - **Validates: Requirements 4.4, 4.5**
  - _Requirements: 4_

- [ ] 21. Write property-based test for stop timeout propagation
  - **Property 5: Stop Timeout Propagation**
  - Generate random integer timeouts and `None`
  - Verify argv contains `-t <n>` exactly when `timeout` is an integer
  - **Validates: Requirements 4.2**
  - _Requirements: 4_

- [ ] 22. Write property-based test for credential redaction
  - **Property 6: Credential Redaction Never Leaks**
  - Generate random usernames and passwords (ASCII, with punctuation)
  - For `login_registry()`: verify password is delivered via `stdin_text` and is
    absent from `command`, `stdout`, and `stderr` on the returned result
  - For `pull_image(creds=...)`: verify `command` has `:***` in place of the
    password
  - **Validates: Requirements 1.3, 6.1, 6.3, 6.5**
  - _Requirements: 1, 6_

- [ ] 23. Write property-based test for registry drop-in byte-identical skip
  - **Property 7: Registry Drop-in Byte-Identical Skip**
  - Generate drop-in names and TOML bodies; seed `MockFileOperator` with
    matching existing content
  - Verify `write_registry_dropin()` returns `SKIPPED` and no write is issued
  - Verify differing content triggers a write with `elevated=True`,
    `mode=0o644`, `owner="root"`, `group="root"`
  - **Validates: Requirements 7.3**
  - _Requirements: 7_

- [ ] 24. Write property-based test for Quadlet byte-identical skip
  - **Property 8: Quadlet Byte-Identical Skip**
  - Generate Quadlet bodies and scopes; seed `MockFileOperator` with matching
    existing content at the scope-correct path
  - Verify `install_quadlet()` returns `SKIPPED` and no write is issued
  - **Validates: Requirements 8.3**
  - _Requirements: 8_

- [ ] 25. Write property-based test for rootless vs rootful scope mismatch
  - **Property 9: Rootless vs Rootful Scope Mismatch**
  - Generate combinations of (`rootless`, Quadlet `scope`)
  - Verify rootful+user and rootless+system raise
    `ContainerScopeMismatchError` before any runner or file-op call
  - **Validates: Requirements 11.3**
  - _Requirements: 11_

- [ ] 26. Write property-based test for dry-run safety
  - **Property 10: Dry-Run Safety**
  - Generate random write operations with `dry_run=True`
  - Verify the command runner is never asked to run, and the file operator is
    never asked to write or remove, and all results have `DRY_RUN` status
  - **Validates: Requirements 1.7, 2.6, 3.6, 4.7, 6.6, 7.5, 8.5, 9.5**
  - _Requirements: 1, 2, 3, 4, 6, 7, 8, 9_

- [ ] 27. Write property-based test for elevation consistency
  - **Property 11: Elevation Consistency (Rootful Only)**
  - Generate rootless/rootful and `config.elevated` combinations
  - Verify rootful mutating calls receive `elevated=True` when configured, and
    rootless calls always receive `elevated=False`
  - **Validates: Requirements 11.1, 11.2, 12.1, 12.2, 12.3**
  - _Requirements: 11, 12_

- [ ] 28. Write property-based test for status mapping correctness
  - **Property 12: Status Mapping Correctness**
  - Generate random exit codes from the command runner
  - Verify `0` maps to `SUCCESS`, non-zero maps to `FAILED` for non-skipped,
    non-dry-run calls
  - **Validates: Requirements 1.5, 1.6, 3.4, 3.5, 4.6, 6.4, 6.5, 9.4**
  - _Requirements: 1, 3, 4, 6, 9_

- [ ] 29. Write property-based test for JSON parse stability
  - **Property 13: JSON Parse Stability**
  - Generate malformed JSON strings and empty strings
  - Verify `list_images()`, `list_containers()`, `inspect_container()`, and
    `query_info()` all raise `ContainerQueryError` and never silently return
    empty collections
  - **Validates: Requirements 5.5, 10.3**
  - _Requirements: 5, 10_

- [ ] 30. Write unit tests for `pull_image()`
  - Test: image absent — runner called with `podman pull <ref>`, returns
    `SUCCESS` with image id and digest
  - Test: image at exact digest already present — returns `SKIPPED`, runner not
    called
  - Test: pull failure — returns `FAILED` with exit code and stderr
  - Test: dry-run mode — returns `DRY_RUN`, runner not called
  - Test: with `creds="user:pass"` — argv on result has `:***`, password never in
    argv
  - Test: rootless — runner called with `elevated=False`
  - _Requirements: 1_

- [ ] 31. Write unit tests for `list_images()` and `remove_image()`
  - Test: list parses `podman images --format json` into `ContainerImage` records
  - Test: malformed JSON raises `ContainerQueryError`
  - Test: remove without dependents — argv lacks `-f`, returns `SUCCESS`
  - Test: remove with dependents and `force=False` — returns `FAILED` naming
    dependents
  - Test: remove with `force=True` — argv has `-f`, returns `SUCCESS`
  - Test: remove non-existent image — returns `SKIPPED`, runner not called
  - Test: dry-run remove — returns `DRY_RUN`
  - _Requirements: 2_

- [ ] 32. Write unit tests for `run_container()`
  - Test: runs container with structured args and returns new container id
  - Test: name collision without `replace` — returns `FAILED`, runner not called
  - Test: name collision with `replace=True` — first invokes `podman rm -f`,
    then `podman run`
  - Test: `interactive=True` — argv contains `-it`, not `-d`
  - Test: run failure — returns `FAILED` with stderr
  - Test: dry-run — returns `DRY_RUN`
  - _Requirements: 3_

- [ ] 33. Write unit tests for lifecycle actions
  - Test: each verb invokes `podman <verb> <name>` and returns `SUCCESS`
  - Test: `stop` with `timeout=10` — argv contains `-t 10`
  - Test: already-in-desired-state — returns `SKIPPED`, runner not called
  - Test: container not found — returns `SKIPPED`, runner not called
  - Test: `remove_container(force=True)` — argv contains `-f`
  - Test: dry-run on each verb — returns `DRY_RUN`
  - _Requirements: 4_

- [ ] 34. Write unit tests for `list_containers()` and `inspect_container()`
  - Test: list parses `podman ps -a --format json` into records
  - Test: inspect returns `ContainerInfo` for existing container
  - Test: inspect returns `None` for missing container
  - Test: inspect non-exit-125 error raises `ContainerQueryError`
  - Test: malformed JSON raises `ContainerQueryError`
  - _Requirements: 5_

- [ ] 35. Write unit tests for `login_registry()` and `logout_registry()`
  - Test: login — argv contains `--username <user> --password-stdin <registry>`;
    `stdin_text` equals the password; `command` on result has username as `***`;
    password is nowhere in result
  - Test: login failure — returns `FAILED` with redacted stderr
  - Test: logout — argv contains `podman logout <registry>`; returns `SUCCESS`
  - Test: dry-run on both — returns `DRY_RUN`
  - Test: `log_username=True` — username is preserved on `command`
  - _Requirements: 6_

- [ ] 36. Write unit tests for registry configuration management
  - Test: `read_registry_config()` merges base file and drop-ins into one record
  - Test: `write_registry_dropin()` writes to correct path with mode `0o644`,
    owner `root`, group `root`, `elevated=True`, returns `SUCCESS`
  - Test: byte-identical existing content — returns `SKIPPED`, no write issued
  - Test: `remove_registry_dropin()` — file absent returns `SKIPPED`; present
    deletes with `elevated=True`
  - Test: dry-run on both mutating methods — returns `DRY_RUN`
  - _Requirements: 7_

- [ ] 37. Write unit tests for Quadlet install and remove
  - Test: `install_quadlet(scope="system")` — writes to
    `/etc/containers/systemd/<name>.container` with `elevated=True`; returns
    `SUCCESS` with a daemon-reload note
  - Test: `install_quadlet(scope="user")` — writes to
    `~/.config/containers/systemd/<name>.container` with `elevated=False`
  - Test: byte-identical existing content — returns `SKIPPED`
  - Test: rootful command with `scope="user"` — raises
    `ContainerScopeMismatchError` before any write
  - Test: rootless command with `scope="system"` — raises
    `ContainerScopeMismatchError` before any write
  - Test: `remove_quadlet()` — absent file returns `SKIPPED`; present deletes
  - Test: dry-run on both — returns `DRY_RUN`
  - _Requirements: 8, 11.3_

- [ ] 38. Write unit tests for prune operations
  - Test: `prune_containers()` — argv is `podman container prune -f`, returns
    `SUCCESS` with reclaimed counts
  - Test: `prune_images(include_unreferenced=False)` — argv lacks `-a`
  - Test: `prune_images(include_unreferenced=True)` — argv has `-a`
  - Test: `prune_volumes()` — argv is `podman volume prune -f`
  - Test: dry-run on each — returns `DRY_RUN`
  - _Requirements: 9_

- [ ] 39. Write unit tests for `query_info()`
  - Test: happy path — parses `podman info --format json` into a `PodmanInfo`
    record with every documented field populated
  - Test: failure — raises `ContainerQueryError` with exit code and stderr
  - Test: malformed JSON — raises `ContainerQueryError`
  - _Requirements: 10_

- [ ] 40. Write unit tests for rootless vs rootful elevation behaviour
  - Test: rootful mutating call with `config.elevated=True` — runner receives
    `elevated=True`
  - Test: rootless mutating call with `config.elevated=True` — runner still
    receives `elevated=False`
  - Test: read-only calls (`list_*`, `inspect_container`, `query_info`) — runner
    receives `elevated=False`
  - Test: rootful write under `/etc/containers/` — file operator receives
    `elevated=True`
  - Test: rootless write under `~/.config/containers/` — file operator receives
    `elevated=False`
  - _Requirements: 11, 12_

- [ ] 41. Checkpoint - Ensure all tests pass
  - Run all unit tests and property-based tests
  - Verify all properties pass with 100+ iterations
  - Ensure all tests pass, ask the user if questions arise

