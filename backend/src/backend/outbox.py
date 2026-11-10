"""Agent → user messages: tail outbox.jsonl; markdown passes through, marimo is exported to HTML."""

import asyncio
import re
from pathlib import Path

from .events import Events, State, tail
from .executor import agent_env
from .notify import Notifier, send_failed_stub


def _first_error(out: str) -> str:
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    return next((l for l in lines if "error" in l.lower()), lines[0] if lines else "unknown error")[:200]


# reports the page's content height so the sandboxed iframe can be sized without same-origin access
HEIGHT_JS = """<script>(() => {
  const html = document.documentElement;
  const post = () => parent.postMessage({ type: "marimo-height", height: html.getBoundingClientRect().height }, "*");
  addEventListener("load", post);
  new ResizeObserver(post).observe(html);
})();</script>"""


def themed(html: str, css: str, theme: str) -> str:
    """Apply app theme to a raw marimo export at serve time."""
    html = html.replace("<html", f'<html data-theme="{theme}"', 1)
    html = re.sub(r'"theme": "(light|dark|system)"', f'"theme": "{theme}"', html)
    return html.replace("</head>", f"<style>{css}</style>{HEIGHT_JS}</head>", 1)


class Outbox:
    def __init__(self, agent_dir: Path, state: State, events: Events, notifier: Notifier):
        self.agent, self.hdir = Path(agent_dir), Path(agent_dir) / ".harness"
        self.state, self.events, self.notifier = state, events, notifier
        self.queue: list[dict] = []
        self.task: asyncio.Task | None = None  # in-flight marimo export; later sends wait behind it

    def poll(self) -> None:
        self.queue += tail(self.hdir / "outbox.jsonl", self.state, "outbox_pos")
        if self.task is None:
            self._drain()

    def _drain(self) -> None:
        """Deliver queued sends in order; a marimo export blocks the rest until it finishes."""
        while self.queue:
            if self.queue[0]["backend"] == "marimo":
                self.task = asyncio.create_task(self._export_next())
                return
            row = self.queue.pop(0)
            self.events.emit("agent_message", id=row["id"], backend="markdown", text=row["content"])

    async def _export_next(self) -> None:
        try:
            await self.export(self.queue.pop(0))
        finally:
            self.task = None
        self._drain()

    async def export(self, row: dict) -> None:
        sid, nb = row["id"], row["content"]
        (self.hdir / "outbox").mkdir(exist_ok=True)
        out = self.hdir / "outbox" / f"{sid}.html"
        p = await asyncio.create_subprocess_exec(
            "uv", "run", "-q", "--with", "marimo", "marimo", "export", "html", "--no-include-code", nb, "-o", str(out),
            cwd=self.agent, env=agent_env(self.hdir), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        log = (await p.communicate())[0].decode(errors="replace")
        if p.returncode == 0 and out.exists():
            self.events.emit("agent_message", id=sid, backend="marimo", path=nb, url=f"/api/outbox/{sid}.html")
        else:
            err = _first_error(log)
            self.events.emit("error", text=f"send #{sid} marimo export failed: {err}", send=sid, log=log[-5000:])
            self.notifier.add(send_failed_stub(sid, err))
