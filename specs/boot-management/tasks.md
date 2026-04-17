# Implementation Plan

- [ ] 1. Create the `bootmanagement` module file
  - Create `source/packages/automojo/sysman/linux/redhat/bootmanagement.py`
  - Add module-level docstring describing purpose and coordinator-driven design
  - Declare imports for `ICommandRunner`, `IFileOperator`, `RunResult`, `dataclass`,
    `Enum`, and typing primitives
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12_

- [ ] 2. Define status enum, firmware enum, and result dataclasses
  - Implement `BootActionStatus` enum with values `SUCCESS`, `FAILED`, `SKIPPED`,
    `DRY_RUN`
  - Implement `BootFirmware` enum with values `BIOS`, `UEFI`
  - Implement `BootActionResult` dataclass with fields `status`, `action`,
    `exit_code`, `stdout`, `stderr`, `command`, `target_path`, `note`
  - Implement `BootTarget` dataclass with field `unit`
  - Implement `KernelEntry` dataclass with fields `index`, `kernel`, `args`,
    `root`, `initrd`, `title`
  - Implement `KernelCmdLine` dataclass with fields `raw` (str) and `parsed`
    (`dict[str, str | None]`), initialized via `field(default_factory=dict)` — never a
    mutable default argument
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11_

- [ ] 3. Define typed exceptions
  - Implement `BootQueryError` with `exit_code` and `stderr` attributes
  - _Requirements: 1, 3, 4, 7, 11_

- [ ] 4. Implement `BootManagerConfig` dataclass
  - Add `dry_run: bool = False` field
  - Add `elevated: bool = False` field
  - _Requirements: 2.6, 4.7, 5.6, 6.5, 8.6, 9.6, 10.6, 12_

- [ ] 5. Implement `BootManager.__init__`, `get_boot_firmware()`, and `get_default_target()`
  - Accept `ICommandRunner`, `IFileOperator`, and `BootManagerConfig` as constructor
    arguments; store as instance attributes in `__init__` only
  - Implement `get_boot_firmware()` by probing `/sys/firmware/efi` via the file
    operator; return `BootFirmware.UEFI` on exists, `BootFirmware.BIOS` on not-found,
    raise `BootQueryError` on any other probe error
  - Implement `get_default_target()` by invoking `systemctl get-default`; parse the
    unit name from stdout; raise `BootQueryError` on non-zero exit
  - _Requirements: 1, 11_

- [ ] 6. Implement `set_default_target()`
  - Call `get_default_target()` to determine current state; return `SKIPPED` when the
    requested target already matches
  - Build `systemctl set-default <target>` command
  - Honour `dry_run` flag; return `DRY_RUN` result without invoking the runner
  - Pass `elevated` flag to runner; map result to `BootActionResult` with
    `SUCCESS`/`FAILED` based on exit code
  - _Requirements: 2, 12_

- [ ] 7. Implement `list_kernels()`, `get_default_kernel()`, and `set_default_kernel()`
  - Implement `list_kernels()` by invoking `grubby --info=ALL`, parsing the
    repeated `index=/kernel=/args=/root=/initrd=/title=` blocks into `KernelEntry`
    records; empty stdout returns an empty list; non-zero exit raises
    `BootQueryError`
  - Implement `get_default_kernel()` by invoking `grubby --default-kernel`, then
    reusing `list_kernels()` output (or `grubby --info=<path>`) to build the full
    `KernelEntry`
  - Implement `set_default_kernel(path=None, index=None)`:
    - Reject calls where both `path` and `index` are `None` or both are provided (use
      explicit `is None` / `is not None` checks, never truthy shortcuts)
    - Call `get_default_kernel()` to check current state; return `SKIPPED` when
      already default
    - Build `grubby --set-default=<path>` or `grubby --set-default-index=<n>`
    - Honour `dry_run`; pass `elevated` flag to the runner; map result to
      `BootActionResult`
  - _Requirements: 3, 4, 12_

