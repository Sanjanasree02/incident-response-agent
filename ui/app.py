"""Streamlit UI. Run: streamlit run ui/app.py

Render all user text with st.text / st.code / st.markdown without unsafe_allow_html.
Incident text and LLM output go through st.text or st.code only, never markdown, so a crafted log or
LLM reply cannot render links or images.
"""

import json
import sys
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # streamlit puts ui/ on the path, not the repo root

from agent.actions import ShopFastClient, ShopFastError  # noqa: E402
from agent.config import ConfigError, load_settings  # noqa: E402
from agent.llm import IncidentAdvisor  # noqa: E402
from agent.memory import IncidentMemory, IncidentMemoryError  # noqa: E402
from agent.models import Incident, Outcome, Postmortem, RemediationAttempt, Severity, Suggestion  # noqa: E402
from agent.service import IncidentService  # noqa: E402
from shopfast.faults import FAULT_LOGS, Fault  # noqa: E402

EVAL_RESULTS = Path(__file__).resolve().parent.parent / "data" / "evaluation_results.json"
CURVE_RESULTS = Path(__file__).resolve().parent.parent / "data" / "learning_curve.json"


@st.cache_resource
def build_service() -> IncidentService:
    settings = load_settings()
    return IncidentService(IncidentMemory(settings), IncidentAdvisor(settings), ShopFastClient.from_settings(settings))


def get_service() -> IncidentService:
    """The service from session state (tests put a fake there), else the real one."""
    if "service" not in st.session_state:
        try:
            st.session_state["service"] = build_service()
        except ConfigError as exc:
            st.error(f"Configuration error: {exc}. Fill in .env (see .env.example) and restart.")
            st.stop()
    return st.session_state["service"]


def validation_message(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in err['loc']) or 'input'}: {err['msg']}" for err in exc.errors())


def lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def load_example() -> None:
    st.session_state["error_log"] = FAULT_LOGS[Fault(st.session_state["example_fault"])]


def show_suggestion(incident: Incident, suggestion: Suggestion) -> None:
    st.subheader("Suggestion")
    st.text(f"Incident {incident.incident_id}  |  {incident.severity.value}  |  {incident.service}  |  "
            f"confidence: {suggestion.confidence}")
    st.text(incident.title)
    if suggestion.llm_error:
        st.warning(f"AI suggestion unavailable: {suggestion.llm_error}")
    if suggestion.memory_used:
        st.success(f"Based on {len(suggestion.similar_incidents)} similar past incident(s) from memory.")
    else:
        st.info("No similar past incident in memory. This is a generic answer.")

    st.markdown("**Probable root cause**")
    st.text(suggestion.probable_root_cause)
    if suggestion.fix_steps:
        st.markdown("**Fix steps, in order**")
        for number, step in enumerate(suggestion.fix_steps, start=1):
            st.text(f"{number}. {step}")
    if suggestion.avoid_steps:
        st.markdown("**Avoid (failed before)**")
        for step in suggestion.avoid_steps:
            st.text(f"- {step}")

    show_evidence(suggestion)

    if suggestion.similar_incidents:
        st.subheader("Similar past incidents")
        for item in suggestion.similar_incidents:
            with st.expander(item.incident_id):
                st.text(item.summary)
                if item.source_text:
                    st.caption("Source stored in memory")
                    st.code(item.source_text, language=None)


def show_attempt(attempt: RemediationAttempt) -> None:
    decision = "approved" if attempt.approved else "rejected"
    if not attempt.approved:
        result = "not run"
    elif not attempt.executed:
        result = "could not run"
    else:
        result = "verified healthy" if attempt.verified else "still failing"
    health = ", ".join(f"{endpoint} {status}" for endpoint, status in attempt.health.items())
    st.text(f"{attempt.at:%H:%M:%S}  {attempt.action} (chosen by {attempt.chosen_by})  |  {decision}  |  {result}"
            + (f"  |  {health}" if health else ""))
    st.caption(f"Why: {attempt.reason}" if attempt.reason else attempt.description)
    if attempt.error:
        st.warning(attempt.error)


