# Telegram palette, file messages, Files view, profiles, settings panel — design

Status: draft for approval · 2026-11-10 · builds on `2026-11-10_ui-marimo-context-view.md`

## 1. Intent

Five changes from day-to-day use:

1. **Agent messages are markdown, plus files.** Remove marimo completely. The agent can attach files to a message instead: images show inline, other files can be downloaded.
2. **Telegram look.** Use Telegram's colour palette and its small controls (round send button, paperclip attach button, message bubbles). The core UX stays as it is: header tabs, composer at the bottom, the same views.
3. **Settings panel instead of the status line.** Remove the footer. A ⚙ panel holds metrics, profiles and theme.
4. **Files view.** Browse the agent folder and read files.
5. **Profiles.** Each profile is its own agent with its own folder. Profiles can be created (choosing a model, default `deepseek/deepseek-v4.1-flash`) and deleted.

After deploying, the existing `./data` (old messages, old agent folder) is wiped. The user starts from scratch, so there is no migration and no fallback for old marimo messages.

## 2. Goals / non-goals / anti-goals

Goals
- `h.send(text, files=[...])`: a markdown message with optional attachments, snapshotted when it is sent.
- Image attachments are shown inline in the bubble. Other files are shown as file rows (name · size) that download when clicked. User attachments are rendered the same way.
- Telegram palette, light and dark. Agent messages go in a left-aligned bubble, user messages in a right-aligned bubble.
- A 4th tab, **Files**: a lazy tree of the agent folder, with the selected file shown on the right.
- Several profiles running at the same time in one backend. Switching profiles only changes which one the UI shows.

Non-goals
- Renaming a profile, or changing its model after creation (`profile.json` can be edited by hand, and the change applies after a backend restart).
- A per-profile budget, foreground timeout or `SUB_MODEL` (these stay global env).
- Unread badges for profiles in the background.
- Editing or uploading files from the Files view (it is read-only).
- Telegram's chat-list layout, wallpaper or fonts.

Anti-goals
- Agent-written HTML/SVG/JS that runs same-origin with the app when opened from a chat file row or the Files view.
- A path in the file route that escapes the profile folder.
- One profile's events, metrics, files or jobs reaching another profile's view.
- Deleting a profile without an explicit typed confirmation.

## 3. Constraints

- Existing stack and conventions: FastAPI backend, stdlib `harness` lib, React + Vite + TS frontend, docker compose, TDD, minimal code, no auto-commit.
- Gateway `/models` lists 97 ids, including `claude-*` and `gpt-*`, which do not work on `/chat/completions`. `deepseek/deepseek-v4.1-flash` exists and returns proper `tool_calls` (verified 2026-11-10).
- The Cloudflare front end needs a custom User-Agent for stdlib/HTTPX clients.
- The agent processes already get their own `HARNESS_DIR` per run (`agent_env(hdir)`). The `harness` lib needs no global state change for multiple profiles.

## 4. Approaches

| Area | Approach | Trade-off | Outcome |
|---|---|---|---|
| multiple agents | **A. One backend, one runtime per profile** in a dict. Routes under `/api/p/{name}/…`, WS `/ws/{name}`. | Reuses every per-agent class unchanged. One backend crash takes all agents down (jobs survive, since they are detached). | **Chosen** |
| | B. Supervisor + one uvicorn per profile behind a proxy | Isolation, but ports, health checks and proxy code add moving parts. | Rejected |
| | C. One container per profile | Compose would have to create containers dynamically. | Rejected |
| sent files | **A. Copy at send time** into `.harness/outbox/<id>/` (mirrors `inbox/<id>/files/`) | A message stays as it was sent even if the file changes or is deleted. Costs disk space for large files. | **Chosen** |
| | B. Reference the live path | No copies, but old messages can change silently or break. | Rejected |

## 5. File tree (overview of changes)

