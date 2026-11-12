"""Profiles: one agent runtime per folder in DATA_DIR that holds a profile.json."""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from fastapi import HTTPException

from harness import write_json

from .events import Events, State, tail
from .executor import Executor
from .loop import Agent
from .metrics import Metrics
from .notify import Notifier
from .outbox import Outbox

log = logging.getLogger("backend")
PROMPT = Path(__file__).parent / "prompts" / "system.md"
NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")


class Runtime:
    """Everything one agent needs: files, event log, notifier, metrics, executor, loop and watcher."""

    def __init__(self, dir: Path, model_factory: Callable):
        self.dir = Path(dir).resolve()
        self.hdir = self.dir / ".harness"
        self.model = json.loads((self.dir / "profile.json").read_text())["model"]
        self.model_factory = model_factory
        self.tasks: list[asyncio.Task] = []

    def start(self) -> None:
        self.hdir.mkdir(parents=True, exist_ok=True)
        if not (self.dir / "pyproject.toml").exists():
            subprocess.run(["uv", "init", "-q", "--bare", "--name", "agent", "--no-workspace"], cwd=self.dir, check=True)
        budget = int(os.environ.get("CONTEXT_BUDGET", "200000"))
        timeout = float(os.environ.get("FOREGROUND_TIMEOUT", "60"))
        self.state, self.events = State(self.hdir / "state.json"), Events(self.hdir / "events.jsonl")
        self.notifier = Notifier(self.hdir / "notifications.jsonl", self.state, self.events)
        self.metrics = Metrics(self.state, self.events, budget, json.loads(os.environ.get("PRICES") or "{}"))
        self.executor = Executor(self.dir, self.notifier, self.events, self.metrics, timeout)
        self.executor.startup()
        system = PROMPT.read_text().replace("{FOREGROUND_TIMEOUT}", f"{timeout:g}")
        self.agent = Agent(self.hdir, system, self.model_factory(self.model), self.model, self.executor,
                           self.notifier, self.events, self.metrics, budget)
        self.notifier.on_new = self.agent.poke
        self.outbox = Outbox(self.hdir, self.state, self.events)
        self.tasks = [asyncio.create_task(self.agent.run_forever()), asyncio.create_task(self.watch())]

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def watch(self) -> None:
        while True:
            try:
                self.poll()
            except Exception:
                log.exception("watcher %s", self.dir.name)
            await asyncio.sleep(0.5)

    def poll(self) -> None:
        self.executor.poll()
        self.outbox.poll()
        for u in tail(self.hdir / "usage.jsonl", self.state, "usage_pos"):
            self.metrics.usage("sub", u["model"], u["in"], u["out"], u["cached"], u["latency"])
        opened = self.state.data.setdefault("opened", [])
        inbox = self.hdir / "inbox"
        for d in inbox.iterdir() if inbox.exists() else []:
            if int(d.name) not in opened and (d / "opened").exists():
                opened.append(int(d.name))
                self.state.save()
                self.events.emit("message_opened", id=int(d.name))

    def info(self) -> dict:
        return {"name": self.dir.name, "model": self.model, "state": self.agent.state}


class Profiles:
    def __init__(self, data_dir: Path, model_factory: Callable):
        self.root, self.model_factory = Path(data_dir).resolve(), model_factory
        self.runtimes: dict[str, Runtime] = {}

    def _start(self, d: Path) -> Runtime:
        r = self.runtimes[d.name] = Runtime(d, self.model_factory)
        r.start()
        return r

    def load(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for d in sorted(self.root.iterdir()):
            if (d / "profile.json").is_file():
                self._start(d)

    def get(self, name: str) -> Runtime:
        if name not in self.runtimes:
            raise HTTPException(404, f"no profile {name!r}")
        return self.runtimes[name]

    def list(self) -> list[dict]:
        return [self.runtimes[n].info() for n in sorted(self.runtimes)]

    def create(self, name: str, model: str) -> Runtime:
        if not NAME.fullmatch(name):
            raise HTTPException(422, "name: a-z, 0-9, _ or -, up to 32 chars, starting with a letter or digit")
        d = self.root / name
        if d.exists():
            raise HTTPException(409, f"profile {name!r} exists")
        d.mkdir()
        write_json(d / "profile.json", {"model": model, "created": time.time()})
        return self._start(d)

    def set_model(self, name: str, model: str) -> Runtime:
        """Persist the new main model and swap it in; an in-flight call finishes on the old one."""
        r = self.get(name)
        p = r.dir / "profile.json"
        write_json(p, {**json.loads(p.read_text()), "model": model})
        r.model = model
        r.agent.model, r.agent.model_name = r.model_factory(model), model
        return r

    async def delete(self, name: str) -> None:
        r = self.get(name)
        del self.runtimes[name]
        await r.stop()
        r.executor.kill_all()
        shutil.rmtree(r.dir)

    async def stop_all(self) -> None:
        await asyncio.gather(*(r.stop() for r in self.runtimes.values()))
