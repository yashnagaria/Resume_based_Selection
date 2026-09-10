"""Command-line entry point.

    python -m resume_pipeline.cli resume.pdf
    python -m resume_pipeline.cli resume.pdf --role "Senior Backend Engineer"
    python -m resume_pipeline.cli resume.pdf --role "ML Engineer" --jd jd.txt
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .ingest import IngestError
from .llm import DEFAULT_EFFORT, DEFAULT_MAX_TOKENS, DEFAULT_MODEL, ClaudeClient, LLMError
from .models import PipelineResult
from .pipeline import run_pipeline
from .report import render_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="resume-pipeline",
        description="Extract a resume, judge role fit, and generate a tailored interview kit.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("resume", help="Path to the resume (.pdf, .docx, .txt, .md, or an image).")
    parser.add_argument(
        "--role", "-r",
        help="Target role to screen against. Omit to have suitable roles recommended instead.",
    )
    parser.add_argument("--jd", help="Optional job-description file (.txt/.md/.docx).")
    parser.add_argument(
        "--questions", "-n", type=int, default=15,
        help="Number of interview questions to generate (default: 15).",
    )
    parser.add_argument(
        "--out", "-o", default="out",
        help="Directory for the generated report and JSON (default: ./out).",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Model id (default: {DEFAULT_MODEL}).")
    parser.add_argument(
        "--effort", default=DEFAULT_EFFORT,
        choices=["low", "medium", "high", "xhigh", "max"],
        help=f"Reasoning effort (default: {DEFAULT_EFFORT}).",
    )
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS, help="Per-call output cap.")
    parser.add_argument(
        "--skip-questions", action="store_true",
        help="Stop after the fit assessment; do not generate interview questions.",
    )
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress progress output.")
    return parser


def _print_summary(result: PipelineResult) -> None:
    p = result.profile
    print()
    print(f"  Candidate : {p.full_name or 'unknown'} - {p.headline}")
    print(f"  Experience: {p.total_experience_years:g} yrs ({p.seniority_level})")

    if result.assessment:
        a = result.assessment
        print(f"  Verdict   : {a.verdict.replace('_', ' ').upper()} for {a.target_role} ({a.fit_score}/100)")
        print(f"  Interview at: {a.recommended_seniority}")
    elif result.recommendations:
        print("  Best-fit roles:")
        for rec in result.recommendations.recommendations[:3]:
            print(f"    - {rec.seniority} {rec.role_title} ({rec.fit_score}/100)")

    if result.interview_kit:
        print(f"  Questions : {len(result.interview_kit.questions)} across "
              f"{len(result.interview_kit.recommended_structure)} rounds")
    print()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.questions < 1:
        print("error: --questions must be at least 1", file=sys.stderr)
        return 2

    def progress(message: str) -> None:
        if not args.quiet:
            print(f"  {message}", flush=True)

    client = ClaudeClient(model=args.model, effort=args.effort, max_tokens=args.max_tokens)

    try:
        result = run_pipeline(
            args.resume,
            client=client,
            target_role=args.role,
            jd_path=args.jd,
            num_questions=args.questions,
            skip_questions=args.skip_questions,
            on_progress=progress,
        )
    except IngestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(args.resume).stem

    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    json_path.write_text(
        json.dumps(result.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    md_path.write_text(render_markdown(result), encoding="utf-8")

    if not args.quiet:
        _print_summary(result)
        print(f"  Report: {md_path}")
        print(f"  Data  : {json_path}")
        print(f"  Usage : {client.usage_summary()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
