"use client";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ago, api, dur,
  type CommandEvent, type DiagnoseResponse, type DiagnosisEvent, type Health,
  type Activity, type Alerting, type IncidentDetail, type IncidentRow, type ModelInfo, type Stats,
} from "@/lib/api";
import { DiagnosisCard } from "@/components/DiagnosisCard";

const REASON_TONE: Record<string, string> = {
  CrashLoopBackOff: "var(--bad)", OOMKilled: "var(--bad)", ImagePullBackOff: "var(--warn)", ErrImagePull: "var(--warn)",
  CreateContainerConfigError: "var(--warn)", Unschedulable: "var(--info)", NotReady: "var(--warn)", NoEndpoints: "var(--info)",
};

export default function Page() {
  return <Suspense><Console /></Suspense>;
}

function Console() {
  const router = useRouter();
  const params = useSearchParams();
  const selected = Number(params.get("incident")) || null;

  const [health, setHealth] = useState<Health | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [alerting, setAlerting] = useState<Alerting | null>(null);
  const [toast, setToast] = useState("");
  const [rows, setRows] = useState<IncidentRow[]>([]);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [model, setModel] = useState("");
  const [tab, setTab] = useState<"open" | "resolved">("open");
  const [err, setErr] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [h, s, r, a] = await Promise.all([api.health(), api.stats(), api.incidents(), api.alerting()]);
      setHealth(h); setStats(s); setRows(r); setAlerting(a); setErr("");
    } catch (e) { setErr(`Backend unreachable: ${e}`); }
  }, []);

  useEffect(() => {
    api.models().then((m) => { setModels(m.models); setModel(m.default); }).catch(() => {});
    refresh();
    const t = setInterval(refresh, 4000);
    return () => clearInterval(t);
  }, [refresh]);

  const shown = rows.filter((r) => r.status === tab);
  const select = (id: number) => router.replace(`/?incident=${id}`, { scroll: false });

  return (
    <div className="max-w-[1440px] mx-auto px-4 py-5 flex flex-col gap-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-baseline gap-3">
          <h1 className="text-2xl font-bold tracking-tight">FirstCall</h1>
          <span className="text-sm text-[var(--muted)] hidden sm:inline">first diagnosis for Kubernetes incidents, on open-weight models</span>
        </div>
        {health && (
          <div className="flex flex-wrap gap-2 text-xs mono items-center">
            <span className="panel px-2 py-1 flex items-center gap-1.5">
              <span className={`inline-block w-2 h-2 rounded-full ${health.watching ? "pulse" : ""}`}
                style={{ background: health.ok ? "var(--ok)" : "var(--bad)" }} />
              {health.watching ? `watching ${health.namespaces.join(",")} · scanned ${ago(health.last_scan)}` : "watcher off"}
            </span>
            <span className="panel px-2 py-1">source: {health.source}</span>
            <span className="panel px-2 py-1">model: {health.llm === "mock" ? "mock" : health.default_model}</span>
            <button className="btn text-xs" onClick={() => api.scan().then(refresh)}>Scan now</button>
            <ThemeToggle />
          </div>
        )}
      </header>

      {alerting && <AlertBar a={alerting} onChange={(m) => api.setAlerting(m).then(setAlerting)}
        onTest={() => api.testAlert().then((r) => setToast(`Test sent: ${Object.keys(r.sent).join(", ")}`)).catch((e) => setToast(String(e)))} />}
      {toast && <div className="panel text-sm px-4 py-2 mono" onClick={() => setToast("")}>{toast}</div>}

      {(err || health?.last_error) && (
        <div className="panel text-sm px-4 py-2 mono break-words" style={{ borderColor: "var(--bad)", color: "var(--bad)" }}>{err || health?.last_error}</div>
      )}

      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-6 gap-px bg-[var(--line)] border border-[var(--line)] rounded-[10px] overflow-hidden">
          <Tile k="open incidents" v={String(stats.open)} tone={stats.open ? "var(--bad)" : "var(--ok)"} />
          <Tile k="resolved" v={String(stats.resolved)} />
          <Tile k="time to first diagnosis" v={dur(stats.mean_time_to_diagnosis_s)} />
          <Tile k="time to resolve" v={dur(stats.mean_time_to_resolve_s)} />
          <Tile k="model spend" v={`$${stats.total_cost_usd.toFixed(4)}`} />
          <Tile k="marked correct" v={stats.feedback_correct + stats.feedback_wrong ? `${stats.feedback_correct}/${stats.feedback_correct + stats.feedback_wrong}` : "–"} />
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[360px_1fr] gap-4 items-start">
        <aside className="panel p-2 flex flex-col gap-1">
          <div className="flex gap-1 p-1" role="tablist">
            {(["open", "resolved"] as const).map((t) => (
              <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
                className={`flex-1 rounded-md text-sm py-1.5 ${tab === t ? "selected font-semibold" : "text-[var(--muted)] hoverable"}`}>
                {t} ({rows.filter((r) => r.status === t).length})
              </button>
            ))}
          </div>
          {shown.map((i) => (
            <button key={i.id} onClick={() => select(i.id)}
              className={`text-left rounded-lg px-3 py-2.5 border-l-[3px] ${selected === i.id ? "selected" : "hoverable"}`}
              style={{ borderLeftColor: i.status === "resolved" ? "var(--ok)" : REASON_TONE[i.reason] ?? "var(--muted)" }}>
              <div className="flex justify-between gap-2 items-baseline">
                <span className="mono text-sm truncate">{i.workload ?? i.pod}</span>
                <span className="text-[11px] text-[var(--muted)] shrink-0">{ago(i.status === "resolved" ? i.resolved_at : i.first_seen)}</span>
              </div>
              <div className="flex justify-between gap-2 text-xs mt-0.5">
                <span style={{ color: i.status === "resolved" ? "var(--ok)" : REASON_TONE[i.reason] ?? "var(--muted)" }}>
                  {i.status === "resolved" ? "resolved" : i.reason}{i.restarts ? ` · ${i.restarts} restarts` : ""}
                </span>
                <span className="text-[var(--muted)]">{i.namespace}</span>
              </div>
              <div className="text-xs text-[var(--muted)] mt-1 line-clamp-2">
                {i.busy ? <span className="pulse" style={{ color: "var(--accent)" }}>diagnosing…</span> : i.summary ?? "waiting for diagnosis"}
              </div>
            </button>
          ))}
          {shown.length === 0 && (
            <p className="text-sm text-[var(--muted)] px-3 py-6">
              {tab === "open" ? "No open incidents. Break something: make reset" : "Nothing resolved yet."}
            </p>
          )}
        </aside>

        <main className="min-w-0 flex flex-col gap-4">
          {selected ? (
            <IncidentView id={selected} models={models} model={model} setModel={setModel} onChange={refresh}
              fixEnabled={health?.remediation === "approve"} />
          ) : (
            <div className="panel p-10 text-center text-[var(--muted)]">Pick an incident. New ones are diagnosed automatically.</div>
          )}
        </main>
      </div>

      <ActivityPanel onPick={select} />
    </div>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useState<"light" | "dark" | "system">("system");
  useEffect(() => {
    let saved: string | null = null;
    try { saved = localStorage.getItem("firstcall-theme"); } catch { /* private window */ }
    apply((saved as "light" | "dark") ?? "system", setTheme);
  }, []);
  const next = theme === "dark" ? "light" : theme === "light" ? "system" : "dark";
  const icon = theme === "dark" ? "☾" : theme === "light" ? "☀" : "◐";
  return (
    <button className="btn text-xs" title={`Theme: ${theme}. Click for ${next}.`} aria-label={`Theme: ${theme}`}
      onClick={() => apply(next, setTheme)}>{icon}</button>
  );
}

