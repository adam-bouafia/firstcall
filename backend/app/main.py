import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app
from pydantic import BaseModel

from app.config import get_settings, load_models
from app.diagnose import redact_snapshot, run_model
from app.engine import Engine
from app.k8s.source import get_source
from app.llm.client import default_model_key
from app.schemas import CompareRequest, DiagnoseRequest, DiagnoseResponse
from app.store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
state: dict = {}


def engine() -> Engine:
    if "engine" not in state:
        s = get_settings()
        state["engine"] = Engine(Store(s.firstcall_db), get_source())
    return state["engine"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    e = engine()
    if get_settings().firstcall_watch:
        e.start()
    yield
    e.stop()


app = FastAPI(title="FirstCall", version="0.2.0", lifespan=lifespan,
              description="Kubernetes incident first-responder on open-weight models")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/metrics", make_asgi_app())


# ---------- meta ----------
@app.get("/health")
def health():
    s, e = get_settings(), engine()
    return {"ok": e.last_error is None, "source": s.firstcall_source, "llm": s.firstcall_llm,
            "default_model": default_model_key(), "watching": bool(e.thread), "last_scan": e.last_scan,
            "last_error": e.last_error, "namespaces": s.namespaces, "remediation": s.firstcall_remediation}


@app.get("/api/models")
def models():
    reg = load_models()
    return {"default": default_model_key(), "cascade": reg.get("cascade"),
            "models": [{"key": k, **v} for k, v in reg["models"].items()]}


@app.get("/api/activity")
def activity(since: int = 0, limit: int = 300):
    from app.activity import since as feed
    return feed(since, limit)


@app.post("/api/incidents/{iid}/remediation/preview")
def remediation_preview(iid: int):
    if not engine().store.get(iid):
        raise HTTPException(404, "incident not found")
    return engine().remediation_preview(iid, by="console")


class Approval(BaseModel):
    hash: str
    approver: str = "console"


@app.post("/api/incidents/{iid}/remediation/apply")
def remediation_apply(iid: int, body: Approval):
    if not engine().store.get(iid):
        raise HTTPException(404, "incident not found")
    res = engine().remediation_apply(iid, body.hash, by=body.approver or "console")
    if not res.get("ok") and "reason" in res and res.get("exit_code") is None:
        raise HTTPException(409, res["reason"])
    return res


@app.get("/api/alerting")
def alerting():
    return engine().alerting_state()


class ModeChoice(BaseModel):
    mode: str


@app.post("/api/alerting")
def set_alerting(body: ModeChoice):
    try:
        engine().set_alert_mode(body.mode, by="api")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return engine().alerting_state()


@app.post("/api/alerting/test")
def test_alert():
    """Send a test push to every configured channel (ignores the schedule)."""
    e = engine()
    sent = {}
    if e.notifier.channels["telegram"]:
        sent["telegram"] = e.telegram.send("🔔 FirstCall test alert: Telegram is wired up.")
    if e.notifier.channels["slack"]:
        e.notifier._slack(":bell: FirstCall test alert: Slack is wired up.")
        sent["slack"] = 1
    if not sent:
        raise HTTPException(400, "no channel configured: set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_IDS or SLACK_WEBHOOK_URL")
    return {"sent": sent}


@app.get("/api/stats")
def stats():
    return engine().store.stats()


# ---------- incidents (the product) ----------
@app.get("/api/incidents")
def incidents(status: str | None = None):
    e = engine()
    rows = e.store.list(status)
    for r in rows:
        r["busy"] = r["id"] in e.busy
    return rows


@app.get("/api/incidents/{iid}")
def incident(iid: int):
    e = engine()
    row = e.store.get(iid)
    if not row:
        raise HTTPException(404, "incident not found")
    return {**row, "busy": iid in e.busy, "timeline": e.store.timeline(iid)}


class ModelChoice(BaseModel):
    model: str | None = None


@app.post("/api/incidents/{iid}/diagnose")
def incident_diagnose(iid: int, body: ModelChoice):
    if not engine().store.get(iid):
        raise HTTPException(404, "incident not found")
    return {"queued": engine().submit_diagnosis(iid, body.model, trigger="manual")}


@app.post("/api/incidents/{iid}/investigate")
def incident_investigate(iid: int, body: ModelChoice):
    """Run the current read-only next command and refine the diagnosis."""
    if not engine().store.get(iid):
        raise HTTPException(404, "incident not found")
    return {"queued": engine().submit_step(iid, body.model)}


class Feedback(BaseModel):
    value: str  # correct | wrong


@app.post("/api/incidents/{iid}/feedback")
def incident_feedback(iid: int, body: Feedback):
    if body.value not in ("correct", "wrong"):
        raise HTTPException(400, "value must be 'correct' or 'wrong'")
    engine().store.set_feedback(iid, body.value)
    return {"ok": True}


@app.get("/api/incidents/{iid}/snapshot")
def incident_snapshot(iid: int):
    row = engine().store.get(iid)
    if not row:
        raise HTTPException(404, "incident not found")
    try:
        return redact_snapshot(engine().source.snapshot(row["namespace"], row["pod"]))
    except Exception as ex:
        raise HTTPException(410, f"workload no longer available: {ex}")


@app.post("/api/scan")
def scan_now():
    return engine().scan()


@app.post("/api/alertmanager")
def alertmanager(payload: dict, bg: BackgroundTasks):
    """Point an Alertmanager webhook receiver here: any firing alert triggers an immediate scan."""
    alerts = payload.get("alerts", [])
    firing = [a for a in alerts if a.get("status") == "firing"]

    def handle():
        e = engine()
        e.scan()  # first make sure the incident exists
        for a in alerts:
            e.alert(a)

    if alerts:
        bg.add_task(handle)
    return {"received": len(alerts), "firing": len(firing)}


# ---------- stateless (bench, compare) ----------
@app.post("/api/diagnose", response_model=DiagnoseResponse)
def run_diagnose(req: DiagnoseRequest):
    try:
        snap = engine().source.snapshot(req.namespace, req.pod)
    except Exception as e:
        raise HTTPException(404, str(e))
    try:
        return run_model(snap, req.model)
    except RuntimeError as e:
        raise HTTPException(503, str(e))


@app.post("/api/compare")
def compare(req: CompareRequest):
    """Same incident, several models in parallel: the live 'model advantage' demo."""
    try:
        snap = engine().source.snapshot(req.namespace, req.pod)
    except Exception as e:
        raise HTTPException(404, str(e))

    def one(m):
        try:
            return run_model(snap, m).model_dump()
        except Exception as ex:
            return {"error": f"{type(ex).__name__}: {ex}"[:300]}

    with ThreadPoolExecutor(max_workers=max(1, len(req.models))) as ex:
        futs = {m: ex.submit(one, m) for m in req.models}
        return {m: f.result() for m, f in futs.items()}
