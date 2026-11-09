import { useEffect, useState } from "react";
import { mergeEvents, type Ev, type Metrics } from "./derive";

/** Event stream: backlog from the last seen seq, then live events; reconnects and resumes after drops. */
export function useEvents() {
  const [events, setEvents] = useState<Ev[]>([]);
  const [metrics, setMetrics] = useState<{ m: Metrics; at: number } | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
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
      ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws?after=${last}`);
      ws.onopen = () => setConnected(true);
      ws.onmessage = (msg) => {
        const d = JSON.parse(msg.data) as Ev | Metrics;
        if (d.type === "metrics") return setMetrics({ m: d, at: Date.now() });
        last = Math.max(last, d.seq);
        queue.push(d);
        flushing ||= window.setTimeout(flush, 30);
      };
      ws.onclose = () => {
        setConnected(false);
        if (!stopped) retry = window.setTimeout(connect, 1000);
      };
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(retry);
      clearTimeout(flushing);
      ws.close();
    };
  }, []);

  return { events, metrics, connected };
}
