import { useEffect, useState, type FormEvent } from "react";
import { fmtCost, fmtDur, fmtTok, pct, type Metrics } from "./derive";

export type Profile = { name: string; model: string; state: string };
const DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash";

/** FastAPI errors: our own detail is a string, pydantic validation errors are a list. */
async function errorOf(r: Response) {
  const d = (await r.json().catch(() => ({}))).detail;
  return typeof d === "string" ? d : d?.[0]?.msg ?? `error ${r.status}`;
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

function DeleteConfirm({ name, onDone }: { name: string; onDone: (deleted: boolean) => void }) {
  const [typed, setTyped] = useState("");
  const [err, setErr] = useState("");
  const del = async () => {
    const r = await fetch(`/api/profiles/${encodeURIComponent(name)}`, { method: "DELETE" });
    if (r.ok) onDone(true);
    else setErr(await errorOf(r));
  };
  return (
    <div className="confirm" onClick={(e) => e.stopPropagation()}>
      <span>Deletes the folder and kills its jobs. Type <b>{name}</b> to delete.</span>
      <div className="row">
        <input autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} />
        <button className="danger" disabled={typed !== name} onClick={() => void del()}>Delete</button>
        <button onClick={() => onDone(false)}>Cancel</button>
      </div>
      {err && <span className="err">{err}</span>}
    </div>
  );
}

function NewProfile({ onCreated }: { onCreated: (name: string) => void }) {
  const [name, setName] = useState("");
  const [model, setModel] = useState(DEFAULT_MODEL);
  const [models, setModels] = useState<string[]>([]);
  const [err, setErr] = useState("");
  useEffect(() => {
    fetch("/api/models").then((r) => r.json()).then(setModels, () => {});
  }, []);
  const create = async (e: FormEvent) => {
    e.preventDefault();
    const r = await fetch("/api/profiles", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, model }),
    });
    if (!r.ok) return setErr(await errorOf(r));
    setErr("");
    setName("");
    onCreated(name);
  };
  return (
    <form className="newp" onSubmit={(e) => void create(e)}>
      <input placeholder="name (a-z 0-9 _ -)" value={name} onChange={(e) => setName(e.target.value)} />
      <div className="row">
        <input list="models" placeholder="model" value={model} onChange={(e) => setModel(e.target.value)} />
        <datalist id="models">{models.map((m) => <option key={m} value={m} />)}</datalist>
        <button className="primary" disabled={!name || !model}>Create</button>
      </div>
      {err && <span className="err">{err}</span>}
    </form>
  );
}

export function SettingsPanel(p: {
  profiles: Profile[]; current: string | null; onSwitch: (name: string) => void; refresh: () => void;
  metrics: { m: Metrics; at: number } | null; theme: string; onTheme: () => void; onClose: () => void;
}) {
  const [deleting, setDeleting] = useState<string | null>(null);
  useEffect(p.refresh, []);
  return (
    <>
      <div className="backdrop" onClick={p.onClose} />
      <aside className="settings">
        <h3>Profiles</h3>
        {p.profiles.map((pr) => (
          <div key={pr.name}>
            <div className={`prow ${pr.name === p.current ? "cur" : ""}`} onClick={() => p.onSwitch(pr.name)}>
              <i className={`dot ${pr.state}`} />
              <span className="n">{pr.name}</span>
              <span className="m" title={pr.model}>{pr.model}</span>
              <button title="delete profile" onClick={(e) => (e.stopPropagation(), setDeleting(pr.name))}>×</button>
            </div>
            {deleting === pr.name && (
              <DeleteConfirm name={pr.name} onDone={(deleted) => (setDeleting(null), deleted && p.refresh())} />
            )}
          </div>
        ))}
        {!p.profiles.length && <div className="dim pad-x">no profiles yet</div>}
        <NewProfile onCreated={(name) => (p.refresh(), p.onSwitch(name))} />
        <h3>Metrics</h3>
        {p.metrics ? <MetricsTable m={p.metrics.m} at={p.metrics.at} /> : <div className="dim pad-x">no profile connected</div>}
        <h3>Appearance</h3>
        <div className="pad-x"><button onClick={p.onTheme}>◐ {p.theme === "dark" ? "dark · switch to light" : "light · switch to dark"}</button></div>
      </aside>
    </>
  );
}
