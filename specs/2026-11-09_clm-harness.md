# CLM harness with RLM input management — design

Status: draft for approval · 2026-11-09

## 1. Intent

A long-running, single-user agent harness that departs from request→response chat:

- **CLM (context as file)** — the agent's working context is mirrored to a file it may edit; edits are parsed back and replace the message list (facebookresearch/context-language-models).
- **RLM (input as variable)** — user inputs never enter context directly; the agent receives a stub and reads/slices/sub-queries the body through Python (alexzhang13/rlm).
- **Indirect communication** — user messages are *notifications*. The agent is never forced to reply; it talks to the user only by calling `h.send(...)` from Python.

Now: a testbed. Later: a research agent working with data and building models.

## 2. Goals / non-goals / anti-goals

Goals
- One tool (`python`) through which the agent does everything, using the `harness` library (`h`).
- Non-blocking notifications: the agent chooses when (or whether) to open a message.
- Agent idles (no LLM calls) after finishing a job until a trigger arrives.
- Long executions never starve the agent of notifications (60 s foreground limit, then detach).
- Agent messages as markdown (default) or marimo notebooks rendered with the app theme.
- Two UI views: chat (user ↔ what the agent sent) and read-only trace (everything the agent does).
- Session survives backend restarts; all state on disk in the agent folder.
- Live session metrics: context size, cost, token usage, call/run counts — visible in the UI at all times.
- Dark/light mode toggle across the whole UI, including marimo messages.

Non-goals (this iteration)
- Token-budget nudges, rollback-and-retry on overflow, edit gate beyond "parses cleanly" (CLM extras).
- Multi-session / multi-user, auth, public exposure (tailscale-only).
- Live marimo kernels (exports are static; JS charts stay interactive, widgets do not).
- Recursion deeper than one level (`h.llm` calls a plain LM, not another harness).

Anti-goals (failure even if it "works")
- The harness forcing a textual reply to every user message.
- User message bodies or attachments inlined into the agent's context.
- A long-running execution blocking delivery of notifications for longer than the foreground limit.

## 3. Constraints

- Backend: Python, uv, FastAPI, langchain (`langchain-openai` `ChatOpenAI` with an OpenAI-compatible endpoint from `.env`: `OPENAI_API_KEY`, `OPENAI_BASE_URL`).
- Agent folder: a uv project; code runs as fresh `uv run` processes with cwd = agent folder. No persistent REPL — Python variables do not survive between runs; persistence is via files.
- Docker compose with two services, `backend` and `frontend`, possibly driven against a remote daemon via `DOCKER_HOST`. Harness code is baked into images; the only bind mount is the agent folder `./data`. Compose resolves `./data` to an absolute path on the client, and the daemon's host creates or uses that path — so on a remote daemon the agent folder lives on the remote machine.
- Personal use, bound to `0.0.0.0`, reached over tailscale. No nginx.
- Repo conventions (AGENTS.md): TDD, minimal code, no auto-commit, no sub-agents by default.

## 4. Approaches considered

| | Approach | Trade-off | Outcome |
|---|---|---|---|
| **A** | Hand-rolled asyncio loop inside FastAPI; langchain only for model calls; library talks to harness via files in `.harness/` | Least code, everything inspectable on disk. Backend restart kills the in-flight step (detached jobs survive process restarts, not container restarts); loop resumes from disk. | **Chosen** |
| B | LangGraph agent with checkpointer + interrupts | Durable state & pause/resume for free, but its append-reducer message state fights CLM's whole-list replacement; checkpoint duplicates `.harness/`. | Rejected |
| C | Agent loop as a separate worker process; FastAPI as thin I/O layer | API/UI restarts never touch the agent; costs an extra long-lived process and file-based coordination. | Deferred — A migrates to C by moving the loop, not redesigning. |

## 5. File tree (overview of changes)

