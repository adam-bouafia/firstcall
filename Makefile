SHELL := /bin/bash
PY ?= python3
VENV := .venv
ACT := source $(VENV)/bin/activate &&
PROVIDER ?= nebius
S ?= 01-crashloop
API_URL ?= http://localhost:8000
NS ?= firstcall-system
TAG ?= 0.4.0
REGISTRY ?= ghcr.io/adam-bouafia
# podman on Fedora, docker elsewhere
OCI ?= $(shell command -v podman >/dev/null && echo podman || echo docker)

.PHONY: stack install-nobot bench-vs-closed activity watch break alert-test help setup env cluster cluster-kind cluster-stop scenarios reset fix fix-all dev backend frontend mock test \
        models capture bench bench-live bench-k8sgpt bench-mock images images-k3s install uninstall monitoring \
        monitoring-down e2e clean

help:              ## list targets
	@grep -E '^[a-z0-9-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-13s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- setup
setup: env         ## venv + python deps + npm deps
	$(PY) -m venv $(VENV)
	$(ACT) pip install -q -r backend/requirements.txt
	cd frontend && npm ci

env:
	@test -f .env || (cp .env.example .env && echo "created .env -> add NEBIUS_API_KEY")

# ---------------------------------------------------------------- cluster
cluster:           ## k3s on this machine (no Docker) + the broken scenarios
	./scripts/setup-k3s.sh

cluster-kind:      ## alternative: kind (needs Docker/Podman; what CI uses)
	./scripts/setup-kind.sh

cluster-stop:      ## stop k3s to free RAM/CPU (start again: sudo systemctl start k3s)
	sudo systemctl stop k3s

scenarios:         ## healthy baseline, then the broken changes
	./scripts/break.sh all

reset:             ## wipe the demo namespace and break everything again
	kubectl delete ns firstcall-demo --ignore-not-found --wait=true && ./scripts/break.sh all

break:             ## break one thing: make break S=03-oom  (see docs/scenarios.md)
	./scripts/break.sh $(S)

fix:               ## apply one fix: make fix S=07-service-selector
	bash scenarios/fixes/$(S).sh

