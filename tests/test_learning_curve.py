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


def test_report_shape_round_trips_as_json(tmp_path):
    report = lc.build_report([lc.round_summary(1, [])], None, "shopfast-curve-test")
    assert report["memory_off"] is None and report["with_memory"][0]["incidents"] == 0
    path = tmp_path / "curve.json"
    path.write_text(json.dumps(report))
    assert json.loads(path.read_text())["bank_id"] == "shopfast-curve-test"


@pytest.mark.parametrize("argv", [["--bank-id", "shopfast-incidents"], ["--bank-id", "shopfast-curve-x", "--rounds", "1"]])
def test_refuses_main_bank_and_single_round(argv):
    with pytest.raises(SystemExit):
        lc.main(argv)