function apply(theme: "light" | "dark" | "system", set: (t: "light" | "dark" | "system") => void) {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
  try { theme === "system" ? localStorage.removeItem("firstcall-theme") : localStorage.setItem("firstcall-theme", theme); } catch { /* ignore */ }
  set(theme);
}

const KIND_TONE: Record<string, string> = {
  scan: "var(--muted)", detect: "var(--bad)", collect: "var(--info)", model: "var(--accent)", command: "var(--info)",
  event: "#b48ead", notify: "#b48ead", resolve: "var(--ok)", alert: "var(--warn)", remediate: "var(--warn)",
  telegram: "#b48ead", error: "var(--bad)", config: "var(--muted)",
};

function ActivityPanel({ onPick }: { onPick: (id: number) => void }) {
  const [items, setItems] = useState<Activity[]>([]);
  const [showScans, setShowScans] = useState(false);
  const [open, setOpen] = useState(true);
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    let last = 0; let stop = false;
    const tick = async () => {
      if (stop) return;
      try {
        const next = await api.activity(last);
        if (next.length) { last = next[next.length - 1].id; setItems((cur) => [...cur, ...next].slice(-400)); }
      } catch { /* backend down: banner shows it */ }
      if (!stop) setTimeout(tick, 1500);
    };
    tick();
    return () => { stop = true; };
  }, []);
  const [frozen, setFrozen] = useState<Activity[]>([]);
  useEffect(() => { if (!paused) setFrozen(items); }, [items, paused]);
  const shown = frozen.filter((a) => showScans || a.kind !== "scan").slice(-200).reverse();
  return (
    <section className="panel">
      <div className="flex items-center gap-3 px-4 py-2 border-b border-[var(--line)]">
        <button onClick={() => setOpen(!open)} className="label hover:text-[var(--fg)]">{open ? "▾" : "▸"} Live activity</button>
        <span className="inline-block w-2 h-2 rounded-full pulse" style={{ background: paused ? "var(--muted)" : "var(--ok)" }} />
        <span className="text-xs text-[var(--muted)]">what FirstCall is doing right now</span>
        <span className="flex-1" />
        <label className="text-xs flex items-center gap-1.5 text-[var(--muted)]">
          <input id="show-scans" type="checkbox" checked={showScans} onChange={(e) => setShowScans(e.target.checked)} /> scans
        </label>
        <button className="btn text-xs" onClick={() => setPaused(!paused)}>{paused ? "Resume" : "Pause"}</button>
      </div>
      {open && (
        <ol className="mono text-xs max-h-72 overflow-auto px-4 py-2 flex flex-col gap-0.5">
          {shown.map((a) => (
            <li key={a.id} className="flex gap-3 leading-relaxed">
              <span className="text-[var(--muted)] shrink-0">{new Date(a.ts * 1000).toLocaleTimeString()}</span>
              <span className="w-20 shrink-0 uppercase" style={{ color: KIND_TONE[a.kind] ?? "var(--muted)" }}>{a.kind}</span>
              {a.incident ? <button className="shrink-0 underline decoration-dotted text-[var(--muted)] hover:text-[var(--fg)]" onClick={() => onPick(a.incident!)}>#{a.incident}</button> : <span className="w-6 shrink-0" />}
              <span className="break-words min-w-0" style={{ color: a.level === "error" ? "var(--bad)" : a.level === "warn" ? "var(--warn)" : undefined }}>{a.text}</span>
            </li>
          ))}
          {shown.length === 0 && <li className="text-[var(--muted)]">waiting for activity…</li>}
        </ol>
      )}
    </section>
  );
}

