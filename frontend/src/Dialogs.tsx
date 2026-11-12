import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import type { Profile } from "./derive";

const DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash";
let models: Promise<string[]> | undefined; // the gateway list rarely changes: fetch once per page load

/** FastAPI errors: our own detail is a string, pydantic validation errors are a list. */
async function errorOf(r: Response) {
  const d = (await r.json().catch(() => ({}))).detail;
  return typeof d === "string" ? d : d?.[0]?.msg ?? `error ${r.status}`;
}

/** Centered card over a dimmed backdrop (a full-screen sheet on mobile); Esc is handled by the app. */
export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className="backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head"><h3>{title}</h3><button className="x" title="close (esc)" onClick={onClose}>×</button></div>
        {children}
      </div>
    </div>
  );
}

/** Free-text model id with the gateway's models as suggestions. */
function ModelInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [list, setList] = useState<string[]>([]);
  useEffect(() => {
    models ??= fetch("/api/models").then((r) => r.json()).catch(() => ((models = undefined), []));
    void models.then(setList);
  }, []);
  return (
    <>
      <input list="models" placeholder="model" value={value} onChange={(e) => onChange(e.target.value)} />
      <datalist id="models">{list.map((m) => <option key={m} value={m} />)}</datalist>
    </>
  );
}

export function NewAgent({ onCreated, onClose }: { onCreated: (name: string) => void; onClose: () => void }) {
  const [name, setName] = useState("");
  const [model, setModel] = useState(DEFAULT_MODEL);
  const [err, setErr] = useState("");
  const create = async (e: FormEvent) => {
    e.preventDefault();
    const r = await fetch("/api/profiles", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, model }),
    });
    if (!r.ok) return setErr(await errorOf(r));
    onCreated(name);
  };
  return (
    <Modal title="New agent" onClose={onClose}>
      <form className="form" onSubmit={(e) => void create(e)}>
        <label>Name<input autoFocus value={name} onChange={(e) => setName(e.target.value)} /></label>
        <span className="hint dim">a-z, 0-9, _ or -, up to 32 chars, starting with a letter or digit · folder data/{name || "<name>"}</span>
        <label>Model<ModelInput value={model} onChange={setModel} /></label>
        {err && <span className="err">{err}</span>}
        <div className="actions"><button type="button" onClick={onClose}>Cancel</button><button className="primary" disabled={!name || !model}>Create</button></div>
      </form>
    </Modal>
  );
}

export function AgentSettings({ agent, onChanged, onDeleted, onClose }: {
  agent: Profile; onChanged: () => void; onDeleted: () => void; onClose: () => void;
}) {
  const [model, setModel] = useState(agent.model);
  const [saved, setSaved] = useState(false);
  const [typed, setTyped] = useState("");
  const [err, setErr] = useState("");
  const url = `/api/profiles/${encodeURIComponent(agent.name)}`;

  const save = async (e: FormEvent) => {
    e.preventDefault();
    const r = await fetch(url, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model }) });
    if (!r.ok) return setErr(await errorOf(r));
    setErr("");
    setSaved(true);
    onChanged();
  };
  const del = async () => {
    const r = await fetch(url, { method: "DELETE" });
    if (!r.ok) return setErr(await errorOf(r));
    onDeleted();
  };

  return (
    <Modal title={`${agent.name} · settings`} onClose={onClose}>
      <div className="form">
        <dl className="info">
          <dt>Name</dt><dd>{agent.name}</dd>
          <dt>Folder</dt><dd className="mono">data/{agent.name}</dd>
          <dt>State</dt><dd>{agent.state}</dd>
        </dl>
        <form className="row" onSubmit={(e) => void save(e)}>
          <label>Model<ModelInput value={model} onChange={(v) => (setModel(v), setSaved(false))} /></label>
          <button className="primary" disabled={!model || model === agent.model}>Save</button>
        </form>
        {saved && <span className="ok">saved · applies from the next model call</span>}
        <div className="danger-zone">
          <b className="err">Danger zone</b>
          <span>Deletes the folder and kills its jobs. Type <b>{agent.name}</b> to delete.</span>
          <div className="row">
            <input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={agent.name} />
            <button className="danger" disabled={typed !== agent.name} onClick={() => void del()}>Delete</button>
          </div>
        </div>
        {err && <span className="err">{err}</span>}
      </div>
    </Modal>
  );
}
