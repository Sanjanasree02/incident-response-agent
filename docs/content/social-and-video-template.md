# Social post and video templates

*Each team member posts their own social post and video (required by the hackathon rules). Use these as a starting point, change the wording to your own, and lead with the part you built. Check the official content guide for platforms, tags and length. Replace everything in [brackets].*

## Social post (LinkedIn or X)
Checkout is down at 2 a.m. The fix is in a postmortem from six months ago. Nobody can find it.

For the #Hindsight hackathon our team built an incident response agent that remembers every incident and gets better at fixing them:

- Recalls how similar incidents were fixed, and which fixes failed
- Proposes one allow-listed action, runs it only after a human approves, and verifies the shop
- Learns from engineers too: when a person picks a different fix, the agent uses it next time on its own
- Writes blameless postmortems with Hindsight reflect, and keeps a living runbook that updates itself
- Works from Claude Code through an MCP server

[One line on your own part: "I built ..."]

[Result line from the final run: "With memory, fixes on the first action went from X% to Y%, and wrong actions on production from A to B. With memory off: ..."]

[Repo link] @Vectorize #AIAgents #SRE #MCP [tag teammates]

## Video (60-90 s)
Suggested shots; spend more time on the part you built.

1. (0-10 s) Storefront, checkout fails. Voice: "Checkout is down. Has this happened before?"
2. (10-25 s) Detect incident. The agent cites INC-1042, proposes a rollback, and warns that restarting pods failed before. Approve. Healthy.
3. (25-45 s) A new failure: Redis timeout. The agent has no memory of it. The engineer picks the fix. Trigger it again: the agent proposes that fix itself, citing the incident from a minute ago.
4. (45-60 s) Postmortem written by Hindsight reflect; the living runbook table; team rules.
5. (60-75 s) Learning-curve chart: with memory vs memory off.
6. (75-90 s) Claude Code: "Detect the latest ShopFast incident and fix it." Close: [your own closing line].