def action_panel(service: IncidentService, incident_id: str) -> None:
    """Approval gate and audit log: the agent acts only on an explicit human decision."""
    incident, suggestion = st.session_state["incidents"][incident_id]
    attempts: list[RemediationAttempt] = st.session_state.setdefault("attempts", {}).setdefault(incident_id, [])
    resolved = any(a.verified for a in attempts)
    decided = {a.action for a in attempts}

    if suggestion.proposed_action and not resolved and suggestion.proposed_action not in decided:
        st.subheader("Proposed action")
        st.text(suggestion.proposed_action)
        if suggestion.action_reason:
            st.text(f"Why: {suggestion.action_reason}")
        st.caption("The agent runs this allow-listed ShopFast action only if you approve it, then verifies checkout.")
        approve, reject = st.columns(2)
        decision = None
        if approve.button("Approve and run", key="approve_action", type="primary"):
            decision = True
        if reject.button("Reject", key="reject_action"):
            decision = False
        if decision is not None:
            with st.spinner("Running action and verifying ShopFast..."):
                attempt = service.remediate(incident, suggestion, approved=decision, previous_attempts=list(attempts))
            attempts.append(attempt)
            st.rerun()  # redraw from the new state, so the decided action's buttons disappear

    if not resolved:
        engineer_choice(service, incident, suggestion, attempts, decided)

    if attempts:
        st.subheader("Action log")
        for attempt in attempts:
            show_attempt(attempt)
        last = attempts[-1]
        if last.verified:
            st.success("Checkout is healthy again. The outcome was recorded in memory automatically.")
        elif last.executed:
            st.error("The action ran but checkout is still failing. The failed attempt was recorded in memory.")
        if not resolved and st.button("Ask the agent for the next action", key="next_action"):
            with st.spinner("Re-analyzing without the actions already tried..."):
                suggestion = service.analyze_incident(incident, tried_actions=[a.action for a in attempts])
            st.session_state["incidents"][incident_id] = (incident, suggestion)
            st.rerun()

    if any(a.executed for a in attempts):
        postmortem_panel(service, incident, suggestion, attempts)


def engineer_choice(service: IncidentService, incident: Incident, suggestion: Suggestion,
                    attempts: list[RemediationAttempt], decided: set[str]) -> None:
    """The engineer can run a different allow-listed action. Its outcome is recorded too, so the agent learns it."""
    names = [a.name for a in service.list_actions() if a.name not in decided]
    if not names:
        return
    with st.expander("Run a different action (engineer's choice)",
                     expanded=not suggestion.proposed_action or suggestion.proposed_action in decided):
        st.selectbox("Allow-listed action", names, key="engineer_action")
        st.caption("Runs now, then verifies ShopFast. The result is recorded in memory like the agent's own actions.")
        if st.button("Run chosen action", key="run_engineer_action"):
            with st.spinner("Running action and verifying ShopFast..."):
                attempt = service.remediate(incident, suggestion, approved=True, previous_attempts=list(attempts),
                                            action=st.session_state["engineer_action"])
            attempts.append(attempt)
            st.rerun()


def postmortem_panel(service: IncidentService, incident: Incident, suggestion: Suggestion,
                     attempts: list[RemediationAttempt]) -> None:
    postmortems: dict[str, Postmortem] = st.session_state.setdefault("postmortems", {})
    if st.button("Write postmortem (Hindsight reflect)", key="write_postmortem"):
        try:
            with st.spinner("Hindsight is reflecting over this incident and all past ones..."):
                postmortems[incident.incident_id] = service.write_postmortem(incident, suggestion, attempts)
        except IncidentMemoryError as exc:
            st.error(f"Postmortem unavailable: {exc}")
    if incident.incident_id in postmortems:
        show_postmortem(postmortems[incident.incident_id])


