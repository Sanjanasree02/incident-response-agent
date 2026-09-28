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
| `ui/app.py` | Streamlit UI, 3 tabs, example fault logs, text-only rendering of incident and LLM text | Done, tested with AppTest |
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

## Configuration
`.env` (git-ignored) holds:

| Variable | Value |
|---|---|
| `HINDSIGHT_BASE_URL` | `https://api.hindsight.vectorize.io` |
| `HINDSIGHT_API_KEY` | from Hindsight Cloud Connect page |
| `HINDSIGHT_BANK_ID` | `shopfast-incidents` (or a demo bank) |
| `GROQ_API_KEY` | from console.groq.com |
| `GROQ_MODEL` | `openai/gpt-oss-120b` |

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
- `recall` + Groq instead of Hindsight `reflect`: gives structured JSON output and explicit citations. `reflect` is a possible stretch goal.
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

## Proposed: embedding the agent in other projects (pending decision)
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
| 2026-09-28 | (this change) | Measurable learning: evidence from recorded outcomes, before-vs-after evaluation |

## Open items
- Decision: embedding options and the Integrate tab.
- Build order: (done) `agent/memory.py` and seed script, (done) `llm.py` and `service.py`, (done) UI, (done) ShopFast endpoints. All modules built.

## Known limits
- Groq free tier allows 8000 tokens per minute (about 2 analyses per minute). The advisor waits as long as Groq asks, up to 20 s, then falls back to showing recalled memory.
- Hindsight calls run one at a time on a single worker thread per `IncidentMemory`. Fine at demo scale.
- Every recorded outcome is retained permanently. Use a demo bank (`--bank-id shopfast-incidents-demoN`) for rehearsals so the main bank is not filled with repeated demo incidents.
- UI state lives in the browser session; a page refresh loses analyzed incidents (see future work).

## Future work
- Agent learns whether its own suggestions worked (retain suggestion plus result).
- ShopFast sends alerts to the agent automatically instead of copy-paste.
- Persist open incidents (SQLite) so a page refresh does not lose them.
- Eval script: recall hit rate before and after the feedback loop.
