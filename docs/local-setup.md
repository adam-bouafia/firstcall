# Local setup, step by step (Fedora, 16 GB)

Goal: a real Kubernetes cluster on your laptop with 8 deliberately broken apps, FirstCall
watching it, diagnoses coming from Nebius Token Factory, alerts on Telegram.

What runs where:

| where | what | RAM |
|---|---|---|
| your laptop | k3s (Kubernetes) + the 8 broken apps | ≈ 0.7 GB |
| your laptop | FirstCall API (Python) + console (Next.js) | ≈ 0.4 GB (dev mode) |
| Nebius | the language model | 0 locally |
| optional, laptop | Prometheus + Alertmanager + Grafana | ≈ 1–1.3 GB |

Every step below has **run**, **you should see**, and **check**. Don't move on until the check passes.

---

## Step 0 · Free the laptop

Docker Desktop runs a whole VM, and Ollama keeps a model in RAM. FirstCall needs neither now.

**Run**
```bash
# Docker Desktop: quit it from its tray icon, or
systemctl --user stop docker-desktop 2>/dev/null; systemctl --user disable docker-desktop 2>/dev/null
# Ollama
sudo systemctl disable --now ollama 2>/dev/null
# the kind cluster from the first attempt (if kind is installed)
kind delete cluster --name firstcall 2>/dev/null
free -h
```
**Check** `free -h` shows at least ~8 GB available.

---

## Step 1 · Tools

You already have Python, Node and kubectl (`/usr/sbin/kubectl` from Fedora). Add helm (and podman for images later):

**Run**
```bash
sudo dnf install -y helm podman
python3 --version && node --version && kubectl version --client && helm version --short
```
**You should see** Python ≥ 3.11, Node ≥ 20, a kubectl version, `v3.x` for helm.

---

## Step 2 · Project dependencies

**Run** (in the project folder, as your normal user)
```bash
make setup
```
**You should see** pip finishing quietly, then `npm ci` adding ~50 packages and **0 vulnerabilities**.

Notes on the messages you saw before:
- `A new release of pip is available`: harmless.
- `sharp ... install scripts blocked`: harmless. sharp is Next.js' optional image optimizer; FirstCall doesn't use it.
- `npm audit fix --force` must never be run in the project root (there is no package.json there) and
  shouldn't be run with `--force` at all: it jumps Next.js a major version. The versions are now pinned
  to patched releases, so there is nothing to fix.

**Check** `ls .venv frontend/node_modules >/dev/null && echo ok`

---

## Step 3 · Nebius key and models

**Run**
```bash
nano .env          # set NEBIUS_API_KEY=...   (FIRSTCALL_DEFAULT_MODEL=qwen3-30b is already set)
make models
```
**You should see** a list that includes `Qwen/Qwen3-30B-A3B-Instruct-2507`, `Qwen/Qwen3-235B-A22B-Instruct-2507`,
`openai/gpt-oss-120b`, `google/gemma-3-27b-it`, `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`: these are the IDs in
`backend/app/models.yaml`. Your list from 22 Sep had all of them.

**Is the €1 trial enough?** Yes, for development and several benchmark runs, if you stay on the small models.

| action | tokens | cost on qwen3-30b ($0.10 / $0.30 per M) |
|---|---|---|
| one diagnosis | ≈ 1.6k in + 0.35k out | ≈ $0.0003 |
| one incident with one follow-up command | ≈ 4.8k in + 0.7k out | ≈ $0.0007 |
| `make reset` (8 incidents) | | ≈ $0.006 |
| `make bench PROVIDER=nebius` (5 models × 8 × 3) | | ≈ $0.05 |

So €1 ≈ 150 full resets, or ~20 benchmark runs. Nemotron Nano is even cheaper ($0.06 / $0.24).
What burns credit: the big models (DeepSeek-V4-Pro, Kimi-K3, Nemotron Ultra 550B, Qwen3.5-397B) cost
10–30× more per call, and `make bench -r 3` with all of them. Prices here are the listed Token Factory
prices at the time of writing; the billing page in the Nebius console shows your real spend.

