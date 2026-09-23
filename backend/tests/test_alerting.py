import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.alerting import Schedule
from app.telegram import TelegramBot

AMS = ZoneInfo("Europe/Amsterdam")
S = Schedule()


def at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=AMS)


def test_business_hours_boundaries():
    # 2026-09-21 is a Monday
    assert S.is_business_hours(at(2026, 9, 21, 9, 0))
    assert S.is_business_hours(at(2026, 9, 21, 16, 59))
    assert not S.is_business_hours(at(2026, 9, 21, 17, 0))
    assert not S.is_business_hours(at(2026, 9, 21, 8, 59))
    assert not S.is_business_hours(at(2026, 9, 26, 12, 0))  # Saturday


def test_modes():
    monday_noon, monday_night, sunday_noon = at(2026, 9, 21, 12), at(2026, 9, 21, 23), at(2026, 9, 27, 12)
    assert S.should_page("always", monday_noon)
    assert not S.should_page("offhours", monday_noon)
    assert S.should_page("offhours", monday_night)
    assert S.should_page("offhours", sunday_noon)


def test_next_change():
    assert S.next_change("always") is None
    assert S.next_change("offhours", at(2026, 9, 21, 10)) == at(2026, 9, 21, 17)
    assert S.next_change("offhours", at(2026, 9, 25, 18)) == at(2026, 9, 28, 9)  # Friday evening -> Monday 09:00


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def handler(self, request: httpx.Request):
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content or b"{}")
        self.sent.append((method, body))
        return httpx.Response(200, json={"ok": True, "result": {}})


def make_bot(allowed=("111",)):
    fake = FakeTelegram()
    bot = TelegramBot("TOKEN", list(allowed))
    bot.http = httpx.Client(transport=httpx.MockTransport(fake.handler))
    return bot, fake


def test_start_from_unknown_chat_reveals_only_chat_id():
    bot, fake = make_bot()
    bot.handle({"message": {"text": "/start", "chat": {"id": 999}}})
    assert fake.sent[0][0] == "sendMessage" and "999" in fake.sent[0][1]["text"]
    fake.sent.clear()
    bot.handle({"message": {"text": "/status", "chat": {"id": 999}}})
    assert fake.sent == []  # no data for unknown chats


def test_format_escapes_html():
    inc = {"id": 3, "namespace": "ns", "workload": "deployment/a<b>", "pod": "p", "reason": "CrashLoopBackOff"}
    resp = {"diagnosis": {"summary": "<script>x</script>", "category": "Other", "confidence": 0.5,
                          "root_cause": "r & s", "evidence": ["e"], "next_command": "kubectl get pods -n ns",
                          "next_command_safe": True, "fix": ""}, "usage": {"model_key": "m"}}
    txt = TelegramBot.format_diagnosis(inc, resp, "http://x")
    assert "<script>" not in txt and "&lt;script&gt;" in txt and "r &amp; s" in txt and "incident=3" in txt


def test_allowed_commands_and_buttons():
    from fastapi.testclient import TestClient
    from app.main import app, engine
    c = TestClient(app)
    c.post("/api/scan")
    e = engine()
    bot, fake = make_bot()
    bot.engine = e
    bot.handle({"message": {"text": "/incidents", "chat": {"id": 111}}})
    assert "open" in fake.sent[-1][1]["text"]
    bot.handle({"message": {"text": "/mode offhours", "chat": {"id": 111}}})
    assert e.alert_mode() == "offhours" and "offhours" in fake.sent[-1][1]["text"]
    e.set_alert_mode("always")
    iid = e.store.list("open")[0]["id"]
    bot.handle({"callback_query": {"id": "cb", "data": f"ok:{iid}", "message": {"chat": {"id": 111}}}})
    assert e.store.get(iid)["feedback"] == "correct"
    assert fake.sent[-1][0] == "answerCallbackQuery"


