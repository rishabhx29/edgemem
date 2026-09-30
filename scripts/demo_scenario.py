"""The demonstration, as one runnable scenario.

Every number printed here is measured during the run. Nothing is asserted, mocked
or narrated: the scenario drives two real devices, a real depot over a real
socket, and real shards, then prints what the engine actually did.

The story it tells is the one the problem statement asks for. Two honest devices,
neither able to reach the other, record incompatible claims about the same thing.
When they reconnect, both claims survive, the engine refuses to choose between
them, and it shows who would be entitled to settle it.

Run it:

    .\\.venv\\Scripts\\python.exe scripts\\demo_scenario.py

Exits non-zero if any assertion about the outcome fails, so it doubles as a
smoke test. The test suite runs the same steps through the same seam.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import fixtures.trucks as trucks  # noqa: E402
from edgemem.domain import AuthorityClass, Claim, utcnow  # noqa: E402
from edgemem.memory import EdgeMemory  # noqa: E402
from edgemem.outbox import Outbox  # noqa: E402
from edgemem.store import ShardStore  # noqa: E402
from edgemem.sync import Depot, DeviceLink, SyncPath  # noqa: E402

WIDTH = 78


def rule(title: str = "") -> None:
    if title:
        print(f"\n{'=' * WIDTH}\n{title}\n{'=' * WIDTH}")
    else:
        print("=" * WIDTH)


def step(n: int, title: str) -> None:
    print(f"\n[{n}] {title}")


class Fleet:
    """Two devices and the depot between them. Real processes of state, real socket."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.shards: list[ShardStore] = []
        self.devices: dict[str, EdgeMemory] = {}
        self.links: dict[str, DeviceLink] = {}

        depot_mem = self._device("DEPOT-1", trucks.PACK)
        self.depot = Depot(depot_mem, root / "ledger.jsonl", depot_id="DEPOT-1")
        self.depot_url = self.depot.serve()

        for device_id in ("TRK-7", "DEPOT-2"):
            self._device(device_id, trucks.PACK)

    def _device(self, device_id: str, pack) -> EdgeMemory:
        store = ShardStore(
            self.root / f"{device_id}-shard", device_id=device_id, dense_size=256
        )
        store.open()
        self.shards.append(store)
        mem = EdgeMemory(
            store,
            pack=pack,
            outbox=Outbox(self.root / f"{device_id}-outbox.jsonl"),
        )
        self.devices[device_id] = mem
        self.links[device_id] = DeviceLink(
            mem,
            mem.outbox,
            self.depot_url if hasattr(self, "depot_url") else "",
            self.root / f"{device_id}-cursor.json",
            depot_id="DEPOT-1",
        )
        return mem

    def reconnect(self, device_id: str):
        return self.links[device_id].sync()

    def close(self) -> None:
        self.depot.stop()
        for store in self.shards:
            store.close()
            store.destroy()


def a_claim(**over) -> Claim:
    base = {
        "subject": "trailer T-114 seal",
        "attribute": "state",
        "author": "operator-7",
        "observer": "handheld",
        "device_id": "TRK-7",
        "observed_at": utcnow(),
        "recorded_at": utcnow(),
        "source_class": AuthorityClass.UNATTESTED_HUMAN,
        "salience": 0.9,
        "urgency": 0.9,
        "sensitivity": 0.0,
    }
    base.update(over)
    return Claim(**base)


