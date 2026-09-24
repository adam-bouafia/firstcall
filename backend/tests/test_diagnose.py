import time

from fastapi.testclient import TestClient

from app.diagnose import parse_diagnosis
from app.config import get_settings
from app.main import app, engine

c = TestClient(app)
N = len(list(get_settings().fixtures_dir.glob("*.json")))  # one incident per recorded scenario


def wait_idle(timeout=10):
    t = time.time()
    while engine().busy and time.time() - t < timeout:
        time.sleep(0.05)


def test_parse_with_think_and_fences():
    d = parse_diagnosis('<think>hmm</think>```json\n{"summary":"s","category":"OOMKilled","root_cause":"r",'
                        '"confidence":1.4,"evidence":"e","next_command":"kubectl top pod x -n d","fix":"f"}\n```')
    assert d.category == "OOMKilled" and d.confidence == 1.0 and d.evidence == ["e"] and d.next_command_safe


def test_unknown_category_falls_back():
    d = parse_diagnosis('{"summary":"s","category":"Weird","root_cause":"r","confidence":0.3,'
                        '"next_command":"kubectl delete pod x"}')
    assert d.category == "Other" and not d.next_command_safe


def test_scan_detects_and_auto_diagnoses():
    r = c.post("/api/scan").json()
    assert r["seen"] == N  # (new may be 0 if another test module scanned first)
    wait_idle()
    incs = c.get("/api/incidents").json()
    assert len(incs) == N and all(i["category"] and i["category"] != "Other" for i in incs)
    # second scan: nothing new
    assert c.post("/api/scan").json()["new"] == []


def test_timeline_and_feedback():
    iid = c.get("/api/incidents").json()[0]["id"]
    c.post(f"/api/incidents/{iid}/diagnose", json={"model": "cascade"})
    wait_idle()
    tl = c.get(f"/api/incidents/{iid}").json()["timeline"]
    kinds = [e["kind"] for e in tl]
    assert kinds[0] == "detected" and kinds.count("diagnosis") >= 2
    assert "cascade" in tl[-1]["data"]["usage"]["model_key"]
    assert c.post(f"/api/incidents/{iid}/feedback", json={"value": "correct"}).status_code == 200
    assert c.get("/api/stats").json()["feedback_correct"] >= 1


def test_crashloop_secret_is_redacted():
    inc = next(i for i in c.get("/api/incidents").json() if i["pod"].startswith("payments-api"))
    snap = c.get(f"/api/incidents/{inc['id']}/snapshot").json()
    assert "sk_live" not in str(snap) and snap["redactions"] >= 1


def test_resolution_after_healthy_scans(monkeypatch):
    e = engine()
    monkeypatch.setattr(e.source, "list_incidents", lambda: [])
    e.scan()
    assert c.get("/api/stats").json()["resolved"] == 0  # needs 2 healthy scans
    e.scan()
    assert c.get("/api/stats").json()["resolved"] == N


def test_compare_stateless():
    r = c.post("/api/compare", json={"namespace": "firstcall-demo", "pod": "svc/orders",
                                      "models": ["qwen3-30b", "local-qwen2.5-7b"]})
    assert r.status_code == 200 and all("diagnosis" in v for v in r.json().values())


def test_alertmanager_webhook():
    r = c.post("/api/alertmanager", json={"alerts": [{"status": "firing", "labels": {"namespace": "x"}}]})
    assert r.json() == {"received": 1, "firing": 1}


def test_metrics_endpoint():
    body = c.get("/metrics/").text
    assert "firstcall_diagnoses_total" in body and "firstcall_incidents_detected_total" in body


def test_provider_prefixed_model_id():
    from app.llm.client import resolve_model
    import pytest
    k, m = resolve_model("groq:openai/gpt-oss-20b")
    assert m["provider"] == "groq" and m["id"] == "openai/gpt-oss-20b"
    with pytest.raises(RuntimeError):
        resolve_model("does-not-exist")


def test_placeholder_fix_is_dropped():
    # "<value-from-vault>" means a human must supply the value: never offer it as a one-click fix
    d = parse_diagnosis('{"summary":"s","category":"CrashLoop-AppError","root_cause":"r","confidence":0.9,'
                        '"next_command":"kubectl logs x -n firstcall-demo","fix":"f","remediation_command":'
                        '"kubectl set env deployment/payments-api DATABASE_URL=<value-from-vault> -n firstcall-demo"}')
    assert d.remediation_command == "" and not d.remediation_allowed
