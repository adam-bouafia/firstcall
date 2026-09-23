"""Follow FirstCall's live activity in a terminal (like `tail -f`).
   python scripts/activity.py [--api http://localhost:8000] [--scans]"""
import argparse
import datetime
import json
import time
import urllib.request

C = {"scan": "90", "detect": "91", "collect": "94", "model": "96", "command": "94", "event": "95", "notify": "95",
     "resolve": "92", "alert": "93", "remediate": "93", "telegram": "95", "error": "91;1", "config": "90"}

ap = argparse.ArgumentParser()
ap.add_argument("--api", default="http://localhost:8000")
ap.add_argument("--scans", action="store_true", help="also show the 15-second scan lines")
a = ap.parse_args()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
last, warned = 0, False
print(f"following {a.api}/api/activity  (Ctrl-C to stop)\n")
while True:
    try:
        items = json.load(opener.open(f"{a.api}/api/activity?since={last}", timeout=5))
        warned = False
        for e in items:
            last = e["id"]
            if e["kind"] == "scan" and not a.scans:
                continue
            t = datetime.datetime.fromtimestamp(e["ts"]).strftime("%H:%M:%S")
            inc = f"#{e['incident']:<3}" if e.get("incident") else "    "
            print(f"\033[90m{t}\033[0m \033[{C.get(e['kind'], '0')}m{e['kind'].upper():<9}\033[0m {inc} {e['text']}", flush=True)
    except KeyboardInterrupt:
        break
    except Exception as ex:
        if not warned:
            print(f"\033[91mAPI not reachable ({ex}); retrying…\033[0m", flush=True)
            warned = True
    time.sleep(1.5)
