"""Load data/seed_incidents.json, validate each record, and retain it into Hindsight.

Run:
  python -m scripts.seed_memory                                   # bank from HINDSIGHT_BANK_ID
  python -m scripts.seed_memory --bank-id shopfast-incidents-demo3  # fresh bank for a demo rerun

Demo reset: seed a new bank ID and set HINDSIGHT_BANK_ID to it. Old banks are left untouched,
so nothing is deleted and earlier runs stay available for comparison.
"""

import argparse
import json
import sys
from pathlib import Path

from agent.config import load_settings
from agent.memory import IncidentMemory, IncidentMemoryError
from agent.models import HistoricalIncident

SEED_FILE = Path(__file__).resolve().parent.parent / "data" / "seed_incidents.json"


def load_seed_incidents(path: Path = SEED_FILE) -> list[HistoricalIncident]:
    """Parse and validate the seed file. Raises pydantic.ValidationError on bad records."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [HistoricalIncident.model_validate(item) for item in raw]


def seed(memory: IncidentMemory, incidents: list[HistoricalIncident]) -> list[str]:
    """Create the bank, then retain each incident. Keeps going after a failed retain; returns the failed IDs."""
    memory.ensure_bank()
    failed = []
    for incident in incidents:
        try:
            memory.retain_historical(incident)
            print(f"  retained {incident.incident_id}")
        except IncidentMemoryError as exc:
            failed.append(incident.incident_id)
            print(f"  FAILED {incident.incident_id}: {exc}", file=sys.stderr)
    try:
        memory.ensure_runbook()
        print("  living runbook (mental model) ready")
    except IncidentMemoryError as exc:
        print(f"  living runbook not created: {exc}", file=sys.stderr)
    return failed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed Hindsight with ShopFast incident history.")
    parser.add_argument("--bank-id", help="Target bank ID (default: HINDSIGHT_BANK_ID from .env)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    incidents = load_seed_incidents()
    print(f"Validated {len(incidents)} seed incidents.")
    with IncidentMemory(load_settings(args.bank_id)) as memory:
        print(f"Seeding bank {memory.bank_id!r}...")
        failed = seed(memory, incidents)
    if failed:
        print(f"{len(failed)} of {len(incidents)} incidents failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    print(f"Done: {len(incidents)} incidents retained in {memory.bank_id!r}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
