"""Streamlit workflows for local software lifecycle analysis modules."""

import json

import streamlit as st
from pydantic import ValidationError

from agent.lifecycle import (
    JourneyStage,
    LifecycleAssessment,
    Postmortem,
    generate_postmortem,
    sanitize_analysis_input,
)
from agent.llm import LLMError
from agent.memory import IncidentMemoryError
from agent.models import MAX_LOG_CHARS, Incident, Outcome, Severity

MODULES = [
    "Development and pull request review",
    "CI/CD pipeline intelligence",
    "Deployment intelligence",
    "Runtime monitoring",
    "Frontend and user journeys",
    "Post-incident review",
]


def _show_assessment(assessment: LifecycleAssessment) -> None:
    st.subheader(assessment.module)
    st.caption("AI analysis grounded in the supplied evidence")
    st.text(assessment.summary)
    for finding in assessment.findings:
        with st.container(border=True):
            st.text(f"{finding.severity.upper()} | {finding.title}")
            st.text(f"Evidence: {finding.evidence}")
            st.text(f"Recommendation: {finding.recommendation}")


def _analyze_with_agent(service, module: str, input_text: str) -> None:
    st.session_state.pop("lifecycle_assessment_module", None)
    st.session_state.pop("lifecycle_incident", None)
    try:
        assessment = service.analyze_lifecycle(module, input_text)
        highest = next((finding.severity for finding in assessment.findings
                        if finding.severity in ("critical", "high")), None)
        if highest is None and any(f.severity == "medium" for f in assessment.findings):
            highest = "medium"
        severity = Severity.SEV1 if highest in ("critical", "high") else Severity.SEV2 if highest else Severity.SEV3
        service_name = {
            MODULES[0]: "development-review",
            MODULES[1]: "ci-cd-pipeline",
            MODULES[2]: "deployment-monitor",
            MODULES[3]: "runtime-monitor",
            MODULES[4]: "frontend-monitor",
        }[module]
        incident = Incident(
            service=service_name,
            severity=severity,
            title=f"{module} assessment",
            symptoms=assessment.summary,
            error_log=sanitize_analysis_input(input_text)[:MAX_LOG_CHARS],
        )
    except LLMError as exc:
        st.error(f"Agent analysis unavailable: {exc}")
        return
    except (ValueError, KeyError) as exc:
        st.error(f"Unable to analyze this input: {exc}")
        return
    st.session_state["lifecycle_assessment"] = assessment
    st.session_state["lifecycle_assessment_module"] = module
    st.session_state["lifecycle_incident"] = incident
    st.session_state["lifecycle_outcome_recorded"] = False


def _record_lifecycle_outcome(service, module: str) -> None:
    incident = st.session_state.get("lifecycle_incident")
    if incident is None or st.session_state.get("lifecycle_assessment_module") != module:
        return
    incident_id = incident.incident_id
    if st.session_state.get("lifecycle_outcome_recorded"):
        st.success(f"Confirmed outcome for {incident_id} recorded in incident memory.")
        return
    st.markdown("**Record the confirmed outcome**")
    st.caption("Record what actually happened after investigation; the AI analysis is not treated as the confirmed cause.")
    with st.form(f"lifecycle_outcome_form_{incident_id}"):
        resolved = st.checkbox("Resolved", value=True, key=f"lifecycle_resolved_{incident_id}")
        root_cause = st.text_area("Actual root cause", key=f"lifecycle_root_cause_{incident_id}")
        worked = st.text_area("Steps that worked (one per line)", key=f"lifecycle_worked_{incident_id}")
        failed = st.text_area("Failed attempts (one per line)", key=f"lifecycle_failed_{incident_id}")
        notes = st.text_area("Lessons and follow-up", key=f"lifecycle_notes_{incident_id}")
        submitted = st.form_submit_button("Record confirmed outcome", key=f"lifecycle_record_{incident_id}")
    if submitted:
        try:
            outcome = Outcome(
                incident_id=incident_id,
                resolved=resolved,
                actual_root_cause=root_cause,
                steps_that_worked=[line.strip() for line in worked.splitlines() if line.strip()],
                failed_attempts=[line.strip() for line in failed.splitlines() if line.strip()],
                notes=notes.strip(),
            )
            service.record_outcome(incident, outcome)
            st.session_state["lifecycle_outcome_recorded"] = True
            st.rerun()
        except (ValidationError, IncidentMemoryError) as exc:
            st.error(f"Could not record outcome: {exc}")


