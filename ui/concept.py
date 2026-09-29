"""Concept page: how the Incident Response Agent learns. Static text only; needs no configuration or network."""

import streamlit as st

# The learning loop, drawn with Graphviz. Light fills with dark text stay readable in light and dark themes.
CONCEPT_FLOW = """digraph {
    rankdir=LR; bgcolor="transparent"; nodesep=0.25; ranksep=0.35;
    node [shape=box, style="rounded,filled", fillcolor="#eef2f7", color="#94a3b8", fontcolor="#1e293b",
          fontname="sans-serif", fontsize=11, margin="0.15,0.08"];
    edge [color="#94a3b8", fontcolor="#64748b", fontname="sans-serif", fontsize=10];
    incident [label="Incident\noccurs"];
    detect [label="Agent detects\nand analyzes it"];
    recall [label="Hindsight recalls\npast incidents and\nverified outcomes"];
    known [label="Proposes the action\nthat worked before"];
    novel [label="LLM proposes\na remediation"];
    approve [label="Engineer reviews\nand approves", fillcolor="#fef3c7", color="#d97706"];
    verify [label="Action runs;\nhealth verified"];
    record [label="Result recorded\nin Hindsight", fillcolor="#dcfce7", color="#16a34a"];
    incident -> detect -> recall;
    recall -> known [label="known"];
    recall -> novel [label="new"];
    known -> approve; novel -> approve;
    approve -> verify -> record;
    record -> recall [label="next time", style=dashed, constraint=false];
}"""

CONCEPT_STEPS = [
    ("Incident occurs", "An endpoint of the application starts failing, for example checkout returns 503."),
    ("Agent detects and analyzes it", "The agent probes the app, opens an incident from its latest failure and "
                                      "reads the error log. Nobody copies logs by hand."),
    ("Hindsight checks past experience", "Memory recalls similar past incidents, the fixes that worked, the fixes "
                                         "that failed, and the team's rules."),
    ("Known incident: reuse what worked", "If the same failure was fixed before, the agent proposes the action that "
                                          "worked and cites the incident it learned it from."),
    ("New incident: the LLM proposes", "With no matching memory, the LLM suggests a remediation from the error log "
                                       "and says plainly that no past incident matches."),
    ("Engineer approves", "Nothing runs without a human. The engineer approves the proposal, rejects it, or runs a "
                          "different allow-listed action."),
    ("Result is recorded", "The agent runs the action, verifies the app is healthy, and stores the outcome in "
                           "Hindsight, including failed attempts."),
    ("Next time it knows", "When the failure comes back, the agent recalls this experience and proposes the fix "
                           "that worked, without being told."),
]

st.title("Concept")
st.caption("How the Incident Response Agent learns from every incident it handles.")
st.graphviz_chart(CONCEPT_FLOW, width="stretch")
st.subheader("Step by step")
for row_start in range(0, len(CONCEPT_STEPS), 4):
    for column, (number, (title, text)) in zip(
            st.columns(4), enumerate(CONCEPT_STEPS[row_start:row_start + 4], start=row_start + 1)):
        with column.container(border=True):
            st.markdown(f"**{number}. {title}**")
            st.caption(text)
st.subheader("Two layers of memory")
left, right = st.columns(2)
with left.container(border=True):
    st.markdown("**Incidents and outcomes**")
    st.caption("Every incident, what was tried, and whether the app was healthy afterwards. Kept as history, "
               "so the agent can cite exactly which past incident a fix comes from.")
with right.container(border=True):
    st.markdown("**Living runbook and team rules**")
    st.caption("A runbook Hindsight rewrites from those outcomes, and rules the team sets once. Both are given "
               "to the agent on every analysis.")
