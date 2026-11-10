import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

import harness as h


@pytest.fixture
def hdir(tmp_path, monkeypatch):
    d = tmp_path / ".harness"
    d.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HARNESS_DIR", str(d))
    return d


def wait_for(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def make_msg(hdir, i, text, files=()):
    d = hdir / "inbox" / str(i)
    (d / "files").mkdir(parents=True)
    for name in files:
        (d / "files" / name).write_text("x")
    (d / "message.json").write_text(json.dumps({"id": i, "text": text, "ts": 1.0, "attachments": list(files)}))


def test_inbox_access_opens_message(hdir):
    make_msg(hdir, 1, "hello", ["a.csv"])
    make_msg(hdir, 2, "world")
    assert h.inbox.unopened() == [1, 2]
    m = h.inbox[1]
    assert (m.text, m.ts) == ("hello", 1.0)
    assert m.attachments == [hdir / "inbox/1/files/a.csv"]
    assert (hdir / "inbox/1/opened").exists()
    assert h.inbox.unopened() == [2]


def test_inbox_missing_message_raises(hdir):
    with pytest.raises(KeyError):
        h.inbox[9]


def test_send_appends_with_unique_ids(hdir):
    ids = []
    threads = [threading.Thread(target=lambda: ids.append(h.send("hi"))) for _ in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(ids) == list(range(1, 21))
    rows = [json.loads(l) for l in (hdir / "outbox.jsonl").read_text().splitlines()]
    assert rows[0]["text"] == "hi" and rows[0]["files"] == []


def test_send_copies_files_at_send_time(hdir, tmp_path):
    (tmp_path / "plot.png").write_bytes(b"png")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub/t.csv").write_text("1,2")
    sid = h.send("see", files=["plot.png", tmp_path / "sub/t.csv"])
    (tmp_path / "plot.png").write_bytes(b"changed")
    assert (hdir / f"outbox/{sid}/plot.png").read_bytes() == b"png"
    assert (hdir / f"outbox/{sid}/t.csv").read_text() == "1,2"
    row = json.loads((hdir / "outbox.jsonl").read_text())
    assert row["id"] == sid and row["text"] == "see"
    assert row["files"] == [{"name": "plot.png", "size": 3}, {"name": "t.csv", "size": 3}]


@pytest.mark.parametrize("files, err", [(["missing.png"], FileNotFoundError), (["sub"], IsADirectoryError),
                                        (["a.txt", "sub/a.txt"], ValueError)])
def test_send_rejects_bad_files_before_writing(hdir, tmp_path, files, err):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub/a.txt").write_text("y")
    with pytest.raises(err):
        h.send("x", files=files)
    assert not (hdir / "outbox.jsonl").exists() or not (hdir / "outbox.jsonl").read_text()
    assert not (hdir / "outbox").exists()


def test_spawn_runs_in_cwd_and_records_exit(hdir, tmp_path):
    jid = h.spawn("pwd; echo err >&2; exit 3")
    jd = hdir / "jobs" / str(jid)
    assert wait_for(lambda: (jd / "exit").exists())
    assert (jd / "exit").read_text().strip() == "3"
    out = (jd / "out.log").read_text()
    assert str(tmp_path.resolve()) in out and "err" in out
    meta = json.loads((jd / "meta.json").read_text())
    assert meta["cmd"].startswith("pwd") and meta["kind"] == "spawn" and meta["status"] == "running" and meta["pid"] > 0
    assert h.spawn("true") == jid + 1


def test_kill_stops_job_group(hdir):
    jid = h.spawn("sleep 30 & sleep 30")
    pid = h.jobs[-1]["pid"]
    h.kill(jid)
    assert wait_for(lambda: not _alive(pid))
    assert h.jobs[-1]["status"] == "killed"


def _alive(pid):
    import os
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # zombie counts as dead
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except FileNotFoundError:
        import subprocess
        st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        return bool(st) and not st.startswith("Z")


class FakeLLM(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.path == "/v1/chat/completions"
        assert self.headers["Authorization"] == "Bearer k"
        assert not self.headers["User-Agent"].startswith("Python-urllib")  # blocked by Cloudflare (1010)
        prompt = body["messages"][0]["content"]
        out = {
            "choices": [{"message": {"role": "assistant", "content": f"{body['model']}:{prompt.upper()}"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "prompt_tokens_details": {"cached_tokens": 4}},
        }
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def llm_env(hdir, monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), FakeLLM)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("OPENAI_BASE_URL", f"http://127.0.0.1:{srv.server_port}/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("SUB_MODEL", "sub")
    yield
    srv.shutdown()


def test_llm_single_and_list_log_usage(hdir, llm_env):
    assert h.llm("hi") == "sub:HI"
    assert h.llm(["a", "b", "c"]) == ["sub:A", "sub:B", "sub:C"]
    rows = [json.loads(l) for l in (hdir / "usage.jsonl").read_text().splitlines()]
    assert len(rows) == 4
    assert rows[0]["model"] == "sub" and (rows[0]["in"], rows[0]["out"], rows[0]["cached"]) == (10, 2, 4)
    assert rows[0]["latency"] >= 0
