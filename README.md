# Incident Response Agent

An AI agent that remembers every past production incident at ShopFast (a fictional e-commerce platform) and uses that memory to suggest root causes and fixes for new incidents. Built on [Hindsight](https://hindsight.vectorize.io/) memory and Groq.

When production breaks, the agent:
1. Detects the failure by probing ShopFast and opens an incident from its latest alert (no copy-paste).
2. Recalls similar past incidents from Hindsight memory and cites them.
3. Suggests a probable root cause, ordered fix steps, fixes that failed before, and one allow-listed remediation action, following the team's rules stored in memory.
4. Runs the action only after a human approves it, then verifies the shop is healthy. The engineer can pick a different action instead.
5. Records the outcome in memory automatically, so the next similar incident gets the proven fix.
6. Writes a blameless postmortem with Hindsight `reflect` over the whole incident history, and stores it with the incident.
7. Keeps a living runbook (a Hindsight mental model) that Hindsight rewrites as the agent learns.

Other agents can use it too: an MCP server (Claude Code, Cursor) and a REST API expose the same flow and memory. Open incidents are kept in SQLite, so a page refresh or a restart loses nothing.

Shareable summaries: [project overview](docs/PROJECT_OVERVIEW.md), [pending work](docs/PENDING.md) and templates for each member's article, social post and video ([docs/content/](docs/content/)). PDFs of both: `uv run --no-project --with markdown --with pytest --python .venv/Scripts/python.exe python -m scripts.build_reports` (written to `share/`).

See [docs/DESIGN.md](docs/DESIGN.md) for architecture, data model, and task split.

## How Hindsight memory is used
- **Seed:** 25 synthetic past incidents (`data/seed_incidents.json`) are stored with `retain` in the shared bank `shopfast-incidents`.
- **Recall:** each new incident is matched against memory with `recall`. Results include source chunks, so the agent cites incident IDs.
- **Learn:** recorded outcomes, including failed fix attempts, are stored with `retain`. A new failure type gets a generic answer the first time and a specific, cited answer after its outcome is recorded. Fixes that an engineer chooses instead of the agent are recorded the same way, so the agent also learns from people.
- **Reflect:** `reflect` with a JSON schema writes the postmortem, reasoning over this incident and every past one. The postmortem is appended to the incident's document (`update_mode="append"`), so later recalls of the incident bring back its lessons.
- **Mental model:** `shopfast-runbook` is a mental model with `refresh_after_consolidation`: a table of what worked and what failed for each failure type, which Hindsight rewrites when it consolidates new outcomes.
- **Directives:** team rules (`data/team_rules.json`, or added in the UI) are Hindsight directives. Every analysis gives them to the advisor, and postmortems apply them (`apply_all_directives`).
- **Observations:** patterns that Hindsight consolidates across incidents are shown under "What the agent has learned".

## Measured learning
`scripts/learning_curve.py` replays all 8 ShopFast faults once per round on a fresh bank, with memory on and with memory off, and averages several independent runs. The UI charts the result ("What the agent has learned" tab). Latest run: see [Status](#status).

## Project structure
```
agent/      config, models, Hindsight memory wrapper, Groq advisor, service, desk (open incidents for API and MCP)
api/        REST API (FastAPI, X-API-Key)
mcp_server/ MCP server (stdio) for Claude Code, Cursor and other agents
shopfast/   mock e-commerce API with fault switches
storefront/ customer-facing ShopFast page (demo layer over the ShopFast API)
ui/         Streamlit UI
data/       synthetic seed incidents, evaluation and learning-curve results
scripts/    seed, before/after evaluation, learning curve
tests/      unit tests
docs/       design document, demo script, project overview, pending work, content templates
```

## Setup
Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv venv
uv pip install -r requirements.txt
copy .env.example .env          # then fill in real values
```

Get credentials:
- Hindsight Cloud: sign up at https://ui.hindsight.vectorize.io, apply promo code in billing, copy the API URL and key.
- Groq: create a key at https://console.groq.com.
- `AGENT_API_KEY`: any long random value, only needed for the REST API.

Never commit `.env`.

## Run
```bash
uv run python -m scripts.seed_memory                            # seed incidents, team rules and the living runbook
uv run uvicorn shopfast.app:app --host 127.0.0.1 --port 8001    # mock shop API
uv run uvicorn storefront.app:app --host 127.0.0.1 --port 8003  # storefront: http://127.0.0.1:8003
uv run streamlit run ui/app.py                                  # agent UI
uv run python -m pytest                                         # tests
```

Measure learning (each on a fresh bank; the scripts refuse the main bank):
```bash
uv run python -m scripts.evaluate_learning --bank-id shopfast-incidents-eval1             # before vs after
uv run python -m scripts.learning_curve --bank-id shopfast-curve-2 --rounds 3 --baseline --repeats 3  # memory on vs off, averaged
```
Repeats can run in parallel, one process per Groq key (each key has its own rate limit), each with `--repeats 1 --out partN.json`; then combine them with `--merge part1.json part2.json ... --bank-id shopfast-curve-2`.

### Use it from Claude Code (MCP)
The repo ships `.mcp.json`, so Claude Code offers the `incident-memory` server when it opens this folder. Start ShopFast, then ask
"Detect the latest ShopFast incident and fix it". Tools: `detect_incident`, `analyze_incident`, `run_action`,
`next_suggestion`, `record_outcome`, `write_postmortem`, `get_runbook`, `list_team_rules`, `search_memory`. Tools that change the shop or
memory are marked destructive, so the client asks before each call; that confirmation is the human approval.

### REST API
```bash
uv run uvicorn api.app:app --host 127.0.0.1 --port 8002   # OpenAPI docs: http://127.0.0.1:8002/docs
curl -X POST http://127.0.0.1:8002/incidents/detect -H "X-API-Key: $AGENT_API_KEY"
curl -X POST http://127.0.0.1:8002/incidents/INC-.../actions -H "X-API-Key: $AGENT_API_KEY" -H "Content-Type: application/json" -d "{}"
curl -X POST http://127.0.0.1:8002/incidents/INC-.../postmortem -H "X-API-Key: $AGENT_API_KEY"
curl http://127.0.0.1:8002/rules -H "X-API-Key: $AGENT_API_KEY"
```
Every endpoint except `/health` needs `X-API-Key`. Without `AGENT_API_KEY` in `.env` the API refuses all calls.

## Status
All modules are built and tested (385 tests passing). The full demo (existing memory, new incident type, learning loop) was verified on 2026-09-28; acceptance criteria AC1-AC5 are met. See `docs/DESIGN.md` for details.

<!-- curve:start -->
Latest learning curve (2026-09-28, 8 faults x 3 rounds, averaged over 2 independent runs on fresh banks, Groq + Hindsight Cloud):

| Round | Fixed by agent's first action (memory on) | Wrong actions (memory on) | Fixed by first action (memory off) | Wrong actions (memory off) |
|---|---|---|---|---|
| 1 | 7/8 | 2.5 | 6/8 | 3 |
| 2 | 7.5/8 | 0.5 | 6/8 | 3 |
| 3 | 7.5/8 | 1 | 6.5/8 | 2.5 |

After round 1, memory cut wrong actions on production to 0.75 per round on average, against 2.75 with memory off, and raised first-action fixes. The remaining misses with memory are rounds where the LLM proposed no action at all, so the engineer fallback stepped in; LLM results vary run to run, hence the averaging. Per-run details: `data/learning_curve.json`.
<!-- curve:end -->
