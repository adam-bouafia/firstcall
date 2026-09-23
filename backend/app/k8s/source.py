from abc import ABC, abstractmethod

from app.schemas import Incident, Snapshot


class ContextSource(ABC):
    """Where cluster context comes from. Swap fixtures <-> live cluster with one env var."""

    @abstractmethod
    def list_incidents(self) -> list[Incident]: ...

    @abstractmethod
    def snapshot(self, namespace: str, pod: str) -> Snapshot: ...

    def record_event(self, namespace: str, workload: str | None, reason: str, message: str,
                     warning: bool = True) -> bool:
        """Write the result back where engineers already look (kubectl describe). No-op offline."""
        return False


def get_source() -> ContextSource:
    from app.config import get_settings

    s = get_settings()
    if s.firstcall_source == "cluster":
        from app.k8s.cluster import ClusterSource

        return ClusterSource(s.namespaces, s.firstcall_log_tail)
    from app.k8s.fixtures import FixtureSource

    return FixtureSource(s.fixtures_dir)
