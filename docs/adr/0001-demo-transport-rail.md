# 0001: The demonstration's transport rail is not the question interface

Date: 2026-09-30

## Status

Accepted

## Context

The specification is unambiguous about the interface. The inspector "renders the
verdict and nothing else. It holds no information the seam does not produce."
Everything the page shows is filled from a single `ask` response, and there is no
second query path.

Applying that to the demonstration produced an immediate problem. The engine's
central claim is that a synchronisation happened *and* that a disagreement was
preserved across it. A demonstration that never shows the exchange has no way to
demonstrate the first half. The `SyncReport` carries the path that actually ran,
the number of claims each way, the cursor movement and the bytes over the socket,
and none of it appears in a `Verdict`.

The two honest options were to drop the exchange from the demonstration, or to
widen what the demonstration is allowed to show. Dropping it would have made the
page that exists to prove the product the one page that cannot show it working.

Widening the verdict was rejected. `Verdict` is the shape a caller programs
against, its four kinds are the product's vocabulary, and transport counters do
not belong in an answer to a question about a seal. Coupling the seam to the sync
path to get them there would have been worse: `DeviceLink` wraps `EdgeMemory`, so
the memory would have had to reach back around itself.

## Decision

The demonstration serves two kinds of payload and keeps them visibly distinct.

1. **The verdict panel** renders `Verdict.to_payload()` and nothing else. If a
   panel can show something, the verdict produces it. No panel on this page reads
   device memory, device statistics, the outbox, the ledger or the shard. Widening
   what a panel can show means widening the verdict, which is a change to the
   engine argued for on its own merits.

2. **The transport rail** renders `SyncReport.to_payload()`, is confined to the
   one step where an exchange occurs, and is labelled "transport, not part of the
   question interface" at the point of use rather than in a footnote.

The free-ask path sends no subject, so the engine infers one and reports the
subject it inferred. That makes the interface's weakest step visible instead of
hidden, which is the same standard applied to the transport rail.

## Consequences

- The specification is amended to distinguish the question interface, which is
  strict and single, from the demonstration's transport rail, which is not part
  of it and says so. The sentence "no second query path" still holds: the rail
  reports an event, it does not answer a question.
- A reader can challenge the verdict panel and the transport rail separately. One
  is the product; the other is the substrate.
- The rail is the only thing on the page that could be accused of being
  convenient. It is also the easiest thing to spot and the cheapest to remove, so
  its cost is bounded.
- If a future change makes an exchange produce a verdict — a device that refuses
  to report completion, say — that signal belongs in the verdict and this ADR
  should be revisited rather than extended.