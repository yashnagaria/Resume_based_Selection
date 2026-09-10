"""The three pipeline stages: extract -> assess fit -> build the interview kit.

Each stage is one schema-constrained model call. Prompts live here so they can
be tuned without touching orchestration.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .llm import ClaudeClient, dump_json
from .models import (
    InterviewKit,
    ResumeProfile,
    RoleFitAssessment,
    RoleRecommendations,
)

# Applies to every stage. Screening resumes is a consequential decision, so the
# guardrails are stated once and reused rather than restated per prompt.
FAIRNESS_RULES = """
Ground rules that apply to everything you produce:
- Judge only on evidence present in the resume. Never infer or comment on age,
  gender, race, nationality, religion, disability, marital or family status,
  or any other protected characteristic, and never use a person's name, photo,
  country, or the prestige of their school or employer as a proxy for one.
- Distinguish what the resume states from what you inferred. If you inferred
  it, say so.
- Do not invent experience, dates, employers, or numbers. If something is
  missing, record it as missing rather than filling the gap.
- A career break is a fact to ask about, not a defect. Flag it neutrally.
- Your output is decision support for a human interviewer, not a hiring
  decision. Write it so a human can check your reasoning against the resume.
"""


def extract_profile(client: ClaudeClient, resume_blocks: List[Dict[str, Any]]) -> ResumeProfile:
    """Stage 1: read the resume and normalise it into a structured profile."""
    system = f"""You are an expert technical recruiter and resume analyst.

Read the attached resume and extract a complete, faithful, structured profile.

{FAIRNESS_RULES}

Extraction guidance:
- Normalise skill names to their common form (JS -> JavaScript, k8s -> Kubernetes)
  and de-duplicate, but never add a skill the resume does not support.
- Separate responsibilities ("what they were assigned") from achievements
  ("what measurably changed"). Only quantified outcomes belong in achievements.
- Compute total_experience_years from actual employment dates, handling
  overlapping roles without double counting. Exclude internships unless
  internships are the candidate's only experience.
- Infer seniority_level from scope, ownership and impact - not from job title
  alone, since titles inflate differently across companies.
- In gaps_and_concerns, record concrete observations an interviewer should
  follow up: unexplained date gaps, very short tenures, responsibilities with
  no supporting outcome, buzzword-dense claims with no substance, a skills list
  that is not reflected anywhere in the work history.
- In extraction_notes, record anything illegible, ambiguous, or that you
  inferred rather than read directly."""

    content = list(resume_blocks) + [
        {"type": "text", "text": "Extract the structured profile from this resume."}
    ]
    return client.structured(
        schema_model=ResumeProfile,
        system=system,
        content=content,
        label="profile extraction",
    )


def recommend_roles(client: ClaudeClient, profile: ResumeProfile) -> RoleRecommendations:
    """Stage 2a: no target role given - propose the roles this candidate fits."""
    system = f"""You are a senior technical hiring manager advising on where a
candidate should be placed.

Given a structured candidate profile, identify the roles they are genuinely
suited for right now, ranked best-first.

{FAIRNESS_RULES}

