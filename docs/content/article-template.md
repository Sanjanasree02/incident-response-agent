# Article template

*Each team member writes their own article (required by the hackathon rules). Use this structure, keep the shared parts, and write the "What I built" part about your own work, in your own voice. Check the official content guide for length and where to publish. Replace everything in [brackets].*

---

# [Title. Example: "Our incident agent learned a fix from an engineer, and then used it without being asked"]

## The problem (shared, adapt freely)
Every on-call engineer knows this feeling. Checkout is down, the error log looks familiar, and somewhere in a postmortem from six months ago is the exact fix. Nobody can find it in time. An AI agent without memory is no better: it gives the same generic answer every time.

## What we built (shared)
For the Hindsight hackathon our team built an incident response agent for ShopFast, a mock e-commerce platform. The agent detects failures, recalls how similar incidents were fixed, proposes one safe runbook action, runs it only after a human approves, and checks that the shop is healthy again. Then it writes the outcome back to Hindsight memory, so the next similar incident gets the proven fix.

Hindsight features we use: `retain` and `recall` for incidents and their outcomes, `reflect` for postmortems, a mental model for a living runbook, directives for team rules, and observations for patterns across incidents.

## What I built (your part, in your own words)
[Pick the 2-3 pieces you worked on and tell the story: what was wrong or missing, what you built, and what changed. Examples of parts people worked on:]

- [Core agent: ShopFast and its faults, memory wrapper and seed history, Groq advisor, the act, verify and learn loop, automatic intake, the UI]
- [Learning from engineers: when the agent proposes nothing, an engineer's fix is recorded too, and the agent proposes it itself the next time]
- [Postmortems with `reflect`; the living runbook as a mental model; team rules as directives]
- [Measuring learning: a learning curve against a memory-off baseline, averaged over several runs]
- [MCP server and REST API, so Claude Code or any service can use the same memory]

## Proof that it learns (shared; use the final numbers)
A learning-curve script replays every fault for several rounds on a fresh memory bank, once with memory and once with memory switched off. [Fill in from `data/learning_curve.json` or the project overview PDF: first-action fix rate in round 1 vs later rounds, and wrong actions on production, memory on vs off.]

## What I learned (your own)
[2-3 honest lessons. Examples:]
- [Memory needs a write path from humans, not only from the agent.]
- [Measure learning against a memory-off baseline, or "it got better" is just a feeling.]
- [Keep guardrails in code: an allow-list, a human approval and verification after every action.]

[Repo link] · Built with Hindsight by Vectorize and Groq.
