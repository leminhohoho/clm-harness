import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import ReactMarkdown from "react-markdown";
import { Virtuoso, type VirtuosoHandle } from "react-virtuoso";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { api, chatItems, firstIndex, fmtSize, isImage, openedIds, rawUrl, timeOf, type ChatItem, type Ev } from "./derive";
import { useFresh, useHistory } from "./ws";

export const Md = memo(({ text }: { text: string }) => (
  <div className="md">
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>{text}</ReactMarkdown>
  </div>
));

export const Icon = ({ d, size = 24 }: { d: string; size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden><path d={d} /></svg>
);
const CLIP = "M16.5 6v11.5c0 2.21-1.79 4-4 4s-4-1.79-4-4V5c0-1.38 1.12-2.5 2.5-2.5s2.5 1.12 2.5 2.5v10.5c0 .55-.45 1-1 1s-1-.45-1-1V6H10v9.5c0 1.38 1.12 2.5 2.5 2.5s2.5-1.12 2.5-2.5V5c0-2.21-1.79-4-4-4S7 2.79 7 5v12.5c0 3.04 2.46 5.5 5.5 5.5s5.5-2.46 5.5-5.5V6h-1.5z";
const PLANE = "M2.01 21 23 12 2.01 3 2 10l15 2-15 2z";
const FILE = "M6 2c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6H6zm7 7V3.5L18.5 9H13z";
const TYPES = ["user_message", "agent_message"];

/** Virtuoso header/footer: a "loading…" line while an older page is on its way, and some breathing room. */
export const Loading = ({ context }: { context?: { loading: boolean } }) => (
  <div className="list-edge dim">{context?.loading ? "loading…" : ""}</div>
);
export const Gap = () => <div className="list-edge" />;
const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
/** Follow new rows only when already at the bottom. */
export const follow = (atBottom: boolean) => (atBottom ? (still ? "auto" : "smooth") : false);

/**
 * Virtuoso follows new rows, not a resizing viewport or rows growing late (composer, live card, images): keep the
 * bottom pinned through its handle (direct scrollTop writes get undone). Virtuoso also scrolls on resize itself,
 * so only scrolls right after user input may unpin.
 */
export function usePinnedBottom() {
  const list = useRef<VirtuosoHandle>(null);
  const [el, setEl] = useState<HTMLElement | null>(null);
  useEffect(() => {
    if (!el) return;
    let pinned = true, input = 0;
    const onInput = () => void (input = Date.now());
    const onScroll = () => void (Date.now() - input < 500 && (pinned = el.scrollHeight - el.scrollTop - el.clientHeight < 60));
    const ro = new ResizeObserver(() => void (pinned && list.current?.scrollTo({ top: Number.MAX_SAFE_INTEGER })));
    const evs = ["wheel", "touchmove", "keydown", "pointerdown"] as const;
    evs.forEach((t) => el.addEventListener(t, onInput, { passive: true }));
    el.addEventListener("scroll", onScroll);
    ro.observe(el);
    const body = el.querySelector("[data-testid=virtuoso-item-list]"); // grows when a row grows (late images)
    if (body) ro.observe(body);
    return () => (ro.disconnect(), el.removeEventListener("scroll", onScroll), evs.forEach((t) => el.removeEventListener(t, onInput)));
  }, [el]);
  return { list, scroller: useCallback((e: HTMLElement | Window | null) => setEl(e instanceof HTMLElement ? e : null), []) };
}

/** Images inline (click opens raw), other files as download rows; same for both directions. */
function Attachments({ profile, dir, files }: { profile: string; dir: string; files: { name: string; size?: number }[] }) {
  if (!files.length) return null;
  return (
    <div className="att">
      {files.map(({ name, size }) => {
        const path = `${dir}/${name}`;
        return isImage(name) ? (
          <a key={name} href={rawUrl(profile, path)} target="_blank" rel="noreferrer"><img src={rawUrl(profile, path)} alt={name} /></a>
        ) : (
          <a key={name} className="fileref" href={rawUrl(profile, path, true)}>
            <span className="ic"><Icon d={FILE} size={20} /></span>
            <span><b>{name}</b>{size != null && <span className="dim">{fmtSize(size)}</span>}</span>
          </a>
        );
      })}
    </div>
  );
}

type ItemProps = { item: ChatItem; profile: string; fresh: boolean };

// events never change once written, so seq + opened identify a row's content
const Item = memo(function Item({ item, profile, fresh }: ItemProps) {
  if (item.type === "user_message")
    return (
      <div className={`msg you ${fresh ? "fresh" : ""}`}>
        <div className="bubble out">
          <Attachments profile={profile} dir={`.harness/inbox/${item.id}/files`} files={item.attachments.map((name) => ({ name }))} />
          {item.text && <span className="pre">{item.text}</span>}
          <span className="meta" title={item.opened ? "opened by the agent" : "not opened yet"}>
            {timeOf(item.ts)} {item.opened ? "✓✓" : "✓"}
          </span>
        </div>
      </div>
    );
  return (
    <div className={`msg ${fresh ? "fresh" : ""}`}>
      <div className="bubble in">
        <Attachments profile={profile} dir={`.harness/outbox/${item.id}`} files={item.files} />
        <Md text={item.text} />
        <span className="meta">{timeOf(item.ts)}</span>
      </div>
    </div>
  );
}, (a: ItemProps, b: ItemProps) => a.item.seq === b.item.seq && a.fresh === b.fresh && a.profile === b.profile
  && (a.item.type === "user_message" && a.item.opened) === (b.item.type === "user_message" && b.item.opened));

/** Holds its own draft, so typing re-renders only this component. */
function Composer({ profile }: { profile: string }) {
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const area = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const el = area.current!;
    el.style.height = "auto"; // grow with the content; CSS caps it at 40vh, then it scrolls
    el.style.height = `${el.scrollHeight}px`;
  }, [text]);

  const send = async () => {
    if (busy || (!text.trim() && !files.length)) return;
    const form = new FormData();
    form.append("text", text);
    files.forEach((f) => form.append("files", f));
    setBusy(true);
    try {
      const r = await fetch(`${api(profile)}/messages`, { method: "POST", body: form });
      if (r.ok) {
        setText("");
        setFiles([]);
      }
    } finally {
      setBusy(false);
    }
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  return (
    <div
      className="composer"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        setFiles((f) => [...f, ...Array.from(e.dataTransfer.files)]);
      }}
    >
      {files.length > 0 && (
        <div className="pending">
          {files.map((f, i) => (
            <button key={i} className="chip" title="remove" onClick={() => setFiles(files.filter((_, j) => j !== i))}>
              {f.name} ×
            </button>
          ))}
        </div>
      )}
      <div className="bar-in">
        <button className="icon" onClick={() => fileInput.current?.click()} title="attach files"><Icon d={CLIP} /></button>
        <input ref={fileInput} type="file" multiple hidden
          onChange={(e) => setFiles((f) => [...f, ...Array.from(e.target.files ?? [])])} />
        <textarea
          ref={area}
          autoFocus
          rows={3}
          value={text}
          placeholder="Message (enter to send, shift+enter newline, drop files)"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
        />
        <button className="icon send" onClick={() => void send()} disabled={busy} title="send"><Icon d={PLANE} size={22} /></button>
      </div>
    </div>
  );
}

