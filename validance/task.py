"""Task declaration — the unit of work in a Validance workflow.

A task is an isolated, containerized computation with declared inputs,
outputs, and dependencies.  The engine handles execution, storage,
hashing, and audit logging.  The workflow author only declares intent.
"""

import warnings
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Deep-freeze utility
# ---------------------------------------------------------------------------

#REVIEW: deep_freeze makes a JSON-like structure fully immutable, recursively.
#REVIEW: Used by the engine to protect the branch-decision context so that
#REVIEW: user-supplied decision callables cannot tamper with shared state.
#REVIEW: Without this, a malicious/buggy decision lambda could mutate the
#REVIEW: context dict and silently corrupt data flowing to downstream tasks.
#REVIEW: This is the technical control behind provenance integrity
#REVIEW: (patent claims 17, 19) and ALCOA+ "accurate" / "original" requirements.
def deep_freeze(obj: Any) -> Any:
    """Recursively make a JSON-like structure immutable.

    - ``dict``  → ``MappingProxyType`` (read-only mapping)
    - ``list``  → ``tuple``            (immutable sequence)
    - primitives (str, int, float, bool, None) are already immutable.

    The returned tree shares no mutable references with *obj*, so the
    caller's data cannot be modified through the frozen view.
    """
    #REVIEW: dict → MappingProxyType: read-only wrapper from the stdlib.
    #REVIEW: Any attempt to __setitem__, __delitem__, pop, update, etc.
    #REVIEW: raises TypeError immediately.
    if isinstance(obj, dict):
        return MappingProxyType({k: deep_freeze(v) for k, v in obj.items()})
    #REVIEW: list → tuple: immutable sequence. Also recurses into elements
    #REVIEW: so nested dicts inside lists are frozen too.
    if isinstance(obj, list):
        return tuple(deep_freeze(item) for item in obj)
    #REVIEW: Primitives (str, int, float, bool, None) are already immutable
    #REVIEW: in Python — nothing to do. NOTE: types like set, bytearray, or
    #REVIEW: custom objects would pass through unfrozen. Currently safe because
    #REVIEW: context is always JSON-like, but worth hardening later.
    return obj


# ---------------------------------------------------------------------------
# Task dataclass
# ---------------------------------------------------------------------------

