# Design: Incident Response Agent

## Goal
Help on-call engineers at ShopFast (a fictional e-commerce platform) resolve production incidents faster by recalling how similar incidents were resolved before. The agent advises; the engineer applies the fix.

## Flow
1. Engineer submits an incident (service, severity, title, symptoms, error log).
2. Agent recalls the most similar past incidents from Hindsight memory, with source incident IDs.
3. Agent suggests a probable root cause, ordered fix steps, and steps to avoid (fixes that failed before).
4. Engineer applies the fix in their own systems, then records the outcome.
5. Agent retains the outcome in Hindsight. The next similar incident gets a better, cited answer.

## Acceptance criteria
- AC1: Submitting an incident returns the top similar past incidents from Hindsight with source references.
- AC2: The agent suggests a root cause and ordered fix steps based on recalled memory.
- AC3: The user records the outcome (resolved or not, actual root cause, steps, notes). The agent retains it in Hindsight.
- AC4: The demo shows improvement: a new incident type gets a generic answer first, and a specific, cited answer after its outcome is recorded.
- AC5: Secrets come only from environment variables. All inputs are validated. LLM errors are handled gracefully.

## Verification (2026-09-28)
All acceptance criteria were verified in a full manual demo against Hindsight Cloud (bank `shopfast-incidents`) and Groq, with 168 automated tests passing.

| Criterion | Status | Evidence |
|---|---|---|
| AC1 | Verified | `DB_POOL_EXHAUST`: checkout returned 503; the agent recalled INC-1042 with its stored source text |
| AC2 | Verified | Root cause and ordered fix steps cited INC-1042; "restart pods" listed under avoid |
| AC3 | Verified | Outcome for a `PAYMENT_GATEWAY_TIMEOUT` incident recorded from the UI and retained in Hindsight |
| AC4 | Verified | First `PAYMENT_GATEWAY_TIMEOUT` analysis: "No similar past incident", low confidence. After the outcome was recorded, the next analysis recalled the new incident with its successful fix and failed attempt |
| AC5 | Verified | Settings from `.env` only; Pydantic validation on all inputs; on LLM failure the UI shows a warning and still shows recalled memory |

Scenario summary:

| Scenario | Result |
|---|---|
| Existing memory (`DB_POOL_EXHAUST`) | Checkout 503, INC-1042 recalled, fix steps generated; checkout 200 after the fault was turned off |
| New incident type, first time (`PAYMENT_GATEWAY_TIMEOUT`) | Generic answer, low confidence, no memory cited |
| Learning loop (`PAYMENT_GATEWAY_TIMEOUT`, after outcome) | Newly recorded incident recalled with its fix and failed attempt |

## Architecture
```
                        ┌──────────────────────────────┐
  ShopFast mock app     │  Streamlit UI  (:8501)       │   (proposed)
  FastAPI 127.0.0.1:8001│  Submit │ Outcome │ Memory   │   REST API + Integrate tab
  fault switches  ────► │  "What the agent has learned"│   for other projects
  real error logs       └──────────────┬───────────────┘
  (pasted into UI)                     │
                           agent/service.py
                   analyze_incident · record_outcome
                      │                          │
           agent/memory.py                 agent/llm.py
           + log_normalizer.py             Groq openai/gpt-oss-120b
                      │                    JSON output, retries
           Hindsight Cloud
           https://api.hindsight.vectorize.io
           bank: shopfast-incidents[-demoN]
           retain · recall(world) · recall(observation)
```

