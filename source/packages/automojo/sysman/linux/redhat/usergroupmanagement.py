# automojo.sysman.linux.redhat.usergroupmanagement
#
# UserManager and GroupManager classes that manage users, groups,
# group membership, SSH authorized keys, account lock state, and
# password age through the ICommandRunner abstraction.

import os

from dataclasses import dataclass
from typing import Optional

from automojo.sysman.linux.redhat.featurebase import (
    ActionStatus,
    BaseFeatureManager,
    FeatureConfig,
)
from automojo.sysman.linux.redhat.runners.models import RunResult


# Re-export ActionStatus at module level — callers and tests import it
# from this module by historical convention.


@dataclass
class UserGroupManagerConfig(FeatureConfig):
    """
        Configuration for :class:`UserManager` and :class:`GroupManager`.
        Inherits ``dry_run`` and ``elevated`` from :class:`FeatureConfig`.
    """
    pass


@dataclass
class UserSpec:
    """
        Specification for creating a new user.

        :param username: Login name for the new account.
        :param uid: Optional UID; system allocates one when None.
        :param home: Optional home directory path.
        :param shell: Optional login shell.
        :param primary_group: Optional primary group name.
        :param comment: Optional GECOS/comment field.
        :param system_account: True when creating a system account.
    """
    username: str
    uid: Optional[int] = None
    home: Optional[str] = None
    shell: Optional[str] = None
    primary_group: Optional[str] = None
    comment: Optional[str] = None
    system_account: bool = False


@dataclass
class UserModSpec:
    """
        Specification for modifying an existing user account.

        :param shell: New login shell.
        :param home: New home directory.
        :param move_home: Move existing content when changing home.
        :param primary_group: New primary group.
        :param comment: New GECOS/comment field.
        :param uid: New UID.
    """
    shell: Optional[str] = None
    home: Optional[str] = None
    move_home: bool = False
    primary_group: Optional[str] = None
    comment: Optional[str] = None
    uid: Optional[int] = None


@dataclass
class GroupSpec:
    """
        Specification for creating a new group.

        :param groupname: Group name.
        :param gid: Optional GID; system allocates one when None.
        :param system_group: True when creating a system group.
    """
    groupname: str
    gid: Optional[int] = None
    system_group: bool = False


@dataclass
class PasswordAgeSpec:
    """
        Specification for password/account age policy.

        :param max_days: Maximum number of days between password changes.
        :param min_days: Minimum number of days between password changes.
        :param expiry_date: Account expiry date in ``YYYY-MM-DD`` format.
    """
    max_days: Optional[int] = None
    min_days: Optional[int] = None
    expiry_date: Optional[str] = None


@dataclass
class UserInfo:
    """
        Information about an existing user account.

        :param username: Login name.
        :param uid: User identifier.
        :param gid: Primary group identifier.
        :param comment: GECOS/comment field.
        :param home: Home directory.
        :param shell: Login shell.
        :param locked: True when the account is locked.
    """
    username: str
    uid: int
    gid: int
    comment: str
    home: str
    shell: str
    locked: bool


@dataclass
class GroupInfo:
    """
        Information about an existing group.

        :param groupname: Group name.
        :param gid: Group identifier.
        :param members: Supplementary members of the group.
    """
    groupname: str
    gid: int
    members: list[str]


@dataclass
class UserActionResult:
    """
        Result returned by user management write operations.
    """
    status: ActionStatus
    username: str
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


@dataclass
class GroupActionResult:
    """
        Result returned by group management write operations.
    """
    status: ActionStatus
    groupname: str
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    command: Optional[list[str]] = None


class UserNotFoundError(Exception):
    """
        Raised when a user management operation requires an existing user.
    """

    def __init__(self, *, username: str) -> None:
        self.username = username
        err_msg_lines = ["User not found."]
        err_msg_lines.append("    USERNAME: {}".format(username))
        err_msg = os.linesep.join(err_msg_lines)
        super().__init__(err_msg)
        return


class GroupNotFoundError(Exception):
    """
        Raised when a group management operation requires an existing group.
    """

    def __init__(self, *, groupname: str) -> None:
        self.groupname = groupname
        err_msg_lines = ["Group not found."]
        err_msg_lines.append("    GROUPNAME: {}".format(groupname))
        err_msg = os.linesep.join(err_msg_lines)
        super().__init__(err_msg)
        return