function AlertBar({ a, onChange, onTest }: { a: Alerting; onChange: (m: string) => void; onTest: () => void }) {
  const channels = Object.entries(a.channels).filter(([, on]) => on).map(([k]) => k);
  const next = a.next_change ? new Date(a.next_change).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : null;
  return (
    <div className="panel px-4 py-2.5 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
      <span className="label">Paging</span>
      <div className="flex rounded-md overflow-hidden border border-[var(--line)]" role="radiogroup" aria-label="Alert mode">
        {[["always", "24/7"], ["offhours", "Nights & weekends"]].map(([m, label]) => (
          <button key={m} role="radio" aria-checked={a.mode === m} onClick={() => onChange(m)}
            className={`px-3 py-1 text-xs ${a.mode === m ? "btn-primary" : "hoverable"}`}>{label}</button>
        ))}
      </div>
      <span className="flex items-center gap-1.5 text-xs">
        <span className="inline-block w-2 h-2 rounded-full" style={{ background: a.paging_now ? "var(--ok)" : "var(--muted)" }} />
        {a.paging_now ? "pushing alerts now" : "quiet (business hours)"}{next && ` · changes ${next}`}
      </span>
      <span className="text-xs text-[var(--muted)]">
        {a.mode === "offhours" ? `weekdays ${a.business_hours.end}–${a.business_hours.start} + weekends · ${a.timezone}` : a.timezone}
      </span>
      <span className="flex-1" />
      <span className="text-xs text-[var(--muted)]">{channels.length ? `to ${channels.join(" + ")}` : "no channel configured"}</span>
      {channels.length > 0 && <button className="btn text-xs" onClick={onTest}>Send test</button>}
    </div>
  );
}

function Tile({ k, v, tone }: { k: string; v: string; tone?: string }) {
  return (
    <div className="bg-[var(--panel)] px-4 py-3">
      <div className="label">{k}</div>
      <div className="text-xl font-semibold tabular-nums mt-1" style={tone ? { color: tone } : undefined}>{v}</div>
    </div>
  );
}

