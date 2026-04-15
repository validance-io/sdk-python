# Validance SDK

Contract interface for **Validance** — a validated workflow orchestration platform with a tamper-evident audit trail.

The SDK defines two primitives: **Task** (a unit of work) and **Workflow** (a DAG of tasks). You declare **what** your workflow does (tasks, dependencies, inputs, outputs). The Validance engine handles **how** it runs (containers, scheduling, file transport, audit chain). The SDK has **zero dependencies** — only Python standard library.

Multi-workflow orchestration (sequencing, branching, process state) is handled by the caller via the engine HTTP API. See [orchestration patterns](docs/orchestration-patterns.md).

```python
from validance.sdk import Task, Workflow
```

---

## Installation

```bash
pip install validance-sdk
```

Requires Python 3.9+. No dependencies.

---

## Quick Start

```python
from validance.sdk import Task, Workflow

# Define tasks
extract = Task(
    name="extract",
    command="python extract.py --output raw.csv",
    output_files={"raw_data": "raw.csv"},
)

transform = Task(
    name="transform",
    command="python transform.py raw.csv cleaned.csv",
    inputs={"raw.csv": "@extract:raw_data"},
    output_files={"cleaned": "cleaned.csv"},
    output_vars={"row_count": "int", "passed": "bool"},
    depends_on=["extract"],
)

# Build workflow
wf = Workflow("data.pipeline")
wf.add_task(extract)
wf.add_task(transform)

# Validate locally (checks for missing/circular dependencies)
errors = wf.validate()
assert not errors, errors

# Serialize to JSON for the engine API
print(wf.to_json(indent=2))
```

Register and trigger via the engine's REST API:

```bash
# Register
curl -X POST http://engine:8001/api/workflows \
  -H "Content-Type: application/json" \
  -d @workflow.json

# Trigger
curl -X POST http://engine:8001/api/workflows/data.pipeline/trigger
```

---

## Tasks

A `Task` is the smallest unit of work — a shell command that runs inside an isolated Docker container.

```python
from validance.sdk import Task

analysis = Task(
    name="run_analysis",
    command="python analyze.py --input data.csv --output results.json",
    docker_image="my-registry.azurecr.io/analysis:v2.1",
    inputs={"data.csv": "@prepare:cleaned_data"},
    output_files={"results": "results.json"},
    output_vars={"row_count": "int", "status": "str"},
    depends_on=["prepare"],
    environment={"MODEL_VERSION": "2.1"},
    timeout=7200,
)
```

Tasks are **immutable** (`frozen=True` dataclass). All fields are set at construction time.

### Task Fields


| Field          | Type              | Default    | Description                                                                                  |
| -------------- | ----------------- | ---------- | -------------------------------------------------------------------------------------------- |
| `name`         | `str`             | *required* | Unique identifier within the workflow                                                        |
| `command`      | `str`             | `""`       | Shell command to execute in the container                                                    |
| `docker_image` | `str | None`      | `None`     | Docker image (`None` = engine default)                                                       |
| `inputs`       | `dict[str, str]`  | `{}`       | Input files mapped into the container                                                        |
| `output_files` | `dict[str, str]`  | `{}`       | Files to capture: `{variable_name: filename}`                                                |
| `output_vars`  | `dict[str, str]`  | `{}`       | Typed scalar outputs: `{variable_name: type}`                                                |
| `depends_on`   | `list[str]`       | `[]`       | Task names that must complete first                                                          |
| `environment`  | `dict[str, str]`  | `{}`       | Extra environment variables for the container                                                |
| `volumes`      | `dict[str, dict]` | `{}`       | Additional volume mounts                                                                     |
| `parallel`     | `bool`            | `False`    | Opt in to concurrent execution with other `parallel=True` tasks at the same dependency level |
| `timeout`      | `int`             | `3600`     | Maximum execution time in seconds                                                            |


### Passing Data Between Tasks

