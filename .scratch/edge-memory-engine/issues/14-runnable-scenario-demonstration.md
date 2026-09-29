# 14: One runnable scenario that is the demonstration

**What to build:** The whole story as one script that runs start to finish: claims recorded, uplink cut, a disagreement created, uplink restored, the same question asked before and after. The destructive comparison runs in the same execution. Measured figures — latency, bytes moved, bytes withheld — appear in the output rather than being asserted separately.

The demonstration is the test suite's output, not a separate artefact. A green suite is a runnable demonstration, which means the demonstration is continuously tested and cannot rot.

**Blocked by:** 05, 08, 09, 10

**Status:** ready-for-agent

- [ ] One script runs the full story end to end and exits non-zero on any failed assertion
- [ ] The scenario covers all four verdicts: answered locally, conflicted, unresolved, corrected
- [ ] The destructive comparison runs within the same execution and its output is labelled as a comparison
- [ ] Latency, bytes moved and bytes withheld are measured during the run and appear in its output
- [ ] A rehearsal script exists that runs the scenario and prints the narration cues
- [ ] The interface renders the scenario's real output, with no separately authored demo data
- [ ] A README states the regulatory citation, the contrast as absence of merge semantics, and the active sync path
