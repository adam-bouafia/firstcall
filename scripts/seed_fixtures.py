"""Write hand-made fixtures that mirror scenarios/manifests. Replace them with real
captures on the day: `make capture` (scripts/capture_fixtures.py) keeps the `expected` block."""
import json
from pathlib import Path

NS = "firstcall-demo"
OUT = Path(__file__).resolve().parents[1] / "scenarios" / "fixtures"


def c(name, image, **kw):
    return {"name": name, "image": image, **kw}


FIX = {
    "01-crashloop": dict(
        reason="CrashLoopBackOff", age="7m",
        snapshot=dict(
            namespace=NS, pod="payments-api-7d9f8c6b5-x2lqp", phase="Running", workload="deployment/payments-api",
            pod_summary={"node": "firstcall-worker", "labels": {"app": "payments-api"},
                         "containers": [c("api", "python:3.12-alpine", command=["python", "-c"],
                                          env_names=["LOG_LEVEL"], resources={"limits": {"memory": "128Mi"}})]},
            container_statuses=[{"name": "api", "ready": False, "restart_count": 6, "state": "waiting",
                                 "reason": "CrashLoopBackOff",
                                 "message": "back-off 2m40s restarting failed container=api pod=payments-api-7d9f8c6b5-x2lqp",
                                 "last_terminated": {"reason": "Error", "exit_code": 1}}],
            events=[{"type": "Normal", "reason": "Pulled", "message": "Container image \"python:3.12-alpine\" already present on machine", "count": 7},
                    {"type": "Warning", "reason": "BackOff", "message": "Back-off restarting failed container api in pod payments-api-7d9f8c6b5-x2lqp", "count": 29}],
            logs={"api": "--- previous ---\npayments-api v2.3.1 starting\nloaded config STRIPE_API_KEY=sk_live_51HxFAKEFAKEFAKE0000 region=eu-west\n"
                         "Traceback (most recent call last):\n  File \"<string>\", line 4, in <module>\n"
                         "  File \"<frozen os>\", line 714, in __getitem__\nKeyError: 'DATABASE_URL'\n"},
            related={"services": [], "pvcs": []}),
        expected={"category": "CrashLoop-AppError", "keywords": [["DATABASE_URL"], ["env", "environment"]],
                  "command_any": ["logs", "describe", "get deploy", "set env"]},
    ),
    "02-imagepull": dict(
        reason="ImagePullBackOff", age="4m",
        snapshot=dict(
            namespace=NS, pod="checkout-web-5c7b9d8f4-k8wzt", phase="Pending", workload="deployment/checkout-web",
            pod_summary={"labels": {"app": "checkout-web"}, "containers": [c("web", "busybox:1.63", ports=[80])]},
            container_statuses=[{"name": "web", "ready": False, "restart_count": 0, "state": "waiting", "reason": "ImagePullBackOff",
                                 "message": "Back-off pulling image \"busybox:1.63\""}],
            events=[{"type": "Normal", "reason": "Pulling", "message": "Pulling image \"busybox:1.63\"", "count": 4},
                    {"type": "Warning", "reason": "Failed", "message": "Failed to pull image \"busybox:1.63\": rpc error: code = NotFound desc = failed to pull and unpack image \"docker.io/library/busybox:1.63\": docker.io/library/busybox:1.63: not found", "count": 4},
                    {"type": "Warning", "reason": "Failed", "message": "Error: ErrImagePull", "count": 4},
                    {"type": "Normal", "reason": "BackOff", "message": "Back-off pulling image \"busybox:1.63\"", "count": 15}],
            logs={"web": ""}, related={"services": [], "pvcs": []}),
        expected={"category": "ImagePull", "keywords": [["1.63"], ["1.36", "typo", "tag", "does not exist", "not found"]],
                  "command_any": ["describe", "get events", "set image"]},
    ),
    "03-oom": dict(
        reason="OOMKilled", age="6m",
        snapshot=dict(
            namespace=NS, pod="report-worker-6f4d7b9c8-qz7mv", phase="Running", workload="deployment/report-worker",
            pod_summary={"labels": {"app": "report-worker"},
                         "containers": [c("worker", "python:3.12-alpine", resources={"requests": {"cpu": "50m", "memory": "32Mi"}, "limits": {"memory": "64Mi"}})]},
            container_statuses=[{"name": "worker", "ready": False, "restart_count": 5, "state": "waiting", "reason": "CrashLoopBackOff",
                                 "last_terminated": {"reason": "OOMKilled", "exit_code": 137}}],
            events=[{"type": "Warning", "reason": "BackOff", "message": "Back-off restarting failed container worker in pod report-worker-6f4d7b9c8-qz7mv", "count": 21}],
            logs={"worker": "--- previous ---\nreport-worker: loading monthly dataset into memory\n"},
            metrics={"worker": {"cpu": "12m", "memory": "61Mi"}}, related={"services": [], "pvcs": []}),
        expected={"category": "OOMKilled", "keywords": [["memory", "64Mi", "limit"], ["OOM", "137"]],
                  "command_any": ["describe", "top", "set resources", "get pod"]},
    ),
    "04-unschedulable": dict(
        reason="Unschedulable", age="9m",
        snapshot=dict(
            namespace=NS, pod="ml-batch-84c5f6d7b9-p4n2r", phase="Pending", workload="deployment/ml-batch",
            pod_summary={"node": None, "labels": {"app": "ml-batch"},
                         "containers": [c("trainer", "busybox:1.36", resources={"requests": {"cpu": "64", "memory": "1Gi"}})],
                         "conditions": [{"type": "PodScheduled", "status": "False", "reason": "Unschedulable",
                                         "message": "0/3 nodes are available: 1 node(s) had untolerated taint {node-role.kubernetes.io/control-plane: }, 2 Insufficient cpu. preemption: 0/3 nodes are available: 1 Preemption is not helpful for scheduling, 2 No preemption victims found for incoming pod."}]},
            container_statuses=[],
            events=[{"type": "Warning", "reason": "FailedScheduling", "message": "0/3 nodes are available: 1 node(s) had untolerated taint {node-role.kubernetes.io/control-plane: }, 2 Insufficient cpu.", "count": 12}],
            logs={},
            related={"services": [], "pvcs": [], "nodes": [
                {"name": "firstcall-control-plane", "allocatable": {"cpu": "4", "memory": "7Gi"}, "taints": [{"key": "node-role.kubernetes.io/control-plane", "effect": "NoSchedule"}]},
                {"name": "firstcall-worker", "allocatable": {"cpu": "4", "memory": "7Gi"}, "taints": []},
                {"name": "firstcall-worker2", "allocatable": {"cpu": "4", "memory": "7Gi"}, "taints": []}]}),
        expected={"category": "Unschedulable", "keywords": [["cpu", "Insufficient"], ["64", "request"]],
                  "command_any": ["describe node", "get nodes", "top node", "describe pod"]},
    ),
    "05-probe": dict(
        reason="NotReady", age="5m",
        snapshot=dict(
            namespace=NS, pod="catalog-api-66b8d9f7c4-hx9tn", phase="Running", workload="deployment/catalog-api",
            pod_summary={"labels": {"app": "catalog-api"},
                         "containers": [c("api", "nginx:1.27-alpine", ports=[80],
                                          readiness_probe={"http_get": {"path": "/healthz", "port": 8080}, "period_seconds": 5, "failure_threshold": 3})]},
            container_statuses=[{"name": "api", "ready": False, "restart_count": 0, "state": "running"}],
            events=[{"type": "Warning", "reason": "Unhealthy", "message": "Readiness probe failed: Get \"http://10.244.1.17:8080/healthz\": dial tcp 10.244.1.17:8080: connect: connection refused", "count": 58}],
            logs={"api": "--- current ---\n/docker-entrypoint.sh: Configuration complete; ready for start up\n2026/09/23 09:14:02 [notice] 1#1: nginx/1.27.1\n2026/09/23 09:14:02 [notice] 1#1: start worker processes\n"},
            related={"services": [], "pvcs": []}),
        expected={"category": "ProbeFailure", "keywords": [["8080"], ["80", "port"]],
                  "command_any": ["describe", "get pod", "patch", "port-forward"]},
    ),
    "06-config-missing": dict(
        reason="CreateContainerConfigError", age="3m",
        snapshot=dict(
            namespace=NS, pod="notify-svc-7b6c5d4f9-m3rkd", phase="Pending", workload="deployment/notify-svc",
            pod_summary={"labels": {"app": "notify-svc"},
                         "containers": [c("svc", "busybox:1.36", env_from=["configMap/notify-config"])]},
            container_statuses=[{"name": "svc", "ready": False, "restart_count": 0, "state": "waiting",
                                 "reason": "CreateContainerConfigError", "message": "configmap \"notify-config\" not found"}],
            events=[{"type": "Warning", "reason": "Failed", "message": "Error: configmap \"notify-config\" not found", "count": 14}],
            logs={"svc": ""}, related={"services": [], "pvcs": []}),
        expected={"category": "ConfigMissing", "keywords": [["notify-config"], ["configmap", "ConfigMap"]],
                  "command_any": ["get configmap", "get cm", "describe"]},
    ),
    "07-service-selector": dict(
        reason="NoEndpoints", age="11m",
        snapshot=dict(
            namespace=NS, pod="svc/orders", phase="NoEndpoints", workload="service/orders",
            pod_summary={"service": {"selector": {"app": "orders"}, "ports": [{"port": 80, "target_port": 80, "protocol": "TCP"}]}},
            events=[],
            related={"endpoints": {"orders": []}, "pods_in_namespace": [
                {"name": "orders-5d8f7c9b6-2kx8p", "labels": {"app": "orders-api", "pod-template-hash": "5d8f7c9b6"}, "phase": "Running"},
                {"name": "orders-5d8f7c9b6-9wq4z", "labels": {"app": "orders-api", "pod-template-hash": "5d8f7c9b6"}, "phase": "Running"}]}),
        expected={"category": "ServiceSelector", "keywords": [["selector", "label"], ["orders-api"]],
                  "command_any": ["get endpoints", "get ep", "get pods --show-labels", "get pods -l", "describe svc", "describe service", "get endpointslice"]},
    ),
    "08-pvc-pending": dict(
        reason="Unschedulable", age="8m",
        snapshot=dict(
            namespace=NS, pod="ledger-db-58c9f7d6b4-v7tjk", phase="Pending", workload="deployment/ledger-db",
            pod_summary={"labels": {"app": "ledger-db"},
                         "containers": [c("db", "busybox:1.36")],
                         "volumes": [{"name": "data", "persistent_volume_claim": {"claim_name": "ledger-data"}}],
                         "conditions": [{"type": "PodScheduled", "status": "False", "reason": "Unschedulable",
                                         "message": "0/3 nodes are available: pod has unbound immediate PersistentVolumeClaims. preemption: 0/3 nodes are available: 3 Preemption is not helpful for scheduling."}]},
            events=[{"type": "Warning", "reason": "FailedScheduling", "message": "0/3 nodes are available: pod has unbound immediate PersistentVolumeClaims.", "count": 9}],
            logs={},
            related={"services": [], "pvcs": [{"name": "ledger-data", "phase": "Pending", "storage_class": "fast-ssd",
                                              "events": [{"type": "Warning", "reason": "ProvisioningFailed", "message": "storageclass.storage.k8s.io \"fast-ssd\" not found", "count": 34}]}]}),
        expected={"category": "StoragePending", "keywords": [["fast-ssd"], ["StorageClass", "storageclass", "storage class"]],
                  "command_any": ["get storageclass", "get sc", "describe pvc"]},
    ),
}

OUT.mkdir(parents=True, exist_ok=True)
for name, data in FIX.items():
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n")
print(f"wrote {len(FIX)} fixtures to {OUT}")