Tasks exchange data in two ways: **files** (via `inputs`) and **values** (via environment variables).

#### File inputs

The `inputs` dict maps a filename inside the container to a source. The engine resolves the source, downloads the file, and places it at the specified path in the working directory.

| Input syntax | Meaning | Example |
|--------------|---------|---------|
| `@task_name:file_ref` | Output file from a previous task (`file_ref` is an `output_files` key) | `@prepare:cleaned_data` |
| `${parameter_name}` | Runtime parameter (must resolve to a URI at trigger time) | `${source}` |
| `azure://container/path` | Direct storage URI | `azure://data/raw.csv` |

The `@` syntax references the **key** in the producer's `output_files`, not the filename:

```python
prepare = Task(
    name="prepare",
    command="python clean.py raw.csv cleaned.csv",
    inputs={"raw.csv": "${source}"},                   # parameter provided at trigger time
    output_files={"cleaned_data": "cleaned.csv"},
    #              ^^^^^^^^^^^^   ← this is the file_ref
)

analyze = Task(
    name="analyze",
    command="python analyze.py data.csv report.json",
    inputs={"data.csv": "@prepare:cleaned_data"},      # references output_files key above
    output_files={"report": "report.json"},
    depends_on=["prepare"],
)
```

Trigger with a file parameter:

```bash
curl -X POST http://engine:8001/api/workflows/data.pipeline/trigger \
  -H "Content-Type: application/json" \
  -d '{"parameters": {"source": "azure://data/samples/experiment_42.csv"}}'
```

The `${source}` parameter resolves to the URI, and the engine downloads the file into the container as `raw.csv`.

#### Value inputs (environment variables)

All trigger parameters and task output variables are injected into every downstream container as `CTX_*` environment variables. The naming convention is `CTX_{TASK_NAME}_{VARIABLE_NAME}` for task outputs and `CTX_{PARAMETER_NAME}` for trigger parameters:

```python
# Trigger with: {"parameters": {"model_version": "2.1", "threshold": "0.8"}}
# → Inside every container:
#   CTX_MODEL_VERSION=2.1
#   CTX_THRESHOLD=0.8

# Task "count" produces output_vars: {"row_count": "int"}
# → Every subsequent container sees:
#   CTX_COUNT_ROW_COUNT=1024
```

**Important:** The context is broadcast — every task sees **all** accumulated values from trigger parameters and all previously completed tasks, not just its declared dependencies. Tasks do not need to declare which values they consume; they read `CTX_*` environment variables directly.

```python
import os

# Read a trigger parameter
model = os.environ["CTX_MODEL_VERSION"]

# Read an upstream task's output variable
row_count = int(os.environ["CTX_COUNT_ROW_COUNT"])
```

Use `environment` for static values; use `CTX_*` env vars for dynamic values from the workflow context.

### Output Variables

Tasks can produce typed scalar values by writing `_validance_vars.json`:

```python
Task(
    name="count_rows",
    command="python count.py",
    output_vars={
        "total_rows": "int",
        "accuracy": "float",
        "passed": "bool",
        "model_name": "str",
        "metadata": "json",
    },
)
```

Inside your task script:

```python
import json

with open("_validance_vars.json", "w") as f:
    json.dump({"total_rows": 1024, "accuracy": 0.95, "passed": True}, f)
```

The engine validates and type-coerces values against the declared types.

**Allowed types:** `"str"`, `"int"`, `"float"`, `"bool"`, `"json"`

### Serialization

```python
task.to_dict()   # JSON-safe dict (omits fields matching defaults)
```

---

## Workflows

A `Workflow` is a directed acyclic graph (DAG) of tasks.

```python
from validance.sdk import Task, Workflow

wf = Workflow("data.pipeline")
wf.add_task(extract)
wf.add_task(transform)
wf.add_task(load)
```

`add_task()` returns the workflow for chaining:

```python
wf = Workflow("data.pipeline")
wf.add_task(extract).add_task(transform).add_task(load)
```

