# Design & Process

How this system is built, and why each decision was made that way.

---

## 1. The problem

Interview preparation is where hiring quality is usually won or lost. A
recruiter has a resume and a role, and needs three things before the call:

1. An accurate picture of the candidate — not a keyword match.
2. A judgement about fit, with reasoning they can check.
3. Questions that probe *this* candidate's actual claims, rather than a
   generic list pulled off the internet.

The third is the part almost nobody does well, because it takes 30–60 minutes
of careful reading per candidate. This pipeline does the reading and turns it
into an interview plan, while keeping the human in the decision.

---

## 2. Architecture

Three sequential model calls, orchestrated in code. The provider is swappable;
nothing below the client boundary knows which one is in use:

```
        resume file (.pdf/.docx/.txt/image)
                    │
              ┌─────▼─────┐
              │  ingest   │  file → API content blocks
              └─────┬─────┘
                    │
         ╔══════════▼══════════╗
         ║ STAGE 1  Extract    ║  → ResumeProfile
         ╚══════════╤══════════╝
                    │
       ┌────────────┴────────────┐
   role given?                no role?
       │                         │
 ╔═════▼═════════╗       ╔═══════▼════════╗
 ║ STAGE 2b      ║       ║ STAGE 2a       ║
 ║ Assess fit    ║       ║ Recommend      ║
 ║ (+ optional   ║       ║ roles          ║
 ║   job desc)   ║       ║                ║
 ╚═════╤═════════╝       ╚═══════╤════════╝
   RoleFitAssessment      RoleRecommendations
       └────────────┬────────────┘
                    │
         ╔══════════▼══════════╗
         ║ STAGE 3  Interview  ║  → InterviewKit
         ╚══════════╤══════════╝
                    │
        ┌───────────┴───────────┐
   report.md                result.json
```

### Why a workflow, not an agent

The steps are known in advance and always run in the same order. There is
nothing for a model to decide about control flow, so an agent loop would add
cost, latency and nondeterminism while buying nothing. Code owns the
orchestration; the model does the judgement work inside each step.

### Why three calls instead of one

A single "read this resume and produce an interview" prompt performs
noticeably worse, for reasons that compound:

- **Attention splitting.** Extraction is a fidelity task; assessment is a
  judgement task; question design is a creative task. Asked together, the
  model does all three at partial depth.
- **Error propagation is invisible.** If a one-shot call misreads the
  experience, the questions are wrong and nothing shows you where it went
  astray. Here, stage 1's output is inspectable, so a bad brief can be
  traced to the step that caused it.
- **Each stage gets a clean, structured input.** Stages 2 and 3 read a
  normalised JSON profile rather than raw resume text, so they reason about
  facts rather than re-parsing layout.
- **Reusable intermediate results.** The same profile can be assessed against
  five different roles without re-reading the resume.

The tradeoff is three round trips instead of one. For a task measured in
minutes of saved human effort, that is the right trade.

### Stage 2 branches by intent

The user's question is genuinely different in each mode, so the prompt and the
output schema are too:

- **No role given** → "what is this person good for?" Produces ranked
  recommendations, stretch roles, and roles to rule out.
- **Role given** → "should we interview them for *this*?" Produces a verdict
  and a requirement-by-requirement breakdown.

Either way, stage 2 emits `areas_to_probe`, which is the contract that stage 3
consumes. That field is what makes the questions follow from the assessment
instead of being generated independently of it.

---

## 3. Key design decisions

### Structured outputs everywhere

Every stage is constrained by a JSON schema derived from a Pydantic model, and
the response is validated before it moves on. This means a malformed response
fails loudly at the stage that produced it, rather than surfacing as a missing
section in the final report.

`llm.to_strict_schema()` converts a Pydantic model into a schema the API
accepts. Pydantic emits `$defs` and `$ref` for nested models; the converter
inlines them, drops `title`/`default`, sets `additionalProperties: false`, and
marks every property required.

**Why every field is required:** optional fields make structured output less
reliable — the model has a choice to make on every field, and quietly omits
things. Instead, "unknown" is expressed in the value: an empty string or an
empty list. The schema stays rigid and the absence is still representable.
`models.py` documents this so the convention survives future edits.

### PDFs go to the API, not through a parser