- [ ] 8. Implement `add_kernel_args()` and `remove_kernel_args()`
  - For `add_kernel_args(args, kernel_path=None)`:
    - Query targeted kernel(s) via `list_kernels()` or `grubby --info=<path>`
    - Return `SKIPPED` when every targeted kernel already contains every argument
      with the same value
    - Build `grubby --update-kernel=ALL --args="<args>"` or
      `grubby --update-kernel=<path> --args="<args>"`
    - Honour `dry_run`; pass `elevated` flag; map result to `BootActionResult`
  - For `remove_kernel_args(args, kernel_path=None)`:
    - Query targeted kernel(s); return `SKIPPED` when no targeted kernel contains any
      of the supplied args
    - Build `grubby --update-kernel=ALL --remove-args="<args>"` or
      `grubby --update-kernel=<path> --remove-args="<args>"`
    - Honour `dry_run`; pass `elevated` flag; map result to `BootActionResult`
  - _Requirements: 5, 6, 12_

- [ ] 9. Implement `get_runtime_cmdline()` and `get_kernel_cmdline()`
  - `get_runtime_cmdline()`: read `/proc/cmdline` via the file operator (no
    elevation); split on whitespace; build `KernelCmdLine(raw, parsed)` where
    `parsed` maps `name` → `value` (or `None` for bare flags like `quiet`); raise
    `BootQueryError` on read failure
  - `get_kernel_cmdline(kernel_path)`: invoke `grubby --info=<kernel_path>`; extract
    the `args=` field; build a `KernelCmdLine`; raise `BootQueryError` on non-zero
    exit
  - _Requirements: 7_

- [ ] 10. Implement `regenerate_grub_config()`
  - When `output_path` is not `None`, use it verbatim; otherwise call
    `get_boot_firmware()` and select `/boot/grub2/grub.cfg` for `BIOS` or
    `/boot/efi/EFI/redhat/grub.cfg` for `UEFI`
  - Build `grub2-mkconfig -o <output_path>` command
  - Honour `dry_run`; pass `elevated` flag; map result to `BootActionResult` and
    populate `target_path` with the chosen output path
  - _Requirements: 8, 11, 12_

- [ ] 11. Implement `remove_kernel_entry()`
  - Read `/proc/cmdline` via the file operator and extract `BOOT_IMAGE=` to
    determine the running kernel path; if the supplied `kernel_path` matches, return
    `FAILED` with an explanatory message and do not invoke the runner
  - Call `get_default_kernel()`; if the supplied `kernel_path` matches the default,
    return `FAILED` with an explanatory message and do not invoke the runner
  - Call `list_kernels()`; when the supplied path is not present, return `SKIPPED`
  - Build `grubby --remove-kernel=<kernel_path>` command
  - Honour `dry_run`; pass `elevated` flag; map result to `BootActionResult`
  - _Requirements: 9, 12_

- [ ] 12. Implement `/etc/default/grub` read/set/delete helpers
  - Implement `get_grub_setting(key)`: read `/etc/default/grub` via the file
    operator; parse `KEY=value` lines preserving quoting; return the value (including
    surrounding quotes as stored) or `None` when absent
  - Implement `set_grub_setting(key, value)`:
    - Read current value via `get_grub_setting(key)`; when already equal, return
      `SKIPPED`
    - Detect existing quoting style (double, single, unquoted) and preserve it when
      rewriting the line; append a new unquoted line when the key is absent
    - Honour `dry_run`; call `write_text` with `mode=0o644`, `owner="root"`,
      `group="root"`, `elevated=<config.elevated>`; populate
      `BootActionResult.note` with a message that `grub2-mkconfig` is required for
      the change to take effect
  - Implement `delete_grub_setting(key)`:
    - When the key is not present, return `SKIPPED`
    - Remove the matching line and write the file back with the same mode/owner/
      group/elevation semantics; honour `dry_run`
  - Build error messages using a list of lines joined with `os.linesep.join()` when
    reporting malformed lines
  - _Requirements: 10, 12_

