# Design

## Overview

The user and group management feature is implemented as two cooperating classes in a single
module, `ccai.sysman.linux.redhat.usergroupmanagement`:

- **`UserManager`** — handles user lifecycle, group membership, SSH keys, lock state, and
  password expiry.
- **`GroupManager`** — handles group lifecycle.

Both classes are constructed with the same command runner implementing the `ICommandRunner`
interface and `UserGroupManagerConfig`. The coordinator instantiates them directly and calls
their methods to perform discrete maintenance actions.

All system interaction is delegated to the command runner. Reads from `/etc/passwd`,
`/etc/group`, and `authorized_keys` are performed via `getent` and `cat` through the
runner. No direct file system access or `subprocess` calls are made inside the module.

The feature works identically whether commands execute locally (via `LocalCommandRunner`) or
remotely over SSH (via `SSHCommandRunner`). When using SSH, the coordinator can track
reliability metrics and apply execution aspects (persistence, retry, timeout) without any
changes to the `UserManager` or `GroupManager` code.

**See Also:** [Command Runner Architecture](../command-runner-architecture/design.md) for
details on local/SSH execution, reliability tracking, and aspect configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Maintenance Coordinator                        │
│  - Instantiates runner (Local or SSH)                      │
│  - Configures aspects (optional)                           │
│  - Injects runner into UserManager and GroupManager        │
└────────────────────────┬────────────────────────────────────┘
                         │ instantiates & injects
                 ┌───────┴───────┐
                 ▼               ▼
┌─────────────────────────┐  ┌──────────────────────────┐
│      UserManager        │  │      GroupManager        │
│                         │  │                          │
│ create()    lock()      │  │ create()                 │
│ delete()    unlock()    │  │ delete()                 │
│ modify()    expire_pw() │  │ query()                  │
│ query()     set_pw_age()│  │                          │
│ add_to_group()          │  │                          │
│ remove_from_group()     │  │                          │
│ add_ssh_key()           │  │                          │
│ remove_ssh_key()        │  │                          │
└────────────┬────────────┘  └──────────┬───────────────┘
             └────────────┬──────────────┘
                          │ delegates to
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                   ICommandRunner                            │
│  run(args, elevated, cwd, env, timeout) → RunResult         │
└────────────────────────┬────────────────────────────────────┘
                         │
          ┌──────────────┴──────────────┐
          ▼                             ▼
┌──────────────────────┐      ┌──────────────────────┐
│ LocalCommandRunner   │      │ SSHCommandRunner     │
│ - subprocess.run()   │      │ - SSH connection     │
│ - sudo for elevation │      │ - reliability metrics│
│ - local execution    │      │ - connection retry   │
└──────────────────────┘      └──────────────────────┘
          │                             │
          └──────────────┬──────────────┘
                         │ optional
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    Execution Aspects                        │
│  - PersistenceAspect: audit logging                         │
│  - RetryAspect: transient failure retry                     │
│  - TimeoutAspect: global timeout policy                     │
└─────────────────────────────────────────────────────────────┘
```

---

## Components

### `UserManager`

**Location:** `source/packages/ccai/sysman/linux/redhat/usergroupmanagement.py`

| Method | Signature | Description |
|---|---|---|
| `create` | `(spec: UserSpec) → UserActionResult` | Create a local user |
| `delete` | `(username, remove_home=False) → UserActionResult` | Delete a local user |
| `modify` | `(username, spec: UserModSpec) → UserActionResult` | Modify user properties |
| `query` | `(username) → UserInfo \| None` | Query user from `/etc/passwd` |
| `add_to_group` | `(username, groupname) → UserActionResult` | Add user to supplementary group |
| `remove_from_group` | `(username, groupname) → UserActionResult` | Remove user from group |
| `add_ssh_key` | `(username, public_key) → UserActionResult` | Append SSH key |
| `remove_ssh_key` | `(username, public_key) → UserActionResult` | Remove SSH key |
| `lock` | `(username) → UserActionResult` | Lock user account |
| `unlock` | `(username) → UserActionResult` | Unlock user account |
| `expire_password` | `(username) → UserActionResult` | Force immediate password expiry |
| `set_password_age` | `(username, spec: PasswordAgeSpec) → UserActionResult` | Set password age policy |

### `GroupManager`

| Method | Signature | Description |
|---|---|---|
| `create` | `(spec: GroupSpec) → GroupActionResult` | Create a local group |
| `delete` | `(groupname) → GroupActionResult` | Delete a local group |
| `query` | `(groupname) → GroupInfo \| None` | Query group from `/etc/group` |

### `UserGroupManagerConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| `dry_run` | `bool` | `False` | When `True`, no commands are executed |
| `elevated` | `bool` | `False` | When `True`, write commands pass `elevated=True` |

