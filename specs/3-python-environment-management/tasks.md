# Implementation Plan — RHEL Python Environment Management

Checked items (`[x]`) are implemented and covered by tests.

## Module

- [x] 1. Create `ccai.sysman.linux.redhat.pythonenvironmentmanagement`
- [x] 2. Define `RedHat`-prefixed aliases of the shared types
- [x] 3. Define `RedHatPythonEnvManagerConfig` (prerequisite_packages)
- [x] 4. Implement `_build_layout` → POSIX layout
- [x] 5. Implement `ensure_prerequisites` delegating to `PackageManager.install`
- [x] 6. Register the feature in `featureregistry._DEFAULT_REGISTRY_SEED`

## Tests

- [x] 7. `test_layout_is_posix`
- [x] 8. `test_ensure_prerequisites_installs_default_dnf_packages`
- [x] 9. `test_ensure_prerequisites_honours_override_and_config`
- [x] 10. `test_ensure_prerequisites_stops_at_first_failure`
- [x] 11. `test_ensure_prerequisites_dry_run_dispatches_nothing`
- [x] 12. `test_plain_base_config_is_accepted`
- [x] 13. `test_deploy_end_to_end_on_rhel_argv`
- [x] 14. Registry count tests updated (47 entries, 44 layer-3)

## Follow-ups

- [ ] 15. Auto-wired `MaintenanceReport` hook: the shared base does not yet accept
  the RHEL `report=` constructor argument; wire it when the common
  `BaseFeatureManager` gains the reporting hook
- [ ] 16. Live-host verification on RHEL 9 / Rocky 9 (stock and `python3.12`
  stream)
