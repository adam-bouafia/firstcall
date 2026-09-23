"""Prometheus metrics: the numbers behind 'measurable model advantage', scraped live."""
from prometheus_client import Counter, Gauge, Histogram

INCIDENTS_DETECTED = Counter("firstcall_incidents_detected_total", "Incidents opened", ["reason"])
INCIDENTS_RESOLVED = Counter("firstcall_incidents_resolved_total", "Incidents resolved")
INCIDENTS_OPEN = Gauge("firstcall_incidents_open", "Incidents currently open")

DIAGNOSES = Counter("firstcall_diagnoses_total", "Diagnoses produced", ["model", "category", "trigger"])
DIAGNOSIS_LATENCY = Histogram("firstcall_diagnosis_latency_seconds", "Model latency per diagnosis", ["model"],
                              buckets=(0.5, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89))
CONFIDENCE = Histogram("firstcall_diagnosis_confidence", "Model-reported confidence", ["model"],
                       buckets=(0.25, 0.5, 0.6, 0.7, 0.75, 0.8, 0.9, 0.95, 1.0))
TOKENS = Counter("firstcall_llm_tokens_total", "Tokens sent/received", ["model", "direction"])
COST = Counter("firstcall_llm_cost_usd_total", "Model spend in USD", ["model"])
REDACTIONS = Counter("firstcall_redactions_total", "Secrets/PII removed before inference")

TIME_TO_DIAGNOSIS = Histogram("firstcall_time_to_first_diagnosis_seconds", "Detection -> first diagnosis",
                              buckets=(1, 2, 5, 10, 20, 30, 60, 120, 300))
TIME_TO_RESOLVE = Histogram("firstcall_time_to_resolve_seconds", "Detection -> resolved",
                            buckets=(30, 60, 120, 300, 600, 1200, 1800, 3600, 7200))

COMMANDS = Counter("firstcall_commands_total", "Read-only investigation commands run", ["result"])
SCANS = Counter("firstcall_scans_total", "Cluster scans", ["result"])
ERRORS = Counter("firstcall_errors_total", "Failures by stage", ["stage"])
NOTIFICATIONS = Counter("firstcall_notifications_total", "Pushes to people", ["channel", "result"])
REMEDIATIONS = Counter("firstcall_remediations_total", "Human-approved fixes", ["stage", "result"])
WEB_SEARCHES = Counter("firstcall_web_searches_total", "Tavily searches (low-confidence escalation)", ["result"])
ALERTS = Counter("firstcall_alertmanager_alerts_total", "Alerts received from Alertmanager", ["status"])


def record_diagnosis(resp: dict, trigger: str):
    u, d = resp["usage"], resp["diagnosis"]
    m = u["model_key"]
    DIAGNOSES.labels(m, d["category"], trigger).inc()
    DIAGNOSIS_LATENCY.labels(m).observe(u["latency_ms"] / 1000)
    CONFIDENCE.labels(m).observe(d["confidence"])
    TOKENS.labels(m, "in").inc(u["prompt_tokens"])
    TOKENS.labels(m, "out").inc(u["completion_tokens"])
    COST.labels(m).inc(u["cost_usd"])
