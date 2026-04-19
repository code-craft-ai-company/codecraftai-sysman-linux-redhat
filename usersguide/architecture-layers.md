# Architecture Layers

This document formalises the six-layer architecture model used to organise the
software stack.  Each layer has a single coherent purpose, a set of
responsibilities, and explicit exclusions so boundaries between layers remain
sharp.  Higher-numbered layers may depend on lower-numbered layers; Layer 5
(Cross-Cutting Concerns) is the exception and applies across every layer but
is scoped to cross-cuts that reach into presentation rather than platform
mechanisms.

---

## Layer 1 — Infrastructure and Platform Layer

**Purpose.** Provides the foundational substrate on which all higher layers
run, the abstractions that isolate the software from the host environment,
and the platform-level mechanisms that higher layers consume as services.

**Responsibilities.**

- Host OS, kernel interfaces, shell, and external command-line tools
- Deployment platform (container runtime, VM, bare metal, OS distribution)
- Filesystem access, network connectivity, and process model
- External systems the software depends on
- Adapter/port abstractions that wrap platform primitives (command runners,
  file operators, system-call wrappers)
- Logging and tracing infrastructure (log frameworks, sinks, transports)
- Error-handling infrastructure (exception classification, propagation,
  standard error types)
- Security infrastructure (authentication libraries, secret stores, crypto
  primitives, credential providers)
- Configuration management (loading, parsing, and resolving configuration
  from files, environment, and remote sources)
- Observability infrastructure (metrics emission, tracing exporters, health
  probes)
- Database connections, connection pooling, and network-client primitives
- Retry, timeout, and circuit-breaker primitives exposed as reusable
  mechanisms
- Execution policies that are platform-level rather than domain-level
  (dry-run, audit hooks, elevation)

**Does not contain.** Domain rules, data models, public API shapes, or
user-facing output formats.

---

## Layer 2 — Data Layer

**Purpose.** Defines the shape, representation, and persistence of data used
throughout the system.

**Responsibilities.**

- Data models, entities, value objects, DTOs
- Serialization and deserialization (JSON, YAML, INI, binary, on-disk
  formats)
- Schema definitions, validation rules tied to structural correctness
- Persistence and retrieval mechanisms built on Layer 1 primitives
- Query primitives and data-access abstractions
- Migration and versioning of data representations

**Does not contain.** Business rules about when or why data changes;
orchestration across multiple data operations; presentation formatting;
connection-pool or transport concerns (those belong in Layer 1).

---

## Layer 3 — Business Logic Layer

**Purpose.** Encodes the domain-specific rules, invariants, and operations
that define what the software actually does, independent of how it is
invoked or delivered.

**Responsibilities.**

- Domain operations and workflows (the verbs of the problem domain)
- Domain invariants and semantic validation (e.g., "a service cannot be
  enabled if masked")
- Idempotency logic, state reconciliation, and conditional action
- Composition of data-layer operations into meaningful domain actions
- Domain-specific error types and error semantics

**Does not contain.** Platform-specific command dispatch, public API
packaging, platform mechanisms (logging, config resolution, auditing
infrastructure), or presentation logic.

---

## Layer 4 — Application Service Layer

**Purpose.** Exposes the public, caller-facing surface of the system by
orchestrating business-logic components into coarse-grained, use-case-oriented
operations.

**Responsibilities.**

- Public API / service façade; the stable contract callers depend on
- Use-case orchestration across multiple business-logic components
- Transaction, session, and unit-of-work boundaries
- Request/response shaping between external callers and the business layer
- Authorization enforcement and input normalization at the service boundary

**Does not contain.** Domain rules themselves, raw platform calls, or
presentation rendering.

---

## Layer 5 — Cross-Cutting Concerns

**Purpose.** Concerns that cut across multiple layers and ultimately shape
user-facing behaviour or output.  Unlike platform mechanisms (which live in
Layer 1), these cross-cuts exist because the *presentation* of the software
varies by user, locale, policy, or experiment.

**Responsibilities.**

- Internationalization (i18n) frameworks and message catalogues
- Localization (l10n): locale-specific formatting of numbers, dates, times,
  currencies, and units
- Timezone handling for user-visible times
- Accessibility conventions (labels, roles, assistive-technology hints)
- Theming, styling, and visual-design tokens
- Pluralization, collation, and text-directionality rules
- Content and messaging conventions (tone, terminology, copy style)
- User-facing feature flags that gate UX variants
- A/B testing and experimentation frameworks for user-visible variants
- Audit and reporting conventions where they shape human-readable output

**Does not contain.** Platform mechanisms such as log frameworks, metrics
emission, security infrastructure, or configuration resolution — those live
in Layer 1.  Domain rules, data schemas, or service-API shapes.

---

## Layer 6 — Presentation Layer

**Purpose.** Translates between external callers (human or machine) and the
application service layer, handling delivery-specific representation.

**Responsibilities.**

- Command-line interface, REST/GraphQL/RPC endpoints, web UI, terminal UI
- Input parsing and validation for the chosen delivery channel
- Output rendering and formatting (text, JSON, tables, HTML, human-readable
  reports)
- Session, routing, and protocol-level concerns specific to the delivery
  channel
- Adapting presentation-specific errors from application-service errors
- Consumption of Layer 5 cross-cuts (locale, theme, accessibility) when
  rendering

**Does not contain.** Business rules, data persistence, or platform
adapters — it is a thin translation layer over the application service.
