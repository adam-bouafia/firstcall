#!/usr/bin/env bash
# End-to-end product test against a live cluster where FirstCall is already running.
#   detect every scenario -> diagnosed -> Event on the workload -> Alertmanager note -> fixes -> all resolved
# Usage: API_URL=http://localhost:8000 ./scripts/e2e.sh      (CI port-forwards the in-cluster API)
set -euo pipefail
cd "$(dirname "$0")/.."
API=${API_URL:-http://localhost:8000}
NS=firstcall-demo
# one incident per recorded fixture (11-dependency-down yields two: cart-api and svc/inventory-db)
EXPECTED=${EXPECTED:-$(ls scenarios/fixtures/*.json | wc -l)}
TIMEOUT=${TIMEOUT:-420}
c() { curl -sf --noproxy '*' "$@"; }
pass() { echo "  ✔ $*"; }
fail() { echo "  ✘ $*"; echo "--- incidents"; c "$API/api/incidents" | python3 -m json.tool | head -80 || true; exit 1; }
wait_for() {  # wait_for <description> <python expression over incidents `x` and stats `s`>
  local what=$1 expr=$2 t=0
  until python3 scripts/e2e_check.py "$API" eval "$expr"; do
    t=$((t+10)); [ $t -ge $TIMEOUT ] && fail "timed out waiting for: $what"; sleep 10
  done
  pass "$what (${t}s)"
}

echo "== FirstCall e2e against $API"
c "$API/health" >/dev/null || fail "API not reachable"
pass "API healthy: $(python3 scripts/e2e_check.py "$API" health)"

wait_for "$EXPECTED incidents detected" "len([i for i in x if i['status']=='open']) >= $EXPECTED"
wait_for "every open incident diagnosed" "all(i['category'] for i in x if i['status']=='open') and not any(i['busy'] for i in x)"

echo "== categories"
python3 scripts/e2e_check.py "$API" show

if kubectl -n $NS get events --field-selector reason=FirstCallDiagnosis -o name 2>/dev/null | grep -q .; then
  pass "diagnoses written back as Kubernetes Events ($(kubectl -n $NS get events --field-selector reason=FirstCallDiagnosis -o name | wc -l))"
else
  echo "  ! no FirstCallDiagnosis events (FIRSTCALL_WRITE_EVENTS=false or RBAC without events/create)"
fi

POD=$(kubectl -n $NS get pod -l app=payments-api -o jsonpath='{.items[0].metadata.name}')
c -X POST "$API/api/alertmanager" -H 'content-type: application/json' -d "{\"alerts\":[{\"status\":\"firing\",\"labels\":{\"alertname\":\"FirstCallPodWaiting\",\"namespace\":\"$NS\",\"pod\":\"$POD\"},\"annotations\":{\"summary\":\"e2e test alert\"}}]}" >/dev/null
sleep 3
python3 scripts/e2e_check.py "$API" alert-attached payments-api \
  && pass "Alertmanager alert attached to the payments-api incident" || fail "alert not attached"

if curl -sf --noproxy '*' "$API/health" | grep -q '"remediation":"approve"'; then
  echo "== human-approved remediation (dry run, then apply) for payments-api"
  python3 scripts/e2e_check.py "$API" remediate payments-api && pass "rollout undo approved and applied by FirstCall" \
    || fail "remediation failed"
fi
echo "== applying the remaining fixes"
for f in scenarios/fixes/*.sh; do bash "$f" >/dev/null 2>&1 || echo "  ! $f failed"; done
wait_for "all incidents resolved" "s['open'] == 0 and s['resolved'] >= $EXPECTED"
python3 scripts/e2e_check.py "$API" stats
echo "== e2e passed"
