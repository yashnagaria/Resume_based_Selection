"""Verify the request we send and the response we parse, for both providers,
against a local mock server. No API key and no network required.

Run: python -m tests.test_request_shape
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from resume_pipeline.ingest import BinaryPart, TextPart
from resume_pipeline.llm import LLMError, make_client
from resume_pipeline.models import ResumeProfile

captured: dict = {}
STOP_REASON = "end_turn"

PROFILE_JSON = {
    "full_name": "Priya Ramanathan",
    "email": "priya.ramanathan@example.com",
    "phone": "+91 98765 43210",
    "location": "Bengaluru, India",
    "links": ["github.com/priyar"],
    "headline": "Backend engineer, payments and ledger systems",
    "summary": "Six years building financial infrastructure.",
    "total_experience_years": 6.0,
    "seniority_level": "senior",
    "education": [], "experience": [], "projects": [], "certifications": [],
    "technical_skills": ["Python", "Go"], "soft_skills": [],
    "domains": ["Payments"], "languages": ["English"],
    "notable_strengths": ["Ledger depth"], "gaps_and_concerns": [],
    "extraction_notes": [],
}


def sse(events: list[tuple[str, dict]]) -> bytes:
    out = []
    for name, payload in events:
        out.append(f"event: {name}\ndata: {json.dumps(payload)}\n\n")
    return "".join(out).encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):  # silence the server log
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        captured["path"] = self.path
        captured["body"] = json.loads(body)
        captured["headers"] = dict(self.headers)

        if "generateContent" in self.path:
            self._reply_gemini()
        else:
            self._reply_anthropic()

    def _reply_gemini(self):
        payload = json.dumps({
            "candidates": [{
                "content": {"role": "model", "parts": [{"text": json.dumps(PROFILE_JSON)}]},
                "finishReason": "MAX_TOKENS" if STOP_REASON == "max_tokens" else "STOP",
                "index": 0,
            }],
            "usageMetadata": {
                "promptTokenCount": 1234,
                "candidatesTokenCount": 567,
                "totalTokenCount": 1801,
            },
            "modelVersion": "mock",
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _reply_anthropic(self):
        text = json.dumps(PROFILE_JSON)
        payload = sse([
            ("message_start", {"type": "message_start", "message": {
                "id": "msg_mock", "type": "message", "role": "assistant",
                "model": "claude-opus-5", "content": [],
                "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 1234, "output_tokens": 1},
            }}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": text}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta",
                               "delta": {"stop_reason": STOP_REASON, "stop_sequence": None},
                               "usage": {"output_tokens": 567}}),
            ("message_stop", {"type": "message_stop"}),
        ])
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base_url = f"http://127.0.0.1:{server.server_port}"

from google import genai  # noqa: E402
from google.genai import types as gtypes  # noqa: E402

# ---------------------------------------------------------------- Gemini
print("\nGemini request shape (mock server)")

gclient = make_client(provider="gemini", effort="low", max_tokens=4321, api_key="AIza-mock")
gclient.client = genai.Client(
    api_key="AIza-mock", http_options=gtypes.HttpOptions(base_url=base_url)
)

gprofile = gclient.structured(
    schema_model=ResumeProfile,
    system="You are a resume analyst.",
    content=[TextPart(text="resume text"),
             BinaryPart(data=b"%PDF-1.4 stub", mime_type="application/pdf")],
    label="profile extraction",
)

gbody = captured["body"]
gcfg = gbody.get("generationConfig", {})
check("posts to :generateContent", "generateContent" in captured["path"], captured["path"])
check("sends systemInstruction", "systemInstruction" in gbody)
check("system prompt content sent",
      "resume analyst" in json.dumps(gbody.get("systemInstruction", "")))
check("sets JSON mime type", gcfg.get("responseMimeType") == "application/json")
check("forwards max_output_tokens", gcfg.get("maxOutputTokens") == 4321)
check("maps effort to thinking level",
      "LOW" in json.dumps(gcfg.get("thinkingConfig", {})).upper(),
      json.dumps(gcfg.get("thinkingConfig")))
check("schema covers all fields",
      set(gcfg.get("responseSchema", {}).get("required", [])) == set(ResumeProfile.model_fields))
gparts = gbody["contents"][0]["parts"]
check("sends text + inline pdf",
      "text" in gparts[0] and "inlineData" in gparts[1],
      str([sorted(p) for p in gparts]))
check("parsed into the model", isinstance(gprofile, ResumeProfile))
check("parsed values correct",
      gprofile.full_name == "Priya Ramanathan" and gprofile.total_experience_years == 6.0)
check("usage recorded",
      gclient.usage and gclient.usage[0]["input_tokens"] == 1234
      and gclient.usage[0]["output_tokens"] == 567)
check("usage summary renders", "1,234 input tokens" in gclient.usage_summary(),
      gclient.usage_summary())

STOP_REASON = "max_tokens"
try:
    gclient.structured(schema_model=ResumeProfile, system="s", content="go",
                       label="profile extraction")
except LLMError as exc:
    check("max_tokens truncation raises", "truncated" in str(exc).lower(), str(exc))
else:
    check("max_tokens truncation raises", False, "no error raised")
STOP_REASON = "end_turn"


# ---------------------------------------------------------------- Anthropic
print("\nAnthropic request shape (mock server)")
captured.clear()

client = make_client(provider="anthropic", model="claude-opus-5", effort="xhigh",
                     max_tokens=9999, api_key="sk-ant-mock")
client.client = type(client.client)(api_key="sk-ant-mock", base_url=base_url, max_retries=0)

profile = client.structured(
    schema_model=ResumeProfile,
    system="You are a resume analyst.",
    content=[TextPart(text="Extract this resume.")],
    label="profile extraction",
)

body = captured["body"]
check("posts to /v1/messages", captured["path"].endswith("/v1/messages"), captured["path"])
check("streaming enabled", body.get("stream") is True)
check("model forwarded", body["model"] == "claude-opus-5")
check("max_tokens forwarded", body["max_tokens"] == 9999)
check("system prompt sent", "resume analyst" in json.dumps(body["system"]))

oc = body.get("output_config", {})
check("output_config.effort sent", oc.get("effort") == "xhigh", json.dumps(oc)[:120])
check("output_config.format is json_schema", oc.get("format", {}).get("type") == "json_schema")

schema = oc.get("format", {}).get("schema", {})
check("schema is strict", schema.get("additionalProperties") is False)
check("schema has no $defs", "$defs" not in json.dumps(schema))
check("schema covers all fields",
      set(schema.get("required", [])) == set(ResumeProfile.model_fields))

check("parsed into the model", isinstance(profile, ResumeProfile))
check("parsed values correct",
      profile.full_name == "Priya Ramanathan" and profile.total_experience_years == 6.0)
check("usage recorded",
      client.usage and client.usage[0]["input_tokens"] == 1234
      and client.usage[0]["output_tokens"] == 567)

# Truncation must be reported, not silently returned as partial data.
STOP_REASON = "max_tokens"
try:
    client.structured(schema_model=ResumeProfile, system="s", content="go",
                      label="profile extraction")
except LLMError as exc:
    check("max_tokens truncation raises", "truncated" in str(exc).lower(), str(exc))
else:
    check("max_tokens truncation raises", False, "no error raised")

server.shutdown()

print()
if failures:
    print(f"{len(failures)} FAILED: {', '.join(failures)}")
    sys.exit(1)
print("Request shapes verified for both providers.")
