import hljs from "highlight.js/lib/common";
import { useMemo } from "react";
import { Md, useStickyScroll } from "./ChatView";
import { api, fmtCost, fmtDur, fmtTok, pct, type Ev } from "./derive";

function Code({ code }: { code: string }) {
  const html = useMemo(() => hljs.highlight(code, { language: "python" }).value, [code]);
  return <pre className="code hljs" dangerouslySetInnerHTML={{ __html: html }} />;
}

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

function Line({ e }: { e: Ev }) {
  switch (e.type) {
    case "user_message":
      return <div className="t user"><b>you</b> · msg #{e.id} · {e.text.length} chars{e.attachments.length ? ` · ${e.attachments.join(", ")}` : ""}</div>;
    case "notification":
      return <div className="t dim">{e.text}</div>;
    case "state":
      return <div className="t state">{e.state}</div>;
    case "llm_reasoning":
      return <div className="t reasoning"><Md text={e.text} /></div>;
    case "code":
      return <div className="t"><Code code={e.code} /></div>;
    case "run_result":
      return <div className="t"><Output text={e.output} /></div>;
    case "context_edit":
      return <div className={`t ${e.ok ? "accent" : "err"}`}>✎ context {e.receipt}</div>;
    case "job_update":
      return <div className="t dim">job #{e.id} {e.status}{e.exit != null ? ` · exit ${e.exit}` : ""}{e.cmd ? ` · ${e.cmd}` : ""}</div>;
    case "usage":
      return (
        <div className="t usage">
          {e.scope} · {e.model} · {fmtTok(e.tin)} in · {fmtTok(e.tout)} out · {e.tin ? pct(e.cached / e.tin) : "0%"} cached · {fmtCost(e.cost)} · {fmtDur(e.latency)}
        </div>
      );
    case "agent_message":
      return <div className="t sent">→ sent #{e.id}{e.files.length ? ` · ${e.files.map((f) => f.name).join(", ")}` : ""}<Md text={e.text} /></div>;
    case "message_opened":
      return <div className="t dim">opened msg #{e.id}</div>;
    case "error":
      return <div className="t err">✗ {e.text}</div>;
  }
}

export function TraceView({ events, state, profile }: { events: Ev[]; state: string; profile: string }) {
  const { ref, onScroll } = useStickyScroll(events.length);
  return (
    <div className="view">
      <div className="toolbar">
        <span className="dim">read-only trace · {events.length} events</span>
        <button className="stop" disabled={state === "idle"} onClick={() => void fetch(`${api(profile)}/agent/stop`, { method: "POST" })}>
          ■ Stop
        </button>
      </div>
      <div className="scroll trace" ref={ref} onScroll={onScroll}>
        {events.map((e) => <Line key={e.seq} e={e} />)}
      </div>
    </div>
  );
}
