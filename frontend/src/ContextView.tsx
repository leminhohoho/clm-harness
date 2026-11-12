import { memo, useEffect, useMemo, useState } from "react";
import { api, contextEdits, foldDiff, type DiffLine, type Ev } from "./derive";
import { useHistory } from "./ws";

const TYPES = ["context_edit"];

const CLS = { " ": "ln", "+": "ln add", "-": "ln del" } as const;

const Line = ({ l }: { l: DiffLine }) => <span className={CLS[l.op]}>{l.text || " "}</span>;

function Fold({ count, lines }: { count: number; lines: DiffLine[] }) {
  const [open, setOpen] = useState(false);
  if (open) return <>{lines.map((l, i) => <Line key={i} l={l} />)}</>;
  const what = lines[0].op === "-" ? "removed" : "unchanged";
  return <span className="ln fold" onClick={() => setOpen(true)}>⋯ {count} {what} lines hidden · click to expand</span>;
}

export const ContextView = memo(function ContextView({ live, profile }: { live: Ev[] | null; profile: string }) {
  const { items, loadOlder, loading, more } = useHistory(profile, TYPES, live, 1000);
  const edits = useMemo(() => contextEdits(items), [items]);
  const [sel, setSel] = useState<number>();
  const cur = edits.find((e) => e.n === sel) ?? edits[0];
  const [diff, setDiff] = useState<{ n: number; lines: DiffLine[] }>();

  useEffect(() => {
    if (!cur) return;
    let live = true;
    fetch(`${api(profile)}/context-edits/${cur.n}`).then((r) => (r.ok ? r.json() : undefined)).then((d) => live && setDiff(d));
    return () => void (live = false);
  }, [cur?.n]);

  if (!cur) return <div className="view"><div className="dim empty pad">{!live || more ? "loading…" : "no context edits yet — they appear here when the agent rewrites context.md."}</div></div>;
  const lines = diff?.n === cur.n ? diff.lines : [];
  const added = lines.filter((l) => l.op === "+").length, removed = lines.filter((l) => l.op === "-").length;

  return (
    <div className="view split">
      <nav className="side">
        {edits.map((e) => (
          <button key={e.n} className={`edit ${e === cur ? "sel" : ""} ${e.ok ? "" : "rej"}`} onClick={() => setSel(e.n)}>
            <span className="t">edit #{e.n} · {e.ts ? new Date(e.ts * 1000).toTimeString().slice(0, 5) : ""}</span>
            <span className="s">{e.receipt.replace(/^\[context edited\] /, "")}</span>
          </button>
        ))}
        {more && <button className="edit older" disabled={loading} onClick={() => void loadOlder()}>{loading ? "loading…" : "load older edits"}</button>}
      </nav>
      <div className="scroll file">
        <div className="bar">context.md after edit #{cur.n} · <span className="ok">+{added}</span> <span className="err">−{removed}</span> lines</div>
        <pre className="raw">
          {foldDiff(lines).map((r, i) => ("fold" in r ? <Fold key={`${cur.n}-${i}`} count={r.fold} lines={r.lines} /> : <Line key={i} l={r} />))}
        </pre>
      </div>
    </div>
  );
});