```
clm-harness/
├── .env                          # existing; add MODEL, SUB_MODEL, CONTEXT_BUDGET, PRICES
├── .gitignore                    # add data/ (agent folder)
├── compose.yaml                  # backend + frontend services
├── specs/
│   └── 2026-11-09_clm-harness.md # this document
├── backend/                      # uv project, FastAPI app
│   ├── Dockerfile                # python + uv; marimo available via `uv run --with marimo`
│   ├── pyproject.toml
│   ├── src/backend/
│   │   ├── app.py                # HTTP routes + WebSocket
│   │   ├── loop.py               # agent loop state machine (idle / thinking / executing)
│   │   ├── context.py            # render messages → context.md, parse back
│   │   ├── executor.py           # runs & jobs, 60 s foreground limit, detach
│   │   ├── notify.py             # notification log + delivery cursor
│   │   ├── outbox.py             # watch outbox.jsonl, marimo export; theme injected at serve time
│   │   ├── events.py             # events.jsonl append + WS broadcast
│   │   ├── metrics.py            # usage → cost, session aggregates, metrics snapshot
│   │   └── prompts/system.md     # system prompt (library docs, protocols)
│   └── tests/
├── lib/                          # `harness` package, injected into every agent run
│   ├── pyproject.toml
│   └── src/harness/__init__.py   # inbox, send, spawn, jobs, kill, llm (logs usage)
└── frontend/                     # React + Vite + TypeScript
    ├── Dockerfile                # runs Vite dev server, proxies /api and /ws to backend
    ├── package.json
    ├── vite.config.ts
    └── src/
        ├── App.tsx               # tabs: chat | trace, hotkeys, status bar, theme toggle
        ├── ChatView.tsx
        ├── TraceView.tsx
        ├── MetricsPanel.tsx      # full metrics breakdown
        ├── ws.ts                 # event stream client
        ├── theme.css             # JetBrains Mono, light + dark palettes as CSS variables
        └── marimo-theme.css      # light + dark variants, injected into marimo HTML on serve

clm-harness/data/                 # agent folder, gitignored; bind-mounted into backend at /agent
├── pyproject.toml, uv.lock       # agent's own uv project (`uv init` if missing)
├── …                             # agent's working files
└── .harness/                     # harness state = protocol between backend and library
    ├── context.md                # CLM mirror of the editable message list
    ├── events.jsonl              # append-only trace (source for both UI views)
    ├── notifications.jsonl       # every notification, with seq
    ├── state.json                # delivered-notification cursor, counters, metric aggregates
    ├── usage.jsonl               # h.llm sub-call usage, appended by the library
    ├── inbox/<id>/               # message.json, files/<attachments>, opened (marker)
    ├── outbox.jsonl              # h.send() appends here
    ├── outbox/<id>.html          # exported marimo messages (raw, unthemed)
    ├── runs/<n>.py               # every executed code cell, kept
    └── jobs/<id>/                # meta.json (pid, cmd, status, exit), out.log
```

## 6. Design

### 6.1 Agent loop (backend `loop.py`)

States: `idle` → `thinking` (LLM call) → `executing` (run) → `thinking` … → `idle`.

1. Before each LLM call, render the editable message list to `.harness/context.md`. System prompt is pinned (not in the file).
2. Call the model with one tool, `python(code: str)`. The assistant text is its reasoning (shown in trace). Record the call's usage (6.8).
3. **No tool call → idle.** The loop stops calling the model.
4. Tool call → executor runs the code (6.3). Tool result = execution output + context size readout + all pending notification stubs.
5. After the run, if `context.md` changed, parse it; on success replace the message list (system prompt untouched) and append a one-line receipt to the tool result; on parse failure discard the edit and say why.
6. Loop.

Triggers: any new notification. If idle → wake: append one `user`-role message containing all pending stubs, go to `thinking`. If busy → stays queued, delivered with the next tool result.

