"""The interface: a server that renders the verdict and nothing else.

This process does two things. It serves the built front end, and it answers four
JSON requests, all of which exist to put a real ``ask`` response in front of
somebody:

- ``GET  /api/state``  what the scenario has done so far
- ``POST /api/step``   run the scenario up to a step
- ``POST /api/reset``  throw the fleet away and build a new one
- ``GET  /api/ask``    ask one device a question, with no subject supplied

There is deliberately no endpoint for device memory, statistics, or anything else
the engine holds. The interface's panels are filled from a verdict, and the only
way to widen that is to widen what a verdict carries — which is a change to the
seam, made in the engine, argued for on its own merits. A second read path here
would quietly undo that.

The one payload that is not a verdict is the sync report, and it is here because
a demonstration which never says that the exchange happened has nothing to show.
It is labelled as transport wherever it appears, and the deviation is recorded in
docs/adr/0001-demo-transport-rail.md.

Run it:

    .\\.venv\\Scripts\\python.exe scripts\\demo_ui.py

Then open http://127.0.0.1:8770
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "scripts", ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import fixtures.trucks as trucks  # noqa: E402
from story import DOCK, STEPS, TRUCK, Fleet  # noqa: E402

UI_PORT = 8770
DIST = ROOT / "web" / "dist"

CLIENT_ROUTES = frozenset({"/", "/demo", "/how", "/evidence"})

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class Console:
    """The scenario, held as state so the interface can step through it.

    A step is a pure function of the steps before it, so entering the interface
    at step 4 means running 1, 2 and 3 first. That is what lets every step be an
    entry point in the page without the page having to defend against a scenario
    run out of order or run twice.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.root = Path(tempfile.mkdtemp(prefix="edgemem-ui-"))
        self.fleet: Fleet | None = None
        self.reached = 0
        self.performed: list[tuple[Any, Any]] = []

    # -- the scenario -----------------------------------------------------

    def run_to(self, n: int) -> None:
        if n <= self.reached and self.fleet is not None:
            return
        with self._lock:
            self._dispose()
            self.fleet, self.performed = _run_to(self.root, n)
            self.reached = n

    def reset(self) -> None:
        with self._lock:
            self._dispose()
            self.reached = 0
            self.performed = []

    def _dispose(self) -> None:
        if self.fleet is not None:
            self.fleet.close()
            self.fleet = None
        shutil.rmtree(self.root, ignore_errors=True)
        self.root = Path(tempfile.mkdtemp(prefix="edgemem-ui-"))

    # -- what the page renders --------------------------------------------

    def state(self) -> dict:
        with self._lock:
            steps: list[dict] = []
            seen: dict[str, bool] = {}
            for step, result in self.performed:
                asks = [a.to_payload() for a in result.asks]
                for payload in asks:
                    seen[payload["verdict"]["kind"]] = True
                steps.append(
                    {
                        "n": step.n,
                        "title": step.title,
                        "note": step.note,
                        "asks": asks,
                        "exchanges": [r.to_payload() for r in result.exchanges],
                        "link_up": bool(self.fleet and self.fleet.link_up),
                        "expects": step.expects,
                    }
                )
            return {
                "seeded": self.reached >= 1,
                "reached": self.reached,
                "steps": steps,
                "devices": [
                    {"id": TRUCK, "link_up": bool(self.fleet and self.fleet.link_up)},
                    {"id": DOCK, "link_up": bool(self.fleet and self.fleet.link_up)},
                ],
                "vertical": {
                    "label": trucks.PACK.labels.claim,
                    "ladder_version": trucks.PACK.ladder.version,
                    "citation": trucks.PACK.citation,
                },
                "seen": {
                    kind: seen.get(kind, False)
                    for kind in (
                        "ANSWERED_LOCALLY",
                        "CONFLICTED",
                        "UNRESOLVED_CLOUD_REQUIRED",
                        "CORRECTED",
                    )
                },
            }

    def ask(self, device_id: str, question: str) -> dict:
        with self._lock:
            if self.fleet is None:
                self.run_to(len(STEPS))
            fleet = self.fleet
            assert fleet is not None
            if device_id not in fleet.devices:
                raise KeyError(f"no device {device_id!r}")
            return fleet.ask(device_id, question).to_payload()

    def close(self) -> None:
        with self._lock:
            self._dispose()
            shutil.rmtree(self.root, ignore_errors=True)


