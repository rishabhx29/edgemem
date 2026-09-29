# Disagreement-Durable Edge Memory Engine

**Status:** ready-for-agent

**Problem statement:** Code Cubicle 6.0, Problem Statement 03 — "AI-Powered Edge Memory & Intelligence Platform" on Qdrant Edge.

---

## Problem Statement

Field devices work in places with no network. A refrigerated truck in a dead zone, a technician in a substation basement, a nurse on an isolated ward. While offline they keep working, and keep recording what they observe.

The trouble starts at the moment they reconnect.

Today, two devices that recorded the same thing differently while apart end up with **one** of the records silently destroyed. The server keeps whichever arrived last, and the device is instructed to garbage-collect its own local copy as "already synced." No error is raised. No one is told. The evidence that a seal was broken, that a patient was dosed, that a spec was deviated from — simply ceases to exist, and the system reports success.

Three related failures make this worse:

1. **Disagreement is resolved silently.** A claim is not a fact — it is an assertion somebody made. When two assertions about the same subject cannot both be true, the correct behaviour is to keep both, attribute both, and escalate. No system does this. Last-write-wins, and the complexity of who wrote last.
2. **Systems that choose what to sync make the choice opaquely.** Everything syncs, or nothing does. The few that choose treat the choice as a configuration detail, not a decision requiring a stated justification. A fleet operator cannot answer "why did this leave the device?" — and neither can an auditor, a regulator, or the person who was wronged by the answer.
3. **Authority is ignored.** "Newer" is treated as "truer." But a calibrated dock scanner and a rumour are not equally credible, and a device that is ten minutes ahead of the server may be quoting a source the server has never heard of.

The result is that edge systems are untrustworthy in exactly the situations where trust matters most — intermittent connectivity, distributed physical work, regulated records — and the failure is invisible.

## Solution

An offline-first memory engine in which **a claim is an attributed assertion, never an overwritten fact**, and every residency decision carries its own justification.

The engine is domain-agnostic. It knows only claims, subjects, conflicts, verdicts and residency. Cold-chain telematics is the fixture it is demonstrated against; substation inspections, clinical records and site surveys bind to the same engine through a schema pack.

Four things follow, and together they are the product.

**Nothing is destroyed by agreement.** When two devices assert incompatible things about the same subject and attribute, both claims survive with their author, observer and time. The engine does not pick a winner and says so. Disagreement is a first-class, addressable object, not a transient during a merge.

**Every residency decision is justified.** Whether a claim stays on the device, syncs, or is marked cloud-only is a deterministic decision, and it emits a human-readable reason naming the signals that fired and the values that crossed their thresholds. The reason is part of the output, not a debug log.

**Recency is not authority.** Sources are ranked by an explicit, versioned authority ladder. A calibrated scanner outranks a handheld note; a handheld note outranks a third-party feed. When a conflict is escalated, the engine shows the ladder and states who would be entitled to settle it — without settling it itself.

**The device can be made to answer, and can be shown to have answered.** Asking a question returns one of four labelled verdicts rather than a bare result: it was answered from local memory and why that was permitted; it is conflicted and here are both assertions; it cannot be answered yet and here is exactly what it would send and how many bytes; or it was answered differently after synchronising, with the difference shown. A system that is aware of its own uncertainty is a different product from a system that returns a confident vector similarity score.

The residency policy and the conflict register are the same subsystem viewed from two sides. When a local claim stays on the device, the cloud is left holding an attested position it cannot fully see. The engine's job is to make that absence **legible** rather than silent.

## User Stories

### Recording claims while offline

1. As a field operator, I want to record what I observe as a claim, so that my observation is captured even with no connectivity.
2. As a field operator, I want the device to timestamp my claim's observation separately from when it was recorded, so that a delayed upload does not misstate when I saw it.
3. As a field operator, I want my claim attributed to me and to the instrument that produced it, so that its credibility is assessable.
4. As a field operator, I want the device to record the location and context of a claim, so that I can later answer "where was this seen?"
5. As a field operator, I want to know immediately that a claim was saved, so that I trust the device before I leave the site.
6. As a field operator, I want to continue working with no error state when offline, so that the loss of network is not an interruption to my work.

### Deciding what stays local

