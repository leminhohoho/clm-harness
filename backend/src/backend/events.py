"""Append-only event log with live WebSocket fan-out, plus the state.json store."""

import asyncio
import json
import time
from pathlib import Path

from harness import append_jsonl, write_json


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict = json.loads(path.read_text()) if path.exists() else {}

    def save(self) -> None:
        write_json(self.path, self.data)


def tail(path: Path, state: State, key: str) -> list[dict]:
    """New complete JSONL rows since the byte offset persisted under state[key]."""
    pos = state.data.get(key, 0)
    if not path.exists() or path.stat().st_size <= pos:
        return []
    with open(path, "rb") as f:
        f.seek(pos)
        data = f.read()
    end = data.rfind(b"\n") + 1
    if not end:
        return []
    state.data[key] = pos + end
    state.save()
    return [json.loads(l) for l in data[:end].splitlines() if l.strip()]


class Events:
    def __init__(self, path: Path):
        self.path = path
        rows = read_jsonl(path)
        self.seq = rows[-1]["seq"] if rows else 0
        self.subs: set[asyncio.Queue] = set()

    def emit(self, type: str, **data) -> dict:
        self.seq += 1
        ev = {"seq": self.seq, "ts": time.time(), "type": type, **data}
        append_jsonl(self.path, ev)
        self.publish(ev)
        return ev

    def publish(self, msg: dict) -> None:
        """Push to live subscribers without persisting (ephemeral, e.g. metrics)."""
        for q in self.subs:
            q.put_nowait(msg)

    def since(self, seq: int) -> list[dict]:
        return [e for e in read_jsonl(self.path) if e["seq"] > seq]

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subs.discard(q)