fix-all:           ## apply every fix, watch incidents resolve
	@for f in scenarios/fixes/*.sh; do echo "== $$f"; bash $$f; done

# ---------------------------------------------------------------- run on the host (fast dev loop)
dev:               ## API :8000 + UI :3000 on this machine, watching the cluster
	@trap 'kill 0' EXIT; $(MAKE) backend & $(MAKE) frontend & wait

backend:
	$(ACT) cd backend && uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

mock:              ## no cluster, no model: recorded fixtures + canned answers
	@trap 'kill 0' EXIT; FIRSTCALL_LLM=mock FIRSTCALL_SOURCE=fixtures FIRSTCALL_AUTO_INVESTIGATE=0 FIRSTCALL_DB=data/mock.db $(MAKE) backend & $(MAKE) frontend & wait

test:              ## backend tests
	$(ACT) cd backend && pytest -q

models:            ## model IDs a provider serves: make models PROVIDER=nebius
	$(ACT) $(PY) scripts/list_models.py $(PROVIDER)

# ---------------------------------------------------------------- run inside the cluster (the product shape)
images:            ## build both images locally ($(OCI))
	$(OCI) build -t $(REGISTRY)/firstcall-backend:$(TAG) -f backend/Dockerfile .
	$(OCI) build -t $(REGISTRY)/firstcall-frontend:$(TAG) frontend

images-k3s: images ## build and import into k3s (no registry needed)
	REGISTRY=$(REGISTRY) TAG=$(TAG) OCI=$(OCI) ./scripts/import-images.sh

install:           ## lab install: helm install FirstCall on this cluster (keys from .env), UI on http://localhost:30300
	@./scripts/preflight-install.sh
	@NS=$(NS) SECRET=firstcall-env-keys ./scripts/create-secret.sh
	@set -a; source .env; set +a; ns=$${FIRSTCALL_NAMESPACES:-firstcall-demo}; \
	helm upgrade --install firstcall charts/firstcall -n $(NS) --create-namespace \
	  --set image.api.tag=$(TAG) --set image.web.tag=$(TAG) \
	  --set existingSecret=firstcall-env-keys \
	  --set-string podAnnotations.firstcall/keys-hash=$$(kubectl -n $(NS) get secret firstcall-env-keys -o jsonpath='{.data}' | sha256sum | cut -c1-16) \
	  --set-string watch.namespaces="$${ns//,/\\,}" \
	  --set web.service.type=NodePort \
	  --set model.default=$${FIRSTCALL_DEFAULT_MODEL:-qwen3-30b} \
	  --set-string telegram.chatIds="$${TELEGRAM_CHAT_IDS//,/\\,}" \
	  --set alerting.mode=$${FIRSTCALL_ALERT_MODE:-always} \
	  --set remediation.enabled=$$( [ "$${FIRSTCALL_REMEDIATION:-off}" = approve ] && echo true || echo false) \
	  --set metrics.serviceMonitor.enabled=$$(kubectl get crd servicemonitors.monitoring.coreos.com >/dev/null 2>&1 && echo true || echo false) \
	  --set metrics.prometheusRule.enabled=$$(kubectl get crd prometheusrules.monitoring.coreos.com >/dev/null 2>&1 && echo true || echo false) \
	  --set dashboard.enabled=true $(HELM_EXTRA) --wait --timeout 5m || ./scripts/why-not-ready.sh

uninstall:
	helm uninstall firstcall -n $(NS)

install-nobot:     ## same, but the in-cluster pod does NOT poll Telegram (keeps `make dev` as the poller)
	$(MAKE) install HELM_EXTRA="--set telegram.commands=false"

stack:             ## the whole product, in order: cluster images -> monitoring -> FirstCall -> Grafana
	$(MAKE) images-k3s
	$(MAKE) monitoring
	$(MAKE) install
	@echo; echo "  console   http://localhost:30300"; echo "  grafana   http://localhost:30301  (FirstCall dashboard)"; \
	 echo "  telegram  the in-cluster pod is the poller now - keep 'make dev' stopped"; echo

monitoring:        ## Prometheus + Alertmanager (-> FirstCall) + Grafana :30301, then re-run make install
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
	helm repo update prometheus-community
	helm upgrade --install monitoring prometheus-community/kube-prometheus-stack -n monitoring --create-namespace \
	  -f deploy/monitoring/kube-prometheus-stack.yaml --wait --timeout 10m
	@echo "Grafana: http://localhost:30301 (anonymous view, admin/firstcall). Now: make install"

monitoring-down:
	helm uninstall monitoring -n monitoring

activity:          ## follow what FirstCall is doing, live (terminal)
	$(ACT) $(PY) scripts/activity.py --api $(API_URL)

watch:             ## live view of the demo namespace (pods, events) in a terminal
	watch -n 2 -c 'kubectl get pods -n firstcall-demo -o wide; echo; kubectl get events -n firstcall-demo --sort-by=.lastTimestamp | tail -12'

alert-test:        ## send a test push to Telegram/Slack (API must be running on :8000)
	curl -s -X POST localhost:8000/api/alerting/test; echo

# ---------------------------------------------------------------- evidence
capture:           ## record the live broken cluster into scenarios/fixtures
	$(ACT) $(PY) scripts/capture_fixtures.py

bench:             ## benchmark on fixtures: make bench PROVIDER=nebius
	$(ACT) set -a; source .env; set +a; $(PY) bench/run_bench.py -r 3 --provider $(PROVIDER) -m cascade -m rules

bench-vs-closed:   ## open models vs GPT/Claude on the same scenarios (needs BASELINE_* keys in .env)
	$(ACT) set -a; source .env; set +a; $(PY) bench/run_bench.py --provider $(PROVIDER) -m rules --baseline

bench-live:        ## benchmark against the live broken cluster
	$(ACT) $(PY) bench/run_bench.py --live --provider $(PROVIDER) -m rules

bench-k8sgpt:      ## FirstCall vs k8sgpt on the live cluster, same Nebius model
	$(ACT) $(PY) bench/run_bench.py --live --provider $(PROVIDER) -m rules -m k8sgpt

bench-mock:        ## benchmark pipeline dry run
	$(ACT) FIRSTCALL_LLM=mock $(PY) bench/run_bench.py -m rules -m cascade

e2e:               ## end-to-end: detect all -> diagnose -> events -> fix -> resolve (what CI runs)
	API_URL=$(API_URL) ./scripts/e2e.sh

clean:
	-/usr/local/bin/k3s-uninstall.sh
	-kind delete cluster --name firstcall