| Module | Responsibility | Status |
|---|---|---|
| `agent/config.py` | Load settings from env; fail fast when missing; bank ID validation and override | Done |
| `agent/models.py` | Pydantic models; all input validation; incident ID generation | Done |
| `agent/log_normalizer.py` | Strip noise from logs before recall | Done, tested |
| `agent/memory.py` | Hindsight `create_bank`, `retain`, `recall`, learned patterns | Done, tested with fake client |
| `agent/llm.py` | Groq call, JSON output, retries (honors 429 retry-after), `LLMError`, delimited untrusted input, relevance judging, citation check | Done, tested with fake client |
| `agent/service.py` | `analyze_incident`, `record_outcome`; returns memory when LLM fails | Done, tested with fakes |
| `shopfast/faults.py` | Fault switches and their log lines | Done |
| `shopfast/app.py` | Mock shop endpoints; `/admin/faults`; faults return and log a timestamped error line | Done, tested |
| `agent/actions.py` | ShopFast tool client: list and run allow-listed actions, health checks | Done, tested |
| `agent/intake.py` | ShopFast alert -> Incident | Done, tested |
| `shopfast/alerts.py` | Bounded log of failure alerts | Done, tested |
| `agent/evidence.py` | Worked/failed counts per action from recorded outcomes | Done, tested |
| `scripts/evaluate_learning.py` | Before-vs-after learning evaluation | Done, tested, run |
| `shopfast/remediations.py` | Simulated runbook actions, including decoys | Done, tested |
| `ui/app.py` | Streamlit UI, 4 tabs, example fault logs, text-only rendering of incident and LLM text, engineer's choice, postmortem, learning curve, living runbook, Integrate tab | Done, tested with AppTest |
| `agent/desk.py` | Open incidents by ID (incident, suggestion, action log) for the API and MCP server | Done, tested |
| `api/app.py` | REST API, `X-API-Key` | Done, tested |
| `mcp_server/server.py` | MCP server (stdio), 8 tools | Done, tested; smoke-tested over stdio against Hindsight Cloud |
| `scripts/learning_curve.py` | Multi-round learning curve, memory on vs off | Done, tested, run |
| `scripts/seed_memory.py` | Validate seed data, `--bank-id`, load into Hindsight | Done, tested |
| `data/seed_incidents.json` | 25 synthetic incidents | Done |

## Storefront (demo layer)
`storefront/` is a customer-facing page on port 8003: products, cart, checkout and a status bar. Its server forwards only `GET /products`, `POST /cart/items` and `POST /checkout` to ShopFast and returns ShopFast's status code and body unchanged, so every error shown is ShopFast's own. `/admin` and `/ops` are not forwarded, so the page cannot change faults or run actions. Same-origin forwarding means ShopFast needs no CORS change. The status bar is derived from the page's own responses; it is not a second monitor. ShopFast and the agent are unchanged.

## Automatic incident intake
ShopFast records every fault-caused failure as an alert (`shopfast/alerts.py`; `GET /ops/incidents/latest`: time, endpoint, status, error, log line, repeat count). "Detect latest ShopFast incident" in the UI calls `IncidentService.detect_incident`: the agent probes the shop's public endpoints like a monitor; if one fails, it fetches the latest alert and `agent/intake.py` turns it into an `Incident` (service from the log line, SEV1 for `/checkout` and `/login`, title and symptoms from the alert, raw log as error log). A healthy shop yields no incident, so an alert from an already fixed failure is never imported. The detected incident is analyzed immediately and then follows the same approval gate. Manual entry stays as a fallback.

## Act -> verify -> learn
The agent can act, but only through an allow-listed, human-approved runbook.
1. `analyze_incident` offers ShopFast's runbook actions (`GET /ops/actions`: name and description only) to the LLM, which proposes one with a reason. The proposal is validated: it must be listed and not already tried, and its reason may cite only relevant incidents.
2. The UI shows the proposed action and why. Nothing runs until a human clicks Approve (or Reject).
3. `remediate` runs the approved action (`POST /ops/actions/{name}`), then probes `/products`, `/login`, `/cart/items` and `/checkout`.
4. The agent builds the outcome itself (worked steps, failed attempts with the failing endpoint, health) and retains it with the existing `retain_outcome`. Failed attempts are recorded too; "Ask the agent for the next action" re-analyzes without the actions already tried.
5. The next similar incident recalls that outcome.

Safety: actions are simulated in ShopFast (`shopfast/remediations.py`), some are decoys that fix nothing, and the agent never sees which fault an action fixes. The agent's client (`agent/actions.py`) cannot reach `/admin/faults`. Every decision is kept in an action log (action, reason, approved or rejected, verification result).

