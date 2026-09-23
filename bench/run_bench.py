"""FirstCall benchmark: the 'evidence of advantage' deliverable.

For every scenario fixture x every model, run a diagnosis and score it against the
ground truth in the fixture's `expected` block.

  python bench/run_bench.py                                  # all models in models.yaml
  python bench/run_bench.py --provider ollama                # only local models
  python bench/run_bench.py --live                           # score against the live cluster, not fixtures
  python bench/run_bench.py -m qwen3-30b -m qwen3-235b -r 3  # 3 repeats each
  python bench/run_bench.py -m cascade -m rules              # adaptive routing + no-LLM baseline
  python bench/run_bench.py --baseline                       # + closed-model reference (needs BASELINE_OPENAI_API_KEY)

Writes bench/results/<timestamp>.{json,csv,md}.
"""
import argparse
import csv
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "bench"))

from app.config import get_settings, load_models  # noqa: E402
from app.diagnose import diagnose, run_model  # noqa: E402
from app.k8s.fixtures import FixtureSource  # noqa: E402
from app.llm.client import LLMResult, MockClient, get_client  # noqa: E402  (BaselineAnthropic uses httpx)


class BaselineOpenAI:
    """Closed-model reference points. Benchmark only, never in the product path.

    They answer one question: does the open model match a closed one, and at what cost?
    """

    def __init__(self, model: str | None = None):
        from openai import OpenAI
        key = os.environ.get("BASELINE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        self.client = OpenAI(api_key=key)
        self.model = model or os.environ.get("BASELINE_OPENAI_MODEL", "gpt-4.1-mini")

    def chat(self, system, user, model_key=None, **_):
        t0 = time.perf_counter()
        r = self.client.chat.completions.create(model=self.model, temperature=0.1, messages=[
            {"role": "system", "content": system}, {"role": "user", "content": user}])
        return LLMResult(r.choices[0].message.content or "", r.usage.prompt_tokens,
                         r.usage.completion_tokens, int((time.perf_counter() - t0) * 1000),
                         f"baseline-openai:{self.model}", self.model)


class BaselineAnthropic:
    def __init__(self, model: str | None = None):
        import httpx
        self.http = httpx.Client(timeout=120)
        self.key = os.environ.get("BASELINE_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model or os.environ.get("BASELINE_ANTHROPIC_MODEL", "").split(",")[0].strip() or self._latest("sonnet")

    def _headers(self):
        return {"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"}

    def _latest(self, family: str) -> str:
        """Model ids move; ask the API which ones exist rather than hard-coding a
        name that returns HTTP 400 six months later."""
        try:
            r = self.http.get("https://api.anthropic.com/v1/models?limit=50", headers=self._headers())
            r.raise_for_status()
            ids = [m["id"] for m in r.json().get("data", [])]     # newest first
            return next((i for i in ids if family in i), ids[0])
        except Exception as e:
            print(f"  ! could not list Anthropic models ({e}); set BASELINE_ANTHROPIC_MODEL in .env")
            return "claude-sonnet-4-5"

    def chat(self, system, user, model_key=None, **_):
        t0 = time.perf_counter()
        r = self.http.post("https://api.anthropic.com/v1/messages",
                           headers=self._headers(),
                           # no temperature: newer models reject it ("`temperature` is deprecated
                           # for this model"). The open models keep 0.1; note the asymmetry.
                           json={"model": self.model, "max_tokens": 1200,
                                 "system": system, "messages": [{"role": "user", "content": user}]})
        if r.status_code >= 400:   # a bare raise_for_status hides which field the API rejected
            raise RuntimeError(f"anthropic {r.status_code}: {r.text[:300]}")
        d = r.json()
        text = "".join(b.get("text", "") for b in d.get("content", []))
        return LLMResult(text, d["usage"]["input_tokens"], d["usage"]["output_tokens"],
                         int((time.perf_counter() - t0) * 1000), f"baseline-anthropic:{self.model}", self.model)


def score(d, exp) -> dict:
    text = " ".join([d.summary, d.root_cause, d.fix, " ".join(d.evidence)]).lower()
    groups = exp.get("keywords", [])
    hit = sum(any(k.lower() in text for k in g) for g in groups)
    kw = hit / len(groups) if groups else 1.0
    cmd = d.next_command.lower()
    cmd_ok = any(c in cmd for c in exp.get("command_any", [])) and ("-n " in cmd or "--namespace" in cmd or "-a" in cmd)
    cat_ok = d.category == exp["category"]
    fix_ok = None
    if exp.get("remediation_any"):
        rc = (d.remediation_command or "").lower()
        fix_ok = bool(rc) and d.remediation_allowed and any(x in rc for x in exp["remediation_any"])
    return {"category_ok": cat_ok, "keyword_score": round(kw, 2), "command_ok": cmd_ok, "detected": True, "fix_ok": fix_ok,
            "score": round(0.5 * cat_ok + 0.3 * kw + 0.2 * cmd_ok, 3),
            "comparable": round(0.6 * kw + 0.4 * cmd_ok, 3)}


def _baseline_cost(res, prefix: str):
    """Closed models have no entry in models.yaml, so cost_usd computes to 0.0 - which would
    print as "$0.00" and read like a rigged comparison. Use list prices from .env when given
    (BASELINE_*_PRICE_IN / _OUT, USD per 1M tokens), otherwise report n/a."""
    pin, pout = os.environ.get(f"{prefix}_PRICE_IN"), os.environ.get(f"{prefix}_PRICE_OUT")
    if not (pin and pout):
        return None
    return round(res.usage.prompt_tokens / 1e6 * float(pin)
                 + res.usage.completion_tokens / 1e6 * float(pout), 6)


def run_one(model: str, snap, s):
    if model == "rules":  # keyword rules, no LLM: the "what a regex runbook gets you" floor
        r = diagnose(snap, None, client=MockClient())
        r.usage.cost_usd, r.usage.model_key, r.usage.model_id = 0.0, "rules", "keyword-rules"
        return r
    if model.startswith("baseline-anthropic"):
        r = diagnose(snap, None, client=BaselineAnthropic(model.partition(":")[2] or None))
        r.usage.cost_override = _baseline_cost(r, "BASELINE_ANTHROPIC")
        return r
    if model.startswith(("baseline", "baseline-openai")):
        r = diagnose(snap, None, client=BaselineOpenAI(model.partition(":")[2] or None))
        r.usage.cost_override = _baseline_cost(r, "BASELINE_OPENAI")
        return r
    return run_model(snap, model, client=get_client())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-m", "--model", action="append", help="model key (repeatable); default: all")
    ap.add_argument("-r", "--repeats", type=int, default=1)
    ap.add_argument("-s", "--scenario", action="append", help="limit to scenario(s)")
    ap.add_argument("--baseline", action="store_true",
                    help="add the closed-model references (OpenAI + Anthropic, needs BASELINE_* keys)")
    ap.add_argument("--provider", help="only models from this provider (nebius, ollama, openrouter)")
    ap.add_argument("--live", action="store_true", help="snapshot the live cluster (scenarios applied) instead of fixtures")
    a = ap.parse_args()

    s = get_settings()
    src = FixtureSource(s.fixtures_dir)
    truth = src.ground_truth()
    scenarios = a.scenario or sorted(truth)
    reg = load_models()["models"]
    if a.provider:
        models = [k for k, v in reg.items() if v["provider"] == a.provider] + (a.model or [])
    else:
        models = a.model or list(reg)
    live = None
    if a.live:
        from app.k8s.cluster import ClusterSource
        sys.path.insert(0, str(ROOT / "scripts"))
        from capture_fixtures import WORKLOAD_TO_SCENARIO
        cs = ClusterSource(["firstcall-demo"])
        live = {}
        for inc in cs.list_incidents():
            name = (inc.workload or inc.pod).split("/", 1)[-1]
            if name in WORKLOAD_TO_SCENARIO:
                live[WORKLOAD_TO_SCENARIO[name]] = cs.snapshot(inc.namespace, inc.pod)
        scenarios = [x for x in scenarios if x in live]
        print(f"live cluster: {len(live)} scenarios broken and captured")
    if a.baseline:
        # BASELINE_*_MODEL may be a comma list: one reference row per closed model.
        for name, keys, ids in (("baseline-openai", ("BASELINE_OPENAI_API_KEY", "OPENAI_API_KEY"),
                                 os.environ.get("BASELINE_OPENAI_MODEL", "")),
                                ("baseline-anthropic", ("BASELINE_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
                                 os.environ.get("BASELINE_ANTHROPIC_MODEL", ""))):
            if not any(os.environ.get(k) for k in keys):
                continue
            picked = [x.strip() for x in ids.split(",") if x.strip()]
            models += [f"{name}:{m}" for m in picked] or [name]

    rows = []
    if "k8sgpt" in models:
        if not live:
            sys.exit("k8sgpt needs a live cluster: add --live")
        import k8sgpt_baseline as kb
        print("running k8sgpt analyze --explain on the live cluster ...")
        try:
            texts, secs = kb.run()
            for sc in scenarios:
                sc_ = kb.score_text(texts.get(sc, ""), truth[sc])
                rows.append({"model": "k8sgpt", "scenario": sc, "repeat": 0, **sc_, "score": None,
                             "comparable": round(0.6 * sc_["keyword_score"] + 0.4 * sc_["command_ok"], 3),
                             "latency_ms": int(secs * 1000 / max(1, len(scenarios))), "cost_usd": None,
                             "prompt_tokens": None, "completion_tokens": None, "error": "",
                             "predicted": None, "next_command": ""})
                print(f"{'k8sgpt':>14} {sc:<22} comparable={rows[-1]['comparable']}")
        except Exception as e:
            print(f"k8sgpt failed: {e}")
            rows += [{"model": "k8sgpt", "scenario": sc, "repeat": 0, "score": 0, "comparable": 0, "category_ok": None,
                      "keyword_score": 0, "command_ok": False, "latency_ms": 0, "cost_usd": 0, "error": str(e)[:200]}
                     for sc in scenarios]
        models = [m for m in models if m != "k8sgpt"] + ["k8sgpt"]
    for m in [m for m in models if m != "k8sgpt"]:
        for sc in scenarios:
            snap = live[sc] if live else src.by_scenario(sc)
            for rep in range(a.repeats):
                try:
                    res = run_one(m, snap, s)
                    sc_ = score(res.diagnosis, truth[sc])
                    row = {"model": m, "scenario": sc, "repeat": rep, **sc_,
                           "confidence": res.diagnosis.confidence, "predicted": res.diagnosis.category,
                           "latency_ms": res.usage.latency_ms, "prompt_tokens": res.usage.prompt_tokens,
                           "completion_tokens": res.usage.completion_tokens, "cost_usd": getattr(res.usage, "cost_override", None) if str(res.usage.model_key).startswith("baseline") else res.usage.cost_usd,
                           "safe_cmd": res.diagnosis.next_command_safe, "next_command": res.diagnosis.next_command,
                           "route": res.usage.model_key, "error": ""}
                except Exception as e:  # keep going, record the failure
                    row = {"model": m, "scenario": sc, "repeat": rep, "score": 0, "category_ok": False,
                           "keyword_score": 0, "command_ok": False, "latency_ms": 0, "cost_usd": 0, "comparable": 0,
                           "error": f"{type(e).__name__}: {e}"[:200]}
                rows.append(row)
                print(f"{m:>14} {sc:<22} score={row['score']!s:<5} {row['latency_ms']:>6}ms {row.get('error','')}")

    def fixrate(rs):
        vals = [r.get("fix_ok") for r in rs if r.get("fix_ok") is not None]
        return (sum(vals) / len(vals)) if vals else None

    out = ROOT / "bench" / "results"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out / f"{stamp}.json").write_text(json.dumps(rows, indent=2))
    with open(out / f"{stamp}.csv", "w", newline="") as f:
        keys = sorted({k for r in rows for k in r})
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)

    lines = ["| model | score | root cause | category acc | cmd ok | right fix proposed | comparable (rc+cmd) | p50 latency | cost / 1k incidents | errors |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for m in models:
        rs = [r for r in rows if r["model"] == m]
        ok = [r for r in rs if not r["error"]]
        if not ok:
            lines.append(f"| {m} | - | - | - | - | - | - | - | - | {len(rs)} |")
            continue
        na = lambda v, f: "n/a" if v is None else f(v)  # noqa: E731
        avg = None if ok[0]["score"] is None else statistics.mean(r["score"] for r in ok)
        cat = None if ok[0]["category_ok"] is None else sum(r["category_ok"] for r in ok) / len(ok)
        cost = None if ok[0]["cost_usd"] is None else statistics.mean(r["cost_usd"] for r in ok) * 1000
        lines.append(
            f"| {m} | {na(avg, lambda v: f'{v:.2f}')} "
            f"| {statistics.mean(r['keyword_score'] for r in ok):.0%} "
            f"| {na(cat, lambda v: f'{v:.0%}')} "
            f"| {sum(r['command_ok'] for r in ok) / len(ok):.0%} "
            f"| {na(fixrate(ok), lambda v: f'{v:.0%}')} "
            f"| {statistics.mean(r['comparable'] for r in ok):.2f} "
            f"| {statistics.median(r['latency_ms'] for r in ok) / 1000:.1f}s "
            f"| {na(cost, lambda v: f'${v:.2f}')} "
            f"| {len(rs) - len(ok)} |")
    lines += ["", "*score* = 0.5 category + 0.3 root-cause keywords + 0.2 command. "
              "*comparable* = 0.6 root cause + 0.4 command, the only fields k8sgpt also produces. "
              "*right fix proposed* = an allow-listed one-click fix of the expected kind, "
              f"on the {sum(1 for s in scenarios if truth[s].get('remediation_any'))} scenarios that have one."]
    md = "\n".join(lines)
    (out / f"{stamp}.md").write_text(md + "\n")
    print("\n" + md + f"\n\nSaved bench/results/{stamp}.*")


if __name__ == "__main__":
    main()
