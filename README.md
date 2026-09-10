# Resume → Role Fit → Interview Kit

A pipeline that reads a resume, extracts everything worth knowing from it,
decides which role the candidate suits (or judges them against a role you
name), and generates an interview tailored to that specific person and role.

Built on the Anthropic Claude API.

---

## What it produces

For every resume you feed it, you get two files:

| File | Contents |
|---|---|
| `out/<name>.md` | A readable interview brief for a human interviewer |
| `out/<name>.json` | The same data, structured, for downstream systems |

The brief contains:

1. **Candidate profile** — contact details, normalised experience with
   quantified achievements separated from responsibilities, education,
   projects, skills, strengths, and concerns worth clarifying.
2. **Role fit** — either a ranked list of roles they suit, or a
   requirement-by-requirement verdict against the role you specified.
3. **Interview kit** — personalised questions with the reason each one is
   being asked, what a strong answer looks like, red flags, and follow-up
   probes; plus a suggested round structure and a weighted scorecard.

---

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
```

Set your API key ([get one here](https://console.anthropic.com/settings/keys)):

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."     # PowerShell
```
```bash
export ANTHROPIC_API_KEY=sk-ant-...       # bash
```

---

## Usage

**Which roles does this candidate suit?**

```bash
python -m resume_pipeline.cli samples/sample_resume.txt
```

Recommends 3–5 ranked roles, then builds an interview for the top one.

**Is this candidate right for a specific role?**

```bash
python -m resume_pipeline.cli samples/sample_resume.txt --role "Senior Backend Engineer"
```

**Screen against an actual job description** (most accurate):

```bash
python -m resume_pipeline.cli samples/sample_resume.txt \
    --role "Senior Backend Engineer" \
    --jd samples/sample_jd.txt
```

With a JD, requirements are taken from your posting rather than from the
model's idea of a generic role.

### Options

| Flag | Default | Purpose |
|---|---|---|
| `--role`, `-r` | — | Role to screen against. Omit to get recommendations instead. |
| `--jd` | — | Job description file (`.txt`, `.md`, `.docx`). |
| `--questions`, `-n` | `15` | How many interview questions to generate. |
| `--out`, `-o` | `out` | Output directory. |
| `--model` | `claude-opus-5` | Model id. |
| `--effort` | `high` | `low` \| `medium` \| `high` \| `xhigh` \| `max`. |
| `--max-tokens` | `32000` | Per-call output cap. |
| `--skip-questions` | off | Stop after the fit assessment. |
| `--quiet`, `-q` | off | Suppress progress output. |

Use `--effort low --skip-questions` for cheap bulk screening; `--effort xhigh`
for a final-round candidate.

### Supported resume formats

`.pdf`, `.docx`, `.txt`, `.md`, and images (`.png`, `.jpg`, `.webp`, `.gif`).

PDFs and images are sent to the model directly rather than through a local text
extractor, so multi-column layouts and tables survive. Legacy `.doc` is not
supported — save as `.docx` or `.pdf`.

---

## Using it as a library

```python
from resume_pipeline.llm import ClaudeClient
from resume_pipeline.pipeline import run_pipeline
from resume_pipeline.report import render_markdown

client = ClaudeClient(effort="high")
result = run_pipeline("resume.pdf", client=client, target_role="ML Engineer")

print(result.assessment.verdict)          # "strong_fit"
print(result.assessment.fit_score)        # 88
for q in result.interview_kit.questions:
    print(q.id, q.question)

open("brief.md", "w", encoding="utf-8").write(render_markdown(result))
```

Every stage returns a validated Pydantic model — see
[resume_pipeline/models.py](resume_pipeline/models.py) for the full shape.

---

## Project layout

```
resume_pipeline/
  models.py     Pydantic schemas - the contract between stages
  llm.py        API wrapper: strict-schema calls, streaming, error handling
  ingest.py     Resume file -> API content blocks
  stages.py     The three prompts (extract / assess / question generation)
  pipeline.py   Orchestration
  report.py     Markdown renderer
  cli.py        Command-line entry point
tests/          Offline test suites (mock server, no API key needed)
samples/        A sample resume and job description
```

See [PROCESS.md](PROCESS.md) for why it is built this way.

---

## Tests

Every suite runs against a local mock server — no API key, no network, no cost:

```bash
python -m tests.run_all
```

Covers schema generation, resume ingestion for every supported format, the
exact HTTP request shape, response parsing, all three pipeline modes, report
rendering, and CLI file output.

---

## A note on responsible use

This tool is **decision support, not a hiring decision**. It is built to help
an interviewer prepare, and its output is designed to be checked:

- Assessments must cite evidence from the resume, so you can verify them.
- The model is instructed never to infer or reason about protected
  characteristics, and never to use names, photos, nationality, or institution
  prestige as a proxy for them.
- Unstated information is recorded as missing rather than guessed, and
  anything inferred is flagged in `extraction_notes`.
- Career breaks are surfaced neutrally, as something to ask about.

These are guardrails, not guarantees. Automated resume screening is regulated
in some jurisdictions (NYC Local Law 144 and the EU AI Act among others) — if
you use this to filter candidates rather than to prepare for interviews, check
your obligations first. Always have a human read the resume before rejecting
anyone.
