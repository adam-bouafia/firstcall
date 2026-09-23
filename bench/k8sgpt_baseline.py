"""Run k8sgpt (CNCF Sandbox) on the same live cluster and score it with the same ground truth.

k8sgpt analyzes the namespace once (with --explain through the SAME OpenAI-compatible model),
we group its findings per scenario and score root-cause keywords and suggested kubectl commands.
k8sgpt has no category field, so category accuracy is reported as n/a.

Uses a throwaway config dir so your own ~/.config/k8sgpt is never touched.
Env: K8SGPT_MODEL, K8SGPT_BASEURL, K8SGPT_BACKEND (default openai), NEBIUS_API_KEY / K8SGPT_API_KEY.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

# which k8sgpt findings belong to which scenario (object name prefixes)
SCENARIO_OBJECTS = {
    "01-crashloop": ["payments-api"],
    "02-imagepull": ["checkout-web"],
    "03-oom": ["report-worker"],
    "04-unschedulable": ["ml-batch"],
    "05-probe": ["catalog-api"],
    "06-config-missing": ["notify-svc", "notify-config"],
    "07-service-selector": ["orders"],
    "08-pvc-pending": ["ledger-db", "ledger-data"],
}


def available() -> bool:
    return shutil.which("k8sgpt") is not None


def run(namespace: str = "firstcall-demo", timeout: int = 900) -> tuple[dict[str, str], float]:
    """Returns ({scenario: combined text}, seconds for the whole analysis)."""
    if not available():
        raise RuntimeError("k8sgpt not installed: https://docs.k8sgpt.ai/getting-started/installation/")
    backend = os.environ.get("K8SGPT_BACKEND", "openai")
    model = os.environ.get("K8SGPT_MODEL", "Qwen/Qwen3-30B-A3B-Instruct-2507")
    baseurl = os.environ.get("K8SGPT_BASEURL", "https://api.tokenfactory.nebius.com/v1/").rstrip("/")
    key = os.environ.get("K8SGPT_API_KEY") or os.environ.get("NEBIUS_API_KEY", "")
    if not key:
        from app.config import get_settings  # read .env like the rest of FirstCall
        key = get_settings().nebius_api_key
    cfg = tempfile.mkdtemp(prefix="k8sgpt-")
    env = {**os.environ, "XDG_CONFIG_HOME": cfg, "HOME": cfg,
           "KUBECONFIG": os.environ.get("KUBECONFIG", os.path.expanduser("~/.kube/config"))}
    add = ["k8sgpt", "auth", "add", "-b", backend, "-m", model, "-u", baseurl]
    if key:
        add += ["-p", key]
    subprocess.run(add, env=env, capture_output=True, text=True, check=True)
    t0 = time.perf_counter()
    p = subprocess.run(["k8sgpt", "analyze", "-n", namespace, "-b", backend, "--explain", "-o", "json", "--no-cache"],
                       env=env, capture_output=True, text=True, timeout=timeout)
    dt = time.perf_counter() - t0
    shutil.rmtree(cfg, ignore_errors=True)
    if p.returncode != 0:
        raise RuntimeError(f"k8sgpt failed: {p.stderr.strip()[-400:]}")
    data = json.loads(p.stdout)
    texts: dict[str, list[str]] = {s: [] for s in SCENARIO_OBJECTS}
    for r in data.get("results") or []:
        obj = r["name"].split("/", 1)[-1]
        for sc, prefixes in SCENARIO_OBJECTS.items():
            if any(obj == pre or obj.startswith(pre + "-") for pre in prefixes):
                texts[sc].append(" ".join(e.get("Text", "") for e in r.get("error") or []))
                texts[sc].append(r.get("details") or "")
    return {sc: "\n".join(t) for sc, t in texts.items()}, dt


def score_text(text: str, exp: dict) -> dict:
    """Same keywords and command expectations as FirstCall's scoring, applied to free text."""
    low = text.lower()
    groups = exp.get("keywords", [])
    kw = sum(any(k.lower() in low for k in g) for g in groups) / len(groups) if groups else 1.0
    cmds = [ln for ln in low.splitlines() if "kubectl" in ln]
    cmd_ok = any(c in ln for ln in cmds for c in exp.get("command_any", []))
    detected = bool(text.strip())
    return {"detected": detected, "keyword_score": round(kw, 2), "command_ok": cmd_ok, "category_ok": None}


if __name__ == "__main__":  # quick manual run
    import sys
    texts, dt = run(sys.argv[1] if len(sys.argv) > 1 else "firstcall-demo")
    print(f"{dt:.1f}s")
    for sc, t in texts.items():
        print(f"== {sc}\n{t[:400]}\n")
