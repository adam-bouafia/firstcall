"""Helper for scripts/e2e.sh (keeps the shell script free of inline Python quoting)."""
import json
import sys
import urllib.request

api, cmd, *args = sys.argv[1:]
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
get = lambda p: json.load(op.open(f"{api}{p}"))  # noqa: E731

if cmd == "eval":  # exit 0 if the expression over incidents `x` and stats `s` is true
    x, s = get("/api/incidents"), get("/api/stats")
    sys.exit(0 if eval(args[0]) else 1)
elif cmd == "show":
    for i in get("/api/incidents"):
        if i["status"] == "open":
            print(f"    {i['workload']:<28} {i['reason']:<28} -> {i['category']}")
elif cmd == "alert-attached":
    wl = args[0]
    inc = [i for i in get("/api/incidents") if (i["workload"] or "").endswith(wl) and i["status"] == "open"][0]
    tl = get(f"/api/incidents/{inc['id']}")["timeline"]
    sys.exit(0 if any(e["kind"] == "note" and "Alertmanager" in e["data"].get("text", "") for e in tl) else 1)
elif cmd == "remediate":  # preview + approve the proposed fix for a workload, print the result
    import json as _j
    wl = args[0]
    inc = [i for i in get("/api/incidents") if (i["workload"] or "").endswith(wl) and i["status"] == "open"][0]
    post = lambda p, body=None: json.load(op.open(urllib.request.Request(  # noqa: E731
        f"{api}{p}", data=_j.dumps(body or {}).encode(), headers={"content-type": "application/json"}, method="POST")))
    prev = post(f"/api/incidents/{inc['id']}/remediation/preview")
    print(f"    dry run: {prev.get('reason')} :: {prev.get('command')}")
    if not prev.get("ok"):
        sys.exit(1)
    res = post(f"/api/incidents/{inc['id']}/remediation/apply", {"hash": prev["hash"], "approver": "e2e"})
    print(f"    applied: {res.get('output')}")
    sys.exit(0 if res.get("ok") else 1)
elif cmd == "stats":
    s = get("/api/stats")
    print(f"    time to first diagnosis {s['mean_time_to_diagnosis_s'] or 0:.1f}s · "
          f"time to resolve {s['mean_time_to_resolve_s'] or 0:.0f}s · diagnoses {s['diagnoses']}")
elif cmd == "health":
    h = get("/health")
    print(h["source"], h["llm"], h["default_model"])
