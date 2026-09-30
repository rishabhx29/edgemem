"""The interface is tested as an interface.

The demonstration is the test suite's output rather than a separate artefact, so
the thing a judge would be shown is the thing under test. These tests start the
real server, drive it over HTTP, and read the payloads back.

The load-bearing test is the last one. It reads the built bundle and fails if it
finds any of the scenario's own wording in it. That is the only mechanical way to
hold the claim that the interface authors no demo data: if someone types a
verdict summary into a component, this fails.

Skipped, not failed, when ``web/dist`` has not been built. A missing front end is
a missing build step, not a broken engine, and failing here would send somebody
to debug the wrong thing.
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "scripts", ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

DIST = ROOT / "web" / "dist"

pytestmark = pytest.mark.skipif(
    not (DIST / "index.html").is_file(),
    reason="web/dist is not built; run `cd web && npm install && npm run build`",
)


@pytest.fixture(scope="module")
def server():
    """The real server on a free port, shared by every test in this module."""
    import demo_ui

    console = demo_ui.Console()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), demo_ui.handler_for(console))
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield base, console
    finally:
        httpd.shutdown()
        console.close()


def get(base: str, path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(base + path, timeout=120) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def post(base: str, path: str, payload: dict) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base + path,
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {}


def run_to(base: str, n: int) -> dict:
    status, state = post(base, "/api/step", {"n": n})
    assert status == 200
    return state


# -- the pages -------------------------------------------------------------


def test_every_page_is_served(server):
    base, _ = server
    for path in ("/", "/demo", "/how", "/evidence"):
        status, body = get(base, path)
        assert status == 200, f"{path} returned {status}"
        assert "<div id=\"root\">" in body, f"{path} is not the application shell"


def test_the_navigation_offers_all_four_pages(server):
    base, _ = server
    status, body = get(base, "/")
    assert status == 200
    # The shell is rendered client-side, so the labels live in the bundle the
    # shell loads rather than in the served HTML.
    bundle = "".join(
        (DIST / "assets" / p.name).read_text(encoding="utf-8")
        for p in (DIST / "assets").glob("*.js")
    )
    for label in ("The demonstration", "How it works", "Evidence"):
        assert label in bundle, f"{label!r} is not reachable from the shell"
    assert body


def test_a_path_outside_the_build_is_refused(server):
    base, _ = server
    status, _body = get(base, "/../pyproject.toml")
    assert status in (403, 404), "the server served something outside web/dist"
    status, _body = get(base, "/%2e%2e/%2e%2e/pyproject.toml")
    assert status in (403, 404), "an encoded traversal escaped the build directory"


def test_an_unknown_page_is_not_found(server):
    base, _ = server
    status, _body = get(base, "/nope")
    assert status == 404


# -- the scenario ----------------------------------------------------------


def test_the_scenario_reaches_all_four_verdicts(server):
    base, _console = server
    state = run_to(base, 6)
    kinds = {
        ask["verdict"]["kind"]
        for step in state["steps"]
        for ask in step["asks"]
    }
    assert kinds == {
        "ANSWERED_LOCALLY",
        "CONFLICTED",
        "UNRESOLVED_CLOUD_REQUIRED",
        "CORRECTED",
    }, f"the run only reached {sorted(kinds)}"


def test_each_step_produces_the_verdict_it_promised(server):
    base, _console = server
    state = run_to(base, 6)
    for step in state["steps"]:
        if not step["expects"]:
            continue
        produced = {ask["verdict"]["kind"] for ask in step["asks"]}
        assert step["expects"] in produced, (
            f"step {step['n']} promised {step['expects']} and produced {produced}"
        )


def test_the_conflict_keeps_both_claims_and_refuses_to_choose(server):
    base, _console = server
    state = run_to(base, 6)
    conflicted = next(
        ask["verdict"]
        for step in state["steps"]
        for ask in step["asks"]
        if ask["verdict"]["kind"] == "CONFLICTED"
    )
    values = {
        side[claim]["value"]
        for side in conflicted["conflicts"]
        for claim in ("a", "b")
    }
    assert values == {"broken", "intact"}
    assert "not choosing" in conflicted["summary"].lower()
    assert conflicted["authority"] is not None
    assert conflicted["authority"]["entitling_class"] == "INSTRUMENT"


def test_the_withheld_claim_is_priced_and_explained(server):
    base, _console = server
    state = run_to(base, 6)
    unresolved = next(
        ask["verdict"]
        for step in state["steps"]
        for ask in step["asks"]
        if ask["verdict"]["kind"] == "UNRESOLVED_CLOUD_REQUIRED"
    )
    assert unresolved["bytes_withheld"] > 0
    assert unresolved["needed"], "a refusal that names no missing claim is silent"
    for needed in unresolved["needed"]:
        assert needed["bytes_if_sent"] > 0
        assert needed["why_withheld"]


def test_the_correction_returns_both_answers(server):
    base, _console = server
    state = run_to(base, 6)
    corrected = next(
        ask["verdict"]
        for step in state["steps"]
        for ask in step["asks"]
        if ask["verdict"]["kind"] == "CORRECTED"
    )
    assert corrected["previous_summary"], (
        "a correction that cannot show what it changed from does not "
        "return both answers"
    )
    assert corrected["changed_by"], "no claim was named as the cause"


def test_the_exchange_reports_the_path_that_actually_ran(server):
    base, _console = server
    state = run_to(base, 6)
    exchanges = [x for step in state["steps"] for x in step["exchanges"]]
    assert exchanges, "the demonstration never reported an exchange"
    for report in exchanges:
        assert report["path"] == "depot_delta"
        assert report["device_id"] and report["depot_id"]
        assert report["complete"] is True
    assert any(r["pushed"] > 0 for r in exchanges)
    assert any(r["pulled"] > 0 for r in exchanges)


# -- the steps are a function of the steps before them ----------------------


def test_entering_at_a_step_replays_the_ones_before_it(server):
    base, console = server
    console.reset()
    direct = run_to(base, 5)
    direct_conflict = next(
        ask["verdict"]
        for step in direct["steps"]
        for ask in step["asks"]
        if ask["verdict"]["kind"] == "CONFLICTED"
    )

    console.reset()
    for n in (1, 2, 3, 4):
        run_to(base, n)
    stepwise = run_to(base, 5)
    stepwise_conflict = next(
        ask["verdict"]
        for step in stepwise["steps"]
        for ask in step["asks"]
        if ask["verdict"]["kind"] == "CONFLICTED"
    )

    assert (
        direct_conflict["summary"] == stepwise_conflict["summary"]
    ), "entering at step 5 produced a different state than walking to it"

    # Claim ids are digests over content that includes the observation time, so
    # two separate fleets can never agree on them. The values and the ladder can
    # be compared, and those are what a reader would check.
    assert _side_values(direct_conflict) == _side_values(stepwise_conflict)
    assert _entitling(direct_conflict) == _entitling(stepwise_conflict)


def _side_values(verdict: dict) -> list[set[str]]:
    return [
        {side["a"]["value"], side["b"]["value"]}
        for side in verdict["conflicts"]
    ]


def _entitling(verdict: dict) -> str | None:
    authority = verdict["authority"] or {}
    return authority.get("entitling_class")


def test_running_the_same_step_twice_changes_nothing(server):
    base, console = server
    console.reset()
    once = run_to(base, 4)
    twice = run_to(base, 4)
    assert once["steps"] == twice["steps"], "a repeated step duplicated the scenario"


def test_reset_returns_the_interface_to_the_start(server):
    base, _console = server
    run_to(base, 6)
    status, state = post(base, "/api/reset", {})
    assert status == 200
    assert state["reached"] == 0
    assert state["steps"] == []
    assert not any(state["seen"].values())


def test_a_step_outside_the_scenario_is_refused(server):
    base, _console = server
    status, _body = post(base, "/api/step", {"n": 99})
    assert status == 400


# -- asking for yourself ----------------------------------------------------


def test_a_free_question_is_answered_without_a_subject(server):
    base, _console = server
    run_to(base, 6)
    status, body = get(base, "/api/ask?device=TRK-7&q=seal%20state")
    assert status == 200
    payload = json.loads(body)
    assert payload["device_id"] == "TRK-7"
    assert payload["verdict"]["subject"] == "trailer T-114 seal"
    assert payload["verdict"]["kind"] in {
        "ANSWERED_LOCALLY",
        "CONFLICTED",
        "CORRECTED",
    }


def test_asking_something_the_device_never_held_does_not_crash(server):
    """The engine may well guess a subject here. It must not invent a claim.

    Recorded because the guess is real: with any claim on the device, subject
    inference always resolves to something, so an unrelated question comes back
    answered about the nearest claim. The interface is required to show which
    subject was inferred, so the guess is visible rather than silent.
    """
    base, _console = server
    run_to(base, 6)
    status, body = get(
        base, "/api/ask?device=TRK-7&q=account%20number%20for%20the%20fuel%20card"
    )
    assert status == 200
    verdict = json.loads(body)["verdict"]
    assert verdict["subject"], "the inferred subject must be reported"
    for claim in verdict["claims"]:
        assert claim["subject"] == verdict["subject"]


def test_an_unknown_device_is_refused(server):
    base, _console = server
    run_to(base, 6)
    status, _body = get(base, "/api/ask?device=NOPE&q=seal%20state")
    assert status == 404


def test_a_question_must_not_be_empty(server):
    base, _console = server
    run_to(base, 6)
    status, _body = get(base, "/api/ask?device=TRK-7&q=")
    assert status == 400


# -- the projection rule ----------------------------------------------------


def test_the_reference_pages_expose_engine_output_only(server):
    base, _console = server
    status, body = get(base, "/api/verdicts")
    assert status == 200
    payload = json.loads(body)
    assert set(payload["verdicts"]) == {
        "ANSWERED_LOCALLY",
        "CONFLICTED",
        "UNRESOLVED_CLOUD_REQUIRED",
        "CORRECTED",
    }
    for ask in payload["verdicts"].values():
        assert ask["verdict"]["summary"], "a reference verdict had no summary"


def test_the_destructive_comparison_is_read_off_the_engine(server):
    base, _console = server
    status, body = get(base, "/api/comparison")
    assert status == 200
    comparison = json.loads(body)["comparison"]
    assert comparison["written"] == 2
    assert comparison["keys"] == 1, "the two writes did not share a storage key"
    assert comparison["overwritten"] == 1
    assert comparison["engine_survivors"] == 2, (
        "the engine did not keep both claims of the pair the comparison destroyed one of"
    )


def test_both_verticals_are_reported_from_the_pack_objects(server):
    base, _console = server
    status, body = get(base, "/api/packs")
    assert status == 200
    packs = json.loads(body)["packs"]
    assert {p["key"] for p in packs} == {"trucks", "substation"}

    for pack in packs:
        assert pack["citation"].startswith("21 CFR")
        assert pack["ladder"], f"{pack['key']} reported no ladder"
        assert len(pack["ladder"]) == len(set(pack["ladder"]))
        assert pack["id_label"], f"{pack['key']} reported no subject model"

    freight = next(p for p in packs if p["key"] == "trucks")
    substation = next(p for p in packs if p["key"] == "substation")

    # The ladder is per-vertical, not a constant in the engine. Freight puts a
    # calibrated scanner first; a torque specification is signed for by a person
    # and does not. If these agreed, the ladder would be decoration.
    assert freight["ladder"][0] == "INSTRUMENT"
    assert substation["ladder"][0] == "ATTESTED_HUMAN"
    assert freight["ladder"] != substation["ladder"]
    assert freight["ladder_version"] and substation["ladder_version"]


def test_the_built_interface_authors_none_of_the_scenarios_data(server):
    """The claim that the interface authors no demo data, checked mechanically.

    Every string the demonstration shows arrives in a response body. If any of the
    scenario's own wording appears in the bundle, a component is carrying data
    the engine did not produce.
    """
    base, _console = server
    run_to(base, 6)
    state = json.loads(get(base, "/api/state")[1])
    authored: set[str] = set()
    for step in state["steps"]:
        for ask in step["asks"]:
            authored.add(ask["verdict"]["summary"])
            for claim in ask["verdict"]["claims"]:
                authored.add(claim["value"])
                authored.add(claim["reason"])
            for pair in ask["verdict"]["conflicts"]:
                for side in ("a", "b"):
                    authored.add(pair[side]["value"])
            for needed in ask["verdict"]["needed"]:
                authored.add(needed["value"])

    bundle = "".join(
        path.read_text(encoding="utf-8")
        for path in (DIST / "assets").glob("*.js")
    )

    leaked = sorted(
        text
        for text in authored
        if len(text) > 24 and text in bundle
    )
    assert not leaked, (
        "the built interface contains strings the engine produced, so it is "
        f"carrying authored demo data: {leaked[:3]}"
    )