7. As a fleet operator, I want each claim to be classified as local, syncing, or cloud-only, so that only what should move actually moves.
8. As a fleet operator, I want to see the reason for every classification, so that I can challenge a decision I disagree with.
9. As a fleet operator, I want the reason to name the signal that fired and the threshold it crossed, so that the decision is auditable rather than merely asserted.
10. As a fleet operator, I want sensitivity to be able to veto a sync regardless of other signals, so that a claim naming a customer cannot leave on the strength of being urgent.
11. As a fleet operator, I want urgency to be adjustable, so that I can trade completeness against my data allowance.
12. As a fleet operator, I want the outbound byte count to respond to policy changes, so that I can see the cost of a policy before I adopt it.
13. As a fleet operator, I want a byte budget the device respects, so that a long offline period does not exhaust the allowance.
14. As a compliance auditor, I want to know which claims never left a device, so that I can account for information that the central system does not hold.
15. As a compliance auditor, I want the count of residentially-sealed claims to be a number I can state, so that a retention question has a quantitative answer.

### Answering questions while offline

16. As a field operator, I want to ask a question in my own words, so that I do not have to remember an exact keyword.
17. As a field operator, I want an exact term to still match even when I phrase it differently, so that I trust the results.
18. As a field operator, I want the answer fast enough to use while working, so that I do not abandon the search.
19. As a field operator, I want every answer to cite the claims behind it, so that I can check it.
20. As a field operator, I want the answer to tell me which of my local claims was used, so that I know what the device is relying on.
21. As a field operator, I want the device to say "I don't know" rather than return its least-bad match, so that I am not misled by a confident wrong answer.
22. As a field operator, I want a partial answer flagged as partial, so that I do not mistake a local view for the whole picture.

### Conflicts and authority

23. As a fleet operator, I want to be told when two claims about the same subject cannot both be true, so that I learn about a disagreement rather than inheriting a silent overwrite.
24. As a fleet operator, I want both conflicting claims retained after synchronisation, so that neither observation is lost.
25. As a fleet operator, I want each side of a conflict to carry who asserted it, from where, and when, so that I can weigh them.
26. As a fleet operator, I want the device to refuse to pick a winner automatically, so that an unreviewed decision is never made on my behalf.
27. As a fleet operator, I want the conflict routed to a person, so that resolution is a human act.
28. As a fleet operator, I want to see the authority ladder that would settle a conflict, so that the basis for resolution is explicit.
29. As a fleet operator, I want to see how many claims support each side of a conflict, so that a single rumour is visibly weaker than a corroborated observation.
30. As a compliance auditor, I want conflicts to persist as inspectable objects rather than being resolved in transit, so that the record shows that a disagreement existed.
31. As a compliance auditor, I want every change to a claim to leave the prior value visible, so that no change obscures what was previously recorded.

### Synchronising

32. As a fleet operator, I want the device to work while the uplink is down, so that connectivity is not on the critical path.
33. As a fleet operator, I want unsent claims to survive the device restarting, so that a crash does not cost me the day's work.
34. As a fleet operator, I want the device to retry automatically when connectivity returns, so that I do not have to babysit the sync.
35. As a fleet operator, I want only what changed to be transferred, so that a reconnect is cheap and fast.
36. As a fleet operator, I want to see how many bytes a sync moved, so that I can verify the incrementality.
37. As a fleet operator, I want sync to be safe to interrupt, so that a dropped connection mid-sync loses nothing.
38. As a fleet operator, I want writes held briefly while a snapshot is applied, so that a local claim is not clobbered by a restore.
39. As a fleet operator, I want the device to show how long it has been offline, so that I understand the gap in my central data.
40. As a fleet operator, I want a device that has been away longest to be identifiable, so that I know what my fleet has missed.

### Answers changing over time

41. As a fleet operator, I want the same question to be answerable differently after a sync, so that I can see what the device learned.
42. As a fleet operator, I want the before and after shown side by side, so that the change is legible rather than inferred.
43. As a fleet operator, I want to know which claim caused the answer to change, so that I understand what was learned.
44. As a field operator, I want an answer to be correctable, so that my own later observation supersedes my earlier one without destroying it.
45. As a field operator, I want superseded claims to remain discoverable as history, so that I can see how a situation evolved.
46. As a fleet operator, I want to know how stale the device's answer is, so that I can weigh it appropriately.