function IncidentView({ id, models, model, setModel, onChange, fixEnabled }: {
  id: number; models: ModelInfo[]; model: string; setModel: (m: string) => void; onChange: () => void; fixEnabled: boolean;
}) {
  const [inc, setInc] = useState<IncidentDetail | null>(null);
  const [snap, setSnap] = useState<Record<string, unknown> | null>(null);
  const [cmp, setCmp] = useState<Record<string, DiagnoseResponse | { error: string }> | null>(null);
  const [comparing, setComparing] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback(() => api.incident(id).then(setInc).catch((e) => setErr(String(e))), [id]);
  useEffect(() => { setInc(null); setSnap(null); setCmp(null); setErr(""); load(); }, [id, load]);
  useEffect(() => {
    const t = setInterval(load, inc?.busy ? 1500 : 4000);
    return () => clearInterval(t);
  }, [load, inc?.busy]);

  const diagnoses = useMemo(() => (inc?.timeline ?? []).filter((e) => e.kind === "diagnosis"), [inc]);
  const latest = diagnoses.at(-1)?.data as DiagnosisEvent | undefined;
  const ran = useMemo(() => new Set((inc?.timeline ?? []).filter((e) => e.kind === "command").map((e) => (e.data.command as string).trim())), [inc]);
  const alreadyRan = !!latest && ran.has(latest.diagnosis.next_command.trim());

  if (err) return <div className="panel p-4 text-sm" style={{ color: "var(--bad)" }}>{err}</div>;
  if (!inc) return <div className="panel p-10 text-center text-[var(--muted)]">Loading…</div>;

  const act = async (fn: () => Promise<unknown>) => { await fn(); load(); onChange(); };
  const compareModels = async () => {
    setComparing(true); setCmp(null);
    try {
      const keys = models.filter((m) => m.provider === models.find((x) => x.key === model)?.provider).map((m) => m.key);
      setCmp(await api.compare(inc.namespace, inc.pod, keys.length ? keys : [model]));
    } catch (e) { setErr(String(e)); }
    setComparing(false);
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="panel p-4 flex flex-col gap-3">
        <div className="flex flex-wrap justify-between gap-2 items-start">
          <div className="min-w-0">
            <div className="mono text-lg truncate">{inc.namespace}/{inc.workload ?? inc.pod}</div>
            <div className="text-xs text-[var(--muted)] mt-0.5 mono truncate">
              pod {inc.pod} · {inc.reason} · first seen {ago(inc.first_seen)}
              {inc.first_diagnosis_at && ` · diagnosed in ${dur(inc.first_diagnosis_at - inc.first_seen)}`}
            </div>
          </div>
          <span className="mono text-xs rounded px-2 py-1" style={{
            background: inc.status === "open" ? "rgba(242,131,122,.12)" : "rgba(111,208,140,.12)",
            color: inc.status === "open" ? "var(--bad)" : "var(--ok)",
          }}>{inc.status === "open" ? "OPEN" : `RESOLVED ${ago(inc.resolved_at)}`}</span>
        </div>
        <div className="flex flex-wrap gap-2 items-center">
          <label htmlFor="model" className="sr-only">Model</label>
          <select id="model" value={model} onChange={(e) => setModel(e.target.value)}
            className="bg-[var(--code)] border border-[var(--line)] rounded-md px-2 py-1.5 text-sm max-w-full">
            {models.map((m) => <option key={m.key} value={m.key}>{m.label} · {m.provider}</option>)}
            <option value="cascade">Cascade (fast model, escalate if unsure)</option>
          </select>
          <button className="btn btn-primary" disabled={inc.busy} onClick={() => act(() => api.diagnose(inc.id, model))}>
            {inc.busy ? "Working…" : latest ? "Diagnose again" : "Diagnose"}
          </button>
          <button className="btn" disabled={comparing || inc.status === "resolved"} onClick={compareModels}>
            {comparing ? "Running models…" : "Compare models"}
          </button>
          <button className="btn" onClick={async () => setSnap(snap ? null : await api.snapshot(inc.id).catch((e) => ({ error: String(e) })))}>
            {snap ? "Hide context" : "What the model sees"}
          </button>
          <span className="flex-1" />
          <span className="text-xs text-[var(--muted)]">Was it right?</span>
          <button className={`btn text-xs ${inc.feedback === "correct" ? "chip-ok" : ""}`} onClick={() => act(() => api.feedback(inc.id, "correct"))}>Correct</button>
          <button className={`btn text-xs ${inc.feedback === "wrong" ? "chip-warn" : ""}`} onClick={() => act(() => api.feedback(inc.id, "wrong"))}>Wrong</button>
        </div>
      </div>

      {snap && <pre className="panel p-4 mono text-xs overflow-auto max-h-96 whitespace-pre-wrap">{JSON.stringify(snap, null, 2)}</pre>}

      {cmp && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {Object.entries(cmp).map(([k, r]) => "error" in r
            ? <div key={k} className="panel p-4 text-sm"><div className="label mb-1">{k}</div><span style={{ color: "var(--bad)" }}>{r.error}</span></div>
            : <DiagnosisCard key={k} res={r} compact title={models.find((m) => m.key === k)?.label ?? k} />)}
        </div>
      )}

      {latest ? (
        <DiagnosisCard res={latest} title="Current diagnosis"
          roundLabel={latest.round ? `refined after ${latest.round} command${latest.round > 1 ? "s" : ""}` : latest.trigger}
          onRun={inc.status === "open" && !alreadyRan ? () => act(() => api.investigate(inc.id, model)) : undefined} running={inc.busy}
          fixEnabled={fixEnabled && inc.status === "open"}
          fixNote={inc.status !== "open" ? "incident resolved" : "remediation off (FIRSTCALL_REMEDIATION)"}
          onPreviewFix={async () => { const r = await api.previewFix(inc.id).catch((e) => ({ ok: false, reason: String(e) })); load(); return r; }}
          onApplyFix={async (h) => { const r = await api.applyFix(inc.id, h).catch((e) => ({ ok: false, reason: String(e) })); load(); onChange(); return r; }} />
      ) : (
        <div className="panel p-6 text-sm text-[var(--muted)]">{inc.busy ? <span className="pulse">Diagnosing…</span> : "No diagnosis yet."}</div>
      )}

      <section className="panel p-4">
        <h3 className="label mb-3">Timeline</h3>
        <ol className="flex flex-col gap-3">
          {inc.timeline.map((e) => <TimelineItem key={e.id} e={e} />)}
        </ol>
      </section>
    </div>
  );
}

