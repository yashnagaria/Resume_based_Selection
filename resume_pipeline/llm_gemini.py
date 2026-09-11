"""Gemini backend, via the google-genai SDK.

Uses `models.generate_content` with a response schema, which the SDK validates
into a Pydantic instance for us (`response.parsed`).
"""

from __future__ import annotations

import os
from typing import Sequence, Type, TypeVar

from pydantic import BaseModel, ValidationError

from .ingest import BinaryPart, ContentPart, TextPart
from .llm import LLMError, StructuredClient

T = TypeVar("T", bound=BaseModel)

AUTH_HELP = (
    "No Gemini API key found.\n"
    "  PowerShell:  $env:GEMINI_API_KEY = 'AIza...'\n"
    "  bash:        export GEMINI_API_KEY=AIza...\n"
    "  Get a key at https://aistudio.google.com/apikey"
)

# The pipeline's effort scale is finer than Gemini's, so the top three collapse.
EFFORT_TO_THINKING_LEVEL = {
    "minimal": "MINIMAL",
    "low": "LOW",
    "medium": "MEDIUM",
    "high": "HIGH",
    "xhigh": "HIGH",
    "max": "HIGH",
}

# Finish reasons that mean the model stopped for policy rather than completion.
BLOCKED_REASONS = {
    "SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION", "SPII",
}


class GeminiClient(StructuredClient):
    """Issues schema-constrained requests to Gemini and validates the result."""

    def __init__(
        self,
        model: str = "gemini-3.8-flash",
        effort: str = "high",
        max_tokens: int = 32000,
        api_key: str | None = None,
    ) -> None:
        super().__init__(model=model, effort=effort, max_tokens=max_tokens)

        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise LLMError(AUTH_HELP)

        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise LLMError(
                "The Gemini backend needs the google-genai package. "
                "Install it with: pip install google-genai"
            ) from exc

        self._genai = genai
        self.client = genai.Client(api_key=key)

    def _to_sdk_parts(self, parts: Sequence[ContentPart]) -> list:
        from google.genai import types

        out = []
        for part in parts:
            if isinstance(part, TextPart):
                out.append(types.Part.from_text(text=part.text))
            elif isinstance(part, BinaryPart):
                out.append(types.Part.from_bytes(data=part.data, mime_type=part.mime_type))
            else:  # pragma: no cover - guarded by the ContentPart union
                raise LLMError(f"Unsupported content part: {type(part).__name__}")
        return out

    def structured(
        self,
        *,
        schema_model: Type[T],
        system: str,
        content: Sequence[ContentPart] | str,
        label: str = "request",
    ) -> T:
        from google.genai import errors, types

        parts = self._to_sdk_parts(self._as_parts(content))

        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=self.max_tokens,
            response_mime_type="application/json",
            response_schema=schema_model,
            thinking_config=types.ThinkingConfig(
                thinking_level=EFFORT_TO_THINKING_LEVEL.get(self.effort, "HIGH"),
            ),
            # This pipeline declares no tools; disabling AFC silences the SDK's
            # "direct use of automatic function calling" advisory.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=[types.Content(role="user", parts=parts)],
                config=config,
            )
        except errors.ClientError as exc:
            message = str(exc)
            if "API key" in message or "API_KEY" in message or getattr(exc, "code", None) == 401:
                raise LLMError(AUTH_HELP) from exc
            if getattr(exc, "code", None) == 429:
                raise LLMError(f"Rate limited during {label}: {message}") from exc
            raise LLMError(f"Request rejected during {label}: {message}") from exc
        except errors.ServerError as exc:
            raise LLMError(f"Gemini server error during {label}: {exc}") from exc
        except errors.APIError as exc:
            raise LLMError(f"API error during {label}: {exc}") from exc

        usage = response.usage_metadata
        if usage is not None:
            self._record_usage(
                label,
                usage.prompt_token_count or 0,
                (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0),
            )

        self._check_finish_reason(response, label)

        # The SDK validates against response_schema and hands back an instance.
        if isinstance(response.parsed, schema_model):
            return response.parsed

        text = (response.text or "").strip()
        if not text:
            raise LLMError(
                f"Empty response for {label}. The model returned no content - "
                "this usually means the request was blocked or the output cap was hit."
            )
        try:
            return schema_model.model_validate_json(text)
        except ValidationError as exc:
            raise LLMError(
                f"Response for {label} did not match the expected schema.\n"
                f"{exc}\nFirst 400 chars: {text[:400]}"
            ) from exc

    def _check_finish_reason(self, response, label: str) -> None:
        """Turn a non-STOP finish into an actionable error."""
        candidates = response.candidates or []
        if not candidates:
            feedback = getattr(response, "prompt_feedback", None)
            raise LLMError(
                f"Gemini returned no candidates for {label}"
                + (f" (prompt feedback: {feedback})" if feedback else "")
                + ". The input may have been blocked."
            )

        reason = candidates[0].finish_reason
        reason_name = getattr(reason, "name", str(reason)) if reason else ""

        if reason_name == "MAX_TOKENS":
            raise LLMError(
                f"The {label} response hit the {self.max_tokens}-token cap and was "
                "truncated. Raise --max-tokens or narrow the input."
            )
        if reason_name in BLOCKED_REASONS:
            raise LLMError(
                f"Gemini blocked the {label} response (reason: {reason_name}). "
                "If the resume contains unusual content, try a different file."
            )
