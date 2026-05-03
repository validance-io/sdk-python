"""Workflow declaration — a directed acyclic graph of tasks.

A workflow is a named, ordered collection of tasks with declared
dependencies.  The engine resolves the DAG, executes tasks, manages
file transport, and builds the provenance chain.  The workflow author
only declares the graph.
"""

import hashlib
import json
from typing import Dict, List, Optional

from validance.task import Task


#REVIEW: The engine's original Workflow class (workflow.py:3330) mixed
#REVIEW: declaration (add_task, name, tasks) with execution
#REVIEW: concerns (self.db = DatabaseManager(), self.context, self.env_info,
#REVIEW: run(), continue_workflow(), get_hash(), cleanup, cancellation checks).
#REVIEW: This SDK Workflow is DEFINITION-ONLY — no DB, no Docker, no env.
#REVIEW: The engine wraps this object in its own execution machinery.
#REVIEW: Mitigates Risk R-010 (contract interface boundary).
class Workflow:
    """Declare a workflow (a DAG of tasks).

    This is a **definition-only** object — it holds no database handles,
    no execution context, no environment info.  The engine consumes
    this object and wraps it in its own orchestration machinery.

    Args:
        name: Unique workflow identifier (e.g. ``"rag_chat"``).

    Example::

        from validance import Task, Workflow

        def create_workflow():
            wf = Workflow("rag_chat")

            retrieve = Task(
                name="retrieve",
                docker_image="rag-tasks:latest",
                command="python modules/rag/tasks/retrieve.py index.json",
                inputs={"index.json": "@build_index:result"},
                output_files=["retrieval.json"],
                outputs={"result": "retrieval.json"},
            )

            generate = Task(
                name="generate",
                docker_image="rag-tasks:latest",
                command="python modules/rag/tasks/generate.py prompt.json",
                inputs={"prompt.json": "@retrieve:result"},
                output_files=["response.json"],
                outputs={"result": "response.json"},
                depends_on=["retrieve"],
            )

            wf.add_task(retrieve)
            wf.add_task(generate)
            return wf
    """

    def __init__(self, name: str):
        if not name:
            raise ValueError("Workflow name is required")
        self.name: str = name
        self._tasks: Dict[str, Task] = {}
        #REVIEW: Compare with the engine's __init__ (workflow.py:3333-3343):
        #REVIEW:   self.db = DatabaseManager()   — engine concern, removed
        #REVIEW:   self.context = {}             — runtime state, removed
        #REVIEW:   self.cleanup_working_dir = .. — engine config, removed
        #REVIEW:   self.env_info = detect_env()  — runtime detection, removed
        #REVIEW: Only the structural declaration (name, tasks) remains.

    # ------------------------------------------------------------------
    # Builder API (fluent)
    # ------------------------------------------------------------------

    def add_task(self, task: Task) -> "Workflow":
        """Add a task to the workflow.  Returns *self* for chaining.

        Raises:
            TypeError:  If *task* is not a :class:`validance.Task`.
            ValueError: If a task with the same name already exists.
        """
        #REVIEW: isinstance check enforces that only SDK Task objects can be
        #REVIEW: added. The engine's original add_task (workflow.py:3345-3349)
        #REVIEW: accepted anything and just stored it — no type check, no
        #REVIEW: duplicate check. This adds both.
        #REVIEW: Mitigates Risk R-010 (contract boundary enforcement).
        if not isinstance(task, Task):
            raise TypeError(
                f"Expected validance.Task, got {type(task).__name__}. "
                f"Workflow tasks must be declared with the SDK."
            )
        if task.name in self._tasks:
            raise ValueError(
                f"Duplicate task name '{task.name}' in workflow '{self.name}'"
            )
        self._tasks[task.name] = task
        return self

    # ------------------------------------------------------------------
    # Read-only accessors
    # ------------------------------------------------------------------

    @property
    def tasks(self) -> Dict[str, Task]:
        #REVIEW: Returns a COPY of the internal dict. The engine's original
        #REVIEW: Workflow exposed self.tasks directly (workflow.py:3335),
        #REVIEW: allowing external code to inject tasks by doing
        #REVIEW: workflow.tasks["evil"] = Task(...). The copy prevents this.
        """Read-only copy of the registered tasks."""
        return dict(self._tasks)

    # ------------------------------------------------------------------
    # Contract validation
    # ------------------------------------------------------------------

    def validate(self, existing_tasks: Optional[Dict[str, "Task"]] = None) -> List[str]:
        """Check the workflow definition for structural errors.

        Returns a list of error messages.  An empty list means the
        definition is valid.  Checks performed:

        1. Missing dependencies — a task declares ``depends_on`` a name
           that is not defined in this workflow or ``existing_tasks``.
        2. Circular dependencies — detected via Kahn's algorithm
           (across both this workflow's tasks and ``existing_tasks``).

        Args:
            existing_tasks: Optional dict of tasks from prior workflows
                (e.g. from a continuation chain). New tasks may declare
                dependencies on these. Backward-compatible: ``None`` =
                current behavior.

        Workflow authors can call this for early feedback.  The engine
        also calls it before execution.
        """
        #REVIEW: This validation logic did NOT exist in the engine's original
        #REVIEW: Workflow class. The engine relied on runtime failures to
        #REVIEW: detect bad DAGs. The SDK adds static validation that can
        #REVIEW: catch errors before any container is launched.
        errors: List[str] = []
        task_names = set(self._tasks.keys())

        # Merge with existing tasks for dependency resolution
        all_known = set(task_names)
        if existing_tasks:
            all_known |= set(existing_tasks.keys())

        # --- missing dependencies ---
        for task in self._tasks.values():
            for dep in task.depends_on:
                if dep not in all_known:
                    errors.append(
                        f"Task '{task.name}' depends on '{dep}' "
                        f"which is not defined in workflow '{self.name}'"
                    )

        #REVIEW: Kahn's algorithm for topological sort / cycle detection.
        #REVIEW: Counts in-degree (number of dependencies) per task, then
        #REVIEW: repeatedly removes tasks with in-degree 0. If not all tasks
        #REVIEW: are visited, there's a cycle in the dependency graph.
        # --- cycle detection (Kahn's algorithm) ---
        # Include existing tasks in the graph for cross-boundary detection
        all_tasks = dict(self._tasks)
        if existing_tasks:
            all_tasks.update(existing_tasks)
        all_task_names = set(all_tasks.keys())

        in_degree = {name: 0 for name in all_task_names}
        for task in all_tasks.values():
            for dep in task.depends_on:
                if dep in in_degree:
                    in_degree[task.name] += 1

        queue = [n for n, d in in_degree.items() if d == 0]
        visited = 0
        while queue:
            node = queue.pop(0)
            visited += 1
            for task in all_tasks.values():
                if node in task.depends_on:
                    in_degree[task.name] -= 1
                    if in_degree[task.name] == 0:
                        queue.append(task.name)

        if visited != len(all_task_names):
            errors.append(
                f"Circular dependency detected in workflow '{self.name}'"
            )

        return errors

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict.

        Delegates to ``Task.to_dict()`` for each task.
        """
        d: dict = {
            "name": self.name,
            "tasks": [t.to_dict() for t in self._tasks.values()],
        }
        return d

    def to_json(self, indent=None) -> str:
        """Canonical JSON string (deterministic, sorted keys)."""
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(',', ':') if indent is None else None,
            indent=indent,
        )

    @property
    def definition_hash(self) -> str:
        """SHA-256 of the canonical JSON representation."""
        return hashlib.sha256(self.to_json().encode('utf-8')).hexdigest()
