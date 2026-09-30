# edgemem — disagreement-durable edge memory

An offline-first semantic memory engine for devices that lose connectivity, built on
the in-process vector engine from Qdrant's Edge release.

**The problem it exists to solve.** Two devices that recorded the same thing differently
while they were apart end up with one record silently destroyed. The central store keeps
whichever arrived last, the device is told to garbage-collect its local copy as "already
synced", and nothing is reported. The evidence is gone and nobody finds out.

**What it does instead.** A claim is an attributed assertion, never an overwritten fact.
Two claims that cannot both be true both survive, both keep their attribution, and the
engine escalates rather than choosing. Whether a claim may leave the device is a decision
with a stated reason, so a fleet operator can challenge it.

## The one interface

Asking a question returns exactly one labelled verdict, and the verdict's shape does not
change with its kind:

```python
device.ask("what state is the trailer T-114 seal in", subject="trailer T-114 seal")
```

| Verdict | When |
| --- | --- |
| `ANSWERED_LOCALLY` | the device's own memory settled it; it says which claims it used and why each was usable |
| `CONFLICTED` | claims cannot both be true; both are returned, with the authority ladder presented and not applied |
| `UNRESOLVED_CLOUD_REQUIRED` | the answer exists but the device may not use it; it names what is missing and prices it in bytes |
| `CORRECTED` | the same question now answers differently; both answers are returned and the cause is named |

Everything the interface renders comes from the verdict. There is no second query path,
which is why the demonstration and the test suite are the same artefact.

## Try it

Requires Python 3.12 (the published library ships `win_amd64`/`abi3` wheels from 3.10 up,
and no source distribution, so the wheel is the build input).

```powershell
uv venv --python 3.12
$env:VIRTUAL_ENV = "$PWD\.venv"
uv pip install qdrant-edge-py==0.8.0 pytest
```

The demonstration, end to end, on two real devices over a real socket:

```powershell
.\.venv\Scripts\python.exe scripts\demo_scenario.py
```

It exits non-zero if any check about the outcome fails, so it is also a smoke test. The
side-by-side contrast with a single-writer last-write-wins strategy:

```powershell
.\.venv\Scripts\python.exe scripts\demo_comparison.py
```

The browser interface, which renders the same scenario step by step. Build the front end
once, then the Python process serves it:

```powershell
cd web; npm install; npm run build; cd ..
.\.venv\Scripts\python.exe scripts\demo_ui.py
```

Then open http://127.0.0.1:8770. Every verdict, byte count and latency on those pages was
produced by the engine during the request that rendered it; a test fails the build if any
of the scenario's own wording appears in the bundle.

The full suite (~5 minutes; every shard is closed and destroyed on teardown):

```powershell
$j = Start-Job -ScriptBlock { & .\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider }; Wait-Job $j -Timeout 900 | Out-Null; Receive-Job $j; Remove-Job $j -Force
```

## What was measured rather than assumed

Every claim below came from a probe in `scripts/`, reproducible, with raw output under
`var/gates/`. Findings that corrected an earlier belief are in `docs/gates.md`.

| Question | Answer |
| --- | --- |
| Does the wheel install and import on Windows? | yes, every release, CPython ≥ 3.10 |
| Does the release fuse dense and sparse at query time? | **yes** — so nothing hand-rolls fusion |
| Does a blocking index build stall a concurrent query? | no at 50k points, **yes at 120k** — index builds belong in a worker process |
| Are keyword scores usable before indexing? | yes, non-degenerate and identical to post-index |
| Does a shard write survive a hard process kill? | **no, without a flush** — all 3 claims lost. Hence the write-ahead log |
| Is the vendor's server→edge snapshot path usable? | **no** — see below |

**The vendor snapshot path does not work in this release, and that is a measured finding.**
A Qdrant server runs in WSL2 and is reachable, so it was tested rather than presumed
unavailable. A full snapshot restores dense vectors and the shard answers dense queries
from it, but the **sparse vectors do not survive the snapshot** (`payload_schema` comes
back empty, BM25 returns zero hits before and after `optimize()`), and
`snapshot_manifest()` on a restored shard raises `Shard is not initialized`. The vendor
mechanism is built around that manifest call, so partial snapshots are unavailable. The
engine implements its own incremental delta exchange and **reports which path ran on every
sync**. That is a finding about the library, not a shortcut taken because a runtime was
missing.

## Two defects this design had to fix before it was honest

Both were found by auditing the code rather than by a test failing, and both are the exact
failure the engine exists to prevent.

**Claim ids were unrecoverable.** Only one read path returned a claim's own id; search,
scroll and facets returned the numeric storage key. A claim found by search could not be
refetched, so a citation identified nothing.

**Point ids folded 128 claims onto one point.** Deriving a storage key from a 15-character
prefix of the claim id is 128-to-1 for any two ids sharing that prefix. Random ids never
hit it; a padded counter or a timestamp-prefixed id collapsed **20,000 distinct claims onto
one point and silently destroyed all but the last.** Now the key is a full-width digest and
`upsert` raises rather than destroy a foreign claim.

## Claims this project does not make

The vendor's published single-writer pattern is **not** said to lose data or drop unsynced
writes. That claim is false: its reference implementation drains its queue before setting
its collection watermark. The accurate and narrower statement, which is what
`scripts/demo_comparison.py` demonstrates, is that such a pattern has **no merge
semantics** — one storage key per logical record, so two concurrent writes leave one
survivor and one overwritten.

Hours-of-service duty-log regulation does not govern cargo or cold-chain records and is
never cited. The anchor is the electronic-records provision requiring that record changes
not obscure previously recorded information.

## Layout

```
src/edgemem/
  domain.py     the vocabulary: Claim, CausalContext, Verdict, Corroboration
  store.py      shard wrapper, native hybrid retrieval, honest index reporting
  residency.py  the policy; every decision carries the signals that produced it
  memory.py     EdgeMemory - the seam, and the four verdicts
  outbox.py     durable acknowledged queue, doubling as a write-ahead log
  sync.py       incremental exchange in both directions, over a real socket
  trust.py      quarantine and corroboration, engine-side
  schema.py     the pack abstraction: labels, ladder, subject model, citation
  comparison.py the labelled last-write-wins contrast, evidence only
fixtures/       verticals, deliberately outside the engine package
scripts/
  story.py      the scenario: claims, fleet, and the order of the steps
  demo_scenario.py  the narration and the checks over that scenario
  demo_ui.py    the interface: a JSON API over the same scenario, plus static files
  demo_comparison.py  the labelled last-write-wins contrast
  probe_*.py    the substrate probes behind docs/gates.md
web/            the front end (Vite, React, TypeScript, Tailwind v4)
  src/lib/types.ts  the wire contract, transcribed from to_payload()
docs/gates.md   every measured finding, including the ones that corrected us
docs/adr/       decisions and the option each one rejected
```

The engine contains no vocabulary belonging to any industry, and a test reads the forbidden
words from the packs themselves, so the net cannot quietly narrow. Binding a vertical is
`EdgeMemory(store, pack=...)` — no engine change.

`fixtures/` lives outside `src/edgemem/` on purpose: a vertical inside the engine package
would be a vertical the engine imports.

`scripts/story.py` holds the scenario so the command-line demonstration and the browser
interface cannot tell each other a different story. `demo_scenario.py` narrates it;
`demo_ui.py` serves it. `web/src/lib/types.ts` is transcribed field-for-field from each
`to_payload()`, so renaming a key in the engine breaks the front-end build rather than
rendering `undefined` in front of somebody judging the project.

## Licence

MIT.