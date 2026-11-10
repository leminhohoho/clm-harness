from backend.events import State, tail
from backend.outbox import Outbox


def test_tail_consumes_only_complete_lines(tmp_path):
    p, st = tmp_path / "x.jsonl", State(tmp_path / "s.json")
    assert tail(p, st, "pos") == []
    p.write_text('{"a": 1}\n{"a": 2}\n{"a"')
    assert tail(p, st, "pos") == [{"a": 1}, {"a": 2}]
    assert tail(p, st, "pos") == []
    with open(p, "a") as f:
        f.write(': 3}\n')
    assert tail(p, State(tmp_path / "s.json"), "pos") == [{"a": 3}]


def ob(parts):
    return Outbox(parts.hdir, parts.state, parts.events)


def test_send_emits_agent_message_with_files_once(parts, monkeypatch):
    import harness
    monkeypatch.chdir(parts.agent)
    (parts.agent / "plot.png").write_bytes(b"png")
    harness.send("**hi**")
    harness.send("chart", files=["plot.png"])
    ob(parts).poll()
    ob(parts).poll()
    msgs = [{k: m[k] for k in ("id", "text", "files")} for m in parts.of("agent_message")]
    assert msgs == [{"id": 1, "text": "**hi**", "files": []},
                    {"id": 2, "text": "chart", "files": [{"name": "plot.png", "size": 3}]}]
