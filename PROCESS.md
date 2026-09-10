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

Three sequential model calls, orchestrated in code:

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

### Adaptive thinking, tunable effort

The model default (`claude-opus-5`) runs adaptive thinking, and `--effort`
exposes the cost/quality dial. Resume screening and interview design are
judgement tasks that repay reasoning, so the default is `high`. Bulk screening
can drop to `low`; a final-round candidate can justify `xhigh`.

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
| `llm.py` | Schema conversion, streaming, retries, error translation | Know what a resume is |
| `ingest.py` | File → content blocks; format validation | Call the API |
| `stages.py` | The three prompts and their schema bindings | Orchestrate or do I/O |
| `pipeline.py` | Stage sequencing and mode branching | Format output |
| `report.py` | Markdown rendering | Call the API |
| `cli.py` | Argument parsing, file output, exit codes | Contain logic |

The practical payoff: prompts can be tuned in `stages.py` without touching
anything else, and the pipeline is usable as a library because all I/O and
argument handling sit in `cli.py`.

---

## 5. Error handling

Failures are converted into messages that tell the user what to do:

- **No credentials** — the SDK raises a bare `TypeError` at request time.
  `llm.py` catches it and prints the exact command to set a key.
- **Truncation** — `stop_reason == "max_tokens"` raises rather than returning
  partial JSON, because silently truncated output is worse than an error.
- **Refusal** — `stop_reason == "refusal"` is checked before reading content
  (`stop_details` is only populated in that case, so it is guarded).
- **Schema mismatch** — a `ValidationError` reports the failing stage and the
  first 400 characters of the response.
- **Bad input** — unreadable, empty, oversized, or unsupported files are
  rejected in `ingest.py` before any tokens are spent.
- **Rate limits and 5xx** — the SDK retries with backoff; what survives that
  is reported with the stage name attached.

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
| `test_request_shape` | The actual HTTP request: streaming on, `output_config` carrying both `effort` and `format`, no `$defs`, correct parsing, usage accounting, truncation detection |
| `test_pipeline_mock` | All three pipeline modes end to end, prompt contents reaching the model, CLI exit codes and written files |

Run with `python -m tests.run_all`.

Two rendering bugs were caught this way and are now regression-tested:
`str.strip(" to")` stripping characters rather than a suffix (turning
"Present" into "Presen"), and unescaped pipes breaking Markdown tables when
model-generated text contained one.

---

## 7. Cost and latency

Three calls per resume. The dominant cost is stage 3, which produces the most
output. Rough shape at `claude-opus-5` ($5/$25 per MTok) for a two-page resume
and 15 questions: single-digit cents per candidate, dominated by output tokens.

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
