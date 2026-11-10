# Calmer UI, document-style marimo messages, context edit view — design

Status: draft for approval · 2026-11-10 · builds on `2026-11-09_clm-harness.md`

## 1. Intent

Three changes to the harness, from day-to-day use:

1. **UI is hard to read.** Replace the terminal look (all-mono, `[chat]` tabs, `›` rows, `│` separators, Claude orange) with a calm, readable interface.
2. **marimo messages should read like normal messages.** marimo is not a notebook viewer. It exists so that charts, tables and figures come from executed code instead of the model typing them out (and maybe getting numbers wrong). The user sees a normal-looking agent message, with no code and no visible cells.
3. **Make the agent's context edits visible.** For each time the agent rewrites `context.md`, show the raw file after the edit, diffed against the version right before it.

## 2. Goals / non-goals / anti-goals

Goals
- Palette C (cool slate + dusty teal), dark and light. Inter for text, JetBrains Mono for code and raw files. No heavy borders: areas are separated by shade, with hairlines at most.
- marimo messages: no code, no cell chrome, no caption, no frame border, no inner scroll bar. The frame height fits its content. Inter and palette C are used inside.
- The system prompt steers the agent to send data-derived content (numbers, tables, charts) as marimo messages built from prose cells plus code cells.
- A **Context** tab: one entry per agent edit, rejected edits included, each showing the raw file after the edit with an inline diff against the previous version.

Non-goals
- Diffing the harness's own per-turn rewrites (those only append turns).
- Live marimo widgets (exports stay static. JS charts stay interactive, sliders and other controls do not).
- Changing the `context.md` format, the `h.send` API, or the trace/chat information content.

Anti-goals
- A marimo message that still looks like an embedded notebook: a box, a caption, a scroll bar, a different font, or visible code.
- Loosening the iframe sandbox (agent-written HTML must not get same-origin access to the app or its API).
- Context history that bloats the event stream (full files replayed on every WS connect).

## 3. Constraints

- Existing stack and conventions: FastAPI backend, React + Vite + TS frontend, TDD, minimal code, no auto-commit.
- Marimo exports are produced at send time and themed at serve time by `themed()` in `outbox.py` (existing mechanism, kept).
- The marimo iframe is sandboxed with `allow-scripts allow-popups allow-downloads`, without `allow-same-origin`, so the parent cannot read its document.

## 4. Approaches

| Area | Approach | Trade-off | Outcome |
|---|---|---|---|
| marimo sizing | **A. Self-reported height:** serve-time injected script posts its height to the parent via `postMessage`. | Sandbox unchanged. CSS that hides marimo chrome depends on marimo's DOM, which can change between versions. | **Chosen** |
| | B. Extract outputs from the export and render them natively in React | Most native look, but marimo's interactive tables/charts need marimo's JS and would break. | Rejected |
| | C. Add `allow-same-origin` and measure from the parent | Less code, but agent-written pages would get full access to the app and API. | Rejected (anti-goal) |
| context history | **A. Snapshot files** `.harness/context-edits/<n>.{before,after}.md` plus a route returning a backend-computed (`difflib`) diff | Small events, no new dependency, testable in pytest. | **Chosen** |
| | B. Full before/after text inside the `context_edit` event | No new route, but every reconnect replays every full file. | Rejected |
| restyle | Swap tokens in `theme.css` / `marimo-theme.css` and reshape header, tabs, chat rows and status bar | Only sensible way. | — |

## 5. File tree (overview of changes)

```
clm-harness/
├── .gitignore                         # + .superpowers/ (brainstorm mockups)
├── specs/
│   └── 2026-11-10_ui-marimo-context-view.md   # this document
├── backend/
│   ├── src/backend/
│   │   ├── loop.py                    # _sync_edit: snapshot before/after, event gains n
│   │   ├── context.py                 # + diff_lines(before, after)
│   │   ├── outbox.py                  # export with --no-include-code; themed() injects height script
│   │   ├── app.py                     # + GET /api/context-edits/{n}
│   │   └── prompts/system.md          # marimo = data-derived content, prose + code cells
│   └── tests/
│       ├── test_context.py            # diff_lines
│       ├── test_loop.py               # snapshots for accepted + rejected edits, n increments
│       ├── test_outbox.py             # --no-include-code, height script injected
│       └── test_app.py                # context-edits route (200 / 404)
└── frontend/src/
    ├── theme.css                      # palette C tokens, Inter + JetBrains Mono, new header/tabs/chat/status/diff styles
    ├── marimo-theme.css               # palette C, Inter, strip marimo chrome, hide code (old exports)
    ├── App.tsx                        # tabs Chat | Trace | Context (pills), alt+t cycles 3 tabs
    ├── ChatView.tsx                   # bubble layout; marimo iframe without figure/caption, auto height
    ├── TraceView.tsx                  # restyle only (classes)
    ├── ContextView.tsx                # NEW: edit list + raw file with inline diff
    ├── derive.ts                      # context_edit gains n; + contextEdits(), foldDiff()
    └── derive.test.ts                 # contextEdits, foldDiff
```