```
clm-harness/
├── .env                                # MODEL → DEFAULT_MODEL=deepseek/deepseek-v4.1-flash
├── compose.yaml                        # ./data:/data (profiles root)
├── specs/
│   └── 2026-11-10_telegram-files-profiles.md   # this document
├── lib/
│   ├── src/harness/__init__.py         # send(text, files=None): copy files into outbox/<id>/; no backend arg
│   └── tests/test_harness.py           # send copies files, missing file / duplicate name raise
├── backend/
│   ├── Dockerfile                      # drop marimo warm-up + MARIMO_THEME_CSS; AGENT_DIR → DATA_DIR=/data
│   ├── src/backend/
│   │   ├── profiles.py                 # NEW: Runtime (per-profile wiring, watcher, start/stop), Profiles registry
│   │   ├── app.py                      # profile CRUD, /api/models, scoped routes, raw file + tree routes, /ws/{name}
│   │   ├── outbox.py                   # tail outbox.jsonl → agent_message {id, text, files}; marimo removed
│   │   ├── executor.py                 # + kill_all() (profile deletion)
│   │   ├── notify.py                   # − send_failed_stub
│   │   └── prompts/system.md           # h.send(text, files=…); marimo text and "send failed" line removed
│   └── tests/
│       ├── test_app.py                 # profiles CRUD/validation, scoping, raw/tree routes, traversal, headers, ws 4404
│       ├── test_outbox.py              # files in agent_message; marimo tests removed
│       └── test_executor.py            # kill_all
└── frontend/src/
    ├── theme.css                       # Telegram tokens, bubbles, composer buttons, settings panel, files view; − footer
    ├── marimo-theme.css                # DELETED
    ├── MetricsPanel.tsx                # DELETED (table moves into SettingsPanel)
    ├── SettingsPanel.tsx               # NEW: Profiles · Metrics · Appearance
    ├── FilesView.tsx                   # NEW: lazy tree + file pane
    ├── App.tsx                         # current profile, header (profile chip, 4 tabs, ⚙), no footer, hotkeys
    ├── ChatView.tsx                    # bubbles, Attachments, paperclip + round send, scoped URLs; − Marimo
    ├── ContextView.tsx                 # scoped URL
    ├── TraceView.tsx                   # scoped URL
    ├── ws.ts                           # useEvents(profile): reconnect + reset on profile change
    ├── derive.ts                       # AgentMsg {text, files}; + isImage, fmtSize, timeOf
    └── derive.test.ts                  # isImage, fmtSize, chatItems with files
```

## 6. Design

### 6.1 Profiles (backend)

Layout: `DATA_DIR` (default `/data`, compose mounts `./data:/data`). Each child folder containing `profile.json` (`{"model": "<id>", "created": <ts>}`) is a profile, and the folder name is the profile name. Folders without `profile.json` are ignored.

`profiles.py`:
- `Runtime(dir, model_factory)` holds everything `create_app` wires today for one agent (uv init if needed, State, Events, Notifier, Metrics, Executor + `startup()`, Agent, Outbox) plus its `watch()` loop. `start()` creates the loop and watcher tasks. `async stop()` cancels them and awaits.
- `Profiles(data_dir, model_factory)`: `load()` starts a runtime per existing profile. `create(name, model)` validates and writes `profile.json`, then starts the runtime. `delete(name)` stops the runtime, calls `executor.kill_all()`, then `shutil.rmtree(dir)`. `get(name)` returns the runtime or raises 404.
- Name rule: `^[a-z0-9][a-z0-9_-]{0,31}$`. An invalid name returns 422, an existing one 409.
- `model_factory(model_id)` returns a bound chat model. The default is `ChatOpenAI(model=model_id, …).bind_tools([TOOL])`, and tests pass a fake. The model id is also passed to `Agent` as `model_name` (pricing via `PRICES`; unknown models count as unpriced, as today).

`create_app(data_dir=None, model_factory=None)` builds `Profiles` in its lifespan and stops every runtime on shutdown. Zero profiles is a valid state, and nothing is auto-created.

`executor.kill_all()`: for every job in `running`, mark it `killed` and SIGTERM its process group (same as `harness.kill`, minus the global `HARNESS_DIR`).

Routes:

| Route | Behaviour |
|---|---|
| `GET /api/profiles` | `[{name, model, state}]`, sorted by name |
| `POST /api/profiles` `{name, model?}` | creates (model defaults to `DEFAULT_MODEL`), returns the profile. 422 / 409 as above |
| `DELETE /api/profiles/{name}` | stops, kills jobs, deletes the folder |
| `GET /api/models` | gateway `/models` ids without `claude-*` / `gpt-*`, cached after the first success. Gateway down → `[]` |
| `POST /api/p/{name}/messages` | as today's `/api/messages` |
| `GET /api/p/{name}/metrics`, `POST /api/p/{name}/agent/stop`, `GET /api/p/{name}/context-edits/{n}` | as today, per profile |
| `GET /api/p/{name}/tree?path=` | directory listing `[{name, dir, size, mtime}]`, dirs first then by name, hidden entries included |
| `GET /api/p/{name}/raw/{path:path}` | file bytes. `?download=1` adds `Content-Disposition: attachment` |
| `WS /ws/{name}?after=` | as today's `/ws`. Unknown profile → close with code 4404 |

Removed: `/api/outbox/{i}.html`, `/api/inbox/{i}/files/{name}` (both kinds of attachment now go through `raw`: `.harness/inbox/<i>/files/<f>` and `.harness/outbox/<i>/<f>`), `/api/messages`, `/ws`, and `/api/metrics`/`/api/agent/stop` without a profile.

