from app.investigate import UnsafeCommand, run_readonly
import pytest


def test_refuses_mutating():
    with pytest.raises(UnsafeCommand):
        run_readonly("kubectl delete pod x -n y")


def test_runs_readonly_with_missing_binary(monkeypatch):
    from app import config
    monkeypatch.setattr(config.get_settings(), "kubectl_bin", "/nonexistent/kubectl")
    r = run_readonly("kubectl get pods -n x")
    assert r["exit_code"] == 127
