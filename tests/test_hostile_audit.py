"""Adversarial audit as a test.

`scripts/audit_hostile.py` attacks the central promise directly. It runs the
same checks here, so a regression in any of them fails the suite rather than
waiting for someone to run the audit by hand.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_hostile.py"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def run_audit() -> tuple[int, str]:
    proc = subprocess.run(
        [str(PYTHON), str(SCRIPT)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=900,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_no_adversarial_check_fails():
    code, output = run_audit()
    assert code == 0, f"an adversarial check failed:\n{output}"
    assert "FAIL" not in output, output
    assert "adversarial checks passed" in output


def test_the_audit_covers_the_promises_it_claims_to():
    _, output = run_audit()
    for phrase in (
        "depot retains both claims",
        "converges on both claims",
        "a device behind is told",
        "replayed claim is stored once",
        "any arrival order",
        "not truncated",
        "byte-identical at the far end",
        "rewinds safely AND says so",
    ):
        assert phrase in output, f"the audit no longer covers: {phrase}"
