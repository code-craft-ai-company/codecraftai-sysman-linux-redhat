# Implementation Plan — Container Management

Checked items (`[x]`) are implemented and covered by tests.  Unchecked
items (`[ ]`) identify remaining work.

## Module and Models

- [x] 1. Create the `containermanagement` module
  - `source/packages/ccai/sysman/linux/redhat/containermanagement.py`
    exists

- [x] 2. Define `ContainerActionStatus` enum (SUCCESS / FAILED / SKIPPED
  / DRY_RUN)

- [x] 3. Define `ContainerManagerConfig` dataclass
  - Fields: `dry_run`, `elevated`, `engine="podman"`

- [x] 4. Define `ContainerSpec` dataclass
  - Fields: `image`, `name`, `command`, `ports`, `env`, `volumes`,
    `detach=True`

- [x] 5. Define `ContainerActionResult` dataclass
  - Fields: `status`, `name`, `exit_code`, `stdout`, `stderr`, `command`

- [x] 6. Define `ContainerInfo` dataclass (id, name, image, state)

## ContainerManager — Implemented

- [x] 7. Implement `__init__` accepting runner + optional config

- [x] 8. Implement `pull_image(image)` — `<engine> pull <image>`

- [x] 9. Implement `run_container(spec)` — builds `<engine> run --name
  <name> [-d] [-p PORT ...] [-e KEY=VAL ...] [-v SRC:DST ...] <image>
  [command...]`

- [x] 10. Implement `stop_container(name)` — `<engine> stop <name>`

- [x] 11. Implement `remove_container(name, force=False)` — `<engine>
  rm [-f] <name>`

- [x] 12. Implement `list_containers(include_all=True)` — `<engine> ps
  [-a] --format=json`; parses JSON into `ContainerInfo` records;
  returns `[]` on non-zero exit or JSON error

- [x] 13. Implement `_dispatch` helper — dry-run short-circuit and
  runner-result-to-ContainerActionResult mapping

## ContainerManager — Gaps (specified but not implemented)

- [ ] 14. Image lifecycle beyond pull: `tag`, `push`, `rmi`, `inspect`,
  image-listing (`podman images --format=json`)

- [ ] 15. Registry management — read/write
  `/etc/containers/registries.conf`, `registries.d/`

- [ ] 16. Registry authentication — `podman login` / `logout` with
  credentials routed through `codecraftai-credentials`

- [ ] 17. Quadlet systemd-unit generation for containers
  (`podman generate systemd` or Quadlet `.container` files under
  `/etc/containers/systemd/`)

- [ ] 18. `start_container(name)` — start a stopped container (distinct
  from `run_container`)

- [ ] 19. `exec_in_container(name, args)` — `podman exec` wrapper

- [ ] 20. Volume/network object management (`podman volume create`,
  `podman network create`)

- [ ] 21. Idempotency: `run_container` SKIPPED when a container with
  the same name is already running; `stop_container` SKIPPED when
  already stopped

- [ ] 22. Sanity-check `engine` at construction time when not dry-run
  (`podman --version`) so failures surface early

## Tests — Implemented

- [x] 23. `test_pull_image_dispatches_podman`
- [x] 24. `test_run_container_builds_full_command`
- [x] 25. `test_list_containers_parses_json_output`

## Tests — Gaps

- [ ] 26. Test `stop_container` dispatch shape

- [ ] 27. Test `remove_container` with and without `force=True`

- [ ] 28. Test `list_containers(include_all=False)` — verify `-a` is
  NOT inserted

- [ ] 29. Test `list_containers` returns `[]` on non-zero exit and on
  invalid JSON

- [ ] 30. Test `run_container` with `detach=False` — verify `-d` is
  NOT added

- [ ] 31. Test dry-run short-circuits for every mutating method

- [ ] 32. Test failure mapping — runner non-zero exit → status FAILED
  with exit_code/stderr preserved

## Follow-ups

- [ ] 33. **codecraftai-* reuse check**: `codecraftai-credentials` for
  registry login (task 16); check `codecraftai-interop` for any container
  / podman helpers before expanding the wrapper.
