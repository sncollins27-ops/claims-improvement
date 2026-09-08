from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from ..config import AgentV1Config
from ..runtime.base import AgentRequest, AgentResult
from ..skillpack import SkillPack
from .llm import LLMClient
from .pipeline import StagedPipeline, StagedSettings


logger = logging.getLogger(__name__)


DEFAULT_PROMPT_FILES = {
    "extract": "prompts/extract.md",
    "consolidate": "prompts/consolidate.md",
    "structure": "prompts/structure.md",
}


class StagedRuntime:
    """Coverage-first staged runtime (extract -> ground -> consolidate -> structure -> assemble)."""

    runtime_name = "staged"

    def __init__(self, config: AgentV1Config) -> None:
        self.config = config

    def run_skill(self, *, skill_pack: SkillPack, run_dir: Path, request: AgentRequest) -> AgentResult:
        started = time.time()
        source_payload = json.loads((run_dir / request.source_payload_path).read_text(encoding="utf-8"))
        paper = dict(request.paper or {})
        prompts = self._prompts(skill_pack)
        settings = self._settings()
        clients = self._clients()
        pipeline = StagedPipeline(clients=clients, prompts=prompts, settings=settings, paper=paper, source_payload=source_payload)
        error: str | None = None
        try:
            artifact = pipeline.run()
        except Exception as exc:  # pragma: no cover - defensive: always leave a trace on disk
            logger.exception("staged runtime failed")
            error = f"{type(exc).__name__}: {exc}"
            artifact = {}
        elapsed = round(time.time() - started, 3)
        usage = _merged_usage(clients)
        trace = pipeline.trace
        trace["elapsed_seconds"] = elapsed
        trace["error"] = error
        trace["settings"] = settings.__dict__
        trace["models"] = {stage: client.model for stage, client in clients.items()}
        (run_dir / "staged_trace.json").write_text(json.dumps(trace, indent=2, ensure_ascii=False), encoding="utf-8")
        output_path = run_dir / request.expected_output_path
        if artifact:
            artifact.setdefault("metadata", {})["staged_runtime"] = {
                **artifact.get("metadata", {}).get("staged_runtime", {}),
                "models": trace["models"],
                "elapsed_seconds": elapsed,
                "usage": {key: usage.get(key) for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "total_tokens", "cost_usd", "requests")},
            }
            output_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
        elif output_path.exists():
            output_path.unlink()
        manifest = {
            "runtime": self.runtime_name,
            "harness": "staged",
            "model": self.config.model,
            "models": trace["models"],
            "elapsed_seconds": elapsed,
            "usage": usage,
            "stages": trace.get("stages", []),
            "skill": skill_pack.manifest(),
            "error": error,
        }
        if error:
            raise RuntimeError(f"staged runtime failed: {error}")
        return AgentResult(output_path=output_path, manifest=manifest, stdout="", stderr="")

    # ------------------------------------------------------------------ helpers
    def _prompts(self, skill_pack: SkillPack) -> dict[str, str]:
        prompts: dict[str, str] = {}
        skill_summary = skill_pack.instructions.strip()
        for stage, path in DEFAULT_PROMPT_FILES.items():
            try:
                text = skill_pack.resource_text(path)
            except FileNotFoundError:
                raise RuntimeError(
                    f"The mounted skill pack '{skill_pack.name}' has no {path}; the staged runtime requires the claims_coverage skill pack"
                ) from None
            # Prepend a compact header so each stage carries the skill's core rules.
            prompts[stage] = f"{text.strip()}\n\n--- SKILL CONTEXT (summary) ---\n{_skill_summary(skill_summary)}"
        return prompts

    def _settings(self) -> StagedSettings:
        env = os.getenv
        return StagedSettings(
            extract_concurrency=int(env("SUBNET_CLAIMS_STAGED_EXTRACT_CONCURRENCY", "4")),
            max_span_chars=int(env("SUBNET_CLAIMS_STAGED_MAX_SPAN_CHARS", "9000")),
            min_quote_chars=int(env("SUBNET_CLAIMS_STAGED_MIN_QUOTE_CHARS", "25")),
            max_quote_chars=int(env("SUBNET_CLAIMS_STAGED_MAX_QUOTE_CHARS", "420")),
            max_candidates_per_consolidation=int(env("SUBNET_CLAIMS_STAGED_MAX_CANDIDATES_PER_CONSOLIDATION", "70")),
            cross_group_duplicate_threshold=float(env("SUBNET_CLAIMS_STAGED_CROSS_GROUP_DUPLICATE_THRESHOLD", "0.55")),
            merge_min_overlap=float(env("SUBNET_CLAIMS_STAGED_MERGE_MIN_OVERLAP", "0.3")),
            merge_max_members=int(env("SUBNET_CLAIMS_STAGED_MERGE_MAX_MEMBERS", "6")),
            premerge_overlap=float(env("SUBNET_CLAIMS_STAGED_PREMERGE_OVERLAP", "0.5")),
            retention_floor=float(env("SUBNET_CLAIMS_STAGED_RETENTION_FLOOR", "0.55")),
            selfcheck=env("SUBNET_CLAIMS_STAGED_SELFCHECK", "true").strip().lower() not in {"0", "false", "no"},
            selfcheck_batch=int(env("SUBNET_CLAIMS_STAGED_SELFCHECK_BATCH", "25")),
            max_claims=int(env("SUBNET_CLAIMS_STAGED_MAX_CLAIMS", "150")),
            extract_max_tokens=int(env("SUBNET_CLAIMS_STAGED_EXTRACT_MAX_TOKENS", "16000")),
            consolidate_max_tokens=int(env("SUBNET_CLAIMS_STAGED_CONSOLIDATE_MAX_TOKENS", "32000")),
            structure_max_tokens=int(env("SUBNET_CLAIMS_STAGED_STRUCTURE_MAX_TOKENS", "32000")),
            extract_reasoning_effort=_effort(env("SUBNET_CLAIMS_STAGED_EXTRACT_REASONING_EFFORT", "low")),
            consolidate_reasoning_effort=_effort(env("SUBNET_CLAIMS_STAGED_CONSOLIDATE_REASONING_EFFORT", "low")),
            structure_reasoning_effort=_effort(env("SUBNET_CLAIMS_STAGED_STRUCTURE_REASONING_EFFORT", "low")),
            repair_ungrounded=env("SUBNET_CLAIMS_STAGED_REPAIR_UNGROUNDED", "true").strip().lower() not in {"0", "false", "no"},
            near_duplicate_threshold=float(env("SUBNET_CLAIMS_STAGED_NEAR_DUPLICATE_THRESHOLD", "0.5")),
        )

    def _clients(self) -> dict[str, LLMClient]:
        api_key = self.config.require_api_key()
        temperature_raw = os.getenv("SUBNET_CLAIMS_STAGED_TEMPERATURE", "").strip()
        temperature = float(temperature_raw) if temperature_raw else None
        timeout = float(os.getenv("SUBNET_CLAIMS_STAGED_REQUEST_TIMEOUT", "300"))
        cache: dict[str, LLMClient] = {}

        def client_for(model: str) -> LLMClient:
            if model not in cache:
                cache[model] = LLMClient(
                    model=model,
                    api_key=api_key,
                    api_base=self.config.api_base,
                    provider=self.config.provider,
                    timeout_seconds=timeout,
                    temperature=temperature,
                )
            return cache[model]

        default_model = self.config.model
        return {
            "extract": client_for(os.getenv("SUBNET_CLAIMS_STAGED_EXTRACT_MODEL", "").strip() or default_model),
            "consolidate": client_for(os.getenv("SUBNET_CLAIMS_STAGED_CONSOLIDATE_MODEL", "").strip() or default_model),
            "structure": client_for(os.getenv("SUBNET_CLAIMS_STAGED_STRUCTURE_MODEL", "").strip() or default_model),
        }


