"""Agent → user messages: tail outbox.jsonl (attachments already copied to outbox/<id>/ by h.send)."""

from pathlib import Path

from .events import Events, State, tail


class Outbox:
    def __init__(self, hdir: Path, state: State, events: Events):
        self.path, self.state, self.events = Path(hdir) / "outbox.jsonl", state, events

    def poll(self) -> None:
        for row in tail(self.path, self.state, "outbox_pos"):
            self.events.emit("agent_message", id=row["id"], text=row["text"], files=row["files"])
