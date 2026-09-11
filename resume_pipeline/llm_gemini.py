"""Gemini backend, via the google-genai SDK.

Uses `models.generate_content` with a response schema, which the SDK validates
into a Pydantic instance for us (`response.parsed`).
"""

from __future__ import annotations

import os
import random
import time
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


# Gemini capacity fluctuates a lot, and a 503 on one model often clears
# instantly on another. When the chosen model stays unavailable we walk down
# this chain rather than failing the run.
FALLBACK_CHAIN = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
]


def _code_of(exc: Exception | None) -> str:
    return str(getattr(exc, "code", "") or "error")


def _fallbacks_for(model: str) -> list:
    """Models to try after `model`, in order, skipping anything above it."""
    if model in FALLBACK_CHAIN:
        return FALLBACK_CHAIN[FALLBACK_CHAIN.index(model) + 1:]
    # An unrecognised or pinned model gets the conservative tail of the chain.
    return FALLBACK_CHAIN[-2:]


class GeminiClient(StructuredClient):
    """Issues schema-constrained requests to Gemini and validates the result."""

    def __init__(
        self,
        model: str = "gemini-3.7-flash",
        effort: str = "high",
        max_tokens: int = 32000,
        api_key: str | None = None,
        max_retries: int = 3,
        on_retry=None,
        allow_fallback: bool = True,
    ) -> None:
        super().__init__(model=model, effort=effort, max_tokens=max_tokens)
        self.max_retries = max_retries
        self.allow_fallback = allow_fallback
        # Which model actually served the last call - may differ after fallback.
        self.active_model = model
        # Lets the CLI/app surface "busy, retrying" instead of appearing hung.
        self.on_retry = on_retry or (lambda message: None)

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

        # 503 (overloaded) and 429 (rate limit) are transient and common on
        # popular models: retry with backoff, then fall back down the chain.
        candidates = [self.model] + (
            _fallbacks_for(self.model) if self.allow_fallback else []
        )
        last_transient: Exception | None = None
        response = None

        for model_name in candidates:
            for attempt in range(self.max_retries + 1):
                try:
                    response = self.client.models.generate_content(
                        model=model_name,
                        contents=[types.Content(role="user", parts=parts)],
                        config=config,
                    )
                    break
                except errors.ClientError as exc:
                    message = str(exc)
                    code = getattr(exc, "code", None)
                    if "API key" in message or "API_KEY" in message or code in (401, 403):
                        raise LLMError(AUTH_HELP) from exc
                    if code == 429:
                        last_transient = exc
                    else:
                        raise LLMError(f"Request rejected during {label}: {message}") from exc
                except errors.ServerError as exc:
                    last_transient = exc
                except errors.APIError as exc:
                    raise LLMError(f"API error during {label}: {exc}") from exc

                if attempt < self.max_retries:
                    delay = min(1.5 * (2 ** attempt) + random.uniform(0, 1), 20.0)
                    self.on_retry(
                        f"{model_name} busy ({_code_of(last_transient)}); "
                        f"retry {attempt + 1}/{self.max_retries} in {delay:.0f}s"
                    )
                    time.sleep(delay)

            if response is not None:
                if model_name != self.model:
                    self.on_retry(f"Switched to {model_name} ({self.model} unavailable)")
                    self.active_model = model_name
                break
            if model_name != candidates[-1]:
                self.on_retry(f"{model_name} still unavailable; trying the next model")

        if response is None:
            tried = ", ".join(candidates)
            raise LLMError(
                f"No Gemini model was available for {label}. Tried: {tried}.\n"
                f"Last error: {last_transient}\n"
                "Gemini is under heavy load. Wait a few minutes and retry."
            )

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
