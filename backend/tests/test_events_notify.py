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


def test_events_page_tail_before_types_and_opened(tmp_path):
    ev = Events(tmp_path / "events.jsonl")
    for i in range(1, 4):
        ev.emit("user_message", id=i, text=f"m{i}")
        ev.emit("state", state="idle")
    ev.emit("message_opened", id=2)
    size = ev.path.stat().st_size
    p = ev.page(None, 3, None)
    assert [e["seq"] for e in p["events"]] == [5, 6, 7] and p["more"]
    p = ev.page(5, 10, {"user_message"})
    assert [(e["id"], e["opened"]) for e in p["events"]] == [(1, False), (2, True)] and not p["more"]
    p = ev.page(6, 1, {"user_message", "state"})
    assert [e["seq"] for e in p["events"]] == [5] and p["more"] and p["events"][0]["opened"] is False
    assert ev.page(1, 5, None) == {"events": [], "more": False}
    assert ev.path.stat().st_size == size  # read-only


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
