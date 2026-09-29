"""Local, evidence-based analyses for the software lifecycle demo modules."""

import re
from collections.abc import Sequence
from math import isfinite
from typing import Literal

from pydantic import BaseModel, Field

from agent.models import Incident, Outcome

MAX_ANALYSIS_CHARS = 50_000
SeverityLabel = Literal["critical", "high", "medium", "low", "info"]


class LifecycleFinding(BaseModel):
    severity: SeverityLabel
    title: str = Field(min_length=1, max_length=200)
    evidence: str = Field(min_length=1, max_length=2000)
    recommendation: str = Field(min_length=1, max_length=2000)


class LifecycleAssessment(BaseModel):
    module: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=2000)
    findings: list[LifecycleFinding] = Field(default_factory=list, max_length=20)


class JourneyStage(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    users: int = Field(ge=0)


class Postmortem(BaseModel):
    incident_id: str
    title: str
    summary: str
    timeline: list[str]
    impact: str
    root_cause: str
    resolution: list[str]
    failed_attempts: list[str]
    lessons: list[str]


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|token|password|secret|authorization)\b\s*[:=]\s*)"
    r"(?:Bearer\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+\S+")
_DATABASE_CALL = re.compile(r"\.query\s*\(|\.execute\s*\(|\.find\s*\(|\bSELECT\s+", re.I)


def _bounded_text(value: str, label: str) -> str:
    if not value.strip():
        raise ValueError(f"{label} cannot be empty")
    if len(value) > MAX_ANALYSIS_CHARS:
        raise ValueError(f"{label} exceeds {MAX_ANALYSIS_CHARS} characters")
    return value


def _redact(text: str) -> str:
    text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    return _BEARER.sub("Bearer [REDACTED]", text)


def sanitize_analysis_input(text: str) -> str:
    """Bound and redact user-provided source, logs, and telemetry before an LLM call."""
    return _redact(_bounded_text(text, "Analysis input"))


def _assessment(module: str, findings: list[LifecycleFinding], clear_summary: str) -> LifecycleAssessment:
    if not findings:
        return LifecycleAssessment(module=module, summary=clear_summary, findings=[])
    highest = next(level for level in ("critical", "high", "medium", "low")
                   if any(f.severity == level for f in findings))
    return LifecycleAssessment(
        module=module,
        summary=f"{len(findings)} finding(s); highest severity is {highest}.",
        findings=findings,
    )


