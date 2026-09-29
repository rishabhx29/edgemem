"""Re-measure the blocking-stall question at a size where there is real work.

The first Gate 2 probe ran optimize() over 400 points, which completed in
0.11ms. A window that short cannot detect a stall, so it could not support the
conclusion it was used for. This probe measures at a size where the call
actually takes seconds, and does so for the raw shard and for the store
wrapper, because the wrapper's lock is a separate variable.
"""

from __future__ import annotations

import json
import statistics
import threading
import time
from pathlib import Path

from edgemem.domain import Claim, utcnow
from edgemem.store import ShardStore

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "var" / "gates"


def make(n: int, store: ShardStore) -> None:
    now = utcnow()
    for batch_start in range(0, n, 2000):
        batch = [
            Claim(
                subject=f"subject {i}",
                attribute="reading",
                value=f"reading {i} on asset {i % 97}",
                author=f"operator-{i % 13}",
                observer="instrument",
                device_id="TRK-7",
                observed_at=now,
                recorded_at=now,
            )
            for i in range(batch_start, min(batch_start + 2000, n))
        ]
        store.upsert(batch)


def hammer(store: ShardStore, stop: threading.Event, out: list[float]) -> None:
    probe = "reading on asset"
    while not stop.is_set():
        t0 = time.perf_counter()
        store.search(probe, limit=5)
        out.append((time.perf_counter() - t0) * 1000)
        time.sleep(0.005)


def measure(store: ShardStore, label: str, n: int) -> dict:
    latencies: list[float] = []
    stop = threading.Event()
    thread = threading.Thread(target=hammer, args=(store, stop, latencies), daemon=True)
    thread.start()
    time.sleep(0.5)
    t0 = time.perf_counter()
    did_work = store.optimize()
    optimize_ms = (time.perf_counter() - t0) * 1000
    time.sleep(0.3)
    stop.set()
    thread.join(timeout=5)

    if not latencies:
        return {"label": label, "error": "no query completed"}
    ordered = sorted(latencies)
    median = statistics.median(ordered)
    worst = ordered[-1]
    return {
        "label": label,
        "points": n,
        "optimize_returned_work": did_work,
        "optimize_ms": round(optimize_ms, 1),
        "queries": len(ordered),
        "median_ms": round(median, 3),
        "p95_ms": round(ordered[int(len(ordered) * 0.95)], 3),
        "worst_ms": round(worst, 3),
        "worst_over_median": round(worst / median, 1) if median else None,
        "stalled": worst > 50.0 and median > 0 and worst / median > 10,
    }


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    results = []

    for n in (50_000, 120_000):
        path = WORK / f"stall_{n}"
        store = ShardStore(path, device_id="TRK-7")
        store.open()
        make(n, store)
        results.append(measure(store, f"store_lock_narrowed_{n}", n))
        store.close()
        store.destroy()

    # Control: the same query volume under a lock held across the whole call,
    # which is what the store did before the fix.
    path = WORK / "stall_control"
    store = ShardStore(path, device_id="TRK-7")
    store.open()
    make(50_000, store)
    latencies: list[float] = []
    stop = threading.Event()
    thread = threading.Thread(target=hammer, args=(store, stop, latencies), daemon=True)
    thread.start()
    time.sleep(0.5)
    t0 = time.perf_counter()
    with store._lock:
        store.optimize()
    optimize_ms = (time.perf_counter() - t0) * 1000
    time.sleep(0.3)
    stop.set()
    thread.join(timeout=5)
    if latencies:
        ordered = sorted(latencies)
        median = statistics.median(ordered)
        results.append(
            {
                "label": "control_lock_held_across_optimize_50000",
                "points": 50_000,
                "optimize_ms": round(optimize_ms, 1),
                "queries": len(ordered),
                "median_ms": round(median, 3),
                "worst_ms": round(ordered[-1], 3),
                "worst_over_median": round(ordered[-1] / median, 1) if median else None,
                "stalled": ordered[-1] > 50.0 and median > 0 and ordered[-1] / median > 10,
            }
        )
    store.close()
    store.destroy()

    out = WORK / "stall_recheck.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    print(f"\nwritten -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
