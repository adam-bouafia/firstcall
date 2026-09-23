from datetime import datetime, timedelta, timezone

import pytest
from kubernetes.client import (V1ContainerState, V1ContainerStateRunning, V1ContainerStateTerminated,
                               V1ContainerStateWaiting, V1ContainerStatus, V1Pod, V1PodStatus)

from app.k8s.cluster import ClusterSource

NOW = datetime.now(timezone.utc)


def pod(state: V1ContainerState, ready=False, restarts=3, last: V1ContainerStateTerminated | None = None,
        phase="Running") -> V1Pod:
    cs = V1ContainerStatus(name="api", image="busybox", image_id="", ready=ready, restart_count=restarts,
                           state=state, last_state=V1ContainerState(terminated=last))
    return V1Pod(status=V1PodStatus(phase=phase, container_statuses=[cs]))


def crashed(seconds_ago: int, code=1, reason="Error") -> V1ContainerStateTerminated:
    return V1ContainerStateTerminated(exit_code=code, reason=reason, finished_at=NOW - timedelta(seconds=seconds_ago))


def reason(p: V1Pod) -> str | None:
    return ClusterSource._pod_reason(object.__new__(ClusterSource), p)


@pytest.mark.parametrize("p, expected", [
    # kubelet reports the back-off as waiting on most versions...
    (pod(V1ContainerState(waiting=V1ContainerStateWaiting(reason="CrashLoopBackOff"))), "CrashLoopBackOff"),
    # ...but newer ones leave it terminated between restarts: still a crash loop, not healthy
    (pod(V1ContainerState(terminated=crashed(5)), last=crashed(40)), "CrashLoopBackOff"),
    # running again for a few seconds after a crash, no readiness probe (ready=True): not recovered yet
    (pod(V1ContainerState(running=V1ContainerStateRunning(started_at=NOW)), ready=True, last=crashed(20)),
     "CrashLoopBackOff"),
    (pod(V1ContainerState(terminated=crashed(5, 137, "OOMKilled"))), "OOMKilled"),
])
def test_detects_crash_loop_in_every_phase(p, expected):
    assert reason(p) == expected


def test_healthy_once_the_last_crash_is_old():
    p = pod(V1ContainerState(running=V1ContainerStateRunning(started_at=NOW)), ready=True, last=crashed(600))
    assert reason(p) is None


def test_clean_exit_is_not_a_crash():
    p = pod(V1ContainerState(terminated=crashed(5, 0, "Completed")), restarts=0, phase="Succeeded")
    assert reason(p) is None