class GroupManager(BaseFeatureManager):
    """
        Facade for local group management through the command runner.
        Keeps its own :class:`GroupActionResult` shape (``groupname``
        required) and does not use the base class's ``_dispatch``.
    """

    feature_name = "user-group-management"

    @property
    def config(self) -> UserGroupManagerConfig:
        """
            Narrow-typed alias for :attr:`BaseFeatureManager.config`.
        """
        return self._config

    def create(self, spec: GroupSpec) -> GroupActionResult:
        """
            Create a local group described by ``spec``.  Returns ``SKIPPED``
            when the group already exists.
        """
        existing = self.query(spec.groupname)
        if existing is not None:
            skipped = GroupActionResult(
                status=ActionStatus.SKIPPED,
                groupname=spec.groupname,
            )
            return skipped

        command = ["groupadd"]
        if spec.system_group:
            command.append("-r")
        if spec.gid is not None:
            command.extend(["-g", str(spec.gid)])
        command.append(spec.groupname)

        if self._config.dry_run:
            dry = GroupActionResult(
                status=ActionStatus.DRY_RUN,
                groupname=spec.groupname,
                command=command,
            )
            return dry

        run_result = self._runner.run(command, elevated=self._config.elevated)
        action_result = _group_result(
            groupname=spec.groupname, run_result=run_result
        )
        return action_result

    def delete(self, groupname: str) -> GroupActionResult:
        """
            Delete a local group.  Returns ``SKIPPED`` when the group is absent.
        """
        existing = self.query(groupname)
        if existing is None:
            skipped = GroupActionResult(
                status=ActionStatus.SKIPPED,
                groupname=groupname,
            )
            return skipped

        command = ["groupdel", groupname]
        if self._config.dry_run:
            dry = GroupActionResult(
                status=ActionStatus.DRY_RUN,
                groupname=groupname,
                command=command,
            )
            return dry

        run_result = self._runner.run(command, elevated=self._config.elevated)
        action_result = _group_result(groupname=groupname, run_result=run_result)
        return action_result

    def query(self, groupname: str) -> Optional[GroupInfo]:
        """
            Return :class:`GroupInfo` for a group, or None when absent.
        """
        command = ["getent", "group", groupname]
        run_result = self._runner.run(command, elevated=False)
        if run_result.exit_code != 0:
            return None
        info = _parse_getent_group(run_result.stdout)
        return info