---

## Data Models

### Input Specs

```python
@dataclass
class UserSpec:
    username: str
    uid: int | None = None
    home: str | None = None
    shell: str | None = None
    primary_group: str | None = None
    comment: str | None = None
    system_account: bool = False

@dataclass
class UserModSpec:
    shell: str | None = None
    home: str | None = None
    move_home: bool = False
    primary_group: str | None = None
    comment: str | None = None
    uid: int | None = None

@dataclass
class GroupSpec:
    groupname: str
    gid: int | None = None
    system_group: bool = False

@dataclass
class PasswordAgeSpec:
    max_days: int | None = None
    min_days: int | None = None
    expiry_date: str | None = None   # ISO 8601 date string: YYYY-MM-DD
```

### Result Types

```python
@dataclass
class UserActionResult:
    status: ActionStatus
    username: str
    exit_code: int | None
    stdout: str | None
    stderr: str | None
    command: list[str] | None

@dataclass
class GroupActionResult:
    status: ActionStatus
    groupname: str
    exit_code: int | None
    stdout: str | None
    stderr: str | None
    command: list[str] | None

class ActionStatus(Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"
    DRY_RUN  = "dry_run"
```

### Query Result Types

```python
@dataclass
class UserInfo:
    username: str
    uid: int
    gid: int
    comment: str
    home: str
    shell: str
    locked: bool           # derived from /etc/shadow '!' prefix on password hash

@dataclass
class GroupInfo:
    groupname: str
    gid: int
    members: list[str]
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Idempotent User Creation

*For any* username, calling `UserManager.create()` on an already existing user should return `SKIPPED` status without invoking the command runner.

**Validates: Requirements 1.2**

### Property 2: Idempotent User Deletion

*For any* username, calling `UserManager.delete()` on a non-existent user should return `SKIPPED` status without invoking the command runner.

**Validates: Requirements 2.3**

### Property 3: Idempotent Group Creation

*For any* groupname, calling `GroupManager.create()` on an already existing group should return `SKIPPED` status without invoking the command runner.

**Validates: Requirements 5.4**

### Property 4: Idempotent Group Deletion

*For any* groupname, calling `GroupManager.delete()` on a non-existent group should return `SKIPPED` status without invoking the command runner.

**Validates: Requirements 6.2**

### Property 5: Idempotent Group Membership Addition

*For any* username and groupname, calling `UserManager.add_to_group()` when the user is already a member should return `SKIPPED` status without invoking the command runner.

**Validates: Requirements 7.2**

### Property 6: Idempotent Group Membership Removal

*For any* username and groupname, calling `UserManager.remove_from_group()` when the user is not a member should return `SKIPPED` status without invoking the command runner.

**Validates: Requirements 8.2**

### Property 7: Idempotent SSH Key Addition

*For any* username and public key, calling `UserManager.add_ssh_key()` when the key is already present in authorized_keys should return `SKIPPED` status without modifying the file.

**Validates: Requirements 9.2**

### Property 8: Idempotent SSH Key Removal

*For any* username and public key, calling `UserManager.remove_ssh_key()` when the key is not present in authorized_keys should return `SKIPPED` status without modifying the file.

**Validates: Requirements 9.5**

### Property 9: Idempotent Account Lock

*For any* username, calling `UserManager.lock()` on an already locked account should return `SKIPPED` status without invoking the command runner.

**Validates: Requirements 10.2**

### Property 10: Idempotent Account Unlock

*For any* username, calling `UserManager.unlock()` on an already unlocked account should return `SKIPPED` status without invoking the command runner.

**Validates: Requirements 10.4**

### Property 11: Dry-Run Safety

*For any* write operation (create user/group, delete user/group, modify user, add/remove group member, add/remove SSH key, lock/unlock, expire password, set password age) with `dry_run=True`, the command runner should never be invoked, and the result status should be `DRY_RUN`.

**Validates: Requirements 1.11, 2.6, 3.10, 5.7, 6.5, 7.7, 8.7, 9.8, 10.8, 11.8**

### Property 12: User Existence Preconditions

*For any* username, operations that require an existing user (modify, add_to_group, remove_from_group, add_ssh_key, remove_ssh_key, lock, unlock, expire_password, set_password_age) should raise `UserNotFoundError` when called on a non-existent user.

**Validates: Requirements 3.2, 7.3, 8.3, 9.7, 10.5, 11.5**

### Property 13: Group Existence Preconditions

*For any* groupname, operations that require an existing group (add_to_group, remove_from_group) should raise `GroupNotFoundError` when called with a non-existent group.

**Validates: Requirements 7.4, 8.4**

### Property 14: SSH Key Validation

*For any* username, calling `UserManager.add_ssh_key()` or `UserManager.remove_ssh_key()` with an empty or whitespace-only key string should raise `UserKeyError` before any command runner invocation.

**Validates: Requirements 9.6**

### Property 15: Command Construction Correctness

*For any* user or group operation, the constructed command arguments passed to the runner should match the expected command format specified in the Command Mappings table.

**Validates: Requirements 1.1, 1.3-1.8, 2.1-2.2, 3.1, 3.3-3.7, 4.1, 5.1-5.3, 6.1, 7.1, 8.1, 9.1, 9.3-9.4, 10.1, 10.3, 11.1-11.4**

### Property 16: Elevation Consistency

*For any* write operation with `elevated=True` in the config, the command runner should be invoked with `elevated=True`; for read operations (query user, query group), `elevated` should never be `True`.

**Validates: Requirements 12.1, 12.2**

### Property 17: Query Consistency

*For any* username or groupname, if `query()` returns an info object, then calling `query()` again immediately should return an equivalent info object with the same field values.

**Validates: Requirements 4.2, 4.3**

### Property 18: Status Mapping Correctness

*For any* operation result from the command runner, the action result status should correctly reflect the runner's exit code: 0 maps to `SUCCESS`, non-zero maps to `FAILED` (unless the operation was skipped or in dry-run mode).

**Validates: Requirements 1.9, 1.10, 2.4, 2.5, 3.8, 3.9, 5.5, 5.6, 6.3, 6.4, 7.5, 7.6, 8.5, 8.6, 10.6, 10.7, 11.6, 11.7**

---

## Interfaces

### `ICommandRunner` (from `codecraftai-interfaces`)

```python
class ICommandRunner(Protocol):
    def run(
        self,
        args: list[str],
        elevated: bool = False,
        capture_output: bool = True,
        stdin: str | None = None,
    ) -> RunResult: ...