function TimelineItem({ e }: { e: { ts: number; kind: string; data: any } }) {
  const [open, setOpen] = useState(false);
  const time = new Date(e.ts * 1000).toLocaleTimeString();
  const dot = { detected: "var(--bad)", diagnosis: "var(--accent)", command: "var(--info)", resolved: "var(--ok)", error: "var(--bad)", remediation: "var(--warn)", remediation_preview: "var(--warn)" }[e.kind] ?? "var(--muted)";
  let body: React.ReactNode = null;
  if (e.kind === "detected") body = <>Detected <b>{e.data.reason}</b> on <span className="mono">{e.data.pod}</span></>;
  else if (e.kind === "diagnosis") {
    const d = e.data as DiagnosisEvent;
    body = <>{d.round ? "Refined" : "Diagnosed"} by <span className="mono">{d.usage.model_key}</span> in {(d.usage.latency_ms / 1000).toFixed(1)}s: <b>{d.diagnosis.summary}</b> ({Math.round(d.diagnosis.confidence * 100)}%)</>;
  } else if (e.kind === "command") {
    const c = e.data as CommandEvent;
    body = (
      <div className="flex flex-col gap-1 min-w-0">
        <button onClick={() => setOpen(!open)} className="text-left">
          Ran <span className="mono">{c.command}</span> <span className="text-[var(--muted)]">(exit {c.exit_code}, {c.redactions} redacted, {c.trigger}) {open ? "▾" : "▸"}</span>
        </button>
        {open && <pre className="mono text-xs bg-[var(--code)] rounded p-2 overflow-auto max-h-72 whitespace-pre-wrap">{c.output}</pre>}
      </div>
    );
  } else if (e.kind === "resolved") body = <>Resolved: workload healthy again after {dur(e.data.after_s)}</>;
  else if (e.kind === "remediation_preview") body = <>Dry run of <span className="mono">{e.data.command}</span>: <b style={{ color: e.data.ok ? "var(--ok)" : "var(--bad)" }}>{e.data.reason}</b> <span className="text-[var(--muted)]">({e.data.by})</span></>;
  else if (e.kind === "remediation") body = <><b style={{ color: e.data.ok ? "var(--warn)" : "var(--bad)" }}>Fix applied</b> by {e.data.approved_by}: <span className="mono">{e.data.command}</span> <span className="text-[var(--muted)]">(exit {e.data.exit_code})</span></>;
  else body = <>{e.data.text}</>;
  return (
    <li className="flex gap-3 text-sm">
      <span className="mono text-xs text-[var(--muted)] w-20 shrink-0 pt-0.5">{time}</span>
      <span className="w-2 h-2 rounded-full mt-1.5 shrink-0" style={{ background: dot }} />
      <div className="min-w-0 flex-1">{body}</div>
    </li>
  );
}
