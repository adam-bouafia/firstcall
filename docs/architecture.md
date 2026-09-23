# FirstCall architecture, and why it is built this way (v0.3)

## One sentence
FirstCall watches a Kubernetes cluster, and when a workload breaks it collects the facts an
on-call engineer would gather in the first 15 minutes, removes secrets, asks an open-weight
model for a diagnosis with evidence, runs the suggested **read-only** command to confirm it,
and tracks the incident until the cluster is healthy again.

## Orchestration (v0.3)

```
 GitHub ── ci.yml ──► tests · UI build · helm lint · e2e on kind (chart + images + 8 failures) · images + chart → GHCR
        └─ bench.yml ► Nebius benchmark on every prompt/collector change, fails below 0.60

 laptop (Fedora, no Docker)
 └─ k3s (one process: containerd, CoreDNS, local-path, metrics-server)
    ├─ firstcall-demo       8 broken workloads                     ◄── kubectl describe shows FirstCall Events
    ├─ firstcall-system     FirstCall (Helm): api + web (:30300), read-only SA + events/create
    │                         ├─► Nebius Token Factory (Qwen3 30B / 235B, gpt-oss, cascade)
    │                         ├─► Kubernetes Events on the broken workload
    │                         └─► /metrics
    └─ monitoring           kube-prometheus-stack (slim)
                              ├─ Prometheus ◄── ServiceMonitor(firstcall)   + PrometheusRule (fast pod alerts)
                              ├─ Alertmanager ──webhook──► FirstCall /api/alertmanager
                              └─ Grafana (:30301) ◄── FirstCall dashboard ConfigMap
```

`make dev` runs the same API and UI on the host instead, for a fast edit-reload loop.

## The loop

```
          ┌──────────────── every 15 s (or instantly on an Alertmanager webhook) ───────────────┐
          ▼                                                                                      │
  1 DETECT   pods in CrashLoopBackOff / ImagePull* / CreateContainerConfigError / OOMKilled /    │
             Pending-Unschedulable / Running-but-NotReady, Services with 0 endpoints            │
          │  one incident per workload (namespace/deployment), not per pod                     │
          ▼                                                                                      │
  2 COLLECT  pod spec summary (env var NAMES only), container states + last termination,        │
             events (pod + PVC), logs current + --previous, metrics.k8s.io, matching Services,  │
             PVCs, nodes when Pending                          read-only ServiceAccount          │
          ▼                                                                                      │
  3 REDACT   JWTs, private keys, cloud keys, bearer tokens, URL credentials, *secret*=value,    │
             emails, IPs. Count shown to the user.                                              │
          ▼                                                                                      │
  4 DIAGNOSE one call to an open-weight model through an OpenAI-compatible API                  │
             (Token Factory / Ollama / OpenRouter / vLLM). Strict JSON: summary, category,      │
             root cause, confidence, evidence quotes, next command, fix. Optional cascade.      │
          ▼                                                                                      │
  5 GUARD    tolerant JSON parse, category validation, command classifier:                      │
             read-only allow-list, no pipes/chaining/redirection                                │
          ▼                                                                                      │
  6 INVESTIGATE  if the next command is read-only: run it (auto: 1 step, or on click),          │
             redact the output, send it back to the model, get a sharper diagnosis              │
          ▼                                                                                      │
  7 NOTIFY   timeline in the UI, optional Slack message                                         │
          ▼                                                                                      │
  8 RESOLVE  a human applies the fix; after 2 healthy scans in a row the incident closes ───────┘
             and time-to-resolve is recorded
```

## Components

