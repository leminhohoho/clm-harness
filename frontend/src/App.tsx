import { useEffect, useState } from "react";
import { ChatView } from "./ChatView";
import { ContextView } from "./ContextView";
import { fmtCost, fmtTok, pct, runningJobs } from "./derive";
import { MetricsPanel } from "./MetricsPanel";
import { TraceView } from "./TraceView";
import { useEvents } from "./ws";

const TABS = ["chat", "trace", "context"] as const;
type Tab = (typeof TABS)[number];

export function App() {
  const { events, metrics, connected } = useEvents();
  const [tab, setTab] = useState<Tab>("chat");
  const [panel, setPanel] = useState(false);
  const [theme, setTheme] = useState(document.documentElement.dataset.theme ?? "light");

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("theme", theme);
  }, [theme]);

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") return setPanel(false);
      if (!e.altKey) return;
      const act = { KeyT: () => setTab((t) => TABS[(TABS.indexOf(t) + 1) % TABS.length]), KeyM: () => setPanel((p) => !p),
        KeyD: () => setTheme((t) => (t === "dark" ? "light" : "dark")) }[e.code];
      if (act) {
        e.preventDefault();
        act();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const m = metrics?.m;
  const state = m?.agent.state ?? "idle";
  const jobs = runningJobs(events);

  return (
    <div className="app">
      <header>
        <span className="brand">clm-harness</span>
        {TABS.map((t) => (
          <button key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
            {t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
        <span className="spacer" />
        <span className="dim hint">alt+t tab · alt+m metrics · alt+d theme</span>
        <button onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>◐ {theme}</button>
      </header>
      <main>
        {tab === "chat" ? <ChatView events={events} theme={theme} />
          : tab === "trace" ? <TraceView events={events} state={state} /> : <ContextView events={events} />}
        {panel && m && metrics && <MetricsPanel m={m} at={metrics.at} onClose={() => setPanel(false)} />}
      </main>
      <footer onClick={() => setPanel((p) => !p)} title="toggle metrics (alt+m)">
        <span className={`state ${connected ? state : "disconnected"}`}>
          <i className="dot" />{connected ? state : "disconnected"}
          {connected && jobs.length ? ` · job ${jobs.map((j) => `#${j}`).join(" ")}` : ""}
        </span>
        {m && (
          <>
            <span>ctx {fmtTok(m.context.tokens)} / {fmtTok(m.context.budget)} · {Math.round(m.context.pct)}%</span>
            <span>{fmtCost(m.cost.total)}</span>
            <span>calls {m.calls.main}+{m.calls.sub}</span>
            <span>cache {pct(m.tokens.cache_rate)}</span>
          </>
        )}
      </footer>
    </div>
  );
}
