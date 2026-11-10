import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { api, chatItems, fmtSize, isImage, rawUrl, timeOf, type ChatItem, type Ev } from "./derive";

export const Md = ({ text }: { text: string }) => (
  <div className="md">
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>{text}</ReactMarkdown>
  </div>
);

const Icon = ({ d, size = 24 }: { d: string; size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden><path d={d} /></svg>
);
const CLIP = "M16.5 6v11.5c0 2.21-1.79 4-4 4s-4-1.79-4-4V5c0-1.38 1.12-2.5 2.5-2.5s2.5 1.12 2.5 2.5v10.5c0 .55-.45 1-1 1s-1-.45-1-1V6H10v9.5c0 1.38 1.12 2.5 2.5 2.5s2.5-1.12 2.5-2.5V5c0-2.21-1.79-4-4-4S7 2.79 7 5v12.5c0 3.04 2.46 5.5 5.5 5.5s5.5-2.46 5.5-5.5V6h-1.5z";
const PLANE = "M2.01 21 23 12 2.01 3 2 10l15 2-15 2z";
const FILE = "M6 2c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6H6zm7 7V3.5L18.5 9H13z";

export function useStickyScroll(dep: unknown) {
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const last = useRef(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // content can grow after render (images load late): keep following the bottom
    const follow = () => void (stick.current && (el.scrollTop = el.scrollHeight));
    follow();
    const ro = new ResizeObserver(follow);
    for (const c of el.children) ro.observe(c);
    return () => ro.disconnect();
  }, [dep]);
  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    // only scrolling up unsticks: our own scroll's event can land after the content grew again
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40 || (stick.current && el.scrollTop >= last.current);
    last.current = el.scrollTop;
  };
  return { ref, onScroll };
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

function Item({ item, profile }: { item: ChatItem; profile: string }) {
  if (item.type === "user_message")
    return (
      <div className="msg you">
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
    <div className="msg">
      <div className="bubble in">
        <Attachments profile={profile} dir={`.harness/outbox/${item.id}`} files={item.files} />
        <span className="meta">{timeOf(item.ts)}</span>
        <Md text={item.text} />
      </div>
    </div>
  );
}

export function ChatView({ events, profile }: { events: Ev[]; profile: string }) {
  const items = chatItems(events);
  const { ref, onScroll } = useStickyScroll(items.length);
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

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
    <div className="view">
      <div className="scroll chat" ref={ref} onScroll={onScroll}>
        {items.length === 0 && <div className="dim empty">no messages yet — the agent idles until you write.</div>}
        {items.map((it) => <Item key={`${it.type}${it.seq}`} item={it} profile={profile} />)}
      </div>
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
            autoFocus
            rows={Math.min(8, text.split("\n").length)}
            value={text}
            placeholder="Message (enter to send, shift+enter newline, drop files)"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKey}
          />
          <button className="icon send" onClick={() => void send()} disabled={busy} title="send"><Icon d={PLANE} size={22} /></button>
        </div>
      </div>
    </div>
  );
}
