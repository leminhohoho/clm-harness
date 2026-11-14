# Mobile fix, home-screen app, browser notifications — design

Status: draft for approval · 2026-11-14 · builds on `2026-11-12_sidebar-paging-trace-cards.md`

## 1. Intent

The user wants to reach the web UI from a phone with as little friction as possible, and to be told when an agent says something.

1. **The iPhone layout is broken.** On an iPhone 12, the agent sidebar pushes the agent view to the side, which makes the UI unusable.
   - On phones, the agent list should be hidden by default and open full-screen from a button, the way Telegram and Messenger work on mobile.
2. **Home-screen app.** Safari's "Add to Home Screen" should give an icon that opens the UI full-screen, like an app.
3. **Browser notifications.** A desktop browser notification appears when any agent sends a message (`h.send`).

Telegram integration was discussed and is **postponed**. Notes for later: one bot per agent, made optional in agent settings by pasting the BotFather token; a fresh chat with no old history; messages typed in the web UI mirrored as "you (web): …"; the raw Bot API polled with `httpx` from each runtime.

**Root cause of (1), reproduced** in Chromium emulating an iPhone 12 (390×844) against the live UI:
- The mobile layout applies correctly: `.app` is `display: block`, and the agent pane is parked at `translateX(100%)`.
- On load, the composer's `autoFocus` (`ChatView.tsx:166`) focuses the textarea inside that off-screen pane. The browser scrolls the nearest scrollable ancestor to reveal it. `.app` has `overflow: hidden`, which hides scrollbars but stays programmatically scrollable, so `.app.scrollLeft` becomes 260.
- The result: the sidebar sits at x = −260 and the pane at x = 130. Selecting an agent keeps the 260px offset.
- Check: with `.app { overflow: clip }` injected, the textarea still takes focus, `scrollLeft` stays 0, the sidebar sits at x = 0, and after selecting an agent the pane sits at x = 0.

## 2. Goals / non-goals / anti-goals

Goals
- **Phone layout (< 600px).** In Safari and in the home-screen app on an iPhone 12:
  - The app opens straight into the last-used agent's screen. The agent list is hidden until the header button opens it full-screen.
  - The list ⇄ agent screens show one full-width pane, with no sideways offset.
  - Tapping the composer does not zoom the page.
  - The composer stays visible above the keyboard.
  - Nothing sits under the status bar or the home indicator.
- **Home-screen app.** "Add to Home Screen" gives the "clm-harness" icon and name, and the app opens with no Safari UI. The top strip follows the dark/light theme.
- **Back from background.** Returning to the app (or tab) brings the live view up to date without a manual reload.
- **Notifications (desktop browsers).**
  - Every `agent_message` from any agent raises a notification within about a second, as long as one UI tab is open somewhere. A background tab or minimized window is fine.
  - No notification when you are already looking at that agent's chat.
  - Clicking a notification focuses the window on that agent's chat.
- **Fast load on phones.** The UI is served as a production build, not Vite's dev server.

Non-goals
- Telegram (postponed, see 1).
- Notifications with every UI tab closed, and notifications on phones. Both need Web Push: a service worker, a push key pair and a backend sender.
- Offline use, an in-app install prompt, unread badges.
- Swipe-back gesture integration (the ← button stays the way back).

Anti-goals
- Making anything reachable from the public internet. The UI stays tailnet-only and has no login.
- A service worker. It would cache the app shell and can serve stale UI after deploys, and nothing here needs one.
- Changing agent data, `events.jsonl` or its format.
- Notification spam: duplicates per tab, or a ping for the chat you are reading.

## 3. Constraints

- Existing stack and conventions:
  - FastAPI backend, React 19 + Vite + TS frontend, docker compose on the remote host `100.79.132.111` (tailscale).
  - TDD, minimal code, no sub-agents, no auto-commit.