## Measurable learning
- Learning evidence (`agent/evidence.py`): for the incidents the agent judged relevant, it reads the stored outcome text Hindsight recalls and counts, per allow-listed action, the incidents where it worked or failed ("rollback_payment_api: worked in 2 of 2 recorded attempts (INC-..., INC-...)"). Only action names inside the recorded "Fix steps that worked" / "Fix attempts that did not work" sections count, so every number traces to a verified outcome; seed incidents add none. Shown under each analysis and in the "What the agent has learned" tab, next to the unchanged Hindsight consolidated patterns.
- Evaluation (`scripts/evaluate_learning.py`): replays each fault through detect -> analyze -> remediate -> verify -> record, first on an empty bank (before), then again after the outcomes are recorded (after). Reports relevant recalls, proven fix proposed first, first-action fixes, actions needed and failed fixes avoided; saves `data/evaluation_results.json`, which the UI shows. The harness approves every proposal (labelled in the report); the UI keeps the human approval gate. It refuses the main bank.
- Evidence also grounds proposals: the LLM sees the same verified outcomes, and a proposal is rejected and retried if, for incidents it judged relevant, it repeats an action that only failed or ignores an action that worked.
- Latest run (bank `shopfast-incidents-eval2`, 4 faults, final code): relevant recall 0/4 before, 3/4 after; resolved 2/4 before, 3/4 after; first-action fixes 2/4 before, 3/4 after; failed fixes avoided 1 of 1. `REDIS_TIMEOUT` got no proposal in either round (the LLM judged no listed action to fit), so nothing was learned for it. A first run on the pre-guard code gave relevant recall 0/4 -> 3/4, resolved 3/4 -> 4/4, first-action fixes 2/4 -> 4/4. Results vary run to run: 4 incidents, one run each.

## Engineer's choice (learning from people)
When the agent proposes nothing, or the engineer disagrees, the UI offers "Run a different action": any allow-listed action, run and verified like the agent's own, marked `chosen_by: engineer` in the action log, and recorded in memory the same way. The agent therefore learns fixes it did not think of. `REDIS_TIMEOUT` shows why this matters: with empty memory the LLM judged no action to fit, so earlier runs never learned anything for it. The evaluation harness now plays an engineer without memory in that case (tries the runbook in listed order; counted separately as `engineer_actions`), and the next round the agent proposes the verified fix itself. Two Redis decoys (`raise_redis_client_timeout`, `failover_cart_redis`, both failed fixes in the seed history) make the choice non-trivial.

## Postmortem (Hindsight reflect)
After any executed action, "Write postmortem" calls `IncidentService.write_postmortem`. `IncidentMemory.reflect_postmortem` sends the incident record and full action log (`postmortem_context`) as `context` to Hindsight `reflect` with a JSON schema (summary, impact, root cause, timeline, what went well and wrong, action items, related incidents). Hindsight reasons over the whole bank, so the postmortem says whether this failure happened before. Related incident IDs are filtered to real IDs. The postmortem is then appended to the incident's own document (`retain(..., document_id=incident_id, update_mode="append")`). `recall_similar` joins all chunks of a document into `source_text`, so later recalls of the incident carry the outcome and the postmortem. Postmortem text uses `*` bullets and no "Fix steps" headings, so it never adds action evidence. Downloadable from the UI.

## Living runbook (Hindsight mental model)
`IncidentMemory.ensure_runbook` creates the mental model `shopfast-runbook` (source query: what worked and failed per failure type, with incident IDs, as a table) with `trigger={"refresh_after_consolidation": True}`. Hindsight rewrites it after each consolidation, so it grows as outcomes are recorded. Created by the seed script and the evaluation scripts; shown on request in the "What the agent has learned" tab (not on every Streamlit rerun), and via `GET /runbook` and the MCP tool `get_runbook`.

## Learning curve
`scripts/learning_curve.py` replays every fault once per round, for N rounds, on one fresh bank; each round has what the earlier ones recorded. `--baseline` repeats the rounds with `NoMemory` (recalls nothing, keeps nothing). Per round: relevant recall, fixed by the agent's first action, wrong actions run, engineer actions. Latest run (`shopfast-curve-1`, 3 rounds): first-action fixes 2/4, 4/4, 4/4 with memory vs 2/4, 2/4, 3/4 off; wrong actions 4, 0, 0 vs 3, 3, 2. Saved to `data/learning_curve.json`; the UI charts "fixed by the agent's first action (%)" and "wrong actions run on production", with memory and with memory off.

## Team rules (Hindsight directives)
Rules the team sets once live in the bank as Hindsight directives. `data/team_rules.json` holds the starting four (prefer rollback after a change, no restart as first fix, cite or say none, blameless postmortems); the seed script adds the missing ones. The UI adds more (name, text, priority, validated). `IncidentService.analyze_incident` reads the active rules (highest priority first) and gives them to the advisor in a `<rules>` block; the system prompt says to follow them and to prefer a rule over a conflicting memory. Rule text is escaped like all other data. The suggestion names the rules it was given. Postmortems call `reflect` with `apply_all_directives=True`. Rules that cannot be read never block an analysis. Exposed as `GET/POST /rules` and the MCP tool `list_team_rules`.

