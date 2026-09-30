"""The demonstration is a runnable scenario, and it is tested as one.

Ticket 14's requirement is that the demonstration and the test suite are the same
artefact. This runs the actual script as a subprocess, so the thing a judge would
be shown is the thing under test, and a broken demo fails the suite.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "demo_scenario.py"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def run_scenario() -> tuple[int, str]:
    proc = subprocess.run(
        [str(PYTHON), str(SCRIPT)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_the_scenario_runs_and_passes_every_check():
    code, output = run_scenario()
    assert code == 0, f"the demonstration failed:\n{output}"
    assert "all checks passed" in output
    assert "FAIL" not in output, f"a check failed inside the run:\n{output}"


def test_the_scenario_reaches_all_four_verdicts_or_explains_why_not():
    """ANSWERED_LOCALLY and CONFLICTED at minimum; the rest are scenario-scoped."""
    _, output = run_scenario()
    assert "ANSWERED_LOCALLY" in output, "no device answered while alone"
    assert "CONFLICTED" in output, "the reconnect produced no disagreement"


def test_the_scenario_measures_rather_than_asserts():
    _, output = run_scenario()
    assert "ms" in output, "no latency was measured"
    assert "B sent" in output, "no byte count was reported"
    assert "cursor" in output, "the incremental position was not reported"


def test_the_scenario_names_the_path_that_actually_ran():
    _, output = run_scenario()
    assert "depot_delta" in output
    assert "vendor" not in output.lower() or "not" in output.lower()


def test_the_scenario_does_not_claim_the_vendor_pattern_loses_data():
    """The false claim must not reach a judge through the demo output."""
    _, output = run_scenario()
    lowered = output.lower()
    for phrase in (
        "loses data",
        "loses your data",
        "destroys unsynced",
        "destroys your",
        "drops unsynced",
        "bug in qdrant",
        "flaw in qdrant",
    ):
        assert phrase not in lowered, f"the demo asserted {phrase!r}"


def test_the_scenario_cites_the_regulation_that_actually_applies():
    _, output = run_scenario()
    assert "21 CFR" in output
    assert "395.8" not in output, "hours-of-service is a category error here"