- **No new dependencies**, frontend or backend.
- **Notifications need a secure context.** Browsers refuse the Notification API on `http://100.79.132.111:5173`.
  - HTTPS comes from Tailscale: the host's MagicDNS name is `ubuntu.taila0f05e.ts.net`, and Tailscale 1.104.5 is installed with no serve config yet.
  - **The tailnet currently has no certificate domains**, so the user must turn on *HTTPS Certificates* in the Tailscale admin console (DNS page).
  - Certificates are issued publicly, so the host name appears in Certificate Transparency logs. Only the name becomes public; the host stays reachable from the tailnet only.
- **iOS home-screen apps need no HTTPS, manifest or service worker** to open full-screen.
  - iOS 26 opens every site added to the Home Screen as a web app by default.
  - iOS 16.4–18 needs `display: standalone` in the manifest, or the `apple-mobile-web-app-capable` meta tag.
  - Sources: WebKit "Safari 26.0"; OpenPWA "iOS Add to Home Screen".
- **iOS suspends background apps.** Sockets go stale while the phone is locked or the app is in the background.
- **Origin change.** `localStorage` is per origin, so the theme, the selected agent and the 🔔 choice start fresh on the HTTPS address.
- **Deploy restarts the backend.** Deploy only while the agents are idle, as before.

## 4. Approaches

| Area | Approach | Trade-off | Outcome |
|---|---|---|---|
| layout fix | **`overflow: clip` on `.app` (mobile)** | Removes the scroll container, so the browser has nothing to scroll and focus can't shift the layout. One line | **Chosen** |
| | Drop `autoFocus` on touch, or make the hidden pane `inert` | Fixes this trigger only; any other focus or `scrollIntoView` in the hidden pane would shift the layout again | Rejected |
| cross-agent messages | **1. One new WS stream `/ws` carrying only `agent_message` from every agent** | About 25 backend lines and one small extra socket per tab | **Chosen by the user** |
| | 2. The tab opens `/ws/{name}` for every agent | No backend change, but each socket streams that agent's full trace and metrics; N copies of the reconnect logic | Rejected |
| | 3. Poll each agent's `/events?types=agent_message` | Simplest, but late, and N requests every few seconds | Rejected |
| notifications | **In-page `new Notification()` from the open tab** | No service worker or push server. Needs an open tab | **Chosen** (the user only needs browser notifications for now) |
| | Web Push (service worker + push key pair + `pywebpush`) | Works with the tab closed and on iPhone home-screen apps. HTTPS, a dependency and about 150 lines | Deferred |
| HTTPS | **`tailscale serve` on the host** | Real certificate, tailnet-only, works in every browser and on the phone. The host name lands in public CT logs | **Chosen** |
| | Chrome flag `unsafely-treat-insecure-origin-as-secure` | No server change, but set per browser, and Safari has nothing similar | Rejected |
| | Public tunnel (Cloudflare) | Public exposure, so the UI would need a login | Rejected (anti-goal) |
| serving | **`vite build` + `vite preview`** | `preview` inherits `server.proxy` and `server.allowedHosts`, so `/api` and `/ws` forwarding stays as it is. A Dockerfile-only change | **Chosen** |
| | nginx, or FastAPI serving static files | A second proxy config, or backend coupling | Rejected |

## 5. File tree (overview of changes)

```
clm-harness/
├── backend/
│   ├── src/backend/
│   │   ├── profiles.py          # Profiles.listeners + broadcast; each Runtime forwards its agent_message events
│   │   └── app.py               # + WS /ws: agent_message from every agent, live only
│   └── tests/test_app.py        # + /ws stream test (existing agent, agent created after connect)
├── frontend/
│   ├── Dockerfile               # npm run build; CMD vite preview --host 0.0.0.0 --port 5173
│   ├── index.html               # viewport-fit=cover, manifest, apple-touch-icon, apple-mobile-web-app-title, theme-color
│   ├── public/                  # NEW (Vite serves it at /)
│   │   ├── manifest.webmanifest # name, short_name, start_url "/", display standalone, colours, icons
│   │   ├── icon.svg             # source artwork
│   │   ├── icon-180.png         # apple-touch-icon
│   │   ├── icon-192.png
│   │   └── icon-512.png
│   └── src/
│       ├── theme.css            # mobile: .app overflow clip; 16px inputs; safe-area padding
│       ├── ws.ts                # + useNotify (the /ws socket); both sockets reconnect on return to visible
│       ├── derive.ts            # + shouldNotify(), notifyBody()
│       ├── derive.test.ts       # + tests for both
│       ├── App.tsx              # phone opens on the agent pane; wires useNotify; notification click → select agent + chat; theme-color sync
│       └── Sidebar.tsx          # + 🔔 toggle in the bottom bar
└── specs/2026-11-14_mobile-pwa-notifications.md

host (outside the repo): tailscale serve --bg https:443 → http://127.0.0.1:5173
```