def _show_code_review(service) -> None:
    mode = st.selectbox("Review type", ["Code scan", "Pull request diff"], key="lifecycle_code_mode")
    sample = (
        "diff --git a/orders.py b/orders.py\n"
        "+for order in orders:\n"
        "+    db.query(Order).get(order.id)\n"
        "+except Exception:\n"
        "+    pass\n"
        if mode == "Pull request diff" else
        "for order in orders:\n    db.query(Order).get(order.id)\n\ntry:\n    run_job()\nexcept Exception:\n    pass\n"
    )
    with st.form("lifecycle_code_form"):
        source = st.text_area("Source code or diff", value=sample, height=240, key="lifecycle_code_input")
        submitted = st.form_submit_button("Review changes", key="lifecycle_code_analyze")
    if submitted:
        _analyze_with_agent(service, MODULES[0], f"Review type: {mode}\n{source}")
    if st.session_state.get("lifecycle_assessment_module") == MODULES[0]:
        _show_assessment(st.session_state["lifecycle_assessment"])
        _record_lifecycle_outcome(service, MODULES[0])


def _show_pipeline(service) -> None:
    sample = "[INFO] Compiling orders-service\nerror: cannot find symbol CustomerDTO\nBUILD FAILURE"
    with st.form("lifecycle_pipeline_form"):
        log = st.text_area("Build, test, security scan, or deployment log", value=sample, height=200,
                           key="lifecycle_pipeline_input")
        submitted = st.form_submit_button("Analyze pipeline", key="lifecycle_pipeline_analyze")
    if submitted:
        _analyze_with_agent(service, MODULES[1], log)
    if st.session_state.get("lifecycle_assessment_module") == MODULES[1]:
        _show_assessment(st.session_state["lifecycle_assessment"])
        _record_lifecycle_outcome(service, MODULES[1])


def _metric_inputs(prefix: str, defaults: tuple[float, float, float, float]) -> dict[str, float]:
    labels = ["Error rate (%)", "p95 latency (ms)", "Database pool (%)", "Memory (%)"]
    keys = ["error_rate_pct", "p95_latency_ms", "db_pool_pct", "memory_pct"]
    columns = st.columns(2)
    values = {}
    for index, (label, key, default) in enumerate(zip(labels, keys, defaults)):
        with columns[index % 2]:
            values[key] = st.number_input(label, min_value=0.0, value=default,
                                          max_value=100.0 if key.endswith("_pct") else 1_000_000.0,
                                          key=f"{prefix}_{key}")
    return values


def _show_deployment(service) -> None:
    with st.form("lifecycle_deployment_form"):
        st.caption("Compare the release baseline with the post-deployment snapshot.")
        before_column, after_column = st.columns(2)
        with before_column:
            st.markdown("**Before deployment**")
            before = _metric_inputs("deployment_before", (0.3, 180.0, 50.0, 45.0))
        with after_column:
            st.markdown("**After deployment**")
            after = _metric_inputs("deployment_after", (4.7, 920.0, 95.0, 60.0))
        submitted = st.form_submit_button("Compare deployment", key="lifecycle_deployment_analyze")
    if submitted:
        _analyze_with_agent(service, MODULES[2], json.dumps({"before": before, "after": after}, sort_keys=True))
    if st.session_state.get("lifecycle_assessment_module") == MODULES[2]:
        _show_assessment(st.session_state["lifecycle_assessment"])
        _record_lifecycle_outcome(service, MODULES[2])


def _show_runtime(service) -> None:
    with st.form("lifecycle_runtime_form"):
        values = _metric_inputs("runtime", (7.0, 1200.0, 96.0, 91.0))
        submitted = st.form_submit_button("Assess runtime", key="lifecycle_runtime_analyze")
    if submitted:
        _analyze_with_agent(service, MODULES[3], json.dumps(values, sort_keys=True))
    if st.session_state.get("lifecycle_assessment_module") == MODULES[3]:
        _show_assessment(st.session_state["lifecycle_assessment"])
        _record_lifecycle_outcome(service, MODULES[3])


def _show_user_journey(service) -> None:
    sample = '[{"name":"Login","users":1000},{"name":"Dashboard","users":920},{"name":"Search","users":850},{"name":"Cart","users":710},{"name":"Checkout","users":520}]'
    with st.form("lifecycle_journey_form"):
        raw_stages = st.text_area("Ordered journey stages (JSON)", value=sample, height=160,
                                  key="lifecycle_journey_stages")
        columns = st.columns(3)
        javascript_errors = columns[0].number_input("JavaScript errors", min_value=0, value=8,
                                                    key="lifecycle_javascript_errors")
        failed_requests = columns[1].number_input("Failed requests", min_value=0, value=13,
                                                  key="lifecycle_failed_requests")
        p75_load_ms = columns[2].number_input("p75 load (ms)", min_value=0.0, value=3100.0,
                                              key="lifecycle_p75_load")
        submitted = st.form_submit_button("Analyze user journey", key="lifecycle_journey_analyze")
    if submitted:
        try:
            stages = [JourneyStage.model_validate(item) for item in json.loads(raw_stages)]
            _analyze_with_agent(
                service, MODULES[4],
                json.dumps({"stages": [stage.model_dump() for stage in stages],
                            "javascript_errors": javascript_errors,
                            "failed_requests": failed_requests, "p75_load_ms": p75_load_ms}, sort_keys=True),
            )
        except (ValueError, TypeError, ValidationError) as exc:
            st.error(f"Invalid journey input: {exc}")
    if st.session_state.get("lifecycle_assessment_module") == MODULES[4]:
        _show_assessment(st.session_state["lifecycle_assessment"])
        _record_lifecycle_outcome(service, MODULES[4])


