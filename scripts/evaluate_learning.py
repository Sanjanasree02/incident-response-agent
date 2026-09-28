"""Before-vs-after learning evaluation.

Replays each ShopFast fault twice through the agent's real flow (detect -> analyze -> remediate -> verify ->
record): first with an empty memory bank (BEFORE), then after all BEFORE outcomes were recorded (AFTER).
ShopFast runs in-process; Groq and Hindsight are real. Approvals are given by this harness, so the numbers
measure the agent's choices; the UI keeps its human approval gate. When the agent proposes no action, the harness
plays an engineer without memory who tries the runbook in listed order; those actions are counted separately and
recorded like any other, so the agent can learn from them.

Run (always against a fresh bank; the main bank is refused so demo memory stays clean):
  python -m scripts.evaluate_learning --bank-id shopfast-incidents-eval1
"""

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from agent.models import RemediationAttempt
from agent.service import IncidentService
from shopfast.faults import Fault

MAIN_BANK = "shopfast-incidents"
MAX_ATTEMPTS = 8
RESULTS_FILE = Path(__file__).resolve().parent.parent / "data" / "evaluation_results.json"


@dataclass
class RoundResult:
    fault: str
    incident_id: str
    memory_used: bool
    relevant_incidents: list[str]
    actions_tried: list[str] = field(default_factory=list)
    engineer_actions: list[str] = field(default_factory=list)  # tried by the engineer fallback, not the agent
    resolved: bool = False
    llm_errors: int = 0

    @property
    def attempts(self) -> int:
        return len(self.actions_tried)

    @property
    def first_action(self) -> str | None:
        return self.actions_tried[0] if self.actions_tried else None

    @property
    def first_try_fix(self) -> bool:
        return self.resolved and self.attempts == 1

    @property
    def agent_fixed(self) -> bool:
        """Resolved by an action the agent itself proposed."""
        return self.resolved and self.actions_tried[-1] not in self.engineer_actions

    @property
    def successful_action(self) -> str | None:
        return self.actions_tried[-1] if self.resolved else None

    @property
    def failed_actions(self) -> list[str]:
        return self.actions_tried[:-1] if self.resolved else list(self.actions_tried)


@dataclass
class Comparison:
    fault: str
    before: RoundResult
    after: RoundResult

    @property
    def recalled_learned_incident(self) -> bool:
        return self.before.incident_id in self.after.relevant_incidents

    @property
    def proposed_proven_fix_first(self) -> bool:
        return self.before.successful_action is not None and self.after.first_action == self.before.successful_action

    @property
    def failed_fixes_avoided(self) -> list[str]:
        return [a for a in self.before.failed_actions if a not in self.after.actions_tried]


def compare(before: RoundResult, after: RoundResult) -> Comparison:
    return Comparison(before.fault, before, after)


def _reset(admin: httpx.Client) -> None:
    for fault in admin.get("/admin/faults").json()["active"]:
        admin.delete(f"/admin/faults/{fault}")


def run_round(service: IncidentService, admin: httpx.Client, fault: Fault, max_attempts: int = MAX_ATTEMPTS,
              pause: Callable[[], None] = lambda: None) -> RoundResult:
    """One incident through the real flow. The harness plays the approver and approves every proposal."""
    _reset(admin)
    admin.post(f"/admin/faults/{fault.value}")
    try:
        incident = service.detect_incident()
        suggestion = service.analyze_incident(incident)
        result = RoundResult(fault=fault.value, incident_id=incident.incident_id, memory_used=suggestion.memory_used,
                             relevant_incidents=[s.incident_id for s in suggestion.similar_incidents])
        attempts: list[RemediationAttempt] = []
        runbook = [a["name"] for a in admin.get("/ops/actions").json()]
        while True:
            result.llm_errors += suggestion.llm_error is not None
            if len(attempts) == max_attempts:
                break
            engineer_choice = None
            if not suggestion.proposed_action:
                engineer_choice = next((a for a in runbook if a not in result.actions_tried), None)
                if engineer_choice is None:
                    break
                result.engineer_actions.append(engineer_choice)
            attempt = service.remediate(incident, suggestion, approved=True, previous_attempts=attempts,
                                        action=engineer_choice)
            attempts.append(attempt)
            result.actions_tried.append(attempt.action)
            if attempt.verified:
                result.resolved = True
                break
            pause()
            suggestion = service.analyze_incident(incident, tried_actions=[a.action for a in attempts])
        return result
    finally:
        _reset(admin)