## Persistence (SQLite)
`agent/store.py` keeps each open incident (incident, latest suggestion, action log, postmortem) as one row with JSON columns, validated by the models on read (`INCIDENT_DB`, default `data/incidents.db`, git-ignored). `IncidentDesk` (API and MCP) uses it instead of an in-process dict, so an incident can be continued after a restart; changes to one desk are serialized. The UI saves after every change and restores the 20 most recent incidents on a new browser session, with a selector to switch between them. Hindsight holds what the agent learned; SQLite holds only work in progress.

## Fault catalogue
| Fault | Endpoint | Seed history | Fixing action | Decoys it can be confused with |
|---|---|---|---|---|
| `DB_POOL_EXHAUST` | checkout 503 | INC-1042, INC-1067, INC-1113 | `rollback_payment_api` | `restart_payment_api_pods`, `scale_out_payment_api` |
| `REDIS_TIMEOUT` | products, cart 503 | INC-1051, INC-1088, INC-1129 | `disable_cart_analytics` | `raise_redis_client_timeout`, `failover_cart_redis` |
| `AUTH_TOKEN_EXPIRED` | login 401 | INC-1075, INC-1098, INC-1141 | `resync_gateway_clocks` | |
| `PAYMENT_GATEWAY_TIMEOUT` | checkout 504 | none | `raise_payment_gateway_timeout` | `rollback_payment_api` |
| `INVENTORY_DEADLOCK` | checkout 500 | INC-1092 | `enable_sorted_row_locking` | `restart_inventory_service` |
| `CERT_EXPIRED` | login 502 | INC-1059 | `renew_auth_certificate` | `resync_gateway_clocks` |
| `SEARCH_DISK_FULL` | products 503 | INC-1118 | `delete_old_log_indices` | `scale_out_search` |
| `PROMO_CONFIG_BROKEN` | checkout 500 | none | `rollback_promotions_config` | `restart_promotions_service`, `rollback_payment_api` |

## Integrations (REST API and MCP)
Both sit on `agent/desk.py`, which keeps open incidents by ID (bounded, 200) so later calls can act, record or write a postmortem by ID.
- REST (`api/app.py`, port 8002, `127.0.0.1`): `POST /incidents/detect`, `POST /incidents/analyze`, `GET /incidents/{id}`, `POST /incidents/{id}/reanalyze`, `POST /incidents/{id}/actions`, `POST /incidents/{id}/outcome`, `POST /incidents/{id}/postmortem`, `GET /runbook`, `GET /memory/search`. `X-API-Key` must equal `AGENT_API_KEY` (constant-time compare); without the variable every call gets 503. Calling `/actions` is the approval. Errors: unknown incident 404, invalid input 422, action not allow-listed 409, Hindsight or ShopFast failure 502.
- MCP (`mcp_server/server.py`, stdio, `mcp` 2.x `MCPServer`): tools `detect_incident`, `analyze_incident`, `run_action`, `next_suggestion`, `record_outcome`, `write_postmortem`, `get_runbook`, `list_team_rules`, `search_memory`. Writing tools carry `destructive_hint=True`, so clients confirm with the user first. Errors are returned as `{"error": ...}`, never raised. `.mcp.json` registers it for Claude Code.
- Not built from the proposal: per-client banks and hashed per-client keys (one shared key and bank at demo scale), Python package, iframe.

## Configuration
`.env` (git-ignored) holds:

| Variable | Value |
|---|---|
| `HINDSIGHT_BASE_URL` | `https://api.hindsight.vectorize.io` |
| `HINDSIGHT_API_KEY` | from Hindsight Cloud Connect page |
| `HINDSIGHT_BANK_ID` | `shopfast-incidents` (or a demo bank) |
| `GROQ_API_KEY` | from console.groq.com |
| `GROQ_MODEL` | `openai/gpt-oss-120b` |
| `AGENT_API_KEY` | REST API key, long random value |

