"""Render a PipelineResult as a human-readable Markdown report."""

from __future__ import annotations

from typing import List

from .models import (
    InterviewKit,
    PipelineResult,
    ResumeProfile,
    RoleFitAssessment,
    RoleRecommendations,
)

VERDICT_LABEL = {
    "strong_fit": "STRONG FIT - interview with confidence",
    "good_fit": "GOOD FIT - interview, probe the named gaps",
    "borderline": "BORDERLINE - probe the gaps before advancing",
    "not_a_fit": "NOT A FIT - do not proceed",
}

CATEGORY_LABEL = {
    "resume_deep_dive": "Resume deep dive",
    "technical_core": "Core technical",
    "system_design": "System design",
    "coding": "Coding",
    "domain_knowledge": "Domain knowledge",
    "behavioral": "Behavioral",
    "situational": "Situational",
    "culture_motivation": "Motivation & culture",
    "red_flag_probe": "Clarification probe",
}


def _bullets(items: List[str], indent: str = "") -> List[str]:
    return [f"{indent}- {item}" for item in items] if items else [f"{indent}- _none noted_"]


def _cell(text: str) -> str:
    """Make model-generated text safe to drop into a Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _profile_section(p: ResumeProfile) -> List[str]:
    out = [
        "## 1. Candidate profile",
        "",
        f"**{p.full_name or 'Name not found'}** - {p.headline}",
        "",
        f"| | |",
        f"|---|---|",
        f"| Experience | {p.total_experience_years:g} years |",
        f"| Assessed level | {p.seniority_level} |",
        f"| Location | {_cell(p.location) or '-'} |",
        f"| Contact | {_cell(p.email) or '-'} / {_cell(p.phone) or '-'} |",
    ]
    if p.links:
        out.append(f"| Links | {_cell(', '.join(p.links))} |")
    out += ["", p.summary, ""]

    if p.experience:
        out += ["### Experience", ""]
        for job in p.experience:
            out.append(f"**{job.title}** - {job.company}  ")
            # str.strip(" to") would strip characters, not a suffix - join instead.
            dates = " to ".join(x for x in [job.start_date, job.end_date] if x)
            meta = " | ".join(x for x in [dates, job.location] if x)
            if meta:
                out.append(f"_{meta}_")
            out.append("")
            if job.achievements:
                out.append("Achievements:")
                out += _bullets(job.achievements)
                out.append("")
            if job.tech_used:
                out += [f"Tech: {', '.join(job.tech_used)}", ""]

    if p.education:
        out += ["### Education", ""]
        for ed in p.education:
            qualification = " ".join(x for x in [ed.degree, ed.field_of_study] if x)
            line = f"- **{qualification}**"
            if ed.institution:
                line += f" - {ed.institution}"
            extras = ", ".join(x for x in [ed.graduation_year, ed.honors] if x)
            out.append(f"{line} ({extras})" if extras else line)
        out.append("")

    if p.projects:
        out += ["### Projects", ""]
        for pr in p.projects:
            out.append(f"- **{pr.name}** - {pr.description}")
            if pr.tech_used:
                out.append(f"  - Tech: {', '.join(pr.tech_used)}")
            if pr.link:
                out.append(f"  - {pr.link}")
        out.append("")

    if p.certifications:
        out += ["### Certifications", ""]
        out += [
            f"- {c.name}" + (f" - {c.issuer}" if c.issuer else "") + (f" ({c.year})" if c.year else "")
            for c in p.certifications
        ]
        out.append("")

    if any([p.technical_skills, p.soft_skills, p.domains, p.languages]):
        out += ["### Skills", ""]
    if p.technical_skills:
        out += [f"**Technical:** {', '.join(p.technical_skills)}", ""]
    if p.soft_skills:
        out += [f"**Soft:** {', '.join(p.soft_skills)}", ""]
    if p.domains:
        out += [f"**Domains:** {', '.join(p.domains)}", ""]
    if p.languages:
        out += [f"**Languages:** {', '.join(p.languages)}", ""]

    out += ["### Strengths", ""] + _bullets(p.notable_strengths) + [""]
    out += ["### Gaps & things to clarify", ""] + _bullets(p.gaps_and_concerns) + [""]
    if p.extraction_notes:
        out += ["### Extraction notes", ""] + _bullets(p.extraction_notes) + [""]
    return out


def _assessment_section(a: RoleFitAssessment) -> List[str]:
    out = [
        "## 2. Role fit assessment",
        "",
        f"**Target role:** {a.target_role}",
        "",
        f"### Verdict: {VERDICT_LABEL.get(a.verdict, a.verdict)}",
        "",
        f"**Fit score:** {a.fit_score}/100  ",
        f"**Interview at level:** {a.recommended_seniority}",
        "",
        a.rationale,
        "",
    ]
    if a.matched_requirements:
        out += [
            "### Requirement-by-requirement",
            "",
            "| Requirement | Strength | Evidence |",
            "|---|---|---|",
        ]
        for m in a.matched_requirements:
            evidence = _cell(m.evidence) or "_no evidence in resume_"
            out.append(f"| {_cell(m.requirement)} | {m.strength} | {evidence} |")
        out.append("")

    out += ["### Missing requirements", ""] + _bullets(a.missing_requirements) + [""]
    out += ["### Transferable evidence", ""] + _bullets(a.transferable_evidence) + [""]
    out += ["### Risks & red flags", ""] + _bullets(a.risks_and_red_flags) + [""]
    out += ["### Areas the interview must resolve", ""] + _bullets(a.areas_to_probe) + [""]
    return out


def _recommendations_section(r: RoleRecommendations) -> List[str]:
    out = [
        "## 2. Suitable roles",
        "",
        f"**Positioning:** {r.overall_positioning}",
        "",
    ]
    for i, rec in enumerate(r.recommendations, 1):
        out += [
            f"### {i}. {rec.seniority} {rec.role_title} - fit {rec.fit_score}/100",
            "",
            "**Why they fit:**",
        ]
        out += _bullets(rec.why_fit)
        out += ["", "**Gaps:**"] + _bullets(rec.gaps)
        out += ["", "**Ramp-up needed:**"] + _bullets(rec.ramp_up_needed) + [""]

    if r.stretch_roles:
        out += ["### Stretch roles (12-18 months)", ""] + _bullets(r.stretch_roles) + [""]
    if r.poor_fit_roles:
        out += ["### Not a fit", ""] + _bullets(r.poor_fit_roles) + [""]
    out += ["### Areas the interview must resolve", ""] + _bullets(r.areas_to_probe) + [""]
    return out


def _kit_section(k: InterviewKit) -> List[str]:
    out = [
        "## 3. Interview kit",
        "",
        f"**Role:** {k.role}  ",
        f"**Focus:** {k.interview_focus}",
        "",
    ]

    if k.recommended_structure:
        out += ["### Suggested loop", "", "| Round | Duration | Objective | Questions |", "|---|---|---|---|"]
        for rd in k.recommended_structure:
            ids = ", ".join(rd.question_ids)
            out.append(
                f"| {_cell(rd.round_name)} | {rd.duration_minutes} min "
                f"| {_cell(rd.objective)} | {ids} |"
            )
        out.append("")

    by_category: dict[str, list] = {}
    for q in k.questions:
        by_category.setdefault(q.category, []).append(q)

    out += ["### Questions", ""]
    for category, questions in by_category.items():
        out += [f"#### {CATEGORY_LABEL.get(category, category)}", ""]
        for q in questions:
            out += [
                f"**{q.id}. {q.question}**",
                "",
                f"_{q.difficulty} | {q.skill_assessed} | ~{q.time_minutes} min_",
                "",
                f"*Why ask it:* {q.why_this_question}",
                "",
                "*Look for:*",
            ]
            out += _bullets(q.what_to_look_for)
            out += ["", "*Red flags:*"] + _bullets(q.red_flags)
            out += ["", "*Follow-ups:*"] + _bullets(q.follow_ups)
            out += ["", "---", ""]

    if k.scorecard:
        total = sum(c.weight_percent for c in k.scorecard)
        out += ["### Scorecard", "", "| Criterion | Weight | What great looks like |", "|---|---|---|"]
        for c in k.scorecard:
            out.append(
                f"| {_cell(c.criterion)} | {c.weight_percent}% "
                f"| {_cell(c.what_great_looks_like)} |"
            )
        out.append("")
        if total != 100:
            out += [f"> Note: weights sum to {total}%, not 100%. Adjust before using.", ""]

    out += ["### Interviewer notes", "", k.closing_notes, ""]
    return out


def render_markdown(result: PipelineResult) -> str:
    name = result.profile.full_name or "Candidate"
    lines = [
        f"# Interview brief - {name}",
        "",
        f"_Source: {result.source_file}_",
        "",
        "> Generated decision support. Verify every claim against the resume before "
        "acting on it; a human makes the hiring decision.",
        "",
        "---",
        "",
    ]
    lines += _profile_section(result.profile)
    lines += ["---", ""]

    if result.assessment:
        lines += _assessment_section(result.assessment)
        lines += ["---", ""]
    elif result.recommendations:
        lines += _recommendations_section(result.recommendations)
        lines += ["---", ""]

    if result.interview_kit:
        lines += _kit_section(result.interview_kit)

    return "\n".join(lines)
