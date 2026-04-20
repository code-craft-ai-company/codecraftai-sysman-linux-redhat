# Design

## Overview

The mail transport management feature is implemented as a single
module,
`automojo.sysman.linux.redhat.mailtransportmanagement`, exposing a
`MailTransportManager` class that wraps `postconf`, `postmap`,
`newaliases`, `postqueue`, `mailq`, and `postsuper` through the
`ICommandRunner` abstraction.  It also directly reads and writes
`/etc/aliases` and map files under `/etc/postfix/` via `cat` /
`tee`.

Follows the standard manager pattern; delegates service lifecycle
to an injected `ServiceManager`.

**See Also:**
- `3-service-management` — `postfix.service` lifecycle.
- `3-firewall-management` — SMTP / submission / smtps ports.
- `3-certificate-trust-management` / `3-certificate-issuance` —
  TLS key/cert material for SMTP+TLS.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│            Maintenance Coordinator                          │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            MailTransportManager                             │
│                                                             │
│  main.cf:      get/set/remove directive                     │
│  master.cf:    get/set/remove service; per-service          │
│                directives                                   │
│  Maps:         write_map(), compile_map(),                  │
│                query_map(), remove_map()                    │
│  /etc/aliases: list_aliases(), set_alias(),                 │
│                remove_alias() (+ newaliases)                │
│  Queue:        list_queue(), flush_queue(),                 │
│                delete_message(), requeue_message(),         │
│                delete_all()                                 │
│  Service:      reload(), restart() (delegated)              │
└────────────────┬──────────────────────────┬─────────────────┘
                 │                          │
                 ▼                          ▼
      ┌─────────────────────┐   ┌─────────────────────────┐
      │   ICommandRunner    │   │ Injected ServiceManager │
      └─────────────────────┘   └─────────────────────────┘
```

---

## Configuration

```python
@dataclass
class MailTransportManagerConfig:
    dry_run: bool = False
    elevated: bool = True
    postfix_dir: str = "/etc/postfix"
    aliases_path: str = "/etc/aliases"
    autoreload: bool = False
```

---

## Data Models

```python
class MailActionStatus(Enum):
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class MailActionResult:
    status: MailActionStatus
    detail: str | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    command: list[str] | None = None


@dataclass
class MasterCfService:
    service: str                            # 'smtp', 'submission', 'pickup', ...
    type: str                               # 'inet' | 'unix' | 'fifo' | 'pass'
    private: str                            # '-' | 'y' | 'n'
    unpriv: str
    chroot: str
    wakeup: str
    maxproc: str
    command: str                            # 'smtpd', 'cleanup', ...
    directives: dict[str, str]              # service-scoped -o lines


@dataclass
class QueuedMessage:
    queue_id: str
    arrival_time: str
    size_bytes: int | None
    sender: str
    recipients: list[str]
    state: str                              # '*', '!', '' (active/hold/deferred)
    last_error: str | None
```

---

## Key Method Designs

### `set_main_directive(key, value)`

1. Call `get_main_directive(key)` (`postconf -h`).
2. If current == value → SKIPPED.
3. Dry-run: DRY_RUN.
4. Dispatch `["postconf", "-e", f"{key}={value}"]`.
5. If `config.autoreload` → `service_manager.reload("postfix")`.

### `write_map(name, content)`

1. path = `{postfix_dir}/{name}`
2. Read current content via `cat`.
3. If matches → SKIPPED (still dispatch `postmap` if `.db` is
   missing or older).
4. Dry-run: DRY_RUN.
5. Write via `tee`.
6. Call `compile_map(name)` unconditionally after a write.

### `set_alias(name, targets)`

1. Read `/etc/aliases`.
2. Parse existing aliases into `dict[str, list[str]]`.
3. If `parsed.get(name) == targets` → SKIPPED.
4. Upsert and rewrite via `tee`.
5. Dispatch `newaliases` to rebuild database.

### `list_queue()`

1. Dispatch `postqueue -p` via runner.
2. If exit is 0 AND output is `"Mail queue is empty"` → return `[]`.
3. Parse records separated by blank lines.  Each record begins
   with the queue id + size + arrival + sender line; subsequent
   lines are error text or recipients.

---

## Dry-Run Behaviour

Dry-run disables every mutating entry point (`postconf -e`,
`postconf -M`, `postconf -MX`, `postconf -X`, `postmap`, map-file
writes, alias edits, `newaliases`, queue deletes / requeues /
flushes).  Read-only queries (`get_*`, `list_aliases`,
`list_queue`, `query_map`) run freely.

---

## Correctness Properties

1. **Idempotent directive sets.** `set_main_directive` /
   `set_master_service` / `set_master_directive` / `set_alias`
   SKIP when the current value already matches.
2. **Alias database coherence.** Every mutation to
   `/etc/aliases` triggers `newaliases`.
3. **Map compile coherence.** `write_map` always invokes
   `postmap` after a content change to keep the `.db` in sync.
4. **Service delegation.** `reload()` / `restart()` always go
   through the injected `ServiceManager` — this feature never
   spells `systemctl` directly.

---

## Testing Strategy

- `_MockRunner` + `_MockServiceManager`.
- Parser tests for `postconf -h`, `postconf` (no arg),
  `postqueue -p`.
- Tests for idempotency on directive-set paths.
- Tests for the `write_map` → `postmap` chain including the
  SKIPPED content-match path still triggering `postmap` when the
  `.db` is stale.
- Tests for `newaliases` chaining after every alias mutation.
- Tests for `autoreload` behaviour when enabled / disabled.
- Dry-run and failure-mapping sweeps.

---

## Seams With Other Features

| Seam | Owner | This Feature |
|---|---|---|
| `postfix.service` lifecycle | `3-service-management` | Delegated |
| SMTP / submission ports | `3-firewall-management` | Out of scope here |
| TLS cert / key for SMTP+TLS | `3-certificate-issuance` / trust | Caller installs before setting `smtpd_tls_*` directives |
