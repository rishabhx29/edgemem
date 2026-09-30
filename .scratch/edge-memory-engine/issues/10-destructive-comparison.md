# 10: The destructive comparison, as a test

**What to build:** A demonstration of the contrast, reproducible by a judge who does not believe the team. Running the same concurrent-write scenario through the published single-writer pattern loses one of the two claims. Running it through this engine loses neither.

This is the evidence for the engine's central claim, and it is the only place the destructive path is ever executed. It is labelled as a comparison everywhere it appears and is never a production path.

**Blocked by:** 08

**Status:** done

- [x] The concurrent-write scenario is runnable as a headless script that prints the surviving claim set for each strategy
- [x] Under the comparison strategy, one of the two concurrent claims is lost
- [x] Under this engine, both concurrent claims survive
- [x] The comparison is labelled as a comparison in the code, in the product, and in any output it produces
- [x] The comparison path is not reachable from any production code path
- [x] A test asserts the difference, and the script's output is reproducible from a clean checkout
- [x] The README states the contrast as absence of merge semantics, and does not claim the published pattern loses unsynced data
