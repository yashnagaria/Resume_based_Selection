"""Offline checks - no API calls. Run: python -m tests.test_offline"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from resume_pipeline.ingest import IngestError, load_job_description, load_resume
from resume_pipeline.llm import to_strict_schema
from resume_pipeline.models import (
    Certification,
    Education,
    Experience,
    InterviewKit,
    InterviewQuestion,
    InterviewRound,
    PipelineResult,
    Project,
    RequirementMatch,
    ResumeProfile,
    RoleFitAssessment,
    RoleRecommendation,
    RoleRecommendations,
    ScorecardCriterion,
)
from resume_pipeline.report import render_markdown

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


# ---------------------------------------------------------------- schemas
def schema_problems(node, path="root") -> list[str]:
    out: list[str] = []
    if isinstance(node, dict):
        if "$ref" in node:
            out.append(f"{path}: unresolved $ref")
        if "title" in node:
            out.append(f"{path}: leftover title")
        if node.get("type") == "object" and "properties" in node:
            if node.get("additionalProperties") is not False:
                out.append(f"{path}: additionalProperties != false")
            if set(node.get("required", [])) != set(node["properties"]):
                out.append(f"{path}: required/properties mismatch")
            for key, value in node["properties"].items():
                out += schema_problems(value, f"{path}.{key}")
        for key in ("items", "anyOf", "allOf"):
            if key in node:
                out += schema_problems(node[key], f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            out += schema_problems(value, f"{path}[{i}]")
    return out


print("\nSchemas")
for model in (ResumeProfile, RoleFitAssessment, RoleRecommendations, InterviewKit):
    schema = to_strict_schema(model)
    problems = schema_problems(schema)
    check(f"{model.__name__} is strict-valid", not problems, "; ".join(problems))
    check(f"{model.__name__} is JSON-serialisable", bool(json.dumps(schema)))


# ---------------------------------------------------------------- ingest
print("\nIngest")
txt_blocks = load_resume(SAMPLES / "sample_resume.txt")
check("txt -> one text block", len(txt_blocks) == 1 and txt_blocks[0]["type"] == "text")
check("txt content preserved", "Razorflow Payments" in txt_blocks[0]["text"])

tmp = ROOT / "out" / "_scratch"
tmp.mkdir(parents=True, exist_ok=True)

pdf = tmp / "fake.pdf"
pdf.write_bytes(b"%PDF-1.4 minimal stub for ingest shape test")
pdf_blocks = load_resume(pdf)
check(
    "pdf -> base64 document block",
    pdf_blocks[0]["type"] == "document"
    and pdf_blocks[0]["source"]["media_type"] == "application/pdf"
    and isinstance(pdf_blocks[0]["source"]["data"], str),
)

png = tmp / "fake.png"
png.write_bytes(b"\x89PNG\r\n\x1a\n stub")
check("png -> image block", load_resume(png)[0]["type"] == "image")


def expect_error(name: str, fn) -> None:
    try:
        fn()
    except IngestError:
        check(name, True)
    except Exception as exc:  # noqa: BLE001
        check(name, False, f"wrong exception: {type(exc).__name__}: {exc}")
    else:
        check(name, False, "no error raised")


expect_error("missing file rejected", lambda: load_resume(tmp / "nope.pdf"))
(tmp / "empty.txt").write_bytes(b"")
expect_error("empty file rejected", lambda: load_resume(tmp / "empty.txt"))
(tmp / "r.xyz").write_text("x")
expect_error("unsupported extension rejected", lambda: load_resume(tmp / "r.xyz"))
(tmp / "r.doc").write_text("x")
expect_error("legacy .doc rejected", lambda: load_resume(tmp / "r.doc"))
expect_error("pdf job description rejected", lambda: load_job_description(pdf))

check("no job description -> empty string", load_job_description(None) == "")
check(
    "job description read",
    "Payments Platform" in load_job_description(SAMPLES / "sample_jd.txt"),
)


# ---------------------------------------------------------------- report
print("\nReport rendering")

profile = ResumeProfile(
    full_name="Priya Ramanathan",
    email="priya.ramanathan@example.com",
    phone="+91 98765 43210",
    location="Bengaluru, India",
    links=["github.com/priyar"],
    headline="Backend engineer specialising in payments and ledger systems",
    summary="Six years building high-throughput financial infrastructure.",
    total_experience_years=6.0,
    seniority_level="senior",
    education=[Education(
        degree="B.E.", field_of_study="Computer Science",
        institution="College of Engineering, Pune",
        graduation_year="2019", honors="CGPA 8.7/10",
    )],
    experience=[Experience(
        title="Senior Software Engineer", company="Razorflow Payments",
        start_date="Mar 2022", end_date="Present", duration_months=42,
        location="Bengaluru",
        responsibilities=["Owned the double-entry ledger service"],
        achievements=["Cut p99 settlement latency from 1.8s to 240ms"],
        tech_used=["Python", "Go", "Kafka"],
    )],
    projects=[Project(
        name="ledgerlite", description="Embeddable double-entry accounting library",
        tech_used=["Go"], link="github.com/priyar/ledgerlite",
    )],
    certifications=[Certification(
        name="AWS Solutions Architect Associate", issuer="AWS", year="2021",
    )],
    technical_skills=["Python", "Go", "Kafka", "PostgreSQL"],
    soft_skills=["Mentoring"],
    domains=["Payments", "Fintech"],
    languages=["English", "Tamil"],
    notable_strengths=["Deep ledger and settlement expertise"],
    gaps_and_concerns=["No stated experience with compliance audits"],
    extraction_notes=["Internship excluded from the experience total"],
)

assessment = RoleFitAssessment(
    candidate_name="Priya Ramanathan",
    target_role="Senior Backend Engineer - Payments Platform",
    verdict="strong_fit", fit_score=88, recommended_seniority="Senior",
    matched_requirements=[
        RequirementMatch(
            requirement="5+ years backend experience",
            evidence="6 years across Razorflow and Nimbus", strength="strong",
        ),
        RequirementMatch(
            requirement="Compliance | audit exposure", evidence="", strength="absent",
        ),
    ],
    missing_requirements=["Direct compliance/audit collaboration"],
    transferable_evidence=["Reconciliation pipeline work is adjacent to audit"],
    risks_and_red_flags=["Design ownership claims need verification"],
    areas_to_probe=["Depth of the Kafka outbox migration"],
    rationale="Strong evidence across every core requirement.",
)

kit = InterviewKit(
    candidate_name="Priya Ramanathan",
    role="Senior Backend Engineer - Payments Platform",
    interview_focus="Verify ledger design ownership and distributed-systems depth.",
    recommended_structure=[InterviewRound(
        round_name="Technical deep dive", duration_minutes=60,
        objective="Probe the ledger migration", question_ids=["Q1"],
    )],
    questions=[InterviewQuestion(
        id="Q1", category="resume_deep_dive", difficulty="hard",
        question="Walk me through the Kafka outbox migration.",
        why_this_question="Their headline achievement claim.",
        skill_assessed="Distributed systems",
        what_to_look_for=["Names the dual-write problem"],
        red_flags=["Cannot explain failure modes"],
        follow_ups=["How did you handle consumer replay?"],
        time_minutes=15,
    )],
    scorecard=[ScorecardCriterion(
        criterion="Distributed systems depth", weight_percent=100,
        what_great_looks_like="Reasons precisely about consistency tradeoffs",
    )],
    closing_notes="Hire if distributed-systems depth is confirmed.",
)

assess_result = PipelineResult(
    source_file="samples/sample_resume.txt", mode="assess",
    profile=profile, assessment=assessment, interview_kit=kit,
)
md = render_markdown(assess_result)
check("assess report renders", len(md) > 800)
for token in ("Interview brief", "Priya Ramanathan", "STRONG FIT", "Interview kit", "Q1", "Scorecard"):
    check(f"assess report contains {token!r}", token in md)
check("pipe in table cell escaped", "Compliance \\| audit exposure" in md)
check("absent-evidence placeholder shown", "_no evidence in resume_" in md)
# Regression: str.strip(" to") used to eat the trailing 't' of "Present".
check("end date not mangled", "Mar 2022 to Present" in md and "Presen |" not in md)
check("education line well formed", "**B.E. Computer Science** - College of Engineering, Pune" in md)

sparse = profile.model_copy(deep=True)
sparse.experience[0].start_date = "Mar 2022"
sparse.experience[0].end_date = ""
sparse.experience[0].location = ""
sparse.education[0].degree = ""
sparse.education[0].graduation_year = ""
sparse.education[0].honors = ""
sparse_md = render_markdown(assess_result.model_copy(update={"profile": sparse}))
check("open-ended date renders", "_Mar 2022_" in sparse_md, "missing bare start date")
edu_block = sparse_md.split("### Education")[1][:200]
check("missing degree leaves no empty bold marker",
      "****" not in edu_block and "- ** " not in edu_block, repr(edu_block[:80]))

recommend_result = PipelineResult(
    source_file="samples/sample_resume.txt", mode="recommend", profile=profile,
    recommendations=RoleRecommendations(
        candidate_name="Priya Ramanathan",
        overall_positioning="A payments infrastructure specialist.",
        recommendations=[RoleRecommendation(
            role_title="Backend Engineer (Payments)", seniority="Senior", fit_score=90,
            why_fit=["Owned a 40M txn/month ledger"], gaps=["No compliance exposure"],
            ramp_up_needed=["Domain onboarding"],
        )],
        stretch_roles=["Engineering Manager"],
        poor_fit_roles=["Frontend Engineer - no UI work evidenced"],
        areas_to_probe=["Depth of design ownership"],
    ),
    interview_kit=kit,
)
md2 = render_markdown(recommend_result)
check("recommend report renders", "Suitable roles" in md2 and "Stretch roles" in md2)

profile_only = PipelineResult(source_file="x.txt", mode="recommend", profile=profile)
check("profile-only report renders", "Candidate profile" in render_markdown(profile_only))

scorecard_bad = kit.model_copy(deep=True)
scorecard_bad.scorecard[0].weight_percent = 70
bad_result = assess_result.model_copy(update={"interview_kit": scorecard_bad})
check("bad scorecard weights flagged", "weights sum to 70%" in render_markdown(bad_result))

check("result round-trips through JSON", bool(json.dumps(assess_result.model_dump())))


# ---------------------------------------------------------------- CLI
print("\nCLI")
from resume_pipeline.cli import build_parser

parser = build_parser()
args = parser.parse_args(["r.pdf", "--role", "Backend Engineer", "-n", "20"])
check("cli parses role and question count", args.role == "Backend Engineer" and args.questions == 20)
defaults = parser.parse_args(["r.pdf"])
check("cli defaults", defaults.role is None and defaults.questions == 15 and defaults.out == "out")


print()
if failures:
    print(f"{len(failures)} FAILED: {', '.join(failures)}")
    sys.exit(1)
print("All offline checks passed.")