| component | file | job |
|---|---|---|
| Collector | `backend/app/k8s/cluster.py` | read-only view of the cluster; one `Snapshot` per incident |
| Fixtures | `backend/app/k8s/fixtures.py` | snapshots recorded from a real cluster: offline demo + benchmark |
| Redactor | `backend/app/redact.py` | scrub before any byte leaves the process |
| Prompt | `backend/app/llm/prompts.py` | system contract + compact JSON context (nulls pruned) |
| Model router | `backend/app/llm/client.py`, `models.yaml` | one OpenAI-compatible client, many providers |
| Diagnose | `backend/app/diagnose.py` | pipeline + cascade routing |
| Investigate | `backend/app/investigate.py` | run allow-listed kubectl, feed output back |
| Safety | `backend/app/safety.py` | read-only verb allow-list |
| Engine | `backend/app/engine.py` | watcher thread + worker pool: the product loop |
| Store | `backend/app/store.py` | SQLite: incidents + timeline + stats + settings |
| Change correlation | `ClusterSource.recent_changes` | rollout history + pod-template diff |
| Remediation | `backend/app/remediate.py`, `safety.remediation_check` | allow-list, dry run, apply after approval |
| Activity feed | `backend/app/activity.py` | live feed for the console and `make activity` |
| Alerting | `backend/app/alerting.py`, `notify.py`, `telegram.py` | modes and schedule, Telegram bot (push, commands, buttons), Slack |
| API | `backend/app/main.py` | FastAPI; also the Alertmanager webhook |
| UI | `frontend/` | Next.js console: incidents, diagnosis, run & refine, compare, feedback |
| Metrics | `backend/app/metrics.py` | Prometheus counters/histograms: latency, tokens, cost per model, time to diagnosis/resolve |
| Events write-back | `ClusterSource.record_event` | posts each diagnosis as a Kubernetes Event on the workload |
| Bench | `bench/run_bench.py`, `bench/k8sgpt_baseline.py` | scores models (and k8sgpt) against ground truth |
| Chart | `charts/firstcall` | Helm: RBAC, Secret, PVC, api + web, ServiceMonitor, PrometheusRule, dashboard |
| Monitoring | `deploy/monitoring/kube-prometheus-stack.yaml` | Prometheus, Alertmanager wired to FirstCall, Grafana |
| CI/CD | `.github/workflows/` | tests, build, lint, kind e2e, GHCR images + OCI chart, benchmark gate |

## Decisions (and what was rejected)

**1. Open-weight models behind an OpenAI-compatible API.**
Logs contain customer data and secrets. Teams in the EU often cannot paste them into a closed
chatbot. Open weights mean the same model can run on Token Factory today and inside the
customer's own VPC tomorrow. *Rejected:* a closed frontier API (compliance blocker for the exact
customer we target); requiring self-hosted GPUs from day one (cost and setup time).

**2. Provider-agnostic registry.** Develop for free on Ollama on a laptop, demo on Token
Factory, compare on OpenRouter: it is one line in `.env`, no code change.

**3. Curated snapshot + one call, not a free-roaming agent.** The collector already gathers
what a senior engineer would look at first, so the model reasons over about 1,000 tokens instead
of driving tools. Cost and latency are predictable, and even 7B local models give usable answers.
*Rejected:* a tool-calling agent loop as the default (small models call tools unreliably, cost
grows without a bound, and the result is harder to audit).

**4. Investigation is read-only and human-approved.** The model never touches the cluster. The
backend runs only allow-listed verbs (`get`, `describe`, `logs`, `top`, `events`, `rollout
status`, `auth can-i`), no pipes or chaining, output redacted and capped. Fixes are suggested,
never applied. RBAC has no `secrets`, no write verbs and no `pods/exec`, so a bug in the
classifier still cannot write.

**5. Redaction before inference, even with a trusted provider.** Defense in depth, and the
counter makes the privacy claim visible in the demo.

**6. Strict JSON contract, tolerant parser.** Small and reasoning models add `<think>` blocks
or code fences; the parser strips them, clamps confidence, maps unknown categories to `Other`.
Ollama models also get JSON mode.

**7. One incident per workload, closed with hysteresis.** Pods get new names on every restart
and rollout, deployments do not. An incident resolves only after 2 healthy scans in a row, so
a pod that flaps does not open and close ten incidents.

**8. Polling plus a webhook, not watch streams.** A 15-second scan is simple and survives
API-server restarts. Alertmanager can push `/api/alertmanager` for an instant scan.
*Rejected:* informers and watches (more moving parts than one day allows).

**9. SQLite.** One file, zero operations, survives restarts, enough for one cluster. Single
replica by design; Postgres is the upgrade path.

**10. Cascade routing.** Most incidents are common failure classes a small MoE model gets
right. Pay big-model prices only when the small one is unsure (confidence < 0.75 or category
`Other`). The benchmark shows whether that holds.

**11. Fixtures recorded from a real cluster.** `make capture` snapshots the live broken cluster,
so the benchmark is reproducible and the demo works without Wi-Fi.

**12. k3s on the laptop, kind in CI.** kind runs every node as a container inside Docker (on
Fedora with Docker Desktop: inside a VM too), which took the laptop down together with a local
model. k3s is one process on the host (≈0.6 GB), CNCF-certified, and ships metrics-server,
storage and CoreDNS. kind stays where it shines: disposable, upstream-exact clusters in CI.
*Rejected:* Docker Desktop + 3-node kind locally (too heavy for 16 GB); minikube (another VM).

**13. Helm chart as the product boundary.** `helm install` is how platform teams adopt tools.
The chart carries the security posture (read-only ClusterRole, non-root, read-only root FS,
dropped capabilities) so nobody has to hand-edit it.

