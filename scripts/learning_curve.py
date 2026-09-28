"""Learning curve: how the agent improves as the same kinds of incidents keep happening.

Replays every ShopFast fault once per round through the agent's real flow (detect -> analyze -> remediate ->
verify -> record), for several rounds, against one fresh memory bank. Each round starts with everything the earlier
rounds recorded. With --baseline the same rounds also run with memory switched off (nothing recalled, nothing
retained), so the chart can show the agent with and without Hindsight side by side.

Approvals are given by the harness, and when the agent proposes nothing the harness plays an engineer without
memory who tries the runbook in listed order (see scripts/evaluate_learning.py). The UI keeps its human gate.

Run (always against a fresh bank; the main bank is refused):
  python -m scripts.learning_curve --bank-id shopfast-curve-1 --rounds 3 --baseline
"""

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path

import httpx

from agent.service import IncidentService
from scripts.evaluate_learning import MAIN_BANK, RoundResult, run_round
from shopfast.faults import Fault

RESULTS_FILE = Path(__file__).resolve().parent.parent / "data" / "learning_curve.json"


class NoMemory:
    """Memory switched off: recalls nothing and forgets every outcome. Used for the baseline curve."""

    def recall_similar(self, incident):
        return []

    def recall_learned_patterns(self, incident):
        return []

    def retain_outcome(self, incident, outcome):
        pass


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


def build_report(with_memory: list[dict], baseline: list[dict] | None, bank_id: str) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bank_id": bank_id,
        "approvals": "given by the evaluation harness",
        "with_memory": with_memory,
        "memory_off": baseline,
    }


def print_report(report: dict) -> None:
    keys = ["relevant_recall", "agent_first_try", "agent_fixed", "wrong_actions", "engineer_actions"]
    for label in ("with_memory", "memory_off"):
        if not report.get(label):
            continue
        print(f"\n{label}")
        print(f"{'round':6}" + "".join(f"{k:>18}" for k in keys) + f"{'incidents':>11}")
        for row in report[label]:
            print(f"{row['round']:<6}" + "".join(f"{row[k]:>18}" for k in keys) + f"{row['incidents']:>11}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure the agent's learning curve over repeated incidents.")
    parser.add_argument("--bank-id", required=True, help="Fresh Hindsight bank for this run (not the main bank)")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--baseline", action="store_true", help="Also run the rounds with memory switched off")
    parser.add_argument("--wait", type=float, default=20.0, help="Seconds to let Hindsight process new memories")
    parser.add_argument("--pause", type=float, default=20.0, help="Seconds between LLM calls (Groq free tier)")
    parser.add_argument("--out", type=Path, default=RESULTS_FILE)
    args = parser.parse_args(argv)
    if args.bank_id == MAIN_BANK:
        parser.error(f"refusing to write evaluation incidents into the main bank {MAIN_BANK!r}; use a fresh bank ID")
    if args.rounds < 2:
        parser.error("a curve needs at least 2 rounds")

    from fastapi.testclient import TestClient

    from agent.actions import ShopFastClient
    from agent.config import load_settings
    from agent.llm import IncidentAdvisor
    from agent.memory import IncidentMemory
    from shopfast import app as shop

    settings = load_settings(args.bank_id)
    admin = TestClient(shop.app)
    advisor = IncidentAdvisor(settings)
    wait, pause = (lambda: time.sleep(args.wait)), (lambda: time.sleep(args.pause))
    with IncidentMemory(settings) as memory:
        memory.ensure_bank()
        memory.ensure_runbook()
        curve = run_curve(IncidentService(memory, advisor, ShopFastClient(admin)), admin, list(Fault),
                          args.rounds, wait, pause)
    baseline = None
    if args.baseline:
        baseline = run_curve(IncidentService(NoMemory(), advisor, ShopFastClient(admin)), admin, list(Fault),
                             args.rounds, lambda: None, pause)
    report = build_report(curve, baseline, args.bank_id)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print_report(report)
    print(f"\nSaved to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