def _run_to(root: Path, n: int):
    from story import run_to

    return run_to(root, n)


_REFERENCE: dict[str, Any] = {}
_REFERENCE_LOCK = threading.Lock()


def reference() -> dict:
    """One run of the whole scenario, kept for the pages that are not the demo.

    ``/demo`` drives its own fleet step by step. The other three pages need
    nothing but finished verdicts, and building a fleet per page view to get
    them would put a shard and a socket in the path of reading a paragraph. So
    the run happens once, its payloads are kept, and the fleet is closed.

    Every figure these pages show comes from here. The pages do not author
    verdicts, and they do not summarise one either.
    """
    with _REFERENCE_LOCK:
        if _REFERENCE:
            return _REFERENCE
        from story import hand_seal, dock_seal, run_to

        root = Path(tempfile.mkdtemp(prefix="edgemem-ref-"))
        fleet = None
        try:
            fleet, performed = run_to(root, len(STEPS))
            by_kind: dict[str, dict] = {}
            exchanges: list[dict] = []
            for _step, result in performed:
                for ask in result.asks:
                    payload = ask.to_payload()
                    by_kind.setdefault(payload["verdict"]["kind"], payload)
                exchanges.extend(r.to_payload() for r in result.exchanges)

            from edgemem.comparison import naive_lww_survivors

            prior = _prior_for_comparison()
            naive = naive_lww_survivors([hand_seal(prior), dock_seal(prior)])

            _REFERENCE.update(
                {
                    "verdicts": by_kind,
                    "exchanges": exchanges,
                    "kinds": sorted(by_kind),
                    "packs": _packs(),
                    "comparison": {
                        "written": naive.written,
                        "keys": naive.keys,
                        "survivors": len(naive.survivors),
                        "overwritten": len(naive.overwritten),
                        "describe": naive.describe(),
                        "lines": list(naive.lines()),
                        "engine_survivors": _engine_survivor_count(by_kind),
                    },
                }
            )
            return _REFERENCE
        finally:
            if fleet is not None:
                fleet.close()
            shutil.rmtree(root, ignore_errors=True)


def _prior_for_comparison():
    from story import _prior

    return _prior()


def _engine_survivor_count(by_kind: dict) -> int:
    """How many claims the engine kept out of the same pair that wrote the two.

    Read off the CONFLICTED verdict, which is the only place the engine states
    what it holds about the disagreement, so the contrast on the evidence page
    is between two counts of the same thing rather than two claims about
    different ones.
    """
    conflicted = by_kind.get("CONFLICTED")
    if not conflicted:
        return 0
    seen: set[str] = set()
    for pair in conflicted["verdict"]["conflicts"]:
        for side in ("a", "b"):
            seen.add(pair[side]["claim_id"])
    return len(seen)


def _packs() -> list[dict]:
    """Both verticals, as the engine holds them.

    Read from the pack objects rather than written into the page, so the ladder
    and the citation shown are the ones a device would actually be bound to.
    """
    import fixtures.substation as substation

    out = []
    for name, pack in (("trucks", trucks.PACK), ("substation", substation.PACK)):
        out.append(
            {
                "key": name,
                "claim": pack.labels.claim,
                "attribute": pack.labels.attribute,
                "conflict": pack.labels.conflict,
                "id_label": pack.subject_model.id_label,
                "default_attribute": pack.subject_model.default_attribute,
                "attributes": dict(pack.subject_model.attributes),
                "citation": pack.citation,
                "ladder_version": pack.ladder.version,
                "ladder": list(pack.ladder.ordered()),
            }
        )
    return out