def analyze_code(source: str, *, diff: bool = False) -> LifecycleAssessment:
    """Scan source text or added diff lines. This never imports or executes submitted code."""
    _bounded_text(source, "Source input")
    source_lines = source.splitlines()
    if diff:
        source_lines = [line[1:] for line in source_lines if line.startswith("+") and not line.startswith("+++")]
        if not source_lines:
            raise ValueError("Diff contains no added lines to review")

    findings: list[LifecycleFinding] = []
    loop_indent: int | None = None
    for number, line in enumerate(source_lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//", "*")):
            continue
        indent = len(line) - len(line.lstrip())
        if loop_indent is not None and indent <= loop_indent:
            loop_indent = None
        if re.match(r"(?:for|while)\b.*:\s*$", stripped):
            loop_indent = indent
            continue
        if loop_indent is not None and indent > loop_indent and _DATABASE_CALL.search(stripped):
            findings.append(LifecycleFinding(
                severity="high",
                title="Possible database call inside a loop",
                evidence=f"Line {number}: database query pattern appears inside a loop.",
                recommendation="Batch the lookup or move it outside the loop to avoid N+1 queries.",
            ))
            loop_indent = None
        if re.search(r"except\s*(?:Exception)?\s*:", stripped):
            findings.append(LifecycleFinding(
                severity="medium",
                title="Broad exception handling",
                evidence=f"Line {number}: catches Exception or uses a bare except.",
                recommendation="Catch the expected exception types and preserve actionable error context.",
            ))
        if _SECRET_ASSIGNMENT.search(stripped):
            findings.append(LifecycleFinding(
                severity="critical",
                title="Possible hard-coded credential",
                evidence=f"Line {number}: credential-like assignment detected; value redacted.",
                recommendation="Load the credential from a secret manager or environment, and rotate it if real.",
            ))
        if re.search(r"subprocess\.(?:run|Popen|call)\(.*shell\s*=\s*True", stripped):
            findings.append(LifecycleFinding(
                severity="high",
                title="Shell-enabled subprocess call",
                evidence=f"Line {number}: subprocess is configured with shell=True.",
                recommendation="Pass an argument list with shell=False and validate untrusted input.",
            ))

    return _assessment("development and pull request review", findings,
                       "No configured risk patterns matched. This heuristic scan is not a full static analyzer.")


_PIPELINE_RULES = (
    ("critical", re.compile(r"\b(?:critical|high)\s+(?:severity\s+)?(?:CVE-\d{4}-\d+|vulnerabilit)", re.I),
     "Dependency security gate failed", "Upgrade or mitigate the affected dependency before deployment."),
    ("high", re.compile(r"readiness probe failed| readiness probe .*failed|CrashLoopBackOff", re.I),
     "Deployment health check failed", "Inspect container startup logs and verify readiness probe path, port, and dependencies."),
    ("high", re.compile(r"compilation failed|cannot find symbol|undefined reference", re.I),
     "Build compilation failed", "Check renamed or missing symbols and compile the affected module before rerunning CI."),
    ("medium", re.compile(r"(?:^|\s)FAILED\s+\S+|\b\d+\s+(?:tests?\s+)?failed\b|AssertionError", re.I),
     "Test stage failed", "Group failures by their first shared setup or assertion error, then fix the common cause."),
    ("medium", re.compile(r"coverage.{0,40}(?:below|dropped|threshold)|quality gate failed", re.I),
     "Quality gate failed", "Review the changed paths and add focused tests before lowering the coverage gate."),
    ("medium", re.compile(r"permission denied|accessdenied|forbidden", re.I),
     "Pipeline permission failure", "Verify the CI identity has the minimum required permission for this step."),
)


def analyze_pipeline(log: str) -> LifecycleAssessment:
    """Classify common CI/CD log signatures and return the matching evidence lines."""
    _bounded_text(log, "Pipeline log")
    findings = []
    for severity, pattern, title, recommendation in _PIPELINE_RULES:
        match = next(((number, line) for number, line in enumerate(log.splitlines(), 1) if pattern.search(line)), None)
        if match:
            number, line = match
            safe_line = _redact(line.strip())[:300]
            findings.append(LifecycleFinding(
                severity=severity,
                title=title,
                evidence=f"Line {number}: {safe_line}",
                recommendation=recommendation,
            ))
    return _assessment("CI/CD pipeline intelligence", findings,
                       "No known failure signature matched. Inspect the preceding stage output and retry context.")


def _metric(value: float, label: str, maximum: float | None = None) -> float:
    if not isfinite(value) or value < 0 or (maximum is not None and value > maximum):
        upper = f" and at most {maximum}" if maximum is not None else ""
        raise ValueError(f"{label} must be between 0{upper}")
    return value


def compare_deployment(before: dict[str, float], after: dict[str, float]) -> LifecycleAssessment:
    """Compare pre/post deployment error, latency, DB pool, and memory signals."""
    metrics = ("error_rate_pct", "p95_latency_ms", "db_pool_pct", "memory_pct")
    for snapshot_name, snapshot in (("before", before), ("after", after)):
        for metric in metrics:
            if metric not in snapshot:
                raise ValueError(f"{snapshot_name} deployment is missing {metric}")
            _metric(snapshot[metric], f"{snapshot_name} {metric}", 100 if metric.endswith("_pct") else None)

    findings = []
    error_delta = after["error_rate_pct"] - before["error_rate_pct"]
    if error_delta >= 1 or (before["error_rate_pct"] > 0 and after["error_rate_pct"] >= before["error_rate_pct"] * 2):
        findings.append(LifecycleFinding(
            severity="high" if after["error_rate_pct"] >= 5 else "medium",
            title="Error rate regressed after deployment",
            evidence=f"Error rate changed from {before['error_rate_pct']:.2f}% to {after['error_rate_pct']:.2f}%.",
            recommendation="Pause rollout or roll back the release, then compare changed routes and dependencies.",
        ))
    if before["p95_latency_ms"] > 0 and after["p95_latency_ms"] >= before["p95_latency_ms"] * 1.5:
        findings.append(LifecycleFinding(
            severity="medium",
            title="Latency increased after deployment",
            evidence=f"p95 latency changed from {before['p95_latency_ms']:.0f} ms to {after['p95_latency_ms']:.0f} ms.",
            recommendation="Compare traces and query counts for the new version before expanding the rollout.",
        ))
    for metric, title in (("db_pool_pct", "Database pool saturation increased"),
                          ("memory_pct", "Memory pressure increased")):
        if after[metric] >= 90 or after[metric] - before[metric] >= 20:
            findings.append(LifecycleFinding(
                severity="high" if after[metric] >= 95 else "medium",
                title=title,
                evidence=f"{metric} changed from {before[metric]:.1f}% to {after[metric]:.1f}%.",
                recommendation="Compare resource use by version and stop rollout if the signal continues to rise.",
            ))
    return _assessment("deployment intelligence", findings,
                       "No configured deployment regression thresholds were crossed.")


def analyze_runtime(error_rate_pct: float, p95_latency_ms: float, db_pool_pct: float,
                    memory_pct: float) -> LifecycleAssessment:
    """Evaluate a current runtime snapshot against conservative demo thresholds."""
    _metric(error_rate_pct, "error rate", 100)
    _metric(p95_latency_ms, "p95 latency")
    _metric(db_pool_pct, "database pool utilization", 100)
    _metric(memory_pct, "memory utilization", 100)
    findings = []
    checks = (
        (error_rate_pct >= 5, "high" if error_rate_pct >= 10 else "medium", "Elevated application error rate",
         f"Current error rate is {error_rate_pct:.2f}%.", "Group errors by route and correlate their start time with deployments."),
        (p95_latency_ms >= 1000, "high" if p95_latency_ms >= 3000 else "medium", "Elevated request latency",
         f"Current p95 latency is {p95_latency_ms:.0f} ms.", "Compare traces for slow dependencies and queries on the affected route."),
        (db_pool_pct >= 90, "high" if db_pool_pct >= 97 else "medium", "Database pool near saturation",
         f"Database pool utilization is {db_pool_pct:.1f}%.", "Check connection counts by service and avoid scaling clients before checking database capacity."),
        (memory_pct >= 90, "high" if memory_pct >= 97 else "medium", "Memory pressure is high",
         f"Memory utilization is {memory_pct:.1f}%.", "Check process memory trend and recent workload or release changes."),
    )
    for triggered, severity, title, evidence, recommendation in checks:
        if triggered:
            findings.append(LifecycleFinding(severity=severity, title=title, evidence=evidence,
                                             recommendation=recommendation))
    return _assessment("runtime monitoring", findings,
                       "Runtime values are within the configured demo thresholds.")


def analyze_user_journey(stages: Sequence[JourneyStage], *, javascript_errors: int = 0,
                         failed_requests: int = 0, p75_load_ms: float = 0) -> LifecycleAssessment:
    """Find funnel drop-offs and browser-side signals from supplied aggregate counts."""
    if len(stages) < 2:
        raise ValueError("Provide at least two ordered journey stages")
    if javascript_errors < 0 or failed_requests < 0:
        raise ValueError("Error and failed-request counts cannot be negative")
    _metric(p75_load_ms, "p75 page load")
    findings = []
    for previous, current in zip(stages, stages[1:]):
        if current.users > previous.users:
            raise ValueError("Journey stage user counts cannot increase")
        if previous.users and (previous.users - current.users) / previous.users >= 0.2:
            drop_pct = (previous.users - current.users) / previous.users * 100
            findings.append(LifecycleFinding(
                severity="high" if drop_pct >= 40 else "medium",
                title=f"User drop-off between {previous.name} and {current.name}",
                evidence=f"{previous.users} users reached {previous.name}; {current.users} reached {current.name} ({drop_pct:.1f}% drop-off).",
                recommendation=f"Inspect {current.name} browser errors and API requests, then compare by browser and release.",
            ))
    if javascript_errors:
        findings.append(LifecycleFinding(
            severity="medium", title="Frontend JavaScript errors reported",
            evidence=f"{javascript_errors} JavaScript error(s) were reported in the selected interval.",
            recommendation="Group errors by component and release, and inspect the first stack frame in application code.",
        ))
    if failed_requests:
        findings.append(LifecycleFinding(
            severity="medium", title="Failed browser requests reported",
            evidence=f"{failed_requests} failed network request(s) were reported in the selected interval.",
            recommendation="Correlate failed requests with the journey step, endpoint, and backend incident timeline.",
        ))
    if p75_load_ms >= 2500:
        findings.append(LifecycleFinding(
            severity="low" if p75_load_ms < 4000 else "medium", title="Page load is slower than target",
            evidence=f"p75 page load is {p75_load_ms:.0f} ms.",
            recommendation="Inspect large resources and the slowest API calls on the affected page.",
        ))
    return _assessment("frontend and user journey monitoring", findings,
                       "No configured journey, browser-error, or page-load signals were detected.")


def generate_postmortem(incident: Incident, outcome: Outcome, timeline: Sequence[str]) -> Postmortem:
    """Create a structured post-incident draft from a validated incident and its recorded outcome."""
    if incident.incident_id != outcome.incident_id:
        raise ValueError("Incident and outcome IDs must match")
    clean_timeline = [entry.strip() for entry in timeline if entry.strip()]
    return Postmortem(
        incident_id=incident.incident_id,
        title=f"Postmortem: {incident.title}",
        summary=(f"{incident.service} experienced a {incident.severity.value} incident. "
                 f"Resolution status: {'resolved' if outcome.resolved else 'unresolved'}"),
        timeline=clean_timeline,
        impact=incident.symptoms,
        root_cause=outcome.actual_root_cause,
        resolution=outcome.steps_that_worked,
        failed_attempts=outcome.failed_attempts,
        lessons=[outcome.notes] if outcome.notes else [],
    )