def summarize(comparisons: Sequence[Comparison]) -> dict:
    before = [c.before for c in comparisons]
    after = [c.after for c in comparisons]
    return {
        "incidents": len(comparisons),
        "relevant_recall_before": sum(r.memory_used for r in before),
        "relevant_recall_after": sum(r.memory_used for r in after),
        "learned_incident_recalled_after": sum(c.recalled_learned_incident for c in comparisons),
        "proven_fix_proposed_first_after": sum(c.proposed_proven_fix_first for c in comparisons),
        "resolved_before": sum(r.resolved for r in before),
        "resolved_after": sum(r.resolved for r in after),
        "first_try_fix_before": sum(r.first_try_fix for r in before),
        "first_try_fix_after": sum(r.first_try_fix for r in after),
        "attempts_before": sum(r.attempts for r in before),
        "attempts_after": sum(r.attempts for r in after),
        "failed_fixes_before": sum(len(r.failed_actions) for r in before),
        "failed_fixes_avoided_after": sum(len(c.failed_fixes_avoided) for c in comparisons),
        "agent_fixed_before": sum(r.agent_fixed for r in before),
        "agent_fixed_after": sum(r.agent_fixed for r in after),
        "engineer_actions_before": sum(len(r.engineer_actions) for r in before),
        "engineer_actions_after": sum(len(r.engineer_actions) for r in after),
        "llm_errors": sum(r.llm_errors for r in before + after),
        "approvals": "given by the evaluation harness",
    }


def evaluate(service: IncidentService, admin: httpx.Client, faults: Sequence[Fault],
             wait: Callable[[], None], pause: Callable[[], None] = lambda: None) -> dict:
    """All BEFORE rounds on empty memory, then wait for memory, then all AFTER rounds."""
    before = []
    for fault in faults:
        before.append(run_round(service, admin, fault, pause=pause))
        pause()
    wait()
    after = []
    for fault in faults:
        after.append(run_round(service, admin, fault, pause=pause))
        pause()
    comparisons = [compare(b, a) for b, a in zip(before, after)]
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": summarize(comparisons),
        "incidents": [
            {
                "fault": c.fault,
                "before": {**asdict(c.before), "attempts": c.before.attempts, "resolved": c.before.resolved},
                "after": {**asdict(c.after), "attempts": c.after.attempts, "resolved": c.after.resolved},
                "recalled_learned_incident": c.recalled_learned_incident,
                "proposed_proven_fix_first": c.proposed_proven_fix_first,
                "failed_fixes_avoided": c.failed_fixes_avoided,
            }
            for c in comparisons
        ],
    }


def write_report(report: dict, path: Path = RESULTS_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def print_report(report: dict) -> None:
    print(f"{'fault':26} {'before: memory / tried':52} {'after: memory / tried'}")
    for item in report["incidents"]:
        b, a = item["before"], item["after"]
        print(f"{item['fault']:26} {str(b['memory_used']):5} {' > '.join(b['actions_tried']) or '-':46} "
              f"{str(a['memory_used']):5} {' > '.join(a['actions_tried']) or '-'}")
    print()
    for key, value in report["summary"].items():
        print(f"  {key}: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure the agent before and after learning.")
    parser.add_argument("--bank-id", required=True, help="Fresh Hindsight bank for this run (not the main bank)")
    parser.add_argument("--wait", type=float, default=15.0, help="Seconds to let Hindsight process new memories")
    parser.add_argument("--pause", type=float, default=20.0, help="Seconds between LLM calls (Groq free tier)")
    parser.add_argument("--out", type=Path, default=RESULTS_FILE)
    args = parser.parse_args(argv)
    if args.bank_id == MAIN_BANK:
        parser.error(f"refusing to write evaluation incidents into the main bank {MAIN_BANK!r}; use a fresh bank ID")

    from fastapi.testclient import TestClient

    from agent.actions import ShopFastClient
    from agent.config import load_settings
    from agent.llm import IncidentAdvisor
    from agent.memory import IncidentMemory
    from shopfast import app as shop

    settings = load_settings(args.bank_id)
    admin = TestClient(shop.app)
    with IncidentMemory(settings) as memory:
        memory.ensure_bank()
        memory.ensure_runbook()
        service = IncidentService(memory, IncidentAdvisor(settings), ShopFastClient(admin))
        report = evaluate(service, admin, list(Fault), wait=lambda: time.sleep(args.wait),
                          pause=lambda: time.sleep(args.pause))
    report["bank_id"] = args.bank_id
    write_report(report, args.out)
    print_report(report)
    print(f"\nSaved to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