### Trust and provenance

47. As a fleet operator, I want claims from untrusted sources marked as such, so that they are not treated as equal to instrument readings.
48. As a fleet operator, I want a low-trust claim to be quarantined rather than silently mixed in, so that it does not contaminate results.
49. As a fleet operator, I want to inspect what was quarantined and why, so that the gate is not a black box.
50. As a fleet operator, I want to know when a single untrusted source has corroborated itself into apparent consensus, so that a false pattern is visible before it is trusted.
51. As a compliance auditor, I want every claim to name its source and the path it took to reach the central system, so that chain of custody is reconstructable.

### Inspecting the system

52. As a fleet operator, I want to see what the device remembers, so that I can judge whether its answers are sound.
53. As a fleet operator, I want to see the device's memory as a map I can navigate, so that structure is visible at a glance.
54. As a fleet operator, I want to filter memory by source, subject and residency, so that I can narrow to what I care on.
55. As a fleet operator, I want to see sync status per device, so that I know which devices are behind.
56. As a fleet operator, I want to see the device's indexing state honestly, so that I understand when results are served by exhaustive scan rather than an index.
57. As a fleet operator, I want a live feed of decisions the system made and why, so that I can review behaviour rather than infer it.
58. As a field operator, I want to see why an answer could not be completed, so that I know what is missing.
59. As a field operator, I want to see what the device would need in order to answer, so that I know whether waiting will help.
60. As a field operator, I want the interface to make the difference between local and cloud-sourced facts obvious, so that I know how much to trust each.

### The engine as a product

61. As a platform engineer, I want to define an authority ladder as data, so that I can version and diff it rather than redeploy to change it.
62. As a platform engineer, I want to bind a new domain by supplying labels, authority classes and a citation, so that a new vertical does not require engine changes.
63. As a platform engineer, I want the engine to have no knowledge of any particular industry, so that its behaviour generalises.
64. As a platform engineer, I want policy weights configurable without a rebuild, so that a fleet can tune to its own costs.
65. As a platform engineer, I want a decision to be reproducible given the same inputs and ladder, so that a decision can be explained after the fact.
66. As a platform engineer, I want every decision to record the inputs that produced it, so that it can be replayed and checked.
67. As a platform engineer, I want the system to never require a network call to make a residency or conflict decision, so that offline operation is structural rather than incidental.
68. As a platform engineer, I want to be able to reproduce the platform's own behaviour as a labelled comparison, so that its guarantees are demonstrable rather than claimed.

## Implementation Decisions

### Runtime and substrate

Python 3.12, with `qdrant-edge-py` pinned to an exact version. The published distribution is a binary wheel with no source distribution, so the exact wheel is vendored into the repository as a build input. The engine uses only the in-process shard type; the client/server library is explicitly rejected because its local mode cannot interoperate with a server, which would make the synchronisation requirement unachievable.

### Process topology

The edge device is its own OS process communicating over a real socket, with a network control the demonstrator can physically sever. A second process is not an implementation detail — it is what makes the offline claim verifiable rather than asserted. The device process owns its shards exclusively; all writes serialise through it behind a lock.

### Shard topology

Two shards per device, following the published dual-shard pattern: a mutable, deliberately unindexed shard absorbing local writes immediately, and an immutable shard restored from a server snapshot and carrying the HNSW index. Queries span both and are merged and de-duplicated. A read-mostly peer consumes deltas only and holds no write path.

### Vector representation

One dense content vector per claim. Every other signal — author, observer, source class, sensitivity, salience, urgency, residency, time observed, time asserted — is a payload field with a payload index, not a named vector. Named vectors force an index build per vector and turn the vector store into a key-value store.

### Search

Whether the current release performs dense/sparse fusion at query time is established by running the shipped example before any of it is written. If query-time fusion exists, it is used. Hand-written fusion is written only for the case it cannot express: ranking where one leg is conflict-suppressed, or where a leg is drawn from a divergent local set. Keyword search uses the engine's own built-in embedder; a third-party keyword implementation is rejected.

### Causal metadata

Every claim carries a causal context sufficient to detect concurrency between two claims about the same subject and attribute, kept bounded so that metadata grows with the number of contributing devices rather than with the number of edits.

### Conflict representation

