# 18: Subject inference never declines on a non-empty device

**Type:** research

**Status:** needs-triage

**What was observed:** `_infer_subject` takes the subject of the best row a search
returns. With any claim on the device a search always returns a row, so the
`subject is None` branch at `memory.py:403` is unreachable in normal operation.
Measured against the demonstration's devices, every one of these questions resolved
to a subject and was then answered:

| question | inferred subject | verdict |
| --- | --- | --- |
| `fuel card for tractor 12` | `trailer T-114 seal` | `CONFLICTED` |
| `zqxjkv nonsense token` | `trailer T-114 seal` | `CONFLICTED` |
| `what colour is the sky` | `reefer probe` | `ANSWERED_LOCALLY` |
| `the driver home address` | `reefer probe` | `ANSWERED_LOCALLY` |

The second row is the serious one: a question with no lexical or semantic
relationship to anything held came back with a conflict about a trailer seal.
`memory.py:425` states that answering the wrong aspect is "the confident-wrong-answer
failure the device exists to avoid", and a subject inferred from nonsense defeats
that for the subject as well as the attribute.

**Why it is not fixed here:** a relevance floor below which the engine returns no
subject would change `ask()` semantics for every caller, and several existing tests
assert on answers to questions whose wording is deliberately loose. It is a design
decision about how strict inference should be, not a bug with an obvious patch.

**What was done instead:** the demonstration does not stage this as a feature. A
seventh step which would have shown the engine declining was cut, and the reason is
recorded in `scripts/story.py`. The interface's free-ask path sends no subject and
displays the subject the engine inferred, so a visitor can reproduce this
themselves rather than take the project's word for it that it does not happen.

**Suggested next step:** decide whether inference should have a floor, and if so what
question wording is expected to fail. `tests/test_demo_ui.py` already asserts that the
inferred subject is reported and that no cited claim disagrees with it, so a fix will
be visible without new instrumentation.