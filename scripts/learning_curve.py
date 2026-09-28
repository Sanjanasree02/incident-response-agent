"""Learning curve: how the agent improves as the same kinds of incidents keep happening.

Replays every ShopFast fault once per round through the agent's real flow (detect -> analyze -> remediate ->
verify -> record), for several rounds, against one fresh memory bank. Each round starts with everything the earlier
rounds recorded. With --baseline the same rounds also run with memory switched off (nothing recalled, nothing
retained), so the chart can show the agent with and without Hindsight side by side. With --repeats the whole
experiment runs several times, each on its own fresh bank (<bank-id>-r1, -r2, ...), and the report averages the rounds,
so one lucky or unlucky LLM run does not decide the result.

Approvals are given by the harness, and when the agent proposes nothing the harness plays an engineer without
memory who tries the runbook in listed order (see scripts/evaluate_learning.py). The UI keeps its human gate.

Repeats can also run in parallel as separate processes (for example one per Groq key, since each key has its own
rate limit), each with --repeats 1 and its own --out, then be combined with --merge.

Run (always against a fresh bank; the main bank is refused):
  python -m scripts.learning_curve --bank-id shopfast-curve-2 --rounds 3 --baseline --repeats 3
"""

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path

import httpx

from agent.config import ConfigError, load_settings, validate_bank_id
from agent.service import IncidentService
from scripts.evaluate_learning import MAIN_BANK, RoundResult, run_round
from shopfast.faults import Fault

RESULTS_FILE = Path(__file__).resolve().parent.parent / "data" / "learning_curve.json"
# Per-round numbers that are averaged across repeats; the two the UI charts also get their min and max.
AVERAGED = ["incidents", "relevant_recall", "resolved", "agent_fixed", "agent_first_try", "actions", "wrong_actions",
            "engineer_actions", "llm_errors"]
RANGED = ["agent_first_try", "wrong_actions"]


class NoMemory:
    """Memory switched off: recalls nothing and forgets every outcome. Used for the baseline curve."""

    def recall_similar(self, incident):
        return []

    def recall_learned_patterns(self, incident):
        return []

    def retain_outcome(self, incident, outcome):
        pass

    def team_rules(self):
        return []


def agent_first_try(result: RoundResult) -> bool:
    """Fixed by the first action, and that action was the agent's own proposal."""
    return result.first_try_fix and not result.engineer_actions


def round_summary(number: int, results: Sequence[RoundResult]) -> dict:
    return {
        "round": number,
        "incidents": len(results),
        "relevant_recall": sum(r.memory_used for r in results),
        "resolved": sum(r.resolved for r in results),
        "agent_fixed": sum(r.agent_fixed for r in results),
        "agent_first_try": sum(agent_first_try(r) for r in results),
        "actions": sum(r.attempts for r in results),
        "wrong_actions": sum(len(r.failed_actions) for r in results),
        "engineer_actions": sum(len(r.engineer_actions) for r in results),
        "llm_errors": sum(r.llm_errors for r in results),
    }


def run_curve(service: IncidentService, admin: httpx.Client, faults: Sequence[Fault], rounds: int,
              wait: Callable[[], None], pause: Callable[[], None] = lambda: None) -> list[dict]:
    """Every fault once per round; memory from earlier rounds is available to later ones."""
    curve = []
    for number in range(1, rounds + 1):
        results = []
        for fault in faults:
            results.append(run_round(service, admin, fault, pause=pause))
            pause()
        curve.append({**round_summary(number, results),
                      "details": [{"fault": r.fault, "actions_tried": r.actions_tried,
                                   "engineer_actions": r.engineer_actions, "resolved": r.resolved,
                                   "memory_used": r.memory_used} for r in results]})
        if number < rounds:
            wait()
    return curve


def average(curves: Sequence[list[dict]]) -> list[dict]:
    """Mean of each round across repeated curves, with min and max for the charted numbers."""
    rows = []
    for rounds in zip(*curves):
        row = {"round": rounds[0]["round"]}
        for key in AVERAGED:
            row[key] = round(sum(r[key] for r in rounds) / len(rounds), 2)
        for key in RANGED:
            row[f"{key}_min"] = min(r[key] for r in rounds)
            row[f"{key}_max"] = max(r[key] for r in rounds)
        rows.append(row)
    return rows