## 6. Design

### 6.1 Mobile layout (`theme.css`, `@media (max-width: 599px)`)

- **`.app { overflow: clip; }`** (root cause, see 1).
- **Agent list hidden by default (phones only).** `App`'s `pane` state starts as `"agent"` instead of `"list"`.
  - The app opens on the agent stored in `localStorage.profile`, or on the first agent when that one is missing (existing fallback).
  - The header's ← button opens the list full-screen, with the existing slide. Tapping an agent slides back to its screen.
  - With zero agents the list (with its + button) shows, because `.pane-agent` already requires a current agent.
  - Desktop (≥ 900px) and tablet (600–899px) layouts are unchanged: the list stays a 300px column or a 72px avatar strip.
- **No zoom on focus.** iOS zooms into any focused field whose font is smaller than 16px. On the phone layout, `textarea`, `input` and `select` get `font-size: 16px`. Desktop keeps 14px.
- **Safe areas.** The viewport meta gains `viewport-fit=cover`.
  - The bottom bars (`.composer`, `.side-bottom`) pad by `env(safe-area-inset-bottom)`.
  - The two top bars (`.side-top`, `.agent-head`) and both sides pad by the matching `env()` insets.
  - Full-screen sheets (`.modal` on mobile) get the same insets.
  - All `env()` values are 0 on desktop, so these rules can sit outside the media query.
- **Keyboard.** The builder checks on the user's device (see 6.6) that the composer stays above the keyboard with `height: 100%`. If it doesn't, size `.app` from `visualViewport.height`. That's left to the builder; no listener is added speculatively.

### 6.2 Home-screen app

`index.html`:
- `<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">`
- `<link rel="manifest" href="/manifest.webmanifest">`
- `<link rel="apple-touch-icon" sizes="180x180" href="/icon-180.png">`
- `<meta name="apple-mobile-web-app-title" content="clm-harness">`
- `<meta name="apple-mobile-web-app-capable" content="yes">`, for iOS 16.4–18 users who add without a manifest read.
- `<meta name="theme-color">`. Set by the inline theme script before first paint, and updated by `App`'s theme effect to the current `--surface` (`#ffffff` light, `#17212b` dark).
- The status-bar style stays at its default, so iOS draws a solid strip coloured by `theme-color` and the page starts below it.

`manifest.webmanifest`: `name` and `short_name` "clm-harness", `id` "/", `start_url` "/", `display` "standalone", `background_color` and `theme_color` from the light palette, icons 192 and 512 (`purpose: any`).

Icon **F2 "typing + online"** (chosen by the user). It's a speech bubble on a rounded tile, drawn on a 120-unit grid:
- **Tile.** The full square in `--accent` `#3390ec`. iOS masks the corners and shows transparency as black, so the PNGs have no transparency and no rounding of their own; `icon.svg` keeps `rx=27` for previews only.
- **Bubble.** White `#ffffff` rounded rectangle at x 18, y 22, size 84×60, `rx` 15, with a tail at the bottom-left (`M30 78 L23 98 L48 81 Z`).
- **Text.** Three `#3390ec` lines, stroke 7, round caps:
  - Line 1: full width, x 34–86.
  - Line 2: short, x 34–56, ending at a `--ok` `#4fae4e` cursor block (x 62, y 45, size 9×14, `rx` 2).
  - Line 3: a "ghost" line at 35% opacity, x 34–78.
