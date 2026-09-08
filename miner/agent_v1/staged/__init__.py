"""Staged, coverage-first agent_v1 runtime.

The pipeline replaces a single monolithic agent loop with deterministic
stages: per-span candidate extraction (parallel), deterministic quote and
number grounding with repair, global consolidation, structure synthesis, and
artifact assembly. Every stage is recorded in ``staged_trace.json`` so runs
can be audited and compared in the dashboard.
"""

from .runtime import StagedRuntime

__all__ = ["StagedRuntime"]
