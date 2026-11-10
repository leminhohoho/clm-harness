import { useEffect, useState } from "react";
import { ChatView } from "./ChatView";
import { ContextView } from "./ContextView";
import { FilesView } from "./FilesView";
import { SettingsPanel, type Profile } from "./SettingsPanel";
import { TraceView } from "./TraceView";
import { useEvents } from "./ws";

const TABS = ["chat", "trace", "context", "files"] as const;
type Tab = (typeof TABS)[number];

export function App() {
  const [profiles, setProfiles] = useState<Profile[] | null>(null);
  const [profile, setProfile] = useState<string | null>(localStorage.getItem("profile"));
  const { events, metrics, connected } = useEvents(profile);
  const [tab, setTab] = useState<Tab>("chat");
  const [panel, setPanel] = useState(false);
  const [theme, setTheme] = useState(document.documentElement.dataset.theme ?? "light");

  const refresh = () => void fetch("/api/profiles").then((r) => r.json()).then(setProfiles, () => {});
  useEffect(refresh, []);

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
  }, [theme]);

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") return setPanel(false);
      if (!e.altKey) return;
      const act = { KeyT: () => setTab((t) => TABS[(TABS.indexOf(t) + 1) % TABS.length]), KeyS: () => setPanel((p) => !p),
        KeyD: () => setTheme((t) => (t === "dark" ? "light" : "dark")) }[e.code];
      if (act) {
        e.preventDefault();
        act();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const state = metrics?.m.agent.state ?? "idle";
  const view = !profile ? null
    : tab === "chat" ? <ChatView events={events} profile={profile} />
    : tab === "trace" ? <TraceView events={events} state={state} profile={profile} />
    : tab === "context" ? <ContextView events={events} profile={profile} />
    : <FilesView profile={profile} />;

  return (
    <div className="app">
      <header>
        <span className="brand">clm-harness</span>
        <button className="chip-profile" onClick={() => setPanel(true)} title={connected ? state : "disconnected"}>
          <i className={`dot ${connected ? state : "disconnected"}`} />{profile ?? "no profile"}
        </button>
        {TABS.map((t) => (
          <button key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
            {t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
        <span className="spacer" />
        <span className="dim hint">alt+t tab · alt+s settings · alt+d theme</span>
        <button className="gear" title="settings (alt+s)" onClick={() => setPanel((p) => !p)}>⚙</button>
      </header>
      <main>
        {view ? <div key={profile} className="view">{view}</div> : profiles && (
          <div className="center dim">
            no profiles yet
            <button className="primary" onClick={() => setPanel(true)}>Open settings</button>
          </div>
        )}
        {panel && (
          <SettingsPanel profiles={profiles ?? []} current={profile} onSwitch={setProfile} refresh={refresh}
            metrics={metrics} theme={theme} onTheme={() => setTheme(theme === "dark" ? "light" : "dark")}
            onClose={() => setPanel(false)} />
        )}
      </main>
    </div>
  );
}
