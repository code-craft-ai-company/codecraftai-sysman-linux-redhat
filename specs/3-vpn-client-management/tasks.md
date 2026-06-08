# Implementation Plan — RHEL/Fedora VPN Client Management

## Module and Types

- [x] 1. Create the `ccai.sysman.linux.redhat.vpnclientmanagement` module.

- [x] 2. Re-export `RedHatVPNActionStatus`, `RedHatVPNActionResult`,
  `RedHatVPNClientStatus`, `RedHatVPNClientQueryError` from the shared base
  under RedHat-prefixed names.

- [x] 3. Define `RedHatVPNClientManagerConfig` extending
  `VPNClientManagerConfig` with `tailscale_channel`, `tailscale_repo_path`,
  `twingate_repo_path`, `twingate_config_path`, `package_manager` fields.

## RedHatVPNClientManager — Implementation

- [x] 4. Implement `__init__(*, runner, config=None)` calling the base.

- [x] 5. Implement `_detect_package_manager(aspects)` — `command -v dnf`
  via the runner, fall back to `yum`; cache on the instance.

- [x] 6. Implement `_rpm_installed_version(pkg, aspects)` — `rpm -q
  --queryformat '%{VERSION}'`; return `None` when not installed.

- [x] 7. Implement `_ensure_tailscale_repo(aspects)` — write
  `<tailscale_repo_path>` via heredoc through `tee`.

- [x] 8. Implement `_ensure_twingate_repo(aspects)` — parallel shape.

- [x] 9. Implement `install_tailscale(*, version, channel, aspects)` —
  idempotency guard via `_rpm_installed_version`, then repo write, then
  `<pm> install -y tailscale[-<version>]`.

- [x] 10. Implement `install_twingate_connector(*, version, aspects)`.

- [x] 11. Implement `configure(*, provider, settings, aspects)` —
  Twingate writes `/etc/twingate/connector.conf`; Tailscale returns
  `SKIPPED`.

- [x] 12. Implement `bring_up(*, provider, authkey, hostname,
  advertise_tags, advertise_routes, exit_node, aspects)`.

- [x] 13. Implement `bring_down(*, provider, aspects)`.

- [x] 14. Implement `status(*, provider, aspects)`.

- [x] 15. Implement `uninstall(*, provider, purge, aspects)` — service stop,
  `<pm> remove -y`, optional repo + state directory cleanup via runner `rm`.

## Parsers

- [x] 16. Implement `_parse_tailscale_status_json(payload)` returning
  `RedHatVPNClientStatus`. Total over all backend states.

- [x] 17. Implement `_parse_twingate_status_text(payload)` returning
  `RedHatVPNClientStatus(self_address=None, peer_addresses={})`.

## Tests — Examples

- [x] 18. `test_detect_package_manager_prefers_dnf`.
- [x] 19. `test_install_tailscale_runs_dnf_install`.
- [x] 20. `test_install_tailscale_skipped_when_present`.
- [x] 21. `test_install_tailscale_failed_on_dnf_error`.
- [x] 22. `test_install_twingate_connector_runs_dnf_install`.
- [x] 23. `test_configure_tailscale_returns_skipped`.
- [x] 24. `test_configure_twingate_writes_conf_via_tee`.
- [x] 25. `test_configure_twingate_skipped_when_content_matches`.
- [x] 26. `test_bring_up_tailscale_runs_systemctl_then_up`.
- [x] 27. `test_bring_up_tailscale_skipped_when_running_with_matching_state`.
- [x] 28. `test_bring_up_twingate_runs_systemctl_enable_now`.
- [x] 29. `test_bring_down_tailscale_runs_tailscale_down`.
- [x] 30. `test_bring_down_twingate_runs_systemctl_stop`.
- [x] 31. `test_status_tailscale_parses_running_json`.
- [x] 32. `test_status_tailscale_returns_installed_false_when_missing`.
- [x] 33. `test_status_twingate_parses_text`.
- [x] 34. `test_uninstall_tailscale_purge_removes_repo_and_state`.
- [x] 35. `test_dry_run_short_circuits_every_write_verb`.
- [x] 36. `test_writes_pass_elevated_when_configured`.
- [x] 37. `test_reads_never_elevate`.

## Tests — Property (hypothesis)

- [x] 38. `test_property_install_tailscale_idempotent`.
- [x] 39. `test_property_uninstall_idempotent`.
- [x] 40. `test_property_bring_up_idempotent_on_matching_state`.
- [x] 41. `test_property_dry_run_never_invokes_runner`.

## Documentation

- [x] 42. Sphinx-style docstrings on every public method.

- [x] 43. Module-level docstring linking to the contract and the base spec.

## Follow-ups

- [x] 44. Cross-OS contract conformance check — diff dataclass field shapes
  against the contract.

- [x] 45. SELinux note — confirm Tailscale's bundled policy is applied on
  RHEL-family hosts where SELinux is enforcing; surface a follow-up if a
  custom policy is required for the connector binary.
