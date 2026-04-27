======================================
Automation Mojo Sysman Linux Red Hat
======================================

``automojo-sysman-linux-redhat`` is a Python library for automating the maintenance of software configurations on Red Hat Enterprise Linux systems. It provides a catalogue of domain primitives — covering packages, repositories, services, users, filesystems, storage, networks, firewalls, kernel parameters, logs, scheduled tasks, security compliance, containers, boot configuration, Cockpit, Insights, subscriptions, and more — that an external maintenance coordinator invokes to apply a declared system posture.

The package does not decide *what* maintenance to perform; it provides the capability to *do it* reliably, idempotently, and with full auditability. All system mutations flow through the ``automojo-interfaces`` command-runner and file-operator abstractions, and through the local-/SSH-runner architecture defined under ``specs/1-command-runner-architecture``. Every feature supports dry-run simulation, command auditing, and test mocking uniformly.

Highlights
==========

- Domain primitives spanning packages, repositories, services, users, filesystems, storage, networking, and firewalls.
- Extended coverage for kernel parameters, logs, scheduled tasks, security compliance, containers, boot configuration, Cockpit, Insights, and subscriptions.
- Mutations routed through ``ICommandRunner`` and ``IFileOperator`` with uniform dry-run, auditing, and mocking.
- Local- and SSH-runner architecture for reaching hosts directly or over SSH.
- Layered architecture model documented in ``userguide/architecture-layers.md``.

Repository Structure
====================

- ``development/`` - Runtime environment scripts.
- ``repository-setup/`` - Repository homing and setup scripts.
- ``source/packages/`` - Package source code.
- ``source/testroots/testbase/`` - TestBase test roots.
- ``specs/`` - Feature specifications, each prefixed with its architecture-layer number.
- ``userguide/`` - Standards, conventions, and architecture documentation.

Environment Setup
=================

When working in a Codex container:

.. code-block:: bash

   ./development/setup-agent-container

For local development:

.. code-block:: bash

   ./repository-setup/rehome-repository
   ./development/setup-envrionment

Running Tests
=============

Activate the virtual environment:

.. code-block:: bash

   source .venv/bin/activate

Run TestBase tests (when test roots are populated):

.. code-block:: bash

   testbase testing run --root source/testroots/testbase --includes=automojo --output=./.output

Standards and Conventions
=========================

- Naming conventions: ``userguide/10-02-naming-conventions.rst``
- Code organization: ``userguide/10-01-code-organization.rst``
- Coding standards: ``userguide/10-00-coding-standards.rst``

Additional Resources
====================

- User guide entry point: ``userguide/userguide.rst``
- Architecture layers: ``userguide/architecture-layers.md`` (brief version: ``userguide/architecture-layers-brief.md``)
- Command runner architecture: ``specs/1-command-runner-architecture/``