### Dependencies and Parallelism

The engine groups tasks into dependency levels via topological sort. Tasks at the same level **can** run concurrently, but only if they opt in with `parallel=True`. Without the flag, same-level tasks run sequentially (safe default — not all tasks are safe to run concurrently due to shared resources or memory limits).

```python
# Both at the same dependency level AND opted in — engine runs them concurrently
wf.add_task(Task(name="validate_schema", ..., depends_on=["extract"], parallel=True))
wf.add_task(Task(name="validate_quality", ..., depends_on=["extract"], parallel=True))

# This waits for both to complete before starting
wf.add_task(Task(name="load", ..., depends_on=["validate_schema", "validate_quality"]))
```

If `parallel=False` (default), tasks at the same dependency level run one at a time, even though the DAG would allow concurrency.

### Validation

Check for structural errors before registering:

```python
errors = wf.validate()
if errors:
    for e in errors:
        print(f"Error: {e}")
```

Checks:

- **Missing dependencies** — `depends_on` references a task not in the workflow
- **Circular dependencies** — the DAG contains a cycle (detected via Kahn's algorithm)

### Serialization and Hashing

```python
wf.to_dict()                # JSON-safe dict
wf.to_json(indent=2)        # Canonical JSON string (sorted keys, deterministic)
wf.definition_hash           # SHA-256 of canonical JSON — changes only when the definition changes
```

### Naming Convention

Use dot-separated names: `data.pipeline`, `rag.ingest`, `analysis.monthly`.

### Multi-Workflow Processes

To run multiple workflows as a process, use the engine API:

```bash
# Initialize a process
POST /api/processes/full.pipeline/init

# Execute workflows sequentially via the caller
POST /api/processes/{hash}/execute-unit?unit_id=step_0&unit_type=workflow&target=ingest
POST /api/processes/{hash}/execute-unit?unit_id=step_1&unit_type=workflow&target=transform
```

See [Orchestration Patterns](docs/orchestration-patterns.md) for detailed examples.

---

## Utilities

### `deep_freeze(obj)`

Recursively makes a JSON-like structure immutable:

- `dict` becomes `MappingProxyType` (read-only)
- `list` becomes `tuple`
- Primitives (`str`, `int`, `float`, `bool`, `None`) are already immutable

```python
from validance.sdk import deep_freeze

context = {"scores": [0.9, 0.8], "meta": {"version": 1}}
frozen = deep_freeze(context)

frozen["scores"]        # (0.9, 0.8) — tuple, not list
frozen["meta"]["version"]  # 1
frozen["meta"]["x"] = 2   # TypeError: 'mappingproxy' object does not support item assignment
```

Used by the engine to protect context passed to branch decision functions.

---

## Contract Versioning

```python
from validance.sdk import __contract_version__
print(__contract_version__)  # "2.0.0"
```

The contract version follows semver:

- **Patch** — bug fixes, no behavioral change
- **Minor** — new features, backward compatible
- **Major** — breaking change (engine may reject workflows built against incompatible versions)

The engine checks the SDK contract version at load time to ensure compatibility.

---

## Architecture

The SDK is one layer of a three-layer architecture:

```
SDK (this package)     Engine (server)       Worker (execution)
─────────────────     ─────────────────     ─────────────────
Task, Workflow         Orchestration          Docker containers
declarations           state, audit           file I/O, uploads
(zero deps)            (PostgreSQL)           (Docker socket)
```

**What the SDK does:** Pure declaration. Define tasks and workflows as immutable data structures with validation. Serialize to JSON.

**What the SDK does NOT do:** No HTTP calls, no database access, no Docker interaction, no file I/O, no side effects. The engine handles all of that.

Any system that can make HTTP calls can orchestrate Validance workflows — Python, Go, bash, CI pipelines. The SDK is a convenience for Python users, not a requirement.

---

## License

Apache 2.0