#REVIEW: frozen=True makes the dataclass immutable after construction.
#REVIEW: Any attempt to reassign a field (task.name = "x") raises
#REVIEW: FrozenInstanceError. This enforces the "definition-only" intent:
#REVIEW: once declared, a task definition cannot be accidentally modified.
#REVIEW: The engine's original Task (workflow.py:2391) was a mutable @dataclass
#REVIEW: that also contained execution methods (get_hash, _emit_task_provenance,
#REVIEW: run logic at line 2458). All execution logic stays in the engine;
#REVIEW: this SDK Task holds ONLY declaration fields.
#REVIEW: Mitigates Risk R-008 (mutable Task after construction).
@dataclass(frozen=True)
class Task:
    """Declare a containerized task.

    This is a **definition-only** object — it holds no execution state,
    no database handles, no storage references.  The engine consumes
    this object and wraps it in its own execution machinery.

    Attributes:
        name:         Unique identifier within the workflow.
        command:      Shell command executed inside the container.
        docker_image: Docker image (tag or digest).  ``None`` means the
                      engine's ``DEFAULT_DOCKER_IMAGE`` setting applies.
        inputs:       Mapping of *filename-inside-container* → source.
                      Sources can be:
                        ``"@task_name:output"``  — output of a prior task
                        ``"${parameter_name}"``  — runtime parameter
                        ``"azure://…"``          — direct storage URI
        outputs:      **Deprecated.** Use ``output_files`` dict instead.
                      Mapping of *variable_name* → output filename.
                      If provided when ``output_files`` is empty, merged
                      into ``output_files`` during normalization.
        output_files: Mapping of *variable_name* → output filename.
        output_vars:  Mapping of *variable_name* → type string.
                      Allowed types: ``"str"``, ``"int"``, ``"float"``,
                      ``"bool"``, ``"json"``.  The task writes these
                      variables to ``_validance_vars.json``; the engine
                      reads and validates them after execution.
        depends_on:   Task names that must complete before this one.
        environment:  Extra environment variables passed to the container.
        volumes:      Additional volume mounts (host_path → mount config).
        parallel:     If ``True``, may execute concurrently with other
                      parallel tasks at the same dependency level.
        timeout:      Maximum execution time in seconds.

    Example::

        retrieve = Task(
            name="retrieve",
            docker_image="rag-tasks:latest",
            command="python modules/rag/tasks/retrieve.py index.json",
            inputs={"index.json": "@build_index:result"},
            output_files={"result": "retrieval.json"},
            timeout=600,
        )
    """

    #REVIEW: These fields mirror the engine's original Task dataclass
    #REVIEW: (workflow.py:2394-2405) but WITHOUT execution concerns.
    #REVIEW: Notably: docker_image defaults to None here (not resolved
    #REVIEW: from os.getenv). The engine resolves it at execution time.
    #REVIEW: This removes the side effect from the engine's __post_init__
    #REVIEW: (workflow.py:2407-2410) which called os.getenv at declaration
    #REVIEW: time — a violation of "definition-only, no side effects."
    name: str
    command: str = ""
    docker_image: Optional[str] = None
    inputs: Dict[str, str] = field(default_factory=dict)
    outputs: Dict[str, str] = field(default_factory=dict)
    output_files: Dict[str, str] = field(default_factory=dict)
    output_vars: Dict[str, str] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    environment: Dict[str, str] = field(default_factory=dict)
    volumes: Dict[str, Dict] = field(default_factory=dict)
    parallel: bool = False
    timeout: int = 3600
    token_budget: Optional[int] = None
    cost_budget: Optional[float] = None
    gate: str = "auto-approve"
    gate_timeout: int = 300
    persistent: bool = False
    trigger_inputs: bool = False
    secret_refs: List[str] = field(default_factory=list)

    _ALLOWED_VAR_TYPES = frozenset({"str", "int", "float", "bool", "json"})
    _VALID_GATES = frozenset({"auto-approve", "human-confirm", "always-deny"})

    def __post_init__(self):
        if not self.name:
            raise ValueError("Task name is required")

        # --- Validate output_vars types ---
        for var_name, var_type in self.output_vars.items():
            if var_type not in self._ALLOWED_VAR_TYPES:
                raise ValueError(
                    f"Task '{self.name}': output_vars type '{var_type}' for "
                    f"'{var_name}' is not allowed. "
                    f"Allowed types: {sorted(self._ALLOWED_VAR_TYPES)}"
                )

        # --- Validate gate ---
        if self.gate not in self._VALID_GATES:
            raise ValueError(
                f"Task '{self.name}': gate '{self.gate}' is not valid. "
                f"Allowed: {sorted(self._VALID_GATES)}"
            )

        # --- Validate secret_refs ---
        for ref in self.secret_refs:
            if not isinstance(ref, str) or not ref:
                raise ValueError(
                    f"Task '{self.name}': each secret_ref must be a non-empty string"
                )

    # -- Type coercion map for output_vars validation --
    _TYPE_COERCIONS = {
        "str": str,
        "int": int,
        "float": float,
        "bool": lambda v: v if isinstance(v, bool) else bool(v),
        "json": lambda v: v,  # already parsed from JSON
    }

    def validate_output_vars(self, raw: dict) -> dict:
        """Validate a raw dict against this task's ``output_vars`` contract.

        Called by the engine after reading ``_validance_vars.json``.
        The SDK owns validation; the engine owns I/O.

        Args:
            raw: Parsed JSON object from ``_validance_vars.json``.

        Returns:
            Dict of validated, type-coerced variable values.

        Raises:
            ValueError: If ``raw`` is not a dict, a required var is
                missing, or a value cannot be coerced to the declared type.
        """
        if not isinstance(raw, dict):
            raise ValueError(
                f"Task '{self.name}': _validance_vars.json must be a JSON object"
            )

        result = {}

        # Validate declared vars
        for var_name, var_type in self.output_vars.items():
            if var_name not in raw:
                raise ValueError(
                    f"Task '{self.name}': required output var '{var_name}' "
                    f"(type={var_type}) not found in _validance_vars.json"
                )
            coerce = self._TYPE_COERCIONS.get(var_type, str)
            try:
                result[var_name] = coerce(raw[var_name])
            except (TypeError, ValueError) as e:
                raise ValueError(
                    f"Task '{self.name}': output var '{var_name}' cannot be "
                    f"coerced to {var_type}: {e}"
                ) from e

        # Include undeclared vars with warning
        for var_name, value in raw.items():
            if var_name not in self.output_vars:
                warnings.warn(
                    f"Task '{self.name}': undeclared output var '{var_name}' "
                    f"in _validance_vars.json (not in output_vars)",
                    UserWarning,
                    stacklevel=2,
                )
                result[var_name] = value

        return result

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict.

        Omits fields that match their defaults to keep the output compact.
        """
        d: dict = {"name": self.name, "command": self.command}
        if self.docker_image is not None:
            d["docker_image"] = self.docker_image
        if self.inputs:
            d["inputs"] = dict(self.inputs)
        if self.outputs:  # deprecated, include if non-empty
            d["outputs"] = dict(self.outputs)
        if self.output_files:
            d["output_files"] = dict(self.output_files)
        if self.output_vars:
            d["output_vars"] = dict(self.output_vars)
        if self.depends_on:
            d["depends_on"] = list(self.depends_on)
        if self.environment:
            d["environment"] = dict(self.environment)
        if self.volumes:
            d["volumes"] = {k: dict(v) for k, v in self.volumes.items()}
        if self.parallel:
            d["parallel"] = True
        if self.timeout != 3600:
            d["timeout"] = self.timeout
        if self.token_budget is not None:
            d["token_budget"] = self.token_budget
        if self.cost_budget is not None:
            d["cost_budget"] = self.cost_budget
        if self.gate != "auto-approve":
            d["gate"] = self.gate
        if self.gate_timeout != 300:
            d["gate_timeout"] = self.gate_timeout
        if self.persistent:
            d["persistent"] = True
        if self.trigger_inputs:
            d["trigger_inputs"] = True
        if self.secret_refs:
            d["secret_refs"] = list(self.secret_refs)
        return d
