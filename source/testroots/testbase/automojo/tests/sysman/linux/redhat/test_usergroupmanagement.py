# Tests for UserManager and GroupManager.

from dataclasses import dataclass, field
from typing import Callable, Optional

from automojo import testbase

from automojo.sysman.linux.redhat.runners.models import RunResult
from automojo.sysman.linux.redhat.usergroupmanagement import (
    ActionStatus,
    GroupManager,
    GroupSpec,
    PasswordAgeSpec,
    UserGroupManagerConfig,
    UserManager,
    UserModSpec,
    UserNotFoundError,
    UserSpec,
)


@dataclass
class _MockRunner:
    """
        Stub runner that replays predefined results and records every call.
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
        self.calls.append({
            "args": list(args),
            "elevated": elevated,
            "stdin": stdin,
        })
        key = tuple(args)
        if key in self.results:
            return self.results[key]
        if self.default_result is not None:
            return self.default_result
        default = RunResult(
            exit_code=0, stdout="", stderr="", command=list(args)
        )
        return default

    def add_aspect(self, aspect) -> None:
        return

    def remove_aspect(self, aspect) -> None:
        return

    def get_metrics(self):
        return None


def _user_row(name: str, *, uid: int = 1000, home: str = "/home/joe") -> RunResult:
    """
        Build a getent passwd RunResult for a single user.
    """
    stdout = "{}:x:{}:1000:Joe User:{}:/bin/bash\n".format(name, uid, home)
    result = RunResult(
        exit_code=0, stdout=stdout, stderr="", command=["getent"],
    )
    return result


def _absent() -> RunResult:
    """
        Build a getent RunResult signalling absence (exit 2).
    """
    return RunResult(exit_code=2, stdout="", stderr="", command=["getent"])


def _group_row(name: str, *, gid: int = 2000, members: str = "") -> RunResult:
    """
        Build a getent group RunResult for a single group.
    """
    stdout = "{}:x:{}:{}\n".format(name, gid, members)
    result = RunResult(
        exit_code=0, stdout=stdout, stderr="", command=["getent"],
    )
    return result


def test_user_query_returns_info_when_present():
    """
        query() parses getent passwd output into a UserInfo record.
    """
    runner = _MockRunner()
    runner.results[("getent", "passwd", "joe")] = _user_row("joe")
    runner.results[("passwd", "-S", "joe")] = RunResult(
        exit_code=0, stdout="joe P 2024-01-01 0 99999 7 -1", stderr="",
        command=["passwd"],
    )
    um = UserManager(runner, UserGroupManagerConfig())
    info = um.query("joe")
    testbase.assert_true(info is not None)
    testbase.assert_equal(info.username, "joe")
    testbase.assert_equal(info.uid, 1000)
    testbase.assert_false(info.locked)
    return


def test_user_query_returns_none_when_absent():
    """
        query() returns None when getent passwd exits non-zero.
    """
    runner = _MockRunner()
    runner.results[("getent", "passwd", "ghost")] = _absent()
    um = UserManager(runner, UserGroupManagerConfig())
    testbase.assert_equal(um.query("ghost"), None)
    return


def test_user_create_when_absent():
    """
        create() dispatches useradd with the configured options.
    """
    runner = _MockRunner()
    runner.results[("getent", "passwd", "alice")] = _absent()
    um = UserManager(runner, UserGroupManagerConfig(elevated=True))
    result = um.create(UserSpec(username="alice", uid=1500, shell="/bin/bash"))
    testbase.assert_equal(result.status, ActionStatus.SUCCESS)
    write_call = runner.calls[-1]
    testbase.assert_equal(
        write_call["args"],
        ["useradd", "-u", "1500", "-s", "/bin/bash", "alice"],
    )
    testbase.assert_true(write_call["elevated"])
    return


def test_user_create_skipped_when_present():
    """
        create() returns SKIPPED when the user already exists.
    """
    runner = _MockRunner()
    runner.results[("getent", "passwd", "alice")] = _user_row("alice")
    runner.results[("passwd", "-S", "alice")] = RunResult(
        exit_code=0, stdout="alice P 2024-01-01 0 99999 7 -1", stderr="",
        command=["passwd"],
    )
    um = UserManager(runner, UserGroupManagerConfig())
    result = um.create(UserSpec(username="alice"))
    testbase.assert_equal(result.status, ActionStatus.SKIPPED)
    return


def test_user_delete_when_present():
    """
        delete() dispatches userdel including -r when remove_home is set.
    """
    runner = _MockRunner()
    runner.results[("getent", "passwd", "alice")] = _user_row("alice")
    runner.results[("passwd", "-S", "alice")] = RunResult(
        exit_code=0, stdout="alice P 2024-01-01 0 99999 7 -1", stderr="",
        command=["passwd"],
    )
    um = UserManager(runner, UserGroupManagerConfig(elevated=True))
    result = um.delete("alice", remove_home=True)
    testbase.assert_equal(result.status, ActionStatus.SUCCESS)
    testbase.assert_equal(runner.calls[-1]["args"], ["userdel", "-r", "alice"])
    return


def test_user_delete_skipped_when_absent():
    """
        delete() on an absent user returns SKIPPED.
    """
    runner = _MockRunner()
    runner.results[("getent", "passwd", "ghost")] = _absent()
    um = UserManager(runner, UserGroupManagerConfig())
    result = um.delete("ghost")
    testbase.assert_equal(result.status, ActionStatus.SKIPPED)
    return


def test_user_modify_requires_existing_user():
    """
        modify() raises UserNotFoundError when the user is absent.
    """
    runner = _MockRunner()
    runner.results[("getent", "passwd", "ghost")] = _absent()
    um = UserManager(runner, UserGroupManagerConfig())
    raised = False
    try:
        um.modify("ghost", UserModSpec(shell="/bin/zsh"))
    except UserNotFoundError:
        raised = True
    testbase.assert_true(raised)
    return


def test_user_modify_dispatches_usermod():
    """
        modify() dispatches usermod with the specified fields.
    """
    runner = _MockRunner()
    runner.results[("getent", "passwd", "joe")] = _user_row("joe")
    runner.results[("passwd", "-S", "joe")] = RunResult(
        exit_code=0, stdout="joe P 2024-01-01 0 99999 7 -1", stderr="",
        command=["passwd"],
    )
    um = UserManager(runner, UserGroupManagerConfig(elevated=True))
    um.modify("joe", UserModSpec(shell="/bin/zsh"))
    testbase.assert_equal(
        runner.calls[-1]["args"], ["usermod", "-s", "/bin/zsh", "joe"]
    )
    return


def test_user_add_to_group_skipped_when_already_member():
    """
        add_to_group() returns SKIPPED when the user is already a member.
    """
    runner = _MockRunner()
    runner.results[("getent", "group", "wheel")] = _group_row(
        "wheel", members="root,joe",
    )
    um = UserManager(runner, UserGroupManagerConfig())
    result = um.add_to_group("joe", "wheel")
    testbase.assert_equal(result.status, ActionStatus.SKIPPED)
    return


def test_user_add_to_group_dispatches_usermod():
    """
        add_to_group() dispatches ``usermod -aG`` when the user is absent.
    """
    runner = _MockRunner()
    runner.results[("getent", "group", "wheel")] = _group_row("wheel")
    um = UserManager(runner, UserGroupManagerConfig(elevated=True))
    result = um.add_to_group("joe", "wheel")
    testbase.assert_equal(result.status, ActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"], ["usermod", "-aG", "wheel", "joe"]
    )
    return


def test_user_remove_from_group_skipped_when_not_member():
    """
        remove_from_group() returns SKIPPED when the user is not a member.
    """
    runner = _MockRunner()
    runner.results[("getent", "group", "wheel")] = _group_row("wheel")
    um = UserManager(runner, UserGroupManagerConfig())
    result = um.remove_from_group("joe", "wheel")
    testbase.assert_equal(result.status, ActionStatus.SKIPPED)
    return


def test_user_lock_and_unlock():
    """
        lock() and unlock() honour the current locked state.
    """
    runner = _MockRunner()
    runner.results[("passwd", "-S", "joe")] = RunResult(
        exit_code=0, stdout="joe P 2024-01-01 0 99999 7 -1", stderr="",
        command=["passwd"],
    )
    um = UserManager(runner, UserGroupManagerConfig(elevated=True))
    result = um.lock("joe")
    testbase.assert_equal(result.status, ActionStatus.SUCCESS)
    testbase.assert_equal(runner.calls[-1]["args"], ["usermod", "-L", "joe"])

    # Flip the state to locked and call unlock.
    runner.results[("passwd", "-S", "joe")] = RunResult(
        exit_code=0, stdout="joe L 2024-01-01 0 99999 7 -1", stderr="",
        command=["passwd"],
    )
    result = um.unlock("joe")
    testbase.assert_equal(result.status, ActionStatus.SUCCESS)
    testbase.assert_equal(runner.calls[-1]["args"], ["usermod", "-U", "joe"])
    return


def test_user_set_password_age_dispatches_chage():
    """
        set_password_age() emits chage with the configured flags.
    """
    runner = _MockRunner()
    um = UserManager(runner, UserGroupManagerConfig(elevated=True))
    result = um.set_password_age(
        "joe",
        PasswordAgeSpec(max_days=90, min_days=7, expiry_date="2030-01-01"),
    )
    testbase.assert_equal(result.status, ActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"],
        ["chage", "-M", "90", "-m", "7", "-E", "2030-01-01", "joe"],
    )
    return


def test_user_add_ssh_key_skipped_when_present():
    """
        add_ssh_key() returns SKIPPED when the key is already authorised.
    """
    runner = _MockRunner()
    runner.results[("getent", "passwd", "joe")] = _user_row("joe")
    runner.results[("passwd", "-S", "joe")] = RunResult(
        exit_code=0, stdout="joe P 2024-01-01 0 99999 7 -1", stderr="",
        command=["passwd"],
    )
    runner.results[("cat", "/home/joe/.ssh/authorized_keys")] = RunResult(
        exit_code=0,
        stdout="ssh-rsa AAA some-key\n",
        stderr="",
        command=["cat"],
    )
    um = UserManager(runner, UserGroupManagerConfig(elevated=True))
    result = um.add_ssh_key("joe", "ssh-rsa AAA some-key")
    testbase.assert_equal(result.status, ActionStatus.SKIPPED)
    return


def test_user_add_ssh_key_writes_keys_file():
    """
        add_ssh_key() writes a new authorized_keys via tee with stdin payload.
    """
    runner = _MockRunner()
    runner.results[("getent", "passwd", "joe")] = _user_row("joe")
    runner.results[("passwd", "-S", "joe")] = RunResult(
        exit_code=0, stdout="joe P 2024-01-01 0 99999 7 -1", stderr="",
        command=["passwd"],
    )
    runner.results[("cat", "/home/joe/.ssh/authorized_keys")] = _absent()
    um = UserManager(runner, UserGroupManagerConfig(elevated=True))
    result = um.add_ssh_key("joe", "ssh-ed25519 NEW user@host")
    testbase.assert_equal(result.status, ActionStatus.SUCCESS)
    tee_calls = [c for c in runner.calls if c["args"][0] == "tee"]
    testbase.assert_equal(len(tee_calls), 1)
    testbase.assert_true("ssh-ed25519 NEW user@host" in tee_calls[0]["stdin"])
    return


def test_group_create_when_absent():
    """
        GroupManager.create() dispatches groupadd when the group is missing.
    """
    runner = _MockRunner()
    runner.results[("getent", "group", "devs")] = _absent()
    gm = GroupManager(runner, UserGroupManagerConfig(elevated=True))
    result = gm.create(GroupSpec(groupname="devs", gid=2100))
    testbase.assert_equal(result.status, ActionStatus.SUCCESS)
    testbase.assert_equal(
        runner.calls[-1]["args"], ["groupadd", "-g", "2100", "devs"]
    )
    return


def test_group_create_skipped_when_present():
    """
        GroupManager.create() returns SKIPPED when the group already exists.
    """
    runner = _MockRunner()
    runner.results[("getent", "group", "devs")] = _group_row("devs")
    gm = GroupManager(runner, UserGroupManagerConfig())
    result = gm.create(GroupSpec(groupname="devs"))
    testbase.assert_equal(result.status, ActionStatus.SKIPPED)
    return


def test_group_delete_skipped_when_absent():
    """
        GroupManager.delete() returns SKIPPED when the group is absent.
    """
    runner = _MockRunner()
    runner.results[("getent", "group", "ghosts")] = _absent()
    gm = GroupManager(runner, UserGroupManagerConfig())
    result = gm.delete("ghosts")
    testbase.assert_equal(result.status, ActionStatus.SKIPPED)
    return


def test_group_query_parses_members():
    """
        GroupManager.query() parses the members field correctly.
    """
    runner = _MockRunner()
    runner.results[("getent", "group", "devs")] = _group_row(
        "devs", members="alice,bob",
    )
    gm = GroupManager(runner, UserGroupManagerConfig())
    info = gm.query("devs")
    testbase.assert_true(info is not None)
    testbase.assert_equal(info.members, ["alice", "bob"])
    return
