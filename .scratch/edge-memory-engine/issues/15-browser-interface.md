# 15: A browser interface that renders the verdict and nothing else

**What to build:** Four pages served by one Python process. An overview that argues
the case, a demonstration that runs the scenario step by step, a reference page that
renders all four verdicts, and an evidence page carrying the measured findings. Every
answer panel is filled from a single `ask` response; nothing on any page is authored
demo data.

**Blocked by:** 14

**Status:** done

- [x] The scenario lives in one place (`scripts/story.py`) so the command-line demonstration and the interface cannot tell each other a different story
- [x] Steps are a pure function of the steps before them, so any step is an entry point and running one twice changes nothing
- [x] The guided scenario reaches all four verdicts, including a correction that returns both answers
- [x] `web/src/lib/types.ts` is transcribed from each `to_payload()`, and the build fails when a key is renamed
- [x] A test reads the built bundle and fails if any string the engine produced appears in it
- [x] No page reads device memory, statistics, the outbox, the ledger or the shard
- [x] The free-ask path sends no subject and shows the subject the engine inferred, so the weakest step is visible
- [x] The served files are confined to the build directory, and traversal is refused
- [x] Reduced motion collapses every transition; figures are tabular; no colour is load-bearing on its own