- [ ] 13. Create test module and mock infrastructure
  - Create `source/testroots/testbase/automojo/tests/sysman/linux/redhat/test_bootmanagement.py`
  - Implement `MockCommandRunner` that returns configurable `RunResult` values and
    records every invocation (args, elevated)
  - Implement `MockFileOperator` that records reads/writes/exists probes and returns
    configurable responses including simulated not-found and I/O errors
  - Set up hypothesis testing framework configuration
  - Create reusable fixtures: sample `grubby --info=ALL` output, sample
    `/proc/cmdline`, sample `/etc/default/grub` contents
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12_

- [ ] 14. Write property-based test for idempotent `set_default_target`
  - **Property 1: Idempotent `set_default_target`**
  - Generate random target unit names
  - Configure `MockCommandRunner` so that `systemctl get-default` returns the same
    target; verify `set_default_target()` returns `SKIPPED` and the set command is
    never invoked
  - **Validates: Requirements 2.2**
  - _Requirements: 2_

- [ ] 15. Write property-based test for idempotent `set_default_kernel`
  - **Property 2: Idempotent `set_default_kernel`**
  - Generate random kernel paths and indices
  - Verify that calling `set_default_kernel()` when the kernel is already default
    returns `SKIPPED` and does not invoke the set command
  - **Validates: Requirements 4.5**
  - _Requirements: 4_

- [ ] 16. Write property-based test for add-args no-op when present
  - **Property 3: Add-Args No-Op When Present**
  - Generate random kernel arg strings
  - Seed the mocked `grubby --info=ALL` output so that every kernel already contains
    the args; verify `add_kernel_args()` returns `SKIPPED`
  - **Validates: Requirements 5.3**
  - _Requirements: 5_

- [ ] 17. Write property-based test for remove-args no-op when absent
  - **Property 4: Remove-Args No-Op When Absent**
  - Generate random kernel arg strings
  - Seed mocked output so that no kernel contains the args; verify
    `remove_kernel_args()` returns `SKIPPED`
  - **Validates: Requirements 6.3**
  - _Requirements: 6_

- [ ] 18. Write property-based test for removing running or default kernel
  - **Property 5: Refusal to Remove Running or Default Kernel**
  - Generate random kernel paths; mark one as running (via `/proc/cmdline`
    `BOOT_IMAGE=`) and one as default (via `grubby --default-kernel`)
  - Verify `remove_kernel_entry()` returns `FAILED` for either case and does not
    invoke the removal command
  - **Validates: Requirements 9.2, 9.3**
  - _Requirements: 9_

- [ ] 19. Write property-based test for firmware-driven `grub2-mkconfig` path selection
  - **Property 6: Firmware-Driven `grub2-mkconfig` Path Selection**
  - Generate BIOS and UEFI probe outcomes and an optional explicit output path
  - Verify path selection: BIOS → `/boot/grub2/grub.cfg`, UEFI →
    `/boot/efi/EFI/redhat/grub.cfg`, explicit path wins unconditionally
  - **Validates: Requirements 8.1, 8.2, 8.3**
  - _Requirements: 8, 11_

- [ ] 20. Write property-based test for dry-run safety
  - **Property 7: Dry-Run Safety**
  - Generate random inputs for every mutating method with `dry_run=True`
  - Verify the command runner is never invoked with a mutating command, the file
    operator's `write_text` is never invoked, and each result carries
    `DRY_RUN` status
  - **Validates: Requirements 2.6, 4.7, 5.6, 6.5, 8.6, 9.6, 10.6**
  - _Requirements: 2, 4, 5, 6, 8, 9, 10_

- [ ] 21. Write property-based test for elevation consistency
  - **Property 8: Elevation Consistency**
  - Generate random mutating and read operations with `elevated=True`/`False`
  - Verify mutating operations propagate `elevated` to both the runner and the file
    operator's `write_text`; verify read-only operations never request elevation
  - **Validates: Requirements 12.1, 12.2, 12.3**
  - _Requirements: 12_