def _show_postmortem(service) -> None:
    with st.form("lifecycle_postmortem_form"):
        service_name = st.text_input("Service", value="payment-api", key="postmortem_service")
        severity = st.selectbox("Severity", [item.value for item in Severity], key="postmortem_severity")
        title = st.text_input("Incident title", value="Checkout failures after deployment", key="postmortem_title")
        symptoms = st.text_area("Customer and system impact", value="Checkout error rate increased after release.",
                                key="postmortem_impact")
        root_cause = st.text_area("Confirmed root cause", key="postmortem_root_cause")
        resolution = st.text_area("Steps that worked (one per line)", key="postmortem_resolution")
        failures = st.text_area("Failed attempts (one per line)", key="postmortem_failures")
        lessons = st.text_area("Lessons and follow-up", key="postmortem_lessons")
        timeline = st.text_area("Timeline (one event per line)", key="postmortem_timeline")
        resolved = st.checkbox("Incident resolved", value=True, key="postmortem_resolved")
        submitted = st.form_submit_button("Draft postmortem", key="lifecycle_postmortem_generate")
    if submitted:
        try:
            incident = Incident(service=service_name, severity=severity, title=title, symptoms=symptoms)
            outcome = Outcome(
                incident_id=incident.incident_id,
                resolved=resolved,
                actual_root_cause=root_cause,
                steps_that_worked=[line.strip() for line in resolution.splitlines() if line.strip()],
                failed_attempts=[line.strip() for line in failures.splitlines() if line.strip()],
                notes=lessons.strip(),
            )
            st.session_state["lifecycle_postmortem"] = generate_postmortem(incident, outcome, timeline.splitlines())
            st.session_state["lifecycle_postmortem_incident"] = incident
            st.session_state["lifecycle_postmortem_outcome"] = outcome
            st.session_state["lifecycle_postmortem_recorded"] = False
            st.session_state["lifecycle_assessment_module"] = MODULES[5]
        except (ValueError, ValidationError) as exc:
            st.error(f"Complete the required incident fields: {exc}")
    if st.session_state.get("lifecycle_assessment_module") == MODULES[5]:
        report: Postmortem = st.session_state["lifecycle_postmortem"]
        st.subheader(report.title)
        st.text(f"Incident: {report.incident_id}")
        st.text(f"Summary: {report.summary}")
        st.text(f"Impact: {report.impact}")
        st.text(f"Root cause: {report.root_cause}")
        st.text("Timeline:\n" + "\n".join(report.timeline))
        st.text("Resolution:\n" + "\n".join(report.resolution))
        st.text("Failed attempts:\n" + "\n".join(report.failed_attempts))
        st.text("Lessons:\n" + "\n".join(report.lessons))
        st.download_button("Download postmortem JSON", report.model_dump_json(indent=2),
                           file_name=f"{report.incident_id.lower()}-postmortem.json",
                           mime="application/json", key="download_postmortem")
        if st.session_state.get("lifecycle_postmortem_recorded"):
            st.success("Confirmed outcome and lessons recorded in incident memory.")
        elif st.button("Record confirmed outcome in incident memory", key="record_postmortem_learning"):
            try:
                service.record_outcome(st.session_state["lifecycle_postmortem_incident"],
                                       st.session_state["lifecycle_postmortem_outcome"])
                st.session_state["lifecycle_postmortem_recorded"] = True
                st.rerun()
            except IncidentMemoryError as exc:
                st.error(f"Memory unavailable: {exc}")


def show_lifecycle_modules(service) -> None:
    st.caption("The Agent analyzes submitted evidence. External Git, CI, cloud, and browser integrations are not connected.")
    module = st.selectbox("Lifecycle module", MODULES, key="lifecycle_module")
    if module == MODULES[0]:
        _show_code_review(service)
    elif module == MODULES[1]:
        _show_pipeline(service)
    elif module == MODULES[2]:
        _show_deployment(service)
    elif module == MODULES[3]:
        _show_runtime(service)
    elif module == MODULES[4]:
        _show_user_journey(service)
    else:
        _show_postmortem(service)