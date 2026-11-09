import asyncio
import json

import harness

from backend.executor import Executor, truncate
from conftest import until


def ex(parts, timeout=30.0):
    return Executor(parts.agent, parts.notifier, parts.events, parts.metrics, timeout=timeout)


def meta(parts, jid):
    return json.loads((parts.hdir / "jobs" / str(jid) / "meta.json").read_text())


async def test_run_finishes_in_foreground_with_harness_injected(parts):
    (parts.hdir / "inbox/1").mkdir(parents=True)
    (parts.hdir / "inbox/1/message.json").write_text('{"id":1,"text":"t","ts":0,"attachments":[]}')
    e = ex(parts)
    out = await e.run("import harness as h, os\nprint(h.inbox.unopened(), os.getcwd())\nraise SystemExit(2)")
    assert out.startswith("exit 2 · ")
    assert f"[1] {parts.agent.resolve()}" in out
    assert (parts.hdir / "runs/1.py").read_text().startswith("import harness")
    assert meta(parts, 1)["status"] == "finished" and meta(parts, 1)["exit"] == 2
    assert parts.metrics.snapshot()["runs"]["total"] == 1
    assert not parts.notifier.has_pending()
    assert (await e.run("print('second')")).rstrip().endswith("\nsecond")
    assert (parts.hdir / "runs/2.py").exists()


def test_truncate_keeps_head_and_tail():
    s = "a" * 6000 + "b" * 6000
    t = truncate(s)
    assert t.startswith("a" * 5000) and t.endswith("b" * 5000) and "2000 chars omitted" in t
    assert truncate("short") == "short"


async def test_long_run_detaches_then_notifies(parts):
    e = ex(parts, timeout=1.5)
    out = await e.run("import time\nprint('started', flush=True)\ntime.sleep(3)\nprint('done')")
    assert out.startswith("detached as job #1 after 1.5s") and "started" in out
    assert meta(parts, 1)["detached"] is True
    assert parts.metrics.snapshot()["runs"]["detached"] == 1
    assert parts.metrics.snapshot()["runs"]["jobs_running"] == 1
    assert await until(parts.notifier.has_pending, tick=e.poll)
    assert parts.notifier.deliver()[0].startswith("[notify] job #1 finished · exit 0 · ")
    assert meta(parts, 1)["status"] == "finished"
    assert parts.metrics.snapshot()["runs"]["jobs_running"] == 0


async def test_cancelled_wait_detaches(parts):
    e = ex(parts)
    t = asyncio.create_task(e.run("import time; time.sleep(1.5)"))
    assert await until(lambda: (parts.hdir / "jobs/1/meta.json").exists())
    t.cancel()
    await asyncio.gather(t, return_exceptions=True)
    assert meta(parts, 1)["detached"] is True
    assert await until(parts.notifier.has_pending, tick=e.poll)


async def test_spawned_job_is_discovered_and_notified(parts):
    e = ex(parts)
    jid = harness.spawn("exit 4")
    assert await until(parts.notifier.has_pending, tick=e.poll)
    assert parts.notifier.deliver() == [f"[notify] job #{jid} finished · exit 4 · 0s · log .harness/jobs/{jid}/out.log"]
    assert [u["status"] for u in parts.of("job_update")] == ["running", "finished"]


async def test_killed_job_is_not_notified(parts):
    e = ex(parts)
    jid = harness.spawn("sleep 30")
    e.poll()
    harness.kill(jid)
    assert await until(lambda: any(u["status"] == "killed" for u in parts.of("job_update")), tick=e.poll)
    assert not parts.notifier.has_pending()


async def test_startup_marks_dead_jobs_lost(parts):
    jd = parts.hdir / "jobs/3"
    jd.mkdir(parents=True)
    (jd / "meta.json").write_text(json.dumps({"id": 3, "cmd": "x", "kind": "spawn", "pid": 999999, "status": "running"}))
    e = ex(parts)
    e.startup()
    assert meta(parts, 3)["status"] == "lost"
    assert parts.notifier.deliver() == ["[notify] job #3 lost (backend restarted)"]


async def test_startup_adopts_live_foreground_run_as_detached(parts):
    e = ex(parts, timeout=0.5)
    await e.run("import time; time.sleep(1.5)")
    m = meta(parts, 1)
    (parts.hdir / "jobs/1/meta.json").write_text(json.dumps({**m, "detached": False}))
    e2 = ex(parts)
    e2.startup()
    assert meta(parts, 1)["detached"] is True
    assert await until(parts.notifier.has_pending, tick=e2.poll)