def test_alerting_api_and_offhours_suppression(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app, engine
    c = TestClient(app)
    assert c.get("/api/alerting").json()["mode"] in ("always", "offhours")
    assert c.post("/api/alerting", json={"mode": "nope"}).status_code == 400
    assert c.post("/api/alerting", json={"mode": "offhours"}).json()["mode"] == "offhours"
    e = engine()
    bot, fake = make_bot()
    e.notifier.telegram = bot
    monkeypatch.setattr(e.schedule, "is_business_hours", lambda when=None: True)
    inc = e.store.list("open")[0] if e.store.list("open") else None
    if inc:
        e.notifier.diagnosed(inc, {"diagnosis": {"summary": "s", "category": "Other", "confidence": .5, "root_cause": "r",
                                                 "evidence": [], "next_command": "kubectl get pods -n x",
                                                 "next_command_safe": True, "fix": ""}, "usage": {"model_key": "m"}})
        assert fake.sent == []  # suppressed during business hours
        assert any("not pushed" in ev["data"].get("text", "") for ev in e.store.timeline(inc["id"]))
    c.post("/api/alerting", json={"mode": "always"})
    if inc:
        e.notifier.diagnosed(inc, {"diagnosis": {"summary": "s", "category": "Other", "confidence": .5, "root_cause": "r",
                                                 "evidence": [], "next_command": "kubectl get pods -n x",
                                                 "next_command_safe": True, "fix": ""}, "usage": {"model_key": "m"}})
        assert fake.sent and fake.sent[-1][0] == "sendMessage"


def test_remediation_flow_requires_preview_and_hash(monkeypatch):
    from fastapi.testclient import TestClient
    from app import remediate
    from app.config import get_settings
    from app.main import app, engine
    c = TestClient(app)
    c.post("/api/scan")
    e = engine()
    monkeypatch.setattr(get_settings(), "firstcall_remediation", "approve")
    calls = []
    monkeypatch.setattr(remediate, "_run", lambda cmd, extra, timeout=30: calls.append((cmd, extra)) or
                        {"command": cmd, "exit_code": 0, "output": "deployment.apps/x rolled back", "duration_ms": 5})
    orders = next(i for i in e.store.list("open") if i["pod"] == "svc/orders")
    c.post(f"/api/incidents/{orders['id']}/diagnose", json={"model": "qwen3-30b"})
    import time
    while e.busy:
        time.sleep(0.05)
    d = e.store.last_diagnosis(orders["id"])["diagnosis"]
    assert d["remediation_command"].startswith("kubectl patch service orders") and d["remediation_allowed"]
    h = remediate.cmd_hash(d["remediation_command"])
    # apply without preview is refused
    assert c.post(f"/api/incidents/{orders['id']}/remediation/apply", json={"hash": h}).status_code == 409
    p = c.post(f"/api/incidents/{orders['id']}/remediation/preview").json()
    assert p["ok"] and calls[-1][1] == ["--dry-run=server"]
    assert c.post(f"/api/incidents/{orders['id']}/remediation/apply", json={"hash": "wrong"}).status_code == 409
    r = c.post(f"/api/incidents/{orders['id']}/remediation/apply", json={"hash": h, "approver": "adam"}).json()
    assert r["ok"] and calls[-1][1] == []
    kinds = [ev["kind"] for ev in e.store.timeline(orders["id"])]
    assert "remediation_preview" in kinds and "remediation" in kinds
    assert any(a["kind"] == "remediate" for a in c.get("/api/activity").json())


def test_remediation_disabled_by_default():
    from app import remediate
    ok, why = remediate.check("kubectl rollout undo deployment/x -n firstcall-demo")
    assert not ok and "disabled" in why


def test_web_search_sanitizes_and_cites(monkeypatch):
    import httpx
    from app import websearch
    from app.config import get_settings
    from app.k8s.fixtures import FixtureSource
    from app.diagnose import diagnose, diagnose_with_web
    from app.llm.client import MockClient

    q = websearch.sanitize('pod payments-api-6bc9c4cdc5-h8cfm failed: dial tcp 10.42.0.78:8080 refused '
                           'token=abc123secret image=123456.dkr.ecr.eu-west-1.amazonaws.com/app')
    assert "payments-api-6bc9c4cdc5-h8cfm" not in q and "10.42.0.78" not in q and "ecr" not in q.lower()

    src = FixtureSource(get_settings().fixtures_dir)
    snap = src.by_scenario("06-config-missing")
    query = websearch.build_query(snap, "ConfigMissing")
    assert query.startswith("kubernetes") and "notify-svc-" not in query

    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = request.content.decode()
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"results": [
            {"title": "ConfigMap not found", "url": "https://kubernetes.io/docs/x", "content": "create the ConfigMap"},
            {"title": "SO answer", "url": "https://stackoverflow.com/q/1", "content": "envFrom requires the ConfigMap"}]})

    monkeypatch.setattr(get_settings(), "tavily_api_key", "tvly-test")
    tv = websearch.Tavily()
    tv.http = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(websearch, "Tavily", lambda *a, **k: tv)
    first = diagnose(snap, "qwen3-30b", client=MockClient())
    better = diagnose_with_web(snap, first, "qwen3-30b", client=MockClient())
    assert seen["auth"] == "Bearer tvly-test" and "kubernetes" in seen["body"]
    assert better and better.diagnosis.sources == ["https://kubernetes.io/docs/x", "https://stackoverflow.com/q/1"]


def test_voice_text_is_speakable():
    from app.voice import spoken_text
    txt = spoken_text({"namespace": "prod", "workload": "deployment/payments-api", "pod": "p", "reason": "CrashLoopBackOff"},
                      {"diagnosis": {"root_cause": "DATABASE_URL is not set.", "suspected_change": "Revision 2 removed it.",
                                     "remediation_command": "kubectl rollout undo deployment/payments-api -n prod"}})
    assert "payments api" in txt and "approval" in txt and len(txt) < 900
