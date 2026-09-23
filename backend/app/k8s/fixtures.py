"""Recorded snapshots: the demo works on a plane, on bad Wi-Fi, and in the benchmark."""
import json
from pathlib import Path

from app.k8s.source import ContextSource
from app.schemas import Incident, Snapshot


class FixtureSource(ContextSource):
    def __init__(self, directory: Path):
        self.dir = Path(directory)

    def _load_all(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("*.json")):
            data = json.loads(p.read_text())
            data.setdefault("_scenario", p.stem)
            out.append(data)
        return out

    def list_incidents(self) -> list[Incident]:
        items = []
        for d in self._load_all():
            snap = d["snapshot"]
            cs = (snap.get("container_statuses") or [{}])[0]
            items.append(
                Incident(
                    namespace=snap["namespace"],
                    pod=snap["pod"],
                    reason=d.get("reason") or cs.get("reason") or snap.get("phase", ""),
                    restarts=cs.get("restart_count", 0),
                    age=d.get("age", "5m"),
                    scenario=d["_scenario"],
                    workload=snap.get("workload"),
                )
            )
        return items

    def snapshot(self, namespace: str, pod: str) -> Snapshot:
        for d in self._load_all():
            s = d["snapshot"]
            if s["namespace"] == namespace and s["pod"] == pod:
                return Snapshot(**s)
        raise KeyError(f"no fixture for {namespace}/{pod}")

    def ground_truth(self) -> dict[str, dict]:
        """scenario -> expected answer, used by the benchmark."""
        return {d["_scenario"]: d["expected"] for d in self._load_all() if "expected" in d}

    def by_scenario(self, scenario: str) -> Snapshot:
        for d in self._load_all():
            if d["_scenario"] == scenario:
                return Snapshot(**d["snapshot"])
        raise KeyError(scenario)
