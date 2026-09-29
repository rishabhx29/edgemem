# 02: Answer a question offline

**What to build:** A field operator with no network records a claim about a subject, then asks a question about that subject and receives a labelled verdict that cites the claim it rests on, with its author, observer and the times observed and recorded. This is the first complete path through the whole system — capture, store, search, verdict, interface — and it is the tracer bullet every later ticket extends.

The interface shows the device's indexing state honestly from the start, so a user can tell when results are served by exhaustive scan rather than an index.

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] A claim can be recorded while the uplink is down, with author, observer, subject, attribute, time observed and time recorded all retained
- [ ] A question about the subject returns a verdict of ANSWED_LOCALLY naming the claims it rests on
- [ ] The verdict carries each cited claim's attribution and timing
- [ ] The device reports whether the underlying shard is indexed, and the interface shows it
- [ ] The interface renders the verdict and holds no information the verdict does not carry
- [ ] One test drives the whole path through the question interface and asserts only on verdict label, cited claims, attribution and indexing state
