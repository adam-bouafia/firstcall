# FirstCall

**The first responder for Kubernetes incidents.** FirstCall watches your cluster. When a workload
breaks, it collects events, logs, metrics and **what changed in the last rollout**, removes secrets,
and asks an open-weight model on **Nebius Token Factory** for a diagnosis with evidence. It runs the
suggested read-only `kubectl` command to confirm the cause, proposes a one-click fix that a person
approves after a server dry run (e.g. `kubectl rollout undo`), and writes the result back where engineers already look: the
workload's Kubernetes Events, Prometheus metrics, Grafana, and Telegram or Slack (24/7, or only nights
and weekends). Then it tracks the incident until the cluster is healthy again.

Accel AI Innovate, 23 September 2026 · team *HaveYouTriedDeletingThePod*

```
Alertmanager ─┐
 15 s scan ───┴► detect ► collect (+ what changed) ► redact ► diagnose (Nebius) ► read-only cmd ► refine
                                                                                        │
     Kubernetes Events · Prometheus/Grafana · Telegram · UI ◄──────────────────────────┘
        ► human approves the proposed fix ► server dry run ► apply ► verified resolved
```

- **Step-by-step setup (start here):** [docs/local-setup.md](docs/local-setup.md)
- Why each piece of the stack is there: [docs/why-this-stack.md](docs/why-this-stack.md)
- How the cluster problems are generated: [docs/scenarios.md](docs/scenarios.md)
- Telegram, Slack and the alert modes: [docs/alerting.md](docs/alerting.md)
- Hackathon day plan, tools and pitch: [docs/hackathon-day.md](docs/hackathon-day.md)
- Submission page (build with `python scripts/build_submission.py`): [docs/submission.html](docs/submission.html)
- Architecture and every decision explained: [docs/architecture.md](docs/architecture.md)
- FirstCall vs k8sgpt: [docs/k8sgpt-comparison.md](docs/k8sgpt-comparison.md)
- Reading the benchmark (and its known artifacts): [docs/benchmark.md](docs/benchmark.md)
- Customer discovery script for the day: [docs/discovery-script.md](docs/discovery-script.md)
- Build board (open in any browser, works offline): [docs/board.html](docs/board.html)

## Quick start (Fedora, 16 GB laptop, no Docker)

```bash
make setup                # venv + npm, creates .env
# put NEBIUS_API_KEY in .env  (FIRSTCALL_DEFAULT_MODEL=qwen3-30b is the default)
make models               # check the Token Factory model IDs match backend/app/models.yaml
make cluster              # k3s on this machine + 8 broken scenarios (~90 s to break). NOT with sudo
make dev                  # API :8000 + UI :3000, watching the cluster
```

Open http://localhost:3000. Every broken workload appears as an incident within ~15 s,
diagnosed, with one read-only follow-up command already run. The **Live activity** panel at the
bottom (or `make activity` in a terminal) shows everything FirstCall does as it happens. Then:

```bash
kubectl -n firstcall-demo describe deploy payments-api   # the diagnosis is on the workload's events
# in the console: payments-api → Proposed fix → Preview (dry run) → Approve & apply  (a rollout undo)
make fix S=07-service-selector                           # or fix by hand, watch it resolve (make fix-all)
make reset                                               # break everything again
```

## The product shape: in-cluster with Helm, plus the CNCF stack

```bash
make monitoring           # kube-prometheus-stack, slimmed: Prometheus, Alertmanager -> FirstCall, Grafana :30301
make images-k3s           # build both images with podman and import them into k3s (or use GHCR images from CI)
make install              # helm install FirstCall: UI :30300, ServiceMonitor, alert rules, Grafana dashboard
make e2e API_URL=...      # the same end-to-end check CI runs
```

| piece | what it does here |
|---|---|
| **k3s** | the cluster on your laptop: one process, no Docker, metrics-server included |
| **Helm** (`charts/firstcall`) | installable product: read-only RBAC, Secret, PVC, ServiceMonitor, PrometheusRule, dashboard |
| **Prometheus** | scrapes `/metrics`: time to diagnosis, time to resolve, latency, tokens and cost per model |
| **Alertmanager** | fast pod alerts (1 min) → FirstCall webhook → scan now + alert attached to the incident |
| **Grafana** | FirstCall dashboard: cost per diagnosis per model, p50/p95 latency, confidence, redactions |
| **Kubernetes Events** | every diagnosis posted on the broken Deployment/Service (`kubectl describe`) |
| **Change correlation** | rollout history + pod-template diff of the latest revision ("env var DATABASE_URL REMOVED, 3 min ago") |
| **Approved remediation** | allow-listed fix → server dry run → a named human approves (console/Telegram) → applied with namespace-scoped RBAC → verified |
| **Tavily web search** | when the model is unsure, the sanitized error string is searched and the answer must cite the sources |
| **Voice alerts** | ElevenLabs: the night alert also arrives as a Telegram voice note |
| **Telegram** | alerts with Investigate / Correct / Wrong buttons, `/status` `/incidents` `/mode`; 24/7 or nights & weekends |
| **kind + GitHub Actions** | CI: tests, UI build, helm lint, full e2e on kind, images + chart to GHCR |
| **k8sgpt** | benchmark baseline: same cluster, same Nebius model, same ground truth |