def build_report(runs: Sequence[dict], bank_id: str) -> dict:
    """runs: one {"bank_id", "with_memory", "memory_off"} per repeat. Top-level curves are the averages."""
    baselines = [r["memory_off"] for r in runs if r.get("memory_off")]
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bank_id": bank_id,
        "repeats": len(runs),
        "approvals": "given by the evaluation harness",
        "with_memory": average([r["with_memory"] for r in runs]),
        "memory_off": average(baselines) if len(baselines) == len(runs) else None,
        "runs": list(runs),
    }


def repeat_bank_ids(bank_id: str, repeats: int) -> list[str]:
    return [bank_id] if repeats == 1 else [f"{bank_id}-r{n}" for n in range(1, repeats + 1)]


def print_report(report: dict) -> None:
    keys = ["relevant_recall", "agent_first_try", "agent_fixed", "wrong_actions", "engineer_actions"]
    for label in ("with_memory", "memory_off"):
        if not report.get(label):
            continue
        print(f"\n{label}")
        print(f"{'round':6}" + "".join(f"{k:>18}" for k in keys) + f"{'incidents':>11}")
        for row in report[label]:
            print(f"{row['round']:<6}" + "".join(f"{row[k]:>18}" for k in keys) + f"{row['incidents']:>11}")


def merge_reports(paths: Sequence[Path], bank_id: str) -> dict:
    """Combine reports from parallel runs into one averaged report."""
    runs = []
    for path in paths:
        runs += json.loads(Path(path).read_text(encoding="utf-8"))["runs"]
    return build_report(runs, bank_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure the agent's learning curve over repeated incidents.")
    parser.add_argument("--merge", nargs="+", type=Path, metavar="REPORT",
                        help="Combine reports of parallel runs into --out instead of running")
    parser.add_argument("--bank-id", required=True, help="Fresh Hindsight bank for this run (not the main bank)")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--baseline", action="store_true", help="Also run the rounds with memory switched off")
    parser.add_argument("--repeats", type=int, default=1, help="Run the experiment N times on fresh banks and average")
    parser.add_argument("--wait", type=float, default=20.0, help="Seconds to let Hindsight process new memories")
    parser.add_argument("--pause", type=float, default=20.0, help="Seconds between LLM calls (Groq free tier)")
    parser.add_argument("--out", type=Path, default=RESULTS_FILE)
    args = parser.parse_args(argv)
    if args.merge:
        report = merge_reports(args.merge, args.bank_id)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print_report(report)
        print(f"\nMerged {report['repeats']} run(s). Saved to {args.out}")
        return 0
    if args.bank_id == MAIN_BANK:
        parser.error(f"refusing to write evaluation incidents into the main bank {MAIN_BANK!r}; use a fresh bank ID")
    if args.rounds < 2:
        parser.error("a curve needs at least 2 rounds")
    if not 1 <= args.repeats <= 10:
        parser.error("--repeats must be between 1 and 10")
    banks = repeat_bank_ids(args.bank_id, args.repeats)
    try:
        for bank in banks:
            validate_bank_id(bank)
    except ConfigError as exc:
        parser.error(str(exc))

    from fastapi.testclient import TestClient

    from agent.actions import ShopFastClient
    from agent.llm import IncidentAdvisor
    from agent.memory import IncidentMemory
    from shopfast import app as shop

    admin = TestClient(shop.app)
    advisor = IncidentAdvisor(load_settings(banks[0]))
    wait, pause = (lambda: time.sleep(args.wait)), (lambda: time.sleep(args.pause))
    runs = []
    for number, bank in enumerate(banks, start=1):
        print(f"repeat {number}/{len(banks)} on bank {bank!r}", flush=True)
        with IncidentMemory(load_settings(bank)) as memory:
            memory.ensure_bank()
            memory.ensure_runbook()
            curve = run_curve(IncidentService(memory, advisor, ShopFastClient(admin)), admin, list(Fault),
                              args.rounds, wait, pause)
        baseline = None
        if args.baseline:
            baseline = run_curve(IncidentService(NoMemory(), advisor, ShopFastClient(admin)), admin, list(Fault),
                                 args.rounds, lambda: None, pause)
        runs.append({"bank_id": bank, "with_memory": curve, "memory_off": baseline})
        report = build_report(runs, args.bank_id)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")  # partial results survive a crash
    print_report(report)
    print(f"\nAveraged over {len(runs)} run(s). Saved to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
