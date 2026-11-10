import { useEffect, useState } from "react";
import { mergeEvents, type Ev, type Metrics } from "./derive";

/** A profile's event stream: backlog from the last seen seq, then live events; reconnects and resumes after drops. */
export function useEvents(profile: string | null) {
  const [events, setEvents] = useState<Ev[]>([]);
  const [metrics, setMetrics] = useState<{ m: Metrics; at: number } | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    setEvents([]);
    setMetrics(null);
    setConnected(false);
    if (!profile) return;
    let ws: WebSocket;
    let stopped = false;
    let last = 0;
    let retry = 0;
    let queue: Ev[] = [];
    let flushing = 0;
    const flush = () => {
      const batch = queue;
      queue = [];
      flushing = 0;
      setEvents((ev) => mergeEvents(ev, batch));
    };
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws/${encodeURIComponent(profile)}?after=${last}`);
      ws.onopen = () => setConnected(true);
      ws.onmessage = (msg) => {
        const d = JSON.parse(msg.data) as Ev | Metrics;
        if (d.type === "metrics") return setMetrics({ m: d, at: Date.now() });
        last = Math.max(last, d.seq);
        queue.push(d);
        flushing ||= window.setTimeout(flush, 30);
      };
      ws.onclose = (e) => {
        setConnected(false);
        if (!stopped && e.code !== 4404) retry = window.setTimeout(connect, 1000); // 4404: profile is gone
      };
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(retry);
      clearTimeout(flushing);
      ws.close();
    };
  }, [profile]);

  return { events, metrics, connected };
}
