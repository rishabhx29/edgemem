"""Gate 4: does a shard write survive a hard process kill without an explicit flush?

The device is terminated rather than shut down, because a shutdown runs the
flushes that a battery coming off a truck does not. If the shard buffers its
writes, a killed device loses the day's work no matter what its outbox promises,
and the outbox has to be a write-ahead log rather than merely a delivery queue.

Run with ``.\\.venv\\Scripts\\python.exe scripts\\probe_kill_durability.py``.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

CHILD = textwrap.dedent(
    """
    import json, sys, time
    from pathlib import Path
    from edgemem.domain import Claim, utcnow
    from edgemem.memory import EdgeMemory
    from edgemem.store import ShardStore

    shard_dir = Path(sys.argv[1])
    flush = sys.argv[2] == "flush"
    store = ShardStore(shard_dir, device_id="TRK-7")
    store.open()
    mem = EdgeMemory(store)
    now = utcnow()
    ids = []
    for i in range(3):
        c = Claim(
            subject="trailer T-114 seal",
            attribute="state",
            value=f"broken {i}",
            author="operator-7",
            observer="handheld",
            device_id="TRK-7",
            observed_at=now,
            recorded_at=now,
        )
        mem.record(c)
        ids.append(c.claim_id)
    if flush:
        store.flush()
    print(json.dumps(ids), flush=True)
    time.sleep(120)
    """
)


def main() -> None:
    import tempfile

    for flush in ("no-flush", "flush"):
        with tempfile.TemporaryDirectory() as td:
            child = Path(td) / "child.py"
            child.write_text(CHILD, encoding="utf-8")
            shard = Path(td) / "mutable"
            proc = subprocess.Popen(
                [sys.executable, str(child), str(shard), flush],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={
                    "PYTHONPATH": str(SRC),
                    "PATH": "",
                    "SYSTEMROOT": str(Path.home()),
                },
                cwd=str(ROOT),
            )
            ids = proc.stdout.readline().strip()
            proc.kill()
            proc.wait(timeout=30)
            err = proc.stderr.read()
            print(f"--- {flush} ---")
            print("child ids:", ids)
            if err.strip():
                print("child stderr:", err[:2000])

            reopen = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    textwrap.dedent(
                        f"""
                        import json
                        from pathlib import Path
                        from edgemem.memory import EdgeMemory
                        from edgemem.store import ShardStore
                        s = ShardStore(Path({str(shard)!r}), device_id="TRK-7")
                        s.open()
                        m = EdgeMemory(s)
                        v = m.ask("seal state", subject="trailer T-114 seal")
                        print(json.dumps({{"kind": v.kind.value, "n": len(v.claims),
                          "ids": [c.claim_id for c in v.claims],
                          "stats": m.stats()}}))
                        """
                    ),
                ],
                capture_output=True,
                text=True,
                env={"PYTHONPATH": str(SRC), "PATH": "", "SYSTEMROOT": str(Path.home())},
                cwd=str(ROOT),
            )
            print("after kill:", reopen.stdout.strip()[:800])
            if reopen.returncode != 0:
                print("reopen stderr:", reopen.stderr[-2000:])


if __name__ == "__main__":
    main()