File safety (`tree` and `raw`): resolve `dir / path` and require `is_relative_to(dir.resolve())`, otherwise 404. Symlinks pointing outside the folder are rejected by the same check. Every `raw` response carries `Content-Security-Policy: sandbox` and `X-Content-Type-Options: nosniff`.

### 6.2 File messages

`harness.send(text: str, files: list[str | Path] | None = None) -> int`:
- Under the existing `flock`: compute `sid`, copy each file to `.harness/outbox/<sid>/<basename>` (`shutil.copy2`), then append `{"id", "ts", "text", "files": [{"name", "size"}]}`. Copying before the row is written means the backend never sees a row whose files are missing.
- A missing path or a directory → `FileNotFoundError` / `IsADirectoryError`. Two files with the same basename → `ValueError`. Both are raised before anything is written, so the agent sees a traceback in its run result.
- Paths are relative to cwd (the agent folder) or absolute.

`outbox.py`: tail `outbox.jsonl` and emit `agent_message {id, text, files}`. The export queue, `themed`, `HEIGHT_JS`, `_first_error` and the send-failed notification are deleted.

System prompt: the `h.send` row becomes `h.send(text, files=[...])`: send a markdown message, optionally with files (paths in the agent folder, copied at send time; images show inline, other files can be downloaded). The marimo row, the marimo paragraph and the `send #5 failed` notification example are removed. A short line replaces the marimo rationale: when a message reports data-derived numbers, compute them in code and attach charts as image files (e.g. matplotlib PNG) and tables as files (CSV) instead of typing them out.

### 6.3 Visual style

Telegram Desktop palette (light = default theme, dark = "Night" blue). Same token names as today, plus bubble tokens:

| token | light | dark |
|---|---|---|
| `--bg` (chat background) | `#e7ebf0` | `#0e1621` |
| `--surface` (header, panels, composer) | `#ffffff` | `#17212b` |
| `--surface-2` (hover, selected) | `#f1f3f5` | `#202b36` |
| `--fg` | `#000000` | `#f5f5f5` |
| `--fg-strong` | `#000000` | `#ffffff` |
| `--muted` | `#707579` | `#708499` |
| `--border` | `#dadce0` | `#101921` |
| `--accent` (links, send button, active tab) | `#3390ec` | `#5288c1` |
| `--accent-bg` (active tab pill) | `#e3f0fd` | `#2b5278` |
| `--bubble-in` (agent) | `#ffffff` | `#182533` |
| `--bubble-out` (user) | `#eeffde` | `#2b5278` |
| `--meta-out` (time/ticks in user bubble) | `#4fae4e` | `#7da8d3` |
| `--ok` | `#4fae4e` | `#6ab36a` |
| `--err` | `#e53935` | `#ec6a6a` |

Fonts stay Inter + JetBrains Mono. Code highlighting keeps today's scheme on the new tokens.

Shapes:
- **Header** (unchanged structure): `clm-harness` brand, then the **profile chip** (state dot + current profile name; click opens settings at Profiles), then pill tabs `Chat · Trace · Context · Files`, a spacer, the muted hotkey hint and a ⚙ button. The theme toggle moves into settings. The state dot is accent when working, muted when idle and err when disconnected, replacing the footer's indicator.
- **Bubbles:** radius 12, with the corner nearest the sender at 4 (Telegram's tail, without the drawn tail), max ~75% width, small shadow in light mode only. Agent: `bubble-in`, left. User: `bubble-out`, right. No "agent" label. Bottom-right of each bubble shows the time `HH:MM` (event `ts`) in muted text (`meta-out` in user bubbles). User bubbles add ✓ when unopened and ✓✓ once opened (Telegram's read ticks), replacing the `unopened` / `✓ opened` text.
- **Attachments** (both directions, one component): images (`png jpg jpeg gif webp svg`) render as `<img>` at the bubble width (max-height ~320px), and clicking one opens `raw` in a new tab. Other files render as a row: round accent file icon, name, muted size. The row links to `raw?download=1`.
- **Composer:** a `surface` bar with a paperclip icon button on the left (muted, accent on hover), a borderless auto-growing textarea, and a round 40px `accent` send button with a white paper-plane icon on the right. Pending files are chips above the textarea. Icons are inline SVG, with no icon library.
- **Footer:** removed.

### 6.4 Settings panel

`SettingsPanel.tsx` replaces `MetricsPanel.tsx`: a ~360px right-side panel on `surface`. It closes on Esc, the ⚙ button or a click outside, but not on clicks inside. Three sections:

1. **Profiles:** a list of `state dot · name · model`. The current profile is highlighted, and clicking a row switches to it. Each row has a × that opens an inline confirm ("type <name> to delete" + Delete button, enabled only on an exact match). Below the list is a **New profile** form: a name input, and a model input with a `<datalist>` from `/api/models`, prefilled with `deepseek/deepseek-v4.1-flash`. Free text is allowed, so it still works when the gateway list is empty. The form shows a Create button and the server error (422/409) inline.
2. **Metrics:** today's metrics table for the current profile, unchanged.
3. **Appearance:** light/dark toggle.

