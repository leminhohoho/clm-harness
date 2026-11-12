import hljs from "highlight.js/lib/common";
import { memo, useEffect, useMemo, useState, type ReactNode } from "react";
import { Virtuoso } from "react-virtuoso";
import { follow, Gap, Loading, Md, usePinnedBottom } from "./ChatView";
import { api, firstIndex, fmtCost, fmtDur, fmtTok, pct, traceCards, type Card, type Ev, type Usage } from "./derive";
import { useFresh, useHistory } from "./ws";

const Code = memo(({ code }: { code: string }) => {
  const html = useMemo(() => hljs.highlight(code, { language: "python" }).value, [code]);
  return <pre className="code hljs" dangerouslySetInnerHTML={{ __html: html }} />;
});

function Output({ text }: { text: string }) {
  const lines = text.split("\n");
  if (lines.length <= 30) return <pre className="out">{text}</pre>;
  return (
    <details className="out">
      <summary>{lines[0]} · {lines.length} lines</summary>
      <pre>{text}</pre>
    </details>
  );
}

const meta = (u: Usage) =>
  `${u.model} · ${fmtTok(u.tin)} in / ${fmtTok(u.tout)} out · ${u.tin ? pct(u.cached / u.tin) : "0%"} cached · ${fmtCost(u.cost)} · ${fmtDur(u.latency)}`;

function Box({ kind, label, info, children }: { kind: string; label: string; info?: ReactNode; children?: ReactNode }) {
  return (
    <div className={`card k-${kind}`}>
      <div className="kh"><span className="kl">{label}</span>{info != null && <span className="ki">{info}</span>}</div>
      {children}
    </div>
  );
}

function Body({ c }: { c: Card }) {
  if (c.kind === "thinking")
    return <Box kind="thinking" label="Thinking" info={c.usage && meta(c.usage)}>{c.text && <Md text={c.text} />}</Box>;
  if (c.kind === "exec")
    return (
      <Box kind="exec" label="Executing">
        {c.code != null && <Code code={c.code} />}
        {c.output != null ? <Output text={c.output} /> : <div className="dim">running…</div>}
      </Box>
    );
  const e = c.e;
  switch (e.type) {
    case "usage":
      return <Box kind="llm" label="LLM call" info={meta(e)} />;
    case "notification":
      return <Box kind="note" label="Notification"><div className="dim pre">{e.text}</div></Box>;
    case "user_message":
      return <Box kind="msg" label={`Message #${e.id}`} info={`${e.text.length} chars${e.attachments.length ? ` · ${e.attachments.join(", ")}` : ""}`} />;
    case "agent_message":
      return (
        <Box kind="sent" label={`Sent #${e.id}`} info={e.files.length ? e.files.map((f) => f.name).join(", ") : undefined}>
          <Md text={e.text} />
        </Box>
      );
    case "job_update":
      return <Box kind="job" label={`Job #${e.id}`} info={`${e.status}${e.exit != null ? ` · exit ${e.exit}` : ""}${e.cmd ? ` · ${e.cmd}` : ""}`} />;
    case "context_edit":
      return <Box kind={e.ok ? "ctx" : "ctx bad"} label="Context edit"><div className={e.ok ? "accent" : "err"}>{e.receipt}</div></Box>;
    case "message_opened":
      return <Box kind="opened" label={`opened msg #${e.id}`} />;
    case "error":
      return <Box kind="error" label="Error"><div className="err pre">{e.text}</div></Box>;
    default:
      return null;
  }
}

type CardProps = { c: Card; fresh: boolean };
// cards are rebuilt on every update; their fields are event values that never change, so compare those
const sameCard = (a: CardProps, b: CardProps) => {
  const x = a.c as Record<string, unknown>, y = b.c as Record<string, unknown>, keys = Object.keys(x);
  return a.fresh === b.fresh && keys.length === Object.keys(y).length && keys.every((k) => x[k] === y[k]);
};
const CardRow = memo(({ c, fresh }: CardProps) => <div className={`vrow ${fresh ? "fresh" : ""}`}><Body c={c} /></div>, sameCard);

/** The agent's current state as a ticking card, shown while it isn't idle. */
function LiveCard({ state, since }: { state: string; since?: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="vrow live">
      <Box kind="state" label={`${state}…`} info={since ? fmtDur(Math.max(0, now / 1000 - since)) : undefined} />
    </div>
  );
}

export const TraceView = memo(function TraceView({ live, profile }: { live: Ev[] | null; profile: string }) {
  const { items, loadOlder, loading, primed, pivot } = useHistory(profile, null, live, 300);
  const cards = useMemo(() => traceCards(items), [items]);
  const st = useMemo(() => live?.findLast((e) => e.type === "state"), [live]);
  const state = st?.type === "state" ? st.state : "idle";
  const fresh = useFresh(live);
  const pin = usePinnedBottom();

  return (
    <div className="view">
      <div className="toolbar">
        <span className="dim">read-only trace · {items.length} events loaded</span>
        <button className="stop" disabled={state === "idle"} onClick={() => void fetch(`${api(profile)}/agent/stop`, { method: "POST" })}>
          ■ Stop
        </button>
      </div>
      {!primed ? <div className="scroll dim empty pad">loading…</div> : (
        <Virtuoso
          className="scroll list trace"
          ref={pin.list}
          scrollerRef={pin.scroller}
          data={cards}
          context={{ loading }}
          firstItemIndex={firstIndex(cards, pivot)}
          initialTopMostItemIndex={{ index: "LAST", align: "end" }}
          followOutput={follow}
          atBottomThreshold={60}
          increaseViewportBy={{ top: 800, bottom: 400 }}
          startReached={loadOlder}
          computeItemKey={(_, c) => c.seq}
          components={{ Header: Loading, Footer: Gap }}
          itemContent={(_, c) => <CardRow c={c} fresh={fresh(c.seq)} />}
        />
      )}
      {state !== "idle" && <LiveCard state={state} since={st?.ts} />}
    </div>
  );
});
