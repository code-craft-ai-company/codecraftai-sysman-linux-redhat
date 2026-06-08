# Design

## Overview

The remote log forwarding feature is implemented as a single
module,
`ccai.sysman.linux.redhat.remotelogforwarding`, exposing a
`RemoteLogForwardingManager` class that manages rsyslog forward
drop-ins under `/etc/rsyslog.d/`, the
`/etc/systemd/journal-upload.conf` file, and journald-forward
directives through the `ICommandRunner` abstraction.

Follows the standard manager pattern; delegates service lifecycle
to an injected `ServiceManager`.

**See Also:**
- `3-log-management` — local journal + logrotate surface.
- `3-certificate-issuance` / `3-certificate-trust-management` —
  TLS material.
- `3-service-management` — `rsyslog.service` and
  `systemd-journal-upload.service` lifecycle; journald restart.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│            Maintenance Coordinator                          │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            RemoteLogForwardingManager                       │
│                                                             │
│  rsyslog:                                                   │
│    add_forward_rule(), remove_forward_rule(),               │
│    list_forward_rules(), validate_rsyslog()                 │
│  journal-upload:                                            │
│    set_upload_config(), get_upload_config(),                │
│    enable_upload(), disable_upload()                        │
│  journald forwarding:                                       │
│    set_journald_directive(), get_journald_directives()      │
│  Probe:                                                     │
│    probe_collector()                                        │
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
class RemoteLogForwardingConfig:
    dry_run: bool = False
    elevated: bool = True
    rsyslog_dropin_dir: str = "/etc/rsyslog.d"
    journal_upload_conf: str = "/etc/systemd/journal-upload.conf"
    journald_conf: str = "/etc/systemd/journald.conf"
    journald_conf_dropin_dir: str = "/etc/systemd/journald.conf.d"
    default_queue_spool: str = "/var/spool/rsyslog"
```

---

## Data Models

```python
class LogForwardingActionStatus(Enum):
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class LogForwardingActionResult:
    status: LogForwardingActionStatus
    detail: str | None = None
    rule_name: str | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    command: list[str] | None = None


@dataclass
class RsyslogForwardRule:
    name: str                                   # matches the drop-in basename
    target_host: str
    target_port: int = 514
    protocol: str = "tcp"                       # 'tcp' | 'udp'
    tls: bool = False
    tls_ca_file: str | None = None
    tls_cert_file: str | None = None
    tls_key_file: str | None = None
    tls_auth_mode: str = "x509/name"
    tls_permitted_peer: str | None = None
    queue_filename: str | None = None
    queue_spool_dir: str | None = None
    queue_max_disk_bytes: int | None = None
    queue_save_on_shutdown: bool = True
    template: str | None = None                 # optional named template for msg format
    raw_extra: str | None = None                # escape hatch for rarely-used options


@dataclass
class LogForwardingHealth:
    host: str
    port: int
    reachable: bool
    tls_verified: bool | None                   # None for non-TLS probe
    raw: str
```

---

## Key Method Designs

### `add_forward_rule(rule)`

1. Render canonical rsyslog `action(...)` construct from `rule`.
2. Read current drop-in at
   `{rsyslog_dropin_dir}/{rule.name}.conf` via `cat`.
3. If content matches → SKIPPED.
4. Dry-run: DRY_RUN.
5. If `rule.queue_spool_dir` is set AND does not exist → `install
   -d -o syslog -g syslog -m 0700 <path>` through the runner.
6. Write the drop-in via `tee`.
7. Dispatch `rsyslogd -N1` to validate.  On non-zero exit → return
   `FAILED` without requesting a service reload.
8. Delegate `service_manager.reload("rsyslog")`.

### `set_upload_config(directives)`

1. Read `/etc/systemd/journal-upload.conf`.
2. Parse the `[Upload]` section.
3. Upsert each directive in `directives`.
4. If every key already matches → SKIPPED.
5. Dry-run: DRY_RUN.
6. Write merged content via `tee`.
7. Return `LogForwardingActionResult(status=SUCCESS)`; the caller
   decides whether to enable / restart the service.

### `set_journald_directive(key, value)`

1. Upsert in `[Journal]` section of the journald.conf (or a
   drop-in).
2. SKIPPED when unchanged.
3. Populate `result.detail` with a notice: "journald.service
   restart required — route through 3-service-management".

### `probe_collector(host, port, tls=False)`

1. If `tls`:
   - Dispatch `["openssl", "s_client", "-connect",
     f"{host}:{port}", "-verify", "1", "-brief"]` with a short
     timeout.
   - `reachable=True` when exit is 0; `tls_verified=True` when
     output contains `"Verification: OK"`.
2. Else:
   - Dispatch `["timeout", "5", "bash", "-c", f"cat <
     /dev/tcp/{host}/{port}"]`.
   - `reachable` from exit code 0 (or 124 → not reachable, not
     failure).
3. Always return a `LogForwardingHealth`; never raise on probe
   failure.

---

## Dry-Run Behaviour

Dry-run disables every mutating entry point.  Read-only queries
and probes run freely.

---

## Correctness Properties

1. **Validate-before-reload.** rsyslog mutations always pass
   through `rsyslogd -N1` before reloading.
2. **No auto-restart of journald.** journald directives flag the
   caller; this feature never triggers a journald restart that
   would drop in-memory records.
3. **Idempotent drop-ins.** rsyslog rules, journal-upload
   directives, and journald directives all SKIP when the live
   content already matches.
4. **Probe never raises.** `probe_collector` wraps every failure
   into the result record.

---

## Testing Strategy

- `_MockRunner` + `_MockServiceManager`.
- Tests for `add_forward_rule` including the TLS / queue / validate /
  reload chain.
- Tests for spool-directory `install -d` being issued when the
  directory does not exist.
- Tests that validation failure aborts the reload delegation.
- Tests for `set_upload_config` idempotency and preservation of
  unrelated sections.
- Tests for `set_journald_directive` result-detail hint content.
- Tests that `probe_collector` returns rather than raising on
  probe failure.
- Dry-run and failure-mapping sweeps.

---

## Seams With Other Features

| Seam | Owner | This Feature |
|---|---|---|
| Local journal + logrotate | `3-log-management` | Out of scope here |
| TLS cert / key material | `3-certificate-issuance` / trust-mgmt | Caller installs; rule references paths |
| rsyslog / journal-upload service lifecycle | `3-service-management` | Reload / enable / disable delegated |
| journald restart for `ForwardTo*` changes | `3-service-management` | Out of scope here; result detail notifies caller |