```

---

## Command Mappings

### User Operations

| Operation | Command |
|---|---|
| Query user | `getent passwd <username>` |
| Query shadow lock state | `passwd -S <username>` |
| Create user (basic) | `useradd [options] <username>` |
| Delete user | `userdel [-r] <username>` |
| Modify user | `usermod [options] <username>` |
| Lock account | `usermod -L <username>` |
| Unlock account | `usermod -U <username>` |
| Add to group | `usermod -aG <groupname> <username>` |
| Remove from group | `gpasswd -d <username> <groupname>` |
| Force password expiry | `passwd --expire <username>` |
| Set max password age | `chage -M <days> <username>` |
| Set min password age | `chage -m <days> <username>` |
| Set account expiry | `chage -E <date> <username>` |

### SSH Key Operations

| Operation | Command |
|---|---|
| Read home directory | `getent passwd <username>` (parse field 6) |
| Check `.ssh` dir exists | `stat <homedir>/.ssh` |
| Create `.ssh` dir | `mkdir -p <homedir>/.ssh` then `chmod 0700` then `chown` |
| Read `authorized_keys` | `cat <homedir>/.ssh/authorized_keys` |
| Write `authorized_keys` | `tee <homedir>/.ssh/authorized_keys` with content on stdin |
| Set `authorized_keys` mode | `chmod 0600 <homedir>/.ssh/authorized_keys` |

### Group Operations

| Operation | Command |
|---|---|
| Query group | `getent group <groupname>` |
| Create group | `groupadd [options] <groupname>` |
| Delete group | `groupdel <groupname>` |

---

## Idempotency Guards

| Method | Guard | Source of truth |
|---|---|---|
| `UserManager.create()` | User exists | `getent passwd <username>` exit 0 |
| `UserManager.delete()` | User absent | `getent passwd <username>` exit 2 |
| `UserManager.modify()` | User absent | raises `UserNotFoundError` |
| `UserManager.add_to_group()` | User already in group | parse `getent group <groupname>` members |
| `UserManager.remove_from_group()` | User not in group | parse `getent group <groupname>` members |
| `UserManager.add_ssh_key()` | Key already in file | `cat authorized_keys` line match |
| `UserManager.remove_ssh_key()` | Key absent from file | `cat authorized_keys` line match |
| `UserManager.lock()` | Account already locked | `passwd -S` output contains `L` flag |
| `UserManager.unlock()` | Account already unlocked | `passwd -S` output contains `P` flag |
| `GroupManager.create()` | Group exists | `getent group <groupname>` exit 0 |
| `GroupManager.delete()` | Group absent | `getent group <groupname>` exit 2 |

---

## SSH Authorized Keys Management

Keys are managed line-by-line in pure Python after reading the file via `cat`.
Matching is done on the full key string (type + base64 + optional comment).
The modified line list is serialised back to a string and written via `tee` with
`stdin`.  This avoids any shell escaping of key content.

Directory and file creation for missing `.ssh` paths follows this sequence:

```
1. mkdir -p <homedir>/.ssh             (elevated)
2. chmod 0700 <homedir>/.ssh           (elevated)
3. chown <username>:<username> .ssh    (elevated)
4. tee <homedir>/.ssh/authorized_keys  (elevated, stdin=full key file content)
5. chmod 0600 authorized_keys          (elevated)
6. chown <username>:<username> authorized_keys  (elevated)
```

---

## Error Handling

### Typed Exceptions

| Exception | Raised When |
|---|---|
| `UserNotFoundError` | `modify()`, `add_to_group()`, `remove_from_group()`, `add_ssh_key()`, `remove_ssh_key()`, `lock()`, `unlock()`, `expire_password()`, `set_password_age()` called for a username that does not exist |
| `GroupNotFoundError` | `add_to_group()` or `remove_from_group()` called for a group that does not exist |
| `UserQueryError` | `query()` fails for a reason other than user-not-found |
| `UserKeyError` | `add_ssh_key()` or `remove_ssh_key()` called with an empty key string |

---

## Module Layout

```
source/packages/ccai/sysman/linux/redhat/
├── __init__.py
└── usergroupmanagement.py        ← UserManager, GroupManager, all specs,
                                     result types, exceptions

