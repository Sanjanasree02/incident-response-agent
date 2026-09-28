# Demo script (about 3 minutes)

Story: "It is 2 a.m. and checkout is down. The on-call engineer has never seen this failure. Our agent has."

## Before the demo
1. Use a fresh demo bank so the story starts clean:
   `uv run python -m scripts.seed_memory --bank-id shopfast-incidents-demo1`, then set `HINDSIGHT_BANK_ID=shopfast-incidents-demo1` in `.env`.
2. Start ShopFast (8001), the storefront (8003) and the UI (`uv run streamlit run ui/app.py`).
3. Have `data/learning_curve.json` from a recent run (the chart in the "What the agent has learned" tab).
4. Open Claude Code in the repo folder with ShopFast running (for the MCP part).

## 1. The problem (20 s)
- Storefront: buy running shoes, checkout works.
- `curl -X POST http://127.0.0.1:8001/admin/faults/DB_POOL_EXHAUST`. Checkout now fails with 503.
- "Somewhere in 25 past incidents, someone has fixed this before. Nobody remembers where."

## 2. Memory recalls the fix (40 s)
- UI: **Detect latest ShopFast incident**. No copy-paste: the agent probed the shop and opened the incident.
- It cites INC-1042 and the other pool-exhaustion incidents, proposes `rollback_payment_api`, and warns that restarting pods failed before.
- **Approve and run**. The agent verifies checkout is healthy and records the outcome in memory by itself.

## 3. A failure it has never seen (40 s)
- Enable `REDIS_TIMEOUT`. Detect. "No similar past incident": generic answer, no action it trusts.
- Open **Run a different action (engineer's choice)**, run `disable_cart_analytics`. Healthy again; recorded.
- Enable `REDIS_TIMEOUT` again. Detect. The agent now cites the incident from a minute ago and proposes `disable_cart_analytics` itself, with "worked in 1 of 1 recorded attempts".
- "It learned from the engineer, not from a prompt."

## 4. Postmortem and living runbook (30 s)
- **Write postmortem (Hindsight reflect)**: summary, timeline, what went wrong, action items, related past incidents. Saved with the incident, so the next recall brings the lessons back.
- "What the agent has learned" tab: **Load living runbook**, a table Hindsight rewrites itself as outcomes arrive.

## 5. Proof it gets better (30 s)
- Learning curve chart: fixed by the agent's first action, round 1 vs later rounds, with memory and with memory off. Wrong actions on production go down only with memory.

## 6. Any agent can use this memory (20 s)
- In Claude Code: "Detect the latest ShopFast incident and fix it." It calls `detect_incident`, asks before `run_action`, then `write_postmortem`.
- "Same memory, same guardrails, from any agent or any service through the REST API."

## Backup lines if something fails
- Groq rate limit: the UI still shows recalled memory; say "the memory works even when the model is down".
- Hindsight slow on reflect: show a postmortem downloaded during rehearsal.
