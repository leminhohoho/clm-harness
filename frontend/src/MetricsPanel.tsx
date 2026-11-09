import { useEffect, useState } from "react";
import { fmtCost, fmtDur, fmtTok, pct, type Metrics } from "./derive";

export function MetricsPanel({ m, at, onClose }: { m: Metrics; at: number; onClose: () => void }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const c = m.context;
  const unpriced = m.cost.unpriced_calls ? ` · ${m.cost.unpriced_calls} unpriced` : "";
  const rows: [string, string, string][] = [
    ["context", `${fmtTok(c.tokens)} / ${fmtTok(c.budget)} (${Math.round(c.pct)}%)`, `edits ${c.edits} · freed ${fmtTok(c.freed)}`],
    ["cost", fmtCost(m.cost.total), `main ${fmtCost(m.cost.main)} · sub ${fmtCost(m.cost.sub)}${unpriced}`],
    ["tokens", `in ${fmtTok(m.tokens.in)} · out ${fmtTok(m.tokens.out)}`, `cached ${pct(m.tokens.cache_rate)}`],
    ["calls", `main ${m.calls.main} · sub ${m.calls.sub}`, `last ${m.calls.last_latency == null ? "—" : fmtDur(m.calls.last_latency)}`],
    ["runs", `${m.runs.total} · detached ${m.runs.detached}`, `jobs running ${m.runs.jobs_running}`],
    ["agent", m.agent.state, `up ${fmtDur(m.agent.uptime + (now - at) / 1000)}`],
  ];
  return (
    <div className="metrics" onClick={onClose}>
      <div className="metrics-title">metrics <span className="dim">(alt+m)</span></div>
      <table>
        <tbody>
          {rows.map(([k, a, b]) => (
            <tr key={k}><td className="dim">{k}</td><td>{a}</td><td className="dim">{b}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