source/testroots/testbase/
└── ccai/tests/sysman/linux/redhat/
    └── test_usergroupmanagement.py
```

---

## Testing Strategy

### Unit Testing

- All tests use a `MockCommandRunner` injected at construction time.
- `getent passwd` and `getent group` mock responses drive idempotency guard behaviour.
- `passwd -S` mock responses drive lock/unlock skip detection.
- SSH key tests supply a mock `cat authorized_keys` response and verify the `tee` stdin
  content the runner receives.
- `PasswordAgeSpec` tests verify the correct `chage` flags are assembled for each
  combination of fields.
- No test requires root access, real system users, or a live PAM stack.

### Property-Based Testing

The implementation uses the `hypothesis` library for property-based testing in Python. Each correctness property defined above will be implemented as a property-based test that:

- Generates random but valid inputs (usernames, groupnames, keys, configurations)
- Executes the operation under test
- Verifies the property holds across all generated inputs
- Runs a minimum of 100 iterations per property test

Property-based tests are tagged with comments referencing the specific correctness property they validate, using the format: `# Feature: user-group-management, Property N: <property description>`

**Property Test Coverage:**

- Properties 1-10: Idempotency tests generate random names and verify repeated operations
- Property 11: Dry-run safety tests verify no runner invocation across all operations
- Properties 12-14: Precondition tests verify correct exception raising
- Property 15: Command construction tests verify correct argument formatting
- Property 16: Elevation tests verify correct elevation flag usage
- Property 17: Query consistency tests verify stable results across multiple calls
- Property 18: Status mapping tests verify correct result interpretation

Both unit tests and property-based tests are complementary: unit tests verify specific examples and edge cases, while property tests verify universal correctness across many inputs.
