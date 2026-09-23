# Tests for the RHEL EncryptionManager facade.
#
# All tests use a ``_MockRunner`` that records every ``run`` call
# (argv + elevation flag + stdin payload) and returns pre-seeded
# :class:`RunResult` values keyed by argv tuple.  No live
# ``cryptsetup`` / ``systemd-cryptenroll`` / ``clevis`` binary is
# invoked.

from dataclasses import dataclass, field
from typing import Callable, Optional

from ccai import testbase

from ccai.sysman.linux.redhat.runners.models import RunResult

from ccai.sysman.linux.redhat.encryptionmanagement import (
    CryptFormatSpec,
    CrypttabEntry,
    EncryptionActionStatus,
    EncryptionCredentialError,
    EncryptionManager,
    EncryptionManagerConfig,
    EncryptionToolNotAvailableError,
    _parse_clevis_luks_list,
    _parse_crypttab,
    _parse_luks_dump,
    _parse_systemd_cryptenroll,
    _render_crypttab,
)


# ----------------------------------------------------------------------
# Mock runner / mock credential
# ----------------------------------------------------------------------


@dataclass
class _MockRunner:
    """
        Minimal :class:`ICommandRunner` stand-in that records every
        ``run`` call (argv + elevation flag + stdin payload) and
        returns pre-seeded :class:`RunResult` values keyed by argv
        tuple.
    """

    results: dict = field(default_factory=dict)
    default_result: Optional[RunResult] = None
    calls: list = field(default_factory=list)

    def run(
        self,
        args: list[str],
        *,
        elevated: bool = False,
        cwd: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
        timeout: Optional[float] = None,
        on_output: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        capture_output: bool = True,
        stdin: Optional[str] = None,
    ) -> RunResult:
        call_record = {
            "args": list(args),
            "elevated": elevated,
            "stdin": stdin,
        }
        self.calls.append(call_record)

        key = tuple(args)
        if key in self.results:
            rtnval = self.results[key]
            return rtnval

        if self.default_result is not None:
            rtnval = self.default_result
            return rtnval

        rtnval = RunResult(
            exit_code=0, stdout="", stderr="", command=list(args),
        )
        return rtnval

    def add_aspect(self, aspect) -> None:
        return

    def remove_aspect(self, aspect) -> None:
        return

    def get_metrics(self):
        rtnval = None
        return rtnval


@dataclass
class _MockCredential:
    """
        Minimal credential stand-in carrying a ``secret_bytes()``
        accessor.
    """

    secret: bytes

    def secret_bytes(self) -> bytes:
        rtnval = self.secret
        return rtnval


# ----------------------------------------------------------------------
# Common fixtures
# ----------------------------------------------------------------------


_DEVICE = "/dev/sda3"
_NAME = "cryptroot"
_CRYPTTAB_PATH = "/etc/crypttab"


_LUKS2_DUMP_OUTPUT = (
    "LUKS header information\n"
    "\n"
    "Version:        2\n"
    "Epoch:          5\n"
    "Metadata area:  16384 bytes\n"
    "Keyslots area:  16744448 bytes\n"
    "UUID:           b6b30b58-eb8d-4d9c-9b78-37dc1ce6da3a\n"
    "Label:          (no label)\n"
    "Subsystem:      (no subsystem)\n"
    "Flags:          (no flags)\n"
    "\n"
    "Data segments:\n"
    "  0: crypt\n"
    "    offset: 16777216 [bytes]\n"
    "    length: (whole device)\n"
    "    cipher: aes-xts-plain64\n"
    "    sector: 512 [bytes]\n"
    "\n"
    "Keyslots:\n"
    "  0: luks2\n"
    "        Key:        512 bits\n"
    "        Priority:   normal\n"
    "        Cipher:     aes-xts-plain64\n"
    "        PBKDF:      argon2id\n"
    "  1: luks2\n"
    "        Key:        512 bits\n"
    "        Priority:   normal\n"
    "        Cipher:     aes-xts-plain64\n"
    "        PBKDF:      argon2id\n"
    "Tokens:\n"
    "  0: systemd-tpm2\n"
    "        Keyslot:    1\n"
    "Digests:\n"
    "  0: pbkdf2\n"
    "        Hash:       sha256\n"
)


_LUKS1_DUMP_OUTPUT = (
    "LUKS header information for /dev/sda3\n"
    "\n"
    "Version:        1\n"
    "Cipher name:    aes\n"
    "Cipher mode:    xts-plain64\n"
    "Hash spec:      sha256\n"
    "Payload offset: 4096\n"
    "MK bits:        512\n"
    "UUID:           111e2222-aaaa-bbbb-cccc-dddddddddddd\n"
    "\n"
    "Key Slot 0: ENABLED\n"
    "    Iterations:             1234\n"
    "Key Slot 1: DISABLED\n"
    "Key Slot 2: DISABLED\n"
)


_SYSTEMD_CRYPTENROLL_OUTPUT = (
    "SLOT TYPE\n"
    "   0 password\n"
    "   1 tpm2\n"
    "   2 empty\n"
)


_CLEVIS_LIST_OUTPUT = (
    "1: tpm2 '{\"hash\":\"sha256\",\"key\":\"ecc\"}'\n"
)


def _seed_run(
    runner: _MockRunner,
    *,
    args: list,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> None:
    """
        Seed a single argv → :class:`RunResult` mapping on ``runner``.
    """
    runner.results[tuple(args)] = RunResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        command=list(args),
    )
    return


def _seed_mapping(
    runner: _MockRunner,
    *,
    name: str,
    exists: bool,
) -> None:
    """
        Seed ``test -b /dev/mapper/<name>`` to the given outcome.
    """
    target = "/dev/mapper/{}".format(name)
    args = ["test", "-b", target]
    if exists:
        _seed_run(runner=runner, args=args, exit_code=0)
    else:
        _seed_run(runner=runner, args=args, exit_code=1)
    return


