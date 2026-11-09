import asyncio

from backend.events import Events, State
from backend.notify import Notifier, fmt_chars, fmt_dur, job_stub, msg_stub


def test_state_persists(tmp_path):
    s = State(tmp_path / "state.json")
    s.data["x"] = 1
    s.save()
    assert State(tmp_path / "state.json").data == {"x": 1}


async def test_events_seq_persist_and_broadcast(tmp_path):
    ev = Events(tmp_path / "events.jsonl")
    q = ev.subscribe()
    a = ev.emit("state", state="idle")
    ev.publish({"type": "metrics", "x": 1})
    b = ev.emit("error", text="boom")
    assert (a["seq"], b["seq"]) == (1, 2) and a["type"] == "state" and a["ts"] > 0
    got = [q.get_nowait() for _ in range(3)]
    assert [g["type"] for g in got] == ["state", "metrics", "error"]
    ev.unsubscribe(q)
    ev2 = Events(tmp_path / "events.jsonl")
    assert [e["seq"] for e in ev2.since(0)] == [1, 2]  # metrics not persisted
    assert [e["seq"] for e in ev2.since(1)] == [2]
    assert ev2.emit("x")["seq"] == 3


def test_notifier_delivers_once_across_restarts(tmp_path):
    st, ev = State(tmp_path / "s.json"), Events(tmp_path / "e.jsonl")
    pokes = []
    n = Notifier(tmp_path / "n.jsonl", st, ev, on_new=lambda: pokes.append(1))
    n.add("[notify] a")
    n.add("[notify] b")
    assert pokes == [1, 1]
    assert [e["text"] for e in ev.since(0) if e["type"] == "notification"] == ["[notify] a", "[notify] b"]
    assert n.has_pending()
    assert n.deliver() == ["[notify] a", "[notify] b"]
    assert n.deliver() == [] and not n.has_pending()
    n.add("[notify] c")
    n2 = Notifier(tmp_path / "n.jsonl", State(tmp_path / "s.json"), ev)
    assert n2.deliver() == ["[notify] c"]
    assert n2.add("[notify] d") == 4


def test_stub_formats():
    assert fmt_chars(34) == "34 chars" and fmt_chars(3400) == "3.4k chars"
    assert fmt_dur(45) == "45s" and fmt_dur(192) == "3m12s" and fmt_dur(7380) == "2h03m"
    assert msg_stub(12, 3400, 1) == "[notify] msg #12 from user · 3.4k chars · 1 attachment"
    assert msg_stub(1, 5, 2).endswith("· 2 attachments")
    assert job_stub(7, 0, 7380) == "[notify] job #7 finished · exit 0 · 2h03m · log .harness/jobs/7/out.log"
