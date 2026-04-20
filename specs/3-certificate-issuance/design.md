# Design

## Overview

The certificate issuance feature is implemented as a single module,
`automojo.sysman.linux.redhat.certificateissuance`, exposing a
`CertificateIssuanceManager` class that wraps `openssl`,
`getcert` (certmonger), and `certbot` through the `ICommandRunner`
abstraction.  It writes private keys, CSRs, and certificates at
declared paths with correct mode bits.

Private-key passphrases and ACME-plugin credentials flow on runner
`stdin`, never argv.  Returned `CertificateActionResult.command`
fields are sanitised.

**See Also:**
- `3-certificate-trust-management` — installs / removes CA
  anchors in the trust store (externally-issued trust).
- `3-identity-management` — IPA-issued certificates typically go
  through certmonger's IPA CA path.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│            Maintenance Coordinator                          │
│  - Declares desired certificate posture                     │
│  - Supplies credential for key-passphrase / ACME creds      │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            CertificateIssuanceManager                       │
│                                                             │
│  Keys:   generate_key()                                     │
│  CSR:    generate_csr(), inspect_csr()                      │
│  Self:   generate_self_signed()                             │
│  Inspect:inspect_certificate(), expires_in()                │
│  certmonger: request_certmonger(),                          │
│              list_certmonger(), stop_certmonger()           │
│  ACME:   request_acme(), renew_acme()                       │
└────────────────────────┬────────────────────────────────────┘
                         │ delegates to
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                   ICommandRunner                            │
└─────────────────────────────────────────────────────────────┘
```

---

## Configuration

```python
@dataclass
class CertificateIssuanceConfig:
    dry_run: bool = False
    elevated: bool = True
    openssl_bin: str = "openssl"
    certbot_bin: str = "certbot"
    getcert_bin: str = "getcert"
    default_rsa_bits: int = 3072
    default_ec_curve: str = "P-256"
    key_mode: int = 0o600
    cert_mode: int = 0o644
    renew_after_days_default: int = 30
```

---

## Data Models

```python
class CertificateActionStatus(Enum):
    SUCCESS = "success"
    FAILED  = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class CertificateActionResult:
    status: CertificateActionStatus
    detail: str | None = None
    path: str | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    command: list[str] | None = None        # sanitised


@dataclass
class SubjectSpec:
    common_name: str
    country: str | None = None
    state: str | None = None
    locality: str | None = None
    organization: str | None = None
    organizational_unit: str | None = None
    email: str | None = None


@dataclass
class CsrSpec:
    key_path: str
    csr_path: str
    subject: SubjectSpec
    sans: list[str] = field(default_factory=list)
    key_usages: list[str] = field(default_factory=list)
    extended_key_usages: list[str] = field(default_factory=list)


@dataclass
class CsrInfo:
    path: str
    subject: str
    public_key_algorithm: str
    sans: list[str]


@dataclass
class CertificateInfo:
    path: str
    subject: str
    issuer: str
    serial_hex: str
    not_before: str
    not_after: str
    signature_algorithm: str
    public_key_algorithm: str
    public_key_size: int | None
    sans: list[str]
    key_usages: list[str]
    extended_key_usages: list[str]
    fingerprint_sha256: str


@dataclass
class CertmongerTracking:
    request_id: str
    status: str                             # MONITORING / NEED_CSR_GEN_PIN / ...
    ca: str
    cert_path: str
    key_path: str
    subject: str | None
    sans: list[str]
    expires: str | None


@dataclass
class AcmeIssuanceSpec:
    domains: list[str]
    email: str
    method: str                             # 'webroot' | 'standalone' | 'dns-<plugin>'
    webroot_path: str | None = None
    plugin: str | None = None               # e.g. 'route53', 'cloudflare'
    plugin_credentials_path: str | None = None
    server: str | None = None               # e.g. ACME staging URL
    renew_after_days: int | None = None     # SKIPPED when cert newer than this
```

---

## Exceptions

```python
class CertificateCredentialError(SemanticError):
    ...


class AcmeRequiredArgumentError(SemanticError):
    ...