- **Online dot.** `#4fae4e`, r 10, at the bubble's top-right (100, 24), with a 5-unit `#3390ec` ring.

`icon.svg` is the source. The PNGs are rendered from it once and committed.

### 6.3 Backend: `/ws` agent-message stream

Contract:
- `GET /ws` (WebSocket). There is no name segment, so it can't collide with a profile name the way `/ws/notify` would collide with an agent called `notify`.
- The server sends one JSON object per agent message: `{"agent": str, "seq": int, "ts": float, "text": str, "files": [{"name", "size"}]}`.
- It covers agents that exist at connect time and agents created afterwards. A deleted agent simply stops producing messages.
- Live only, with no replay. Messages sent while no tab is connected are not notified (the chat still shows them).
- The client sends nothing. On disconnect the server drops the subscription.

Wiring:
- `Profiles` owns `listeners: set[asyncio.Queue]` and `broadcast(agent, ev)`.
- Each `Runtime` forwards every `agent_message` it emits to `broadcast` with its own name.
- The exact hook is the builder's choice: an `on_emit` callback on `Events`, or the emit in `Outbox.poll`. It must not change what `events.jsonl` stores or what `/ws/{name}` sends.

Test (`test_app.py`): two profiles `a` and `b`. Connect `/ws`, write an outbox row for `b`, run its watcher poll, and receive `{"agent": "b", …}`. Then create `c`, do the same for `c`, and receive it. Nothing arrives for user messages or other event types.

### 6.4 Frontend: notifications

**Availability.** The 🔔 toggle appears only when all of the following hold. Otherwise it's hidden.
- `"Notification" in window`.
- `isSecureContext`.
- The device isn't touch-first (`!matchMedia("(pointer: coarse)").matches`). Phone browsers only show notifications through a service worker (non-goal).

**Toggle** (`Sidebar.tsx`, the bottom bar next to ◐):
- States: 🔕 off, 🔔 on. It is stored in `localStorage.notify` (`"on"`/absent).
- Turning it on when `Notification.permission` is `"default"` calls `requestPermission()` from the click, and stores "on" only if the result is `"granted"`.
- With permission `"denied"` the button is disabled and its title says "notifications blocked in browser settings".

**Stream** (`useNotify` in `ws.ts`):
- Opens `/ws` (`wss` on HTTPS) while the toggle is on, and closes it when off.
- Reconnects after 1s on close, the same as `useEvents`.

**Decision** (`derive.ts`, pure and unit-tested):
- `shouldNotify(msg, view)` is false when `view.visible && view.focused && view.agent === msg.agent && view.tab === "chat" && view.pane === "agent"`. Otherwise it's true.
  - `visible` = `document.visibilityState === "visible"`.
  - `focused` = `document.hasFocus()`.
  - `pane` covers the mobile list screen. On desktop it's treated as `"agent"`.
- `notifyBody(text, files)` returns the first ~140 characters of the text with whitespace collapsed and markdown punctuation (`*`, `_`, backticks, `#`, `>`) stripped, plus `…` when cut. With no text it returns `📎 name1, name2`. With text and files it appends ` · 📎 N file(s)`.

**Raising one:**
- `new Notification(agent, { body: notifyBody(...), icon: "/icon-192.png", tag: agent })`.
- The per-agent `tag` makes a newer message from the same agent replace the older notification. It also collapses duplicates when two UI tabs are open in the same browser.
- `onclick`: `window.focus()`, select that agent, switch to the chat tab and the agent pane, then `close()`.

### 6.5 Back from background (`ws.ts`)

On `visibilitychange` → `visible`, both `useEvents` and `useNotify` close and reopen their socket at once:
- `useEvents` resumes from `after=last`, so no events are lost or duplicated. The existing `seq` merge handles any overlap.
- This replaces waiting for a stale iOS socket to time out. It costs one reconnect per return, which is cheap.