def show_postmortem(postmortem: Postmortem) -> None:
    st.subheader(f"Postmortem {postmortem.incident_id}")
    for label, value in (("Summary", postmortem.summary), ("Impact", postmortem.impact),
                         ("Root cause", postmortem.root_cause)):
        st.markdown(f"**{label}**")
        st.text(value)
    for label, items in (("Timeline", postmortem.timeline), ("What went well", postmortem.what_went_well),
                         ("What went wrong", postmortem.what_went_wrong), ("Action items", postmortem.action_items)):
        if items:
            st.markdown(f"**{label}**")
            for item in items:
                st.text(f"- {item}")
    if postmortem.related_incidents:
        st.text(f"Related past incidents from memory: {', '.join(postmortem.related_incidents)}")
    if postmortem.saved_to_memory:
        st.success("Postmortem saved to memory with the incident; future recalls of it include these lessons.")
    else:
        st.warning("Postmortem written, but it could not be saved to memory.")
    st.download_button("Download postmortem", postmortem.to_text(), file_name=f"{postmortem.incident_id}-postmortem.txt",
                       key="download_postmortem")


def analyze(service: IncidentService, incident: Incident) -> None:
    """Investigate an incident and make it the one shown, whether it was detected or typed in."""
    try:
        with st.spinner("Recalling past incidents and asking the advisor..."):
            suggestion = service.analyze_incident(incident)
    except IncidentMemoryError as exc:
        st.error(f"Memory unavailable: {exc}")
        return
    st.session_state.setdefault("incidents", {})[incident.incident_id] = (incident, suggestion)
    st.session_state["last_id"] = incident.incident_id


def detect(service: IncidentService) -> None:
    try:
        with st.spinner("Probing ShopFast endpoints..."):
            incident = service.detect_incident()
    except ShopFastError as exc:
        st.error(f"ShopFast unreachable: {exc}")
        return
    if incident is None:
        st.info("No failing ShopFast endpoint detected. The shop is healthy.")
        return
    analyze(service, incident)


def evidence_line(action: str, worked: list[str], failed: list[str]) -> str:
    total = len(worked) + len(failed)
    parts = [f"worked in {len(worked)} of {total}"] if worked else []
    parts += [f"failed in {len(failed)} of {total}"] if failed else []
    return f"{action}: {', '.join(parts)} recorded attempts ({', '.join(worked + failed)})"


def show_evidence(suggestion: Suggestion) -> None:
    """Counts come only from outcomes the agent recorded after verification and Hindsight recalled."""
    st.markdown("**Learning evidence (verified outcomes in memory)**")
    if not suggestion.action_evidence:
        st.caption("No verified action outcomes in memory for this incident yet.")
        return
    for evidence in suggestion.action_evidence:
        st.text(evidence_line(evidence.action, evidence.worked_in, evidence.failed_in))
    latest = max((e.latest for e in suggestion.action_evidence), key=lambda i: int(i.removeprefix("INC-")))
    st.caption(f"Last learned from {latest}")


def show_evaluation(path: Path) -> None:
    st.subheader("Before vs after learning (evaluation)")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        s = report["summary"]
    except (OSError, ValueError, KeyError):
        st.info("No evaluation yet. Run: python -m scripts.evaluate_learning --bank-id shopfast-incidents-eval1")
        return
    n = s["incidents"]
    st.text(f"Relevant past incident recalled: {s['relevant_recall_before']}/{n} before, {s['relevant_recall_after']}/{n} after")
    st.text(f"Proven fix proposed first: {s['proven_fix_proposed_first_after']}/{n} after")
    st.text(f"Fixed on the first action: {s['first_try_fix_before']}/{n} before, {s['first_try_fix_after']}/{n} after")
    st.text(f"Actions needed: {s['attempts_before']} before, {s['attempts_after']} after")
    st.text(f"Failed fixes avoided after learning: {s['failed_fixes_avoided_after']} of {s['failed_fixes_before']}")
    st.caption(f"{n} controlled ShopFast incidents, run {report.get('generated_at', '')}; approvals {s['approvals']}.")


def curve_rows(rows: list[dict], key: str, percent: bool) -> list[float]:
    return [round(100 * r[key] / r["incidents"]) if percent else r[key] for r in rows]