def _seed_luksdump(
    runner: _MockRunner,
    *,
    device: str,
    output: Optional[str],
) -> None:
    """
        Seed ``cryptsetup luksDump <device>``: empty output and a
        non-zero exit when ``output`` is ``None``.
    """
    args = ["cryptsetup", "luksDump", device]
    if output is None:
        _seed_run(
            runner=runner, args=args, exit_code=1,
            stderr="not a LUKS device",
        )
    else:
        _seed_run(runner=runner, args=args, stdout=output)
    return


def _seed_crypttab(
    runner: _MockRunner,
    *,
    body: Optional[str],
) -> None:
    """
        Seed ``cat /etc/crypttab``.
    """
    args = ["cat", _CRYPTTAB_PATH]
    if body is None:
        _seed_run(
            runner=runner, args=args, exit_code=1,
            stderr="No such file or directory",
        )
    else:
        _seed_run(runner=runner, args=args, stdout=body)
    return


# ----------------------------------------------------------------------
# query_header
# ----------------------------------------------------------------------


def test_query_header_parses_luks2_payload():
    """
        query_header parses a LUKS2 luksDump payload into a
        populated :class:`LuksHeader`.
    """
    runner = _MockRunner()
    _seed_luksdump(runner=runner, device=_DEVICE, output=_LUKS2_DUMP_OUTPUT)
    manager = EncryptionManager(runner, EncryptionManagerConfig())
    header = manager.query_header(device=_DEVICE)

    testbase.assert_true(header is not None)
    testbase.assert_equal(header.version, "2")
    testbase.assert_equal(header.cipher, "aes-xts-plain64")
    testbase.assert_equal(len(header.key_slots), 2)
    testbase.assert_equal(header.key_slots[0].state, "ENABLED")
    testbase.assert_equal(header.key_slots[0].slot, 0)
    testbase.assert_equal(header.key_slots[0].key_size_bits, 512)
    testbase.assert_equal(len(header.tokens), 1)
    return


def test_query_header_parses_luks1_payload():
    """
        query_header parses a LUKS1 luksDump payload into the right
        version + slot states.
    """
    runner = _MockRunner()
    _seed_luksdump(runner=runner, device=_DEVICE, output=_LUKS1_DUMP_OUTPUT)
    manager = EncryptionManager(runner, EncryptionManagerConfig())
    header = manager.query_header(device=_DEVICE)

    testbase.assert_true(header is not None)
    testbase.assert_equal(header.version, "1")
    testbase.assert_equal(header.cipher, "aes-xts-plain64")
    testbase.assert_equal(header.hash, "sha256")
    enabled_count = 0
    for slot_info in header.key_slots:
        if slot_info.state == "ENABLED":
            enabled_count = enabled_count + 1
    testbase.assert_equal(enabled_count, 1)
    return


def test_query_header_returns_none_for_non_luks():
    """
        query_header returns None when ``cryptsetup luksDump``
        exits non-zero (the device is not LUKS-formatted).
    """
    runner = _MockRunner()
    _seed_luksdump(runner=runner, device=_DEVICE, output=None)
    manager = EncryptionManager(runner, EncryptionManagerConfig())
    header = manager.query_header(device=_DEVICE)
    testbase.assert_true(header is None)
    return


def test_query_header_raises_when_cryptsetup_missing():
    """
        query_header raises :class:`EncryptionToolNotAvailableError`
        when the runner reports the cryptsetup binary is missing.
    """
    runner = _MockRunner()
    _seed_run(
        runner=runner,
        args=["cryptsetup", "luksDump", _DEVICE],
        exit_code=127,
        stderr="cryptsetup: command not found",
    )
    manager = EncryptionManager(runner, EncryptionManagerConfig())

    raised = False
    try:
        manager.query_header(device=_DEVICE)
    except EncryptionToolNotAvailableError:
        raised = True
    testbase.assert_true(raised)
    return


# ----------------------------------------------------------------------
# format_device
# ----------------------------------------------------------------------


def test_format_device_dispatches_luksformat_with_stdin_passphrase():
    """
        format_device dispatches ``cryptsetup luksFormat`` with the
        passphrase delivered via stdin and the standard knobs on
        argv.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    _seed_luksdump(runner=runner, device=_DEVICE, output=None)
    manager = EncryptionManager(runner, EncryptionManagerConfig())
    cred = _MockCredential(secret=b"hunter2")

    result = manager.format_device(
        device=_DEVICE,
        spec=CryptFormatSpec(),
        credential=cred,
    )

    testbase.assert_equal(result.status, EncryptionActionStatus.SUCCESS)
    format_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["cryptsetup", "luksFormat"]
    ]
    testbase.assert_equal(len(format_calls), 1)
    testbase.assert_equal(format_calls[0]["stdin"], "hunter2")
    expected_args = [
        "cryptsetup", "luksFormat",
        "--type", "luks2",
        "--cipher", "aes-xts-plain64",
        "--key-size", "512",
        "--hash", "sha256",
        "--pbkdf", "argon2id",
        _DEVICE,
    ]
    testbase.assert_equal(format_calls[0]["args"], expected_args)
    return


def test_format_device_skipped_when_already_luks():
    """
        format_device returns SKIPPED when the device already has a
        LUKS header and ``force=False``.
    """
    runner = _MockRunner()
    _seed_luksdump(runner=runner, device=_DEVICE, output=_LUKS2_DUMP_OUTPUT)
    manager = EncryptionManager(runner, EncryptionManagerConfig())
    cred = _MockCredential(secret=b"hunter2")

    result = manager.format_device(
        device=_DEVICE,
        spec=CryptFormatSpec(),
        credential=cred,
    )

    testbase.assert_equal(result.status, EncryptionActionStatus.SKIPPED)
    format_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["cryptsetup", "luksFormat"]
    ]
    testbase.assert_equal(format_calls, [])
    return


def test_format_device_force_passes_batch_mode():
    """
        format_device with ``force=True`` appends ``--batch-mode``
        and dispatches even when the device is already LUKS.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    _seed_luksdump(runner=runner, device=_DEVICE, output=_LUKS2_DUMP_OUTPUT)
    manager = EncryptionManager(runner, EncryptionManagerConfig())
    cred = _MockCredential(secret=b"hunter2")

    result = manager.format_device(
        device=_DEVICE,
        spec=CryptFormatSpec(),
        credential=cred,
        force=True,
    )

    testbase.assert_equal(result.status, EncryptionActionStatus.SUCCESS)
    format_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["cryptsetup", "luksFormat"]
    ]
    testbase.assert_equal(len(format_calls), 1)
    testbase.assert_true("--batch-mode" in format_calls[0]["args"])
    return


