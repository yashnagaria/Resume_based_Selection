"""Run every offline test suite. Usage: python -m tests.run_all"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUITES = ["tests.test_offline", "tests.test_request_shape", "tests.test_pipeline_mock"]

failed: list[str] = []
for suite in SUITES:
    print(f"\n{'=' * 60}\n{suite}\n{'=' * 60}")
    result = subprocess.run([sys.executable, "-m", suite], cwd=ROOT)
    if result.returncode != 0:
        failed.append(suite)

print(f"\n{'=' * 60}")
if failed:
    print(f"FAILED: {', '.join(failed)}")
    sys.exit(1)
print(f"All {len(SUITES)} suites passed.")
