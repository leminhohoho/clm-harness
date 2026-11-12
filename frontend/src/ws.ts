import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, mergeEvents, type Ev, type Metrics } from "./derive";

const TAIL = 300;

/**
 * A profile's events: one REST tail page, then the WS from its last seq (reconnects resume from there).
 * `live` is null until the tail page arrived; its reference changes only when a batch is flushed.
 */
export function useEvents(profile: string | null) {
  const [live, setLive] = useState<{ profile: string; events: Ev[] } | null>(null);
  const [metrics, setMetrics] = useState<{ m: Metrics; at: number } | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    setLive(null);
    setMetrics(null);
    setConnected(false);
    if (!profile) return;
    let ws: WebSocket | undefined;
    let stopped = false;
    let last = 0;
    let retry = 0;
    let queue: Ev[] = [];
    let flushing = 0;
    const flush = () => {
      const batch = queue;
      queue = [];
      flushing = 0;
      setLive((l) => l && { profile, events: mergeEvents(l.events, batch) });
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
    const seed = async () => {
      try {
        const r = await fetch(`${api(profile)}/events?limit=${TAIL}`);
        if (r.status === 404 || stopped) return;
        if (!r.ok) throw new Error(`${r.status}`);
        const { events } = (await r.json()) as { events: Ev[] };
        if (stopped) return;
        last = events.at(-1)?.seq ?? 0;
        setLive({ profile, events });
        connect();
      } catch {
        if (!stopped) retry = window.setTimeout(seed, 1000);
      }
    };
    void seed();
    return () => {
      stopped = true;
      clearTimeout(retry);
      clearTimeout(flushing);
      ws?.close();
    };
  }, [profile]);

  return { live: live?.profile === profile ? live.events : null, metrics, connected };
}

/**
 * One view's history: older pages of `types` (null = all) fetched before the live window, plus the matching live events.
 * Mount it under a `key={profile}` so it starts fresh per profile.
 */
export function useHistory(profile: string, types: string[] | null, live: Ev[] | null, size: number) {
  const [older, setOlder] = useState<Ev[]>([]);
  const [more, setMore] = useState(true);
  const [loading, setLoading] = useState(false);
  const [primed, setPrimed] = useState(false);
  const busy = useRef(false);
  const alive = useRef(true);
  useEffect(() => ((alive.current = true), () => void (alive.current = false)), []);

  const own = useMemo(() => (!live ? [] : types ? live.filter((e) => types.includes(e.type)) : live), [live, types]);
  const items = useMemo(() => (older.length ? older.concat(own) : own), [older, own]);
  const before = older[0]?.seq ?? live?.[0]?.seq;

  const loadOlder = useCallback(async () => {
    if (busy.current || !more || before == null) return;
    busy.current = true;
    setLoading(true);
    try {
      const q = new URLSearchParams({ before: `${before}`, limit: `${size}`, ...(types && { types: types.join(",") }) });
      const r = await fetch(`${api(profile)}/events?${q}`);
      if (!r.ok || !alive.current) return;
      const p = (await r.json()) as { events: Ev[]; more: boolean };
      if (!alive.current) return;
      setOlder((o) => p.events.concat(o));
      setMore(p.more);
    } catch {
      // network hiccup: the next scroll to the top retries
    } finally {
      busy.current = false;
      if (alive.current) setLoading(false);
    }
  }, [profile, types, size, more, before]);

  // a thin live slice: fetch one older page before the list mounts, so it opens filled and at the bottom
  const ready = live !== null;
  useEffect(() => {
    if (ready) void (own.length < size ? loadOlder() : Promise.resolve()).then(() => setPrimed(true));
  }, [ready]);

  return { items, loadOlder, loading, primed, more: more && before != null, pivot: live?.[0]?.seq ?? 0 };
}

/** Rows whose events arrived live after mount animate once; prepended or virtually re-mounted rows don't. */
export function useFresh(live: Ev[] | null) {
  const seen = useRef<number | undefined>(undefined);
  const arrived = useRef(new Map<number, number>());
  if (live) {
    if (seen.current === undefined) seen.current = live.at(-1)?.seq ?? 0;
    for (let i = live.length - 1; i >= 0 && live[i].seq > seen.current; i--) arrived.current.set(live[i].seq, Date.now());
    seen.current = live.at(-1)?.seq ?? seen.current;
  }
  return (seq: number) => Date.now() - (arrived.current.get(seq) ?? 0) < 1000;
}
