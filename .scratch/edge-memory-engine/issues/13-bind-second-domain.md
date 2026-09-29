# 13: Bind a second domain with a schema pack

**What to build:** A second vertical — substation torque specifications — bound to the same engine by supplying display labels, an authority ladder, a subject model and a regulatory citation. No engine code changes.

This makes the engine's domain-agnosticism structural rather than a claim. A substation technician offline in a basement faces the same shape of problem as a truck driver in a dead zone: an OEM specification and a site standard disagree, the site standard is a confidential local deviation, and the central office must not learn of it until it is reviewed.

**Blocked by:** 04

**Status:** ready-for-agent

- [ ] A second vertical works by supplying labels, ladder, subject model and citation only
- [ ] No engine source file changes to bind the second vertical
- [ ] A test asserts the engine's source contains no vocabulary belonging to any particular industry
- [ ] The second vertical's residency and conflict behaviour matches the first, driven only by its schema pack
- [ ] A conflicted claim in the second vertical presents that vertical's own ladder
- [ ] The citation used by each vertical is that vertical's own, and neither is hard-coded into the engine
