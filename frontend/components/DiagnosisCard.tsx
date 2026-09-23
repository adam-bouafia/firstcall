"use client";
import { useState } from "react";
import type { DiagnoseResponse, Remediation } from "@/lib/api";

type Props = {
  res: DiagnoseResponse; title?: string; compact?: boolean;
  onRun?: () => void; running?: boolean; roundLabel?: string;
  onPreviewFix?: () => Promise<Remediation>; onApplyFix?: (hash: string) => Promise<Remediation>; fixEnabled?: boolean;
  fixNote?: string;
};

export function DiagnosisCard({ res, title, compact = false, onRun, running, roundLabel, onPreviewFix, onApplyFix, fixEnabled, fixNote }: Props) {
  const [preview, setPreview] = useState<Remediation | null>(null);
  const [applied, setApplied] = useState<Remediation | null>(null);
  const [fixBusy, setFixBusy] = useState(false);
  const d = res.diagnosis;
  const u = res.usage;
  const [copied, setCopied] = useState(false);
  const pct = Math.round(d.confidence * 100);
  const confColor = pct >= 75 ? "var(--ok)" : pct >= 50 ? "var(--warn)" : "var(--bad)";

  return (
    <div className="panel p-5 flex flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          {(title || roundLabel) && (
            <div className="text-xs uppercase tracking-wider text-[var(--muted)] mb-1">{title}{roundLabel && <span> · {roundLabel}</span>}</div>
          )}
          <h3 className="text-lg font-semibold leading-snug">{d.summary}</h3>
        </div>
        <span className="shrink-0 rounded-full border border-[var(--line)] px-2.5 py-1 text-xs mono">{d.category}</span>
      </div>

      <div>
        <div className="flex justify-between text-xs text-[var(--muted)] mb-1"><span>confidence</span><span>{pct}%</span></div>
        <div className="h-1.5 rounded bg-[var(--line)]"><div className="h-1.5 rounded" style={{ width: `${pct}%`, background: confColor }} /></div>
      </div>

      <section>
        <h4 className="label mb-1">Root cause</h4>
        <p className="text-sm leading-relaxed">{d.root_cause}</p>
      </section>

      {!compact && d.evidence.length > 0 && (
        <section>
          <h4 className="label mb-1">Evidence</h4>
          <ul className="flex flex-col gap-1">
            {d.evidence.map((e, i) => (
              <li key={i} className="mono text-xs bg-[var(--code)] rounded px-2 py-1.5 border-l-2 border-[var(--accent)] break-words">{e}</li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <div className="flex items-center justify-between mb-1 gap-2">
          <h4 className="label">Next command</h4>
          <span className={`text-[10px] rounded px-1.5 py-0.5 mono ${d.next_command_safe ? "chip-ok" : "chip-warn"}`}>
            {d.next_command_safe ? "read-only" : "changes the cluster: review first"}
          </span>
        </div>
        <div className="flex gap-2 flex-wrap sm:flex-nowrap">
          <code className="mono text-sm flex-1 min-w-0 bg-[var(--code)] rounded px-3 py-2 overflow-x-auto whitespace-nowrap">$ {d.next_command}</code>
          <button
            onClick={() => { navigator.clipboard.writeText(d.next_command); setCopied(true); setTimeout(() => setCopied(false), 1200); }}
            className="btn text-xs"
          >{copied ? "Copied" : "Copy"}</button>
          {onRun && d.next_command_safe && (
            <button onClick={onRun} disabled={running} className="btn btn-primary text-xs whitespace-nowrap">
              {running ? "Running…" : "Run & refine"}
            </button>
          )}
        </div>
      </section>

      {d.suspected_change && (
        <section>
          <h4 className="label mb-1">What changed</h4>
          <p className="text-sm leading-relaxed" style={{ color: "var(--warn)" }}>{d.suspected_change}</p>
        </section>
      )}

      {d.fix && (
        <section>
          <h4 className="label mb-1">Suggested fix</h4>
          <p className="text-sm leading-relaxed">{d.fix}</p>
        </section>
      )}

      {!compact && d.remediation_command && (
        <section className="rounded-md border border-[var(--line)] p-3 flex flex-col gap-2">
          <div className="flex items-center justify-between gap-2">
            <h4 className="label">Proposed fix · runs only after your approval</h4>
            <span className={`text-[10px] rounded px-1.5 py-0.5 mono ${d.remediation_allowed ? "chip-warn" : "chip-ok"}`}>
              {d.remediation_allowed ? (fixEnabled ? "approval required" : (fixNote ?? "remediation off")) : "outside the allow-list: do it by hand"}
            </span>
          </div>
          <code className="mono text-sm bg-[var(--code)] rounded px-3 py-2 overflow-x-auto whitespace-nowrap">$ {d.remediation_command}</code>
          {d.remediation_allowed && fixEnabled && onPreviewFix && onApplyFix && !applied && (
            <div className="flex gap-2 flex-wrap">
              <button className="btn text-xs" disabled={fixBusy} onClick={async () => { setFixBusy(true); setPreview(await onPreviewFix()); setFixBusy(false); }}>
                {fixBusy && !preview ? "Dry run…" : "Preview (server dry run)"}
              </button>
              {preview?.ok && preview.hash && (
                <button className="btn btn-primary text-xs" disabled={fixBusy}
                  onClick={async () => {
                    if (!confirm(`Apply this change to the cluster?\n\n${d.remediation_command}`)) return;
                    setFixBusy(true); setApplied(await onApplyFix(preview.hash!)); setFixBusy(false);
                  }}>Approve &amp; apply</button>
              )}
            </div>
          )}
          {preview && !applied && (
            <pre className="mono text-xs bg-[var(--code)] rounded p-2 overflow-auto max-h-48 whitespace-pre-wrap"
              style={{ color: preview.ok ? "var(--ok)" : "var(--bad)" }}>{preview.reason}{preview.output ? `\n${preview.output}` : ""}</pre>
          )}
          {applied && (
            <pre className="mono text-xs bg-[var(--code)] rounded p-2 overflow-auto max-h-48 whitespace-pre-wrap"
              style={{ color: applied.ok ? "var(--ok)" : "var(--bad)" }}>{applied.ok ? "Applied. FirstCall will confirm when the workload is healthy." : applied.reason}{applied.output ? `\n${applied.output}` : ""}</pre>
          )}
        </section>
      )}

      <footer className="grid grid-cols-2 sm:grid-cols-5 gap-2 text-xs border-t border-[var(--line)] pt-3">
        <Stat k="model" v={u.model_key} />
        <Stat k="latency" v={`${(u.latency_ms / 1000).toFixed(2)}s`} />
        <Stat k="tokens" v={`${u.prompt_tokens}+${u.completion_tokens}`} />
        <Stat k="cost" v={`$${u.cost_usd.toFixed(5)}`} />
        <Stat k="secrets redacted" v={String(res.redactions)} />
      </footer>
    </div>
  );
}

function Stat({ k, v }: { k: string; v: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[var(--muted)]">{k}</div>
      <div className="mono truncate" title={v}>{v}</div>
    </div>
  );
}