A conflict is a persistent object over the pair of claims, not a value computed during a merge. Each side retains its assertion independently, so a conflict can be listed, cited, resolved and audited long after the sync that revealed it. Nothing in the conflict path resolves a value by recency.

### Authority ladder

An ordered list of source classes with an attestation strength, held as versioned data rather than code. Resolution is never automated: the ladder is what the engine *shows* when escalating, and it determines the order in which corroborating sources are presented. Recency is not an input.

### Residency policy

A deterministic scorer over sensitivity, salience, urgency, redundancy against the device's own memory, and remaining byte budget. Sensitivity is a veto, not a weight. The output is a residency and a mandatory reason string naming each signal that fired, its value, and the threshold crossed. The policy is pure, so a decision is reproducible from the recorded inputs and the ladder version.

### Durable outbox

Unsent claims are persisted in an acknowledged on-disk queue, not an in-process queue, so that a restart does not discard a day's work. Delivery is at-least-once with idempotent replay keyed on the claim identifier.

### Synchronisation

Upstream deltas are pulled driven by a monotonic per-collection sequence the device retains, so only claims the device has not seen cross the link. Writes are held and the outbox flushed before a restore is applied. Synchronisation annotates and merges; it never deletes a claim to reclaim space. Claim removal is a separate, explicit, audited operation.

**Vendor-path note.** The vendor's server→edge delta is a partial snapshot driven by the shard manifest, which requires a running Qdrant server. Where no server is available, the engine implements the equivalent delta exchange against the depot endpoint and records which path is active. This is surfaced in the product, not hidden.

### Destructive comparison

The published single-writer pattern is implemented as a labelled comparison mode solely to demonstrate the contrast, and never as a production path. It is named as such wherever it appears.

### Verdict

Asking a question about a subject yields exactly one of four outcomes: answered from local memory, conflicted, not answerable yet, or corrected after synchronising. Every outcome carries the claims it rests on, their attribution, the device's residency reasoning where relevant, and — where the answer is incomplete — the content and byte cost of what would be needed. The interface is the same regardless of which outcome is returned.

### UI is a projection

The inspector renders the verdict and nothing else. It holds no information the seam does not produce. This keeps one seam honest and means the demo is a rendering of the test suite's actual output rather than a separate artefact.

### Domain abstraction

The engine's only concepts are claim, subject, attribute, conflict, verdict, residency and source class. A vertical supplies a schema pack: display labels, the authority ladder, the subject model, and the regulatory citation. No engine code changes when the vertical changes.

### No model in the decision path

Residency and conflict resolution are deterministic and reproducible. A language model, where used at all, narrates outcomes and does not decide them. This is a deliberate inversion of the common pattern and is stated explicitly in the product.

### Citations

The regulatory anchor is the electronic-records provision requiring that record changes not obscure previously recorded information, together with the signature-manifestation requirement for attribution. Hours-of-service duty-log regulation is explicitly not used: it does not govern cargo or cold-chain records and citing it would be a category error.

### Honest substrate reporting

Indexing state is derived from the shard's own reported counts and shown in the interface, so a user can see when results are served by exhaustive scan rather than an index. Latency and byte figures are measured, not estimated, and appear in the product and in the submission.

## Testing Decisions

### What makes a good test here

Every test drives the device through the one seam — ask a question about a subject, receive a verdict — and asserts only on what the caller can observe: the verdict's label, the claims it carries, their attribution, the residency reason, the byte counts, and whether claims survived. No test asserts on shard internals, index structure, scoring arithmetic, or the behaviour of any third-party component. A test that would still pass if the entire storage layer were replaced is testing the right thing.

### The seam

One seam, the question interface. The conflict arbiter sits behind it rather than beside it — the conflicted verdict *is* the arbiter's output — and the UI is a projection of it. No second seam is opened, because every behaviour the requirements demand is reachable through this one, and a second seam is a second thing to keep honest.

### The scenario harness

The primary test is the whole story as a script, not a set of isolated cases: record claims, sever the link, record more, introduce a disagreement, restore the link, ask the same question before and after. This is deliberately the same script as the demonstration, so a green suite is a runnable demonstration and the demonstration is continuously tested. Isolated cases then subdivide it, but no test invents a path the interface does not expose.

### The comparison is a test