def test_format_device_dry_run_short_circuits():
    """
        format_device dry-run records the would-have-been argv
        without dispatching the runner.
    """
    runner = _MockRunner()
    manager = EncryptionManager(
        runner, EncryptionManagerConfig(dry_run=True),
    )
    cred = _MockCredential(secret=b"hunter2")
    result = manager.format_device(
        device=_DEVICE,
        spec=CryptFormatSpec(),
        credential=cred,
    )
    testbase.assert_equal(result.status, EncryptionActionStatus.DRY_RUN)
    testbase.assert_equal(len(runner.calls), 0)
    testbase.assert_equal(result.command[1], "luksFormat")
    return


def test_format_device_failed_propagates_runner_error():
    """
        format_device returns FAILED carrying exit_code/stderr when
        the runner reports a non-zero exit code.
    """
    runner = _MockRunner()
    _seed_luksdump(runner=runner, device=_DEVICE, output=None)
    runner.default_result = RunResult(
        exit_code=2, stdout="", stderr="device busy", command=[],
    )
    manager = EncryptionManager(runner, EncryptionManagerConfig())
    cred = _MockCredential(secret=b"hunter2")
    result = manager.format_device(
        device=_DEVICE,
        spec=CryptFormatSpec(),
        credential=cred,
    )
    testbase.assert_equal(result.status, EncryptionActionStatus.FAILED)
    testbase.assert_equal(result.exit_code, 2)
    testbase.assert_equal(result.stderr, "device busy")
    return


def test_format_device_raises_when_credential_missing():
    """
        format_device raises :class:`EncryptionCredentialError` when
        no credential is supplied.
    """
    runner = _MockRunner()
    manager = EncryptionManager(runner, EncryptionManagerConfig())

    raised = False
    try:
        manager.format_device(
            device=_DEVICE,
            spec=CryptFormatSpec(),
            credential=None,
        )
    except EncryptionCredentialError:
        raised = True
    testbase.assert_true(raised)
    return


# ----------------------------------------------------------------------
# open / close / resize
# ----------------------------------------------------------------------


def test_open_dispatches_with_stdin_passphrase():
    """
        open dispatches ``cryptsetup open <device> <name>`` with the
        passphrase on stdin.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    _seed_mapping(runner=runner, name=_NAME, exists=False)
    manager = EncryptionManager(runner, EncryptionManagerConfig())
    cred = _MockCredential(secret=b"hunter2")

    result = manager.open(device=_DEVICE, name=_NAME, credential=cred)

    testbase.assert_equal(result.status, EncryptionActionStatus.SUCCESS)
    open_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["cryptsetup", "open"]
    ]
    testbase.assert_equal(len(open_calls), 1)
    testbase.assert_equal(
        open_calls[0]["args"],
        ["cryptsetup", "open", _DEVICE, _NAME],
    )
    testbase.assert_equal(open_calls[0]["stdin"], "hunter2")
    return


def test_open_with_key_file_does_not_pass_stdin():
    """
        open with ``key_file=`` dispatches ``cryptsetup open
        --key-file <file> <device> <name>`` with no stdin payload.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    _seed_mapping(runner=runner, name=_NAME, exists=False)
    manager = EncryptionManager(runner, EncryptionManagerConfig())

    result = manager.open(
        device=_DEVICE,
        name=_NAME,
        key_file="/var/keys/master.key",
    )

    testbase.assert_equal(result.status, EncryptionActionStatus.SUCCESS)
    open_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["cryptsetup", "open"]
    ]
    testbase.assert_equal(len(open_calls), 1)
    testbase.assert_equal(
        open_calls[0]["args"],
        [
            "cryptsetup", "open",
            "--key-file", "/var/keys/master.key",
            _DEVICE, _NAME,
        ],
    )
    testbase.assert_true(open_calls[0]["stdin"] is None)
    return


def test_open_skipped_when_mapping_exists():
    """
        open returns SKIPPED when the mapping is already active.
    """
    runner = _MockRunner()
    _seed_mapping(runner=runner, name=_NAME, exists=True)
    manager = EncryptionManager(runner, EncryptionManagerConfig())
    cred = _MockCredential(secret=b"hunter2")

    result = manager.open(device=_DEVICE, name=_NAME, credential=cred)

    testbase.assert_equal(result.status, EncryptionActionStatus.SKIPPED)
    open_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["cryptsetup", "open"]
    ]
    testbase.assert_equal(open_calls, [])
    return


def test_open_raises_when_no_credential_or_key_file():
    """
        open raises :class:`EncryptionCredentialError` when neither
        ``credential`` nor ``key_file`` is supplied.
    """
    runner = _MockRunner()
    manager = EncryptionManager(runner, EncryptionManagerConfig())

    raised = False
    try:
        manager.open(device=_DEVICE, name=_NAME)
    except EncryptionCredentialError:
        raised = True
    testbase.assert_true(raised)
    return