`ingest.py` base64-encodes PDFs and images into native `document` / `image`
content blocks rather than extracting text locally. Resumes are visually
structured — two-column layouts, sidebars, skill tables — and text extractors
routinely interleave columns into nonsense. The model reads the rendered page.
`.docx` is the exception: there is no native block for it, so `python-docx`
extracts paragraphs and table cells.

### Streaming on every call

All requests use `client.messages.stream(...).get_final_message()`. With
`max_tokens` at 32,000, a non-streaming request risks an HTTP timeout on the
long stage-3 generation. Streaming removes that failure mode; the code still
waits for the complete message, so nothing else changes.

### Provider-neutral by construction

The pipeline runs on Gemini by default and Anthropic on request, because only
one thing differs between them: how a schema-constrained request is put on the
wire. That lives behind `StructuredClient`, an abstract base with a single
method (`structured`) that returns a validated Pydantic instance. `models.py`,
`stages.py`, `pipeline.py` and `report.py` never learn which vendor answered.

Two pieces make this work:

- **`ingest.py` emits neutral parts.** `TextPart` and `BinaryPart` (raw bytes
  plus a MIME type) carry the resume; each backend converts them at the edge -
  Gemini to `types.Part.from_bytes`, Anthropic to base64 `document`/`image`
  blocks. Without this, file handling would fork per provider.
- **`make_client(provider, ...)` is the only construction point.** The CLI and
  the Streamlit app both call it, so adding a third provider means adding one
  module and one dictionary entry.

The two backends do differ in how the schema is expressed. Gemini takes the
Pydantic class directly as `response_schema` and validates for us
(`response.parsed`); Anthropic needs the strict JSON Schema that
`to_strict_schema()` produces. Both paths still end at
`model_validate_json`, so a malformed response fails the same way on either.

### Supplying today's date

Stage 1 is given the current date, because "Present" as an end date is
uncomputable without it. Left to itself the model guessed, and understated a
candidate's experience by two and a half years. The prompt also asks it to show
its arithmetic in `extraction_notes`, which turns an unverifiable number into a
checkable one - and in practice it now flags when its own calculation disagrees
with the summary line the candidate wrote.

### Effort, and how it maps per provider

`--effort` is the cost/quality dial. Resume screening and interview design are
judgement tasks that repay reasoning, so the default is `high`; bulk screening
can drop to `low`.

The pipeline's scale is finer than Gemini's four thinking levels, so
`llm_gemini.EFFORT_TO_THINKING_LEVEL` collapses the top three onto `HIGH`
rather than silently dropping the setting. Anthropic passes `effort` through
as-is. The mapping is a named constant precisely because it is lossy - it
should be obvious and editable, not buried in a call.

### Fairness rules stated once

`stages.FAIRNESS_RULES` is a single constant injected into all three system
prompts. Duplicating the rules per prompt would let them drift, and drift in
this particular text is a correctness problem, not a style problem. The rules
require evidence-grounded claims, forbid inferring protected characteristics
or using proxies for them, forbid inventing facts, treat career breaks
neutrally, and frame the output as decision support for a human.

The prompts also enforce *checkability*: assessments must cite evidence,
`RequirementMatch` includes unmet requirements at strength `absent` rather than
only listing wins, and inferences are recorded in `extraction_notes`. An
interviewer can hold the brief next to the resume and verify it.

### Questions must be personalised

The stage-3 prompt's central instruction is that a question which could be
asked of any candidate for the role has failed. Each question carries
`why_this_question` tying it to a specific resume line, `what_to_look_for` as
observable answer content rather than vague praise, and `follow_ups` that go a
level deeper — which is how an interviewer distinguishes lived experience from
a rehearsed narrative.

Concerns from stage 1 and unverified claims from stage 2 become
`red_flag_probe` questions, phrased neutrally as an invitation to explain.

---

## 4. Module responsibilities

| Module | Owns | Deliberately does not |
|---|---|---|
| `models.py` | The data contract between stages | Any prompt or API detail |
| `llm.py` | Schema conversion, the client factory, `.env` loading | Know any provider's wire format |
| `llm_gemini.py` | The Gemini request/response shape | Orchestrate |
| `llm_anthropic.py` | The Anthropic request/response shape | Orchestrate |
| `ingest.py` | File → neutral content parts; format validation | Call the API |
| `stages.py` | The three prompts and their schema bindings | Orchestrate or do I/O |
| `pipeline.py` | Stage sequencing and mode branching | Format output |
| `report.py` | Markdown rendering | Call the API |
| `cli.py` | Argument parsing, file output, exit codes | Contain logic |
| `app.py` | Streamlit UI: widgets, session state, rendering | Contain pipeline logic |

