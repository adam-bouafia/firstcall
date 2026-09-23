#!/usr/bin/env bash
# helm said "context deadline exceeded". This says why, in the order that is usually true.
NS="${NS:-firstcall-system}"
echo
echo "================ why the release is not ready ================"
kubectl -n "$NS" get pods -o wide 2>/dev/null
echo
for p in $(kubectl -n "$NS" get pods -o name 2>/dev/null); do
  reason=$(kubectl -n "$NS" get "$p" -o jsonpath='{.status.containerStatuses[*].state.waiting.reason}' 2>/dev/null)
  case "$reason" in
    ErrImagePull|ImagePullBackOff)
      echo ">> $p cannot get its image."
      echo "   k3s pulls from the network unless the image is already in containerd."
      echo "   fix: make images-k3s   (then: make install)";;
    CrashLoopBackOff|CreateContainerConfigError)
      echo ">> $p is crashing. last 25 log lines:"
      kubectl -n "$NS" logs "$p" --tail=25 --all-containers 2>&1 | sed 's/^/     /'
      echo "   a missing NEBIUS_API_KEY in .env shows up here.";;
  esac
done
echo
kubectl -n "$NS" get events --sort-by=.lastTimestamp 2>/dev/null | tail -12
echo "============================================================="
exit 1
