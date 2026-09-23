"""One OpenAI-compatible client for every provider (Token Factory, Ollama, OpenRouter, vLLM...),
plus a mock for credit-free UI work."""
import json
import os
import re
import time
from dataclasses import dataclass
from functools import lru_cache

from openai import OpenAI

from app.config import get_settings, load_models


@dataclass
class LLMResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    model_key: str
    model_id: str
    cost_override: float | None = None   # closed-model baselines: price from .env, or None = n/a

    @property
    def cost_usd(self) -> float:
        m = load_models()["models"].get(self.model_key, {})
        return round(self.prompt_tokens / 1e6 * m.get("price_in", 0)
                     + self.completion_tokens / 1e6 * m.get("price_out", 0), 6)


def default_model_key() -> str:
    return get_settings().firstcall_default_model or load_models()["default"]


def resolve_model(key: str | None) -> tuple[str, dict]:
    """key -> (key, model config).

    Accepts a registry key ("qwen3-30b"), or "provider:raw-id" ("groq:openai/gpt-oss-20b")
    so any model a provider serves can be used without editing models.yaml."""
    reg = load_models()
    key = key or default_model_key()
    if key in reg["models"]:
        return key, reg["models"][key]
    if ":" in key and key.split(":", 1)[0] in reg["providers"]:
        prov, mid = key.split(":", 1)
        return key, {"provider": prov, "id": mid, "label": mid}
    raise RuntimeError(f"unknown model '{key}': use a key from models.yaml or provider:model-id")


def provider_config(name: str) -> dict:
    p = dict(load_models()["providers"][name])
    base = os.environ.get(p.get("base_url_env", ""), "") or p.get("base_url", "")
    key = os.environ.get(p.get("api_key_env", ""), "") or p.get("api_key", "")
    if not key:  # values from .env are loaded into Settings, not os.environ
        key = getattr(get_settings(), p.get("api_key_env", "").lower(), "") or ""
    if not base:
        base = getattr(get_settings(), p.get("base_url_env", "").lower(), "") or ""
    return {"base_url": base, "api_key": key, "timeout": p.get("timeout", 120)}


@lru_cache(maxsize=16)
def _openai(provider: str) -> OpenAI:
    cfg = provider_config(provider)
    if not cfg["base_url"]:
        raise RuntimeError(f"provider '{provider}' has no base URL configured")
    if not cfg["api_key"]:
        raise RuntimeError(f"provider '{provider}' needs an API key (see .env.example)")
    return OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"], timeout=cfg["timeout"], max_retries=1)


class OpenAICompatClient:
    def chat(self, system: str, user: str, model_key: str | None = None,
             temperature: float = 0.1, max_tokens: int = 1500) -> LLMResult:
        key, m = resolve_model(model_key)
        client = _openai(m["provider"])
        # reasoning models spend the budget on <think> before the JSON; a truncated answer
        # is a measurement artifact, not a weak model. models.yaml can raise the ceiling.
        max_tokens = m.get("max_tokens", max_tokens)
        kwargs = {}
        if m.get("json_mode"):
            kwargs["response_format"] = {"type": "json_object"}
        t0 = time.perf_counter()
        resp = client.chat.completions.create(
            model=m["id"], temperature=temperature, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], **kwargs)
        dt = int((time.perf_counter() - t0) * 1000)
        u = resp.usage
        return LLMResult(resp.choices[0].message.content or "",
                         getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0,
                         dt, key, m["id"])

    def list_models(self, provider: str) -> list[str]:
        return sorted(x.id for x in _openai(provider).models.list().data)


# Backwards-compatible name used by bench/scripts
TokenFactoryClient = OpenAICompatClient


class MockClient:
    """Keyword-based fake so the UI and tests run with zero credits."""

    RULES = [
        ("OOMKilled", "OOMKilled", "Container exceeded its memory limit and was OOM-killed."),
        ("ImagePullBackOff|ErrImagePull|manifest unknown", "ImagePull", "The image reference cannot be pulled."),
        ("CreateContainerConfigError|configmap .* not found", "ConfigMissing", "A referenced ConfigMap/Secret does not exist."),
        ("Readiness probe failed|Liveness probe failed", "ProbeFailure", "The probe targets a port/path the app does not serve."),
        ("NoEndpoints", "ServiceSelector", "The Service selector matches no pod labels."),
        ("FailedScheduling|Insufficient", "Unschedulable", "No node has enough allocatable resources."),
        ("ProvisioningFailed|storageclass", "StoragePending", "The PVC references a StorageClass that does not exist."),
        ("CrashLoopBackOff|Back-off restarting", "CrashLoop-AppError", "The application exits on startup."),
    ]

    def chat(self, system: str, user: str, model_key: str | None = None, **_) -> LLMResult:
        key, m = resolve_model(model_key)
        found = re.search(r"Incident: ([^/\s]+)/(\S+)", user)
        ns, pod = (found.group(1), found.group(2)) if found else ("default", "pod")
        cat, cause = "Other", "Not enough evidence in the snapshot."
        for pat, c, why in self.RULES:
            if re.search(pat, user, re.I):
                cat, cause = c, why
                break
        target = pod.replace("svc/", "service/") if pod.startswith("svc/") else f"pod/{pod}"
        dep = re.search(r'"workload":"deployment/([^"]+)"', user)
        rem, change = "", ""
        if cat in ("ImagePull", "CrashLoop-AppError", "OOMKilled", "ProbeFailure") and '"previous_revision"' in user and dep:
            rem = f"kubectl rollout undo deployment/{dep.group(1)} -n {ns}"
            change = "The latest rollout changed the pod template shortly before the failure."
        elif cat == "ServiceSelector" and pod.startswith("svc/") and (
                zero := re.search(r'"deployment":"([^"]+)","replicas":0', user)):
            rem = f"kubectl scale deployment/{zero.group(1)} --replicas=1 -n {ns}"
        elif cat == "ServiceSelector" and pod.startswith("svc/"):
            rem = f"kubectl patch service {pod[4:]} -n {ns} -p '{{\"spec\":{{\"selector\":{{\"app\":\"{pod[4:]}-api\"}}}}}}'"
        body = {"summary": f"[mock] {cat} on {pod}", "category": cat, "root_cause": cause, "confidence": 0.5,
                "evidence": ["(mock mode: set FIRSTCALL_LLM=real for real analysis)"],
                "next_command": f"kubectl describe {target} -n {ns}",
                "fix": "Mock answer. Configure a real model for a real fix.",
                "suspected_change": change, "remediation_command": rem}
        time.sleep(0.2)
        return LLMResult(json.dumps(body), len(user) // 4, 120, 200, key, m["id"])

    def list_models(self, provider: str = "") -> list[str]:
        return [m["id"] for m in load_models()["models"].values()]


def get_client():
    return MockClient() if get_settings().firstcall_llm == "mock" else OpenAICompatClient()
