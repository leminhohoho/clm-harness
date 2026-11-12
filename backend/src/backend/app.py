"""HTTP routes + WebSocket over a registry of profiles (one agent runtime each)."""

import asyncio
import json
import os
import time
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from harness import write_json

from .context import diff_lines
from .notify import msg_stub
from .profiles import Profiles, Runtime

TOOL = {"type": "function", "function": {
    "name": "python",
    "description": "Run Python code as a fresh `uv run` process in the agent folder (`import harness as h`). "
                   "Returns exit code, duration and output, or a job id if it detaches after the foreground limit.",
    "parameters": {"type": "object", "properties": {"code": {"type": "string", "description": "Python source"}},
                   "required": ["code"]},
}}
FALLBACK_MODEL = "deepseek/deepseek-v4.1-flash"
RAW_HEADERS = {"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"}


def default_model(model_id: str):
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=model_id, base_url=os.environ.get("OPENAI_BASE_URL"),
                      api_key=os.environ.get("OPENAI_API_KEY"), timeout=600, max_retries=2).bind_tools([TOOL])


def gateway_models() -> list[str]:
    """Model ids the gateway serves on /chat/completions (claude-*/gpt-* are listed but don't work there)."""
    req = urllib.request.Request(os.environ.get("OPENAI_BASE_URL", "").rstrip("/") + "/models", headers={
        "Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}", "User-Agent": "clm-harness/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        ids = [m["id"] for m in json.load(r)["data"]]
    return [i for i in ids if not i.startswith(("claude-", "gpt-"))]


def inside(r: Runtime, path: str) -> Path:
    """`path` resolved within the profile folder (symlinks followed), else 404."""
    p = (r.dir / path).resolve()
    if not p.is_relative_to(r.dir):
        raise HTTPException(404)
    return p


class NewProfile(BaseModel):
    name: str
    model: str | None = None


class ModelChange(BaseModel):
    model: str = Field(min_length=1)


def create_app(data_dir: Path | None = None, model_factory=None) -> FastAPI:
    profiles = Profiles(Path(data_dir or os.environ.get("DATA_DIR", "/data")), model_factory or default_model)
    models: list[str] = []

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        profiles.load()
        yield
        await profiles.stop_all()

    app = FastAPI(lifespan=lifespan)

    @app.get("/api/profiles")
    async def list_profiles():
        return profiles.list()

    @app.post("/api/profiles")
    async def create_profile(p: NewProfile):
        return profiles.create(p.name, p.model or os.environ.get("DEFAULT_MODEL", FALLBACK_MODEL)).info()

    @app.delete("/api/profiles/{name}")
    async def delete_profile(name: str):
        await profiles.delete(name)
        return {"ok": True}

    @app.patch("/api/profiles/{name}")
    async def patch_profile(name: str, p: ModelChange):
        return profiles.set_model(name, p.model).info()

    @app.get("/api/models")
    async def list_models():
        if not models:
            try:
                models.extend(await asyncio.to_thread(gateway_models))
            except Exception:
                return []
        return models

    @app.post("/api/p/{name}/messages")
    async def post_message(name: str, text: str = Form(""), files: list[UploadFile] = []):
        r = profiles.get(name)
        root = r.hdir / "inbox"
        root.mkdir(exist_ok=True)
        i = max((int(p.name) for p in root.iterdir()), default=0) + 1
        d = root / str(i)
        (d / "files").mkdir(parents=True)
        names = []
        for f in files:
            fname = Path(f.filename or "").name
            if fname:
                (d / "files" / fname).write_bytes(await f.read())
                names.append(fname)
        write_json(d / "message.json", {"id": i, "text": text, "ts": time.time(), "attachments": names})
        r.events.emit("user_message", id=i, text=text, attachments=names)
        r.notifier.add(msg_stub(i, len(text), len(names)), msg=i)
        return {"id": i}

    @app.get("/api/p/{name}/context-edits/{n}")
    async def context_edit(name: str, n: int):
        hdir = profiles.get(name).hdir
        b, a = (hdir / "context-edits" / f"{n}.{k}.md" for k in ("before", "after"))
        if not (b.is_file() and a.is_file()):
            raise HTTPException(404)
        return {"n": n, "lines": diff_lines(b.read_text(), a.read_text())}

    @app.get("/api/p/{name}/events")
    async def events_page(name: str, before: int | None = None, limit: int = 200, types: str = ""):
        return profiles.get(name).events.page(before, min(max(limit, 1), 1000), set(types.split(",")) if types else None)

    @app.get("/api/p/{name}/metrics")
    async def get_metrics(name: str):
        return profiles.get(name).metrics.snapshot()

    @app.post("/api/p/{name}/agent/stop")
    async def stop(name: str):
        profiles.get(name).agent.stop()
        return {"ok": True}

    @app.get("/api/p/{name}/tree")
    async def tree(name: str, path: str = ""):
        d = inside(profiles.get(name), path)
        if not d.is_dir():
            raise HTTPException(404)
        out = []
        for e in d.iterdir():
            try:
                st, isdir = e.stat(), e.is_dir()
            except OSError:  # dangling symlink
                continue
            out.append({"name": e.name, "dir": isdir, "size": 0 if isdir else st.st_size, "mtime": st.st_mtime})
        return sorted(out, key=lambda e: (not e["dir"], e["name"].lower()))

    @app.get("/api/p/{name}/raw/{path:path}")
    async def raw(name: str, path: str, download: bool = False):
        p = inside(profiles.get(name), path)
        if not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, headers=RAW_HEADERS, filename=p.name if download else None)

    @app.websocket("/ws/{name}")
    async def ws(sock: WebSocket, name: str, after: int = 0):
        await sock.accept()
        r = profiles.runtimes.get(name)
        if r is None:
            return await sock.close(code=4404)
        q = r.events.subscribe()

        async def pump():
            await sock.send_json(r.metrics.snapshot())
            last = after
            for e in r.events.since(after):
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
            r.events.unsubscribe(q)

    return app
