"""Validance SDK — Contract Interface

This is the ONLY module that workflow definitions should import.
All workflow interactions with the Validance engine pass through this
interface.  Engine internals are not exposed.

    from validance.sdk import Task, Workflow

Contract versioning (semver):
    Patch  — bug fixes, no behavioral change, no revalidation.
    Minor  — new features, backward compatible, no revalidation.
    Major  — breaking change, revalidation required.
"""

#REVIEW: Contract version string — every SDK release gets a semver bump.
#REVIEW: Workflow authors can read this to check compatibility.
#REVIEW: Engine should verify sdk.__contract_version__ at load time
#REVIEW: to reject workflows built against incompatible SDK versions.
__contract_version__ = "2.0.0"

#REVIEW: These are the ONLY symbols workflow authors should use.
#REVIEW: Before the SDK existed, workflows imported directly from
#REVIEW: the engine module (`from workflow import Task, Workflow`),
#REVIEW: which gave them access to engine internals (DB, Docker, etc.).
#REVIEW: This boundary mitigates Risk R-010 (contract interface not enforced).
from validance.sdk.task import Task, deep_freeze
from validance.sdk.workflow import Workflow


__all__ = [
    "Task",
    "Workflow",
    "deep_freeze",
    "__contract_version__",
]
