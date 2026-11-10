import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import harness
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
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFAULT_MODEL", "m")
    monkeypatch.setenv("PRICES", '{"m": {"in": 1, "out": 1, "cached": 1}}')
    monkeypatch.setenv("CONTEXT_BUDGET", "5000")
    return tmp_path / "data"


def client(data, *script):
    return TestClient(create_app(data, lambda model_id: FakeModel(*script)))


def events(agent, type=None):
    p = agent / ".harness/events.jsonl"
    rows = [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []
    return [r for r in rows if type is None or r["type"] == type]


def test_message_flow_and_ws_backlog(data):
    with client(data, say("hello there")) as c:
        assert c.post("/api/profiles", json={"name": "a"}).json() == {"name": "a", "model": "m", "state": "idle"}
        a = data / "a"
        assert (a / "pyproject.toml").exists()  # agent folder initialised as uv project
        r = c.post("/api/p/a/messages", data={"text": "see attached"}, files=[("files", ("a.csv", b"1,2"))])
        assert r.status_code == 200 and r.json()["id"] == 1
        msg = json.loads((a / ".harness/inbox/1/message.json").read_text())
        assert msg["text"] == "see attached" and msg["attachments"] == ["a.csv"]
        assert wait(lambda: events(a, "llm_reasoning"))
        assert wait(lambda: events(a, "state") and events(a, "state")[-1]["state"] == "idle")
        assert events(a, "notification")[0]["text"] == "[notify] msg #1 from user · 12 chars · 1 attachment"
        assert c.get("/api/p/a/raw/.harness/inbox/1/files/a.csv").content == b"1,2"
        assert c.get("/api/p/a/metrics").json()["calls"]["main"] == 1
        with c.websocket_connect("/ws/a?after=0") as ws:
            assert ws.receive_json()["type"] == "metrics"
            got = [ws.receive_json() for _ in range(len(events(a)))]
            assert [g["seq"] for g in got] == [e["seq"] for e in events(a)]
            assert got[0]["type"] == "user_message" and got[0]["text"] == "see attached"
        with c.websocket_connect(f"/ws/a?after={events(a)[-1]['seq'] - 1}") as ws:
            ws.receive_json()
            assert ws.receive_json()["seq"] == events(a)[-1]["seq"]


def test_profiles_crud_and_validation(data):
    (data / "stray").mkdir(parents=True)  # folders without profile.json are not profiles
    (data / "old").mkdir()
    (data / "old/profile.json").write_text('{"model": "x/old", "created": 1}')
    with client(data) as c:
        assert c.get("/api/profiles").json() == [{"name": "old", "model": "x/old", "state": "idle"}]
        for bad in ["Bad", "-x", "a" * 33, "a/b", "", ".."]:
            assert c.post("/api/profiles", json={"name": bad}).status_code == 422, bad
        assert c.post("/api/profiles", json={"name": "b_2", "model": "x/y"}).status_code == 200
        assert c.post("/api/profiles", json={"name": "b_2"}).status_code == 409
        assert c.post("/api/profiles", json={"name": "stray"}).status_code == 409
        assert [p["name"] for p in c.get("/api/profiles").json()] == ["b_2", "old"]
        assert json.loads((data / "b_2/profile.json").read_text())["model"] == "x/y"
        assert c.delete("/api/profiles/old").status_code == 200
        assert not (data / "old").exists()
        assert [p["name"] for p in c.get("/api/profiles").json()] == ["b_2"]
        assert c.get("/api/p/old/metrics").status_code == 404
        assert c.delete("/api/profiles/old").status_code == 404


def test_profiles_are_isolated(data):
    with client(data) as c:
        c.post("/api/profiles", json={"name": "a"})
        c.post("/api/profiles", json={"name": "b"})
        c.post("/api/p/a/messages", data={"text": "only a"})
        assert wait(lambda: events(data / "a", "user_message"))
        assert not events(data / "b", "user_message")
        assert c.get("/api/p/b/metrics").json()["calls"]["main"] == 0


def test_delete_kills_profile_jobs(data):
    with client(data) as c:
        c.post("/api/profiles", json={"name": "a"})
        jid, _ = harness.start_job("sleep 30", hdir=data / "a/.harness")
        meta = data / f"a/.harness/jobs/{jid}/meta.json"
        pid = json.loads(meta.read_text())["pid"]
        assert wait(lambda: events(data / "a", "job_update"))  # watcher adopted it
        assert c.delete("/api/profiles/a").status_code == 200

        def gone():
            try:
                os.killpg(pid, 0)
            except ProcessLookupError:
                return True
            return False
        assert wait(gone) and not (data / "a").exists()


def test_raw_and_tree_routes(data, tmp_path):
    with client(data) as c:
        c.post("/api/profiles", json={"name": "a"})
        a = data / "a"
        (a / "sub").mkdir()
        (a / "sub/x.html").write_text("<script>alert(1)</script>")
        (a / "B.txt").write_text("hi")
        (a / ".hidden").write_text("h")
        (tmp_path / "secret").write_text("s")
        (a / "link").symlink_to(tmp_path / "secret")
        (a / "outdir").symlink_to(tmp_path)
        tree = c.get("/api/p/a/tree").json()
        names = [e["name"] for e in tree]
        dirs = [e["name"] for e in tree if e["dir"]]
        assert names[:len(dirs)] == dirs and {".harness", "sub"} <= set(dirs) and ".hidden" in names
        assert names.index("B.txt") < names.index("link")  # case-insensitive order
        b = next(e for e in tree if e["name"] == "B.txt")
        assert b["size"] == 2 and b["mtime"] > 0 and b["dir"] is False
        assert [e["name"] for e in c.get("/api/p/a/tree?path=sub").json()] == ["x.html"]
        r = c.get("/api/p/a/raw/sub/x.html")
        assert r.text == "<script>alert(1)</script>"
        assert r.headers["content-security-policy"] == "sandbox" and r.headers["x-content-type-options"] == "nosniff"
        assert "content-disposition" not in r.headers
        assert c.get("/api/p/a/raw/B.txt?download=1").headers["content-disposition"].startswith("attachment")
        for bad in ["raw/..%2F..%2Fsecret", "raw/link", "raw/outdir/secret", "raw/sub", "raw/nope",
                    "tree?path=..", "tree?path=outdir", "tree?path=B.txt"]:
            assert c.get(f"/api/p/a/{bad}").status_code == 404, bad


def test_ws_unknown_profile_closes_4404(data):
    with client(data) as c, c.websocket_connect("/ws/nope") as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 4404


class Gateway(BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.path == "/v1/models" and self.headers["User-Agent"].startswith("clm-harness")
        body = json.dumps({"data": [{"id": i} for i in ["deepseek/a", "claude-x", "gpt-5", "z/b"]]}).encode()
        self.send_response(200 if ok else 403)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_models_route_filters_and_caches(data, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")  # nothing listens: gateway down
    with client(data) as c:
        assert c.get("/api/models").json() == []
        srv = HTTPServer(("127.0.0.1", 0), Gateway)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        monkeypatch.setenv("OPENAI_BASE_URL", f"http://127.0.0.1:{srv.server_port}/v1")
        assert c.get("/api/models").json() == ["deepseek/a", "z/b"]
        srv.shutdown()
        srv.server_close()
        assert c.get("/api/models").json() == ["deepseek/a", "z/b"]  # cached after first success


def test_watcher_reports_opened_and_sub_usage(data):
    with client(data) as c:
        c.post("/api/profiles", json={"name": "a"})
        a = data / "a"
        c.post("/api/p/a/messages", data={"text": "x"})
        (a / ".harness/inbox/1/opened").touch()
        with open(a / ".harness/usage.jsonl", "a") as f:
            f.write(json.dumps({"ts": 0, "model": "m", "in": 10, "out": 5, "cached": 0, "latency": 0.1}) + "\n")
        assert wait(lambda: events(a, "message_opened"))
        assert events(a, "message_opened")[0]["id"] == 1
        assert wait(lambda: events(a, "usage"))
        u = events(a, "usage")[0]
        assert u["scope"] == "sub" and u["cost"] == pytest.approx(15 / 1e6)
        assert c.get("/api/p/a/metrics").json()["calls"]["sub"] == 1


def test_context_edit_diff_and_stop_routes(data):
    with client(data) as c:
        c.post("/api/profiles", json={"name": "a"})
        d = data / "a/.harness/context-edits"
        d.mkdir()
        (d / "2.before.md").write_text("a\nb\n")
        (d / "2.after.md").write_text("a\nc\n")
        assert c.get("/api/p/a/context-edits/2").json() == {
            "n": 2, "lines": [{"op": " ", "text": "a"}, {"op": "-", "text": "b"}, {"op": "+", "text": "c"}]}
        assert c.get("/api/p/a/context-edits/3").status_code == 404
        assert c.post("/api/p/a/agent/stop").json() == {"ok": True}
