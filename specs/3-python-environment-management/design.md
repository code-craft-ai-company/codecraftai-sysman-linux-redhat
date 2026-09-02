# Design

## Overview

`ccai.sysman.linux.redhat.pythonenvironmentmanagement` is a thin subclass of the
shared `PythonEnvironmentManagementBase`.  Every venv and pip verb, the deploy
algorithm, the requirement probe, and the read surface are documented in
`codecraftai-sysman-common/specs/python-environment-management-base/design.md`.
This document covers only what RHEL adds.

Unlike the other layer-3 RHEL features, this manager extends the *common*
`BaseFeatureManager` (via the shared python-environment base) rather than the
package-local `ccai.sysman.linux.redhat.featurebase`, exactly as
`RedHatVPNClientManager` already does.  The RHEL `ICommandRunner` protocol is
structurally compatible.

---

## Architecture

```
RedHatPythonEnvironmentManager
├── _build_layout()           → PosixPythonEnvironmentLayout
├── ensure_prerequisites()    → PackageManager.install(name) × N   (dnf)
│                                (same ICommandRunner, same dry_run / elevated)
└── (inherited)               → create_environment, remove_environment,
                                install_packages, install_requirements,
                                uninstall_packages, upgrade_pip, deploy, reads
```

---

## Components

### `RedHatPythonEnvManagerConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| *(contract fields)* | — | — | inherited from `PythonEnvManagerConfig` |
| `prerequisite_packages` | `list[str] \| None` | `None` → `["python3", "python3-pip"]` | dnf packages for `ensure_prerequisites` |

### `RedHatPythonEnvironmentManager`

| Method | Description |
|---|---|
| `_build_layout()` | Returns `PosixPythonEnvironmentLayout()` |
| `ensure_prerequisites(packages=None)` | Installs each prerequisite via `PackageManager.install`; stops at first `FAILED` |
| `_resolve_prerequisite_packages(packages)` | argument > config > default |
| `_build_package_manager()` | Builds `PackageManager(runner, PackageManagerConfig(dry_run, elevated))` |
| `_typed_config()` | Upgrades a plain `PythonEnvManagerConfig` |

---

## Command Mappings

| Step | Command (through the runner) | Elevated |
|---|---|---|
| Prerequisite probe | `rpm -q --queryformat … <pkg>` (by `PackageManager`) | never |
| Prerequisite install | `dnf install -y <pkg>` | `config.elevated` |
| Create venv | `python3 -m venv [--system-site-packages] <path>` | `config.elevated` |
| Existence probe | `test -d <path>` | never |
| Validity probe | `<path>/bin/python -c "import platform; print(platform.python_version())"` | never |
| pip (any) | `<path>/bin/python -m pip --disable-pip-version-check --no-input …` | writes only |
| Remove | `rm -rf <path>` | `config.elevated` |

Interpreter streams: RHEL 9 ships `python3` (3.9) plus optional `python3.11` /
`python3.12` application streams.  To build environments on a newer stream set
`prerequisite_packages=["python3.12", "python3.12-pip"]` and
`base_interpreter=["python3.12"]` together.

---

## Correctness Properties

1. **Layout.** `manager.layout` is a `PosixPythonEnvironmentLayout`.
2. **Prerequisite defaults.** `dnf install -y` for `python3` then `python3-pip`,
   elevated per config.
3. **Prerequisite precedence.** Argument > config > default.
4. **Prerequisite short-circuit.** The first `FAILED` install is the last attempted.
5. **Prerequisite dry-run.** Every result is `DRY_RUN`; no `dnf install` dispatched.
6. **Plain config.** Accepted; RHEL-only field defaults.
7. **Deploy argv.** A fresh `deploy` issues `python3 -m venv`, pip upgrade, and
   `pip install -r …` in order.
8. **Registry.** `build_default_registry().resolve("python-environment-management")`
   is `RedHatPythonEnvironmentManager`.

---

## Module Layout

```
source/packages/ccai/sysman/linux/redhat/
├── featureregistry.py                 ← + python-environment-management descriptor
└── pythonenvironmentmanagement.py

source/testroots/testbase/ccai/tests/sysman/linux/redhat/
├── test_featureregistry.py            ← counts 46→47, layer-3 43→44
└── test_pythonenvironmentmanagement.py
```

---

## Testing Strategy

The shared base suite covers the verbs.  The RHEL suite asserts layout selection,
the dnf delegation (argv, elevation, precedence, short-circuit, dry-run), plain
config acceptance, and one end-to-end deploy argv sequence, using a `_MockRunner`
that returns the RHEL `RunResult` (which requires `command`).
