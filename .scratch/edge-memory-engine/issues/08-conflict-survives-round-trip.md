# 08: A conflict survives the round trip

**What to build:** Two devices, both honest, both unable to reach each other, both record incompatible claims about the same subject and attribute. One observes a broken seal. The other, an instrument, records it intact. Neither is lying about their own moment.

When they reconnect, the engine does not pick a winner. Both claims survive with their author, observer and timing intact, and a question about that subject returns a verdict of CONFLICTED showing both sides. The conflict is a persistent object that outlives the sync that revealed it, and it survives a restart.

**Blocked by:** 07

**Status:** ready-for-agent

- [ ] Two claims about the same subject and attribute, written on separate devices while disconnected, are recognised as concurrent rather than as an update
- [ ] After sync, both claims are retained and neither is deleted
- [ ] A question about that subject returns a verdict of CONFLICTED carrying both sides
- [ ] Each side of the conflict names its author, its observer, its device, and the time observed
- [ ] The verdict states that the engine is not resolving the disagreement
- [ ] The conflict survives a process restart and is still listable
- [ ] A test asserts both sides survive a sync and a restart, through the question interface
