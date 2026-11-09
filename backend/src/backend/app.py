"""HTTP routes + WebSocket; wires the agent loop and the background watcher."""

import asyncio
import json
import logging
import os
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse

from harness import write_json

from .events import Events, State, tail
from .executor import Executor
from .loop import Agent
from .metrics import Metrics
from .notify import Notifier, msg_stub
from .outbox import Outbox, themed

log = logging.getLogger("backend")
PROMPT = Path(__file__).parent / "prompts" / "system.md"
THEME_CSS = Path(__file__).resolve().parents[3] / "frontend" / "src" / "marimo-theme.css"
TOOL = {"type": "function", "function": {
    "name": "python",
    "description": "Run Python code as a fresh `uv run` process in the agent folder (`import harness as h`). "
                   "Returns exit code, duration and output, or a job id if it detaches after the foreground limit.",
    "parameters": {"type": "object", "properties": {"code": {"type": "string", "description": "Python source"}},
                   "required": ["code"]},
}}


def default_model():
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=os.environ["MODEL"], base_url=os.environ.get("OPENAI_BASE_URL"),
                      api_key=os.environ.get("OPENAI_API_KEY"), timeout=600, max_retries=2).bind_tools([TOOL])


def create_app(agent_dir: Path | None = None, model=None) -> FastAPI:
    agent = Path(agent_dir or os.environ.get("AGENT_DIR", "/agent")).resolve()
    hdir = agent / ".harness"
    budget = int(os.environ.get("CONTEXT_BUDGET", "200000"))
    s: dict = {}

    async def watch():
        while True:
            try:
                s["executor"].poll()
                s["outbox"].poll()
                for u in tail(hdir / "usage.jsonl", s["state"], "usage_pos"):
                    s["metrics"].usage("sub", u["model"], u["in"], u["out"], u["cached"], u["latency"])
                opened = s["state"].data.setdefault("opened", [])
                for d in (hdir / "inbox").iterdir() if (hdir / "inbox").exists() else []:
                    if int(d.name) not in opened and (d / "opened").exists():
                        opened.append(int(d.name))
                        s["state"].save()
                        s["events"].emit("message_opened", id=int(d.name))
            except Exception:
                log.exception("watcher")
            await asyncio.sleep(0.5)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hdir.mkdir(parents=True, exist_ok=True)
        if not (agent / "pyproject.toml").exists():
            subprocess.run(["uv", "init", "-q", "--bare", "--name", "agent", "--no-workspace"], cwd=agent, check=True)
        state, events = State(hdir / "state.json"), Events(hdir / "events.jsonl")
        notifier = Notifier(hdir / "notifications.jsonl", state, events)
        metrics = Metrics(state, events, budget, json.loads(os.environ.get("PRICES") or "{}"))
        timeout = float(os.environ.get("FOREGROUND_TIMEOUT", "60"))
        executor = Executor(agent, notifier, events, metrics, timeout)
        executor.startup()
        system = PROMPT.read_text().replace("{FOREGROUND_TIMEOUT}", f"{timeout:g}")
        loop = Agent(hdir, system, model or default_model(), os.environ.get("MODEL", ""), executor,
                     notifier, events, metrics, budget)
        notifier.on_new = loop.poke
        s.update(state=state, events=events, notifier=notifier, metrics=metrics, executor=executor, agent=loop,
                 outbox=Outbox(agent, state, events, notifier))
        tasks = [asyncio.create_task(loop.run_forever()), asyncio.create_task(watch())]
        yield
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(lifespan=lifespan)

    @app.post("/api/messages")
    async def post_message(text: str = Form(""), files: list[UploadFile] = []):
        root = hdir / "inbox"
        root.mkdir(exist_ok=True)
        i = max((int(p.name) for p in root.iterdir()), default=0) + 1
        d = root / str(i)
        (d / "files").mkdir(parents=True)
        names = []
        for f in files:
            name = Path(f.filename or "").name
            if name:
                (d / "files" / name).write_bytes(await f.read())
                names.append(name)
        write_json(d / "message.json", {"id": i, "text": text, "ts": time.time(), "attachments": names})
        s["events"].emit("user_message", id=i, text=text, attachments=names)
        s["notifier"].add(msg_stub(i, len(text), len(names)), msg=i)
        return {"id": i}

    @app.get("/api/inbox/{i}/files/{name}")
    async def attachment(i: int, name: str):
        p = hdir / "inbox" / str(i) / "files" / name
        if Path(name).name != name or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p)

    @app.get("/api/outbox/{i}.html")
    async def outbox_html(i: int, theme: str = "light"):
        p = hdir / "outbox" / f"{i}.html"
        if not p.is_file():
            raise HTTPException(404)
        css_path = Path(os.environ.get("MARIMO_THEME_CSS", THEME_CSS))
        css = css_path.read_text() if css_path.is_file() else ""
        return HTMLResponse(themed(p.read_text(), css, "dark" if theme == "dark" else "light"))

    @app.get("/api/metrics")
    async def get_metrics():
        return s["metrics"].snapshot()

    @app.post("/api/agent/stop")
    async def stop():
        s["agent"].stop()
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(sock: WebSocket, after: int = 0):
        await sock.accept()
        q = s["events"].subscribe()

        async def pump():
            await sock.send_json(s["metrics"].snapshot())
            last = after
            for e in s["events"].since(after):
                await sock.send_json(e)
                last = e["seq"]
            while True:
                m = await q.get()
                if "seq" in m:
                    if m["seq"] <= last:
                        continue
                    last = m["seq"]
                await sock.send_json(m)

        sender = asyncio.create_task(pump())
        try:
            while True:
                await sock.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
            s["events"].unsubscribe(q)

    return app
