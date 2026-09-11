"""Provider-neutral LLM layer.

Every stage of the pipeline is one request that must come back as valid JSON
matching a Pydantic model. That contract lives here; the per-provider wire
details live in `llm_gemini.py` and `llm_anthropic.py`.
"""

from __future__ import annotations

import copy
import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Sequence, Type, TypeVar

from pydantic import BaseModel

from .ingest import ContentPart, TextPart


def load_dotenv(path: str | Path = ".env") -> None:
    """Load KEY=VALUE lines from a .env file into the environment.

    Hand-rolled to avoid a dependency. Real environment variables always win,
    so an exported key is never silently overridden by a stale file.
    """
    env_path = Path(path)
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()

DEFAULT_PROVIDER = "gemini"
DEFAULT_EFFORT = "high"
DEFAULT_MAX_TOKENS = 32000

DEFAULT_MODELS = {
    "gemini": "gemini-3.8-flash",
    "anthropic": "claude-opus-5",
}

EFFORT_LEVELS = ["minimal", "low", "medium", "high", "xhigh", "max"]

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Raised when the model could not produce a usable structured response."""


# --------------------------------------------------------------------------
# Pydantic JSON schema -> strict JSON schema
# --------------------------------------------------------------------------

_DROP_KEYS = {"title", "default", "$defs", "definitions"}


def _resolve(node: Any, defs: Dict[str, Any]) -> Any:
    """Inline every $ref and normalise the node into a strict-mode schema."""
    if isinstance(node, list):
        return [_resolve(item, defs) for item in node]
    if not isinstance(node, dict):
        return node

    # A bare $ref, or pydantic's `allOf: [{$ref: ...}]` wrapper used when a
    # referenced field also carries a description.
    if "$ref" in node:
        target = defs[node["$ref"].rsplit("/", 1)[-1]]
        merged = _resolve(target, defs)
        extra = {k: v for k, v in node.items() if k != "$ref" and k not in _DROP_KEYS}
        return {**merged, **extra}
    if "allOf" in node and len(node["allOf"]) == 1:
        merged = _resolve(node["allOf"][0], defs)
        extra = {k: v for k, v in node.items() if k != "allOf" and k not in _DROP_KEYS}
        return {**merged, **extra}

    out: Dict[str, Any] = {}
    for key, value in node.items():
        if key in _DROP_KEYS:
            continue
        out[key] = _resolve(value, defs)

    if out.get("type") == "object" and "properties" in out:
        out["additionalProperties"] = False
        # Strict mode wants every property listed as required; optionality is
        # expressed by the field's own type (empty string / empty list).
        out["required"] = list(out["properties"].keys())
    return out


def to_strict_schema(model: Type[BaseModel]) -> Dict[str, Any]:
    """Convert a Pydantic model into a self-contained strict JSON schema."""
    raw = copy.deepcopy(model.model_json_schema())
    defs = raw.get("$defs", raw.get("definitions", {}))
    return _resolve(raw, defs)


# --------------------------------------------------------------------------
# Base client
# --------------------------------------------------------------------------

class StructuredClient(ABC):
    """A model client that returns validated Pydantic instances."""

    def __init__(self, model: str, effort: str, max_tokens: int) -> None:
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.usage: List[Dict[str, Any]] = []

    @abstractmethod
    def structured(
        self,
        *,
        schema_model: Type[T],
        system: str,
        content: Sequence[ContentPart] | str,
        label: str = "request",
    ) -> T:
        """One request, one validated model instance."""

    @staticmethod
    def _as_parts(content: Sequence[ContentPart] | str) -> List[ContentPart]:
        return [TextPart(text=content)] if isinstance(content, str) else list(content)

    def _record_usage(self, label: str, input_tokens: int, output_tokens: int) -> None:
        self.usage.append(
            {"stage": label, "input_tokens": input_tokens, "output_tokens": output_tokens}
        )

    def usage_summary(self) -> str:
        total_in = sum(u["input_tokens"] for u in self.usage)
        total_out = sum(u["output_tokens"] for u in self.usage)
        return f"{total_in:,} input tokens, {total_out:,} output tokens across {len(self.usage)} calls"


def make_client(
    provider: str = DEFAULT_PROVIDER,
    model: str | None = None,
    effort: str = DEFAULT_EFFORT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    api_key: str | None = None,
) -> StructuredClient:
    """Build the client for the named provider.

    `model=None` picks that provider's default from DEFAULT_MODELS.
    """
    provider = provider.lower()
    resolved = model or DEFAULT_MODELS.get(provider)
    if resolved is None:
        raise LLMError(
            f"Unknown provider '{provider}'. Choose one of: {', '.join(DEFAULT_MODELS)}"
        )

    if provider == "gemini":
        from .llm_gemini import GeminiClient

        return GeminiClient(model=resolved, effort=effort, max_tokens=max_tokens, api_key=api_key)

    if provider == "anthropic":
        from .llm_anthropic import ClaudeClient

        return ClaudeClient(model=resolved, effort=effort, max_tokens=max_tokens, api_key=api_key)

    raise LLMError(
        f"Unknown provider '{provider}'. Choose one of: {', '.join(DEFAULT_MODELS)}"
    )


def dump_json(model: BaseModel) -> str:
    return json.dumps(model.model_dump(), indent=2, ensure_ascii=False)