Stop control: `POST /api/agent/stop` cancels the in-flight LLM call / foreground wait and goes idle (does not kill detached jobs). *(Builder's addition, see 8.)*

### 6.2 Context file format (`context.md`)

CLM-style turn blocks: `[[CTX_TURN <i> role=<user|assistant|tool>]]` followed by the body. Assistant turns carry reasoning and the code of their tool call; tool turns carry the result. Requirements on the parser:
- Round-trip: render → parse with no edits yields an equivalent message list.
- Output must be a valid chat sequence: every assistant tool call followed by its result. If an edit removes one half of a pair, drop the orphan (or fold it into plain text) rather than fail.
- Tool-call ids may be regenerated on parse.

Exact delimiter syntax for code within assistant turns is left to the builder.

### 6.3 Execution (`executor.py`)

- Code is written to `.harness/runs/<n>.py` and run as `uv run python .harness/runs/<n>.py` with cwd = agent folder, the `harness` library (baked into the backend image at `/opt/harness-lib`) injected without touching the agent's `pyproject.toml` (e.g. `uv run --with-editable /opt/harness-lib`; builder confirms the mechanism).
- **Every run is a job from the start:** stdout+stderr go to `.harness/jobs/<id>/out.log`; process started in its own session.
- Foreground wait up to **60 s** (configurable). Finished → result is exit code, duration, output (head 5 000 + tail 5 000 chars when long). Still running → result is `detached as job #<id> after 60s` + output so far; completion later produces a notification.
- `h.spawn(cmd)` starts a shell command as a job immediately and returns its id.
- On backend startup, jobs marked running whose pid is gone are marked `lost` and notified.

### 6.4 Notifications (`notify.py`)

Appended to `notifications.jsonl` with a monotonically increasing seq; `state.json` holds the last delivered seq so nothing is lost or duplicated across restarts. Stub formats:

```
[notify] msg #12 from user · 3.4k chars · 1 attachment
[notify] job #7 finished · exit 0 · 2h03m · log .harness/jobs/7/out.log
[notify] job #7 lost (backend restarted)
[notify] send #5 failed · marimo export: <first error line>
```

Message bodies are never inlined (option B, for consistency) — even short ones.

### 6.5 The `harness` library (`lib/`)

Imported in agent code as `import harness as h`. Talks to the backend only through `.harness/` files (location from env `HARNESS_DIR`, set by the executor).

| API | Behaviour |
|---|---|
| `h.inbox[i]` → `.text`, `.attachments` (paths), `.ts` | Reads `inbox/<i>/`; writes the `opened` marker on first access. |
| `h.inbox.unopened()` | Ids not yet opened. |
| `h.send(content, backend="markdown")` | Appends to `outbox.jsonl`; returns send id. |
| `h.send(path, backend="marimo")` | `path` = notebook in the agent folder. |
| `h.spawn(cmd)` → id · `h.jobs` · `h.kill(id)` | Background jobs (6.3). |
| `h.llm(prompt \| list[prompt])` → `str \| list[str]` | RLM sub-call to `SUB_MODEL` via the OpenAI-compatible endpoint (creds passed in env); lists run concurrently. Appends each call's token usage to `usage.jsonl`. |

### 6.6 Outbox & marimo (`outbox.py`)

The backend watches `outbox.jsonl` (`watchfiles`), so sends from detached jobs appear live.
- markdown → event `agent_message` with the text.
- marimo → at send time, run `uv run --with marimo marimo export html <nb> -o .harness/outbox/<id>.html` in the agent folder (so notebooks use the agent's env and data) and emit `agent_message` referencing it. The stored HTML is unthemed; `GET /api/outbox/<id>.html?theme=dark|light` injects the matching `marimo-theme.css` variant on serve, so a theme toggle only reloads the iframe — no re-export. The frontend renders it in a sandboxed iframe. Export failure → `send failed` notification to the agent and an error event in trace.

### 6.7 Events & API (`events.py`, `app.py`)

`events.jsonl` is append-only, each event `{seq, ts, type, …}`. Types at minimum: `user_message`, `agent_message`, `llm_reasoning`, `code`, `run_result`, `notification`, `context_edit` (receipt + size before/after), `job_update`, `state` (idle/thinking/executing), `usage` (per LLM call, main or sub: model, tokens in/out/cached, cost, latency), `error`. Additionally the WS pushes an ephemeral (not persisted) `metrics` snapshot whenever aggregates change.

| Route | Purpose |
|---|---|
| `POST /api/messages` (multipart: text + files) | Create inbox entry → notification → event. |
| `WS /ws?after=<seq>` | Backlog from seq, then live events. |
| `GET /api/outbox/<id>.html?theme=dark\|light` | Exported marimo message, themed on serve. |
| `GET /api/metrics` | Current metrics snapshot (initial load). |
| `GET /api/inbox/<id>/files/<name>` | User's own attachments (chat view links). |
| `POST /api/agent/stop` | 6.1 stop control. |

### 6.8 Metrics (`metrics.py`)

Sources: main-loop calls (usage from the model response) and `h.llm` sub-calls (`usage.jsonl`, watched like the outbox). Each call → one `usage` event; aggregates kept in `state.json` so totals survive restarts. Session = the agent folder's lifetime (no reset this iteration).

Cost = tokens × per-model prices from `PRICES` (JSON, USD per 1M tokens: `{"<model>": {"in": …, "out": …, "cached": …}}`). A model without a price still counts tokens; its cost shows as `—`.

Snapshot fields:

| Metric | Detail |
|---|---|
| context | current tokens / `CONTEXT_BUDGET` (%); context edits count, tokens freed by edits |
| cost | session total; split main / sub |
| tokens | in / out / cached totals; cache hit rate |
| calls | LLM calls main / sub; last call latency |
| runs | runs executed, runs detached, jobs running |
| agent | state, uptime |

The agent itself only sees the context size readout in each tool result (unchanged from 6.1).

### 6.9 Frontend

React + Vite + TypeScript, served by the Vite dev server (proxying `/api`, `/ws`), single URL `http://<server>:5173`.

```
 clm-harness   [chat]  trace                                       ◐ dark
─────────────────────────────────────────────────────────────────────────────
 you    › here's the sales csv, find seasonality            [sales.csv] ✓opened
 agent  › Weekly seasonality is strong; Dec spike is 3.1×.
          ┌ marimo ─────────────────────────────┐
          │  [interactive chart]                │
          └─────────────────────────────────────┘
─────────────────────────────────────────────────────────────────────────────
 › _
─────────────────────────────────────────────────────────────────────────────
 ● working · job #7 │ ctx 12.4k/32k 39% │ $0.42 │ calls 38+112 │ cache 71%
```

Metrics panel (toggled by hotkey or clicking the status bar):

```
 metrics ────────────────────────────────────────────────
 context    12.4k / 32k (39%)    edits 4 · freed 21.3k
 cost       $0.42                main $0.37 · sub $0.05
 tokens     in 1.21M · out 38k   cached 71%
 calls      main 38 · sub 112    last 2.4s
 runs       41 · detached 2      jobs running 1
 agent      working              up 3h12m
```

- **Chat**: user messages (with attachments and opened marker) and `agent_message` events only. Markdown rendered; marimo as themed iframe.
- **Trace**: read-only, append-only rendering of all events — reasoning, code, results, notifications, context-edit receipts, job updates, per-call usage line (tokens · cost · latency); stop button.
- **Status bar** (both views): agent state, running jobs, context %, session cost, call counts, cache rate. Hotkey toggles tabs.
- **Theme toggle** in the header (plus hotkey): dark ↔ light. Default follows the OS `prefers-color-scheme`; the choice persists in `localStorage`. Both palettes are CSS variables on `[data-theme]`; code highlighting and marimo iframes (via `?theme=`) follow the active mode.
- Look: Claude-Code-style terminal aesthetic, JetBrains Mono everywhere, "hybrid-monotone" palette — interpreted as a neutral grayscale base with one restrained accent hue for state/emphasis, in a dark and a light variant (builder's interpretation, see 8). Same tokens drive `marimo-theme.css`.

### 6.10 Deployment

`compose.yaml` (all harness code `COPY`'d into images at build time; changes need `docker compose up --build`):
- `backend`: uvicorn on `0.0.0.0:8000` (only needs reaching from frontend); image contains `backend/` and `lib/` (at `/opt/harness-lib`); bind-mounts `./data` at `/agent`; env from `.env`.
- `frontend`: image contains `frontend/`; Vite dev server on `0.0.0.0:5173`.

Config (env): `MODEL`, `SUB_MODEL`, `CONTEXT_BUDGET` (size readout + metrics %), `PRICES` (6.8), `FOREGROUND_TIMEOUT=60`. Agent folder is fixed at `./data` → `/agent`.

## 7. Decided by the user

- CLM context-as-file + RLM input-as-variable; agent communicates only via Python library calls.
- Code runs via `uv run` in the agent folder; no persistent REPL.
- Agent idles after finishing until a trigger.
- Messages always arrive as stubs (option B), bodies only via `h.inbox`.
- Notifications are non-blocking; agent decides when to open them.
- Runs over the foreground limit (~60 s) detach into jobs; `h.spawn` for known-long work.
- Markdown default, marimo as `backend=` argument, executed in the agent folder, themed.
- Two views: chat and read-only trace. Claude-Code-style UI, JetBrains Mono, hybrid-monotone palette.
- Docker compose, backend + frontend, harness code baked into images, agent folder `./data` (gitignored) bind-mounted. No nginx.
- Metrics in the UI (context size, cost, …); dark/light mode toggle.
- Approach A.

## 8. Builder's calls (correctable)

- A fresh session starts idle; the first trigger is the first user message (no initial task).
- Idle = assistant turn with no tool call (no explicit `h.idle()`).
- `h.llm` included (RLM sub-calls, depth 1, list → concurrent); called directly from the agent process with creds in env.
- "Edit memories" = editing `context.md`; no separate memory store.
- Library injected at run time, agent's `pyproject.toml` untouched.
- Every run is a job from the start (makes detach seamless).
- Opened marker on inbox messages, surfaced in chat.
- Stop control in trace view (safety against runaway loops).
- Context overflow at the provider: error event, agent goes idle; no automatic recovery this iteration.
- Frontend: React + Vite + TS; palette interpretation as above.
- Cost from a `PRICES` config, not provider-reported cost; unpriced models show tokens only.
- Metrics are UI-only; the agent sees just its context size.
- Theme defaults to the OS preference; marimo themed at serve time so toggling needs no re-export.

## 9. Left to the builder

- Exact `context.md` delimiter syntax for code/results; tool-call id handling.
- Token counting method for the size readout.
- marimo theming mechanism within serve-time injection (CSS overrides vs marimo's own theme config), as long as both modes match the app.
- System prompt wording (must document the library, notification semantics, context-file protocol, foreground limit, and "peek / slice / `h.llm`, don't print whole inputs").
- Frontend component/library choices (markdown renderer, state management).
