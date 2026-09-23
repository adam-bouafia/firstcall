"""SQLite incident store: the product's memory. One file, no server, survives restarts."""
from __future__ import annotations
import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fingerprint TEXT NOT NULL,
  namespace TEXT NOT NULL,
  workload TEXT,
  pod TEXT NOT NULL,
  reason TEXT NOT NULL,
  restarts INTEGER DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'open',      -- open | resolved
  first_seen REAL NOT NULL,
  last_seen REAL NOT NULL,
  first_diagnosis_at REAL,
  resolved_at REAL,
  healthy_scans INTEGER DEFAULT 0,
  feedback TEXT                               -- correct | wrong | null
);
CREATE INDEX IF NOT EXISTS ix_inc_fp ON incidents(fingerprint, status);
CREATE TABLE IF NOT EXISTS events (           -- the incident timeline
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id INTEGER NOT NULL REFERENCES incidents(id),
  ts REAL NOT NULL,
  kind TEXT NOT NULL,                         -- detected | diagnosis | command | resolved | reopened | note | error
  data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ev_inc ON events(incident_id, ts);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Store:
    def __init__(self, path: Path | str):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)

    # ---------- incidents ----------
    def open_incident(self, fingerprint: str) -> dict | None:
        r = self.db.execute("SELECT * FROM incidents WHERE fingerprint=? AND status='open' ORDER BY id DESC LIMIT 1",
                            (fingerprint,)).fetchone()
        return dict(r) if r else None

    def upsert_seen(self, inc) -> tuple[dict, bool]:
        """Record that `inc` is unhealthy now. Returns (row, is_new)."""
        now = time.time()
        with self.lock:
            row = self.open_incident(inc.fingerprint)
            if row:
                self.db.execute("UPDATE incidents SET last_seen=?, pod=?, reason=?, restarts=?, healthy_scans=0 WHERE id=?",
                                (now, inc.pod, inc.reason, inc.restarts, row["id"]))
                self.db.commit()
                if row["reason"] != inc.reason:
                    self._event(row["id"], "note", {"text": f"reason changed {row['reason']} -> {inc.reason}"})
                return self.get(row["id"]), False
            cur = self.db.execute(
                "INSERT INTO incidents(fingerprint,namespace,workload,pod,reason,restarts,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?)",
                (inc.fingerprint, inc.namespace, inc.workload, inc.pod, inc.reason, inc.restarts, now, now))
            self.db.commit()
            iid = cur.lastrowid
            self._event(iid, "detected", {"pod": inc.pod, "reason": inc.reason, "restarts": inc.restarts})
            return self.get(iid), True

    def mark_healthy_scan(self, seen_fingerprints: set[str], resolve_after: int) -> list[dict]:
        """Bump healthy counters for open incidents not seen this scan; resolve when threshold hit."""
        resolved = []
        with self.lock:
            rows = self.db.execute("SELECT * FROM incidents WHERE status='open'").fetchall()
            for r in rows:
                if r["fingerprint"] in seen_fingerprints:
                    continue
                n = r["healthy_scans"] + 1
                if n >= resolve_after:
                    now = time.time()
                    self.db.execute("UPDATE incidents SET status='resolved', resolved_at=?, healthy_scans=? WHERE id=?",
                                    (now, n, r["id"]))
                    self._event(r["id"], "resolved", {"after_s": round(now - r["first_seen"])})
                    resolved.append(dict(r))
                else:
                    self.db.execute("UPDATE incidents SET healthy_scans=? WHERE id=?", (n, r["id"]))
            self.db.commit()
        return resolved

    def get(self, iid: int) -> dict | None:
        r = self.db.execute("SELECT * FROM incidents WHERE id=?", (iid,)).fetchone()
        return dict(r) if r else None

    def list(self, status: str | None = None, limit: int = 200) -> list[dict]:
        q, args = "SELECT * FROM incidents", []
        if status:
            q += " WHERE status=?"
            args.append(status)
        q += " ORDER BY status='resolved', last_seen DESC LIMIT ?"
        args.append(limit)
        out = []
        for r in self.db.execute(q, args).fetchall():
            d = dict(r)
            last = self.last_diagnosis(d["id"])
            d["summary"] = last["diagnosis"]["summary"] if last else None
            d["category"] = last["diagnosis"]["category"] if last else None
            out.append(d)
        return out

    def set_feedback(self, iid: int, value: str):
        with self.lock:
            self.db.execute("UPDATE incidents SET feedback=? WHERE id=?", (value, iid))
            self.db.commit()
            self._event(iid, "note", {"text": f"feedback: diagnosis marked {value}"})

    # ---------- timeline ----------
    def _event(self, iid: int, kind: str, data: dict):
        self.db.execute("INSERT INTO events(incident_id,ts,kind,data) VALUES(?,?,?,?)",
                        (iid, time.time(), kind, json.dumps(data, default=str)))
        self.db.commit()

    def add_event(self, iid: int, kind: str, data: dict):
        with self.lock:
            self._event(iid, kind, data)
            if kind == "diagnosis":
                self.db.execute("UPDATE incidents SET first_diagnosis_at=COALESCE(first_diagnosis_at, ?) WHERE id=?",
                                (time.time(), iid))
                self.db.commit()

    def timeline(self, iid: int) -> list[dict]:
        rows = self.db.execute("SELECT * FROM events WHERE incident_id=? ORDER BY ts, id", (iid,)).fetchall()
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    def last_diagnosis(self, iid: int) -> dict | None:
        r = self.db.execute("SELECT data FROM events WHERE incident_id=? AND kind='diagnosis' ORDER BY ts DESC, id DESC LIMIT 1",
                            (iid,)).fetchone()
        return json.loads(r["data"]) if r else None

    # ---------- settings ----------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        r = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_setting(self, key: str, value: str):
        with self.lock:
            self.db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, value))
            self.db.commit()

    # ---------- stats ----------
    def stats(self) -> dict:
        q = lambda sql: self.db.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "open": q("SELECT COUNT(*) FROM incidents WHERE status='open'"),
            "resolved": q("SELECT COUNT(*) FROM incidents WHERE status='resolved'"),
            "mean_time_to_diagnosis_s": q("SELECT AVG(first_diagnosis_at-first_seen) FROM incidents WHERE first_diagnosis_at IS NOT NULL"),
            "mean_time_to_resolve_s": q("SELECT AVG(resolved_at-first_seen) FROM incidents WHERE resolved_at IS NOT NULL"),
            "diagnoses": q("SELECT COUNT(*) FROM events WHERE kind='diagnosis'"),
            "total_cost_usd": q("SELECT COALESCE(SUM(json_extract(data,'$.usage.cost_usd')),0) FROM events WHERE kind='diagnosis'"),
            "feedback_correct": q("SELECT COUNT(*) FROM incidents WHERE feedback='correct'"),
            "feedback_wrong": q("SELECT COUNT(*) FROM incidents WHERE feedback='wrong'"),
        }