**Check**
```bash
source .venv/bin/activate && python3 -c "
import sys; sys.path.insert(0,'backend')
from app.llm.client import OpenAICompatClient
r = OpenAICompatClient().chat('Reply with the word ok.', 'ping', 'qwen3-30b')
print(r.text, r.prompt_tokens, r.completion_tokens, f'\${r.cost_usd:.6f}')"
```
You should see `ok` and a cost around `$0.00000x`.

---

## Step 4 · The cluster (k3s)

Run as your **normal user**, not with `sudo make cluster`: the script asks for sudo itself, and with
sudo the kubeconfig ends up in root's home.

**If a previous attempt failed** (your log: `Job for k3s.service failed`), first look at why:
```bash
sudo journalctl -u k3s -n 60 --no-pager | grep -v level=info | tail -30
```
The usual causes and fixes:

| log line contains | cause | fix |
|---|---|---|
| `server` / `token` / `failed to get CA certs` / `https://<ip>:6443` | leftover `/etc/rancher/k3s/config.yaml` from the old two-PC cluster makes k3s try to join node-1 | `sudo mv /etc/rancher/k3s/config.yaml /etc/rancher/k3s/config.yaml.old` |
| `bootstrap data already found and encrypted with different token` | old cluster data | `sudo /usr/local/bin/k3s-uninstall.sh` then run `make cluster` again (wipes the old cluster) |
| `address already in use` `:6443` or `:10250` | another process holds the API port: often a stray k3s from an earlier attempt, or minikube/another kubelet | `sudo ss -tulpn \| grep :6443` shows the process. If it's an old `k3s`/`kube-apiserver`: `sudo kill <PID>` (then `-9` if needed), `sudo systemctl restart k3s`. If it's something you use (minikube, a VM), stop that instead |
| `unknown flag` / `invalid argument` | a flag in `/etc/systemd/system/k3s.service` | `sudo /usr/local/bin/k3s-uninstall.sh`, re-run |
| `avc: denied` / SELinux | SELinux policy | `sudo setenforce 0`, retry; if that fixes it, keep SELinux and report the line |

The new `make cluster` detects the first three automatically and prints the log if k3s still fails.

**Run**
```bash
make cluster
```
**You should see** `== preflight`, possibly `firewalld: trusting the k3s ... networks`, `waiting for k3s`,
then a status line every 5 s while the node starts (`[  5s] utopia NotReady ...` → `Ready`),
`coredns successfully rolled out`, the healthy baseline rolling out, then the 8 broken changes.
If the node isn't Ready after 3 minutes the script stops and prints why (see *node never becomes Ready* below).

**Check**
```bash
kubectl get nodes                          # Utopia   Ready   control-plane,master
kubectl get pods -n firstcall-demo -w      # wait ~90 s, Ctrl-C when you see:
```
```
catalog-api-...    0/1  Running
checkout-web-...   0/1  ImagePullBackOff
ledger-db-...      0/1  Pending
ml-batch-...       0/1  Pending
notify-svc-...     0/1  CreateContainerConfigError
orders-...         1/1  Running          (x2: healthy pods, broken Service)
payments-api-...   0/1  CrashLoopBackOff
report-worker-...  0/1  OOMKilled / CrashLoopBackOff
```
How these problems are made (and how to add your own): [scenarios.md](scenarios.md).

---

## Seeing what happens in the background (keep these open)