### 6.6 Serving and deploy

`frontend/Dockerfile`:
- `RUN npm run build` after `COPY`.
- `CMD ["npx", "vite", "preview", "--host", "0.0.0.0", "--port", "5173"]`.
- `vite.config.ts` stays unchanged if `preview` inherits `proxy` and `allowedHosts` as documented. The builder checks the WS proxy works under `preview`, and otherwise mirrors the two keys under `preview`.

Deploy:
1. **User:** turn on *HTTPS Certificates* in the Tailscale admin console (DNS page).
2. Confirm the agents are idle (sidebar shows no "thinking…/executing…").
3. Rebuild and restart both services with compose (`set -a; . ./.env; set +a` first).
4. On the host: `sudo tailscale serve --bg --https=443 http://127.0.0.1:5173`. tailscaled persists it across reboots. `http://100.79.132.111:5173` keeps working as a fallback without notifications.
5. Smoke (builder, desktop Chrome on `https://ubuntu.taila0f05e.ts.net`):
   - The app loads and agent chats stream.
   - Turn on 🔔. With `researcher` open, a test `h.send` on `predictor` raises a notification, and clicking it opens `predictor`'s chat.
   - A test `h.send` on the open, focused chat raises nothing.
   - The manifest and icons load.
   - In iPhone 12 emulation: no sideways offset on load or after selecting an agent (the before/after check from 1), and the composer font is 16px.
6. **User, on the iPhone 12:**
   - Safari → `https://ubuntu.taila0f05e.ts.net` → Share → Add to Home Screen.
   - Launch it and check: full-screen, list ⇄ agent, no zoom on composer tap, composer above the keyboard, nothing under the status bar or home bar.
   - Lock the phone for a minute, return, and check the chat catches up.

## 7. Decided by the user

- Fix the mobile UI first. Telegram is postponed.
- The web UI becomes a home-screen app ("least friction on mobile").
- Notifications: desktop browser notifications only for now. Phone and closed-tab notifications are deferred.
- Notifications cover **all agents**, not only the one that's open.
- Cross-agent delivery: approach 1, a dedicated light stream.
- App icon: F2, a speech bubble mid-sentence (cursor plus ghost line) with an online dot.
- On phones only, the agent list is hidden by default and opens full-screen from a header button. Desktop and tablet keep the side column and the avatar strip.

## 8. Builder's calls (correctable)

- **HTTPS through `tailscale serve`.** The user didn't object when it was proposed. The host name appears in public CT logs.
- **Stream path `/ws`**, not the `/ws/notify` named in conversation, because `notify` is a valid agent name.
- **Live-only stream.** No catch-up notifications for messages sent while no tab was open or the laptop slept.
- **No ping while reading.** Suppressed only while the window is visible and focused and on that agent's chat tab. A background tab, another tab or another agent all notify.
- **One notification per agent** (`tag`). A burst from one agent shows the latest message, not a stack.
- **The 🔔 is hidden on touch-first devices**, because it couldn't work there without push.
- **16px form text on the phone layout only.** Desktop stays at 14px.
- **Status bar default style + `theme-color`**, rather than `black-translucent` with a top inset. This is fewer moving parts, and the strip follows the theme.
- **Reconnect on every return to visible**, rather than detecting stale sockets.
- **The old `http://…:5173` address stays up.**
- **Production build served by `vite preview`.** The remote has no live editing (the image copies sources), so dev mode bought nothing there.
- **Light palette for the icon** (the blue tile with a white bubble), rather than the dark one. A home-screen icon can't follow the theme, and the blue tile stays visible on dark wallpapers.
- **The list button is the existing ← in the agent header**, as in Telegram and Messenger on iOS, rather than a new ☰ button. The phone's swipe-back gesture still isn't wired (non-goal).

## 9. Left to the builder

- The exact `notifyBody` cut length and markdown stripping, within "readable one-line preview".
- The hook point for `broadcast` (see 6.3).
- Whether the keyboard needs a `visualViewport` fallback, decided from the user's device check (6.1).
