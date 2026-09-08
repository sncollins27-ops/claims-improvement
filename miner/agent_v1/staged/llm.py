from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any

import requests


logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


def direct_model_id(model: str, *, provider: str, api_base: str) -> str:
    """Map a runtime model id onto the id the OpenAI-compatible endpoint expects."""
    normalized = str(model or "").strip()
    if provider == "openrouter" or "openrouter.ai" in api_base:
        return normalized.removeprefix("openrouter/")
    return normalized


def empty_usage() -> dict[str, Any]:
    return {
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


class LLMClient:
    """Small, dependency-light OpenAI-compatible chat client with JSON parsing and usage accounting."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        api_base: str,
        provider: str = "openrouter",
        timeout_seconds: float = 300.0,
        max_retries: int = 4,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        app_title: str = "claims-improvement-james",
    ) -> None:
        self.model = model
        self.direct_model = direct_model_id(model, provider=provider, api_base=api_base)
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.provider = provider
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.reasoning_effort = reasoning_effort or None
        self.temperature = temperature
        self.app_title = app_title
        self._lock = threading.Lock()
        self.totals = empty_usage()
        self.by_stage: dict[str, dict[str, Any]] = {}
        self.calls: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ public
    def complete_json(
        self,
        *,
        system: str,
        user: str,
        stage: str,
        max_tokens: int = 16000,
        reasoning_effort: str | None = None,
        label: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        body: dict[str, Any] = {
            "model": self.direct_model,
            "messages": messages,
            "max_tokens": int(max_tokens),
            "response_format": {"type": "json_object"},
            "usage": {"include": True},
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        effort = reasoning_effort if reasoning_effort is not None else self.reasoning_effort
        if effort:
            body["reasoning"] = {"effort": effort}

        started = time.perf_counter()
        content, usage = self._post_with_retries(body, stage=stage, label=label)
        parsed = _parse_json_object(content)
        if parsed is None:
            # One nudge: ask again for strict JSON only.
            nudged = dict(body)
            nudged["messages"] = messages + [
                {"role": "assistant", "content": content[:4000]},
                {"role": "user", "content": "Your previous reply was not a single valid JSON object. Return ONLY the complete JSON object now."},
            ]
            content, usage2 = self._post_with_retries(nudged, stage=stage, label=f"{label}:json-retry")
            usage = _merge(usage, usage2)
            parsed = _parse_json_object(content)
            if parsed is None:
                raise LLMError(f"Model did not return a JSON object for stage={stage} label={label}: {content[:300]!r}")
        usage["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        self._record(stage, usage, label)
        return parsed, usage

    def usage_summary(self) -> dict[str, Any]:
        with self._lock:
            summary = dict(self.totals)
            summary["by_stage"] = {key: dict(value) for key, value in self.by_stage.items()}
            summary["model"] = self.model
            return summary

    # ----------------------------------------------------------------- private
    def _post_with_retries(self, body: dict[str, Any], *, stage: str, label: str) -> tuple[str, dict[str, Any]]:
        attempt = 0
        body = dict(body)
        last_error: Exception | None = None
        while attempt <= self.max_retries:
            attempt += 1
            try:
                response = requests.post(
                    f"{self.api_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://github.com/claims-improvement-james",
                        "X-Title": self.app_title,
                    },
                    data=json.dumps(body),
                    timeout=self.timeout_seconds,
                )
            except requests.RequestException as exc:
                last_error = exc
                logger.warning("LLM request error stage=%s label=%s attempt=%s: %s", stage, label, attempt, exc)
                time.sleep(min(30.0, 2.0 ** attempt))
                continue
            if response.status_code == 400:
                text = response.text
                lowered = text.lower()
                if "reasoning" in lowered and "reasoning" in body:
                    body.pop("reasoning", None)
                    logger.info("Provider rejected reasoning parameter; retrying without it (stage=%s)", stage)
                    continue
                if "response_format" in lowered and "response_format" in body:
                    body.pop("response_format", None)
                    logger.info("Provider rejected response_format; retrying without it (stage=%s)", stage)
                    continue
                if "temperature" in lowered and "temperature" in body:
                    body.pop("temperature", None)
                    logger.info("Provider rejected temperature; retrying without it (stage=%s)", stage)
                    continue
                raise LLMError(f"HTTP 400 from provider (stage={stage} label={label}): {text[:600]}")
            if response.status_code in {401, 403}:
                raise LLMError(f"HTTP {response.status_code} from provider: {response.text[:300]}")
            if response.status_code >= 400:
                last_error = LLMError(f"HTTP {response.status_code}: {response.text[:300]}")
                logger.warning("LLM HTTP %s stage=%s label=%s attempt=%s", response.status_code, stage, label, attempt)
                time.sleep(min(45.0, 2.0 ** attempt + 1))
                continue
            try:
                payload = response.json()
            except ValueError as exc:
                last_error = exc
                time.sleep(2.0)
                continue
            if isinstance(payload, dict) and payload.get("error"):
                message = json.dumps(payload.get("error"))[:400]
                last_error = LLMError(message)
                logger.warning("LLM provider error stage=%s label=%s attempt=%s: %s", stage, label, attempt, message)
                time.sleep(min(45.0, 2.0 ** attempt + 1))
                continue
            choices = payload.get("choices") if isinstance(payload, dict) else None
            if not choices:
                last_error = LLMError(f"No choices in response: {json.dumps(payload)[:300]}")
                time.sleep(2.0)
                continue
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            if not isinstance(content, str) or not content.strip():
                finish = choices[0].get("finish_reason")
                last_error = LLMError(f"Empty content (finish_reason={finish})")
                if finish == "length":
                    body["max_tokens"] = int(body.get("max_tokens", 16000) * 1.5)
                time.sleep(2.0)
                continue
            return content, _usage_from_payload(payload)
        raise LLMError(f"LLM call failed after {attempt} attempts (stage={stage} label={label}): {last_error}")

    def _record(self, stage: str, usage: dict[str, Any], label: str) -> None:
        with self._lock:
            self.totals = _merge(self.totals, usage)
            self.by_stage[stage] = _merge(self.by_stage.get(stage) or empty_usage(), usage)
            self.calls.append({"stage": stage, "label": label, **{k: usage.get(k) for k in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "cost_usd", "elapsed_seconds")}})


def _usage_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    completion_details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
    prompt_details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    cost = usage.get("cost")
    return {
        "prompt_tokens": _int(usage.get("prompt_tokens")),
        "completion_tokens": _int(usage.get("completion_tokens")),
        "reasoning_tokens": _int(completion_details.get("reasoning_tokens")),
        "cache_read_tokens": _int(prompt_details.get("cached_tokens")),
        "cache_write_tokens": None,
        "total_tokens": _int(usage.get("total_tokens")),
        "cost_usd": float(cost) if isinstance(cost, int | float) else 0.0,
        "cost_kind": "actual" if isinstance(cost, int | float) else "unavailable",
        "source": "openai_compatible_response",
        "requests": 1,
    }


def _merge(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "cache_read_tokens", "total_tokens", "requests"):
        merged[key] = (_int(left.get(key)) or 0) + (_int(right.get(key)) or 0)
    merged["cost_usd"] = round(float(left.get("cost_usd") or 0.0) + float(right.get("cost_usd") or 0.0), 8)
    merged["cost_kind"] = "actual" if (left.get("cost_kind") in {"actual", None} and right.get("cost_kind") in {"actual", None}) else "estimated"
    merged["source"] = "openai_compatible_response"
    if "elapsed_seconds" in left or "elapsed_seconds" in right:
        merged["elapsed_seconds"] = round(float(left.get("elapsed_seconds") or 0.0) + float(right.get("elapsed_seconds") or 0.0), 3)
    return merged


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    stripped = (text or "").strip()
    if not stripped:
        return None
    candidates = [stripped]
    candidates.extend(_FENCE.findall(stripped))
    balanced = _balanced_object(stripped)
    if balanced:
        candidates.append(balanced)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _balanced_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""
