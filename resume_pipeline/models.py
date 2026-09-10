"""Pydantic schemas for every stage of the pipeline.

Design note: fields are deliberately non-Optional. Structured outputs are more
reliable when every field is required, so "unknown" is expressed as an empty
string / empty list rather than null. Keep it that way when extending.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

SeniorityLevel = Literal[
    "intern", "entry", "junior", "mid", "senior",
    "staff", "principal", "lead", "manager", "director", "executive", "unclear",
]


# --------------------------------------------------------------------------
# Stage 1 - extraction
# --------------------------------------------------------------------------

class Education(BaseModel):
    degree: str = Field(description="e.g. B.Tech, MSc. Empty string if absent.")
    field_of_study: str
    institution: str
    graduation_year: str = Field(description="Year as written, or empty string.")
    honors: str = Field(description="GPA, distinctions, rank. Empty string if absent.")


class Experience(BaseModel):
    title: str
    company: str
    start_date: str = Field(description="As written on the resume, e.g. Jan 2021.")
    end_date: str = Field(description="As written, or Present.")
    duration_months: int = Field(description="Best estimate; 0 if undeterminable.")
    location: str
    responsibilities: List[str]
    achievements: List[str] = Field(
        description="Quantified outcomes only (numbers, %, scale, revenue, latency)."
    )
    tech_used: List[str]


class Project(BaseModel):
    name: str
    description: str
    tech_used: List[str]
    link: str


class Certification(BaseModel):
    name: str
    issuer: str
    year: str


class ResumeProfile(BaseModel):
    """Everything worth knowing about the candidate, normalised."""

    full_name: str
    email: str
    phone: str
    location: str
    links: List[str] = Field(description="LinkedIn, GitHub, portfolio, etc.")

    headline: str = Field(description="One line describing who this candidate is.")
    summary: str = Field(description="3-5 sentence narrative of their career arc.")

    total_experience_years: float = Field(
        description="Professional experience only. Exclude internships unless that is all there is."
    )
    seniority_level: SeniorityLevel

    education: List[Education]
    experience: List[Experience]
    projects: List[Project]
    certifications: List[Certification]

    technical_skills: List[str] = Field(description="Normalised, de-duplicated.")
    soft_skills: List[str] = Field(description="Only those evidenced by the resume.")
    domains: List[str] = Field(description="Industries / problem domains, e.g. fintech.")
    languages: List[str] = Field(description="Human languages.")

    notable_strengths: List[str]
    gaps_and_concerns: List[str] = Field(
        description="Employment gaps, job hopping, vague or unverifiable claims, "
        "skill/seniority mismatches. Be specific and factual, never speculative "
        "about protected characteristics."
    )
    extraction_notes: List[str] = Field(
        description="Anything unreadable, ambiguous, or inferred rather than stated."
    )


# --------------------------------------------------------------------------
# Stage 2a - open-ended role recommendation (no target role given)
# --------------------------------------------------------------------------

class RoleRecommendation(BaseModel):
    role_title: str
    seniority: str = Field(description="Suggested level for this role, e.g. Senior.")
    fit_score: int = Field(description="0-100 confidence that the candidate fits.")
    why_fit: List[str] = Field(description="Evidence drawn from the resume.")
    gaps: List[str]
    ramp_up_needed: List[str]


class RoleRecommendations(BaseModel):
    candidate_name: str
    overall_positioning: str = Field(description="How this candidate should be pitched.")
    recommendations: List[RoleRecommendation] = Field(
        description="Ranked best-first. 3-5 entries."
    )
    stretch_roles: List[str] = Field(description="Plausible in 12-18 months, not today.")
    poor_fit_roles: List[str] = Field(description="Roles to rule out, with the reason inline.")
    areas_to_probe: List[str] = Field(description="What an interview must resolve.")


# --------------------------------------------------------------------------
# Stage 2b - targeted role fit (a specific role was requested)
# --------------------------------------------------------------------------

class RequirementMatch(BaseModel):
    requirement: str
    evidence: str = Field(description="Quote or cite the resume. Empty string if none.")
    strength: Literal["strong", "moderate", "weak", "absent"]


class RoleFitAssessment(BaseModel):
    candidate_name: str
    target_role: str
    verdict: Literal["strong_fit", "good_fit", "borderline", "not_a_fit"]
    fit_score: int = Field(description="0-100.")
    recommended_seniority: str = Field(
        description="The level they should actually be interviewed at, which may differ "
        "from the level of the target role."
    )
    matched_requirements: List[RequirementMatch]
    missing_requirements: List[str]
    transferable_evidence: List[str] = Field(
        description="Adjacent experience that partly covers a missing requirement."
    )
    risks_and_red_flags: List[str]
    areas_to_probe: List[str] = Field(
        description="The specific claims the interview must verify. Drives stage 3."
    )
    rationale: str = Field(description="2-4 paragraphs justifying the verdict.")


# --------------------------------------------------------------------------
# Stage 3 - interview kit
# --------------------------------------------------------------------------

QuestionCategory = Literal[
    "resume_deep_dive",
    "technical_core",
    "system_design",
    "coding",
    "domain_knowledge",
    "behavioral",
    "situational",
    "culture_motivation",
    "red_flag_probe",
]


class InterviewQuestion(BaseModel):
    id: str = Field(description="Stable short id, e.g. Q1.")
    category: QuestionCategory
    difficulty: Literal["easy", "medium", "hard"]
    question: str = Field(description="Ask verbatim. Reference the candidate's own work.")
    why_this_question: str = Field(
        description="Tie it to a specific resume line or role requirement."
    )
    skill_assessed: str
    what_to_look_for: List[str] = Field(description="Signals of a strong answer.")
    red_flags: List[str] = Field(description="Signals of a weak or fabricated answer.")
    follow_ups: List[str] = Field(description="Probes that go one level deeper.")
    time_minutes: int


class InterviewRound(BaseModel):
    round_name: str
    duration_minutes: int
    objective: str
    question_ids: List[str]


class ScorecardCriterion(BaseModel):
    criterion: str
    weight_percent: int
    what_great_looks_like: str


class InterviewKit(BaseModel):
    candidate_name: str
    role: str
    interview_focus: str = Field(description="The 2-3 things this loop must determine.")
    recommended_structure: List[InterviewRound]
    questions: List[InterviewQuestion]
    scorecard: List[ScorecardCriterion] = Field(description="Weights must sum to 100.")
    closing_notes: str = Field(
        description="Guidance for the interviewer, including hiring-bar advice."
    )


# --------------------------------------------------------------------------
# Full pipeline result
# --------------------------------------------------------------------------

class PipelineResult(BaseModel):
    source_file: str
    mode: Literal["recommend", "assess"]
    profile: ResumeProfile
    recommendations: Optional[RoleRecommendations] = None
    assessment: Optional[RoleFitAssessment] = None
    interview_kit: Optional[InterviewKit] = None
