import asyncio
import time

import pytest

from backend.events import Events, State
from backend.metrics import Metrics
from backend.notify import Notifier


class Parts:
    def __init__(self, root):
        self.agent = root / "agent"
        self.hdir = self.agent / ".harness"
        self.hdir.mkdir(parents=True)
        (self.agent / "pyproject.toml").write_text(
            '[project]\nname = "agent"\nversion = "0"\nrequires-python = ">=3.12"\ndependencies = []\n')
        self.state = State(self.hdir / "state.json")
        self.events = Events(self.hdir / "events.jsonl")
        self.notifier = Notifier(self.hdir / "notifications.jsonl", self.state, self.events)
        self.metrics = Metrics(self.state, self.events, budget=1000, prices={})

    def of(self, type):
        return [e for e in self.events.since(0) if e["type"] == type]


@pytest.fixture
def parts(tmp_path, monkeypatch):
    p = Parts(tmp_path)
    monkeypatch.setenv("HARNESS_DIR", str(p.hdir))
    return p


async def until(pred, timeout=10.0, tick=None):
    end = time.time() + timeout
    while time.time() < end:
        if tick:
            tick()
        if pred():
            return True
        await asyncio.sleep(0.05)
    return False
