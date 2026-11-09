type Base = { seq: number; ts?: number };
export type UserMsg = Base & { type: "user_message"; id: number; text: string; attachments: string[] };
export type AgentMsg = Base & { type: "agent_message"; id: number } & (
  | { backend: "markdown"; text: string }
  | { backend: "marimo"; path: string; url: string }
);
export type Ev =
  | UserMsg
  | AgentMsg
  | (Base & { type: "llm_reasoning"; text: string })
  | (Base & { type: "code"; code: string })
  | (Base & { type: "run_result"; output: string })
  | (Base & { type: "notification"; nseq: number; text: string })
  | (Base & { type: "context_edit"; ok: boolean; receipt: string; before?: number; after?: number })
  | (Base & { type: "job_update"; id: number; status: string; cmd?: string; exit?: number })
  | (Base & { type: "state"; state: string })
  | (Base & {
      type: "usage"; scope: string; model: string; tin: number; tout: number; cached: number;
      cost: number | null; latency: number;
    })
  | (Base & { type: "message_opened"; id: number })
  | (Base & { type: "error"; text: string; send?: number; log?: string });

export type Metrics = {
  type: "metrics";
  context: { tokens: number; budget: number; pct: number; edits: number; freed: number };
  cost: { total: number; main: number; sub: number; unpriced_calls: number };
  tokens: { in: number; out: number; cached: number; cache_rate: number };
  calls: { main: number; sub: number; last_latency: number | null };
  runs: { total: number; detached: number; jobs_running: number };
  agent: { state: string; uptime: number };
};

export const fmtTok = (n: number) =>
  n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`;

export const fmtCost = (x: number | null | undefined) =>
  x == null ? "—" : `$${x.toFixed(x < 0.1 && x > 0 ? 3 : 2)}`;

export function fmtDur(s: number) {
  if (s < 60) return `${s.toFixed(1)}s`;
  s = Math.floor(s);
  if (s < 3600) return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}

export const pct = (x: number) => `${Math.round(x * 100)}%`;

export type ChatItem = (UserMsg & { opened: boolean }) | AgentMsg;

export function chatItems(events: Ev[]): ChatItem[] {
  const opened = new Set(events.flatMap((e) => (e.type === "message_opened" ? [e.id] : [])));
  return events.flatMap((e): ChatItem[] =>
    e.type === "user_message" ? [{ ...e, opened: opened.has(e.id) }] : e.type === "agent_message" ? [e] : []);
}

export function runningJobs(events: Ev[]): number[] {
  const live = new Set<number>();
  for (const e of events) {
    if (e.type !== "job_update") continue;
    if (e.status === "running" || e.status === "detached") live.add(e.id);
    else live.delete(e.id);
  }
  return [...live].sort((a, b) => a - b);
}

export function mergeEvents(events: Ev[], batch: Ev[]): Ev[] {
  let last = events.length ? events[events.length - 1].seq : 0;
  const fresh = batch.filter((e) => e.seq > last && ((last = e.seq), true));
  return fresh.length ? events.concat(fresh) : events;
}
