"""Notification log with a persisted delivery cursor (state.json `delivered`)."""

import time
from collections.abc import Callable
from pathlib import Path

from harness import append_jsonl

from .events import Events, State, read_jsonl


def fmt_chars(n: int) -> str:
    return f"{n / 1000:.1f}k chars" if n >= 1000 else f"{n} chars"


def fmt_dur(s: float) -> str:
    s = int(s)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{s % 3600 // 60:02d}m"


def msg_stub(i: int, chars: int, n_att: int) -> str:
    return f"[notify] msg #{i} from user · {fmt_chars(chars)} · {n_att} attachment{'' if n_att == 1 else 's'}"


def job_stub(i: int, code: int, dur: float) -> str:
    return f"[notify] job #{i} finished · exit {code} · {fmt_dur(dur)} · log .harness/jobs/{i}/out.log"


def lost_stub(i: int) -> str:
    return f"[notify] job #{i} lost (backend restarted)"


def send_failed_stub(i: int, err: str) -> str:
    return f"[notify] send #{i} failed · marimo export: {err}"


class Notifier:
    def __init__(self, path: Path, state: State, events: Events, on_new: Callable[[], None] = lambda: None):
        self.path, self.state, self.events, self.on_new = path, state, events, on_new
        rows = read_jsonl(path)
        self.seq = rows[-1]["seq"] if rows else 0
        delivered = state.data.get("delivered", 0)
        self.pending = [r for r in rows if r["seq"] > delivered]

    def add(self, text: str, **data) -> int:
        self.seq += 1
        row = {"seq": self.seq, "ts": time.time(), "text": text, **data}
        append_jsonl(self.path, row)
        self.pending.append(row)
        self.events.emit("notification", nseq=self.seq, text=text)
        self.on_new()
        return self.seq

    def has_pending(self) -> bool:
        return bool(self.pending)

    def deliver(self) -> list[str]:
        rows, self.pending = self.pending, []
        if rows:
            self.state.data["delivered"] = rows[-1]["seq"]
            self.state.save()
        return [r["text"] for r in rows]