def test_open_with_clevis_dispatches_clevis_unlock():
    """
        open_with_clevis dispatches ``clevis luks unlock`` with no
        stdin payload.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    _seed_mapping(runner=runner, name=_NAME, exists=False)
    manager = EncryptionManager(runner, EncryptionManagerConfig())

    result = manager.open_with_clevis(device=_DEVICE, name=_NAME)

    testbase.assert_equal(result.status, EncryptionActionStatus.SUCCESS)
    unlock_calls = [
        c for c in runner.calls
        if c["args"][:3] == ["clevis", "luks", "unlock"]
    ]
    testbase.assert_equal(len(unlock_calls), 1)
    testbase.assert_equal(
        unlock_calls[0]["args"],
        ["clevis", "luks", "unlock", "-d", _DEVICE, "-n", _NAME],
    )
    testbase.assert_true(unlock_calls[0]["stdin"] is None)
    return


def test_close_dispatches_when_mapping_exists():
    """
        close dispatches ``cryptsetup close <name>`` only when the
        mapping is active; otherwise it returns SKIPPED.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    _seed_mapping(runner=runner, name=_NAME, exists=True)
    manager = EncryptionManager(runner, EncryptionManagerConfig())

    result = manager.close(name=_NAME)

    testbase.assert_equal(result.status, EncryptionActionStatus.SUCCESS)
    close_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["cryptsetup", "close"]
    ]
    testbase.assert_equal(len(close_calls), 1)
    return


def test_close_skipped_when_mapping_absent():
    """
        close returns SKIPPED when the mapping is not active.
    """
    runner = _MockRunner()
    _seed_mapping(runner=runner, name=_NAME, exists=False)
    manager = EncryptionManager(runner, EncryptionManagerConfig())

    result = manager.close(name=_NAME)

    testbase.assert_equal(result.status, EncryptionActionStatus.SKIPPED)
    close_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["cryptsetup", "close"]
    ]
    testbase.assert_equal(close_calls, [])
    return


def test_resize_without_sectors_grows_to_device():
    """
        resize without an explicit sectors value dispatches
        ``cryptsetup resize <name>`` with no extra args.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    _seed_mapping(runner=runner, name=_NAME, exists=True)
    manager = EncryptionManager(runner, EncryptionManagerConfig())

    result = manager.resize(name=_NAME)

    testbase.assert_equal(result.status, EncryptionActionStatus.SUCCESS)
    resize_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["cryptsetup", "resize"]
    ]
    testbase.assert_equal(len(resize_calls), 1)
    testbase.assert_equal(
        resize_calls[0]["args"], ["cryptsetup", "resize", _NAME],
    )
    return


def test_resize_with_sectors_passes_size_flag():
    """
        resize with explicit sectors appends ``--size <sectors>``.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    _seed_mapping(runner=runner, name=_NAME, exists=True)
    manager = EncryptionManager(runner, EncryptionManagerConfig())

    result = manager.resize(name=_NAME, sectors=204800)

    testbase.assert_equal(result.status, EncryptionActionStatus.SUCCESS)
    resize_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["cryptsetup", "resize"]
    ]
    testbase.assert_equal(len(resize_calls), 1)
    testbase.assert_equal(
        resize_calls[0]["args"],
        ["cryptsetup", "resize", _NAME, "--size", "204800"],
    )
    return


def test_resize_failed_when_mapping_absent():
    """
        resize returns FAILED without dispatching when the mapping
        is not active.
    """
    runner = _MockRunner()
    _seed_mapping(runner=runner, name=_NAME, exists=False)
    manager = EncryptionManager(runner, EncryptionManagerConfig())

    result = manager.resize(name=_NAME)

    testbase.assert_equal(result.status, EncryptionActionStatus.FAILED)
    resize_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["cryptsetup", "resize"]
    ]
    testbase.assert_equal(resize_calls, [])
    return


# ----------------------------------------------------------------------
# Key-slot lifecycle
# ----------------------------------------------------------------------


def test_add_key_sends_combined_stdin_payload():
    """
        add_key dispatches ``cryptsetup luksAddKey <device>`` with
        the existing and new passphrases on a single stdin payload.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    manager = EncryptionManager(runner, EncryptionManagerConfig())
    old = _MockCredential(secret=b"hunter2")
    new = _MockCredential(secret=b"new-secret")

    result = manager.add_key(
        device=_DEVICE,
        existing_credential=old,
        new_credential=new,
    )

    testbase.assert_equal(result.status, EncryptionActionStatus.SUCCESS)
    addkey_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["cryptsetup", "luksAddKey"]
    ]
    testbase.assert_equal(len(addkey_calls), 1)
    testbase.assert_equal(
        addkey_calls[0]["args"], ["cryptsetup", "luksAddKey", _DEVICE],
    )
    testbase.assert_equal(addkey_calls[0]["stdin"], "hunter2\nnew-secret\n")
    return


def test_remove_key_by_slot_dispatches_with_passphrase_on_stdin():
    """
        remove_key_by_slot dispatches ``cryptsetup luksKillSlot``
        with the existing passphrase on stdin when at least one
        other enabled slot remains.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    _seed_luksdump(runner=runner, device=_DEVICE, output=_LUKS2_DUMP_OUTPUT)
    manager = EncryptionManager(runner, EncryptionManagerConfig())
    cred = _MockCredential(secret=b"hunter2")

    result = manager.remove_key_by_slot(
        device=_DEVICE, slot=0, credential=cred,
    )

    testbase.assert_equal(result.status, EncryptionActionStatus.SUCCESS)
    kill_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["cryptsetup", "luksKillSlot"]
    ]
    testbase.assert_equal(len(kill_calls), 1)
    testbase.assert_equal(
        kill_calls[0]["args"],
        ["cryptsetup", "luksKillSlot", _DEVICE, "0"],
    )
    testbase.assert_equal(kill_calls[0]["stdin"], "hunter2")
    return


