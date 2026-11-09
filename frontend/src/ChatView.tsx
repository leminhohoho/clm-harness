import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { chatItems, type ChatItem, type Ev } from "./derive";

export const Md = ({ text }: { text: string }) => (
  <div className="md">
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>{text}</ReactMarkdown>
  </div>
);

export function useStickyScroll(dep: unknown) {
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [dep]);
  const onScroll = () => {
    const el = ref.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };
  return { ref, onScroll };
}

function Item({ item, theme }: { item: ChatItem; theme: string }) {
  if (item.type === "user_message")
    return (
      <div className="row">
        <span className="who you">you</span>
        <span className="gt">›</span>
        <div className="body">
          <div className="pre">{item.text}</div>
          <div className="meta">
            {item.attachments.map((a) => (
              <a key={a} href={`/api/inbox/${item.id}/files/${encodeURIComponent(a)}`} target="_blank" rel="noreferrer">
                [{a}]
              </a>
            ))}
            {item.opened ? <span className="ok">✓opened</span> : <span className="dim">unopened</span>}
          </div>
        </div>
      </div>
    );
  return (
    <div className="row">
      <span className="who agent">agent</span>
      <span className="gt">›</span>
      <div className="body">
        {item.backend === "markdown" ? (
          <Md text={item.text} />
        ) : (
          <figure className="marimo">
            <figcaption>marimo · {item.path}</figcaption>
            <iframe title={item.path} src={`${item.url}?theme=${theme}`} sandbox="allow-scripts allow-popups allow-downloads" />
          </figure>
        )}
      </div>
    </div>
  );
}

export function ChatView({ events, theme }: { events: Ev[]; theme: string }) {
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
      const r = await fetch("/api/messages", { method: "POST", body: form });
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
      <div className="scroll" ref={ref} onScroll={onScroll}>
        {items.length === 0 && <div className="dim empty">no messages yet — the agent idles until you write.</div>}
        {items.map((it) => <Item key={`${it.type}${it.seq}`} item={it} theme={theme} />)}
      </div>
      <div
        className="composer"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          setFiles((f) => [...f, ...Array.from(e.dataTransfer.files)]);
        }}
      >
        <span className="gt">›</span>
        <textarea
          autoFocus
          rows={Math.min(8, text.split("\n").length)}
          value={text}
          placeholder="message the agent (enter to send, shift+enter newline, drop files)"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
        />
        {files.map((f, i) => (
          <button key={i} className="chip" title="remove" onClick={() => setFiles(files.filter((_, j) => j !== i))}>
            [{f.name}] ×
          </button>
        ))}
        <button onClick={() => fileInput.current?.click()} title="attach files">+file</button>
        <input ref={fileInput} type="file" multiple hidden
          onChange={(e) => setFiles((f) => [...f, ...Array.from(e.target.files ?? [])])} />
        <button onClick={() => void send()} disabled={busy}>send</button>
      </div>
    </div>
  );
}