| what | command |
|---|---|
| what FirstCall is doing (scan, collect, model calls, commands, alerts, fixes) | console → **Live activity** panel, or `make activity` in a terminal |
| the demo namespace: pods + latest events, refreshed every 2 s | `make watch` |
| the whole cluster, interactive (recommended) | [k9s](https://k9scli.io): `curl -sL https://github.com/derailed/k9s/releases/latest/download/k9s_Linux_amd64.tar.gz \| tar xz k9s && sudo install k9s /usr/local/bin/`, then `k9s -n firstcall-demo` |
| k3s itself | `sudo journalctl -u k3s -f` |
| the FirstCall API log | the `make dev` terminal |

A good demo layout: console on the left, `make activity` and `k9s` stacked on the right.

---

## Step 5 · Run FirstCall

**Run**
```bash
make dev
```
Open http://localhost:3000.

**You should see**, within ~15 s: the header says `watching firstcall-demo`, the list fills with 8
incidents, each gets a diagnosis within a few seconds (Nebius), and the timeline of each shows a
`kubectl` command FirstCall ran itself.

**Check**
```bash
kubectl -n firstcall-demo describe deploy payments-api | tail -5     # FirstCallDiagnosis events
curl -s localhost:8000/api/stats                                      # "open":8, "diagnoses":16
```

---

## Step 6 · Telegram alerts (optional, 5 minutes)

Full guide: [alerting.md](alerting.md). Short version:
1. In Telegram, open **@BotFather** → `/newbot` → copy the token into `.env` as `TELEGRAM_BOT_TOKEN`.
2. Restart `make dev`, open your new bot, send `/start`. It replies with your chat id.
3. Put it in `.env` as `TELEGRAM_CHAT_IDS`, restart `make dev`.
4. `make alert-test` → a test message arrives. `make reset` → 8 alerts arrive, with buttons.

---

## Step 7 · Approve a fix, or fix by hand, and watch it resolve

`FIRSTCALL_REMEDIATION=approve` (the `.env` default) lets FirstCall *propose* one fix per incident.
In the console, open **payments-api** → **Proposed fix** (`kubectl rollout undo ...`) → **Preview**
(Kubernetes runs a server-side dry run) → **Approve & apply**. The incident resolves ~30 s later. On
Telegram the same flow is the **🛠 Fix…** button. Only rollout undo / set image / set resources / scale /
patch on a Deployment or Service in a watched namespace can ever be applied, and only after a person
approves the exact command.

By hand:

```bash
make fix S=07-service-selector      # one fix
make fix-all                        # all of them
make reset                          # break everything again
make break S=03-oom                 # break just one
```
**Check** in the console the incident moves to *resolved* ~30 s after the fix (two healthy scans).

---

## Step 8 · The product shape: console + Grafana + Telegram, all at once

This is the demo configuration: FirstCall runs **inside** the cluster, Prometheus scrapes it,
Grafana draws it, and the pod is the one talking to Telegram.

```bash
# stop `make dev` (Ctrl-C) first - see the note below, it is the only real constraint
make stack             # = images-k3s, then monitoring, then install, in that order
```
or the same three steps by hand:
```bash
make images-k3s        # builds both images, imports them into k3s' containerd (asks for sudo once)
make monitoring        # Prometheus + Alertmanager + Grafana, ~3-5 min the first time
make install           # FirstCall with Helm, keys from .env
```

| | |
|---|---|
| console | http://localhost:30300 |
| Grafana | http://localhost:30301 — dashboard **FirstCall** (anonymous view; admin/firstcall) |
| Telegram | the pod polls the bot and pushes every new diagnosis |
| Prometheus | scrapes `firstcall-api` via the ServiceMonitor the chart installs |

### Why `make dev` has to stop first

Nothing here conflicts except one thing: **Telegram allows exactly one poller per bot token.**
A second `getUpdates` loop gets `409 Conflict` and one of the two bots goes deaf. So:

| you want | do this |
|---|---|
| the demo setup: cluster runs everything | stop `make dev`, then `make install` |
| keep coding locally, cluster still pages you | `make install-nobot` — the pod pushes alerts but does not poll, your laptop keeps the buttons |
| both, properly | make a second bot with @BotFather and put its token in `.env` for local dev |

Grafana is unaffected either way, with one caveat worth knowing: Prometheus scrapes the
in-cluster Service, so the FirstCall panels only have data for the copy installed by Helm.
A local `make dev` run is invisible to Grafana — that is the reason to install, not a bug.

### If `make install` times out

`make install` runs `scripts/preflight-install.sh` first, and on failure
`scripts/why-not-ready.sh` prints the reason. The usual one is that the images never reached
containerd, so the pods sit in `ErrImagePull` and Helm's `--wait` expires with
`context deadline exceeded`. Fix: `make images-k3s`, then `make install` again.

`make images-k3s` asks for your sudo password once, up front. (It no longer pipes into
`sudo` — sudo cannot read a password when its stdin is a pipe, which is what produced
`sudo: timed out reading password`.)

---

## Stopping and cleaning up
| | |
|---|---|
| pause everything | `Ctrl-C` on `make dev`, then `make cluster-stop` |
| resume | `sudo systemctl start k3s`, `make dev` |
| remove monitoring | `make monitoring-down` |
| remove FirstCall from the cluster | `make uninstall` |
| remove k3s entirely | `sudo /usr/local/bin/k3s-uninstall.sh` |

## Troubleshooting
| symptom | cause / fix |
|---|---|
| `sudo: timed out reading password` during `make images-k3s` | old pipe into sudo; pull the latest Makefile — it runs `scripts/import-images.sh`, which takes the password first |
| `make install`: `context deadline exceeded` | the images are not in k3s' containerd → `make images-k3s`, then `make install`. `scripts/why-not-ready.sh` prints which pod and why |
| Telegram bot stops answering after `make install` | two pollers, one token (409 Conflict). Stop `make dev`, or use `make install-nobot` |
| Grafana FirstCall panels are empty | Prometheus scrapes the in-cluster Service only: run `make install`, and `make monitoring` before it so the ServiceMonitor CRD exists |
| red banner `scan failed ... 504` | the API server is starved: Docker Desktop / Ollama still running; check `free -h` |
| red banner `scan failed ... connection refused` | k3s stopped: `sudo systemctl start k3s` |
| `provider 'nebius' needs an API key` | `NEBIUS_API_KEY` missing in `.env` |
| diagnoses never arrive, API log shows 401/403 | wrong key, or the trial credit is used up (Nebius console → billing) |
| `unknown model` | use a key from `models.yaml` or `provider:model-id` |
| incidents stuck "waiting for diagnosis" after a model outage | FirstCall retries every 60 s automatically |
| Telegram: nothing arrives | chat id missing in `TELEGRAM_CHAT_IDS`; `make alert-test` shows the error |
| Telegram log `409 Conflict` | two FirstCalls polling the same bot (dev + Helm): stop one |
| pods Pending with `Insufficient cpu` for everything | your laptop is really full; only `ml-batch` should be Pending for CPU |
| DNS errors inside pods | firewalld blocking pod traffic: re-run `make cluster` (adds trusted zones) |
| `make cluster` stuck at "waiting for the node" (old script) | the node is NotReady or kubectl can't reach it: `kubectl get nodes` (shows the real error), `kubectl describe nodes \| sed -n '/Conditions/,/Addresses/p'`, `sudo journalctl -u k3s -n 50 --no-pager` |
| node never becomes Ready: `cni plugin not initialized` | flannel couldn't start: `sudo systemctl restart k3s`; if it persists, firewalld/nftables: re-run `make cluster` |
| node never becomes Ready: `x509` / `Unable to connect` | kubeconfig from an older k3s: `cp /etc/rancher/k3s/k3s.yaml ~/.kube/config` (the script does this) |
| Proposed fix says "remediation off" | set `FIRSTCALL_REMEDIATION=approve` in `.env`, restart |
| Preview fails with `forbidden` (in-cluster) | chart installed without `remediation.enabled=true`, or the namespace didn't exist at install time: `make install` again |