## Hindsight memory usage
- One shared bank: `shopfast-incidents` (overridable per demo run, see below).
- Seeding: one `retain` per past incident, `document_id = incident_id`, metadata `service`, `severity`, `resolved`. Content text includes the incident ID so recalled facts can be cited.
- Analyze, similar incidents: `recall(query, types=["world"], include_chunks=True)`. Chunks provide source text for citations.
- Relevance: recall always returns the nearest incidents, and reranker scores overlap between true and false matches, so no fixed threshold works. The LLM returns `relevant_incident_ids` (must be recalled IDs); only those are cited and shown, and `memory_used` is false when none match. This makes a new failure type get a generic answer (AC4).
- Analyze, learned patterns: `recall(query, types=["observation"])`. Observations are patterns Hindsight consolidates across many incidents (for example "restarting pods never fixes pool exhaustion"). Shown in the UI as "What the agent has learned", so learning is visible, not only search.
- Recall query: `service + title + symptoms + normalize_log(error_log)`. `agent/log_normalizer.py` replaces timestamps, IPs, UUIDs, pod hashes, hex values and long numbers with placeholders, so the same error from different runs produces the same query.
- Learn: `record_outcome` calls `retain` with the new incident and its outcome, including failed attempts.

## Incident IDs
Generated automatically when not supplied: `INC-` + UTC timestamp `yymmddHHMMSS` + 4 random digits (for example `INC-2609281430120734`). The ID is the Hindsight `document_id`, so it must be unique: within a process an ID is never repeated, even within one second; across processes a collision needs the same second and the same 1-in-10,000 suffix. (Earlier IDs used 2 digits, which collided 1 in 100 within a second.) Seed incidents keep short IDs (`INC-1042`). Pattern: `^INC-\d{4,16}$`.

## Demo reset
The Hindsight SDK does not document a bank delete call, and deleting memory is irreversible. Instead, each demo run uses a fresh bank:
```
python -m scripts.seed_memory --bank-id shopfast-incidents-demo3
```
Then set `HINDSIGHT_BANK_ID=shopfast-incidents-demo3` in `.env`. Old banks stay untouched. Bank IDs must match `^[a-z0-9][a-z0-9-]{2,63}$`.

## Seed data
25 synthetic incidents in `data/seed_incidents.json`:

| Pattern | Incidents | Demo role |
|---|---|---|
| DB connection pool exhaustion | INC-1042, INC-1067, INC-1113 | strong recall |
| Redis timeout | INC-1051, INC-1088, INC-1129 | strong recall |
| Auth token expiry | INC-1075, INC-1098, INC-1141 | strong recall |
| Payment gateway timeout | none | learning moment |
| Other realistic incidents | 16 | noise; shows ranking |

## Trade-offs
- `recall` + Groq for the live suggestion: structured JSON output, explicit citations and validation against the allow-list. `reflect` is used where Hindsight's own reasoning over the whole bank is the point: the postmortem and the living runbook.
- Sync SDK: Streamlit is synchronous; simpler code at demo scale.
- Fault switches held in memory: simple; state resets on restart.

## Security
- Groq calls verify TLS against the OS certificate store (`truststore`), so antivirus or proxy roots installed in Windows are trusted. Verification is never disabled.
- Secrets only in `.env` (git-ignored). `.env.example` has placeholders.
- Pydantic validation with length limits and patterns on all inputs.
- No `unsafe_allow_html` in Streamlit.
- ShopFast binds to `127.0.0.1`; `/admin/faults` is local-only.
- No SQL database in this build.

## Demo script (about 90 s)
1. ShopFast checkout works.
2. Enable `DB_POOL_EXHAUST`; checkout fails with 503 and a real log line.
3. Paste the log into the agent; it cites INC-1042 and others, suggests rollback plus pool size, warns that restarting pods failed before.
4. Disable the fault ("apply fix"); checkout works. Record the outcome.
5. Enable `PAYMENT_GATEWAY_TIMEOUT`; agent reports no similar incident and gives a generic answer. Record the real fix.
6. Trigger it again; agent now gives a specific answer citing the incident just recorded.

## Task split (4 people)
| Owner | Tasks |
|---|---|
| Memory owner | `agent/memory.py` (incl. `recall_learned_patterns`), `scripts/seed_memory.py`, Hindsight Cloud bank setup |
| Agent owner | `agent/llm.py`, `agent/service.py`, prompt, Groq error handling |
| ShopFast owner | `shopfast/app.py` endpoints and fault behavior |
| UI + demo owner | `ui/app.py` (incl. "What the agent has learned" panel), demo script, video, content deliverables |

