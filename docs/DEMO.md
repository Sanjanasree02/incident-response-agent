# Demo script (about 4 minutes)

Story: "It is 2 a.m. and checkout is down. The on-call engineer has never seen this failure. Our agent has."

## Before the demo
1. Use a fresh demo bank so the story starts clean:
   `uv run python -m scripts.seed_memory --bank-id shopfast-incidents-demo1`, then set `HINDSIGHT_BANK_ID=shopfast-incidents-demo1` in `.env`.
   Rehearse on a different bank. `PAYMENT_GATEWAY_TIMEOUT` (step 4) is new only while the bank has never seen it: after one
   rehearsal the agent already remembers it.
2. Start ShopFast (8001), the storefront (8003) and the UI (`uv run streamlit run ui/app.py`).
3. Have `data/learning_curve.json` from a recent run (the chart in the "What the agent has learned" tab).
4. Open Claude Code in the repo folder with ShopFast running (for the MCP part).
5. Groq free tier allows 8,000 tokens per minute and each detection uses about 4,000. Detections made back to back can get
   `429 rate_limit_exceeded`. Leave about one minute between detections; the order below fills those gaps.

## 1. The problem (20 s)
- Storefront: buy running shoes, checkout works.
- `curl -X POST http://127.0.0.1:8001/admin/faults/DB_POOL_EXHAUST`. Checkout now fails with 503.
- "Somewhere in 25 past incidents, someone has fixed this before. Nobody remembers where."

## 2. Memory recalls the fix (40 s)
- UI: **Detect latest ShopFast incident**. No copy-paste: the agent probed the shop and opened the incident.
- It cites INC-1042 and the other pool-exhaustion incidents, proposes `rollback_payment_api`, and warns that restarting pods failed before.
- **Approve and run**. The agent verifies checkout is healthy and records the outcome in memory by itself.

## 3. It also remembers what failed (20 s)
- `curl -X POST http://127.0.0.1:8001/admin/faults/REDIS_TIMEOUT`. Adding to cart fails with 503. Detect.
- The agent cites INC-1051 from the seed history and proposes `disable_cart_analytics`. Two other actions look plausible,
  but the seed history records that both failed: raising the Redis client timeout (INC-1051) and failing over to the
  replica (INC-1088). The agent does not propose them.
- **Approve and run**. Healthy again. (LLM output varies: if it proposes nothing, run `disable_cart_analytics` as the
  engineer's choice.)

## 4. A failure it has never seen, then learns (60 s)
- `curl -X POST http://127.0.0.1:8001/admin/faults/PAYMENT_GATEWAY_TIMEOUT`. Checkout now fails with 504. Detect.
- "No similar past incident in memory. This is a generic answer." Low confidence. The seed history has no payment
  gateway timeout, so there is nothing to recall.
- The agent still proposes an action from the error log alone (in rehearsal: `raise_payment_gateway_timeout`).
  **Approve and run**. Checkout is healthy; the outcome is recorded in memory.
  If it proposes nothing or the wrong action, open **Run a different action (engineer's choice)** and run
  `raise_payment_gateway_timeout`. It is recorded the same way: "it learned from the engineer, not from a prompt".
- **Write postmortem (Hindsight reflect)** for this incident: summary, timeline, what went wrong, action items. This
  uses Hindsight, not Groq, so it also fills the one-minute gap before the next detection.
- Enable `PAYMENT_GATEWAY_TIMEOUT` again. Detect. The agent now cites the incident from a minute ago and proposes
  `raise_payment_gateway_timeout` itself, with "worked in 1 of 1 recorded attempts".

## 5. Living runbook (15 s)
- "What the agent has learned" tab: **Load living runbook**, a table Hindsight rewrites itself as outcomes arrive.

## 6. Proof it gets better (30 s)
- Learning curve chart: fixed by the agent's first action, round 1 vs later rounds, with memory and with memory off. Wrong actions on production go down only with memory.

## 7. Any agent can use this memory (20 s)
- In Claude Code: "Detect the latest ShopFast incident and fix it." It calls `detect_incident`, asks before `run_action`, then `write_postmortem`.
- "Same memory, same guardrails, from any agent or any service through the REST API."

## Backup lines if something fails
- Groq rate limit (`429`): the UI still shows recalled memory and "AI suggestion unavailable"; say "the memory works
  even when the model is down". Wait a minute, then detect again.
- Hindsight slow on reflect: show a postmortem downloaded during rehearsal.
