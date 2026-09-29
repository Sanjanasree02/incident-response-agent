# Deploying the demo

- **Render** (`render.yaml`): `shopfast-api` (the mock shop) and `shopfast-storefront` (the customer page), as free
  web services.
- **Streamlit Community Cloud**: the agent UI (`ui/app.py`).

Judges open the UI, enter the shared password, break the shop from the demo panel and watch the agent detect, fix
and learn.

## 1. Memory bank

Seed a separate bank so the demo never touches your own:
`uv run python -m scripts.seed_memory --bank-id shopfast-incidents-judges`

## 2. ShopFast and storefront on Render

1. Render dashboard: **New > Blueprint**, connect the GitHub repo `Sanjanasree02/incident-response-agent`, branch
   `main`. Render reads `render.yaml`. Leave `SHOPFAST_URL` empty and click **Apply**.
2. When `shopfast-api` is live, copy its URL and check `<URL>/products` lists three products. In its
   **Environment** tab, copy the generated `SHOPFAST_ADMIN_TOKEN`.
3. On `shopfast-storefront`, set `SHOPFAST_URL` to the `shopfast-api` URL and save (it redeploys). Open the
   storefront URL: the shop should show its products.

## 3. Agent UI on Streamlit Community Cloud

1. share.streamlit.io: **Create app**, repo `Sanjanasree02/incident-response-agent`, branch `main`, main file
   `ui/app.py`. Advanced settings: Python 3.12.
2. **Secrets** (top-level keys reach the app as environment variables; never commit them):

   ```toml
   HINDSIGHT_BASE_URL = "..."
   HINDSIGHT_API_KEY = "..."
   HINDSIGHT_BANK_ID = "shopfast-incidents-judges"
   GROQ_API_KEY = "..."
   GROQ_MODEL = "openai/gpt-oss-120b"
   SHOPFAST_URL = "https://<shopfast-api>.onrender.com"
   SHOPFAST_ADMIN_TOKEN = "<from the shopfast-api Environment tab>"
   UI_PASSWORD = "<shared password for judges>"
   DEMO_CONTROLS = "1"
   STOREFRONT_URL = "https://<shopfast-storefront>.onrender.com"
   ```
3. **Deploy**, then check: enter the password, pick `DB_POOL_EXHAUST`, **Break the shop**, then
   **Detect latest ShopFast incident** and **Approve and run**.

## What judges should know (put this in the submission)

- The UI link and the password.
- Free Render services sleep after 15 minutes idle. The first visit can take about a minute; if the demo panel says
  ShopFast is waking up, wait and reload.
- Suggested path: `DB_POOL_EXHAUST` (known from past incidents) then `PAYMENT_GATEWAY_TIMEOUT` twice (new to the
  agent: generic answer first, recalled fix the second time). Wait about a minute between detections.

## Limits

- **Shared state.** All judges use one shop and one memory bank. Once anyone fixes `PAYMENT_GATEWAY_TIMEOUT`, it
  is no longer new for the next judge. To reset, seed a fresh bank and change `HINDSIGHT_BANK_ID` in the secrets.
- **Groq free tier.** 8,000 tokens per minute across all judges, about 4,000 per detection. Two judges detecting at
  once can get "AI suggestion unavailable"; the recalled memory is still shown, and a retry a minute later works.
- **Open incidents** are kept on the UI's local disk, which Streamlit Cloud loses when the app restarts. Memory in
  Hindsight is not affected.
- **ShopFast's customer endpoints are public** (they are the mock shop). Fault switches and runbook actions need
  `SHOPFAST_ADMIN_TOKEN`; only the UI has it.