def _effort(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"", "none", "off", "0", "false"}:
        return None
    return normalized


def _skill_summary(skill_markdown: str) -> str:
    """Return the Hard rules + Coverage self-check sections of SKILL.md (compact context for each stage)."""
    lines = skill_markdown.splitlines()
    keep: list[str] = []
    capture = False
    for line in lines:
        if line.startswith("## "):
            capture = line.strip() in {"## Hard rules", "## Coverage self-check before finishing"}
            if capture:
                keep.append(line)
            continue
        if capture:
            keep.append(line)
    text = "\n".join(keep).strip()
    return text[:4000] if text else skill_markdown[:2500]


def _merged_usage(clients: dict[str, LLMClient]) -> dict[str, Any]:
    seen: dict[int, LLMClient] = {}
    for client in clients.values():
        seen[id(client)] = client
    totals: dict[str, Any] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "reasoning_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": None,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "cost_kind": "actual",
        "source": "openai_compatible_response",
        "requests": 0,
    }
    for client in seen.values():
        summary = client.usage_summary()
        for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "cache_read_tokens", "total_tokens", "requests"):
            totals[key] += int(summary.get(key) or 0)
        totals["cost_usd"] = round(totals["cost_usd"] + float(summary.get("cost_usd") or 0.0), 8)
        if summary.get("cost_kind") == "estimated":
            totals["cost_kind"] = "estimated"
    return totals
