"""Orchestration: wires ingest -> extract -> fit -> interview kit together."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from .ingest import load_job_description, load_resume
from .llm import StructuredClient, dump_json
from .models import PipelineResult
from .stages import assess_role_fit, build_interview_kit, extract_profile, recommend_roles


def _noop(_message: str) -> None:
    pass


def run_pipeline(
    resume_path: str | Path,
    *,
    client: StructuredClient,
    target_role: Optional[str] = None,
    jd_path: Optional[str | Path] = None,
    num_questions: int = 15,
    skip_questions: bool = False,
    on_progress: Callable[[str], None] = _noop,
) -> PipelineResult:
    """Run the full pipeline.

    With `target_role`, the candidate is screened against that role. Without it,
    the pipeline recommends roles and then interviews for the top recommendation.
    """
    resume_path = Path(resume_path)

    on_progress(f"Reading {resume_path.name}")
    resume_parts = load_resume(resume_path)
    job_description = load_job_description(jd_path)

    on_progress("Stage 1/3  Extracting candidate profile")
    profile = extract_profile(client, resume_parts)

    result = PipelineResult(
        source_file=str(resume_path),
        mode="assess" if target_role else "recommend",
        profile=profile,
    )

    if target_role:
        on_progress(f"Stage 2/3  Assessing fit for: {target_role}")
        assessment = assess_role_fit(client, profile, target_role, job_description)
        result.assessment = assessment
        interview_role = target_role
        fit_context = dump_json(assessment)
    else:
        on_progress("Stage 2/3  Recommending suitable roles")
        recommendations = recommend_roles(client, profile)
        result.recommendations = recommendations
        if not recommendations.recommendations:
            raise RuntimeError("No role recommendations were produced; cannot build an interview.")
        top = recommendations.recommendations[0]
        interview_role = f"{top.seniority} {top.role_title}".strip()
        fit_context = dump_json(recommendations)

    if skip_questions:
        return result

    on_progress(f"Stage 3/3  Building interview kit for: {interview_role}")
    result.interview_kit = build_interview_kit(
        client,
        profile,
        interview_role,
        fit_context,
        num_questions,
        job_description,
    )
    return result
