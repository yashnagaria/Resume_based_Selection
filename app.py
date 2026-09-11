"""Streamlit frontend for the resume -> role fit -> interview kit pipeline.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import streamlit as st

from resume_pipeline.ingest import IngestError
from resume_pipeline.llm import (
    DEFAULT_EFFORT,
    DEFAULT_MODELS,
    DEFAULT_PROVIDER,
    EFFORT_LEVELS,
    LLMError,
    make_client,
)
from resume_pipeline.models import PipelineResult
from resume_pipeline.pipeline import run_pipeline
from resume_pipeline.report import VERDICT_LABEL, render_markdown

SUPPORTED = ["pdf", "docx", "txt", "md", "png", "jpg", "jpeg", "webp"]

# Env vars each provider's SDK will pick a key up from, most specific first.
KEY_ENV_VARS = {
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
}

VERDICT_COLOR = {
    "strong_fit": "#1a7f37",
    "good_fit": "#1a7f37",
    "borderline": "#9a6700",
    "not_a_fit": "#b42318",
}

st.set_page_config(page_title="Resume to Interview Kit", page_icon="📄", layout="wide")


# --------------------------------------------------------------------------
# Sidebar - inputs
# --------------------------------------------------------------------------

with st.sidebar:
    st.header("Candidate")

    uploaded = st.file_uploader(
        "Resume",
        type=SUPPORTED,
        help="PDF, DOCX, TXT, MD or an image. PDFs are read natively, so "
             "multi-column layouts stay intact.",
    )

    role = st.text_input(
        "Role being interviewed for",
        placeholder="e.g. Senior Backend Engineer",
        help="Leave empty and the pipeline will recommend roles that suit the candidate.",
    )

    jd_text = st.text_area(
        "Job description (optional)",
        height=140,
        placeholder="Paste the posting here for a far more accurate assessment.",
    )

    st.divider()
    st.header("Settings")

    num_questions = st.slider("Interview questions", 5, 30, 15)
    effort = st.select_slider(
        "Reasoning effort",
        options=EFFORT_LEVELS,
        value=DEFAULT_EFFORT,
        help="Higher effort costs more and takes longer. 'high' suits most screening.",
    )
    skip_questions = st.checkbox(
        "Skip questions (fit assessment only)",
        help="Faster and cheaper - useful for bulk screening.",
    )

    provider = st.selectbox(
        "Provider",
        options=sorted(DEFAULT_MODELS),
        index=sorted(DEFAULT_MODELS).index(DEFAULT_PROVIDER),
    )
    model = st.text_input("Model", value=DEFAULT_MODELS[provider])

    key_env = KEY_ENV_VARS[provider]
    env_key = next((os.environ[v] for v in key_env if os.environ.get(v)), "")
    api_key = env_key or st.text_input(
        f"{provider.title()} API key",
        type="password",
        help=f"Not stored. Set {key_env[0]} in your environment to skip this.",
    )
    if env_key:
        st.caption(f"Using {key_env[0]} from the environment.")

    run = st.button("Analyse resume", type="primary", width="stretch")


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

def analyse() -> None:
    """Run the pipeline and stash the result in session state."""
    suffix = Path(uploaded.name).suffix or ".txt"
    tmp_path = None
    jd_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            handle.write(uploaded.getbuffer())
            tmp_path = handle.name

        if jd_text.strip():
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=".txt", mode="w", encoding="utf-8"
            ) as handle:
                handle.write(jd_text)
                jd_path = handle.name

        client = make_client(
            provider=provider, model=model, effort=effort, api_key=api_key or None
        )

        with st.status("Working...", expanded=True) as status:
            result = run_pipeline(
                tmp_path,
                client=client,
                target_role=role.strip() or None,
                jd_path=jd_path,
                num_questions=num_questions,
                skip_questions=skip_questions,
                on_progress=lambda message: status.write(message),
            )
            status.update(label="Done", state="complete", expanded=False)

        st.session_state["result"] = result
        st.session_state["usage"] = client.usage_summary()
        # The uploaded name gives the downloads a sensible filename.
        st.session_state["stem"] = Path(uploaded.name).stem

    except (IngestError, LLMError) as exc:
        st.session_state.pop("result", None)
        st.error(str(exc))
    except Exception as exc:  # noqa: BLE001 - surface anything unexpected in the UI
        st.session_state.pop("result", None)
        st.error(f"Unexpected error: {exc}")
    finally:
        for path in (tmp_path, jd_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


if run:
    if uploaded is None:
        st.warning("Upload a resume first.")
    elif not api_key:
        st.warning(
            f"A {provider.title()} API key is required. Enter it in the sidebar, "
            f"or set {KEY_ENV_VARS[provider][0]} in your environment."
        )
    else:
        analyse()


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def show_profile(result: PipelineResult) -> None:
    p = result.profile
    st.subheader(p.full_name or "Candidate")
    if p.headline:
        st.caption(p.headline)

    cols = st.columns(4)
    cols[0].metric("Experience", f"{p.total_experience_years:g} yrs")
    cols[1].metric("Level", p.seniority_level)
    cols[2].metric("Location", p.location or "-")
    cols[3].metric("Skills found", len(p.technical_skills))

    if p.summary:
        st.write(p.summary)

    left, right = st.columns(2)
    with left:
        st.markdown("**Strengths**")
        for item in p.notable_strengths or ["_none noted_"]:
            st.markdown(f"- {item}")
    with right:
        st.markdown("**Gaps & things to clarify**")
        for item in p.gaps_and_concerns or ["_none noted_"]:
            st.markdown(f"- {item}")

    if p.technical_skills:
        st.markdown("**Technical skills**")
        st.write(", ".join(p.technical_skills))

    for job in p.experience:
        dates = " to ".join(x for x in [job.start_date, job.end_date] if x)
        with st.expander(f"{job.title} - {job.company}  ({dates})"):
            if job.achievements:
                st.markdown("**Achievements**")
                for item in job.achievements:
                    st.markdown(f"- {item}")
            if job.responsibilities:
                st.markdown("**Responsibilities**")
                for item in job.responsibilities:
                    st.markdown(f"- {item}")
            if job.tech_used:
                st.caption("Tech: " + ", ".join(job.tech_used))


def show_fit(result: PipelineResult) -> None:
    if result.assessment:
        a = result.assessment
        color = VERDICT_COLOR.get(a.verdict, "#444")
        st.markdown(
            f"### <span style='color:{color}'>{VERDICT_LABEL.get(a.verdict, a.verdict)}</span>",
            unsafe_allow_html=True,
        )
        cols = st.columns(3)
        cols[0].metric("Fit score", f"{a.fit_score}/100")
        cols[1].metric("Interview at", a.recommended_seniority)
        cols[2].metric("Target role", a.target_role)

        st.write(a.rationale)

        if a.matched_requirements:
            st.markdown("**Requirement by requirement**")
            st.dataframe(
                [
                    {
                        "Requirement": m.requirement,
                        "Strength": m.strength,
                        "Evidence": m.evidence or "-",
                    }
                    for m in a.matched_requirements
                ],
                width="stretch",
                hide_index=True,
            )

        left, right = st.columns(2)
        with left:
            st.markdown("**Missing requirements**")
            for item in a.missing_requirements or ["_none_"]:
                st.markdown(f"- {item}")
        with right:
            st.markdown("**Risks & red flags**")
            for item in a.risks_and_red_flags or ["_none noted_"]:
                st.markdown(f"- {item}")

        st.markdown("**The interview must resolve**")
        for item in a.areas_to_probe:
            st.markdown(f"- {item}")

    elif result.recommendations:
        r = result.recommendations
        st.info(r.overall_positioning)
        for i, rec in enumerate(r.recommendations, 1):
            with st.expander(
                f"{i}. {rec.seniority} {rec.role_title} - fit {rec.fit_score}/100",
                expanded=(i == 1),
            ):
                st.progress(min(max(rec.fit_score, 0), 100) / 100)
                st.markdown("**Why they fit**")
                for item in rec.why_fit:
                    st.markdown(f"- {item}")
                st.markdown("**Gaps**")
                for item in rec.gaps or ["_none noted_"]:
                    st.markdown(f"- {item}")
        if r.stretch_roles:
            st.markdown("**Stretch roles (12-18 months)**")
            st.write(", ".join(r.stretch_roles))
        if r.poor_fit_roles:
            st.markdown("**Not a fit**")
            for item in r.poor_fit_roles:
                st.markdown(f"- {item}")


def show_kit(result: PipelineResult) -> None:
    kit = result.interview_kit
    if kit is None:
        st.info("Question generation was skipped.")
        return

    st.markdown(f"**Focus:** {kit.interview_focus}")

    if kit.recommended_structure:
        st.markdown("**Suggested loop**")
        st.dataframe(
            [
                {
                    "Round": rd.round_name,
                    "Minutes": rd.duration_minutes,
                    "Objective": rd.objective,
                    "Questions": ", ".join(rd.question_ids),
                }
                for rd in kit.recommended_structure
            ],
            width="stretch",
            hide_index=True,
        )

    categories = sorted({q.category for q in kit.questions})
    chosen = st.multiselect(
        "Filter by category",
        categories,
        default=categories,
        format_func=lambda c: c.replace("_", " ").title(),
    )

    for q in kit.questions:
        if q.category not in chosen:
            continue
        with st.expander(f"{q.id}  ·  {q.question}"):
            st.caption(
                f"{q.category.replace('_', ' ')} · {q.difficulty} · "
                f"{q.skill_assessed} · ~{q.time_minutes} min"
            )
            st.markdown(f"*Why ask it:* {q.why_this_question}")
            left, right = st.columns(2)
            with left:
                st.markdown("**Look for**")
                for item in q.what_to_look_for:
                    st.markdown(f"- {item}")
            with right:
                st.markdown("**Red flags**")
                for item in q.red_flags or ["_none noted_"]:
                    st.markdown(f"- {item}")
            if q.follow_ups:
                st.markdown("**Follow-ups**")
                for item in q.follow_ups:
                    st.markdown(f"- {item}")

    if kit.scorecard:
        st.markdown("**Scorecard**")
        st.dataframe(
            [
                {
                    "Criterion": c.criterion,
                    "Weight %": c.weight_percent,
                    "What great looks like": c.what_great_looks_like,
                }
                for c in kit.scorecard
            ],
            width="stretch",
            hide_index=True,
        )
        total = sum(c.weight_percent for c in kit.scorecard)
        if total != 100:
            st.warning(f"Scorecard weights sum to {total}%, not 100%. Adjust before using.")

    if kit.closing_notes:
        st.markdown("**Interviewer notes**")
        st.write(kit.closing_notes)


result: PipelineResult | None = st.session_state.get("result")

if result is None:
    st.title("Resume to Interview Kit")
    st.markdown(
        "Upload a resume and name the role. You get a structured candidate profile, "
        "a fit verdict with evidence you can check, and interview questions written "
        "for **this** candidate rather than pulled off a generic list."
    )
    st.markdown(
        "Leave the role blank and the pipeline will instead recommend the roles "
        "the candidate suits, then build an interview for the strongest one."
    )
    st.info("Set your inputs in the sidebar, then press **Analyse resume**.")
else:
    st.title(f"Interview brief · {result.profile.full_name or 'Candidate'}")

    stem = st.session_state.get("stem", "brief")
    col_a, col_b, _ = st.columns([1, 1, 3])
    col_a.download_button(
        "Download report (.md)",
        render_markdown(result),
        file_name=f"{stem}.md",
        mime="text/markdown",
        width="stretch",
    )
    col_b.download_button(
        "Download data (.json)",
        json.dumps(result.model_dump(), indent=2, ensure_ascii=False),
        file_name=f"{stem}.json",
        mime="application/json",
        width="stretch",
    )

    fit_tab_label = "Role fit" if result.assessment else "Suitable roles"
    tabs = st.tabs(["Profile", fit_tab_label, "Interview kit"])
    with tabs[0]:
        show_profile(result)
    with tabs[1]:
        show_fit(result)
    with tabs[2]:
        show_kit(result)

    if usage := st.session_state.get("usage"):
        st.caption(f"Usage: {usage}")
    st.caption(
        "Decision support only - verify every claim against the resume. "
        "A human makes the hiring decision."
    )