class UserManager(BaseFeatureManager):
    """
        Facade for local user management through the command runner.
        Keeps its own :class:`UserActionResult` shape (``username``
        required) and does not use the base class's ``_dispatch``.
    """

    feature_name = "user-group-management"

    @property
    def config(self) -> UserGroupManagerConfig:
        """
            Narrow-typed alias for :attr:`BaseFeatureManager.config`.
        """
        return self._config

    def create(self, spec: UserSpec) -> UserActionResult:
        """
            Create a local user described by ``spec``.  Returns ``SKIPPED``
            when the user already exists.
        """
        existing = self.query(spec.username)
        if existing is not None:
            skipped = UserActionResult(
                status=ActionStatus.SKIPPED,
                username=spec.username,
            )
            return skipped

        command = ["useradd"]
        if spec.system_account:
            command.append("-r")
        if spec.uid is not None:
            command.extend(["-u", str(spec.uid)])
        if spec.home is not None:
            command.extend(["-d", spec.home])
        if spec.shell is not None:
            command.extend(["-s", spec.shell])
        if spec.primary_group is not None:
            command.extend(["-g", spec.primary_group])
        if spec.comment is not None:
            command.extend(["-c", spec.comment])
        command.append(spec.username)

        if self._config.dry_run:
            dry = UserActionResult(
                status=ActionStatus.DRY_RUN,
                username=spec.username,
                command=command,
            )
            return dry

        run_result = self._runner.run(command, elevated=self._config.elevated)
        action_result = _user_result(username=spec.username, run_result=run_result)
        return action_result

    def delete(
        self,
        username: str,
        *,
        remove_home: bool = False,
    ) -> UserActionResult:
        """
            Delete a local user.  Returns ``SKIPPED`` when the user is absent.
        """
        existing = self.query(username)
        if existing is None:
            skipped = UserActionResult(
                status=ActionStatus.SKIPPED,
                username=username,
            )
            return skipped

        command = ["userdel"]
        if remove_home:
            command.append("-r")
        command.append(username)

        if self._config.dry_run:
            dry = UserActionResult(
                status=ActionStatus.DRY_RUN,
                username=username,
                command=command,
            )
            return dry

        run_result = self._runner.run(command, elevated=self._config.elevated)
        action_result = _user_result(username=username, run_result=run_result)
        return action_result

    def modify(self, username: str, spec: UserModSpec) -> UserActionResult:
        """
            Modify an existing user account.  Raises :class:`UserNotFoundError`
            when the user does not exist.
        """
        existing = self.query(username)
        if existing is None:
            raise UserNotFoundError(username=username)

        command = ["usermod"]
        if spec.shell is not None:
            command.extend(["-s", spec.shell])
        if spec.home is not None:
            command.extend(["-d", spec.home])
            if spec.move_home:
                command.append("-m")
        if spec.primary_group is not None:
            command.extend(["-g", spec.primary_group])
        if spec.comment is not None:
            command.extend(["-c", spec.comment])
        if spec.uid is not None:
            command.extend(["-u", str(spec.uid)])
        command.append(username)

        if len(command) == 2:
            skipped = UserActionResult(
                status=ActionStatus.SKIPPED,
                username=username,
            )
            return skipped

        if self._config.dry_run:
            dry = UserActionResult(
                status=ActionStatus.DRY_RUN,
                username=username,
                command=command,
            )
            return dry

        run_result = self._runner.run(command, elevated=self._config.elevated)
        action_result = _user_result(username=username, run_result=run_result)
        return action_result

    def query(self, username: str) -> Optional[UserInfo]:
        """
            Return :class:`UserInfo` for a user, or None when absent.
        """
        command = ["getent", "passwd", username]
        run_result = self._runner.run(command, elevated=False)
        if run_result.exit_code != 0:
            return None
        base = _parse_getent_passwd(run_result.stdout)
        if base is None:
            return None
        locked = self._is_locked(username=username)
        info = UserInfo(
            username=base["username"],
            uid=base["uid"],
            gid=base["gid"],
            comment=base["comment"],
            home=base["home"],
            shell=base["shell"],
            locked=locked,
        )
        return info

    def add_to_group(
        self,
        username: str,
        groupname: str,
    ) -> UserActionResult:
        """
            Add ``username`` to ``groupname`` as a supplementary member.
        """
        if self._user_in_group(username=username, groupname=groupname):
            skipped = UserActionResult(
                status=ActionStatus.SKIPPED,
                username=username,
            )
            return skipped
        command = ["usermod", "-aG", groupname, username]
        return self._write_user(username=username, command=command)

    def remove_from_group(
        self,
        username: str,
        groupname: str,
    ) -> UserActionResult:
        """
            Remove ``username`` from a supplementary group.
        """
        if not self._user_in_group(username=username, groupname=groupname):
            skipped = UserActionResult(
                status=ActionStatus.SKIPPED,
                username=username,
            )
            return skipped
        command = ["gpasswd", "-d", username, groupname]
        return self._write_user(username=username, command=command)

    def lock(self, username: str) -> UserActionResult:
        """
            Lock a user account.
        """
        if self._is_locked(username=username):
            skipped = UserActionResult(
                status=ActionStatus.SKIPPED,
                username=username,
            )
            return skipped
        command = ["usermod", "-L", username]
        return self._write_user(username=username, command=command)

    def unlock(self, username: str) -> UserActionResult:
        """
            Unlock a user account.
        """
        if not self._is_locked(username=username):
            skipped = UserActionResult(
                status=ActionStatus.SKIPPED,
                username=username,
            )
            return skipped
        command = ["usermod", "-U", username]
        return self._write_user(username=username, command=command)

    def expire_password(self, username: str) -> UserActionResult:
        """
            Force a password change on next login.
        """
        command = ["passwd", "--expire", username]
        return self._write_user(username=username, command=command)

    def set_password(self, username: str, credential) -> UserActionResult:
        """
            Set the user's password via ``chpasswd``.  The password is
            supplied through the runner's ``stdin`` channel so it never
            appears on argv (Part C seam — credential material no
            longer leaks into process listings).

            :param username: Login name whose password to set.
            :param credential: A credential object with a ``.password``
                               attribute (e.g.
                               :class:`automojo.credentials.basiccredential.BasicCredential`).
                               The credential's ``.username`` is
                               ignored — this method uses the explicit
                               ``username`` argument.

            The returned :class:`UserActionResult.command` field records
            only ``['chpasswd']`` — never the password payload.
        """
        if credential is None:
            raise ValueError(
                "set_password requires a credential with .password; got None",
            )
        payload = "{}:{}\n".format(username, credential.password)
        if self._config.dry_run:
            return UserActionResult(
                status=ActionStatus.DRY_RUN,
                username=username,
                command=["chpasswd"],
            )
        run_result = self._runner.run(
            ["chpasswd"],
            elevated=self._config.elevated,
            stdin=payload,
        )
        # Use our own result construction (not _user_result) so we can
        # scrub the `command` field back to the argv-only form without
        # any runner-returned state that might include the stdin.
        if run_result.exit_code == 0:
            status = ActionStatus.SUCCESS
        else:
            status = ActionStatus.FAILED
        return UserActionResult(
            status=status,
            username=username,
            exit_code=run_result.exit_code,
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            command=["chpasswd"],
        )

    def set_password_age(
        self,
        username: str,
        spec: PasswordAgeSpec,
    ) -> UserActionResult:
        """
            Update password and account age policy using chage.
        """
        command = ["chage"]
        if spec.max_days is not None:
            command.extend(["-M", str(spec.max_days)])
        if spec.min_days is not None:
            command.extend(["-m", str(spec.min_days)])
        if spec.expiry_date is not None:
            command.extend(["-E", spec.expiry_date])
        if len(command) == 1:
            skipped = UserActionResult(
                status=ActionStatus.SKIPPED,
                username=username,
            )
            return skipped
        command.append(username)
        return self._write_user(username=username, command=command)

    def add_ssh_key(
        self,
        username: str,
        public_key: str,
    ) -> UserActionResult:
        """
            Append a public SSH key to ``authorized_keys``.  Idempotent.
        """
        info = self.query(username)
        if info is None:
            raise UserNotFoundError(username=username)

        existing_keys = self._read_authorized_keys(home=info.home)
        normalized_key = public_key.strip()
        if normalized_key in existing_keys:
            skipped = UserActionResult(
                status=ActionStatus.SKIPPED,
                username=username,
            )
            return skipped

        new_keys = list(existing_keys)
        new_keys.append(normalized_key)
        return self._write_authorized_keys(
            username=username,
            home=info.home,
            keys=new_keys,
        )

    def remove_ssh_key(
        self,
        username: str,
        public_key: str,
    ) -> UserActionResult:
        """
            Remove a public SSH key from ``authorized_keys``.  Idempotent.
        """
        info = self.query(username)
        if info is None:
            raise UserNotFoundError(username=username)

        existing_keys = self._read_authorized_keys(home=info.home)
        normalized_key = public_key.strip()
        if normalized_key not in existing_keys:
            skipped = UserActionResult(
                status=ActionStatus.SKIPPED,
                username=username,
            )
            return skipped

        new_keys = [k for k in existing_keys if k != normalized_key]
        return self._write_authorized_keys(
            username=username,
            home=info.home,
            keys=new_keys,
        )

    def _write_user(
        self,
        *,
        username: str,
        command: list[str],
    ) -> UserActionResult:
        """
            Issue a user-management write command honouring dry-run.
        """
        if self._config.dry_run:
            dry = UserActionResult(
                status=ActionStatus.DRY_RUN,
                username=username,
                command=command,
            )
            return dry
        run_result = self._runner.run(command, elevated=self._config.elevated)
        action_result = _user_result(username=username, run_result=run_result)
        return action_result

    def _user_in_group(
        self,
        *,
        username: str,
        groupname: str,
    ) -> bool:
        """
            Return True when ``username`` appears in the supplementary members
            of ``groupname``.
        """
        command = ["getent", "group", groupname]
        run_result = self._runner.run(command, elevated=False)
        if run_result.exit_code != 0:
            return False
        info = _parse_getent_group(run_result.stdout)
        if info is None:
            return False
        return username in info.members

    def _is_locked(self, *, username: str) -> bool:
        """
            Return True when ``passwd -S`` reports the account as locked.
        """
        command = ["passwd", "-S", username]
        run_result = self._runner.run(command, elevated=self._config.elevated)
        if run_result.exit_code != 0:
            return False
        parts = run_result.stdout.split()
        if len(parts) < 2:
            return False
        flag = parts[1]
        is_locked = flag == "L"
        return is_locked

    def _read_authorized_keys(self, *, home: str) -> list[str]:
        """
            Read ``<home>/.ssh/authorized_keys`` into a list of lines.
            Returns an empty list when the file is absent.
        """
        path = "{}/.ssh/authorized_keys".format(home)
        run_result = self._runner.run(
            ["cat", path], elevated=self._config.elevated
        )
        if run_result.exit_code != 0:
            return []
        raw_lines = run_result.stdout.splitlines()
        trimmed: list[str] = []
        for raw in raw_lines:
            line = raw.strip()
            if len(line) == 0:
                continue
            trimmed.append(line)
        return trimmed

    def _write_authorized_keys(
        self,
        *,
        username: str,
        home: str,
        keys: list[str],
    ) -> UserActionResult:
        """
            Replace the ``authorized_keys`` file with ``keys``.  Ensures the
            ``.ssh`` directory exists with conservative permissions.
        """
        commands: list[tuple[list[str], Optional[str]]] = []
        ssh_dir = "{}/.ssh".format(home)
        keys_path = "{}/.ssh/authorized_keys".format(home)
        body = "\n".join(keys) + "\n"
        owner_spec = "{}:{}".format(username, username)

        commands.append((["mkdir", "-p", ssh_dir], None))
        commands.append((["chmod", "0700", ssh_dir], None))
        commands.append((["chown", owner_spec, ssh_dir], None))
        commands.append((["tee", keys_path], body))
        commands.append((["chmod", "0600", keys_path], None))
        commands.append((["chown", owner_spec, keys_path], None))

        if self._config.dry_run:
            dry = UserActionResult(
                status=ActionStatus.DRY_RUN,
                username=username,
                command=commands[-1][0],
            )
            return dry

        last_result: Optional[RunResult] = None
        for command, stdin_payload in commands:
            run_result = self._runner.run(
                command,
                elevated=self._config.elevated,
                stdin=stdin_payload,
            )
            last_result = run_result
            if run_result.exit_code != 0:
                failed = UserActionResult(
                    status=ActionStatus.FAILED,
                    username=username,
                    exit_code=run_result.exit_code,
                    stdout=run_result.stdout,
                    stderr=run_result.stderr,
                    command=list(run_result.command),
                )
                return failed
        assert last_result is not None
        success = UserActionResult(
            status=ActionStatus.SUCCESS,
            username=username,
            exit_code=last_result.exit_code,
            stdout=last_result.stdout,
            stderr=last_result.stderr,
            command=list(last_result.command),
        )
        return success


