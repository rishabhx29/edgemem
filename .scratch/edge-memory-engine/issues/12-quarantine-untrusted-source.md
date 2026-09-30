# 12: Quarantine a claim from an untrusted source

**What to build:** A claim arrives from a source the device does not trust. It does not join the device's memory alongside everything else â€” it is held apart, with the reason visible, and the operator can inspect what was held and why.

The device also shows when a single untrusted source has repeated itself into apparent agreement with itself, so a false pattern is visible before it is believed.

**Blocked by:** 04

**Status:** done

- [x] A claim from a low-trust source is quarantined rather than mixed into the answerable memory
- [x] The quarantine is inspectable, naming the claim and the reason it was held
- [x] A quarantined claim does not contribute to a verdict's supporting claims
- [x] The verdict reports the corroboration count per source class, so a source that has only corroborated itself is visible
- [x] Releasing a quarantined claim requires an explicit action, not the passage of time
- [x] A test asserts a low-trust claim is held, is inspectable, and does not appear among a verdict's supporting claims
