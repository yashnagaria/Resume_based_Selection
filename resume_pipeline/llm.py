"""Thin wrapper around the Anthropic Messages API for schema-constrained calls.

Every stage of the pipeline is one request that must come back as valid JSON
matching a Pydantic model, so that logic lives here exactly once.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Sequence, Type, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_EFFORT = "high"
DEFAULT_MAX_TOKENS = 32000

T = TypeVar("T", bound=BaseModel)


AUTH_HELP = (
    "No Anthropic credentials found.\n"
    "  Set an API key:   PowerShell  $env:ANTHROPIC_API_KEY = 'sk-ant-...'\n"
    "                    bash        export ANTHROPIC_API_KEY=sk-ant-...\n"
    "  Get one at https://console.anthropic.com/settings/keys"
)


class LLMError(RuntimeError):
    """Raised when the model could not produce a usable structured response."""


# --------------------------------------------------------------------------
# Pydantic JSON schema -> strict JSON schema the API will accept
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
        # expressed by the field's own type (empty string / empty list / null).
        out["required"] = list(out["properties"].keys())
    return out


def to_strict_schema(model: Type[BaseModel]) -> Dict[str, Any]:
    """Convert a Pydantic model into a self-contained strict JSON schema."""
    raw = copy.deepcopy(model.model_json_schema())
    defs = raw.get("$defs", raw.get("definitions", {}))
    return _resolve(raw, defs)


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------

class ClaudeClient:
    """Issues schema-constrained requests and validates the result."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        effort: str = DEFAULT_EFFORT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        api_key: str | None = None,
    ) -> None:
        # No api_key argument means the SDK resolves ANTHROPIC_API_KEY,
        # ANTHROPIC_AUTH_TOKEN, or an `ant auth login` profile in that order.
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.usage: List[Dict[str, int]] = []

    def structured(
        self,
        *,
        schema_model: Type[T],
        system: str,
        content: Sequence[Dict[str, Any]] | str,
        label: str = "request",
    ) -> T:
        """One request, one validated model instance.

        Streams so that a large `max_tokens` cannot trip the HTTP timeout.
        """
        user_content = [{"type": "text", "text": content}] if isinstance(content, str) else list(content)

        try:
            with self.client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_content}],
                output_config={
                    "effort": self.effort,
                    "format": {
                        "type": "json_schema",
                        "schema": to_strict_schema(schema_model),
                    },
                },
            ) as stream:
                response = stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise LLMError(AUTH_HELP) from exc
        except TypeError as exc:
            # The SDK raises a bare TypeError when no credential source resolves.
            if "authentication method" in str(exc):
                raise LLMError(AUTH_HELP) from exc
            raise
        except anthropic.RateLimitError as exc:
            raise LLMError(f"Rate limited during {label}: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"API error during {label} ({exc.status_code}): {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"Network error during {label}: {exc}") from exc

        if response.usage is not None:
            self.usage.append(
                {
                    "stage": label,
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                }
            )

        # `stop_details` is populated only on a refusal, so guard before reading.
        if response.stop_reason == "refusal":
            detail = getattr(response.stop_details, "explanation", "") or ""
            raise LLMError(f"The model declined the {label} request. {detail}".strip())
        if response.stop_reason == "max_tokens":
            raise LLMError(
                f"The {label} response hit the {self.max_tokens}-token cap and was truncated. "
                "Raise --max-tokens or narrow the input."
            )

        text = next((b.text for b in response.content if b.type == "text"), "")
        if not text.strip():
            raise LLMError(f"Empty response for {label}.")

        try:
            return schema_model.model_validate_json(text)
        except ValidationError as exc:
            preview = text[:400]
            raise LLMError(
                f"Response for {label} did not match the expected schema.\n"
                f"{exc}\nFirst 400 chars: {preview}"
            ) from exc

    def usage_summary(self) -> str:
        total_in = sum(u["input_tokens"] for u in self.usage)
        total_out = sum(u["output_tokens"] for u in self.usage)
        return f"{total_in:,} input tokens, {total_out:,} output tokens across {len(self.usage)} calls"


def dump_json(model: BaseModel) -> str:
    return json.dumps(model.model_dump(), indent=2, ensure_ascii=False)