```

---

## Key Method Designs

### `generate_key(path, algorithm, bits_or_curve, credential=None, overwrite=False)`

1. Check target file via runner (`test -e <path>`); if exists and
   `overwrite=False` → SKIPPED.
2. Build args:
   - RSA: `[openssl, "genpkey", "-algorithm", "RSA", "-pkeyopt",
     f"rsa_keygen_bits:{bits}", "-out", path]`
   - EC:  `[openssl, "genpkey", "-algorithm", "EC", "-pkeyopt",
     f"ec_paramgen_curve:{curve}", "-out", path]`
3. If `credential` supplied, append `-aes256 -pass stdin`;
   dispatch with `stdin=credential.secret_bytes()`.
4. Dry-run: DRY_RUN (no file written).
5. Dispatch; chmod `path` to `config.key_mode` (`0600`).

### `generate_csr(spec, credential=None, overwrite=False)`

1. Check `spec.csr_path` existence; SKIPPED when already present
   and `overwrite=False`.
2. Render OpenSSL config content from `spec.subject`, `spec.sans`,
   and key usages.
3. Write config to a temp file via `tee`.
4. Dispatch `[openssl, "req", "-new", "-key", spec.key_path,
   "-out", spec.csr_path, "-config", <tmp>]` with
   `stdin=credential.secret_bytes()` when the key is encrypted
   (append `-passin stdin`).
5. Always `rm -f` the temp config in a `finally` block
   (implemented as a try/finally around the dispatch).

### `request_certmonger(spec, cert_path, key_path, ca=None, profile=None)`

1. Read existing tracking via `list_certmonger()`.
2. If an entry exists for `cert_path` with matching `subject` +
   `sans` + `ca` → SKIPPED.
3. Dry-run: DRY_RUN.
4. Dispatch `[getcert, "request", "-f", cert_path, "-k",
   key_path, "-r", "-w", "-N", spec.subject.common_name,
   *(f"-D {san}" spread for san in spec.sans),
   *(["-c", ca] if ca else []),
   *(["-T", profile] if profile else [])]`.

### `request_acme(spec)`

1. Compute the cert path certbot will write and read its
   `not_after` via `inspect_certificate` when the file exists.
2. If exists and `renew_after_days` (or config default) > days-
   until-expiry → SKIPPED.
3. If `spec.method` is a `dns-<plugin>` style AND
   `spec.plugin_credentials_path` is set → ensure the credentials
   file exists at the declared path with mode `0600` (this
   feature does NOT create it; caller writes it).
4. Dispatch certbot per the `method`:
   - `webroot` → `["certbot", "certonly", "--webroot",
     "--webroot-path", spec.webroot_path, ...]`
   - `standalone` → `["certbot", "certonly", "--standalone", ...]`
   - `dns-<plugin>` → `["certbot", "certonly", f"--{spec.plugin}",
     f"--{spec.plugin}-credentials",
     spec.plugin_credentials_path, ...]`
5. Common trailing args: `["--agree-tos", "-m", spec.email,
   "--non-interactive", *(["--server", spec.server] if spec.server
   else []), *(spec_domain_args_for(spec.domains))]`.

### `inspect_certificate(path)`

1. Probe existence; return `None` if absent.
2. Dispatch `[openssl, "x509", "-in", path, "-noout", "-text"]`
   + `[openssl, "x509", "-in", path, "-noout",
   "-fingerprint", "-sha256"]`.
3. Parse the text output into `CertificateInfo`.

### `expires_in(path)`

1. `info = inspect_certificate(path)`; if `None` → raise
   `FileNotFoundError`.
2. Parse `info.not_after` as a UTC datetime; return `delta =
   not_after - datetime.now(timezone.utc)`.

---

## Dry-Run Behaviour

Dry-run disables every mutating path (key generation, CSR
generation, self-sign, certmonger request/stop, ACME
request/renew).  Inspection queries (`inspect_csr`,
`inspect_certificate`, `list_certmonger`, `expires_in`) run
freely, though `expires_in` may still fail with
`FileNotFoundError` if the cert is absent.

---

## Correctness Properties

1. **No passphrase on argv.** Every code path that accepts a
   key passphrase routes the bytes on `stdin`; the recorded
   `command` field is sanitised.
2. **Mode-bit invariants.** New keys are chmod'd to `0600`,
   certs to `0644`, post-write.
3. **Idempotent certmonger tracking.** `request_certmonger`
   SKIPPED when an equivalent tracking already exists.
4. **Temp-config cleanup.** `generate_csr` and
   `generate_self_signed` always delete their temp OpenSSL config
   file, even on failure.
5. **Renewal threshold.** `request_acme` SKIPPED when an existing
   cert still has more than `renew_after_days` of validity.

---

## Testing Strategy

- `_MockRunner` + `_MockCredential`.
- Parser tests for `openssl req -noout -text`, `openssl x509
  -noout -text`, `openssl x509 -noout -fingerprint -sha256`,
  `getcert list`.
- Tests for key / CSR / self-sign paths — including
  `overwrite=False` SKIPPED branch.
- `assert-no-passphrase-on-argv` sweep across `generate_key`,
  `generate_csr`, `generate_self_signed`.
- Tests that `generate_csr` always cleans its temp config, even
  on dispatch failure.
- Tests for certmonger idempotency.
- Tests for ACME renewal-threshold SKIPPED path.
- Dry-run and failure-mapping sweeps.

---

## Seams With Other Features

| Seam | Owner | This Feature |
|---|---|---|
| CA trust-store anchors | `3-certificate-trust-management` | Separate — trust vs. issuance |
| IPA-issued certs via certmonger | This feature (via `request_certmonger(ca="IPA")`) | `3-identity-management` performs the IPA join first |
| TLS material for Postfix / Samba / NFS | Respective feature specs | This feature produces the key/cert pair; callers reference the paths |
| Credential / passphrase handling | `automojo-credentials` | Credential objects accepted; passphrases on stdin only |