One test asserts the destructive pattern loses a concurrent claim and ours does not. This is the only place the destructive path is exercised, and it is exercised as evidence.

### What must be covered

Offline answering with both retrieval paths named; residency classification with its reason; sensitivity veto overriding urgency; survival of both sides of a conflict across a restart; survival of unsent claims across a restart; incremental transfer after reconnect; the corrected verdict differing from the pre-sync verdict; a local-only claim remaining absent from the central system and being reported as such; and a claim from an untrusted source being quarantined.

### Prior art

None. The repository is greenfield with no existing tests, no fixtures and no harness, so there is nothing to follow and no conventions to match. The harness and its fixtures are established here.

### Spikes are not tests

Four environment questions — whether the published wheel installs on the target platform, whether the current release already fuses, whether a blocking shard operation stalls the interface, and whether keyword scores remain usable before indexing — are substrate checks, not tests. They have pass/fail answers that change the plan, and they run before the test suite exists. They are recorded as plan items, not assertions.

## Out of Scope

- Native mobile clients, any Rust component, and any cross-language bridge. A laptop or browser process running the real in-process engine is an honest edge device and is claimed as exactly that.
- Vision, image, audio or multimodal embeddings. This is a separate problem statement.
- Any model in the decision path.
- Authentication, authorisation, multi-tenancy and user accounts. None appear in the requirements.
- A general dashboard, landing page, or decorative interface. Interface work is spent only where it is evidence.
- A custom keyword index, a custom approximate-nearest-neighbour index, or any reimplementation of a capability the engine already ships.
- Automated evaluation harnesses and metrics dashboards that were not requested.
- Byte-range-resumable uploads. Incremental transfer already delivers the incrementality guarantee.
- Deduplication of claims by identity. Overwrite detection is required; identity resolution is not.
- Any claim that the published single-writer pattern loses unsynced data. The contrast is about the absence of merge semantics, not about durability.

## Further Notes

### Three claims that must not appear in the submission or the demo

First, that the published synchronisation pattern destroys unsynced local writes — the reference implementation flushes its queue before establishing its collection watermark, so the claim is false and is disprovable in seconds from the vendor's own repository. The accurate statement is that the pattern has no merge semantics and is a single-writer protocol. Second, that it de-duplicates by wall-clock timestamp — de-duplication is by point identifier and the timestamp is a local collection threshold. Third, that hours-of-service duty-log regulation requires preservation of cold-chain seal evidence — it governs driver duty records and says nothing about cargo.

### A fourth claim is to be verified rather than asserted

Whether the current release performs dense and sparse fusion at query time must be established by running the shipped example. If it does, hand-written fusion is redundant work *and* a visible misreading of the documentation.

### The structural risk that most threatens the demonstration

Every shard operation is blocking and synchronous, and applying a restore rewrites files in place. If the binding holds the interpreter lock, a reconnect stalls the whole process mid-demo. This must be tested before anything else is built, because the remedy — moving restore application to a worker process — changes the architecture.

### Second structural risk

Keyword scoring on the unindexed mutable shard is unverified and could return degenerate results, which would visibly break the offline search that the demonstration depends on.

### Known competitors

Two other teams are working this problem statement. One uses a client/server library in local mode, which cannot synchronise with a server and therefore cannot satisfy the synchronisation requirement. The other has a well-executed native pipeline whose own specification routes all data through the central server, meaning it has no residency policy at all. The differentiator here is the residency decision with its stated reason, combined with the conflict register — neither competitor addresses either.

### Open questions to resolve with the organisers

Whether the judging rubric is available, and whether an edge device must be physical hardware. Both change the plan: the first changes how the work is scored, the second changes whether the two-process topology is sufficient.

### Terminology note

A claim is an assertion somebody made, not a fact. This is load-bearing. An earlier framing in which a physical event such as a broken seal was modelled as an editable field was rejected precisely because an event that happened cannot be superseded by a later assertion about it, and modelling it as an editable field manufactures a conflict in order to have something to resolve.

### Environment constraint discovered during planning

No container runtime is available on the development machine, so the vendor's server→edge partial-snapshot path cannot be exercised during development. The engine therefore implements its own delta exchange against the depot endpoint and records which path is active. Upgrading to the vendor snapshot path is a configuration change, not a rewrite.
