"""Claims miner packages (improved fork).

Only ``agent_v1`` is imported eagerly. The legacy ``section_context_v1`` and
``ontology_context_v1`` pipelines are resolved lazily so the improved miner
does not need them installed.
"""

from __future__ import annotations

import importlib
from typing import Any

from .agent_v1 import AgentV1Config, AgentV1Runner, Artifact, SkillPack, load_skill_pack, materialize_agent_artifact, validate_agent_artifact

_LAZY = {
    "SectionContextV1Config": ("miner.section_context_v1", "SectionContextV1Config"),
    "SectionContextV1Runner": ("miner.section_context_v1", "SectionContextV1Runner"),
    "OntologyContextV1Config": ("miner.ontology_context_v1", "OntologyContextV1Config"),
    "OntologyContextV1Miner": ("miner.ontology_context_v1", "OntologyContextV1Miner"),
    "OntologyContextV1Runner": ("miner.ontology_context_v1", "OntologyContextV1Runner"),
    "OntologyContextV1Validator": ("miner.ontology_context_v1", "OntologyContextV1Validator"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(name)
    module = importlib.import_module(target[0])
    return getattr(module, target[1])


__all__ = [
    "Artifact",
    "AgentV1Config",
    "AgentV1Runner",
    "SkillPack",
    "load_skill_pack",
    "materialize_agent_artifact",
    "validate_agent_artifact",
    *_LAZY.keys(),
]
