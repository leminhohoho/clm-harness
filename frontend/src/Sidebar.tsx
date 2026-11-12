import { useEffect, useState } from "react";
import { fmtCost, fmtDur, fmtTok, pct, type Metrics, type Profile } from "./derive";

type Live = { m: Metrics; at: number } | null;
const PALETTE = ["#e17076", "#faa774", "#a695e7", "#7bc862", "#6ec9cb", "#65aadd", "#ee7aae"];
export const working = (state: string) => state === "thinking" || state === "executing";

/** Telegram-style round initial, coloured from a hash of the name; ringed while the agent works. */
export function Avatar({ name, state }: { name: string; state: string }) {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) | 0;
  return <span className={`av ${state}`} style={{ background: PALETTE[Math.abs(h) % PALETTE.length] }}>{name[0].toUpperCase()}</span>;
}

function MetricsTable({ m, at }: { m: Metrics; at: number }) {
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
    <table>
      <tbody>
        {rows.map(([k, a, b]) => (
          <tr key={k}><td className="dim">{k}</td><td>{a}</td><td className="dim">{b}</td></tr>
        ))}
      </tbody>
    </table>
  );
}

export function Sidebar(p: {
  profiles: Profile[]; current: string | null; state: string; metrics: Live; pop: boolean; theme: string;
  onSelect: (name: string) => void; onNew: () => void; onSettings: (name: string) => void; onPop: () => void; onTheme: () => void;
}) {
  const m = p.metrics?.m;
  return (
    <aside className="sidebar">
      <div className="side-top">
        <span className="brand">clm-harness</span>
        <button className="round" title="new agent (alt+n)" onClick={p.onNew}>+</button>
      </div>
      <div className="agents">
        {p.profiles.map((a) => {
          const state = a.name === p.current ? p.state : a.state;
          return (
            <div key={a.name} className={`arow ${a.name === p.current ? "sel" : ""}`} title={a.name} onClick={() => p.onSelect(a.name)}>
              <Avatar name={a.name} state={state} />
              <span className="who">
                <b>{a.name}</b>
                <span className={working(state) ? "accent" : "dim"}>{working(state) ? `${state}…` : a.model}</span>
              </span>
              <button className="more" title="agent settings" onClick={(e) => (e.stopPropagation(), p.onSettings(a.name))}>⋯</button>
            </div>
          );
        })}
      </div>
      <div className="side-bottom">
        <button className="metrics-btn" title="metrics (alt+m)" disabled={!m} onClick={p.onPop}>
          ▤<span className="lbl">{m ? ` ${Math.round(m.context.pct)}% · ${fmtCost(m.cost.total)}` : ""}</span>
        </button>
        <button className="round" title={`theme: ${p.theme} (alt+d)`} onClick={p.onTheme}>◐</button>
        {p.pop && p.metrics && <div className="popover"><MetricsTable m={p.metrics.m} at={p.metrics.at} /></div>}
      </div>
    </aside>
  );
}
