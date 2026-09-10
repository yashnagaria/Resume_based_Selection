"""End-to-end pipeline and CLI runs against the mock server, exercising both
modes plus report rendering and file output. No API key required.

Run: python -m tests.test_pipeline_mock
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from resume_pipeline.cli import main as cli_main
from resume_pipeline.llm import ClaudeClient
from resume_pipeline.pipeline import run_pipeline
from resume_pipeline.report import render_markdown
from tests.mock_server import MockHandler, start_mock_server

ROOT = Path(__file__).resolve().parent.parent
RESUME = ROOT / "samples" / "sample_resume.txt"
JD = ROOT / "samples" / "sample_jd.txt"

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


server, base_url = start_mock_server()


def make_client() -> ClaudeClient:
    client = ClaudeClient(api_key="sk-ant-mock")
    client.client = type(client.client)(
        api_key="sk-ant-mock", base_url=base_url, max_retries=0
    )
    return client


print("\nAssess mode (target role supplied)")
steps: list[str] = []
client = make_client()
result = run_pipeline(
    RESUME, client=client, target_role="Senior Backend Engineer",
    jd_path=JD, num_questions=12, on_progress=steps.append,
)
check("three model calls made", len(client.usage) == 3, str(len(client.usage)))
check("mode is assess", result.mode == "assess")
check("profile populated", result.profile.full_name == "Priya Ramanathan")
check("assessment populated", result.assessment is not None and result.assessment.fit_score == 88)
check("recommendations skipped", result.recommendations is None)
check("interview kit populated", result.interview_kit is not None)
check("progress reported for 3 stages", sum("Stage" in s for s in steps) == 3, str(steps))

sent = json.dumps(MockHandler.requests)
check("job description reached the model", "Payments Platform" in sent)
check("resume text reached the model", "Razorflow Payments" in sent)
check("question count reached the prompt", "exactly 12 questions" in sent)
check("fairness rules present in every call",
      all("protected characteristic" in json.dumps(r["system"]) for r in MockHandler.requests))

md = render_markdown(result)
check("assess report has all sections",
      all(s in md for s in ("Candidate profile", "Role fit assessment", "Interview kit")))

print("\nRecommend mode (no target role)")
client2 = make_client()
result2 = run_pipeline(RESUME, client=client2, num_questions=12)
check("three model calls made", len(client2.usage) == 3, str(len(client2.usage)))
check("mode is recommend", result2.mode == "recommend")
check("recommendations populated", result2.recommendations is not None)
check("assessment skipped", result2.assessment is None)
check("interview kit built for top role", result2.interview_kit is not None)
md2 = render_markdown(result2)
check("recommend report has all sections",
      all(s in md2 for s in ("Candidate profile", "Suitable roles", "Interview kit")))

print("\nSkip-questions mode")
client3 = make_client()
result3 = run_pipeline(RESUME, client=client3, target_role="Senior Backend Engineer",
                       skip_questions=True)
check("two model calls made", len(client3.usage) == 2, str(len(client3.usage)))
check("interview kit omitted", result3.interview_kit is None)
check("report still renders", "Role fit assessment" in render_markdown(result3))

print("\nCLI (writes report + JSON)")
out_dir = ROOT / "out" / "_clitest"
import os
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-mock"
os.environ["ANTHROPIC_BASE_URL"] = base_url

code = cli_main([str(RESUME), "--role", "Senior Backend Engineer",
                 "--jd", str(JD), "-n", "12", "--out", str(out_dir), "--quiet"])
check("cli exits 0", code == 0, str(code))
md_file = out_dir / "sample_resume.md"
json_file = out_dir / "sample_resume.json"
check("markdown report written", md_file.is_file() and md_file.stat().st_size > 500)
check("json written", json_file.is_file())
if json_file.is_file():
    data = json.loads(json_file.read_text(encoding="utf-8"))
    check("json has all stages",
          data["mode"] == "assess" and data["profile"]["full_name"] == "Priya Ramanathan"
          and data["assessment"]["fit_score"] == 88 and data["interview_kit"] is not None)

bad = cli_main([str(RESUME), "-n", "0", "--out", str(out_dir), "--quiet"])
check("cli rejects --questions 0", bad == 2, str(bad))

missing = cli_main(["does_not_exist.pdf", "--out", str(out_dir), "--quiet"])
check("cli reports a missing resume cleanly", missing == 1, str(missing))

server.shutdown()

print()
if failures:
    print(f"{len(failures)} FAILED: {', '.join(failures)}")
    sys.exit(1)
print("Pipeline and CLI verified end to end.")