def handler_for(console: Console):
    class Handler(BaseHTTPRequestHandler):
        server_version = "edgemem"
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: dict) -> None:
            self._send(
                200, json.dumps(payload, default=str).encode("utf-8"),
                "application/json",
            )

        def _static(self, path: str) -> None:
            """Serve a built file, refusing anything outside ``web/dist``.

            The front end is a single bundle with a client-side router, so every
            route resolves to the same ``index.html`` and the router picks the
            page from the path. Only the four routes the bundle actually declares
            are answered that way; anything else is a 404 rather than a guessed
            page, so a mistyped URL does not silently render the overview.

            Asset paths are resolved and then checked against the dist root, so a
            traversal in the URL cannot reach the repository or the temporary
            directory the scenario runs in. This binds to loopback and is not
            exposed to a network, but a demonstration that serves the repository
            is still a demonstration nobody should run in front of a stranger.
            """
            if path in CLIENT_ROUTES:
                target = DIST / "index.html"
            else:
                candidate = (DIST / path.lstrip("/")).resolve()
                try:
                    candidate.relative_to(DIST.resolve())
                except ValueError:
                    self._send(403, b"forbidden", "text/plain; charset=utf-8")
                    return
                target = candidate
            if not target.is_file():
                self._send(404, b"not built", "text/plain; charset=utf-8")
                return
            self._send(200, target.read_bytes(), MIME.get(target.suffix, "text/plain"))

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/api/state":
                    self._json(console.state())
                elif parsed.path == "/api/ask":
                    query = parse_qs(parsed.query)
                    device = (query.get("device") or [""])[0]
                    question = (query.get("q") or [""])[0].strip()
                    if not device or not question:
                        self._send(
                            400, b"device and q are both required", "text/plain"
                        )
                        return
                    self._json(console.ask(device, question))
                elif parsed.path == "/api/health":
                    self._json({"ok": True, "built": (DIST / "index.html").is_file()})
                elif parsed.path == "/api/verdicts":
                    self._json(
                        {
                            "verdicts": reference()["verdicts"],
                            "kinds": reference()["kinds"],
                        }
                    )
                elif parsed.path == "/api/packs":
                    self._json({"packs": reference()["packs"]})
                elif parsed.path == "/api/comparison":
                    self._json(
                        {
                            "comparison": reference()["comparison"],
                            "exchanges": reference()["exchanges"],
                        }
                    )
                else:
                    self._static(parsed.path)
            except KeyError as exc:
                self._send(404, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
            except Exception as exc:  # noqa: BLE001 - the page must show failures
                self._send(
                    500,
                    f"{type(exc).__name__}: {exc}".encode("utf-8"),
                    "text/plain; charset=utf-8",
                )

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._send(400, b"bad json", "text/plain; charset=utf-8")
                return
            try:
                if parsed.path == "/api/step":
                    n = int(body.get("n") or 0)
                    if not 1 <= n <= len(STEPS):
                        self._send(400, b"no such step", "text/plain")
                        return
                    console.run_to(n)
                    self._json(console.state())
                elif parsed.path == "/api/reset":
                    console.reset()
                    self._json(console.state())
                else:
                    self._send(404, b"not found", "text/plain")
            except Exception as exc:  # noqa: BLE001
                self._send(
                    500,
                    f"{type(exc).__name__}: {exc}".encode("utf-8"),
                    "text/plain; charset=utf-8",
                )

    return Handler


def main() -> int:
    if not (DIST / "index.html").is_file():
        print("The interface has not been built yet.")
        print("  Run:  cd web; npm install; npm run build")
        print(f"  Expected: {DIST}")
        return 1

    console = Console()
    server = ThreadingHTTPServer(("127.0.0.1", UI_PORT), handler_for(console))
    server.daemon_threads = True

    print(f"edgemem on http://127.0.0.1:{UI_PORT}")
    print("  /          what this is")
    print("  /demo      the scenario, step by step")
    print("  /how       the one interface and the four verdicts")
    print("  /evidence  what was measured, including what failed")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.shutdown()
        console.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())