def _parse_getent_passwd(stdout: str) -> Optional[dict]:
    """
        Parse a single ``getent passwd`` line into a dict of fields.
    """
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if len(line) == 0:
            continue
        fields = line.split(":")
        if len(fields) < 7:
            continue
        parsed = {
            "username": fields[0],
            "uid": int(fields[2]),
            "gid": int(fields[3]),
            "comment": fields[4],
            "home": fields[5],
            "shell": fields[6],
        }
        return parsed
    return None


def _parse_getent_group(stdout: str) -> Optional[GroupInfo]:
    """
        Parse a ``getent group`` line into a :class:`GroupInfo`.
    """
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if len(line) == 0:
            continue
        fields = line.split(":")
        if len(fields) < 4:
            continue
        member_field = fields[3]
        members: list[str] = []
        if len(member_field) > 0:
            for member in member_field.split(","):
                stripped = member.strip()
                if len(stripped) > 0:
                    members.append(stripped)
        info = GroupInfo(
            groupname=fields[0],
            gid=int(fields[2]),
            members=members,
        )
        return info
    return None


def _user_result(*, username: str, run_result: RunResult) -> UserActionResult:
    """
        Map a :class:`RunResult` to a :class:`UserActionResult`.
    """
    status = ActionStatus.SUCCESS if run_result.exit_code == 0 else ActionStatus.FAILED
    result = UserActionResult(
        status=status,
        username=username,
        exit_code=run_result.exit_code,
        stdout=run_result.stdout,
        stderr=run_result.stderr,
        command=list(run_result.command),
    )
    return result


def _group_result(*, groupname: str, run_result: RunResult) -> GroupActionResult:
    """
        Map a :class:`RunResult` to a :class:`GroupActionResult`.
    """
    status = ActionStatus.SUCCESS if run_result.exit_code == 0 else ActionStatus.FAILED
    result = GroupActionResult(
        status=status,
        groupname=groupname,
        exit_code=run_result.exit_code,
        stdout=run_result.stdout,
        stderr=run_result.stderr,
        command=list(run_result.command),
    )
    return result