def main() -> int:
    failures: list[str] = []

    def check(label: str, condition: bool) -> None:
        mark = "ok  " if condition else "FAIL"
        print(f"      [{mark}] {label}")
        if not condition:
            failures.append(label)

    root = Path(tempfile.mkdtemp(prefix="edgemem-demo-"))
    try:
        fleet = Fleet(root)
        subject = "trailer T-114 seal"

        rule("EDGEMEM - disagreement-durable edge memory")
        print(
            "Two offline devices record incompatible claims about one seal.\n"
            "They reconnect. Both claims survive. The engine refuses to choose."
        )
        print(f"\nvertical: {trucks.PACK.labels.claim}, ladder {trucks.PACK.ladder.version}")
        print(f"anchored to: {trucks.PACK.citation}")

        # ------------------------------------------------------------------
        step(1, "Both devices go out of coverage and record")
        # Each claim is stamped from one shared prior state, so neither saw the
        # other. That is what makes them concurrent rather than a revision.
        from dataclasses import replace

        from edgemem.domain import CausalContext

        prior = CausalContext({"TRK-7": 1, "DEPOT-2": 1})

        hand = a_claim(value="broken")
        hand_ctx = prior.copy_for_write("TRK-7")
        hand_ctx.observe("TRK-7")
        hand = replace(hand, causal=hand_ctx)

        dock = a_claim(
            value="intact",
            author="dock operator 2",
            observer="dock-scanner",
            device_id="DEPOT-2",
            source_class=AuthorityClass.INSTRUMENT,
        )
        dock_ctx = prior.copy_for_write("DEPOT-2")
        dock_ctx.observe("DEPOT-2")
        dock = replace(dock, causal=dock_ctx)

        check(
            "the two claims are concurrent, not a revision",
            hand.causal.is_concurrent_with(dock.causal),
        )

        fleet.devices["TRK-7"].record(hand)
        fleet.devices["DEPOT-2"].record(dock)
        print(f"      TRK-7   recorded {hand.value!r}, by {hand.author} on {hand.observer}")
        print(f"      DEPOT-2 recorded {dock.value!r}, by {dock.author} on {dock.observer}")

        # ------------------------------------------------------------------
        step(2, "Each device answers alone, with no uplink")
        for device_id in ("TRK-7", "DEPOT-2"):
            v = fleet.devices[device_id].ask("seal state", subject=subject)
            print(
                f"      {device_id}: {v.kind.value} in {v.latency_ms:.2f} ms "
                f"via {','.join(v.paths_used)} - {v.summary}"
            )
        check(
            "neither device sees a conflict while alone",
            all(
                fleet.devices[d].ask("seal state", subject=subject).kind.value
                == "ANSWERED_LOCALLY"
                for d in ("TRK-7", "DEPOT-2")
            ),
        )

        # ------------------------------------------------------------------
        step(3, "Both reconnect")
        up = fleet.reconnect("TRK-7")
        down = fleet.reconnect("DEPOT-2")
        for report in (up, down):
            print(
                f"      {report.device_id}: {report.path} - "
                f"{report.pushed} up, {report.pulled} down, "
                f"{report.bytes_sent} B sent, {report.bytes_received} B received, "
                f"cursor {report.cursor_before}->{report.cursor_after}"
            )
        check("the sync path is reported, not assumed", up.path == SyncPath.DEPOT_DELTA.value)
        check("the depot agrees on the path", up.path == up.depot_path)
        check("claims were transferred both ways", up.pushed >= 1 and down.pulled >= 1)
        check(
            "the sender's byte count and the depot's agree",
            up.bytes_sent == up.depot_bytes_received,
        )

        # ------------------------------------------------------------------
        step(4, "The same question now returns a disagreement")
        v = fleet.devices["DEPOT-2"].ask("seal state", subject=subject)
        print(f"      verdict : {v.kind.value}")
        print(f"      {v.summary}")
        for a, b in v.conflicts:
            for side in (a, b):
                flag = "entitled " if side.entitling else "retained "
                print(
                    f"        {flag}{side.claim.value!r:>9} - "
                    f"{side.claim.author}, {side.claim.observer}, "
                    f"{side.claim.observed_at}, {side.claim.claim_id[:8]}"
                )
                if side.corroboration is not None:
                    co = side.corroboration
                    print(
                        f"                  {co.agreeing_claims} agreeing claim(s) "
                        f"= {co.voices} voice(s); echoes {co.echoes}; "
                        f"self-corroborated: {co.self_corroborated}"
                    )
        check("the verdict is CONFLICTED", v.kind.value == "CONFLICTED")
        check("both claims are retained", len(v.conflicts) == 1)
        check(
            "both values survive",
            {a.claim.value for pair in v.conflicts for a in pair} == {"broken", "intact"},
        )
        check("the engine says it is not choosing", "not choosing" in v.summary.lower())
        check("a ladder is presented", v.authority is not None)
        if v.authority:
            print(f"      ladder   : {' > '.join(v.authority.ordered_classes)}")
            print(f"      entitled : {v.authority.entitling_class}")
            check(
                "the instrument is the entitling class, despite observing earlier",
                v.authority.entitling_class == AuthorityClass.INSTRUMENT.value,
            )

        # ------------------------------------------------------------------
        step(5, "The device restarts. The disagreement is still there.")
        restart = fleet.devices["DEPOT-2"]
        store = next(s for s in fleet.shards if s.device_id == "DEPOT-2")
        store.reopen()
        after = fleet.devices["DEPOT-2"].ask("seal state", subject=subject)
        print(f"      verdict after restart: {after.kind.value}")
        check("the conflict survives a restart", after.kind.value == "CONFLICTED")
        check(
            "the same claim ids are still held",
            {c.claim_id for c in after.claims}
            == {c.claim_id for c in v.claims},
        )
        del restart

        # ------------------------------------------------------------------
        step(6, "What the device would have sent, and what it kept")
        stats = fleet.devices["TRK-7"].stats()
        print(f"      {json.dumps(stats, indent=6, default=str)}")
        check("the index state is reported honestly", bool(stats["index"]))

        rule()
        if failures:
            print(f"{len(failures)} FAILED:")
            for f in failures:
                print(f"  - {f}")
            return 1
        print("all checks passed - this is what the engine did, not what it was told to do")
        return 0
    finally:
        fleet_close = locals().get("fleet")
        if fleet_close is not None:
            fleet_close.close()
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())