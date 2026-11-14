type Base = { seq: number; ts?: number };
export type UserMsg = Base & { type: "user_message"; id: number; text: string; attachments: string[]; opened?: boolean };
export type FileRef = { name: string; size: number };
export type AgentMsg = Base & { type: "agent_message"; id: number; text: string; files: FileRef[] };
export type ContextEdit = Base & { type: "context_edit"; ok: boolean; receipt: string; before?: number; after?: number; n?: number };
export type Ev =
  | UserMsg
  | AgentMsg
  | (Base & { type: "llm_reasoning"; text: string })
  | (Base & { type: "code"; code: string })
  | (Base & { type: "run_result"; output: string })
  | (Base & { type: "notification"; nseq: number; text: string })
  | ContextEdit
  | (Base & { type: "job_update"; id: number; status: string; cmd?: string; exit?: number })
  | (Base & { type: "state"; state: string })
  | Usage
  | (Base & { type: "message_opened"; id: number })
  | (Base & { type: "error"; text: string });

export type Usage = Base & {
  type: "usage"; scope: string; model: string; tin: number; tout: number; cached: number; cost: number | null; latency: number;
};

export type Profile = { name: string; model: string; state: string };

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

export function fmtSize(n: number) {
  const units = ["KB", "MB", "GB"];
  if (n < 1024) return `${n} B`;
  let i = -1;
  while (n >= 1024 && i < units.length - 1) (n /= 1024), i++;
  return `${n.toFixed(1)} ${units[i]}`;
}

export const timeOf = (ts?: number) => (ts ? new Date(ts * 1000).toTimeString().slice(0, 5) : "");

export const isImage = (name: string) => /\.(png|jpe?g|gif|webp|svg)$/i.test(name);

export const api = (profile: string) => `/api/p/${encodeURIComponent(profile)}`;

export const rawUrl = (profile: string, path: string, download = false) =>
  `${api(profile)}/raw/${path.split("/").map(encodeURIComponent).join("/")}${download ? "?download=1" : ""}`;

/** One row of the /ws stream: an agent_message from any agent. */
export type Push = { agent: string; seq: number; ts: number; text: string; files: FileRef[] };
export type View = { visible: boolean; focused: boolean; agent: string | null; tab: string; pane: "list" | "agent" };

/** Notify unless the user is already looking at that agent's chat. */
export const shouldNotify = (msg: { agent: string }, v: View) =>
  !(v.visible && v.focused && v.agent === msg.agent && v.tab === "chat" && v.pane === "agent");

/** One-line preview: markdown punctuation stripped, whitespace collapsed, cut at 140 chars; files summarised. */
export function notifyBody(text: string, files: FileRef[]) {
  const t = text.replace(/[*_`#>]/g, "").replace(/\s+/g, " ").trim();
  const body = t.length > 140 ? `${t.slice(0, 140).trimEnd()}…` : t;
  if (!files.length) return body;
  if (!body) return `📎 ${files.map((f) => f.name).join(", ")}`;
  return `${body} · 📎 ${files.length} file${files.length > 1 ? "s" : ""}`;
}

export type ChatItem = (UserMsg & { opened: boolean }) | AgentMsg;

/** Ids of messages the agent opened, from live message_opened events (paged rows carry their own `opened`). */
export const openedIds = (events: Ev[]) => new Set(events.flatMap((e) => (e.type === "message_opened" ? [e.id] : [])));

export function chatItems(events: Ev[], opened: Set<number>): ChatItem[] {
  return events.flatMap((e): ChatItem[] =>
    e.type === "user_message" ? [{ ...e, opened: !!e.opened || opened.has(e.id) }] : e.type === "agent_message" ? [e] : []);
}

/** Trace boxes, keyed by the seq of their first event. */
export type Card =
  | { kind: "thinking"; seq: number; usage?: Usage; text?: string }
  | { kind: "exec"; seq: number; code?: string; output?: string }
  | { kind: "event"; seq: number; e: Ev };

export function traceCards(events: Ev[]): Card[] {
  const cards: Card[] = [];
  const open: (Card & { kind: "exec" })[] = [];
  let think: (Card & { kind: "thinking" }) | undefined;
  for (const e of events) {
    if (e.type === "state") continue;
    if (e.type === "usage" && e.scope === "main") cards.push((think = { kind: "thinking", seq: e.seq, usage: e }));
    else if (e.type === "llm_reasoning") {
      if (think && think.text == null) think.text = e.text;
      else cards.push((think = { kind: "thinking", seq: e.seq, text: e.text }));
    } else if (e.type === "code") {
      const c = { kind: "exec" as const, seq: e.seq, code: e.code };
      cards.push(c);
      open.push(c);
      think = undefined;
    } else if (e.type === "run_result") {
      const c = open.pop();
      if (c) c.output = e.output;
      else cards.push({ kind: "exec", seq: e.seq, output: e.output });
    } else cards.push({ kind: "event", seq: e.seq, e });
  }
  return cards;
}

const BASE = 10_000_000;

/** Virtuoso firstItemIndex: rows older than the first live seq count down from BASE, so prepends stay anchored. */
export function firstIndex(rows: { seq: number }[], pivot: number) {
  const i = rows.findIndex((r) => r.seq >= pivot);
  return BASE - (i < 0 ? rows.length : i);
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

export const contextEdits = (events: Ev[]) =>
  events.filter((e): e is ContextEdit & { n: number } => e.type === "context_edit" && e.n != null).reverse();

export type DiffLine = { op: " " | "+" | "-"; text: string };
export type DiffRow = DiffLine | { fold: number; lines: DiffLine[] };

/** Collapse runs of more than `min` unchanged (head+tail `ctx` kept) or removed (head `ctx` kept) lines. */
export function foldDiff(lines: DiffLine[], ctx = 3, min = 8): DiffRow[] {
  const out: DiffRow[] = [];
  for (let i = 0; i < lines.length; ) {
    let j = i;
    while (j < lines.length && lines[j].op === lines[i].op) j++;
    const run = lines.slice(i, j), op = lines[i].op;
    if (op === "+" || run.length <= min) out.push(...run);
    else {
      const tail = op === " " ? ctx : 0;
      out.push(...run.slice(0, ctx), { fold: run.length - ctx - tail, lines: run.slice(ctx, run.length - tail) },
        ...run.slice(run.length - tail));
    }
    i = j;
  }
  return out;
}
