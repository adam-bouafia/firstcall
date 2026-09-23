#!/usr/bin/env bash
# Five seconds of checks that turn "context deadline exceeded" into a sentence.
set -uo pipefail
NS="${NS:-firstcall-system}"
TAG="${TAG:-0.3.0}"
fail=0
say() { printf '%s\n' "$*"; }

[ -f .env ] || { say "!! no .env - run: make env, then add NEBIUS_API_KEY"; fail=1; }

kubectl version -o json >/dev/null 2>&1 || {
  say "!! kubectl cannot reach a cluster - run: make cluster"; exit 1; }

# 1. are the images actually inside containerd? this is the usual cause of a hung install
if command -v k3s >/dev/null 2>&1; then
  if ! sudo -n k3s ctr images ls -q 2>/dev/null | grep -q firstcall-backend; then
    # sudo -n may simply have no cached timestamp; only warn when we could look
    if sudo -n true 2>/dev/null; then
      say "!! firstcall images are not in k3s' containerd."
      say "   the pods will sit in ErrImagePull and helm --wait will time out."
      say "   fix: make images-k3s"
      fail=1
    fi
  fi
fi

# 2. is something local already polling the Telegram bot token?
if curl -s -m 1 http://localhost:8000/api/stats >/dev/null 2>&1; then
  say "!! a local FirstCall is running on :8000 (make dev)."
  say "   Telegram allows ONE poller per bot token - the second one gets 409 Conflict."
  say "   either stop 'make dev' (the cluster becomes the poller, this is the demo setup)"
  say "   or run: make install-nobot   (cluster only pushes alerts, laptop keeps the buttons)"
fi

# 3. free node capacity, cheap sanity
kubectl get nodes --no-headers 2>/dev/null | grep -qv ' Ready ' && say "!! node not Ready: kubectl get nodes"

exit $fail