- [ ] 22. Write property-based test for status mapping correctness
  - **Property 9: Status Mapping Correctness**
  - Generate random exit codes and stderr strings from the mocked runner
  - Verify exit code `0` maps to `SUCCESS`, non-zero maps to `FAILED` with the exit
    code and stderr preserved, and skip/dry-run paths preempt both
  - **Validates: Requirements 1.3, 2.4, 2.5, 3.4, 7.4, 8.4, 8.5, 11.4**
  - _Requirements: 1, 2, 3, 7, 8, 11_

- [ ] 23. Write property-based test for grub-setting idempotency and quoting preservation
  - **Property 10: Idempotent `set_grub_setting` with Quoting Preservation**
  - Generate random `(key, value, quoting_style)` triples
  - Verify `SKIPPED` when value already matches; verify that when a write occurs the
    produced line preserves the original double-/single-/unquoted style
  - **Validates: Requirements 10.2, 10.4**
  - _Requirements: 10_

- [ ] 24. Write property-based test for firmware probe classification
  - **Property 11: Firmware Probe Classification**
  - Generate `exists=True`, `exists=False`, and error outcomes for the
    `/sys/firmware/efi` probe
  - Verify `BootFirmware.UEFI`, `BootFirmware.BIOS`, and `BootQueryError` respectively
  - **Validates: Requirements 11.1, 11.2, 11.3, 11.4**
  - _Requirements: 11_

- [ ] 25. Write property-based test for command construction correctness
  - **Property 12: Command Construction Correctness**
  - Generate random inputs for every operation
  - Verify the command argument list passed to the runner matches the entries in the
    Command Mappings table
  - **Validates: Requirements 1.1, 2.1, 3.1, 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 6.1, 6.2, 7.1, 7.2, 8.1, 8.2, 9.1, 10.1, 10.2, 10.3, 11.1**
  - _Requirements: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11_

- [ ] 26. Write unit tests for `get_default_target()` and `set_default_target()`
  - Test: `get_default_target()` returns the parsed unit name on success
  - Test: `get_default_target()` raises `BootQueryError` on non-zero exit
  - Test: `set_default_target()` with a new target — runner called with correct args,
    returns `SUCCESS`
  - Test: `set_default_target()` when already default — returns `SKIPPED`, runner
    not called with the set command
  - Test: `set_default_target()` failure — returns `FAILED` with exit code and stderr
  - Test: `set_default_target()` dry-run — returns `DRY_RUN`, runner not called
  - Test: `set_default_target()` elevated — runner invoked with `elevated=True`
  - _Requirements: 1, 2_

- [ ] 27. Write unit tests for `list_kernels()`, `get_default_kernel()`, `set_default_kernel()`
  - Test: `list_kernels()` parses a multi-entry `grubby --info=ALL` stdout into
    `KernelEntry` records with correct fields
  - Test: `list_kernels()` returns `[]` for empty stdout
  - Test: `list_kernels()` raises `BootQueryError` on non-zero exit
  - Test: `get_default_kernel()` returns the matching `KernelEntry`
  - Test: `set_default_kernel(path=...)` — runner called with
    `grubby --set-default=<path>`
  - Test: `set_default_kernel(index=...)` — runner called with
    `grubby --set-default-index=<n>`
  - Test: `set_default_kernel()` when already default — returns `SKIPPED`
  - Test: neither `path` nor `index` supplied — raises a clear error; both supplied
    — raises a clear error
  - Test: dry-run and elevated flag propagation
  - _Requirements: 3, 4_