The practical payoff: prompts can be tuned in `stages.py` without touching
anything else, and the pipeline is usable as a library because all I/O and
argument handling sit in `cli.py`.

---

## 5. Error handling

Failures are converted into messages that tell the user what to do:

- **No credentials** — each backend checks at construction and prints the exact
  command to set that provider's key. (Anthropic's SDK raises a bare
  `TypeError` at request time, which `llm_anthropic.py` translates.)
- **Truncation** — `stop_reason == "max_tokens"` raises rather than returning
  partial JSON, because silently truncated output is worse than an error.
- **Blocked output** — Gemini's `finish_reason` (`SAFETY`, `PROHIBITED_CONTENT`,
  `RECITATION`, …) and Anthropic's `stop_reason == "refusal"` both become a
  named error rather than an empty result. A response with no candidates at all
  reports the prompt feedback.
- **Schema mismatch** — a `ValidationError` reports the failing stage and the
  first 400 characters of the response.
- **Bad input** — unreadable, empty, oversized, or unsupported files are
  rejected in `ingest.py` before any tokens are spent.
- **Rate limits and 5xx** — Gemini returns `503 UNAVAILABLE` under load far
  more often than a hosted API usually does, so `llm_gemini.py` retries with
  exponential backoff and then walks down `FALLBACK_CHAIN` to a less loaded
  model, reporting each switch through `on_retry` rather than appearing hung.
  This was added after a real run failed five straight attempts on the default
  model; the same run then completed by falling back one generation.

Transport errors are caught per-type rather than as one broad class, so
retryable failures stay distinguishable from permanent ones.

---

## 6. Testing

Everything is verified offline against a mock Anthropic server
(`tests/mock_server.py`) that matches each request to a canned response by the
field set of its schema and replies with a well-formed SSE stream. No API key,
no network, no cost — so the suite runs in CI.

| Suite | Verifies |
|---|---|
| `test_offline` | Schema conversion is strict and ref-free; ingestion of every format and every rejection path; report rendering across all modes and sparse data |
| `test_request_shape` | The actual HTTP request for **both** providers: Gemini's `systemInstruction` / `responseSchema` / inline PDF part and Anthropic's streaming `output_config`, plus parsing, usage accounting and truncation detection on each |
| `test_pipeline_mock` | All three pipeline modes end to end, prompt contents reaching the model, CLI exit codes and written files. Runs against Gemini by default; `TEST_PROVIDER=anthropic` runs the same suite through the other backend |
| `test_app` | The Streamlit frontend, run for real via `AppTest` — initial render, empty-input warnings, and the full results view with all three tabs |

Run with `python -m tests.run_all`.

Two rendering bugs were caught this way and are now regression-tested:
`str.strip(" to")` stripping characters rather than a suffix (turning
"Present" into "Presen"), and unescaped pipes breaking Markdown tables when
model-generated text contained one.

---

## 7. Cost and latency

Three calls per resume. The dominant cost is stage 3, which produces the most
output. For a two-page resume and 15 questions expect single-digit cents per
candidate on a Flash-tier Gemini model, dominated by output tokens; an
Opus-tier Anthropic model costs meaningfully more per candidate.

Levers, cheapest first:

- `--skip-questions` for bulk screening — drops stage 3 entirely.
- `--effort low` or `medium` for the first-pass filter.
- `-n 8` instead of 15 — output tokens scale with question count.
- Reuse the stage-1 profile across several roles when using the library API.

---

## 8. Extending it

- **Different questions** — edit `build_interview_kit` in `stages.py`. To add a
  question type, extend the `QuestionCategory` literal and `CATEGORY_LABEL` in
  `report.py`.
- **Extra profile fields** — add to `ResumeProfile` with a `description`; the
  schema, the prompt contract and the JSON output all follow automatically.
  Then render it in `report.py`.
- **Batch processing** — call `run_pipeline` in a loop, or use the Batch API
  for 50% off when latency does not matter.
- **A different output format** — `report.py` is pure rendering over
  `PipelineResult`; an HTML or PDF renderer is a sibling module.
- **Scoring calibration** — the score bands are defined in the stage-2 prompts.
  Tune them there against your own hiring bar; that is the file to change.
