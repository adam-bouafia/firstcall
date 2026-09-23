export const API = "/fc";

export type ModelInfo = { key: string; id: string; label: string; role: string; provider: string; price_in: number; price_out: number };
export type Diagnosis = {
  summary: string; category: string; root_cause: string; confidence: number;
  evidence: string[]; next_command: string; next_command_safe: boolean; fix: string;
  suspected_change?: string; remediation_command?: string; remediation_allowed?: boolean;
};
export type Activity = { id: number; ts: number; kind: string; level: string; text: string; incident: number | null };
export type Remediation = { ok: boolean; reason: string; command?: string; output?: string; hash?: string; exit_code?: number };
export type Usage = { model_key: string; model_id: string; prompt_tokens: number; completion_tokens: number; latency_ms: number; cost_usd: number };
export type DiagnoseResponse = { diagnosis: Diagnosis; usage: Usage; redactions: number; context_chars: number };
export type DiagnosisEvent = DiagnoseResponse & { trigger: string; round: number };
export type CommandEvent = { command: string; exit_code: number; output: string; redactions: number; duration_ms: number; trigger: string };

export type IncidentRow = {
  id: number; fingerprint: string; namespace: string; workload: string | null; pod: string; reason: string;
  restarts: number; status: "open" | "resolved"; first_seen: number; last_seen: number;
  first_diagnosis_at: number | null; resolved_at: number | null; feedback: string | null;
  summary: string | null; category: string | null; busy: boolean;
};
export type TimelineEvent = { id: number; ts: number; kind: string; data: any };
export type IncidentDetail = IncidentRow & { timeline: TimelineEvent[] };
export type Health = { ok: boolean; source: string; llm: string; default_model: string; watching: boolean; last_scan: number | null; last_error: string | null; namespaces: string[]; remediation: string };
export type Alerting = {
  mode: "always" | "offhours"; modes: string[]; description: string; paging_now: boolean; next_change: string | null;
  timezone: string; business_hours: { start: string; end: string; days: string }; channels: { telegram: boolean; slack: boolean };
};
export type Stats = { open: number; resolved: number; mean_time_to_diagnosis_s: number | null; mean_time_to_resolve_s: number | null; diagnoses: number; total_cost_usd: number; feedback_correct: number; feedback_wrong: number };

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API}${path}`, { cache: "no-store", headers: { "Content-Type": "application/json" }, ...init });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}
const post = <T,>(path: string, body: unknown = {}) => j<T>(path, { method: "POST", body: JSON.stringify(body) });

export const api = {
  health: () => j<Health>("/health"),
  stats: () => j<Stats>("/api/stats"),
  models: () => j<{ default: string; models: ModelInfo[] }>("/api/models"),
  incidents: () => j<IncidentRow[]>("/api/incidents"),
  incident: (id: number) => j<IncidentDetail>(`/api/incidents/${id}`),
  snapshot: (id: number) => j<Record<string, unknown>>(`/api/incidents/${id}/snapshot`),
  diagnose: (id: number, model?: string) => post(`/api/incidents/${id}/diagnose`, { model }),
  investigate: (id: number, model?: string) => post(`/api/incidents/${id}/investigate`, { model }),
  feedback: (id: number, value: "correct" | "wrong") => post(`/api/incidents/${id}/feedback`, { value }),
  scan: () => post<{ seen: number; new: number[] }>("/api/scan"),
  alerting: () => j<Alerting>("/api/alerting"),
  activity: (since: number) => j<Activity[]>(`/api/activity?since=${since}`),
  previewFix: (id: number) => post<Remediation>(`/api/incidents/${id}/remediation/preview`),
  applyFix: (id: number, hash: string) => post<Remediation>(`/api/incidents/${id}/remediation/apply`, { hash, approver: "console" }),
  setAlerting: (mode: string) => post<Alerting>("/api/alerting", { mode }),
  testAlert: () => post<{ sent: Record<string, number> }>("/api/alerting/test"),
  compare: (namespace: string, pod: string, models: string[]) =>
    post<Record<string, DiagnoseResponse | { error: string }>>("/api/compare", { namespace, pod, models }),
};

export function ago(ts: number | null | undefined): string {
  if (!ts) return "–";
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
export function dur(s: number | null | undefined): string {
  if (s == null) return "–";
  if (s < 60) return `${s.toFixed(1)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${(s / 3600).toFixed(1)}h`;
}