- [ ] 28. Write unit tests for `add_kernel_args()` and `remove_kernel_args()`
  - Test: add args to ALL — runner called with
    `grubby --update-kernel=ALL --args="..."`
  - Test: add args to specific path — runner called with
    `grubby --update-kernel=<path> --args="..."`
  - Test: add args when already present on every targeted kernel — returns
    `SKIPPED`, set command not invoked
  - Test: remove args from ALL — runner called with
    `grubby --update-kernel=ALL --remove-args="..."`
  - Test: remove args from specific path — correct command
  - Test: remove args when absent on every targeted kernel — returns `SKIPPED`
  - Test: dry-run and elevated flag propagation on both methods
  - _Requirements: 5, 6_

- [ ] 29. Write unit tests for `get_runtime_cmdline()` and `get_kernel_cmdline()`
  - Test: `get_runtime_cmdline()` returns the raw string and a parsed dict
    containing both `key=value` pairs and bare flags (mapped to `None`)
  - Test: `get_runtime_cmdline()` raises `BootQueryError` on file-operator read error
  - Test: `get_kernel_cmdline(path)` parses the `args=` line from `grubby --info`
    output
  - Test: `get_kernel_cmdline(path)` raises `BootQueryError` on non-zero exit
  - _Requirements: 7_

- [ ] 30. Write unit tests for `regenerate_grub_config()`
  - Test: BIOS firmware — runner called with
    `grub2-mkconfig -o /boot/grub2/grub.cfg`; `target_path` populated
  - Test: UEFI firmware — runner called with
    `grub2-mkconfig -o /boot/efi/EFI/redhat/grub.cfg`; `target_path` populated
  - Test: explicit `output_path` argument overrides firmware-based selection
  - Test: failure — returns `FAILED` with exit code and stderr
  - Test: dry-run — returns `DRY_RUN`, runner not called
  - Test: elevated flag propagation
  - _Requirements: 8, 11_

- [ ] 31. Write unit tests for `remove_kernel_entry()`
  - Test: removing a non-default, non-running kernel — runner called with
    `grubby --remove-kernel=<path>`, returns `SUCCESS`
  - Test: removing the running kernel — returns `FAILED`, removal command not
    invoked
  - Test: removing the default kernel — returns `FAILED`, removal command not
    invoked
  - Test: removing a path not in `list_kernels()` — returns `SKIPPED`
  - Test: dry-run — returns `DRY_RUN`, removal command not invoked
  - Test: elevated flag propagation
  - _Requirements: 9_

- [ ] 32. Write unit tests for `get_grub_setting()`, `set_grub_setting()`, `delete_grub_setting()`
  - Test: `get_grub_setting()` returns the value for a present key; returns `None`
    for an absent key
  - Test: `get_grub_setting()` preserves quoting in the returned value for
    double-quoted, single-quoted, and unquoted source lines
  - Test: `set_grub_setting()` with a new value — `write_text` called with
    `mode=0o644`, `owner="root"`, `group="root"`, `elevated=<config.elevated>`
  - Test: `set_grub_setting()` preserves existing quoting style on rewrite for each
    of double-quoted, single-quoted, and unquoted source lines
  - Test: `set_grub_setting()` appends an unquoted line when the key is absent
  - Test: `set_grub_setting()` when value already matches — returns `SKIPPED`, no
    write performed
  - Test: `set_grub_setting()` populates `note` with a `grub2-mkconfig` reminder
  - Test: `delete_grub_setting()` removes the matching line; returns `SKIPPED` when
    key absent
  - Test: dry-run — no write performed, `DRY_RUN` returned for both set and delete
  - _Requirements: 10, 12_

- [ ] 33. Write unit tests for `get_boot_firmware()`
  - Test: `/sys/firmware/efi` exists — returns `BootFirmware.UEFI`
  - Test: `/sys/firmware/efi` does not exist — returns `BootFirmware.BIOS`
  - Test: probe raises a non-not-found error — `BootQueryError` is raised
  - _Requirements: 11_

- [ ] 34. Checkpoint - Ensure all tests pass
  - Run all unit tests and property-based tests
  - Verify all properties pass with 100+ iterations
  - Ensure all tests pass, ask the user if questions arise