The list is fetched when the panel opens and after each create/delete.

### 6.5 Files view

`FilesView.tsx`: the left pane (~280px, `surface`) is a tree rooted at the profile folder. Clicking a dir fetches `tree?path=` lazily and expands it. Dirs come first, and hidden entries are shown. A ↻ button in the pane header refetches expanded dirs. The right pane shows a sticky bar (`path · size · Download`) and the content:
- images (same extension list): `<img>` from `raw`
- otherwise, if size ≤ 1 MB: fetched as text and shown in mono with line numbers. If the first 8 KB contain a NUL byte, the file is treated as binary instead.
- binary or > 1 MB: "binary file · size" plus Download.

With nothing selected, an empty state is shown.

### 6.6 Frontend wiring

- The current profile lives in `localStorage` (`profile`). On load, or when the current profile is deleted, the frontend falls back to the first profile from `/api/profiles`. With zero profiles, the main area shows "no profiles yet" with a button that opens settings.
- `useEvents(profile)` connects to `/ws/${profile}` and clears its events and metrics when the profile changes. Views use `/api/p/${profile}/…` URLs.
- Hotkeys: `alt+t` cycles 4 tabs, `alt+s` toggles settings (replaces `alt+m`), `alt+d` toggles theme, Esc closes settings.
- `derive.ts`: `AgentMsg = Base & {type: "agent_message"; id; text; files: {name; size}[]}`. Adds `isImage(name)`, `fmtSize(bytes)` and `timeOf(ts)`.

### 6.7 Deploy and cleanup

1. `.env`: `MODEL` → `DEFAULT_MODEL=deepseek/deepseek-v4.1-flash`. `PRICES` stays as is (v4.1-flash is unpriced until the user adds its price).
2. Rebuild and restart with compose on the remote host.
3. Wipe the old data: the backend runs as root, so `docker compose exec backend sh -c 'rm -rf /data/* /data/.[!.]*'`. This step is explicitly requested by the user.
4. Smoke test: create a profile, send a message, have the agent send an image and a CSV, browse the files, delete the profile.

## 7. Decided by the user

- Telegram colour palette and small controls (send button, attach button, bubbles). The core UX stays as before.
- Agent messages are markdown only, and marimo is removed. The agent can send files: images viewable inline, others downloadable.
- Agent messages in a left-aligned bubble.
- Footer removed. A ⚙ settings panel holds metrics, profiles (switch, create with model, delete) and theme.
- A Files view to browse the agent folder and see file contents.
- Profiles: create/delete, one agent per profile, model chosen at creation, default `deepseek/deepseek-v4.1-flash`.
- Old messages and the old folder are wiped after the implementation, with no migration.
- Approaches 1A (one backend, runtime per profile) and 2A (copy files at send time).

## 8. Builder's calls (correctable)

- All profiles run at once. An idle agent costs nothing, since it waits for notifications.
- The profile model replaces `MODEL` only. `SUB_MODEL`, budget and foreground timeout stay global.
- Deleting a profile deletes its folder and kills its running jobs, after a typed-name confirmation.
- `claude-*` and `gpt-*` are hidden from the model suggestions. The model field is free text with suggestions instead of a strict dropdown, so a gateway outage does not block creating profiles.
- No auto-created profile. Zero profiles shows an empty state.
- Profile names are restricted to `[a-z0-9_-]`, up to 32 characters, and start with a letter or digit (safe as folder names).
- Inbox and outbox attachments are served through the same `raw` route, and the dedicated inbox route is removed.
- User attachments render the same way as agent attachments (inline images, file rows).
- Telegram read ticks (✓ / ✓✓) and an `HH:MM` time inside bubbles replace the opened/unopened text. The "agent" label is removed (bubble side and colour already say who is speaking).
- Theme toggle moves from the header into settings. `alt+m` becomes `alt+s`.
- The profile chip in the header shows the agent state dot (replacing the footer indicator) and opens settings when clicked.
- Fonts stay Inter + JetBrains Mono rather than Telegram's system font.
- Files view: hidden entries shown, text preview limit 1 MB, NUL-byte binary sniff, read-only.
- `MODEL` env renamed to `DEFAULT_MODEL`, and `AGENT_DIR` to `DATA_DIR`.

## 9. Left to the builder

- Exact spacing, shadows and icon paths within Telegram's character. Check contrast (text ≥ 7:1, muted ≥ 4.5:1 on `bg`, `surface` and both bubbles), and adjust `--muted` / `--meta-out` if they fall short.
- How `tree` sorts names (case-insensitive is fine).
- Whether the ↻ refresh also reloads the open file.