def test_remove_key_by_slot_refuses_last_slot():
    """
        remove_key_by_slot returns FAILED without dispatching when
        the removal would leave the device with zero enabled slots.
    """
    runner = _MockRunner()
    _seed_luksdump(runner=runner, device=_DEVICE, output=_LUKS1_DUMP_OUTPUT)
    manager = EncryptionManager(runner, EncryptionManagerConfig())
    cred = _MockCredential(secret=b"hunter2")

    result = manager.remove_key_by_slot(
        device=_DEVICE, slot=0, credential=cred,
    )

    testbase.assert_equal(result.status, EncryptionActionStatus.FAILED)
    kill_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["cryptsetup", "luksKillSlot"]
    ]
    testbase.assert_equal(kill_calls, [])
    return


def test_remove_key_by_passphrase_dispatches_luksremovekey():
    """
        remove_key_by_passphrase dispatches ``cryptsetup
        luksRemoveKey`` with the passphrase on stdin.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    manager = EncryptionManager(runner, EncryptionManagerConfig())
    cred = _MockCredential(secret=b"hunter2")

    result = manager.remove_key_by_passphrase(
        device=_DEVICE, credential=cred,
    )

    testbase.assert_equal(result.status, EncryptionActionStatus.SUCCESS)
    remove_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["cryptsetup", "luksRemoveKey"]
    ]
    testbase.assert_equal(len(remove_calls), 1)
    testbase.assert_equal(
        remove_calls[0]["args"], ["cryptsetup", "luksRemoveKey", _DEVICE],
    )
    testbase.assert_equal(remove_calls[0]["stdin"], "hunter2")
    return


def test_rotate_key_sends_combined_stdin_payload():
    """
        rotate_key dispatches ``cryptsetup luksChangeKey`` with the
        old and new passphrases joined by a newline on a single
        stdin payload.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    manager = EncryptionManager(runner, EncryptionManagerConfig())
    old = _MockCredential(secret=b"hunter2")
    new = _MockCredential(secret=b"new-secret")

    result = manager.rotate_key(
        device=_DEVICE,
        old_credential=old,
        new_credential=new,
    )

    testbase.assert_equal(result.status, EncryptionActionStatus.SUCCESS)
    rotate_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["cryptsetup", "luksChangeKey"]
    ]
    testbase.assert_equal(len(rotate_calls), 1)
    testbase.assert_equal(
        rotate_calls[0]["args"], ["cryptsetup", "luksChangeKey", _DEVICE],
    )
    testbase.assert_equal(rotate_calls[0]["stdin"], "hunter2\nnew-secret\n")
    return


# ----------------------------------------------------------------------
# TPM2 / Clevis enrolment
# ----------------------------------------------------------------------


def test_enroll_tpm2_dispatches_systemd_cryptenroll():
    """
        enroll_tpm2 dispatches ``systemd-cryptenroll
        --tpm2-device=auto --tpm2-pcrs=<pcrs> <device>`` with the
        passphrase on stdin.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    manager = EncryptionManager(runner, EncryptionManagerConfig())
    cred = _MockCredential(secret=b"hunter2")

    result = manager.enroll_tpm2(
        device=_DEVICE,
        credential=cred,
        pcrs="0,7",
    )

    testbase.assert_equal(result.status, EncryptionActionStatus.SUCCESS)
    sce_calls = [
        c for c in runner.calls
        if c["args"][:1] == ["systemd-cryptenroll"]
    ]
    testbase.assert_equal(len(sce_calls), 1)
    testbase.assert_equal(
        sce_calls[0]["args"],
        [
            "systemd-cryptenroll",
            "--tpm2-device=auto",
            "--tpm2-pcrs=0,7",
            _DEVICE,
        ],
    )
    testbase.assert_equal(sce_calls[0]["stdin"], "hunter2")
    return


def test_wipe_tpm2_slot_dispatches_wipe_slot_flag():
    """
        wipe_tpm2_slot dispatches ``systemd-cryptenroll
        --wipe-slot=<slot> <device>``.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    manager = EncryptionManager(runner, EncryptionManagerConfig())

    result = manager.wipe_tpm2_slot(device=_DEVICE, slot=2)

    testbase.assert_equal(result.status, EncryptionActionStatus.SUCCESS)
    sce_calls = [
        c for c in runner.calls
        if c["args"][:1] == ["systemd-cryptenroll"]
    ]
    testbase.assert_equal(len(sce_calls), 1)
    testbase.assert_equal(
        sce_calls[0]["args"],
        ["systemd-cryptenroll", "--wipe-slot=2", _DEVICE],
    )
    return


def test_enroll_clevis_dispatches_clevis_luks_bind():
    """
        enroll_clevis dispatches ``clevis luks bind`` with the JSON
        config on argv (no secret material) and the passphrase on
        stdin.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    manager = EncryptionManager(runner, EncryptionManagerConfig())
    cred = _MockCredential(secret=b"hunter2")

    result = manager.enroll_clevis(
        device=_DEVICE,
        pin="tpm2",
        config={"hash": "sha256", "key": "ecc"},
        credential=cred,
    )

    testbase.assert_equal(result.status, EncryptionActionStatus.SUCCESS)
    bind_calls = [
        c for c in runner.calls
        if c["args"][:3] == ["clevis", "luks", "bind"]
    ]
    testbase.assert_equal(len(bind_calls), 1)
    testbase.assert_equal(bind_calls[0]["args"][:5], [
        "clevis", "luks", "bind", "-d", _DEVICE,
    ])
    testbase.assert_equal(bind_calls[0]["args"][5], "tpm2")
    testbase.assert_equal(bind_calls[0]["stdin"], "hunter2")
    return


def test_unbind_clevis_dispatches_clevis_luks_unbind():
    """
        unbind_clevis dispatches ``clevis luks unbind -d <device>
        -s <slot>``.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    manager = EncryptionManager(runner, EncryptionManagerConfig())

    result = manager.unbind_clevis(device=_DEVICE, slot=1)

    testbase.assert_equal(result.status, EncryptionActionStatus.SUCCESS)
    unbind_calls = [
        c for c in runner.calls
        if c["args"][:3] == ["clevis", "luks", "unbind"]
    ]
    testbase.assert_equal(len(unbind_calls), 1)
    testbase.assert_equal(
        unbind_calls[0]["args"],
        ["clevis", "luks", "unbind", "-d", _DEVICE, "-s", "1"],
    )
    return


