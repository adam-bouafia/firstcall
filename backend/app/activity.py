"""A live, in-memory feed of what FirstCall is doing (the console's "Live activity" panel,
`make activity` in a terminal, GET /api/activity). Newest 1,000 entries."""
import itertools
import logging
import threading
import time
from collections import deque

_buf: deque = deque(maxlen=1000)
_ids = itertools.count(1)
_lock = threading.Lock()
log = logging.getLogger("firstcall.activity")

KINDS = ("scan", "detect", "collect", "model", "search", "command", "event", "notify", "resolve",
         "alert", "remediate", "telegram", "voice", "error", "config")


def emit(kind: str, text: str, incident: int | None = None, level: str = "info", **data):
    entry = {"id": next(_ids), "ts": time.time(), "kind": kind, "level": level, "text": text,
             "incident": incident, **({"data": data} if data else {})}
    with _lock:
        _buf.append(entry)
    (log.warning if level in ("warn", "error") else log.info)("[%s]%s %s", kind, f" #{incident}" if incident else "", text)
    return entry


def since(last_id: int = 0, limit: int = 300) -> list[dict]:
    with _lock:
        items = [e for e in _buf if e["id"] > last_id]
    return items[-limit:]