Guidance:
- Recommend real, hireable job titles at a specific level (e.g. "Senior Backend
  Engineer (Python/Distributed Systems)"), not broad categories like "Software
  Engineer".
- Every why_fit bullet must point at concrete evidence in the profile. If you
  cannot cite evidence, it is not a reason.
- fit_score is calibrated: 85+ means they would clear the bar at a strong
  company today; 70-84 means a solid interview candidate with real gaps;
  below 60 means do not put them through this loop.
- Be honest about gaps. A recommendation with no gaps listed is almost always
  an under-examined one.
- stretch_roles are reachable in 12-18 months with focused growth, not today.
- poor_fit_roles are adjacent roles a recruiter might wrongly match them to;
  state the reason inline so the recruiter understands the boundary.
- areas_to_probe are the things the profile cannot settle and an interview must."""

    content = f"Candidate profile:\n\n{dump_json(profile)}\n\nRecommend the roles this candidate fits."
    return client.structured(
        schema_model=RoleRecommendations,
        system=system,
        content=content,
        label="role recommendation",
    )


def assess_role_fit(
    client: ClaudeClient,
    profile: ResumeProfile,
    target_role: str,
    job_description: str = "",
) -> RoleFitAssessment:
    """Stage 2b: a specific role was named - decide whether they fit it."""
    system = f"""You are a senior technical hiring manager screening a candidate
against one specific role.

Decide whether this candidate should be interviewed for the target role, and
justify it against the requirements.

{FAIRNESS_RULES}

Guidance:
- If a job description is supplied, derive the requirement list from it. If not,
  derive the industry-standard requirement set for the named role and level, and
  say so in your rationale.
- Assess every meaningful requirement, including the ones the candidate meets
  well. matched_requirements must include entries at strength "absent" where the
  requirement is unmet - an interviewer needs the full picture, not just wins.
- evidence must cite something actually in the profile. If there is none, leave
  evidence empty and mark the strength "absent".
- Weigh demonstrated ability above keyword presence. Someone who built the thing
  outranks someone who listed the thing.
- recommended_seniority is your honest read of the level they would succeed at,
  even when that is below or above the target role's level.
- Verdict calibration: strong_fit = interview with confidence; good_fit =
  interview, with named gaps to probe; borderline = interview only if the
  pipeline is thin, and probe the gaps first; not_a_fit = do not proceed, and
  the rationale must make the blocking gap unmistakable.
- areas_to_probe drives the interview that follows, so make each one a specific,
  checkable claim rather than a broad topic."""

    jd_section = (
        f"\n\nJob description:\n\n{job_description}"
        if job_description
        else "\n\n(No job description supplied - use the standard requirements for this role and level.)"
    )
    content = (
        f"Target role: {target_role}{jd_section}\n\n"
        f"Candidate profile:\n\n{dump_json(profile)}\n\n"
        "Assess this candidate's fit for the target role."
    )
    return client.structured(
        schema_model=RoleFitAssessment,
        system=system,
        content=content,
        label="role fit assessment",
    )


def build_interview_kit(
    client: ClaudeClient,
    profile: ResumeProfile,
    role: str,
    fit_context: str,
    num_questions: int,
    job_description: str = "",
) -> InterviewKit:
    """Stage 3: generate the interview plan for this candidate and role."""
    system = f"""You are a staff-level interviewer designing an interview loop
for one specific candidate and one specific role.

{FAIRNESS_RULES}

Design rules:
- Produce exactly {num_questions} questions.
- Every question must be personalised. A question that could be asked of any
  candidate for this role has failed - anchor it in this candidate's actual
  projects, employers, technologies, and claims.
- Calibrate difficulty to the candidate's assessed level, not to the title.
- Cover a deliberate spread: deep dives into their claimed work, core technical
  skills for the role, design or architecture at a scope matching their level,
  behavioral and situational questions drawn from their real history, and
  motivation. Include coding questions only where the role warrants them.
- Turn each area to probe into at least one question. Unverified claims and
  concerns from the profile become red_flag_probe questions - phrase these
  neutrally and curiously, as an invitation to explain, never as an accusation.
- what_to_look_for must describe observable answer content, not vague praise.
  "Names the consistency tradeoff and why they chose eventual consistency" is
  useful; "shows good understanding" is not.
- follow_ups should push one level deeper than the surface answer, and are how
  the interviewer distinguishes lived experience from rehearsed narrative.
- recommended_structure must group the questions you wrote into realistic rounds
  whose question_ids all exist and whose durations respect each time_minutes.
- scorecard weights must sum to exactly 100 and reflect what actually matters
  for this role.
- closing_notes tells the interviewer where the hiring bar sits and what
  evidence would change the verdict either way."""

    jd_section = f"\n\nJob description:\n\n{job_description}" if job_description else ""
    content = (
        f"Role being interviewed for: {role}{jd_section}\n\n"
        f"Candidate profile:\n\n{dump_json(profile)}\n\n"
        f"Screening assessment:\n\n{fit_context}\n\n"
        f"Design the interview kit."
    )
    return client.structured(
        schema_model=InterviewKit,
        system=system,
        content=content,
        label="interview kit",
    )
