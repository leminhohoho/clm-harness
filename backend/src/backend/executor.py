"""Runs agent code as jobs: foreground wait up to a limit, then detach; job completion → notifications."""

import asyncio
import json
import os
import signal
import time
from pathlib import Path

import harness
from harness import start_job, write_json

from .events import Events
from .metrics import Metrics
from .notify import Notifier, fmt_dur, job_stub, lost_stub

LIB_PATH = str(Path(harness.__file__).resolve().parent.parent)
HEAD = TAIL = 5000


def truncate(s: str) -> str:
    if len(s) <= HEAD + TAIL:
        return s
    return f"{s[:HEAD]}\n… [{len(s) - HEAD - TAIL} chars omitted] …\n{s[-TAIL:]}"


def _alive(pid: int, jd: Path) -> bool:
    cmdline = Path(f"/proc/{pid}/cmdline")
    if Path("/proc/self").exists():  # linux: guard against pid reuse after container restart
        return cmdline.exists() and str(jd).encode() in cmdline.read_bytes()
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def agent_env(hdir: Path) -> dict:
    """Environment for processes run in the agent folder: harness lib injected, backend venv hidden."""
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    return env | {"HARNESS_DIR": str(hdir), "PYTHONPATH": LIB_PATH}


class Executor:
    def __init__(self, agent_dir: Path, notifier: Notifier, events: Events, metrics: Metrics, timeout: float = 60):
        self.agent, self.hdir = Path(agent_dir).resolve(), Path(agent_dir).resolve() / ".harness"
        self.jobs, self.runs = self.hdir / "jobs", self.hdir / "runs"
        self.notifier, self.events, self.metrics, self.timeout = notifier, events, metrics, timeout
        self.known: set[int] = set()
        self.running: set[int] = set()
        self.fg: set[int] = set()  # runs the executor is waiting on in the foreground
        self.env = agent_env(self.hdir)

    # --- job files ---------------------------------------------------------
    def _meta(self, jid: int) -> dict:
        return json.loads((self.jobs / str(jid) / "meta.json").read_text())

    def _update(self, jid: int, **kv) -> dict:
        m = {**self._meta(jid), **kv}
        write_json(self.jobs / str(jid) / "meta.json", m)
        return m

    def _output(self, jid: int) -> str:
        p = self.jobs / str(jid) / "out.log"
        return truncate(p.read_text(errors="replace")) if p.exists() else ""

    def _exit(self, jid: int) -> tuple[int, float] | None:
        p = self.jobs / str(jid) / "exit"
        return (int(p.read_text().strip()), p.stat().st_mtime) if p.exists() else None

    def _sync_count(self) -> None:
        self.metrics.set(jobs_running=len(self.running - self.fg))

    # --- foreground runs ---------------------------------------------------
    async def run(self, code: str) -> str:
        self.runs.mkdir(parents=True, exist_ok=True)
        n = len(list(self.runs.glob("*.py"))) + 1
        (self.runs / f"{n}.py").write_text(code)
        jid, _ = start_job(f"uv run -q python .harness/runs/{n}.py", kind="run", cwd=self.agent, env=self.env,
                           hdir=self.hdir, run=n)
        self.known.add(jid)
        self.running.add(jid)
        self.fg.add(jid)
        self.metrics.bump(runs=1)
        t0 = time.time()
        try:
            while (ex := self._exit(jid)) is None and time.time() - t0 < self.timeout:
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            self._detach(jid)
            raise
        if ex is None:
            self._detach(jid)
            return f"detached as job #{jid} after {self.timeout:g}s · completion will be notified\n{self._output(jid)}"
        self.fg.discard(jid)
        self.running.discard(jid)
        self._update(jid, status="finished", exit=ex[0], end=ex[1])
        return f"exit {ex[0]} · {time.time() - t0:.1f}s\n{self._output(jid)}"

    def _detach(self, jid: int) -> None:
        self.fg.discard(jid)
        self._update(jid, detached=True)
        self.metrics.bump(detached=1)
        self.events.emit("job_update", id=jid, status="detached")
        self._sync_count()

    # --- background jobs ---------------------------------------------------
    def startup(self) -> None:
        """Recover after a backend restart: adopt live jobs, mark dead ones lost."""
        if not self.jobs.exists():
            return
        for d in self.jobs.iterdir():
            if not (d / "meta.json").exists():
                continue
            jid = int(d.name)
            self.known.add(jid)
            m = self._meta(jid)
            if m["status"] != "running":
                continue
            if self._exit(jid) or _alive(m["pid"], d.resolve()):
                self.running.add(jid)
                if m["kind"] == "run" and not m.get("detached"):
                    self._update(jid, detached=True)
            else:
                self._update(jid, status="lost", end=time.time())
                self.events.emit("job_update", id=jid, status="lost")
                self.notifier.add(lost_stub(jid))
        self._sync_count()

    def poll(self) -> None:
        if not self.jobs.exists():
            return
        changed = False
        for d in self.jobs.iterdir():
            if not d.name.isdigit() or int(d.name) in self.known or not (d / "meta.json").exists():
                continue
            jid = int(d.name)
            self.known.add(jid)
            m = self._meta(jid)
            if m["status"] == "running":
                self.running.add(jid)
                self.events.emit("job_update", id=jid, status="running", cmd=m["cmd"])
                changed = True
        for jid in sorted(self.running - self.fg):
            m = self._meta(jid)
            if m["status"] == "killed":
                self.running.discard(jid)
                self.events.emit("job_update", id=jid, status="killed")
                changed = True
            elif ex := self._exit(jid):
                self.running.discard(jid)
                m = self._update(jid, status="finished", exit=ex[0], end=ex[1])
                self.events.emit("job_update", id=jid, status="finished", exit=ex[0])
                self.notifier.add(job_stub(jid, ex[0], ex[1] - m["start"]))
                changed = True
        if changed:
            self._sync_count()

    def kill_all(self) -> None:
        """Kill every running job (profile deletion); same as harness.kill without the global HARNESS_DIR."""
        for jid in sorted(self.running):
            m = self._update(jid, status="killed", end=time.time())
            try:
                os.killpg(m["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
        self.running.clear()
