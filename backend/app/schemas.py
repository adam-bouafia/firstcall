from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "CrashLoop-AppError",
    "ImagePull",
    "OOMKilled",
    "Unschedulable",
    "ProbeFailure",
    "ConfigMissing",
    "ServiceSelector",
    "StoragePending",
    "Other",
]


class Snapshot(BaseModel):
    """Everything FirstCall knows about one workload at one moment (already redacted)."""

    namespace: str
    pod: str
    phase: str = ""
    workload: str | None = None
    pod_summary: dict = Field(default_factory=dict)
    container_statuses: list[dict] = Field(default_factory=list)
    events: list[dict] = Field(default_factory=list)
    logs: dict[str, str] = Field(default_factory=dict)  # container -> tail (current + previous)
    metrics: dict = Field(default_factory=dict)
    related: dict = Field(default_factory=dict)  # services, endpoints, pvcs, nodes
    redactions: int = 0


class Incident(BaseModel):
    """A workload currently unhealthy, as seen by one scan."""

    namespace: str
    pod: str  # pod name, or svc/<name> for Service-level incidents
    reason: str
    restarts: int = 0
    age: str = ""
    workload: str | None = None  # deployment/x, statefulset/y, service/z: the stable identity
    scenario: str | None = None

    @property
    def fingerprint(self) -> str:
        return f"{self.namespace}/{self.workload or self.pod}"


class Diagnosis(BaseModel):
    summary: str
    category: Category = "Other"
    root_cause: str
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    next_command: str
    next_command_safe: bool = True
    fix: str = ""
    suspected_change: str = ""        # the recent rollout that likely caused it, if any
    remediation_command: str = ""     # one kubectl command that fixes it (applied only after human approval)
    remediation_allowed: bool = False  # passes the remediation allow-list
    sources: list[str] = Field(default_factory=list)  # public URLs used (web search, when enabled)


class Usage(BaseModel):
    model_key: str
    model_id: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0


class DiagnoseRequest(BaseModel):
    namespace: str
    pod: str
    model: str | None = None


class DiagnoseResponse(BaseModel):
    diagnosis: Diagnosis
    usage: Usage
    redactions: int
    context_chars: int


class CompareRequest(BaseModel):
    namespace: str
    pod: str
    models: list[str]