## 6. Design

### 6.1 Visual style

Fonts: Inter (text, headings, UI) and JetBrains Mono (code, run output, raw context file, inline code), both from Google Fonts as today. Body text is 14px with line height ~1.6, and code is 12.5px.

Tokens (contrast-checked: text ≥ 10:1, muted ≥ 5:1 on both `bg` and `surface`):

| token | dark | light |
|---|---|---|
| `--bg` | `#22262c` | `#f5f7f9` |
| `--surface` (header, footer, code, panels) | `#282d34` | `#eaeef2` |
| `--surface-2` (user bubble, selected item, active tab) | `#2e333b` | `#e4e9ee` |
| `--fg` | `#d3d8de` | `#2a3038` |
| `--fg-strong` | `#e4e8ec` | `#1d232a` |
| `--muted` | `#949da8` | `#5c6672` |
| `--border` (hairlines only) | `#30363e` | `#dde3e9` |
| `--accent` | `#8fbcbb` | `#3a6e6c` |
| `--accent-bg` (active tab pill) | `#2c3d40` | `#d7e7e6` |
| `--ok` | `#b4c79a` | `#56733a` |
| `--err` | `#d88a8a` | `#a8505a` |

Shapes (from the approved mockup):
- **Header:** `surface` background with no border line. "clm-harness" brand, then pill tabs `Chat · Trace · Context` (active tab = `accent-bg` pill in `accent` text). Theme toggle on the right. The hotkey hint stays, in muted small text.
- **Chat:** user messages are right-aligned bubbles (`surface-2`, radius 12/12/3/12, max ~75% width) with attachment chips and the opened marker below. Agent messages are left-aligned, with a small "agent" label in `accent` above the body and no bubble. Markdown and marimo messages look identical apart from content.
- **Footer/status bar:** `surface` background, no `│` separators. Items are spaced with a `·` separator, and the state is shown as a coloured dot (accent when working, muted when idle, err when disconnected).
- **Trace:** same tokens and fonts. Reasoning is in Inter, code/output blocks are in mono on `surface` with radius 6. Usage lines are muted small text.
- **Borders:** 1px `--border` only where shade alone can't separate (tables, the context-view list divider). No 2px left rules, except a thin accent rule on markdown blockquotes.
- **Code highlighting:** stays monotone-ish on the new tokens (keywords `accent`, strings `ok`, comments muted italic).

### 6.2 marimo messages

Export (`outbox.py`): add `--no-include-code` to the `marimo export html` command. Verified with marimo's CLI: this produces read mode with outputs only, cells without output leave no gap, and the result already reads as one document.