def test_list_bindings_merges_systemd_and_clevis():
    """
        list_bindings merges the systemd-cryptenroll table and the
        clevis luks list output into a single
        ``list[CryptBinding]``.
    """
    runner = _MockRunner()
    _seed_run(
        runner=runner,
        args=["systemd-cryptenroll", _DEVICE],
        stdout=_SYSTEMD_CRYPTENROLL_OUTPUT,
    )
    _seed_run(
        runner=runner,
        args=["clevis", "luks", "list", "-d", _DEVICE],
        stdout=_CLEVIS_LIST_OUTPUT,
    )
    manager = EncryptionManager(runner, EncryptionManagerConfig())

    bindings = manager.list_bindings(device=_DEVICE)

    backends = sorted([b.backend for b in bindings])
    testbase.assert_true("tpm2" in backends)
    testbase.assert_true("passphrase" in backends)
    testbase.assert_true("clevis" in backends)
    return


# ----------------------------------------------------------------------
# /etc/crypttab
# ----------------------------------------------------------------------


def test_list_crypttab_parses_typical_file():
    """
        list_crypttab parses comments, blank lines, the ``-`` keyfile
        sentinel, and comma-separated options.
    """
    runner = _MockRunner()
    body = (
        "# this is a comment\n"
        "\n"
        "cryptroot UUID=abcd-1234 - luks,tpm2-device=auto\n"
        "cryptdata /dev/sdb1 /var/keys/sdb1.key luks,timeout=180\n"
    )
    _seed_crypttab(runner=runner, body=body)
    manager = EncryptionManager(runner, EncryptionManagerConfig())

    entries = manager.list_crypttab()

    testbase.assert_equal(len(entries), 2)
    testbase.assert_equal(entries[0].name, "cryptroot")
    testbase.assert_equal(entries[0].device, "UUID=abcd-1234")
    testbase.assert_true(entries[0].keyfile is None)
    testbase.assert_true("luks" in entries[0].options)
    testbase.assert_true("tpm2-device=auto" in entries[0].options)
    testbase.assert_equal(entries[1].keyfile, "/var/keys/sdb1.key")
    return


def test_add_crypttab_entry_skipped_when_present():
    """
        add_crypttab_entry returns SKIPPED when an identical entry
        already exists in the file.
    """
    runner = _MockRunner()
    body = "cryptroot UUID=abcd-1234 - luks\n"
    _seed_crypttab(runner=runner, body=body)
    manager = EncryptionManager(runner, EncryptionManagerConfig())

    entry = CrypttabEntry(
        name="cryptroot",
        device="UUID=abcd-1234",
        keyfile=None,
        options=["luks"],
    )
    result = manager.add_crypttab_entry(entry=entry)

    testbase.assert_equal(result.status, EncryptionActionStatus.SKIPPED)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(tee_calls, [])
    return


