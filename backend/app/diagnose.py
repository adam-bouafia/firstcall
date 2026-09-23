"""collect -> redact -> prompt -> Token Factory -> parse -> safety check."""
import json
import re

from pydantic import ValidationError

from app.llm.client import get_client
from app.llm.prompts import SYSTEM_PROMPT, build_user_prompt
from app.redact import redact_obj
from app.safety import is_safe
from app.schemas import Diagnosis, DiagnoseResponse, Snapshot, Usage


def redact_snapshot(snap: Snapshot) -> Snapshot:
    data, n = redact_obj(snap.model_dump())
    data["redactions"] = n
    return Snapshot(**data)


def parse_diagnosis(text: str) -> Diagnosis:
    # Reasoning models may emit <think>...</think> or ```json fences first.
    text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("model did not return JSON")
    raw = json.loads(m.group(0))
    try:
        raw["confidence"] = max(0.0, min(1.0, float(raw.get("confidence", 0.5))))
    except (TypeError, ValueError):
        raw["confidence"] = 0.5
    if isinstance(raw.get("evidence"), str):
        raw["evidence"] = [raw["evidence"]]
    try:
        d = Diagnosis(**raw)
    except ValidationError:
        raw["category"] = "Other"
        d = Diagnosis(**raw)
    d.next_command_safe = is_safe(d.next_command)
    from app.config import get_settings
    from app.safety import remediation_check
    d.remediation_allowed = remediation_check(d.remediation_command, get_settings().namespaces)[0]
    return d


def diagnose(snap: Snapshot, model_key: str | None = None, client=None) -> DiagnoseResponse:
    clean = redact_snapshot(snap)
    user = build_user_prompt(clean)
    client = client or get_client()
    res = client.chat(SYSTEM_PROMPT, user, model_key=model_key)
    try:
        diag = parse_diagnosis(res.text)
    except (ValueError, json.JSONDecodeError):
        diag = Diagnosis(
            summary="Model returned an unparseable answer.",
            root_cause=res.text[:500],
            confidence=0.0,
            next_command=f"kubectl describe pod {snap.pod} -n {snap.namespace}",
            next_command_safe=True,
        )
    return DiagnoseResponse(
        diagnosis=diag,
        usage=Usage(
            model_key=res.model_key,
            model_id=res.model_id,
            prompt_tokens=res.prompt_tokens,
            completion_tokens=res.completion_tokens,
            latency_ms=res.latency_ms,
            cost_usd=res.cost_usd,
        ),
        redactions=clean.redactions,
        context_chars=len(user),
    )


def run_model(snap: Snapshot, model_key: str | None, client=None) -> DiagnoseResponse:
    """Entry point used everywhere: a model key, a raw id, 'cascade' or 'cascade:fast>strong'."""
    from app.config import load_models

    if model_key and model_key.startswith("cascade"):
        cfg = load_models().get("cascade", {})
        fast, strong = cfg.get("fast", "qwen3-30b"), cfg.get("strong", "qwen3-235b")
        if ":" in model_key:
            fast, strong = model_key.split(":", 1)[1].split(">")
        return diagnose_cascade(snap, fast, strong, cfg.get("threshold", 0.75), client=client)
    return diagnose(snap, model_key, client=client)


def diagnose_cascade(snap: Snapshot, fast: str, strong: str, threshold: float = 0.75,
                     client=None) -> DiagnoseResponse:
    """Adaptive routing: cheap/fast model first, escalate only when it is unsure.

    This is the 'only possible now' angle: small open MoE models are good enough for
    most incidents, and you pay big-model prices only for the hard ones.
    """
    first = diagnose(snap, fast, client=client)
    if first.diagnosis.confidence >= threshold and first.diagnosis.category != "Other":
        first.usage.model_key = f"cascade({fast})"
        return first
    second = diagnose(snap, strong, client=client)
    second.usage = Usage(
        model_key=f"cascade({fast}->{strong})",
        model_id=f"{first.usage.model_id} -> {second.usage.model_id}",
        prompt_tokens=first.usage.prompt_tokens + second.usage.prompt_tokens,
        completion_tokens=first.usage.completion_tokens + second.usage.completion_tokens,
        latency_ms=first.usage.latency_ms + second.usage.latency_ms,
        cost_usd=round(first.usage.cost_usd + second.usage.cost_usd, 6),
    )
    return second


def diagnose_with_web(snap: Snapshot, first: DiagnoseResponse, model_key: str | None = None,
                      client=None) -> DiagnoseResponse | None:
    """Second pass with public web evidence, used when the model was unsure.

    Only a sanitized error string is searched (no names, IPs or logs). Returns None if nothing found.
    """
    from app import metrics as M
    from app.llm.client import get_client
    from app.websearch import Tavily, build_query, format_results

    tv = Tavily()
    if not tv.configured:
        return None
    query = build_query(snap, first.diagnosis.category)
    try:
        results = tv.search(query)
        M.WEB_SEARCHES.labels("ok" if results else "empty").inc()
    except Exception as e:
        M.WEB_SEARCHES.labels("error").inc()
        raise RuntimeError(f"Tavily search failed: {e}") from e
    if not results:
        return None
    clean = redact_snapshot(snap)
    user = build_user_prompt(clean) + format_results(query, results)
    client = client or get_client()
    res = client.chat(SYSTEM_PROMPT, user, model_key=model_key)
    try:
        d = parse_diagnosis(res.text)
    except Exception:
        return None
    known = {r["url"] for r in results}
    d.sources = [u for u in (d.sources or []) if u in known] or [r["url"] for r in results[:2]]
    return DiagnoseResponse(
        diagnosis=d,
        usage=Usage(model_key=res.model_key, model_id=res.model_id, prompt_tokens=res.prompt_tokens,
                    completion_tokens=res.completion_tokens, latency_ms=res.latency_ms, cost_usd=res.cost_usd),
        redactions=clean.redactions, context_chars=len(user))