Serve time (`themed()` + `marimo-theme.css`):
- Set fonts to Inter (text, headings) and JetBrains Mono (mono), and apply palette C (map tokens onto marimo's CSS variables as today).
- Strip chrome: remove the centred narrow column's max-width and side padding (content fills the frame width, aligned with markdown messages), hide the "made with marimo" badge and the static-notebook banner, and set the page background to `--bg` (identical to the chat background, so the frame is invisible).
- **Older messages exported with code:** serve-time CSS hides code editors and cell borders, so they render like new ones. Builder's call: no re-export, because the notebook file may have changed or been deleted since it was sent, and a re-export could silently change what the user was shown.
- Height script, injected before `</head>`: on load and via `ResizeObserver` on `document.documentElement`, it sends `parent.postMessage({ type: "marimo-height", height }, "*")`. The page's own overflow is hidden.

Chat (`ChatView.tsx`): render the iframe directly in the agent message body, with no `<figure>` or caption, no border, width 100%, and an initial height of ~120px. A `message` listener matches `event.source` to the iframe's `contentWindow` and sets its height. The sandbox attributes stay unchanged. The theme toggle still reloads via `?theme=`.

System prompt (`system.md`, the `h.send(..., backend="marimo")` row plus a short paragraph): a marimo message is a normal message whose numbers, tables and charts are computed by code cells instead of typed. Write prose in `mo.md(...)` cells, and put each figure/table in its own cell as that cell's last expression. Code is never shown to the user. Use marimo whenever the message contains data-derived numbers, tables or charts, and markdown for plain prose.

### 6.3 Context edit history

Backend (`loop.py`, `_sync_edit`): whenever the file differs from `self.rendered` (accepted *or* rejected):
- `n` = next edit number (count of existing `*.after.md` + 1, so it survives restarts).
- Write `.harness/context-edits/<n>.before.md` = `self.rendered` (the version the harness last wrote) and `<n>.after.md` = the agent's raw file text exactly as written (for accepted edits too: the user wants the raw file, not the re-rendered parse).
- The existing `context_edit` event gains `n`. Everything else about the event and the agent-facing receipt stays the same.

`context.py`: `diff_lines(before: str, after: str) -> list[dict]` with items `{"op": " " | "+" | "-", "text": str}`, built from `difflib.SequenceMatcher` on lines. Replaced blocks emit removed lines before added lines, in file order.

`app.py`: `GET /api/context-edits/{n}` returns `{n, lines}` (404 if missing). The `ok`/receipt/token data comes from the event the frontend already has.

Frontend:
- `derive.ts`: `context_edit` type gains `n?: number`. `contextEdits(events)` returns edit events that have `n`, newest first. `foldDiff(lines, ctx = 3, min = 8)` turns runs of more than `min` unchanged lines into head/tail `ctx` lines plus a fold marker `{fold: count, lines}`. Removed runs longer than `min` show their first `ctx` lines plus a fold marker. Added lines are never folded.
- `ContextView.tsx`: left list (~260px, `surface`, one hairline divider) with entries `edit #n · HH:MM` (time from the event `ts`) and the event's receipt as subtitle (`18 → 9 turns · 12.4k → 5.1k tokens`, or `rejected: <reason>`). Rejected entries are shown in `err`. The newest edit is selected by default. The right pane has a sticky bar (`context.md after edit #n · +A −R lines`) and the raw file in mono. Added lines get an `ok` tint (~15% alpha). Removed lines get an `err` tint plus strikethrough. Fold rows are muted italic and expand on click. If there are no edits yet, an empty-state line is shown.
- `App.tsx`: third tab `Context`, and `alt+t` cycles Chat → Trace → Context.

## 7. Decided by the user

- Palette C (cool slate + dusty teal), dark and light. Fonts Inter + JetBrains Mono. No thick or white borders.
- marimo messages look like normal messages: no code, no visible cells. Charts/tables come from executed code cells instead of model-typed text.
- Context view: one entry per agent edit, raw file after the edit, diff against the previous version. List-plus-file layout as in the mockup.
- Approaches 1A (self-reported iframe height, sandbox unchanged) and 2A (snapshot files + diff route).

## 8. Builder's calls (correctable)

- Older marimo messages: hide code via CSS instead of re-exporting (6.2, reason given).
- Rejected edits get entries and snapshots too, and the diff shows what the agent tried to write.
- The edit number counts accepted and rejected edits together.
- `after` snapshot = the agent's raw text, `before` = the harness-rendered version it replaced.
- Diff computed in the backend with `difflib`, folding done in the frontend.
- Fold thresholds: runs > 8 lines fold, keeping 3 lines of context.
- Muted text colours adjusted from the mockup (`#87909b` → `#949da8` dark, `#6e7884` → `#5c6672` light) to reach ≥ 5:1 contrast.
- Hotkey hint and theme toggle kept in the header.

## 9. Left to the builder

- Exact marimo CSS selectors for the column width, badge, banner and (old-export) code editors. Verify against a real export containing prose, a table and a chart, in both themes, before and after `--no-include-code`.
- Exact spacing, radii and font sizes within the mockup's character.
- Whether the fold marker for removed runs sits inline or merges with an adjacent unchanged fold.
