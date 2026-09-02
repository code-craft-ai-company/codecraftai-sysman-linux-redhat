# Requirements

## Introduction

The python-environment-management feature provides the
`codecraftai-sysman-linux-redhat` package with the ability to deploy Python virtual
environments with a declared dependency set on RHEL-family hosts.  This spec
implements the python-environment-management contract defined at
`codecraftai-sysman-common/specs/contracts/python-environment-management/contract.md`.

Every venv / pip verb is inherited unchanged from
`ccai.sysman.common.pythonenvironmentmanagementbase.PythonEnvironmentManagementBase`
(see `codecraftai-sysman-common/specs/python-environment-management-base/`).  This
package contributes exactly two things:

1. The POSIX layout — `<venv>/bin/python`, `python3` launcher, `test -d` / `rm -rf`
   probes.
2. `ensure_prerequisites()`, which installs `python3` and `python3-pip` through the
   existing dnf-backed `PackageManager` rather than issuing `dnf` itself.

Naming note: this feature uses the `RedHat` prefix (`RedHatPythonEnvironmentManager`)
even though the RHEL package historically ships unprefixed managers, because the
types are aliases of shared cross-OS types and mixed-OS coordinators import all
four packages side by side.

---

## Requirements

### Requirement 1: Select the POSIX Layout

#### Acceptance Criteria

1. THE SYSTEM SHALL return a `PosixPythonEnvironmentLayout` from `_build_layout()`.
2. THE SYSTEM SHALL resolve the environment interpreter to `<path>/bin/python` and
   the default base interpreter to `["python3"]`.

---

### Requirement 2: Ensure dnf Prerequisites

#### Acceptance Criteria

1. WHEN the coordinator calls `ensure_prerequisites()` THE SYSTEM SHALL install, in
   order, `python3` and `python3-pip` via `PackageManager.install`, sharing this
   manager's runner.
2. THE SYSTEM SHALL construct the `PackageManagerConfig` with this manager's
   `dry_run` and `elevated` flags.
3. WHEN `config.prerequisite_packages` is set THE SYSTEM SHALL use it instead of the
   default list; WHEN the `packages` argument is supplied THE SYSTEM SHALL use it in
   preference to both (for example `["python3.12", "python3.12-pip"]` paired with
   `base_interpreter=["python3.12"]`).
4. WHEN an install returns `FAILED` THE SYSTEM SHALL stop and return the results
   collected so far.
5. WHEN dry-run is active THE SYSTEM SHALL return `DRY_RUN` per package and SHALL
   NOT dispatch `dnf install`.
6. THE SYSTEM SHALL return one `PackageActionResult` per package attempted.

---

### Requirement 3: Accept a Plain Base Config

#### Acceptance Criteria

1. WHEN constructed with a plain `PythonEnvManagerConfig` THE SYSTEM SHALL behave as
   if `prerequisite_packages=None`.

---

### Requirement 4: Registry and Aliases

#### Acceptance Criteria

1. THE SYSTEM SHALL register `python-environment-management` (layer 3, module
   `ccai.sysman.linux.redhat.pythonenvironmentmanagement`, class
   `RedHatPythonEnvironmentManager`) in `featureregistry.build_default_registry`.
2. THE SYSTEM SHALL export `RedHatPythonEnvActionStatus`,
   `RedHatPythonEnvActionResult`, `RedHatPythonEnvDeployResult`,
   `RedHatPythonEnvInfo`, `RedHatPythonEnvQueryError`, `RedHatPythonEnvSpec`,
   `RedHatPythonPackageInfo`, `RedHatPythonEnvManagerConfig`, and
   `RedHatPythonEnvironmentManager`.
