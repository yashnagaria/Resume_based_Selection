<<<<<<< HEAD
# Resume → Role Fit → Interview Kit

A pipeline that reads a resume, extracts everything worth knowing from it,
decides which role the candidate suits (or judges them against a role you
name), and generates an interview tailored to that specific person and role.

Runs on **Google Gemini** by default, with Anthropic Claude as an alternative provider.

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

### Where to put your Gemini API key

Get one from [Google AI Studio](https://aistudio.google.com/apikey), then set
`GEMINI_API_KEY` in your shell **before** launching:

```powershell
$env:GEMINI_API_KEY = "AIza..."           # PowerShell (this session only)
```
```bash
export GEMINI_API_KEY=AIza...             # bash
```

To persist it on Windows so you do not retype it each time:

```powershell
setx GEMINI_API_KEY "AIza..."             # then open a NEW terminal
```

Three other options, in order of preference:

1. **Paste it into the web app** — if the env var is not set, the sidebar shows
   a password field. Nothing is written to disk.
2. **A `.env` file** in the project root (already gitignored):
   `GEMINI_API_KEY=AIza...`
3. `GOOGLE_API_KEY` also works — the SDK reads either.

Never hardcode the key in a source file, and never commit it.

For `--provider anthropic`, set `ANTHROPIC_API_KEY` instead
([key page](https://console.anthropic.com/settings/keys)).

---

## Usage — web app

```bash
streamlit run app.py
```

Opens at `http://localhost:8501`. Upload a resume in the sidebar, type the role,
press **Analyse resume**. Results appear in three tabs — Profile, Role fit,
Interview kit — with the questions collapsible and filterable by category, and
download buttons for the Markdown brief and the JSON.

If `GEMINI_API_KEY` is set the app picks it up automatically; otherwise there's
a password field in the sidebar. The Provider dropdown switches between Gemini
and Anthropic. Leave the role blank to get role recommendations instead of a
verdict.

### Deploying to Streamlit Community Cloud

1. Push this repo to GitHub (already done if you cloned it from there).
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**, pick
   the repo/branch, and set **Main file path** to `app.py`.
3. Open **Advanced settings → Secrets** (or **Settings → Secrets** after
   deploying) and paste:

   ```toml
   GEMINI_API_KEY = "AIza-your-key-here"
   ```

4. Deploy. `requirements.txt` and `.streamlit/config.toml` are picked up
   automatically.

The app reads the key from Streamlit secrets first, then the environment, then
the sidebar field — so a deployed app uses the operator's configured key and
never needs one typed in.

**Running it locally with secrets instead of env vars:** copy
`.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and fill in your
key. That file is gitignored.

> **A deployed app spends your API quota on every visitor's upload.** Streamlit
> Community Cloud apps are public by default. Either keep the app private, or
> remove the key from secrets so each user supplies their own in the sidebar.

---

## Usage — CLI

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
| `--provider`, `-p` | `gemini` | `gemini` or `anthropic`. |
| `--model` | provider default | `gemini-3.7-flash` / `claude-opus-5`. |
| `--effort` | `high` | `minimal` \| `low` \| `medium` \| `high` \| `xhigh` \| `max`. |
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
from resume_pipeline.llm import make_client
from resume_pipeline.pipeline import run_pipeline
from resume_pipeline.report import render_markdown

client = make_client(provider="gemini", effort="high")
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
app.py          Streamlit frontend
resume_pipeline/
  models.py     Pydantic schemas - the contract between stages
  llm.py        Provider-neutral base: schema conversion, client factory
  llm_gemini.py     Gemini backend (google-genai)
  llm_anthropic.py  Anthropic backend (anthropic)
  ingest.py     Resume file -> provider-neutral content parts
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
rendering, CLI file output, and the Streamlit frontend.

### Model availability

Gemini capacity fluctuates, and a busy model returns `503 UNAVAILABLE`. The
Gemini backend retries with backoff and then walks down a fallback chain
(`gemini-3.8-flash` → `3.7` → `3.6` → `3.5-flash`), reporting each switch, so a
run completes rather than failing. Pin one model with `--model` if you need
reproducibility; note that pinning still allows the tail of the chain as a last
resort.

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
=======
# Resume_based_Selection
>>>>>>> 781f188d5902158008fd5fd838baf7413cf60554