## Models

| key | provider | use |
|---|---|---|
| `qwen3-30b` (default) | Nebius Token Factory | fast first pass, ≈ $0.0003 per diagnosis |
| `nemotron-nano` | Nebius | cheapest ($0.06 / $0.24 per M) |
| `qwen3-235b` | Nebius | hard cases |
| `gpt-oss-120b` | Nebius | reasoning comparison |
| `cascade` | Nebius | 30B first, 235B only when unsure |
| `groq-gpt-oss-120b` | Groq free tier | fallback (set `FIRSTCALL_AUTO_INVESTIGATE=0`) |
| `cerebras-gpt-oss-120b` | Cerebras trial | fallback |
| `local-*` | Ollama | only on machines with RAM to spare |

Anything else: `provider:model-id`, e.g. `FIRSTCALL_DEFAULT_MODEL=nebius:deepseek-ai/DeepSeek-V3-0324`.

## Evidence

```bash
make capture                         # record the live broken cluster into fixtures
make bench PROVIDER=nebius           # all Nebius models x 8 scenarios x 3 + rules floor
make bench-k8sgpt PROVIDER=nebius    # adds k8sgpt on the live cluster, same model
```
CI re-runs the benchmark on every change to the prompt or collector (`.github/workflows/bench.yml`,
needs the `NEBIUS_API_KEY` repository secret) and fails if the best score drops below 0.60.

## The 8 scenarios

| workload | what's broken | fix |
|---|---|---|
| payments-api | `DATABASE_URL` missing → CrashLoopBackOff; logs leak a fake Stripe key | `set env` |
| checkout-web | image tag `busybox:1.63` (meant 1.36) | `set image` |
| report-worker | unbounded load under a 32Mi limit → OOMKilled | stream in chunks |
| ml-batch | requests 64 CPUs | `set resources` |
| catalog-api | readiness probe on :8080, app serves :80 | probe port 80 |
| notify-svc | `envFrom` ConfigMap `notify-config` missing | create it |
| orders | Service selects `app=orders`, pods are `app=orders-api` → 0 endpoints | fix selector |
| ledger-db | PVC wants StorageClass `fast-ssd` (doesn't exist) | recreate PVC |

## API

| | path | |
|---|---|---|
| GET | `/health`, `/api/stats`, `/api/models`, `/metrics` | |
| GET | `/api/incidents`, `/api/incidents/{id}` | list, detail + timeline |
| POST | `/api/incidents/{id}/diagnose` `{model?}` | re-diagnose |
| POST | `/api/incidents/{id}/investigate` `{model?}` | run the read-only next command, refine |
| POST | `/api/incidents/{id}/feedback` `{value}` | correct / wrong |
| GET | `/api/incidents/{id}/snapshot` | exactly what the model sees (redacted) |
| POST | `/api/scan`, `/api/alertmanager` | scan now; Alertmanager webhook receiver |
| GET/POST | `/api/alerting` `{mode}` | alert mode: `always` or `offhours` |
| POST | `/api/incidents/{id}/remediation/preview` | server dry run of the proposed fix |
| POST | `/api/incidents/{id}/remediation/apply` `{hash, approver}` | apply after approval (hash from the preview) |
| GET | `/api/activity?since=` | live activity feed |
| POST | `/api/alerting/test` | test push to Telegram/Slack |
| POST | `/api/diagnose`, `/api/compare` | stateless: compare view and bench |

## Layout
```
backend/app/     engine (loop) · store (SQLite) · metrics · k8s/ (collector, events write-back, fixtures)
                 llm/ (router, prompt) · diagnose · investigate · redact · safety · notify · models.yaml
frontend/        Next.js 15 console; /fc/* proxies to the API (FIRSTCALL_API_URL, read at runtime)
charts/firstcall Helm chart (+ Grafana dashboard JSON)
deploy/monitoring kube-prometheus-stack values wired to FirstCall
bench/           scoring harness + k8sgpt baseline
scenarios/       manifests/ (break) · fixes/ (repair) · fixtures/ (recorded snapshots + ground truth)
scripts/         setup-k3s · setup-kind · e2e · capture · list_models
.github/         ci.yml (test, build, lint, e2e on kind, publish) · bench.yml (Nebius regression gate)
```
