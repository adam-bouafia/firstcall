"""The product loop: scan -> detect -> diagnose -> investigate (read-only) -> notify -> resolve."""
import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from app import metrics as M
from app import remediate
from app.activity import emit
from app.alerting import MODES, Schedule
from app.notify import Notifier
from app.telegram import TelegramBot
from app.config import get_settings
from app.diagnose import diagnose_with_web, run_model
from app.investigate import UnsafeCommand, followup, run_readonly
from app.k8s.source import ContextSource
from app.safety import is_safe
from app.store import Store

log = logging.getLogger("firstcall.engine")


class Engine:
    def __init__(self, store: Store, source: ContextSource):
        self.s = get_settings()
        self.store = store
        self.source = source
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="diag")
        self.busy: set[int] = set()
        self._last_try: dict[int, float] = {}  # incident -> last auto-diagnosis attempt (retry after failures)
        self.stop_evt = threading.Event()
        self.thread: threading.Thread | None = None
        self.last_scan: float | None = None
        self.last_error: str | None = None
        self.scan_lock = threading.Lock()
        s = self.s
        self.schedule = Schedule(s.firstcall_timezone, s.firstcall_business_start,
                                 s.firstcall_business_end, s.firstcall_business_days)
        self.telegram = TelegramBot(s.telegram_bot_token, s.telegram_chat_ids.split(","), s.telegram_api_base)
        self.telegram.engine = self
        self.notifier = Notifier(s, store, self.schedule, self.telegram)

    # ---------- alerting ----------
    def alert_mode(self) -> str:
        return self.store.get_setting("alert_mode", self.s.firstcall_alert_mode)

    def set_alert_mode(self, mode: str, by: str = "api"):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.store.set_setting("alert_mode", mode)
        emit("config", f"alert mode -> {mode} (by {by})")

    def alerting_state(self) -> dict:
        mode = self.alert_mode()
        nxt = self.schedule.next_change(mode)
        return {"mode": mode, "modes": list(MODES), "description": self.schedule.describe(mode),
                "paging_now": self.schedule.should_page(mode), "next_change": nxt.isoformat() if nxt else None,
                "timezone": self.s.firstcall_timezone, "business_hours": {
                    "start": self.s.firstcall_business_start, "end": self.s.firstcall_business_end,
                    "days": self.s.firstcall_business_days},
                "channels": self.notifier.channels}

    # ---------- loop ----------
    def start(self):
        if self.thread:
            return
        self.thread = threading.Thread(target=self._loop, name="watcher", daemon=True)
        self.thread.start()
        if self.s.telegram_commands:
            self.telegram.start_polling()

    def stop(self):
        self.stop_evt.set()
        self.telegram.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def _loop(self):
        while not self.stop_evt.is_set():
            self.scan()
            self.stop_evt.wait(self.s.firstcall_watch_interval)

    def scan(self) -> dict:
        with self.scan_lock:
            try:
                incidents = self.source.list_incidents()
            except Exception as e:
                self.last_error = f"scan failed: {type(e).__name__}: {e}"[:400]
                emit("error", self.last_error, level="error")
                M.SCANS.labels("error").inc()
                return {"error": self.last_error}
            M.SCANS.labels("ok").inc()
            self._scans = getattr(self, "_scans", 0) + 1
            self.last_error = None
            self.last_scan = time.time()
            new = []
            for inc in incidents:
                row, is_new = self.store.upsert_seen(inc)
                if is_new:
                    new.append(row["id"])
                    M.INCIDENTS_DETECTED.labels(inc.reason).inc()
                    emit("detect", f"new incident {inc.namespace}/{inc.workload or inc.pod}: {inc.reason}"
                                   f"{f' ({inc.restarts} restarts)' if inc.restarts else ''}", row["id"], "warn")
                if not self.s.firstcall_auto_diagnose or row["id"] in self.busy:
                    continue
                # new incident, or an earlier attempt failed (model down, quota, network): retry every 60 s
                if row["first_diagnosis_at"] is None and time.time() - self._last_try.get(row["id"], 0) > 60:
                    if row["id"] in self._last_try:
                        emit("model", "retrying diagnosis (previous attempt failed)", row["id"], "warn")
                    self._last_try[row["id"]] = time.time()
                    self.submit_diagnosis(row["id"], trigger="auto")
            resolved = self.store.mark_healthy_scan({i.fingerprint for i in incidents}, self.s.firstcall_resolve_after)
            emit("scan", f"scanned {', '.join(self.s.namespaces)}: {len(incidents)} unhealthy, "
                         f"{len(new)} new, {len(resolved)} resolved")
            for r in resolved:
                emit("resolve", f"{r['namespace']}/{r['workload'] or r['pod']} healthy again after "
                                f"{round(time.time() - r['first_seen'])} s", r["id"])
                M.INCIDENTS_RESOLVED.inc()
                M.TIME_TO_RESOLVE.observe(time.time() - r["first_seen"])
                self.notifier.resolved(r)
                self._event(r, "FirstCallResolved", "FirstCall: workload healthy again, incident resolved.", False)
            M.INCIDENTS_OPEN.set(self.store.stats()["open"])
            return {"seen": len(incidents), "new": new}

    # ---------- work ----------
    def submit_diagnosis(self, iid: int, model: str | None = None, trigger: str = "manual"):
        if iid in self.busy:
            return False
        self.busy.add(iid)
        self.pool.submit(self._guard, iid, self._diagnose, iid, model, trigger)
        return True

    def submit_step(self, iid: int, model: str | None = None):
        if iid in self.busy:
            return False
        self.busy.add(iid)
        self.pool.submit(self._guard, iid, self._step, iid, model)
        return True

    def _guard(self, iid, fn, *args):
        try:
            fn(*args)
        except Exception as e:
            log.error("incident %s: %s", iid, traceback.format_exc())
            emit("error", f"{fn.__name__.strip('_')} failed: {type(e).__name__}: {e}"[:300], iid, "error")
            M.ERRORS.labels(fn.__name__.strip("_")).inc()
            self.store.add_event(iid, "error", {"text": f"{type(e).__name__}: {e}"[:500]})
        finally:
            self.busy.discard(iid)

    def _snapshot(self, inc: dict):
        return self.source.snapshot(inc["namespace"], inc["pod"])

    def _event(self, inc: dict, reason: str, message: str, warning: bool = True):
        if self.s.firstcall_write_events:
            try:
                if self.source.record_event(inc["namespace"], inc.get("workload"), reason, message, warning):
                    emit("event", f"posted Kubernetes Event {reason} on {inc.get('workload')}", inc["id"])
            except Exception as e:  # never let write-back break the loop
                log.warning("event write-back failed: %s", e)

    def _record(self, iid: int, inc: dict, resp: dict, trigger: str, rnd: int):
        if inc.get("first_diagnosis_at") is None and rnd == 0:
            M.TIME_TO_DIAGNOSIS.observe(time.time() - inc["first_seen"])
        self.store.add_event(iid, "diagnosis", {**resp, "trigger": trigger, "round": rnd})
        M.record_diagnosis(resp, trigger)
        M.REDACTIONS.inc(resp.get("redactions", 0))
        d = resp["diagnosis"]
        self._event(inc, "FirstCallDiagnosis",
                    f"FirstCall ({resp['usage']['model_key']}, {round(d['confidence'] * 100)}%): {d['summary']} "
                    f"| cause: {d['root_cause']} | next: {d['next_command']}")

    def _run_model(self, iid, snap, model, label="diagnosing"):
        from app.llm.client import default_model_key
        emit("model", f"{label} with {model or default_model_key()} …", iid)
        resp = run_model(snap, model).model_dump()
        u, d = resp["usage"], resp["diagnosis"]
        emit("model", f"{u['model_key']} answered in {u['latency_ms'] / 1000:.1f}s: {d['category']} "
                      f"({round(d['confidence'] * 100)}%) · {u['prompt_tokens']}+{u['completion_tokens']} tokens · "
                      f"${u['cost_usd']:.5f}", iid)
        return resp

    def _diagnose(self, iid: int, model: str | None, trigger: str):
        inc = self.store.get(iid)
        snap = self._snapshot(inc)
        ch = snap.related.get("recent_changes", {})
        emit("collect", f"snapshot of {snap.pod}: {len(snap.events)} events, "
                        f"{sum(len(v or '') for v in snap.logs.values())} log chars"
                        + (f", rollout rev {ch.get('current_revision')} ({len(ch.get('diff', []))} changes vs previous)"
                           if ch.get("previous_revision") else ""), iid)
        resp = self._run_model(iid, snap, model)
        self._record(iid, inc, resp, trigger, 0)
        resp = self._maybe_web(iid, inc, snap, resp, model, trigger) or resp
        steps = []
        for _ in range(self.s.firstcall_auto_investigate if trigger == "auto" else 0):
            new = self._investigate(iid, snap, resp, steps, model, trigger="auto")
            if new is None:
                break
            resp = new
        if trigger == "auto":  # one push per incident, with the refined answer
            self.notifier.diagnosed(self.store.get(iid), resp)

    def _step(self, iid: int, model: str | None):
        inc = self.store.get(iid)
        last = self.store.last_diagnosis(iid)
        if not last:
            return self._diagnose(iid, model, "manual")
        steps = [e["data"] for e in self.store.timeline(iid) if e["kind"] == "command"]
        self._investigate(iid, self._snapshot(inc), last, steps, model, trigger="manual")

    def _investigate(self, iid, snap, resp: dict, steps: list, model, trigger: str):
        cmd = resp["diagnosis"]["next_command"]
        if not is_safe(cmd) or any(s["command"].strip() == cmd.strip() for s in steps):
            return None
        try:
            step = run_readonly(cmd)
        except UnsafeCommand as e:
            self.store.add_event(iid, "error", {"text": str(e)})
            return None
        steps.append(step)
        M.COMMANDS.labels("ok" if step["exit_code"] == 0 else "error").inc()
        emit("command", f"ran read-only `{cmd}` (exit {step['exit_code']}, {step['duration_ms']} ms, "
                        f"{step['redactions']} redacted)", iid)
        self.store.add_event(iid, "command", {**step, "trigger": trigger})
        emit("model", "refining with the command output …", iid)
        new = followup(snap, steps, model).model_dump()
        emit("model", f"refined: {new['diagnosis']['category']} ({round(new['diagnosis']['confidence'] * 100)}%)", iid)
        self._record(iid, self.store.get(iid), new, trigger, len(steps))
        return new

    def alert(self, alert: dict):
        """Attach an Alertmanager alert to the matching open incident (if any) and scan now."""
        labels = alert.get("labels", {})
        ns, pod = labels.get("namespace"), labels.get("pod") or labels.get("service") or labels.get("deployment")
        M.ALERTS.labels(alert.get("status", "unknown")).inc()
        emit("alert", f"Alertmanager: {labels.get('alertname', 'alert')} {alert.get('status', '')} "
                      f"{labels.get('namespace', '')}/{labels.get('pod', '')}")
        if not ns:
            return None
        for row in self.store.list("open"):
            if row["namespace"] != ns:
                continue
            target = (row["workload"] or "").split("/", 1)[-1]
            if pod and (pod == row["pod"] or pod.startswith(target) or target == pod):
                self.store.add_event(row["id"], "note", {
                    "text": f"Alertmanager: {labels.get('alertname', 'alert')} {alert.get('status', '')}"
                            f"{' · ' + alert['annotations']['summary'] if alert.get('annotations', {}).get('summary') else ''}"})
                return row["id"]
        return None

    def _maybe_web(self, iid, inc, snap, resp, model, trigger):
        """Unsure? Search the public web for the error text (Tavily) and re-ask, with citations."""
        mode = self.s.firstcall_web_search
        d = resp["diagnosis"]
        unsure = d["confidence"] < self.s.firstcall_web_search_threshold or d["category"] == "Other"
        if mode == "off" or (mode == "low-confidence" and not unsure):
            return None
        from app.schemas import DiagnoseResponse
        try:
            emit("search", f"confidence {round(d['confidence'] * 100)}%: searching public sources for the error text", iid)
            better = diagnose_with_web(snap, DiagnoseResponse(**resp), model)
        except Exception as e:
            emit("search", f"web search unavailable: {e}"[:200], iid, "warn")
            return None
        if not better:
            emit("search", "no useful public sources found", iid)
            return None
        new = better.model_dump()
        emit("search", f"re-diagnosed with {len(new['diagnosis']['sources'])} public source(s): "
                       f"{new['diagnosis']['category']} ({round(new['diagnosis']['confidence'] * 100)}%)", iid)
        self._record(iid, inc, new, trigger + "+web", 0)
        return new

    # ---------- human-approved remediation ----------
    def remediation_preview(self, iid: int, by: str = "console") -> dict:
        last = self.store.last_diagnosis(iid)
        cmd = (last or {}).get("diagnosis", {}).get("remediation_command", "")
        if not cmd:
            return {"ok": False, "reason": "no remediation proposed for this incident"}
        res = remediate.preview(cmd)
        M.REMEDIATIONS.labels("preview", "ok" if res["ok"] else "rejected").inc()
        self.store.add_event(iid, "remediation_preview", {**res, "by": by})
        emit("remediate", f"dry run of `{cmd}`: {res['reason']}", iid, "info" if res["ok"] else "warn")
        return res

    def remediation_apply(self, iid: int, expected_hash: str, by: str) -> dict:
        last = self.store.last_diagnosis(iid)
        cmd = (last or {}).get("diagnosis", {}).get("remediation_command", "")
        if not cmd:
            return {"ok": False, "reason": "no remediation proposed"}
        if remediate.cmd_hash(cmd) != expected_hash:
            return {"ok": False, "reason": "the proposed fix changed since the preview: preview again"}
        prev = [e["data"] for e in self.store.timeline(iid) if e["kind"] == "remediation_preview"]
        if not prev or not prev[-1].get("ok") or prev[-1].get("hash") != expected_hash:
            return {"ok": False, "reason": "run a successful dry run (preview) first"}
        emit("remediate", f"{by} approved `{cmd}`: applying", iid, "warn")
        res = remediate.apply(cmd)
        M.REMEDIATIONS.labels("apply", "ok" if res["ok"] else "failed").inc()
        self.store.add_event(iid, "remediation", {**res, "approved_by": by})
        emit("remediate", f"`{cmd}` {res['reason']} (exit {res.get('exit_code')}); verifying on the next scans", iid,
             "info" if res["ok"] else "error")
        inc = self.store.get(iid)
        self._event(inc, "FirstCallRemediation", f"FirstCall: {by} approved and applied: {cmd}", warning=False)
        return res
