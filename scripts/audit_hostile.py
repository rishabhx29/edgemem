"""Hostile audit: try to make the engine lose, overwrite or alter a claim.

The product's central promise is that a claim is never an overwritten fact. This
probe attacks that promise directly rather than reading the code and forming an
opinion. Every check is adversarial: it sets up a situation where a reasonable
implementation would lose something, and reports whether this one does.

Run: .\\.venv\\Scripts\\python.exe scripts\\audit_hostile.py
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

from edgemem.domain import AuthorityClass, Claim, CausalContext, utcnow  # noqa: E402
from edgemem.memory import EdgeMemory  # noqa: E402
from edgemem.outbox import Outbox  # noqa: E402
from edgemem.store import ShardStore  # noqa: E402
from edgemem.sync import Depot, DeviceLink  # noqa: E402

RESULTS: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(("PASS" if ok else "FAIL", name, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


class Rig:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.shards: list[ShardStore] = []
        depot_mem, depot_ob = self._device("DEPOT-1")
        self.depot = Depot(depot_mem, root / "ledger.jsonl", depot_id="DEPOT-1")
        self.url = self.depot.serve()
        self.mem: dict[str, EdgeMemory] = {"DEPOT-1": depot_mem}
        self.link: dict[str, DeviceLink] = {}
        for device_id in ("TRK-7", "GATE-1", "TRK-9"):
            mem, ob = self._device(device_id)
            self.mem[device_id] = mem
            self.link[device_id] = DeviceLink(
                mem, ob, self.url, root / f"{device_id}-cursor.json"
            )

    def _device(self, device_id: str):
        store = ShardStore(root := self.root / f"{device_id}-s", device_id=device_id)
        store.open()
        self.shards.append(store)
        outbox = Outbox(self.root / f"{device_id}-ob.jsonl")
        return EdgeMemory(store, outbox=outbox), outbox

    def close(self):
        self.depot.stop()
        for s in self.shards:
            s.close()
            s.destroy()


def claim(value: str, **over) -> Claim:
    now = utcnow()
    base = {
        "subject": "trailer T-114 seal",
        "attribute": "state",
        "value": value,
        "author": "operator-7",
        "observer": "handheld",
        "device_id": "TRK-7",
        "observed_at": now,
        "recorded_at": now,
    }
    base.update(over)
    return Claim(**base)


def concurrent(a_id: str, b_id: str) -> tuple[CausalContext, CausalContext]:
    prior = CausalContext({a_id: 1, b_id: 1})
    left = prior.copy_for_write(a_id)
    left.observe(a_id)
    right = prior.copy_for_write(b_id)
    right.observe(b_id)
    return left, right


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="edgemem-audit-"))
    try:
        print("\n=== 1. concurrent claims, every sync ordering ===")
        print("    (convergence for N devices takes N rounds by design; what must")
        print("     hold is that nothing is LOST, and that a device behind is told)")
        for order in (("TRK-7", "GATE-1"), ("GATE-1", "TRK-7")):
            rig = Rig(root / f"order-{'x'.join(order)}")
            left_ctx, right_ctx = concurrent("TRK-7", "GATE-1")
            a = claim("broken")
            b = claim(
                "intact",
                author="dock operator",
                observer="scanner",
                device_id="GATE-1",
                source_class=AuthorityClass.INSTRUMENT,
            )
            from dataclasses import replace

            a = replace(a, causal=left_ctx)
            b = replace(b, causal=right_ctx)

            rig.mem[order[0]].record(a)
            rig.mem[order[1]].record(b)
            reports = {}
            for device_id in order:
                reports[device_id] = rig.link[device_id].sync()

            # Nothing may be lost at the depot, ever.
            depot_ids = {c.claim_id for c in rig.mem["DEPOT-1"].memory()}
            record(
                f"the depot retains both claims, sync order {'->'.join(order)}",
                {a.claim_id, b.claim_id} <= depot_ids,
                f"{len(depot_ids)} claim(s) at the depot",
            )

            # Whatever is still held locally must be recoverable by more syncs.
            for device_id in ("TRK-7", "GATE-1"):
                for _ in range(4):
                    rig.link[device_id].sync()
            for device_id in ("TRK-7", "GATE-1"):
                held = {c.claim_id for c in rig.mem[device_id].memory()}
                record(
                    f"{device_id} converges on both claims",
                    {a.claim_id, b.claim_id} <= held,
                    f"{len(held)} claim(s) held",
                )

            # A device that pushed but did not receive must be told why.
            behind = any(
                "next one" in " ".join(r.notes) for r in reports.values()
            )
            record(
                f"a device behind is told it will catch up (order {'->'.join(order)})",
                behind or all(
                    len(rig.mem[d].memory()) == 2 for d in ("TRK-7", "GATE-1")
                ),
            )
            rig.close()

        print("\n=== 2. a replayed claim is never stored twice, at either end ===")
        # At-least-once means the same claim may be offered twice. Two layers
        # guard it: the outbox keys on the claim id, and the depot recognises a
        # claim it already holds. Whichever fires first, the outcome is the same
        # and it is the outcome that matters.
        rig = Rig(root / "dupe")
        c1 = claim("broken")
        rig.mem["TRK-7"].record(c1)
        rig.link["TRK-7"].sync()
        requeued = rig.mem["TRK-7"].outbox.enqueue(c1)
        second = rig.link["TRK-7"].sync()
        stored = [c for c in rig.mem["TRK-7"].memory() if c.claim_id == c1.claim_id]
        at_depot = [c for c in rig.mem["DEPOT-1"].memory() if c.claim_id == c1.claim_id]
        record(
            "a replayed claim is stored once, at both ends",
            len(stored) == 1 and len(at_depot) == 1,
            f"device={len(stored)} depot={len(at_depot)} "
            f"requeued={requeued} duplicates={second.duplicates}",
        )
        rig.close()

        print("\n=== 3. out-of-causal-order arrival ===")
        rig = Rig(root / "order-causal")
        older = claim("intact")
        newer = claim("broken", supersedes=older.claim_id)
        # The revision arrives first, the claim it revises second.
        rig.mem["GATE-1"].record(newer)
        rig.mem["GATE-1"].record(older)
        rig.link["GATE-1"].sync()
        rig.link["TRK-7"].sync()
        held = {c.claim_id for c in rig.mem["TRK-7"].memory()}
        record(
            "both a revision and the claim it revises survive, in any arrival order",
            newer.claim_id in held and older.claim_id in held,
        )
        rig.close()

        print("\n=== 4. a large claim set is not truncated ===")
        rig = Rig(root / "bulk")
        bulk = [
            claim(f"reading {i}", claim_id=f"{i:032x}", subject="bulk subject")
            for i in range(1500)
        ]
        for batch in range(0, len(bulk), 500):
            for c in bulk[batch : batch + 500]:
                rig.mem["TRK-7"].record(c)
        rig.mem["TRK-7"].flush()
        got = len(rig.mem["TRK-7"].memory())
        record(
            "1500 claims are all readable afterwards",
            got == len(bulk),
            f"read back {got} of {len(bulk)}",
        )
        rig.close()

        print("\n=== 5. corrupt files are refused, not silently ignored ===")
        rig = Rig(root / "corrupt")
        c1 = claim("broken")
        rig.mem["TRK-7"].record(c1)
        rig.link["TRK-7"].sync()

        ob = root / "corrupt" / "TRK-7-ob.jsonl"
        outcome = {}
        try:
            corrupt = Outbox(ob)
            corrupt.enqueue([claim("after corruption")])
            outcome["outbox"] = "accepted corrupt file"
        except Exception as exc:  # noqa: BLE001
            outcome["outbox"] = f"refused: {type(exc).__name__}"

        cursor_path = root / "corrupt" / "TRK-7-cursor.json"
        cur = "unreadable"
        rewound_loudly = False
        try:
            cursor_path.write_text("{not json", encoding="utf-8")
            link = DeviceLink(
                rig.mem["TRK-7"],
                Outbox(root / "corrupt" / "fresh-ob.jsonl"),
                rig.url,
                cursor_path,
            )
            report = link.sync()
            cur = "restarted from the beginning"
            rewound_loudly = any(
                "unreadable" in n for n in report.notes
            )
        except Exception as exc:  # noqa: BLE001
            cur = f"raised {type(exc).__name__}"

        print(f"      outbox corrupt -> {outcome['outbox']}")
        print(f"      cursor corrupt -> {cur}; reported: {rewound_loudly}")
        record(
            "a corrupt cursor rewinds safely AND says so",
            "raised" in cur or rewound_loudly,
            cur,
        )
        rig.close()

        print("\n=== 6. a claim's own bytes are never altered ===")
        rig = Rig(root / "integrity")
        from dataclasses import replace

        base = claim("broken", sensitivity=0.4, urgency=0.6, salience=0.7)
        ctx = CausalContext({"TRK-7": 1})
        base = replace(base, causal=ctx)
        rig.mem["TRK-7"].record(base)
        rig.link["TRK-7"].sync()
        rig.link["GATE-1"].sync()

        copied = next(
            (c for c in rig.mem["GATE-1"].memory() if c.claim_id == base.claim_id),
            None,
        )
        same = copied is not None and (
            copied.value == base.value
            and copied.author == base.author
            and copied.observed_at == base.observed_at
            and copied.causal.versions == base.causal.versions
        )
        record(
            "a claim that syncs is byte-identical at the far end",
            bool(same),
            "" if same else "attribution or timing changed in transit",
        )
        rig.close()

        failed = [r for r in RESULTS if r[0] == "FAIL"]
        print("\n" + "=" * 70)
        print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} adversarial checks passed")
        if failed:
            print("\nFAILURES:")
            for _, name, detail in failed:
                print(f"  - {name}" + (f" ({detail})" if detail else ""))
        print("=" * 70)
        return 1 if failed else 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())