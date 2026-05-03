# Changelog

All notable changes to `validance-sdk` are recorded here.

This changelog is **part of Validance's contract-versioning story**, not a list of features. Each entry answers four questions:

1. **What changed in the SDK contract?** — the `Task` / `Workflow` shape, public exports, behavioural invariants.
2. **Was the change additive or breaking?** — the answer drives the semver bump.
3. **Which engine versions can safely accept this SDK?** — compatibility statement.
4. **Do existing workflow definitions need updating?** — workflow-author impact.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), extended with Validance-specific sections (**Contract surface**, **Behavioural guarantees**, **Engine compatibility**, **Migration notes**).

Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The PyPI release version is the same string as `validance.__contract_version__` — see `README.md` § *Contract Versioning*.

**Pre-1.0 semantics apply.** While at `0.x`, *any minor bump may introduce breaking changes*. The `1.0.0` release will mark the first stability commitment — at that point, breaking changes require a major bump and engine-side revalidation.

---

## [Unreleased]

(Nothing pending.)

---

## [0.1.0] — 2026-05-03

First publish to PyPI. This release establishes the public contract baseline.

### Contract surface

Public exports from `validance` (use as `from validance import …`):

- `Task` — frozen dataclass declaring a unit of containerised work. Fields: `name`, `command`, `docker_image`, `inputs`, `output_files`, `output_vars`, `depends_on`, `environment`, `volumes`, `parallel`, `timeout`, `token_budget`, `cost_budget`.
- `Workflow` — DAG container. Methods: `add_task()`, `validate()`, `to_dict()`, `to_json()`. Property: `definition_hash` (SHA-256 of canonical JSON).
- `deep_freeze(obj)` — recursive immutability helper for context structures (returns `MappingProxyType` / `tuple` / primitives).
- `__contract_version__` — string, currently `"0.1.0"`.

No other names are part of the contract. Anything reachable via `validance.task` or `validance.workflow` submodule paths is an implementation detail and may change without a contract bump.

### Behavioural guarantees

- `Task` is immutable after construction (`frozen=True` dataclass). Attempting to mutate raises `dataclasses.FrozenInstanceError`.
- `Workflow.validate()` detects (a) references to undeclared task names in `depends_on`, and (b) DAG cycles (Kahn's algorithm). Returns a list of error strings; empty list = valid.
- `Workflow.to_json()` produces canonical JSON: keys sorted, deterministic separators. Same workflow definition → same JSON bytes → same `definition_hash`.
- `Workflow.tasks` returns a defensive copy; external mutation of the returned dict does not affect the workflow.
- `output_vars` declared types are enforced by the engine at task completion (the SDK declares; the engine validates and coerces).
- `parallel=False` is the default for tasks at the same dependency level (sequential execution, safe by default).

### Engine compatibility

- Compatible with Validance engine builds that accept SDK contract `0.x`.
- Engine-side load-time enforcement of `__contract_version__` is not yet implemented; contract compatibility between SDK and engine is currently by convention rather than an automated check. Pin both ends explicitly when version mismatches matter.

### Migration notes

- N/A (first publish).

---

[Unreleased]: https://github.com/validance-io/sdk-python/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/validance-io/sdk-python/releases/tag/v0.1.0
