# 11: The authority ladder on escalation

**What to build:** A conflict escalates showing who would be entitled to settle it, without settling it. A calibrated dock scanner outranks a handheld note; a handheld note outranks a third-party feed. The engine shows the ladder, shows how many claims support each side, and names who could resolve it.

The ladder is data. A platform engineer changes it, versions it and diffs it without redeploying and without touching engine code. Recency is never an input.

**Blocked by:** 08

**Status:** ready-for-agent

- [ ] A conflicted verdict presents the versioned source-class ladder that would settle it
- [ ] The verdict names who is entitled to settle it, and resolves nothing itself
- [ ] The verdict reports how many claims support each side of the conflict
- [ ] The ladder is held as versioned data, and changing it requires no engine code change and no redeploy
- [ ] Recency is not an input to any ordering the ladder performs
- [ ] A test asserts a conflicted verdict presents the ladder, names the entitling source class, and still resolves nothing
