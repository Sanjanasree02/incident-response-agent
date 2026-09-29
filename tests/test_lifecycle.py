import pytest

from agent.lifecycle import (
    JourneyStage,
    analyze_code,
    analyze_pipeline,
    analyze_runtime,
    analyze_user_journey,
    compare_deployment,
    generate_postmortem,
)
from agent.models import Incident, Outcome


def test_code_review_flags_n_plus_one_broad_catch_and_redacts_secret():
    assessment = analyze_code('''for order in orders:
    db.query(Order).get(order.id)
try:
    run_job()
except Exception:
    pass
api_key = "secret-value-123"
''')
    assert {finding.title for finding in assessment.findings} == {
        "Possible database call inside a loop", "Broad exception handling", "Possible hard-coded credential"
    }
    assert "secret-value-123" not in str(assessment)


def test_pull_request_review_scans_only_added_lines():
    assessment = analyze_code("-except Exception:\n+except ValueError:\n", diff=True)
    assert not assessment.findings


@pytest.mark.parametrize("log, title", [
    ("error: cannot find symbol CustomerDTO", "Build compilation failed"),
    ("FAILED tests/test_orders.py::test_checkout", "Test stage failed"),
    ("Readiness probe failed: HTTP probe failed", "Deployment health check failed"),
    ("CRITICAL CVE-2026-12345 detected", "Dependency security gate failed"),
])
def test_pipeline_classifies_failure_signatures(log, title):
    assessment = analyze_pipeline(log)
    assert title in {finding.title for finding in assessment.findings}


def test_pipeline_redacts_credential_evidence():
    assessment = analyze_pipeline('FAILED test: api_key="secret-value-123" Authorization: Bearer bearer-token-123')
    assert "secret-value-123" not in str(assessment)
    assert "bearer-token-123" not in str(assessment)


def test_deployment_comparison_flags_multiple_regressions():
    assessment = compare_deployment(
        {"error_rate_pct": 0.3, "p95_latency_ms": 180, "db_pool_pct": 50, "memory_pct": 45},
        {"error_rate_pct": 4.7, "p95_latency_ms": 920, "db_pool_pct": 95, "memory_pct": 60},
    )
    assert len(assessment.findings) == 3
    assert "Error rate regressed after deployment" in {f.title for f in assessment.findings}


def test_deployment_rejects_missing_or_out_of_range_metrics():
    with pytest.raises(ValueError, match="missing p95_latency_ms"):
        compare_deployment({"error_rate_pct": 1}, {"error_rate_pct": 1})


def test_runtime_alerts_on_saturation_and_pressure():
    assessment = analyze_runtime(7, 1200, 96, 91)
    assert len(assessment.findings) == 4


def test_user_journey_identifies_dropoff_and_browser_signals():
    assessment = analyze_user_journey(
        [JourneyStage(name="Login", users=1000), JourneyStage(name="Checkout", users=520)],
        javascript_errors=8, failed_requests=13, p75_load_ms=3100,
    )
    assert len(assessment.findings) == 4
    assert "User drop-off between Login and Checkout" in {f.title for f in assessment.findings}


def test_user_journey_rejects_increasing_funnel_counts():
    with pytest.raises(ValueError, match="cannot increase"):
        analyze_user_journey([JourneyStage(name="Login", users=10), JourneyStage(name="Cart", users=11)])


def test_postmortem_uses_validated_incident_outcome_and_timeline():
    incident = Incident(service="payment-api", severity="SEV1", title="Checkout failing", symptoms="Payments unavailable")
    outcome = Outcome(incident_id=incident.incident_id, resolved=True, actual_root_cause="Provider credential was stale",
                      steps_that_worked=["Refresh provider key"], failed_attempts=["Retry charges"], notes="Add rotation check")
    postmortem = generate_postmortem(incident, outcome, ["10:00 alert", "10:12 recovered"])
    assert postmortem.incident_id == incident.incident_id
    assert postmortem.timeline == ["10:00 alert", "10:12 recovered"]
    assert postmortem.resolution == ["Refresh provider key"]
    assert postmortem.lessons == ["Add rotation check"]


def test_postmortem_rejects_mismatched_outcome():
    incident = Incident(service="payment-api", severity="SEV1", title="Checkout failing", symptoms="Payments unavailable")
    outcome = Outcome(incident_id="INC-1042", resolved=False, actual_root_cause="Unknown provider issue")
    with pytest.raises(ValueError, match="IDs must match"):
        generate_postmortem(incident, outcome, [])