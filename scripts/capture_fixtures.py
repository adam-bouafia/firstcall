"""Record live snapshots from the kind cluster into scenarios/fixtures, keeping each
scenario's `expected` ground-truth block. Run after `make scenarios` + ~3 minutes."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.k8s.cluster import ClusterSource  # noqa: E402

FIX = ROOT / "scenarios" / "fixtures"
WORKLOAD_TO_SCENARIO = {
    "payments-api": "01-crashloop", "checkout-web": "02-imagepull", "report-worker": "03-oom",
    "ml-batch": "04-unschedulable", "catalog-api": "05-probe", "notify-svc": "06-config-missing",
    "orders": "07-service-selector", "ledger-db": "08-pvc-pending",
}

def main():
    src = ClusterSource(["firstcall-demo"])
    seen = set()
    for inc in src.list_incidents():
        name = (inc.workload or inc.pod).split("/", 1)[-1]
        sc = WORKLOAD_TO_SCENARIO.get(name)
        if not sc or sc in seen:
            continue
        seen.add(sc)
        path = FIX / f"{sc}.json"
        old = json.loads(path.read_text()) if path.exists() else {}
        snap = src.snapshot(inc.namespace, inc.pod)
        data = {"reason": inc.reason, "age": inc.age, "snapshot": snap.model_dump(exclude={"redactions"}),
                "expected": old.get("expected", {})}
        path.write_text(json.dumps(data, indent=2, default=str) + "\n")
        print(f"captured {sc:<22} <- {inc.namespace}/{inc.pod} ({inc.reason})")
    missing = set(WORKLOAD_TO_SCENARIO.values()) - seen
    if missing:
        print("not yet broken (wait and re-run):", ", ".join(sorted(missing)))


if __name__ == "__main__":
    main()
