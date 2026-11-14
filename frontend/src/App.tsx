import { useEffect, useState } from "react";
import { ChatView } from "./ChatView";
import { ContextView } from "./ContextView";
import { notifyBody, shouldNotify, type Profile } from "./derive";
import { AgentSettings, NewAgent } from "./Dialogs";
import { FilesView } from "./FilesView";
import { Avatar, Sidebar, working, type Notify } from "./Sidebar";
import { TraceView } from "./TraceView";
import { useEvents, useNotify } from "./ws";

const TABS = ["chat", "trace", "context", "files"] as const;
type Tab = (typeof TABS)[number];
type Dialog = { kind: "new" } | { kind: "settings"; name: string } | null;
/** Desktop browsers in a secure context; phones only show notifications through a service worker (non-goal). */
const CAN_NOTIFY = "Notification" in window && isSecureContext && !matchMedia("(pointer: coarse)").matches;
const notifyState = (): Notify => Notification.permission === "denied" ? "blocked"
  : Notification.permission === "granted" && localStorage.getItem("notify") === "on" ? "on" : "off";

export function App() {
  const [profiles, setProfiles] = useState<Profile[] | null>(null);
  const [profile, setProfile] = useState<string | null>(localStorage.getItem("profile"));
  const { live, metrics, connected } = useEvents(profile);
  const [tab, setTab] = useState<Tab>("chat");
  const [dialog, setDialog] = useState<Dialog>(null);
  const [pop, setPop] = useState(false);
  const [pane, setPane] = useState<"list" | "agent">("agent"); // mobile only: the list opens from ←
  const [theme, setTheme] = useState(document.documentElement.dataset.theme ?? "light");
  const [notify, setNotify] = useState(CAN_NOTIFY ? notifyState() : null);

  useNotify(notify === "on", (m) => {
    const phone = matchMedia("(max-width: 599px)").matches;
    const view = { visible: document.visibilityState === "visible", focused: document.hasFocus(), agent: profile, tab, pane: phone ? pane : "agent" as const };
    if (!shouldNotify(m, view)) return;
    const n = new Notification(m.agent, { body: notifyBody(m.text, m.files), icon: "/icon-192.png", tag: m.agent });
    n.onclick = () => (window.focus(), setProfile(m.agent), setTab("chat"), setPane("agent"), n.close());
  });

  const toggleNotify = async () => {
    if (notify === "on") localStorage.removeItem("notify");
    else if ((await Notification.requestPermission()) === "granted") localStorage.setItem("notify", "on");
    setNotify(notifyState());
  };

  const refresh = () => void fetch("/api/profiles").then((r) => r.json()).then(setProfiles, () => {});
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 10_000); // keeps the other agents' states current
    return () => clearInterval(t);
  }, []);

  // fall back to the first profile when the stored one is missing or was deleted
  useEffect(() => {
    if (profiles && !profiles.some((p) => p.name === profile)) setProfile(profiles[0]?.name ?? null);
  }, [profiles]);

  useEffect(() => {
    if (profile) localStorage.setItem("profile", profile);
  }, [profile]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("theme", theme);
    const surface = getComputedStyle(document.documentElement).getPropertyValue("--surface").trim();
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", surface);
  }, [theme]);

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") return setDialog(null), setPop(false);
      if (!e.altKey) return;
      const act = {
        KeyT: () => setTab((t) => TABS[(TABS.indexOf(t) + 1) % TABS.length]),
        KeyD: () => setTheme((t) => (t === "dark" ? "light" : "dark")),
        KeyN: () => setDialog({ kind: "new" }),
        KeyS: () => profile && setDialog({ kind: "settings", name: profile }),
        KeyM: () => setPop((p) => !p),
      }[e.code];
      if (act) {
        e.preventDefault();
        act();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [profile]);

  const state = connected ? metrics?.m.agent.state ?? "idle" : "disconnected";
  const cur = profiles?.find((p) => p.name === profile);
  const editing = dialog?.kind === "settings" && profiles?.find((p) => p.name === dialog.name);
  const view = !cur ? null
    : tab === "chat" ? <ChatView live={live} profile={cur.name} />
    : tab === "trace" ? <TraceView live={live} profile={cur.name} />
    : tab === "context" ? <ContextView live={live} profile={cur.name} />
    : <FilesView profile={cur.name} />;

  return (
    <div className={`app ${pane === "agent" && cur ? "pane-agent" : ""}`}>
      <Sidebar profiles={profiles ?? []} current={profile} state={state} metrics={metrics} pop={pop} theme={theme}
        notify={notify} onNotify={toggleNotify}
        onSelect={(name) => (setProfile(name), setPane("agent"))} onNew={() => setDialog({ kind: "new" })}
        onSettings={(name) => setDialog({ kind: "settings", name })} onPop={() => setPop((p) => !p)}
        onTheme={() => setTheme(theme === "dark" ? "light" : "dark")} />
      <section className="pane">
        {cur ? (
          <>
            <header className="agent-head">
              <button className="back" title="agents" onClick={() => setPane("list")}>←</button>
              <Avatar name={cur.name} state={state} />
              <span className="who">
                <b>{cur.name}</b>
                <span className={working(state) ? "accent" : state === "disconnected" ? "err" : "dim"}>{working(state) ? `${state}…` : state}</span>
              </span>
              <nav className="tabs">
                {TABS.map((t) => (
                  <button key={t} className={`tab ${tab === t ? "active" : ""}`} title="alt+t" onClick={() => setTab(t)}>
                    {t[0].toUpperCase() + t.slice(1)}
                  </button>
                ))}
              </nav>
              <button className="more" title="agent settings (alt+s)" onClick={() => setDialog({ kind: "settings", name: cur.name })}>⋯</button>
            </header>
            <main><div key={`${cur.name}/${tab}`} className="view fade">{view}</div></main>
          </>
        ) : profiles?.length === 0 && (
          <div className="center dim">
            no agents yet
            <button className="primary" onClick={() => setDialog({ kind: "new" })}>New agent</button>
          </div>
        )}
      </section>
      {dialog?.kind === "new" && (
        <NewAgent onClose={() => setDialog(null)}
          onCreated={(name) => (refresh(), setProfile(name), setPane("agent"), setDialog(null))} />
      )}
      {editing && (
        <AgentSettings key={editing.name} agent={editing.name === profile ? { ...editing, state } : editing}
          onChanged={refresh} onDeleted={() => (refresh(), setDialog(null))} onClose={() => setDialog(null)} />
      )}
    </div>
  );
}
