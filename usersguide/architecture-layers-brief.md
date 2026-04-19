# Architecture Layers (Brief)

**Layer 1: Infrastructure and Platform Layer** - Host OS and kernel interfaces, shell and external command-line tools, deployment platform, filesystem access, network connectivity, process model, platform adapters, command runners, file operators, logging systems, error handling, security infrastructure, configuration management, observability and metrics, database connections, retry/timeout/circuit-breaker primitives, execution policies (dry-run, elevation, audit hooks)

**Layer 2: Data Layer** - Data models, entities, value objects, DTOs, serialization and deserialization (JSON, YAML, INI, binary, on-disk formats), schema definitions, structural validation, persistence and retrieval, query primitives, data-access abstractions, migration and versioning

**Layer 3: Business Logic Layer** - Domain operations and workflows, domain invariants and semantic validation, idempotency logic, state reconciliation, conditional action, composition of data-layer operations into domain actions, domain-specific error types

**Layer 4: Application Service Layer** - Public API and service façade, use-case orchestration across business-logic components, transaction and session boundaries, request/response shaping, authorization enforcement, input normalization at the service boundary

**Layer 5: Cross-Cutting Concerns** - Internationalization (i18n), localization (l10n), locale-specific formatting of numbers/dates/times/currencies/units, timezone handling, accessibility, theming and styling, pluralization and text directionality, content and messaging conventions, user-facing feature flags, A/B testing and experimentation, human-readable reporting conventions

**Layer 6: Presentation Layer** - Command-line interface, REST/GraphQL/RPC endpoints, web UI, terminal UI, input parsing, output rendering and formatting (text, JSON, tables, HTML, human-readable reports), session, routing, protocol-level concerns, presentation-specific error adapting, consumption of locale/theme/accessibility cross-cuts
