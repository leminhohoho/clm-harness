import { useEffect, useState, type ReactNode } from "react";
import { api, fmtSize, isImage, rawUrl } from "./derive";

type Entry = { name: string; dir: boolean; size: number; mtime: number };
type Sel = { path: string; size: number };
const TEXT_LIMIT = 1 << 20;

type Content = { path: string; rev: number } & ({ text: string } | { binary: true });

function FilePane({ profile, sel, rev }: { profile: string; sel: Sel; rev: number }) {
  const [content, setContent] = useState<Content>();
  const image = isImage(sel.path);
  useEffect(() => {
    if (image || sel.size > TEXT_LIMIT) return;
    let live = true;
    fetch(rawUrl(profile, sel.path)).then((r) => r.arrayBuffer()).then((buf) => {
      if (!live) return;
      const bytes = new Uint8Array(buf);
      setContent(bytes.subarray(0, 8192).includes(0) ? { path: sel.path, rev, binary: true }
        : { path: sel.path, rev, text: new TextDecoder().decode(bytes) });
    });
    return () => void (live = false);
  }, [profile, sel.path, rev]);

  const cur = content?.path === sel.path && content.rev === rev ? content : undefined;
  const binary = !image && (sel.size > TEXT_LIMIT || (cur && "binary" in cur));
  return (
    <div className="scroll file">
      <div className="bar">
        <span>{sel.path} · {fmtSize(sel.size)}</span>
        <a href={rawUrl(profile, sel.path, true)}>Download</a>
      </div>
      {image ? <div className="preview"><img src={`${rawUrl(profile, sel.path)}?r=${rev}`} alt={sel.path} /></div>
        : binary ? <div className="preview dim">binary file · {fmtSize(sel.size)}</div>
        : cur && "text" in cur && (
          <pre className="raw numbered">{cur.text.replace(/\n$/, "").split("\n").map((l, i) => <span key={i} className="ln">{l || " "}</span>)}</pre>
        )}
    </div>
  );
}

export function FilesView({ profile }: { profile: string }) {
  const [kids, setKids] = useState(new Map<string, Entry[]>());
  const [open, setOpen] = useState<Set<string>>(new Set([""]));
  const [sel, setSel] = useState<Sel>();
  const [rev, setRev] = useState(0);

  const load = (path: string) =>
    fetch(`${api(profile)}/tree?path=${encodeURIComponent(path)}`)
      .then((r) => (r.ok ? r.json() : []))
      .then((entries: Entry[]) => setKids((k) => new Map(k).set(path, entries)));

  useEffect(() => void load(""), [profile]);

  const toggle = (path: string) => {
    const next = new Set(open);
    if (next.delete(path)) return setOpen(next);
    setOpen(next.add(path));
    void load(path);
  };
  const refresh = () => {
    open.forEach((p) => void load(p));
    setRev((r) => r + 1);
  };

  const tree = (dir: string, depth: number): ReactNode =>
    (kids.get(dir) ?? []).map((e) => {
      const path = dir ? `${dir}/${e.name}` : e.name;
      return (
        <div key={path}>
          <button className={`node ${sel?.path === path ? "sel" : ""}`} style={{ paddingLeft: 10 + depth * 14 }} title={path}
            onClick={() => (e.dir ? toggle(path) : setSel({ path, size: e.size }))}>
            <span className="tw">{e.dir ? (open.has(path) ? "▾" : "▸") : ""}</span>{e.name}
          </button>
          {e.dir && open.has(path) && tree(path, depth + 1)}
        </div>
      );
    });

  return (
    <div className="view split files">
      <nav className="side">
        <div className="side-head"><span>agent folder · {profile}</span><button title="refresh" onClick={refresh}>↻</button></div>
        {tree("", 0)}
      </nav>
      {sel ? <FilePane profile={profile} sel={sel} rev={rev} />
        : <div className="dim empty pad">select a file to view it.</div>}
    </div>
  );
}
