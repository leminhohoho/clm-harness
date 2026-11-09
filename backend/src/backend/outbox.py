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


def themed(html: str, css: str, theme: str) -> str:
    """Apply app theme to a raw marimo export at serve time."""
    html = html.replace("<html", f'<html data-theme="{theme}"', 1)
    html = re.sub(r'"theme": "(light|dark|system)"', f'"theme": "{theme}"', html)
    return html.replace("</head>", f"<style>{css}</style></head>", 1)


class Outbox:
    def __init__(self, agent_dir: Path, state: State, events: Events, notifier: Notifier):
        self.agent, self.hdir = Path(agent_dir), Path(agent_dir) / ".harness"
        self.state, self.events, self.notifier = state, events, notifier
        self.tasks: set[asyncio.Task] = set()

    def poll(self) -> None:
        for row in tail(self.hdir / "outbox.jsonl", self.state, "outbox_pos"):
            if row["backend"] == "marimo":
                t = asyncio.create_task(self.export(row))
                self.tasks.add(t)
                t.add_done_callback(self.tasks.discard)
            else:
                self.events.emit("agent_message", id=row["id"], backend="markdown", text=row["content"])

    async def export(self, row: dict) -> None:
        sid, nb = row["id"], row["content"]
        (self.hdir / "outbox").mkdir(exist_ok=True)
        out = self.hdir / "outbox" / f"{sid}.html"
        p = await asyncio.create_subprocess_exec(
            "uv", "run", "-q", "--with", "marimo", "marimo", "export", "html", nb, "-o", str(out),
            cwd=self.agent, env=agent_env(self.hdir), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        log = (await p.communicate())[0].decode(errors="replace")
        if p.returncode == 0 and out.exists():
            self.events.emit("agent_message", id=sid, backend="marimo", path=nb, url=f"/api/outbox/{sid}.html")
        else:
            err = _first_error(log)
            self.events.emit("error", text=f"send #{sid} marimo export failed: {err}", send=sid, log=log[-5000:])
            self.notifier.add(send_failed_stub(sid, err))
