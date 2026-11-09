"""Agent-side harness library. Talks to the backend only through files in $HARNESS_DIR."""

import fcntl
import json
import os
import signal
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

__all__ = ["inbox", "send", "spawn", "jobs", "kill", "llm", "start_job"]


def _dir() -> Path:
    return Path(os.environ.get("HARNESS_DIR", ".harness"))


def _read_json(p: Path):
    return json.loads(p.read_text())


def write_json(p: Path, data) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, p)


def append_jsonl(p: Path, row: dict) -> None:
    with open(p, "a") as f:
        f.write(json.dumps(row) + "\n")


# --- inbox -----------------------------------------------------------------

@dataclass
class Message:
    id: int
    text: str
    attachments: list[Path]
    ts: float


class Inbox:
    def __getitem__(self, i: int) -> Message:
        d = _dir() / "inbox" / str(i)
        if not (d / "message.json").exists():
            raise KeyError(i)
        m = _read_json(d / "message.json")
        (d / "opened").touch()
        return Message(i, m["text"], [d / "files" / n for n in m["attachments"]], m["ts"])

    def unopened(self) -> list[int]:
        root = _dir() / "inbox"
        if not root.exists():
            return []
        return sorted(int(d.name) for d in root.iterdir() if (d / "message.json").exists() and not (d / "opened").exists())


inbox = Inbox()


# --- outbox ----------------------------------------------------------------

def send(content: str, backend: str = "markdown") -> int:
    """Send a message to the user. backend="marimo": content is a notebook path in the agent folder."""
    if backend not in ("markdown", "marimo"):
        raise ValueError(f"unknown backend {backend!r} (use 'markdown' or 'marimo')")
    with open(_dir() / "outbox.jsonl", "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        sid = sum(1 for _ in f) + 1
        f.write(json.dumps({"id": sid, "ts": time.time(), "backend": backend, "content": str(content)}) + "\n")
    return sid


# --- jobs ------------------------------------------------------------------

_WRAP = 'sh -c "$0" >"$1/out.log" 2>&1; echo $? >"$1/exit.tmp"; mv "$1/exit.tmp" "$1/exit"'


def start_job(cmd: str, kind: str = "spawn", cwd=None, env: dict | None = None, hdir: Path | None = None, **extra):
    """Start `cmd` as a job in its own session. Returns (id, Popen)."""
    root = (hdir or _dir()) / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    jid = max((int(p.name) for p in root.iterdir() if p.name.isdigit()), default=0) + 1
    while True:
        try:
            (root / str(jid)).mkdir()
            break
        except FileExistsError:
            jid += 1
    jd = (root / str(jid)).resolve()
    p = subprocess.Popen(["sh", "-c", _WRAP, cmd, str(jd)], cwd=cwd, env=env, start_new_session=True,
                         stdin=subprocess.DEVNULL)
    threading.Thread(target=p.wait, daemon=True).start()  # reap
    write_json(jd / "meta.json", {"id": jid, "cmd": cmd, "kind": kind, "pid": p.pid, "status": "running",
                                  "start": time.time(), **extra})
    return jid, p


def spawn(cmd: str) -> int:
    """Run a shell command as a background job (cwd = agent folder). Returns job id."""
    return start_job(cmd)[0]


def _jobs() -> list[dict]:
    root = _dir() / "jobs"
    if not root.exists():
        return []
    metas = [_read_json(p / "meta.json") for p in root.iterdir() if (p / "meta.json").exists()]
    return sorted(metas, key=lambda m: m["id"])


def kill(jid: int) -> None:
    p = _dir() / "jobs" / str(jid) / "meta.json"
    meta = _read_json(p)
    write_json(p, {**meta, "status": "killed", "end": time.time()})
    try:
        os.killpg(meta["pid"], signal.SIGTERM)
    except ProcessLookupError:
        pass


def __getattr__(name):
    if name == "jobs":
        return _jobs()
    raise AttributeError(name)


# --- llm -------------------------------------------------------------------

def _complete(prompt: str) -> str:
    model = os.environ["SUB_MODEL"]
    req = urllib.request.Request(
        os.environ["OPENAI_BASE_URL"].rstrip("/") + "/chat/completions",
        data=json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                 "User-Agent": "clm-harness/0.1"},  # default Python-urllib UA is blocked by some gateways
    )
    t = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        res = json.load(r)
    u = res.get("usage") or {}
    append_jsonl(_dir() / "usage.jsonl", {
        "ts": time.time(), "model": model, "latency": time.time() - t,
        "in": u.get("prompt_tokens", 0), "out": u.get("completion_tokens", 0),
        "cached": (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0,
    })
    return res["choices"][0]["message"]["content"] or ""


def llm(prompt: str | list[str]) -> str | list[str]:
    """Sub-call a plain LM (SUB_MODEL). A list of prompts runs concurrently and returns a list."""
    if isinstance(prompt, str):
        return _complete(prompt)
    with ThreadPoolExecutor(max_workers=min(16, len(prompt)) or 1) as ex:
        return list(ex.map(_complete, prompt))