## Embedding the agent in other projects (original proposal; REST API and MCP server are built, see Integrations)
| Option | Who can use it | Effort |
|---|---|---|
| REST API (FastAPI): `POST /incidents/analyze`, `POST /incidents/{id}/outcome`, `GET /patterns` | Any project, any language | about 1-1.5 h |
| Python package (`pip install git+<repo>`) | Python projects | almost none |
| Streamlit iframe (`?embed=true`) | Web apps, UI only | small |
| MCP server (`analyze_incident`, `record_outcome` tools) | Other AI agents | about 1 h on top of the REST API |

UI addition: an "Integrate" tab showing the API endpoint, a `curl` example, the iframe snippet, and how to request an API key.

Security for embedding: API key per client (stored hashed), one memory bank per client so no client can read another's incidents, CORS allow-list, rate limits, fault admin stays local-only.

## Progress log
| Date | Commit | Change |
|---|---|---|
| 2026-09-28 | `5d183c5` | Project skeleton: structure, models, stubs, seed data, tests, docs |
| 2026-09-28 | `3c83ec9` | Log normalizer, automatic incident IDs, learned patterns interface, demo bank reset |
| 2026-09-28 | `a51c8c2` | Design doc: final architecture, module status, configuration, embedding proposal, open items |
| 2026-09-28 | `8643269` | Mark test suite as passing |
| 2026-09-28 | `8af7835` | Fix input validation (whitespace, required `reported_at`), log normalizer (comma milliseconds, region names), empty `GROQ_MODEL`; 9 regression tests |
| 2026-09-28 | `6e96114` | README status, progress log and open items |
| 2026-09-28 | `4906384` | Fix blank or unbounded list items and invalid service names; tests for config, seed data, fault admin, recall query |
| 2026-09-28 | `a41e734` | Hindsight memory wrapper and seed script; 25 seed incidents loaded into `shopfast-incidents` |
| 2026-09-28 | `e1e02ce` | Close the Hindsight client session after use |
| 2026-09-28 | `eebeff6` | Groq advisor and incident service |
| 2026-09-28 | `4136e4f` | LLM relevance judging (`relevant_incident_ids`), OS trust store for Groq TLS, honor 429 retry-after |
| 2026-09-28 | `8e8481e` | Streamlit UI: submit, record outcome, learned patterns |
| 2026-09-28 | `1067c29` | ShopFast shop endpoints with fault behavior |
| 2026-09-28 | `e3face3` | Run all Hindsight calls on one worker thread (fixes "Timeout context manager should be used inside a task" on the second UI analysis) |
| 2026-09-28 | `fbd313a` | Demo verification results; AC1-AC5 verified |
| 2026-09-28 | `bae7c4e` | Act -> verify -> learn: allow-listed runbook actions, human approval, verification, automatic outcome recording |
| 2026-09-28 | `6a3501e` | Automatic incident intake: ShopFast alerts, Detect button, manual form kept as fallback |
| 2026-09-28 | `41b9563` | Measurable learning: evidence from recorded outcomes, before-vs-after evaluation |
| 2026-09-28 | `58cec3f` | Customer-facing ShopFast storefront |
| 2026-09-29 | PR #1 | Engineer's choice and Redis decoys; postmortem with `reflect`; living runbook mental model; learning curve with memory-off baseline; REST API; MCP server; uv setup |
| 2026-09-29 | PR #2 | Four more faults (8 total); learning curve repeats, averages and parallel merge; incident ID collision fix; team rules as directives; SQLite persistence; overview and pending-work PDFs |

## Open items
- Per-client banks and keys for the REST API if it is ever exposed beyond localhost.
- Build order: (done) `agent/memory.py` and seed script, (done) `llm.py` and `service.py`, (done) UI, (done) ShopFast endpoints. All modules built.

## Known limits
- Groq free tier allows 8000 tokens per minute (about 2 analyses per minute). The advisor waits as long as Groq asks, up to 20 s, then falls back to showing recalled memory.
- Hindsight calls run one at a time on a single worker thread per `IncidentMemory`. Fine at demo scale.
- Every recorded outcome is retained permanently. Use a demo bank (`--bank-id shopfast-incidents-demoN`) for rehearsals so the main bank is not filled with repeated demo incidents.
- Open incidents persist in SQLite; delete `data/incidents.db` to start the UI with none.

## Future work
- See `docs/PENDING.md` for the remaining submission work and ideas.
