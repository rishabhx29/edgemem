"""The demonstration, as one runnable scenario.

Every number printed here is measured during the run. Nothing is asserted, mocked
or narrated: the scenario drives two real devices, a real depot over a real
socket, and real shards, then prints what the engine actually did.

The story is not written here. The claims, the fleet and the ordering of the
steps live in :mod:`story`, which the browser interface runs too, so the two
demonstrations cannot tell each other a different story. This file is the
narration and the checks; ``story`` is the thing being narrated.

Run it:

    .\\.venv\\Scripts\\python.exe scripts\\demo_scenario.py

Exits non-zero if any assertion about the outcome fails, so it doubles as a smoke
test. The test suite runs the same steps through the same seam.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "scripts", ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import fixtures.trucks as trucks  # noqa: E402
import story  # noqa: E402
from edgemem.domain import AuthorityClass  # noqa: E402

WIDTH = 78


def rule(title: str = "") -> None:
    if title:
        print(f"\n{'=' * WIDTH}\n{title}\n{'=' * WIDTH}")
    else:
        print("=" * WIDTH)


def main() -> int:
    failures: list[str] = []

    def check(label: str, condition: bool) -> None:
        mark = "ok  " if condition else "FAIL"
        print(f"      [{mark}] {label}")
        if not condition:
            failures.append(label)

    root = Path(tempfile.mkdtemp(prefix="edgemem-demo-"))
    fleet = None
    try:
        fleet, performed = story.run_to(root, len(story.STEPS))
        by_kind = {
            ask.verdict.kind.value: ask
            for _step, result in performed
            for ask in result.asks
        }
        exchanges = [r for _step, result in performed for r in result.exchanges]

        rule("EDGEMEM - disagreement-durable edge memory")
        print(
            "Two offline devices record incompatible claims about one seal.\n"
            "They reconnect. Both claims survive. The engine refuses to choose."
        )
        print(f"\nvertical : {trucks.PACK.labels.claim}")
        print(f"ladder   : {trucks.PACK.ladder.version}")
        print(f"anchored : {trucks.PACK.citation}")

        # -- the steps, in the order they happen --------------------------
        for step, result in performed:
            rule(f"[{step.n}] {step.title}")
            print(step.note)

            for ask in result.asks:
                verdict = ask.verdict
                print(
                    f"      {ask.device_id:8} {verdict.kind.value:26} "
                    f"{verdict.latency_ms:6.2f} ms via {','.join(verdict.paths_used)}"
                )
                print(f"               {verdict.summary}")

                for pair in verdict.conflicts:
                    for side in (pair[0], pair[1]):
                        flag = "entitled " if side.entitling else "retained "
                        print(
                            f"                 {flag}{side.claim.value!r:>9} - "
                            f"{side.claim.author}, {side.claim.observer}, "
                            f"{side.claim.claim_id[:8]}"
                        )
                        if side.corroboration is not None:
                            co = side.corroboration
                            print(
                                f"                   {co.agreeing_claims} agreeing "
                                f"claim(s) = {co.voices} voice(s); echoes "
                                f"{co.echoes}; self-corroborated "
                                f"{co.self_corroborated}"
                            )
                    if verdict.authority is not None:
                        print(
                            f"                 ladder   : "
                            f"{' > '.join(verdict.authority.ordered_classes)}"
                        )
                        print(
                            f"                 entitled : "
                            f"{verdict.authority.entitling_class}"
                        )

                for needed in verdict.needed:
                    print(
                        f"                 withheld: {needed.value!r} "
                        f"would cost {needed.bytes_if_sent} B"
                    )
                if verdict.previous_summary:
                    print(
                        f"                 was      : "
                        f"{verdict.previous_summary}"
                    )
                if verdict.changed_by:
                    print(f"                 changed  : {verdict.changed_by[:8]}")

            for report in result.exchanges:
                print(
                    f"      {report.device_id:8} {report.path:14} "
                    f"{report.pushed} B sent, {report.bytes_received} B received, "
                    f"cursor {report.cursor_before}->{report.cursor_after}"
                )
                for note in report.notes:
                    print(f"                 note     : {note}")

        # -- what has to be true ------------------------------------------
        rule()
        print("      checks")

        for kind in (
            "ANSWERED_LOCALLY",
            "CONFLICTED",
            "UNRESOLVED_CLOUD_REQUIRED",
            "CORRECTED",
        ):
            check(f"the run reached {kind}", kind in by_kind)

        answered = by_kind["ANSWERED_LOCALLY"].verdict
        check(
            "an answer alone cites the claim it used",
            len(answered.claims) == 1,
        )

        conflicted = by_kind["CONFLICTED"].verdict
        check("the verdict is CONFLICTED", len(conflicted.conflicts) == 1)
        check(
            "both values survive the disagreement",
            {s.claim.value for side in conflicted.conflicts for s in side}
            == {"broken", "intact"},
        )
        check(
            "the engine says it is not choosing",
            "not choosing" in conflicted.summary.lower(),
        )
        check("a ladder is presented", conflicted.authority is not None)
        if conflicted.authority is not None:
            check(
                "the instrument is the entitling class, despite observing earlier",
                conflicted.authority.entitling_class == AuthorityClass.INSTRUMENT.value,
            )

        unresolved = by_kind["UNRESOLVED_CLOUD_REQUIRED"].verdict
        check(
            "the device prices what it is not permitted to use",
            unresolved.bytes_withheld > 0
            and any(n.bytes_if_sent > 0 for n in unresolved.needed),
        )
        check(
            "every withheld claim says why",
            all(n.why_withheld for n in unresolved.needed),
        )

        corrected = by_kind["CORRECTED"].verdict
        check(
            "a correction can show what it said last time",
            bool(corrected.previous_summary),
        )
        check(
            "a correction names the single claim that moved it",
            bool(corrected.changed_by),
        )

        check("the sync path is reported, not assumed", bool(exchanges))
        check(
            "both directions of the exchange carried something",
            any(r.pushed > 0 for r in exchanges) and any(r.pulled > 0 for r in exchanges),
        )
        check(
            "the sender's byte count and the depot's agree",
            all(
                r.bytes_sent == r.depot_bytes_received
                for r in exchanges
                if r.pushed > 0
            ),
        )
        check(
            "every exchange completed",
            all(r.complete for r in exchanges),
        )

        # -- the state the devices are left in ---------------------------
        rule()
        stats = fleet.devices[story.DOCK].stats()
        print("      device memory")
        print(json.dumps(stats, indent=6, default=str))
        check("the index state is reported honestly", bool(stats["index"]))

        rule()
        if failures:
            print(f"      {len(failures)} FAILED:")
            for f in failures:
                print(f"        - {f}")
            return 1
        print(
            "      all checks passed - this is what the engine did, "
            "not what it was told to do"
        )
        return 0
    finally:
        if fleet is not None:
            fleet.close()
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())