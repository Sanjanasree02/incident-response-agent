"""Learning-curve harness, offline: in-process ShopFast, store memory and scripted advisor from the evaluation tests."""

import json

import pytest
from fastapi.testclient import TestClient

from agent.actions import ShopFastClient
from agent.service import IncidentService
from scripts import learning_curve as lc
from shopfast import app as shop
from shopfast.faults import Fault
from tests.test_evaluate_learning import DECOY, ScriptedAdvisor, SilentAdvisor, StoreMemory

FAULTS = [Fault.DB_POOL_EXHAUST, Fault.PAYMENT_GATEWAY_TIMEOUT]


@pytest.fixture
def http(monkeypatch):
    monkeypatch.setattr(shop, "faults", shop.FaultRegistry())
    monkeypatch.setattr(shop, "alerts", shop.AlertLog())
    return TestClient(shop.app)


def test_curve_improves_with_memory(http):
    waits = []
    service = IncidentService(StoreMemory(), ScriptedAdvisor(), shop=ShopFastClient(http))
    curve = lc.run_curve(service, http, FAULTS, rounds=3, wait=lambda: waits.append(1))
    assert [r["round"] for r in curve] == [1, 2, 3]
    assert len(waits) == 2  # between rounds only
    first, later = curve[0], curve[1:]
    assert (first["relevant_recall"], first["agent_first_try"], first["wrong_actions"]) == (0, 0, 2)
    for row in later:
        assert (row["relevant_recall"], row["agent_first_try"], row["wrong_actions"]) == (2, 2, 0)
    assert curve[0]["details"][0]["actions_tried"] == [DECOY, "rollback_payment_api"]


def test_memory_off_baseline_stays_flat(http):
    service = IncidentService(lc.NoMemory(), ScriptedAdvisor(), shop=ShopFastClient(http))
    curve = lc.run_curve(service, http, FAULTS, rounds=3, wait=lambda: None)
    assert {(r["relevant_recall"], r["agent_first_try"], r["wrong_actions"]) for r in curve} == {(0, 0, 2)}


def test_engineer_fallback_is_counted_separately(http):
    service = IncidentService(StoreMemory(), SilentAdvisor(), shop=ShopFastClient(http))
    first, second = lc.run_curve(service, http, [Fault.REDIS_TIMEOUT], rounds=2, wait=lambda: None)
    assert first["engineer_actions"] > 0 and first["agent_fixed"] == 0 and first["resolved"] == 1
    assert (second["engineer_actions"], second["agent_fixed"], second["agent_first_try"]) == (0, 1, 1)


def _row(n, first, wrong):
    return {**lc.round_summary(n, []), "incidents": 4, "agent_first_try": first, "wrong_actions": wrong}


def test_report_averages_repeats_with_min_and_max():
    runs = [{"bank_id": "b-r1", "with_memory": [_row(1, 2, 4), _row(2, 4, 0)], "memory_off": [_row(1, 2, 3), _row(2, 2, 3)]},
            {"bank_id": "b-r2", "with_memory": [_row(1, 1, 5), _row(2, 3, 1)], "memory_off": [_row(1, 3, 2), _row(2, 2, 2)]}]
    report = lc.build_report(runs, "b")
    first, second = report["with_memory"]
    assert (first["agent_first_try"], first["agent_first_try_min"], first["agent_first_try_max"]) == (1.5, 1, 2)
    assert (second["wrong_actions"], second["wrong_actions_min"], second["wrong_actions_max"]) == (0.5, 0, 1)
    assert report["memory_off"][0]["wrong_actions"] == 2.5
    assert report["repeats"] == 2 and report["runs"] == runs


def test_baseline_average_needs_every_repeat():
    runs = [{"bank_id": "b", "with_memory": [_row(1, 2, 4)], "memory_off": None}]
    assert lc.build_report(runs, "b")["memory_off"] is None


def test_repeat_bank_ids():
    assert lc.repeat_bank_ids("shopfast-curve-2", 1) == ["shopfast-curve-2"]
    assert lc.repeat_bank_ids("shopfast-curve-2", 3) == ["shopfast-curve-2-r1", "shopfast-curve-2-r2", "shopfast-curve-2-r3"]


def test_report_shape_round_trips_as_json(tmp_path):
    report = lc.build_report([{"bank_id": "shopfast-curve-test", "with_memory": [lc.round_summary(1, [])],
                               "memory_off": None}], "shopfast-curve-test")
    assert report["memory_off"] is None and report["with_memory"][0]["incidents"] == 0
    path = tmp_path / "curve.json"
    path.write_text(json.dumps(report))
    assert json.loads(path.read_text())["bank_id"] == "shopfast-curve-test"


@pytest.mark.parametrize("argv", [["--bank-id", "shopfast-incidents"], ["--bank-id", "shopfast-curve-x", "--rounds", "1"],
                                  ["--bank-id", "shopfast-curve-x", "--repeats", "0"],
                                  ["--bank-id", "x" * 62, "--repeats", "3"]])
def test_refuses_main_bank_and_single_round(argv):
    with pytest.raises(SystemExit):
        lc.main(argv)


def test_merge_combines_parallel_runs(tmp_path):
    parts = []
    for n, first in ((1, 2), (2, 4)):
        part = tmp_path / f"r{n}.json"
        part.write_text(json.dumps(lc.build_report([{"bank_id": f"b-r{n}", "with_memory": [_row(1, first, 0)],
                                                     "memory_off": [_row(1, 2, 2)]}], f"b-r{n}")))
        parts.append(part)
    out = tmp_path / "merged.json"
    assert lc.main(["--bank-id", "b", "--merge", *map(str, parts), "--out", str(out)]) == 0
    merged = json.loads(out.read_text())
    assert merged["repeats"] == 2 and merged["with_memory"][0]["agent_first_try"] == 3
    assert [r["bank_id"] for r in merged["runs"]] == ["b-r1", "b-r2"]
