"""Anthropic (Claude) backend, via the anthropic SDK.

Kept alongside the Gemini backend so the pipeline is not tied to one vendor.
Streams every request so a large `max_tokens` cannot trip the HTTP timeout.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Type, TypeVar

from pydantic import BaseModel, ValidationError

from .ingest import BinaryPart, ContentPart, TextPart
from .llm import LLMError, StructuredClient, to_strict_schema

T = TypeVar("T", bound=BaseModel)

AUTH_HELP = (
    "No Anthropic credentials found.\n"
    "  PowerShell:  $env:ANTHROPIC_API_KEY = 'sk-ant-...'\n"
    "  bash:        export ANTHROPIC_API_KEY=sk-ant-...\n"
    "  Get a key at https://console.anthropic.com/settings/keys"
)


class ClaudeClient(StructuredClient):
    """Issues schema-constrained requests to Claude and validates the result."""

    def __init__(
        self,
        model: str = "claude-opus-5",
        effort: str = "high",
        max_tokens: int = 32000,
        api_key: str | None = None,
    ) -> None:
        super().__init__(model=model, effort=effort, max_tokens=max_tokens)
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise LLMError(
                "The Anthropic backend needs the anthropic package. "
                "Install it with: pip install anthropic"
            ) from exc

        self._anthropic = anthropic
        # No api_key argument means the SDK resolves ANTHROPIC_API_KEY,
        # ANTHROPIC_AUTH_TOKEN, or an `ant auth login` profile in that order.
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    @staticmethod
    def _to_blocks(parts: Sequence[ContentPart]) -> List[Dict[str, Any]]:
        import base64

        blocks: List[Dict[str, Any]] = []
        for part in parts:
            if isinstance(part, TextPart):
                blocks.append({"type": "text", "text": part.text})
            elif isinstance(part, BinaryPart):
                data = base64.standard_b64encode(part.data).decode("utf-8")
                kind = "document" if part.mime_type == "application/pdf" else "image"
                blocks.append(
                    {
                        "type": kind,
                        "source": {
                            "type": "base64",
                            "media_type": part.mime_type,
                            "data": data,
                        },
                    }
                )
            else:  # pragma: no cover - guarded by the ContentPart union
                raise LLMError(f"Unsupported content part: {type(part).__name__}")
        return blocks

    def structured(
        self,
        *,
        schema_model: Type[T],
        system: str,
        content: Sequence[ContentPart] | str,
        label: str = "request",
    ) -> T:
        anthropic = self._anthropic
        blocks = self._to_blocks(self._as_parts(content))

        try:
            with self.client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": blocks}],
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
            self._record_usage(label, response.usage.input_tokens, response.usage.output_tokens)

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
            raise LLMError(
                f"Response for {label} did not match the expected schema.\n"
                f"{exc}\nFirst 400 chars: {text[:400]}"
            ) from exc