export const ChatView = memo(function ChatView({ live, profile }: { live: Ev[] | null; profile: string }) {
  const { items, loadOlder, loading, primed, pivot } = useHistory(profile, TYPES, live, 50);
  const opened = useMemo(() => openedIds(live ?? []), [live]);
  const rows = useMemo(() => chatItems(items, opened), [items, opened]);
  const fresh = useFresh(live);
  const pin = usePinnedBottom();

  return (
    <div className="view">
      {!primed ? <div className="scroll dim empty">loading…</div>
        : !rows.length ? <div className="scroll dim empty">no messages yet — the agent idles until you write.</div>
        : (
          <Virtuoso
            className="scroll list"
            ref={pin.list}
            scrollerRef={pin.scroller}
            data={rows}
            context={{ loading }}
            firstItemIndex={firstIndex(rows, pivot)}
            initialTopMostItemIndex={{ index: "LAST", align: "end" }}
            alignToBottom
            followOutput={follow}
            atBottomThreshold={60}
            increaseViewportBy={{ top: 800, bottom: 400 }}
            startReached={loadOlder}
            computeItemKey={(_, it) => `${it.type}${it.seq}`}
            components={{ Header: Loading, Footer: Gap }}
            itemContent={(_, it) => <div className="vrow"><Item item={it} profile={profile} fresh={fresh(it.seq)} /></div>}
          />
        )}
      <Composer profile={profile} />
    </div>
  );
});
