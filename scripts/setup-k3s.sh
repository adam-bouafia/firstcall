#!/usr/bin/env bash
# Local cluster for FirstCall: single-node k3s straight on Fedora. No Docker, no VM.
# Run as your normal user (NOT with sudo): the script asks for sudo only where needed.
#   make cluster        uninstall later: /usr/local/bin/k3s-uninstall.sh
set -euo pipefail
cd "$(dirname "$0")/.."
say()  { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mxx %s\033[0m\n' "$*"; exit 1; }

# ---- who gets the kubeconfig -------------------------------------------------
if [ "$EUID" -eq 0 ]; then
  [ -n "${SUDO_USER:-}" ] || die "run as a normal user with sudo rights, not as root"
  warn "you ran this with sudo; the kubeconfig will still go to $SUDO_USER (next time: plain 'make cluster')"
  TARGET_USER=$SUDO_USER; SUDO=""
else
  TARGET_USER=$USER; SUDO="sudo"
fi
TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)

# ---- preflight: leftovers from an old multi-node setup -----------------------
say "preflight"
if systemctl list-unit-files 2>/dev/null | grep -q '^k3s-agent'; then
  die "a k3s AGENT from an old setup is installed. Remove it: sudo /usr/local/bin/k3s-agent-uninstall.sh"
fi
if [ -f /etc/rancher/k3s/config.yaml ] && grep -qE '^\s*(server|token)\s*:' /etc/rancher/k3s/config.yaml; then
  warn "/etc/rancher/k3s/config.yaml points k3s at another server (old cluster). Moving it aside:"
  $SUDO cat /etc/rancher/k3s/config.yaml | sed 's/token:.*/token: <hidden>/'
  $SUDO mv /etc/rancher/k3s/config.yaml "/etc/rancher/k3s/config.yaml.old-$(date +%s)"
fi
for port in 6443 10250; do
  if $SUDO ss -ltnp "sport = :$port" 2>/dev/null | grep -q LISTEN && ! $SUDO ss -ltnp "sport = :$port" | grep -q k3s; then
    $SUDO ss -ltnp "sport = :$port"
    die "port $port is used by another process (old kubelet/k8s?). Stop it first."
  fi
done
if systemctl is-active --quiet docker-desktop 2>/dev/null || pgrep -f "Docker Desktop" >/dev/null; then
  warn "Docker Desktop is running and eats RAM; FirstCall doesn't need it (quit it from its tray icon)"
fi
free -h | awk '/Mem:/ {print "memory: " $3 " used / " $2 " total"}'

# ---- firewalld (Fedora default): let pods and services talk ------------------
if systemctl is-active --quiet firewalld; then
  say "firewalld: trusting the k3s pod (10.42/16) and service (10.43/16) networks"
  $SUDO firewall-cmd --permanent --zone=trusted --add-source=10.42.0.0/16 >/dev/null
  $SUDO firewall-cmd --permanent --zone=trusted --add-source=10.43.0.0/16 >/dev/null
  $SUDO firewall-cmd --permanent --add-port=6443/tcp >/dev/null
  $SUDO firewall-cmd --reload >/dev/null
fi

# ---- install / start ---------------------------------------------------------
if ! command -v k3s >/dev/null; then
  say "installing k3s (single node, Traefik disabled: FirstCall doesn't need an ingress)"
  curl -sfL https://get.k3s.io | $SUDO INSTALL_K3S_EXEC="server --disable=traefik --write-kubeconfig-mode=644" sh - \
    || true
else
  say "k3s already installed, (re)starting it"
  $SUDO systemctl restart k3s || true
fi

say "waiting for k3s to come up"
for i in $(seq 1 30); do
  systemctl is-active --quiet k3s && [ -f /etc/rancher/k3s/k3s.yaml ] && break
  sleep 2
done
if ! systemctl is-active --quiet k3s; then
  warn "k3s did not start. Last log lines:"
  $SUDO journalctl -u k3s -n 40 --no-pager | grep -vE 'level=info|^--' | tail -25
  echo
  echo "Paste the lines above to get help, or see docs/local-setup.md -> Troubleshooting."
  exit 1
fi

# ---- kubeconfig --------------------------------------------------------------
say "kubeconfig -> $TARGET_HOME/.kube/config"
$SUDO mkdir -p "$TARGET_HOME/.kube"
if [ -f "$TARGET_HOME/.kube/config" ] && ! grep -q "127.0.0.1:6443" "$TARGET_HOME/.kube/config"; then
  $SUDO cp "$TARGET_HOME/.kube/config" "$TARGET_HOME/.kube/config.bak.$(date +%s)"
  warn "your previous kubeconfig was backed up next to it"
fi
$SUDO cp /etc/rancher/k3s/k3s.yaml "$TARGET_HOME/.kube/config"
$SUDO chown "$TARGET_USER:" "$TARGET_HOME/.kube" "$TARGET_HOME/.kube/config"
$SUDO chmod 600 "$TARGET_HOME/.kube/config"
export KUBECONFIG="$TARGET_HOME/.kube/config"

say "waiting for the node to be Ready (max 3 min, status every 5 s)"
ok=""
for i in $(seq 1 36); do
  out=$(kubectl get nodes --no-headers 2>&1) || true
  printf '   [%3ss] %s\n' $((i*5)) "$(echo "$out" | head -1)"
  if echo "$out" | awk '{print $2}' | grep -qx Ready; then ok=1; break; fi
  sleep 5
done
if [ -z "$ok" ]; then
  warn "the node never became Ready. What Kubernetes says:"
  kubectl get nodes -o wide 2>&1 || true
  kubectl describe nodes 2>/dev/null | sed -n '/Conditions:/,/Addresses:/p' | head -20
  warn "last k3s log lines:"
  $SUDO journalctl -u k3s -n 30 --no-pager | grep -vE 'level=info' | tail -15
  echo; echo "See docs/local-setup.md -> 'node never becomes Ready'."
  exit 1
fi

say "waiting for system pods (CoreDNS, metrics-server)"
kubectl get pods -n kube-system
kubectl -n kube-system rollout status deploy/coredns --timeout=180s
kubectl -n kube-system rollout status deploy/metrics-server --timeout=180s || warn "metrics-server not ready yet (FirstCall works without it)"

say "pre-pulling busybox (the scenarios' only image) so they break for the right reason"
$SUDO k3s ctr images pull docker.io/library/busybox:1.36 >/dev/null

say "deploying the healthy baseline, then the 8 broken changes"
./scripts/break.sh all
kubectl get nodes -o wide
cat <<MSG

Done. The scenarios need ~90 s to break:   kubectl get pods -n firstcall-demo -w
Then:                                      make dev   ->  http://localhost:3000
Stop k3s when not using it:                make cluster-stop
MSG