def show_learning_curve(path: Path) -> None:
    st.subheader("Learning curve: the same incidents, again and again")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        with_memory = report["with_memory"]
    except (OSError, ValueError, KeyError):
        st.info("No learning curve yet. Run: python -m scripts.learning_curve --bank-id shopfast-curve-1 --baseline")
        return
    baseline = report.get("memory_off") or []
    rounds = [r["round"] for r in with_memory]
    fixed = {"Round": rounds, "With Hindsight memory": curve_rows(with_memory, "agent_first_try", True)}
    wrong = {"Round": rounds, "With Hindsight memory": curve_rows(with_memory, "wrong_actions", False)}
    if len(baseline) == len(with_memory):
        fixed["Memory off"] = curve_rows(baseline, "agent_first_try", True)
        wrong["Memory off"] = curve_rows(baseline, "wrong_actions", False)
    left, right = st.columns(2)
    left.markdown("**Fixed by the agent's first action (%)**")
    left.line_chart(fixed, x="Round", y=[k for k in fixed if k != "Round"])
    right.markdown("**Wrong actions run on production**")
    right.line_chart(wrong, x="Round", y=[k for k in wrong if k != "Round"])
    for row in with_memory:
        n = row["incidents"]
        st.text(f"Round {row['round']}: memory recalled {row['relevant_recall']}/{n}, first action fixed "
                f"{row['agent_first_try']}/{n}, wrong actions {row['wrong_actions']}, "
                f"engineer had to step in {row['engineer_actions']} time(s)")
    st.caption(f"Each round replays every ShopFast fault once on one fresh bank ({report.get('bank_id', '')}), "
               f"run {report.get('generated_at', '')}; approvals {report.get('approvals', '')}.")


def show_runbook(service: IncidentService) -> None:
    st.subheader("Living runbook (Hindsight mental model)")
    st.caption("Hindsight rewrites this after it consolidates new memories: what worked and failed, per failure.")
    if st.button("Load living runbook", key="load_runbook"):
        try:
            st.session_state["runbook"] = service.runbook()
        except IncidentMemoryError as exc:
            st.error(f"Memory unavailable: {exc}")
    if "runbook" in st.session_state:
        runbook = st.session_state["runbook"]
        if runbook is None:
            st.info("No living runbook in this bank yet. Run: python -m scripts.seed_memory")
        else:
            st.code(runbook or "(empty: Hindsight has not consolidated memories yet)", language="markdown")


def integrate_tab() -> None:
    st.caption("Use the agent and its memory from other tools. Both run locally and share the same Hindsight bank.")
    st.subheader("MCP server (Claude Code, Cursor, any MCP client)")
    st.code('{\n  "mcpServers": {\n    "incident-memory": {\n      "command": "uv",\n'
            '      "args": ["run", "python", "-m", "mcp_server.server"]\n    }\n  }\n}', language="json")
    st.text("Tools: detect_incident, analyze_incident, run_action, next_suggestion, record_outcome, write_postmortem,\n"
            "get_runbook, search_memory. run_action and the other writing tools are marked destructive, so the\n"
            "client asks you before each call.")
    st.subheader("REST API")
    st.code("uvicorn api.app:app --host 127.0.0.1 --port 8002", language="bash")
    st.code('curl -X POST http://127.0.0.1:8002/incidents/detect -H "X-API-Key: $AGENT_API_KEY"', language="bash")
    st.text("Endpoints: POST /incidents/detect, POST /incidents/analyze, GET /incidents/{id},\n"
            "POST /incidents/{id}/reanalyze, POST /incidents/{id}/actions, POST /incidents/{id}/outcome,\n"
            "POST /incidents/{id}/postmortem, GET /runbook, GET /memory/search?q=...  (docs at /docs)")


def submit_tab(service: IncidentService) -> None:
    st.caption("The agent probes ShopFast, opens an incident from its latest failure and investigates it.")
    if st.button("Detect latest ShopFast incident", key="detect_incident", type="primary"):
        detect(service)

    with st.expander("Or enter an incident manually"):
        manual_form(service)

    last_id = st.session_state.get("last_id")
    if last_id:
        show_suggestion(*st.session_state["incidents"][last_id])
        action_panel(service, last_id)


