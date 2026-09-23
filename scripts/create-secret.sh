#!/usr/bin/env bash
# Create or update the provider-keys Secret from .env, then point the chart at it with existingSecret.
# Only non-empty *_KEY / *_TOKEN / *_WEBHOOK_URL values are copied. They go through a mode-600 temp
# file, so they never appear on a command line or in Helm's release history.
#   NS=firstcall-system SECRET=firstcall-env-keys ./scripts/create-secret.sh
# (not firstcall-keys: that name belongs to the chart's own Secret and Helm would delete it on upgrade)
set -euo pipefail
cd "$(dirname "$0")/.."
NS=${NS:-firstcall-system}
SECRET=${SECRET:-firstcall-env-keys}
ENV_FILE=${ENV_FILE:-.env}
[ -f "$ENV_FILE" ] || { echo "no $ENV_FILE - run: make env"; exit 1; }

tmp=$(mktemp)
chmod 600 "$tmp"
trap 'rm -f "$tmp"' EXIT
grep -E '^[A-Z0-9_]+(_KEY|_TOKEN|_WEBHOOK_URL)=.+' "$ENV_FILE" | grep -v '^BASELINE_' > "$tmp" || true

kubectl get ns "$NS" >/dev/null 2>&1 || kubectl create ns "$NS" >/dev/null
kubectl -n "$NS" create secret generic "$SECRET" --from-env-file="$tmp" --dry-run=client -o yaml \
  | kubectl apply -f - >/dev/null
echo "secret $NS/$SECRET: $(cut -d= -f1 "$tmp" | paste -sd, - | sed 's/^$/(no keys set)/')"
