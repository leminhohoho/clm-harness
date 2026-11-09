import json
import time

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from test_loop import FakeModel, say


def wait(pred, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL", "m")
    monkeypatch.setenv("PRICES", '{"m": {"in": 1, "out": 1, "cached": 1}}')
    monkeypatch.setenv("CONTEXT_BUDGET", "5000")
    css = tmp_path / "theme.css"
    css.write_text("/*THEME*/")
    monkeypatch.setenv("MARIMO_THEME_CSS", str(css))
    return tmp_path / "agent"


def client(agent, *script):
    return TestClient(create_app(agent, FakeModel(*script)))


def events(agent, type=None):
    p = agent / ".harness/events.jsonl"
    rows = [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []
    return [r for r in rows if type is None or r["type"] == type]


def test_message_flow_and_ws_backlog(env):
    with client(env, say("hello there")) as c:
        assert (env / "pyproject.toml").exists()  # agent folder initialised as uv project
        r = c.post("/api/messages", data={"text": "see attached"}, files=[("files", ("a.csv", b"1,2"))])
        assert r.status_code == 200 and r.json()["id"] == 1
        msg = json.loads((env / ".harness/inbox/1/message.json").read_text())
        assert msg["text"] == "see attached" and msg["attachments"] == ["a.csv"]
        assert wait(lambda: events(env, "llm_reasoning"))
        assert wait(lambda: events(env, "state") and events(env, "state")[-1]["state"] == "idle")
        n = events(env, "notification")[0]
        assert n["text"] == "[notify] msg #1 from user · 12 chars · 1 attachment"
        assert c.get("/api/inbox/1/files/a.csv").content == b"1,2"
        assert c.get("/api/inbox/1/files/..%2Fmessage.json").status_code == 404
        assert c.get("/api/metrics").json()["calls"]["main"] == 1
        with c.websocket_connect("/ws?after=0") as ws:
            first = ws.receive_json()
            assert first["type"] == "metrics"
            got = [ws.receive_json() for _ in range(len(events(env)))]
            assert [g["seq"] for g in got] == [e["seq"] for e in events(env)]
            assert got[0]["type"] == "user_message" and got[0]["text"] == "see attached"
        with c.websocket_connect(f"/ws?after={events(env)[-1]['seq'] - 1}") as ws:
            ws.receive_json()
            assert ws.receive_json()["seq"] == events(env)[-1]["seq"]


def test_watcher_reports_opened_and_sub_usage(env):
    with client(env) as c:
        c.post("/api/messages", data={"text": "x"})
        (env / ".harness/inbox/1/opened").touch()
        with open(env / ".harness/usage.jsonl", "a") as f:
            f.write(json.dumps({"ts": 0, "model": "m", "in": 10, "out": 5, "cached": 0, "latency": 0.1}) + "\n")
        assert wait(lambda: events(env, "message_opened"))
        assert events(env, "message_opened")[0]["id"] == 1
        assert wait(lambda: events(env, "usage"))
        u = events(env, "usage")[0]
        assert u["scope"] == "sub" and u["cost"] == pytest.approx(15 / 1e6)
        assert c.get("/api/metrics").json()["calls"]["sub"] == 1


def test_outbox_html_is_themed_on_serve(env):
    with client(env) as c:
        (env / ".harness/outbox").mkdir()
        (env / ".harness/outbox/3.html").write_text('<html><head></head><body>"theme": "light"</body></html>')
        dark = c.get("/api/outbox/3.html?theme=dark").text
        assert 'data-theme="dark"' in dark and '"theme": "dark"' in dark and "/*THEME*/" in dark
        assert '"theme": "light"' in c.get("/api/outbox/3.html?theme=light").text
        assert c.get("/api/outbox/9.html").status_code == 404


def test_stop_endpoint(env):
    with client(env) as c:
        assert c.post("/api/agent/stop").json() == {"ok": True}
