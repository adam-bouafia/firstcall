"""Live collector. Read-only: FirstCall only needs get/list/watch + pods/log.

See infra/rbac.yaml for the exact ClusterRole to run it with least privilege.
"""
import uuid
from datetime import datetime, timezone

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from app.k8s.source import ContextSource
from app.schemas import Incident, Snapshot

BAD_WAITING = {
    "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError",
    "CreateContainerError", "InvalidImageName", "RunContainerError",
}


def _age(ts) -> str:
    if not ts:
        return ""
    secs = int((datetime.now(timezone.utc) - ts).total_seconds())
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= n:
            return f"{secs // n}{unit}"
    return f"{secs}s"


class ClusterSource(ContextSource):
    def __init__(self, namespaces: list[str], log_tail: int = 80):
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()
        self.custom = client.CustomObjectsApi()
        self.namespaces = namespaces
        self.log_tail = log_tail

    # ---------- incident discovery ----------
    def _pod_reason(self, pod) -> str | None:
        st = pod.status
        if st.phase == "Pending":
            for c in st.conditions or []:
                if c.type == "PodScheduled" and c.status == "False":
                    return c.reason or "Unschedulable"
        for cs in (st.container_statuses or []) + (st.init_container_statuses or []):
            if cs.last_state.terminated and cs.last_state.terminated.reason == "OOMKilled" and not cs.ready:
                return "OOMKilled"
            if cs.state.waiting and cs.state.waiting.reason in BAD_WAITING:
                return cs.state.waiting.reason
            if cs.state.running and not cs.ready:
                return "NotReady"
        if st.phase in ("Pending", "Failed", "Unknown"):
            return st.phase
        return None

    SYSTEM_NS = {"kube-system", "kube-public", "kube-node-lease", "local-path-storage", "firstcall-system"}

    def _namespaces(self) -> list[str]:
        if "*" not in self.namespaces:
            return self.namespaces
        return [n.metadata.name for n in self.core.list_namespace().items if n.metadata.name not in self.SYSTEM_NS]

    def list_incidents(self) -> list[Incident]:
        items: list[Incident] = []
        for ns in self._namespaces():
            for pod in self.core.list_namespaced_pod(ns).items:
                reason = self._pod_reason(pod)
                if not reason:
                    continue
                restarts = sum(cs.restart_count for cs in pod.status.container_statuses or [])
                items.append(Incident(namespace=ns, pod=pod.metadata.name, reason=reason,
                                      restarts=restarts, age=_age(pod.metadata.creation_timestamp),
                                      workload=self._owner(pod) or f"pod/{pod.metadata.name}"))
            items.extend(self._service_incidents(ns))
        best: dict[str, Incident] = {}
        for i in items:
            if i.fingerprint not in best or i.restarts > best[i.fingerprint].restarts:
                best[i.fingerprint] = i
        return list(best.values())

    def _service_incidents(self, ns: str) -> list[Incident]:
        """Healthy pods but a Service with zero endpoints is an incident too."""
        out = []
        for svc in self.core.list_namespaced_service(ns).items:
            if not svc.spec.selector:
                continue
            try:
                ep = self.core.read_namespaced_endpoints(svc.metadata.name, ns)
            except ApiException:
                continue
            if any(s.addresses or s.not_ready_addresses for s in (ep.subsets or [])):
                continue  # has pods (maybe not ready): the pod-level incident covers it
            sel = svc.spec.selector
            matching = [p for p in self.core.list_namespaced_pod(ns).items
                        if all((p.metadata.labels or {}).get(k) == v for k, v in sel.items())]
            if not matching:
                out.append(Incident(namespace=ns, pod=f"svc/{svc.metadata.name}",
                                    reason="NoEndpoints", age=_age(svc.metadata.creation_timestamp),
                                    workload=f"service/{svc.metadata.name}"))
        return out

    # ---------- snapshot ----------
    def snapshot(self, namespace: str, pod: str) -> Snapshot:
        if pod.startswith("svc/"):
            return self._service_snapshot(namespace, pod[4:])
        p = self.core.read_namespaced_pod(pod, namespace)
        spec = p.spec
        summary = {
            "node": spec.node_name,
            "service_account": spec.service_account_name,
            "labels": p.metadata.labels or {},
            "containers": [
                {
                    "name": c.name,
                    "image": c.image,
                    "command": c.command,
                    "args": c.args,
                    "ports": [pt.container_port for pt in c.ports or []],
                    "env_names": [e.name for e in c.env or []],  # names only, never values
                    "env_from": [
                        (ef.config_map_ref and f"configMap/{ef.config_map_ref.name}")
                        or (ef.secret_ref and f"secret/{ef.secret_ref.name}")
                        for ef in c.env_from or []
                    ],
                    "resources": c.resources.to_dict() if c.resources else {},
                    "readiness_probe": c.readiness_probe.to_dict() if c.readiness_probe else None,
                    "liveness_probe": c.liveness_probe.to_dict() if c.liveness_probe else None,
                }
                for c in spec.containers
            ],
            "volumes": [
                {k: v for k, v in vol.to_dict().items() if v is not None} for vol in spec.volumes or []
            ],
            "conditions": [
                {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
                for c in p.status.conditions or []
            ],
        }
        statuses = []
        for cs in p.status.container_statuses or []:
            d = {"name": cs.name, "ready": cs.ready, "restart_count": cs.restart_count}
            if cs.state.waiting:
                d.update(state="waiting", reason=cs.state.waiting.reason, message=cs.state.waiting.message)
            elif cs.state.terminated:
                d.update(state="terminated", reason=cs.state.terminated.reason,
                         exit_code=cs.state.terminated.exit_code)
            else:
                d["state"] = "running"
            if cs.last_state.terminated:
                t = cs.last_state.terminated
                d["last_terminated"] = {"reason": t.reason, "exit_code": t.exit_code}
            statuses.append(d)

        return Snapshot(
            namespace=namespace,
            pod=pod,
            phase=p.status.phase,
            workload=self._owner(p),
            pod_summary=summary,
            container_statuses=statuses,
            events=self._events(namespace, "Pod", pod),
            logs=self._logs(namespace, pod, [c.name for c in spec.containers], statuses),
            metrics=self._metrics(namespace, pod),
            related={**self._related(namespace, p), **self._changes_for(namespace, self._owner(p))},
        )

    # ---------- what changed? (the first question on every call) ----------
    def _changes_for(self, ns: str, workload: str | None) -> dict:
        if not workload or not workload.startswith("deployment/"):
            return {}
        try:
            return {"recent_changes": self.recent_changes(ns, workload.split("/", 1)[1])}
        except ApiException:
            return {}

    def recent_changes(self, ns: str, name: str) -> dict:
        dep = self.apps.read_namespaced_deployment(name, ns)
        rss = [rs for rs in self.apps.list_namespaced_replica_set(ns).items
               if any(o.uid == dep.metadata.uid for o in rs.metadata.owner_references or [])]
        rev = lambda rs: int((rs.metadata.annotations or {}).get("deployment.kubernetes.io/revision", 0))  # noqa: E731
        rss.sort(key=rev)
        if not rss:
            return {}
        history = [{"revision": rev(r), "age": _age(r.metadata.creation_timestamp),
                    "change_cause": (r.metadata.annotations or {}).get("kubernetes.io/change-cause", "")}
                   for r in rss[-5:]]
        cur = rss[-1]
        out = {"deployment": name, "current_revision": rev(cur),
               "current_deployed": _age(cur.metadata.creation_timestamp) + " ago", "history": history}
        if len(rss) < 2:
            out["note"] = "first rollout: no previous revision to compare"
            return out
        prev = rss[-2]
        out["previous_revision"] = rev(prev)
        out["diff"] = self._template_diff(prev.spec.template, cur.spec.template)
        return out

    def _template_diff(self, old, new) -> list[str]:
        ser = client.ApiClient().sanitize_for_serialization
        o, n = ser(old).get("spec", {}), ser(new).get("spec", {})
        diffs: list[str] = []
        oc = {c["name"]: c for c in o.get("containers", [])}
        for c in n.get("containers", []):
            name, before = c["name"], oc.get(c["name"])
            if not before:
                diffs.append(f"container {name} added")
                continue
            if before.get("image") != c.get("image"):
                diffs.append(f"{name}: image {before.get('image')} -> {c.get('image')}")
            be = {e["name"]: e for e in before.get("env", [])}
            ne = {e["name"]: e for e in c.get("env", [])}
            for k in be.keys() - ne.keys():
                diffs.append(f"{name}: env var {k} REMOVED")
            for k in ne.keys() - be.keys():
                diffs.append(f"{name}: env var {k} added")
            for k in be.keys() & ne.keys():
                if be[k] != ne[k]:
                    diffs.append(f"{name}: env var {k} value changed")
            if before.get("envFrom") != c.get("envFrom"):
                diffs.append(f"{name}: envFrom {before.get('envFrom')} -> {c.get('envFrom')}")
            if before.get("resources") != c.get("resources"):
                diffs.append(f"{name}: resources {before.get('resources')} -> {c.get('resources')}")
            for probe in ("readinessProbe", "livenessProbe", "startupProbe"):
                if before.get(probe) != c.get(probe):
                    diffs.append(f"{name}: {probe} {before.get(probe)} -> {c.get(probe)}")
            for f in ("command", "args"):
                if before.get(f) != c.get(f):
                    old_l = "\n".join(before.get(f) or []).splitlines()
                    new_l = "\n".join(c.get(f) or []).splitlines()
                    removed = [x.strip() for x in old_l if x not in new_l][:4]
                    added = [x.strip() for x in new_l if x not in old_l][:4]
                    diffs.append(f"{name}: {f} changed; removed {removed} added {added}")
        for c in oc.keys() - {c["name"] for c in n.get("containers", [])}:
            diffs.append(f"container {c} removed")
        if o.get("volumes") != n.get("volumes"):
            diffs.append("volumes changed")
        return diffs or ["pod template identical (scale or metadata-only change)"]

    def _owner(self, p) -> str | None:
        for ref in p.metadata.owner_references or []:
            if ref.kind == "ReplicaSet":
                return "deployment/" + ref.name.rsplit("-", 1)[0]
            return f"{ref.kind.lower()}/{ref.name}"
        return None

    def _events(self, ns: str, kind: str, name: str) -> list[dict]:
        evs = self.core.list_namespaced_event(
            ns, field_selector=f"involvedObject.kind={kind},involvedObject.name={name}"
        ).items
        evs.sort(key=lambda e: e.last_timestamp or e.event_time or e.metadata.creation_timestamp)
        return [
            {"type": e.type, "reason": e.reason, "message": e.message, "count": e.count}
            for e in evs[-25:]
        ]

    def _logs(self, ns, pod, containers, statuses) -> dict[str, str]:
        out = {}
        for c in containers:
            chunks = []
            prev = any(s["name"] == c and s.get("last_terminated") for s in statuses)
            for previous in ([True] if prev else []) + [False]:
                try:
                    # _preload_content=False: some client versions return str(b"...") otherwise
                    resp = self.core.read_namespaced_pod_log(
                        pod, ns, container=c, tail_lines=self.log_tail, previous=previous,
                        _preload_content=False,
                    )
                    txt = resp.data.decode("utf-8", "replace")
                    if txt:
                        chunks.append(("--- previous ---\n" if previous else "--- current ---\n") + txt)
                except ApiException:
                    pass
            out[c] = "\n".join(chunks)
        return out

    def _metrics(self, ns, pod) -> dict:
        try:
            m = self.custom.get_namespaced_custom_object("metrics.k8s.io", "v1beta1", ns, "pods", pod)
            return {c["name"]: c["usage"] for c in m.get("containers", [])}
        except ApiException:
            return {}

    def _related(self, ns, p) -> dict:
        rel: dict = {}
        labels = p.metadata.labels or {}
        svcs = []
        for s in self.core.list_namespaced_service(ns).items:
            sel = s.spec.selector or {}
            if sel and all(labels.get(k) == v for k, v in sel.items()):
                svcs.append({"name": s.metadata.name, "selector": sel,
                             "ports": [pt.to_dict() for pt in s.spec.ports or []]})
        rel["services"] = svcs
        pvcs = []
        for vol in p.spec.volumes or []:
            if vol.persistent_volume_claim:
                name = vol.persistent_volume_claim.claim_name
                try:
                    pvc = self.core.read_namespaced_persistent_volume_claim(name, ns)
                    pvcs.append({"name": name, "phase": pvc.status.phase,
                                 "storage_class": pvc.spec.storage_class_name,
                                 "events": self._events(ns, "PersistentVolumeClaim", name)})
                except ApiException as e:
                    pvcs.append({"name": name, "error": e.reason})
        rel["pvcs"] = pvcs
        if p.status.phase == "Pending":
            rel["nodes"] = [
                {"name": n.metadata.name, "allocatable": n.status.allocatable,
                 "taints": [t.to_dict() for t in n.spec.taints or []]}
                for n in self.core.list_node().items
            ]
        return rel

    def _service_snapshot(self, ns: str, name: str) -> Snapshot:
        svc = self.core.read_namespaced_service(name, ns)
        pods = self.core.list_namespaced_pod(ns).items
        return Snapshot(
            namespace=ns,
            pod=f"svc/{name}",
            phase="NoEndpoints",
            workload=f"service/{name}",
            pod_summary={"service": {"selector": svc.spec.selector,
                                     "ports": [pt.to_dict() for pt in svc.spec.ports or []]}},
            events=self._events(ns, "Service", name),
            related={"pods_in_namespace": [
                {"name": x.metadata.name, "labels": x.metadata.labels, "phase": x.status.phase}
                for x in pods
            ]},
        )

    # ---------- write-back ----------
    _KINDS = {
        "deployment": ("Deployment", "apps/v1", "read_namespaced_deployment", "apps"),
        "statefulset": ("StatefulSet", "apps/v1", "read_namespaced_stateful_set", "apps"),
        "daemonset": ("DaemonSet", "apps/v1", "read_namespaced_daemon_set", "apps"),
        "service": ("Service", "v1", "read_namespaced_service", "core"),
        "pod": ("Pod", "v1", "read_namespaced_pod", "core"),
    }

    def record_event(self, namespace, workload, reason, message, warning=True) -> bool:
        """Post a core/v1 Event on the workload so `kubectl describe` and `kubectl events` show it."""
        if not workload or "/" not in workload:
            return False
        kind_key, name = workload.split("/", 1)
        if kind_key not in self._KINDS:
            return False
        kind, api_version, reader, group = self._KINDS[kind_key]
        api = self.apps if group == "apps" else self.core
        try:
            obj = getattr(api, reader)(name, namespace)
        except ApiException:
            return False
        now = datetime.now(timezone.utc)
        ev = client.CoreV1Event(
            metadata=client.V1ObjectMeta(name=f"{name}.firstcall.{uuid.uuid4().hex[:10]}", namespace=namespace),
            involved_object=client.V1ObjectReference(kind=kind, api_version=api_version, name=name,
                                                     namespace=namespace, uid=obj.metadata.uid),
            reason=reason, message=message[:1000], type="Warning" if warning else "Normal",
            source=client.V1EventSource(component="firstcall"),
            first_timestamp=now, last_timestamp=now, count=1,
        )
        try:
            self.core.create_namespaced_event(namespace, ev)
            return True
        except ApiException:
            return False  # e.g. RBAC without events/create: silently skip