def manual_form(service: IncidentService) -> None:
    left, right = st.columns([3, 1])
    left.selectbox("Example error log (ShopFast fault)", [f.value for f in Fault], key="example_fault")
    right.button("Load example", key="load_example", on_click=load_example)

    with st.form("incident_form"):
        st.text_input("Service", key="service_name", placeholder="payment-api")
        st.selectbox("Severity", [s.value for s in Severity], key="severity")
        st.text_input("Title", key="title")
        st.text_area("Symptoms", key="symptoms")
        st.text_area("Error log", key="error_log", height=120)
        submitted = st.form_submit_button("Analyze", key="analyze")

    if submitted:
        try:
            incident = Incident(
                service=st.session_state["service_name"],
                severity=st.session_state["severity"],
                title=st.session_state["title"],
                symptoms=st.session_state["symptoms"],
                error_log=st.session_state["error_log"],
            )
        except ValidationError as exc:
            st.error(f"Invalid incident: {validation_message(exc)}")
            return
        analyze(service, incident)


def outcome_tab(service: IncidentService) -> None:
    incidents = st.session_state.get("incidents", {})
    if not incidents:
        st.info("Analyze an incident first, then record its outcome here.")
        return
    ids = list(reversed(incidents))
    with st.form("outcome_form"):
        st.selectbox("Incident", ids, key="outcome_incident",
                     format_func=lambda i: f"{i}: {incidents[i][0].title}")
        st.checkbox("Resolved", key="resolved")
        st.text_area("Actual root cause", key="actual_root_cause")
        st.text_area("Steps that worked (one per line)", key="steps_worked")
        st.text_area("Failed attempts (one per line)", key="failed_attempts")
        st.text_area("Notes", key="notes")
        submitted = st.form_submit_button("Record outcome", key="record_outcome")

    if submitted:
        incident, _ = incidents[st.session_state["outcome_incident"]]
        try:
            outcome = Outcome(
                incident_id=incident.incident_id,
                resolved=st.session_state["resolved"],
                actual_root_cause=st.session_state["actual_root_cause"],
                steps_that_worked=lines(st.session_state["steps_worked"]),
                failed_attempts=lines(st.session_state["failed_attempts"]),
                notes=st.session_state["notes"],
            )
        except ValidationError as exc:
            st.error(f"Invalid outcome: {validation_message(exc)}")
            return
        try:
            service.record_outcome(incident, outcome)
        except IncidentMemoryError as exc:
            st.error(f"Memory unavailable: {exc}")
            return
        st.success(f"Outcome for {incident.incident_id} saved to memory. The next similar incident will use it.")


def learned_tab(service: IncidentService) -> None:
    show_learning_curve(Path(st.session_state.get("curve_results_path", CURVE_RESULTS)))
    show_evaluation(Path(st.session_state.get("eval_results_path", EVAL_RESULTS)))
    show_runbook(service)
    last_id = st.session_state.get("last_id")
    if not last_id:
        st.info("Analyze an incident to see what the agent has learned about it.")
        return
    incident, suggestion = st.session_state["incidents"][last_id]
    show_evidence(suggestion)
    st.caption(f"Patterns Hindsight consolidated across past incidents, relevant to {incident.incident_id}")
    if not suggestion.learned_patterns:
        st.info("No learned patterns yet for this kind of incident.")
    for pattern in suggestion.learned_patterns:
        st.text(f"- {pattern.text}")


st.set_page_config(page_title="Incident Response Agent", layout="wide")
st.title("Incident Response Agent")
st.caption("Recalls past ShopFast incidents from Hindsight memory and suggests fixes.")

incident_service = get_service()
submit, outcome, learned, integrate = st.tabs(["Submit incident", "Record outcome", "What the agent has learned",
                                               "Integrate"])
with submit:
    submit_tab(incident_service)
with outcome:
    outcome_tab(incident_service)
with learned:
    learned_tab(incident_service)
with integrate:
    integrate_tab()