**14. Meet teams in the stack they already run.** Alertmanager can push to FirstCall, Prometheus
scrapes FirstCall, Grafana shows the model economics, and every diagnosis lands as a Kubernetes
Event. The only write permission FirstCall has is `events/create`, and it can be turned off
(`writeEvents=false`).
*Rejected:* a CRD/operator for results (more to install and more RBAC, for the same visibility).

**15. Fast alert rules for the demo namespace.** kube-prometheus-stack's `KubePodCrashLooping`
waits 15 minutes. The chart ships 1-2 minute rules (`FirstCallPodWaiting`, `…OOMKilled`,
`…Pending`) so the Alertmanager path fires during a demo.

**16. CI proves the claims.** The e2e job builds the images, installs the chart on kind, breaks
8 workloads and asserts detection, diagnosis, Events, Alertmanager handling and resolution. The
benchmark workflow re-runs on Nebius whenever the prompt, collector or models change, and fails
the build if quality drops.

**18. Telegram pushes with two alert modes.** Night incidents are handled from a phone, and Telegram
bots are free, need no admin, and support buttons (Investigate runs the read-only next command). One
push per incident, sent after the follow-up command so it carries the refined answer. Mode `always`
(default) or `offhours` (weekdays 17:00–09:00 + weekends), switchable at runtime and persisted. Only
pushes are gated; console, Events and metrics never are. Only allow-listed chat ids get data.
Failed diagnoses (model outage, quota) are retried every 60 s.

**19. "What changed?" is collected, not guessed.** Most incidents follow a change. For Deployments,
FirstCall reads the rollout history (ReplicaSet revisions, change-cause annotations) and diffs the pod
template of the current vs previous revision: image, env var names added/removed/changed (never
values), envFrom, resources, probes, command/args, volumes. The model gets that as evidence and fills
`suspected_change`. The demo scenarios deploy a healthy baseline first, so the history is real.

**20. Fixes need a human, a dry run, and a narrow allow-list.** The model may propose one
`remediation_command`. It is applied only if (1) it passes the allow-list (rollout undo, set image, set
resources, scale, patch; Deployment/Service; watched namespace; no chaining), (2) a server-side dry run
succeeds, (3) a named person approves that exact command (hash-checked against the preview), and
(4) in-cluster, a namespace-scoped RoleBinding grants only those verbs (`remediation.enabled`). Every
step is in the timeline and posted as a Kubernetes Event; the normal resolve loop verifies the fix.
*Rejected:* autonomous remediation (trust), a free-form "run any command" button (blast radius).

**21. A live activity feed.** Background work is invisible unless you show it. Every scan, snapshot,
model call (latency, tokens, cost), command, Event, push and fix goes to an in-memory feed:
console panel, `make activity`, `/api/activity`.

**17. k8sgpt as the baseline.** The first question people ask is "how is this different from
k8sgpt?". The benchmark answers it with numbers: same cluster, same model, same ground truth
([k8sgpt-comparison.md](k8sgpt-comparison.md)).

## Trust boundary

```
 your infrastructure                                  │  model provider (your choice)
 ─────────────────────────────────────────────────────┼──────────────────────────────
 k8s API ─► FirstCall (read-only SA) ─► redactor ─────┼─► open-weight model
                ▲                                      │   (Token Factory, or Ollama
                └── kubectl allow-list (read-only)     │    on the same machine = nothing leaves)
```

With Ollama or an in-VPC vLLM, the dashed line moves inside your infrastructure: nothing
leaves at all.

## Why now
- Open-weight models (Qwen3, gpt-oss, Llama, Gemma) are now good enough at structured
  operations reasoning to be useful on call.
- Token Factory serves them behind a standard API at cents per thousand incidents, so running
  on every alert is affordable.
- The same weights can be self-hosted later, which closed models cannot offer.

## Limits
- Regex redaction can miss novel secret formats; env var values are never collected, which is
  the stronger guarantee.
- One cluster per backend; single replica.
- The 12 scenarios are deliberate failures written by the author, not a real incident history, and the
  ground truth is authored too: treat the benchmark as a regression gate, not proof of accuracy.
- Logs are tailed (80 lines per container), so a root cause buried earlier can be missed.

## Roadmap
Prometheus range queries (restart rate, memory trend) in the snapshot · OpenTelemetry traces per
diagnosis (Jaeger) · Argo CD / Flux GitOps demo · Headlamp plugin · multi-cluster · fine-tune on
a team's resolved incidents (the feedback buttons already collect labels) · Postgres store ·
contribute the read-only investigation loop upstream to k8sgpt.
