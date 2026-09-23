kubectl -n firstcall-demo scale deploy inventory-db --replicas=1
kubectl -n firstcall-demo rollout status deploy inventory-db --timeout=60s
# skip cart-api's crash-loop back-off instead of waiting up to 5 minutes for the next restart
kubectl -n firstcall-demo delete pod -l app=cart-api --wait=false
