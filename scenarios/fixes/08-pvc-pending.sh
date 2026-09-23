# storageClassName is immutable on a PVC: recreate it with the cluster's default StorageClass.
set -e
kubectl -n firstcall-demo scale deploy/ledger-db --replicas=0
kubectl -n firstcall-demo delete pvc ledger-data --wait=true
kubectl -n firstcall-demo apply -f - <<'YAML'
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: ledger-data, namespace: firstcall-demo}
spec:
  accessModes: [ReadWriteOnce]
  resources: {requests: {storage: 1Gi}}
YAML
kubectl -n firstcall-demo scale deploy/ledger-db --replicas=1
