"""A local stand-in for the Anthropic Messages API.

It matches each incoming request to a canned response by the field set of the
JSON schema in `output_config.format`, then replies with a well-formed SSE
stream. Lets the whole pipeline be exercised with no API key and no network.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Tuple

from resume_pipeline.models import (
    InterviewKit,
    ResumeProfile,
    RoleFitAssessment,
    RoleRecommendations,
)

PROFILE: Dict[str, Any] = {
    "full_name": "Priya Ramanathan", "email": "p@example.com", "phone": "+91 90000 00000",
    "location": "Bengaluru, India", "links": ["github.com/priyar"],
    "headline": "Backend engineer, payments", "summary": "Six years in fintech infra.",
    "total_experience_years": 6.0, "seniority_level": "senior",
    "education": [{"degree": "B.E.", "field_of_study": "CS", "institution": "CoEP",
                   "graduation_year": "2019", "honors": "8.7 CGPA"}],
    "experience": [{"title": "Senior SWE", "company": "Razorflow", "start_date": "Mar 2022",
                    "end_date": "Present", "duration_months": 42, "location": "Bengaluru",
                    "responsibilities": ["Owned the ledger service"],
                    "achievements": ["Cut p99 from 1.8s to 240ms"],
                    "tech_used": ["Go", "Kafka"]}],
    "projects": [{"name": "ledgerlite", "description": "Accounting library",
                  "tech_used": ["Go"], "link": "github.com/priyar/ledgerlite"}],
    "certifications": [{"name": "AWS SAA", "issuer": "AWS", "year": "2021"}],
    "technical_skills": ["Go", "Python", "Kafka"], "soft_skills": ["Mentoring"],
    "domains": ["Payments"], "languages": ["English", "Tamil"],
    "notable_strengths": ["Ledger depth"], "gaps_and_concerns": ["No compliance exposure"],
    "extraction_notes": ["Internship excluded"],
}

ASSESSMENT: Dict[str, Any] = {
    "candidate_name": "Priya Ramanathan", "target_role": "Senior Backend Engineer",
    "verdict": "strong_fit", "fit_score": 88, "recommended_seniority": "Senior",
    "matched_requirements": [{"requirement": "5+ yrs backend", "evidence": "6 yrs",
                              "strength": "strong"}],
    "missing_requirements": ["Compliance work"], "transferable_evidence": ["Reconciliation"],
    "risks_and_red_flags": ["Verify design ownership"],
    "areas_to_probe": ["Kafka outbox migration depth"],
    "rationale": "Meets every core requirement with evidence.",
}

RECOMMENDATIONS: Dict[str, Any] = {
    "candidate_name": "Priya Ramanathan",
    "overall_positioning": "Payments infrastructure specialist.",
    "recommendations": [{"role_title": "Backend Engineer (Payments)", "seniority": "Senior",
                         "fit_score": 90, "why_fit": ["Owned a 40M txn/mo ledger"],
                         "gaps": ["No compliance exposure"],
                         "ramp_up_needed": ["Domain onboarding"]}],
    "stretch_roles": ["Engineering Manager"],
    "poor_fit_roles": ["Frontend Engineer - no UI evidence"],
    "areas_to_probe": ["Design ownership depth"],
}

KIT: Dict[str, Any] = {
    "candidate_name": "Priya Ramanathan", "role": "Senior Backend Engineer",
    "interview_focus": "Verify ledger ownership.",
    "recommended_structure": [{"round_name": "Deep dive", "duration_minutes": 60,
                               "objective": "Probe the migration", "question_ids": ["Q1"]}],
    "questions": [{"id": "Q1", "category": "resume_deep_dive", "difficulty": "hard",
                   "question": "Walk me through the Kafka outbox migration.",
                   "why_this_question": "Headline achievement claim.",
                   "skill_assessed": "Distributed systems",
                   "what_to_look_for": ["Names the dual-write problem"],
                   "red_flags": ["Cannot explain failure modes"],
                   "follow_ups": ["How did you handle replay?"], "time_minutes": 15}],
    "scorecard": [{"criterion": "Distributed systems", "weight_percent": 100,
                   "what_great_looks_like": "Precise about consistency tradeoffs"}],
    "closing_notes": "Hire if depth is confirmed.",
}

ROUTES: List[Tuple[frozenset, Dict[str, Any]]] = [
    (frozenset(ResumeProfile.model_fields), PROFILE),
    (frozenset(RoleFitAssessment.model_fields), ASSESSMENT),
    (frozenset(RoleRecommendations.model_fields), RECOMMENDATIONS),
    (frozenset(InterviewKit.model_fields), KIT),
]


def _sse(payload_obj: Dict[str, Any]) -> bytes:
    text = json.dumps(payload_obj)
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_mock", "type": "message", "role": "assistant",
            "model": "claude-opus-5", "content": [], "stop_reason": None,
            "stop_sequence": None, "usage": {"input_tokens": 100, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": 200}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {n}\ndata: {json.dumps(p)}\n\n" for n, p in events).encode()


class MockHandler(BaseHTTPRequestHandler):
    requests: List[Dict[str, Any]] = []

    def log_message(self, *_args):  # keep the test output clean
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        MockHandler.requests.append(body)

        required = frozenset(body["output_config"]["format"]["schema"]["required"])
        payload_obj = next((resp for fields, resp in ROUTES if fields == required), None)
        if payload_obj is None:
            self.send_error(400, f"No canned response for fields: {sorted(required)}")
            return

        payload = _sse(payload_obj)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def start_mock_server() -> Tuple[HTTPServer, str]:
    """Start the mock on a free port; returns the server and its base URL."""
    MockHandler.requests = []
    server = HTTPServer(("127.0.0.1", 0), MockHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"
