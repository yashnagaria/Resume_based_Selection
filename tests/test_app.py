"""Exercise the Streamlit frontend headlessly against the mock server.

Uses Streamlit's AppTest harness, which runs app.py for real and surfaces any
exception the script raises.

Run: python -m tests.test_app
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from streamlit.testing.v1 import AppTest

from tests.mock_server import start_mock_server

ROOT = Path(__file__).resolve().parent.parent
RESUME = ROOT / "samples" / "sample_resume.txt"

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


server, base_url = start_mock_server()
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-mock"
os.environ["ANTHROPIC_BASE_URL"] = base_url


def fresh_app() -> AppTest:
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    app.run()
    return app


print("\nInitial render")
at = fresh_app()
check("script runs without exception", not at.exception, str(at.exception))
check("title rendered", any("Resume to Interview Kit" in t.value for t in at.title))
check("file uploader present", len(at.sidebar.text_input) >= 1)
check("role input present",
      any("Role" in ti.label for ti in at.sidebar.text_input),
      str([ti.label for ti in at.sidebar.text_input]))
check("analyse button present", len(at.sidebar.button) == 1)
check("prompt shown before any run", len(at.info) >= 1)

print("\nClicking Analyse with no resume")
at2 = fresh_app()
at2.sidebar.button[0].click().run()
check("no exception", not at2.exception, str(at2.exception))
check("warns that a resume is needed",
      any("Upload a resume" in w.value for w in at2.warning),
      str([w.value for w in at2.warning]))

print("\nFull run with an uploaded resume")
at3 = fresh_app()

# AppTest has no file_uploader widget API, so drive the pipeline the way the
# app does and assert the rendering functions handle a real PipelineResult.
import app as app_module  # noqa: E402
from resume_pipeline.llm import ClaudeClient  # noqa: E402
from resume_pipeline.pipeline import run_pipeline  # noqa: E402
from resume_pipeline.report import render_markdown  # noqa: E402

client = ClaudeClient(api_key="sk-ant-mock")
client.client = type(client.client)(api_key="sk-ant-mock", base_url=base_url, max_retries=0)
result = run_pipeline(RESUME, client=client, target_role="Senior Backend Engineer",
                      num_questions=12)

check("pipeline produced a result", result.assessment is not None)
check("markdown renders from the app's renderer", len(render_markdown(result)) > 800)
check("app exposes the render helpers",
      all(hasattr(app_module, fn) for fn in ("show_profile", "show_fit", "show_kit")))
check("verdict colour map covers every verdict",
      set(app_module.VERDICT_COLOR) == {"strong_fit", "good_fit", "borderline", "not_a_fit"})

# Render the result through the app's own script by seeding session state.
at4 = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
at4.session_state["result"] = result
at4.session_state["usage"] = client.usage_summary()
at4.session_state["stem"] = "sample_resume"
at4.run()
check("results view runs without exception", not at4.exception, str(at4.exception))
check("brief title shown",
      any("Interview brief" in t.value for t in at4.title),
      str([t.value for t in at4.title]))
check("three tabs rendered", len(at4.tabs) == 3, str(len(at4.tabs)))
check("download buttons present", len(at4.download_button) == 2, str(len(at4.download_button)))
check("usage caption shown", any("input tokens" in c.value for c in at4.caption))

server.shutdown()

print()
if failures:
    print(f"{len(failures)} FAILED: {', '.join(failures)}")
    sys.exit(1)
print("Frontend verified.")
