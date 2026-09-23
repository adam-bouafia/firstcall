#!/usr/bin/env bash
# Alternative to k3s (and what CI uses): kind, 1 control-plane + 2 workers. Needs Docker or Podman.
# Fedora + podman instead of docker:  export KIND_EXPERIMENTAL_PROVIDER=podman
set -euo pipefail
cd "$(dirname "$0")/.."

command -v kind >/dev/null || { echo "install kind: https://kind.sigs.k8s.io/docs/user/quick-start/#installation"; exit 1; }
command -v kubectl >/dev/null || { echo "install kubectl: sudo dnf install kubectl"; exit 1; }

if ! kind get clusters 2>/dev/null | grep -qx firstcall; then
  kind create cluster --config infra/kind-config.yaml
fi
kubectl config use-context kind-firstcall

echo "== metrics-server"
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kubectl -n kube-system patch deploy metrics-server --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]' 2>/dev/null || true

echo "== pre-pull busybox so scenarios break for the right reason, not a slow pull"
docker pull -q busybox:1.36 >/dev/null 2>&1 && kind load docker-image busybox:1.36 --name firstcall || true

echo "== scenarios"
kubectl apply -f scenarios/manifests/
echo
echo "Give them ~90 seconds to break, then:  kubectl get pods -n firstcall-demo"