def test_add_crypttab_entry_writes_via_tee():
    """
        add_crypttab_entry dispatches ``tee /etc/crypttab`` with the
        new content on stdin.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    body = "cryptroot UUID=abcd-1234 - luks\n"
    _seed_crypttab(runner=runner, body=body)
    manager = EncryptionManager(runner, EncryptionManagerConfig())

    entry = CrypttabEntry(
        name="cryptdata",
        device="/dev/sdb1",
        keyfile="/var/keys/sdb1.key",
        options=["luks", "timeout=180"],
    )
    result = manager.add_crypttab_entry(entry=entry)

    testbase.assert_equal(result.status, EncryptionActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_equal(tee_calls[0]["args"], ["tee", _CRYPTTAB_PATH])
    testbase.assert_true("cryptdata /dev/sdb1" in tee_calls[0]["stdin"])
    return


def test_remove_crypttab_entry_skipped_when_absent():
    """
        remove_crypttab_entry returns SKIPPED when no entry of that
        name exists.
    """
    runner = _MockRunner()
    _seed_crypttab(runner=runner, body="cryptroot UUID=abcd - luks\n")
    manager = EncryptionManager(runner, EncryptionManagerConfig())

    result = manager.remove_crypttab_entry(name="cryptdata")

    testbase.assert_equal(result.status, EncryptionActionStatus.SKIPPED)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(tee_calls, [])
    return


def test_remove_crypttab_entry_writes_via_tee():
    """
        remove_crypttab_entry dispatches ``tee`` with the updated
        content (matching entry stripped) on stdin.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    body = (
        "cryptroot UUID=abcd - luks\n"
        "cryptdata /dev/sdb1 /var/keys/sdb1.key luks\n"
    )
    _seed_crypttab(runner=runner, body=body)
    manager = EncryptionManager(runner, EncryptionManagerConfig())

    result = manager.remove_crypttab_entry(name="cryptdata")

    testbase.assert_equal(result.status, EncryptionActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true("cryptroot" in tee_calls[0]["stdin"])
    testbase.assert_true("cryptdata" not in tee_calls[0]["stdin"])
    return


def test_update_crypttab_entry_replaces_in_place():
    """
        update_crypttab_entry dispatches ``tee`` with the matching
        entry replaced, preserving file order.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    body = (
        "cryptroot UUID=abcd - luks\n"
        "cryptdata /dev/sdb1 - luks\n"
    )
    _seed_crypttab(runner=runner, body=body)
    manager = EncryptionManager(runner, EncryptionManagerConfig())

    new_entry = CrypttabEntry(
        name="cryptdata",
        device="/dev/sdb1",
        keyfile=None,
        options=["luks", "timeout=300"],
    )
    result = manager.update_crypttab_entry(entry=new_entry)

    testbase.assert_equal(result.status, EncryptionActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true("timeout=300" in tee_calls[0]["stdin"])
    testbase.assert_true("cryptroot" in tee_calls[0]["stdin"])
    return


def test_update_crypttab_entry_skipped_when_already_matches():
    """
        update_crypttab_entry returns SKIPPED when the matching
        entry already has identical fields.
    """
    runner = _MockRunner()
    body = "cryptroot UUID=abcd - luks\n"
    _seed_crypttab(runner=runner, body=body)
    manager = EncryptionManager(runner, EncryptionManagerConfig())

    entry = CrypttabEntry(
        name="cryptroot",
        device="UUID=abcd",
        keyfile=None,
        options=["luks"],
    )
    result = manager.update_crypttab_entry(entry=entry)

    testbase.assert_equal(result.status, EncryptionActionStatus.SKIPPED)
    return


# ----------------------------------------------------------------------
# Dry-run sweep
# ----------------------------------------------------------------------


def test_dry_run_short_circuits_every_mutating_method():
    """
        Dry-run mode short-circuits every mutating method without
        invoking the runner for the dispatched command.
    """
    runner = _MockRunner()
    _seed_luksdump(runner=runner, device=_DEVICE, output=None)
    # Mapping exists so close() / resize() reach the dispatch branch
    # rather than short-circuiting on the idempotency / safety rail.
    _seed_mapping(runner=runner, name=_NAME, exists=True)
    _seed_crypttab(runner=runner, body="")
    manager = EncryptionManager(
        runner, EncryptionManagerConfig(dry_run=True),
    )
    cred = _MockCredential(secret=b"hunter2")

    # ``open`` would SKIP when the mapping is already active; pass a
    # distinct mapping name so the dispatch branch fires.
    _seed_mapping(runner=runner, name="cryptopen", exists=False)
    r1 = manager.format_device(
        device=_DEVICE, spec=CryptFormatSpec(), credential=cred,
    )
    r2 = manager.open(device=_DEVICE, name="cryptopen", credential=cred)
    r3 = manager.close(name=_NAME)
    r4 = manager.resize(name=_NAME)
    r5 = manager.add_key(
        device=_DEVICE, existing_credential=cred, new_credential=cred,
    )
    r6 = manager.remove_key_by_passphrase(device=_DEVICE, credential=cred)
    r7 = manager.rotate_key(
        device=_DEVICE, old_credential=cred, new_credential=cred,
    )
    r8 = manager.enroll_tpm2(device=_DEVICE, credential=cred)
    r9 = manager.wipe_tpm2_slot(device=_DEVICE, slot=2)
    r10 = manager.enroll_clevis(
        device=_DEVICE, pin="tpm2", config={}, credential=cred,
    )
    r11 = manager.unbind_clevis(device=_DEVICE, slot=1)
    r12 = manager.add_crypttab_entry(
        entry=CrypttabEntry(name="cryptroot", device="UUID=abcd"),
    )
    r13 = manager.update_crypttab_entry(
        entry=CrypttabEntry(name="cryptroot", device="UUID=abcd"),
    )

    expected_dry_run = [
        r1, r2, r3, r4, r5, r6, r7, r8, r9, r10, r11, r12, r13,
    ]
    for result in expected_dry_run:
        testbase.assert_equal(result.status, EncryptionActionStatus.DRY_RUN)

    # Idempotency-rail SKIPPED checks: close on an absent mapping and
    # remove of an absent crypttab entry win over dry-run.
    _seed_mapping(runner=runner, name="absentmap", exists=False)
    skipped_close = manager.close(name="absentmap")
    testbase.assert_equal(skipped_close.status, EncryptionActionStatus.SKIPPED)

    skipped_remove = manager.remove_crypttab_entry(name="cryptroot")
    testbase.assert_equal(skipped_remove.status, EncryptionActionStatus.SKIPPED)
    return


# ----------------------------------------------------------------------
# Argv / stdin hygiene — secret bytes never touch argv
# ----------------------------------------------------------------------


def test_passphrase_never_appears_in_recorded_argv():
    """
        Plaintext passphrase bytes never appear in any ``args`` field
        recorded by the runner; the bytes flow on stdin only.  Tests
        every passphrase-accepting verb.
    """
    secret = "super-secret-string"
    new_secret = "new-secret-string"
    cred_old = _MockCredential(secret=secret.encode("utf-8"))
    cred_new = _MockCredential(secret=new_secret.encode("utf-8"))

    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    _seed_luksdump(runner=runner, device=_DEVICE, output=_LUKS2_DUMP_OUTPUT)
    _seed_mapping(runner=runner, name=_NAME, exists=False)
    manager = EncryptionManager(runner, EncryptionManagerConfig())

    # format_device with force=True dispatches even when the device
    # already has a header.
    manager.format_device(
        device=_DEVICE,
        spec=CryptFormatSpec(),
        credential=cred_old,
        force=True,
    )
    manager.open(device=_DEVICE, name=_NAME, credential=cred_old)
    manager.add_key(
        device=_DEVICE,
        existing_credential=cred_old,
        new_credential=cred_new,
    )
    manager.remove_key_by_slot(
        device=_DEVICE, slot=0, credential=cred_old,
    )
    manager.remove_key_by_passphrase(device=_DEVICE, credential=cred_old)
    manager.rotate_key(
        device=_DEVICE,
        old_credential=cred_old,
        new_credential=cred_new,
    )
    manager.enroll_tpm2(device=_DEVICE, credential=cred_old)
    manager.enroll_clevis(
        device=_DEVICE,
        pin="tpm2",
        config={"hash": "sha256"},
        credential=cred_old,
    )

    for call in runner.calls:
        for token in call["args"]:
            testbase.assert_true(token != secret)
            testbase.assert_true(token != new_secret)
            testbase.assert_true(secret not in token)
            testbase.assert_true(new_secret not in token)
    return


# ----------------------------------------------------------------------
# Elevation
# ----------------------------------------------------------------------


def test_writes_elevate_per_config():
    """
        Mutating operations dispatch with ``elevated=True`` when the
        config requests elevation.
    """
    runner = _MockRunner(
        default_result=RunResult(
            exit_code=0, stdout="", stderr="", command=[],
        ),
    )
    _seed_luksdump(runner=runner, device=_DEVICE, output=None)
    manager = EncryptionManager(
        runner, EncryptionManagerConfig(elevated=True),
    )
    cred = _MockCredential(secret=b"hunter2")
    manager.format_device(
        device=_DEVICE, spec=CryptFormatSpec(), credential=cred,
    )

    write_calls = [
        c for c in runner.calls
        if c["args"][:2] == ["cryptsetup", "luksFormat"]
    ]
    testbase.assert_equal(len(write_calls), 1)
    testbase.assert_equal(write_calls[0]["elevated"], True)
    return


def test_reads_never_elevate():
    """
        Read verbs never pass ``elevated=True`` regardless of
        config.
    """
    runner = _MockRunner()
    _seed_luksdump(runner=runner, device=_DEVICE, output=_LUKS2_DUMP_OUTPUT)
    manager = EncryptionManager(
        runner, EncryptionManagerConfig(elevated=True),
    )
    manager.query_header(device=_DEVICE)

    for call in runner.calls:
        testbase.assert_equal(call["elevated"], False)
    return


# ----------------------------------------------------------------------
# Module-level parsers
# ----------------------------------------------------------------------


def test_parse_luks_dump_handles_luks2_payload():
    """
        _parse_luks_dump pulls cipher/version/key-slot data out of a
        LUKS2 dump.
    """
    parsed = _parse_luks_dump(device=_DEVICE, output=_LUKS2_DUMP_OUTPUT)
    testbase.assert_equal(parsed.version, "2")
    testbase.assert_equal(parsed.cipher, "aes-xts-plain64")
    testbase.assert_equal(len(parsed.key_slots), 2)
    testbase.assert_equal(parsed.key_slots[0].slot, 0)
    testbase.assert_equal(parsed.key_slots[0].key_size_bits, 512)
    return


def test_parse_luks_dump_handles_luks1_payload():
    """
        _parse_luks_dump composes the cipher field from ``Cipher
        name:`` + ``Cipher mode:`` for LUKS1 dumps.
    """
    parsed = _parse_luks_dump(device=_DEVICE, output=_LUKS1_DUMP_OUTPUT)
    testbase.assert_equal(parsed.version, "1")
    testbase.assert_equal(parsed.cipher, "aes-xts-plain64")
    testbase.assert_equal(parsed.hash, "sha256")
    enabled_slots = [
        s for s in parsed.key_slots if s.state == "ENABLED"
    ]
    testbase.assert_equal(len(enabled_slots), 1)
    return


def test_parse_systemd_cryptenroll_extracts_slot_table():
    """
        _parse_systemd_cryptenroll skips the header and ``empty``
        rows and returns one :class:`CryptBinding` per active slot.
    """
    parsed = _parse_systemd_cryptenroll(output=_SYSTEMD_CRYPTENROLL_OUTPUT)
    testbase.assert_equal(len(parsed), 2)
    backends = sorted([b.backend for b in parsed])
    testbase.assert_equal(backends, ["passphrase", "tpm2"])
    return


def test_parse_clevis_luks_list_extracts_pin_and_config():
    """
        _parse_clevis_luks_list extracts the slot index, pin name,
        and parsed JSON config from a clevis list line.
    """
    parsed = _parse_clevis_luks_list(output=_CLEVIS_LIST_OUTPUT)
    testbase.assert_equal(len(parsed), 1)
    testbase.assert_equal(parsed[0].slot, 1)
    testbase.assert_equal(parsed[0].pin, "tpm2")
    testbase.assert_equal(parsed[0].backend, "clevis")
    testbase.assert_equal(parsed[0].config["hash"], "sha256")
    return


def test_parse_crypttab_handles_comments_and_keyfile_dash():
    """
        _parse_crypttab skips comments / blank lines and normalises
        the ``-`` keyfile sentinel to ``None``.
    """
    body = (
        "# comment\n"
        "\n"
        "cryptroot UUID=abcd - luks,tpm2-device=auto\n"
    )
    entries = _parse_crypttab(content=body)
    testbase.assert_equal(len(entries), 1)
    testbase.assert_equal(entries[0].name, "cryptroot")
    testbase.assert_true(entries[0].keyfile is None)
    testbase.assert_equal(entries[0].options, ["luks", "tpm2-device=auto"])
    return


def test_render_crypttab_round_trips_through_parser():
    """
        _render_crypttab → _parse_crypttab round-trips a list of
        entries.
    """
    entries = [
        CrypttabEntry(
            name="cryptroot",
            device="UUID=abcd",
            keyfile=None,
            options=["luks"],
        ),
        CrypttabEntry(
            name="cryptdata",
            device="/dev/sdb1",
            keyfile="/var/keys/sdb1.key",
            options=["luks", "timeout=180"],
        ),
    ]
    rendered = _render_crypttab(entries=entries)
    parsed = _parse_crypttab(content=rendered)
    testbase.assert_equal(len(parsed), 2)
    testbase.assert_equal(parsed[0].name, "cryptroot")
    testbase.assert_true(parsed[0].keyfile is None)
    testbase.assert_equal(parsed[1].keyfile, "/var/keys/sdb1.key")
    testbase.assert_equal(parsed[1].options, ["luks", "timeout=180"])
    return
