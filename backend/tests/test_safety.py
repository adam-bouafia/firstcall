import pytest

from app.safety import is_safe


@pytest.mark.parametrize("cmd", [
    "kubectl describe pod x -n demo", "kubectl logs x -n demo --previous",
    "kubectl get sc", "kubectl rollout status deploy/x -n demo", "kubectl -n demo get ep orders",
])
def test_safe(cmd):
    assert is_safe(cmd)


@pytest.mark.parametrize("cmd", [
    "kubectl delete pod x", "kubectl apply -f x.yaml", "kubectl rollout restart deploy/x",
    "kubectl get pods | xargs kubectl delete", "rm -rf /", "kubectl exec -it x -- sh", "",
])
def test_unsafe(cmd):
    assert not is_safe(cmd)
