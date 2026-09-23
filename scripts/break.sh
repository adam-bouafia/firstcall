#!/usr/bin/env bash
# Break the demo namespace the way production breaks: most failures arrive as a bad change on top of
# a healthy version. For scenarios with a baseline, the healthy version is rolled out first, then the
# broken change is applied, so `kubectl rollout history` shows exactly what changed (and `rollout undo` works).
#   ./scripts/break.sh all            every scenario
#   ./scripts/break.sh 03-oom         one scenario
set -euo pipefail
cd "$(dirname "$0")/.."
NS=firstcall-demo
target=${1:-all}
kubectl apply -f scenarios/manifests/00-namespace.yaml >/dev/null

if [ "$target" = all ]; then files=(scenarios/manifests/0[1-9]*.yaml); else files=("scenarios/manifests/$target.yaml"); fi

baselines=()
for f in "${files[@]}"; do b="scenarios/baseline/$(basename "$f")"; [ -f "$b" ] && baselines+=("$b"); done
if [ ${#baselines[@]} -gt 0 ]; then
  echo "== 1/2 healthy baseline: ${#baselines[@]} deployment(s)"
  for b in "${baselines[@]}"; do kubectl apply -f "$b"; done
  for b in "${baselines[@]}"; do
    d=$(awk '/^  name:/ {print $2; exit}' "$b")
    kubectl -n $NS rollout status deploy/"$d" --timeout=120s | sed 's/^/   /'
  done
fi
echo "== 2/2 applying the broken change(s)"
for f in "${files[@]}"; do kubectl apply -f "$f"; done
echo
echo "Breaking now (~60-90 s). Watch:  kubectl get pods -n $NS -w"
