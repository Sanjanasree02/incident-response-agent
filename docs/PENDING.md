# Incident Response Agent: what's left

Hey team, here's where we stand. The code is done and ready for the demo. I've opened two pull requests with everything I added on top of Sanjana's build. What's left is mostly the submission material: the demo video, our content deliverables, and a rehearsed live demo. Please put your name next to anything you pick up.

## 1. What we must submit

| # | Item | Who | Notes |
| --- | --- | --- | --- |
| 1 | Demo video | | I wrote a 3-minute script in `docs/DEMO.md`. Please record it on a fresh demo bank, not the main one. |
| 2 | Article, social post and video for each of us | Everyone | The rules say every team member has to do all three. Check the official content guide for the format. Templates are ready, see "Content templates" below. |
| 3 | Live demo to the judges | | Let's rehearse `docs/DEMO.md` end to end at least twice. The script has backup lines in case Groq or Hindsight is slow. |
| 4 | How we use Hindsight memory | | Already written up in the README and the project overview. Just check it still matches what we show in the demo. |
| 5 | Clean, documented repo | Sanjana | Merge my two PRs, then read the README top to bottom once. |

### Content templates
The rules say every one of us has to publish an article, a social post and a video; one person doing it for the team doesn't count. To make it quicker, I've put templates in `docs/content/` (on my PR #2 branch until it's merged). The shared parts (problem, what we built, results) are written; the "what I built" part is for each of us to write about our own work, in our own voice.

| Template | What's in it |
| --- | --- |
| [Article template](https://github.com/Raphael-08/incident-response-agent/blob/feat/more-faults-persistence-directives/docs/content/article-template.md) | Structure with shared sections written, plus prompts for your own part and your lessons |
| [Social post and video template](https://github.com/Raphael-08/incident-response-agent/blob/feat/more-faults-persistence-directives/docs/content/social-and-video-template.md) | A LinkedIn/X post and a 60-90 s shot list, with a slot for your part |

Before publishing: fill in the final learning-curve numbers (from the project overview PDF), add the repo link, and follow the official content guide.

## 2. One video I parked: Claude Code using our agent
I'd love a short screen recording (30-60 s) of Claude Code fixing ShopFast through our MCP server. It shows that any AI agent can use our memory, which is a strong point for innovation. I parked it because it needs someone to record it. Here's how:

1. Start ShopFast, then break it: `curl -X POST http://127.0.0.1:8001/admin/faults/DB_POOL_EXHAUST`.
2. Open Claude Code in the repo folder. It picks up `.mcp.json` and offers the `incident-memory` server; approve it.
3. Ask: "Detect the latest ShopFast incident and fix it, then write the postmortem."
4. On screen you should see it call `detect_incident`, ask before `run_action`, verify the shop, then call `write_postmortem`.
5. Optional extras: "What are our team rules?" (`list_team_rules`) and "What fixed Redis timeouts before?" (`search_memory`).

## 3. Reviewing and merging my PRs

| Item | Who | Notes |
| --- | --- | --- |
| Review and merge [PR #1](https://github.com/Sanjanasree02/incident-response-agent/pull/1) | Sanjana | Learning from engineers, reflect postmortems, living runbook, learning curve, REST API, MCP server |
| Review and merge [PR #2](https://github.com/Sanjanasree02/incident-response-agent/pull/2) | Sanjana | 4 more faults, averaged learning curve, open incidents saved in SQLite, team rules (directives) |
| Setup after merging | Everyone | Run `uv pip install -r requirements.txt`, then `uv run python -m scripts.seed_memory` on your bank; that adds the team rules and the living runbook. You only need `AGENT_API_KEY` in `.env` if you use the REST API. |

## 4. Things I'd like us to check before the demo

- [ ] Full UI walkthrough of `docs/DEMO.md` on a fresh demo bank (`--bank-id shopfast-incidents-demo1`)
- [ ] Add a team rule in the UI and check that the next suggestion names it and follows it
- [ ] Write a postmortem from the UI and download it
- [ ] Refresh the browser in the middle of an incident and check that it comes back with its action log
- [ ] Load the living runbook after a few incidents (Hindsight refreshes it after consolidation, which can take a minute)
- [ ] Quick REST API test with `curl` (detect, actions, postmortem)
- [ ] Make sure whoever runs the live demo has Groq quota left (the free tier is 8,000 tokens per minute)

## 5. Ideas if we still have time

| Idea | Why it's worth it | Effort |
| --- | --- | --- |
| Deploy ShopFast and the UI (Render or Fly.io) so judges can click through themselves | Live demo without a laptop | Medium |
| Take alerts from a webhook (Slack or PagerDuty style) instead of probing the shop | Closer to a real on-call setup | Medium |
| An "ask memory" chat box backed by `reflect` | Free-form questions over our incident history | Small |
| Tag memories per service and filter recall by tag | Sharper recall as the bank grows | Small |
| Per-client banks and hashed API keys | Only needed if the API ever leaves localhost | Medium |
| Delete my test banks (`shopfast-curve-*`, `shopfast-probe-*`) in Hindsight Cloud | Keeps the account tidy | Small |

## Good to know
- Groq's free tier allows about 2 analyses per minute. The agent waits when Groq asks, and falls back to showing memory if it can't get an answer.
- Runbook actions are simulated inside ShopFast. The evaluation scripts approve every proposal automatically; the UI never does.
- LLM answers vary from run to run, so I averaged the learning curve over 3 independent runs, one per Groq key, run in parallel.

Ping me if anything is unclear. Mohan
