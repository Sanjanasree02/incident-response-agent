# Incident Response Agent: project overview

An on-call agent for ShopFast (a mock e-commerce platform) that remembers every production incident in Hindsight and gets measurably better at fixing them. Built for the Hindsight hackathon with Hindsight Cloud and Groq (`openai/gpt-oss-120b`).

**Result:** CURVE_HEADLINE

## The problem
When production breaks, the fix is often in an old postmortem or in one engineer's head. On-call engineers repeat fixes that failed before, and every team rediscovers the same lessons. An agent without memory gives the same generic answer each time.

## What the agent does
1. **Detects** a failure by probing ShopFast's endpoints like a monitor, and opens an incident from ShopFast's latest alert. No copy-paste.
2. **Recalls** similar past incidents from Hindsight and cites them by ID, with the fixes that worked and the fixes that failed.
3. **Suggests** a root cause, ordered fix steps, steps to avoid, and one allow-listed runbook action. It follows the team's rules, which are stored in memory.
4. **Acts** only after a human approves, then verifies that the shop is healthy. The engineer can choose a different action instead.
5. **Learns:** the verified outcome, including failed attempts, is written back to memory automatically. That includes fixes chosen by the engineer.
6. **Writes the postmortem** with Hindsight `reflect` over the whole incident history, and stores it with the incident.
7. **Keeps a living runbook**, a Hindsight mental model that rewrites itself as outcomes arrive.

Other agents and services can use the same memory through an MCP server (Claude Code, Cursor) and a REST API.

<!-- diagram:architecture -->

## How Hindsight memory is used

| Hindsight feature | Where | What it gives the agent |
| --- | --- | --- |
| `retain` | Seed history, every verified outcome, engineer fixes, postmortems (`update_mode="append"`) | Long-term memory of incidents, fixes that worked and fixes that failed |
| `recall` (world facts + source chunks) | Every analysis | Similar past incidents, cited by ID; verified action evidence ("worked in 2 of 2") |
| `recall` (observations) | Every analysis, "What the agent has learned" tab | Patterns Hindsight consolidated across incidents |
| `reflect` with a JSON schema | Postmortem | A blameless postmortem that knows whether this happened before |
| Mental model (`refresh_after_consolidation`) | Living runbook | A table of what worked and failed per failure type, maintained by Hindsight |
| Directives | Team rules | Rules the team sets once; the agent follows them and postmortems apply them |

## Measured learning
The learning curve replays every ShopFast fault once per round on a fresh memory bank. It runs with memory on and with memory switched off, CURVE_METHOD.

<!-- chart:curve -->

CURVE_TABLE

## Guardrails
- The agent can run only allow-listed runbook actions. It never sees which fault an action fixes, and some actions are decoys.
- Nothing runs without a human approval: an Approve click in the UI, a call to `/actions` in the API, or the client's confirmation in MCP (writing tools are marked destructive).
- Every action is verified against four endpoints; failed attempts are recorded, not hidden.
- All inputs are validated (Pydantic). Incident text, memory and rules are delimited and escaped before they reach the LLM. Secrets live only in `.env`.
- The REST API needs an API key (constant-time compare) and binds to localhost.

## What was built

| Area | Built |
| --- | --- |
| ShopFast mock | 8 faults with real-looking logs and alerts; 14 runbook actions, including decoys; a customer-facing storefront |
| Agent core | Automatic intake, recall with citations, Groq advisor with validation and retries, act, verify and learn loop |
| Memory | Seed history (25 incidents), outcomes, engineer fixes, postmortems, living runbook, team rules |
| Measurement | Before/after evaluation; learning curve with repeats and a memory-off baseline |
| Interfaces | Streamlit UI (4 tabs), REST API, MCP server (9 tools) |
| Persistence | Open incidents in SQLite, so a refresh or restart loses nothing |
| Quality | TEST_COUNT automated tests; every commit passes on its own |

## Who built what
- **Sanjana:** the project and its core. ShopFast and its faults, Hindsight memory wrapper and seed data, Groq advisor, act/verify/learn loop, automatic intake, learning evidence, before/after evaluation, Streamlit UI, storefront.
- **Mohan:** learning from engineers, postmortems with `reflect`, living runbook, team rules (directives), learning curve with repeats and baseline, four more faults, SQLite persistence, REST API and MCP server.

## Run it
```bash
uv venv && uv pip install -r requirements.txt
copy .env.example .env                                          # fill in Hindsight and Groq keys
uv run python -m scripts.seed_memory                            # seed history, team rules, living runbook
uv run uvicorn shopfast.app:app --host 127.0.0.1 --port 8001
uv run uvicorn storefront.app:app --host 127.0.0.1 --port 8003
uv run streamlit run ui/app.py
```
Code: [github.com/Sanjanasree02/incident-response-agent](https://github.com/Sanjanasree02/incident-response-agent). Pull requests [#1](https://github.com/Sanjanasree02/incident-response-agent/pull/1) and [#2](https://github.com/Sanjanasree02/incident-response-agent/pull/2) hold the latest work.
