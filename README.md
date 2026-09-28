# Incident Response Agent

An AI agent that remembers every past production incident at ShopFast (a fictional e-commerce platform) and uses that memory to suggest root causes and fixes for new incidents. Built on [Hindsight](https://hindsight.vectorize.io/) memory and Groq.

When production breaks, the agent:
1. Detects the failure by probing ShopFast and opens an incident from its latest alert (no copy-paste).
2. Recalls similar past incidents from Hindsight memory and cites them.
3. Suggests a probable root cause, ordered fix steps, fixes that failed before, and one allow-listed remediation action.
4. Runs the action only after a human approves it, then verifies the shop is healthy.
5. Records the outcome in memory automatically, so the next similar incident gets the proven fix.

See [docs/DESIGN.md](docs/DESIGN.md) for architecture, data model, and task split.

## How Hindsight memory is used
- **Seed:** 25 synthetic past incidents (`data/seed_incidents.json`) are stored with `retain` in the shared bank `shopfast-incidents`.
- **Recall:** each new incident is matched against memory with `recall`. Results include source chunks, so the agent cites incident IDs.
- **Learn:** recorded outcomes, including failed fix attempts, are stored with `retain`. A new failure type gets a generic answer the first time and a specific, cited answer after its outcome is recorded.

## Project structure
```
agent/      config, models, Hindsight memory wrapper, Groq advisor, service
shopfast/   mock e-commerce API with fault switches
storefront/ customer-facing ShopFast page (demo layer over the ShopFast API)
ui/         Streamlit UI
data/       synthetic seed incidents
scripts/    seed script
tests/      unit tests
docs/       design document
```

## Setup
Requires Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # then fill in real values
```

Get credentials:
- Hindsight Cloud: sign up at https://ui.hindsight.vectorize.io, apply promo code in billing, copy the API URL and key.
- Groq: create a key at https://console.groq.com.

Never commit `.env`.

## Run
```bash
python -m scripts.seed_memory                              # load seed incidents into Hindsight
python -m scripts.evaluate_learning --bank-id shopfast-incidents-eval1   # before-vs-after learning evaluation (fresh bank)
uvicorn shopfast.app:app --host 127.0.0.1 --port 8001      # mock shop API
uvicorn storefront.app:app --host 127.0.0.1 --port 8003    # storefront: http://127.0.0.1:8003
streamlit run ui/app.py                                    # agent UI
pytest                                                     # tests
```

## Status
All modules are built and tested (282 tests passing): config, models, log normalizer, Hindsight memory, seed script, Groq advisor, service, Streamlit UI and the ShopFast mock shop. The full demo (existing memory, new incident type, learning loop) was verified on 2026-09-28; acceptance criteria AC1-AC5 are met. See `docs/DESIGN.md` for details.
