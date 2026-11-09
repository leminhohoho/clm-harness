"""Agent loop: idle → thinking (LLM call) → executing (run) → … → idle. Woken only by notifications."""

import asyncio
import time
from pathlib import Path

from .context import Msg, ParseError, parse, render, to_langchain, tokens
from .events import Events
from .executor import Executor
from .metrics import Metrics
from .notify import Notifier

STOPPED = "[stopped by user] the run continues as a background job; its completion will be notified"


def fmt_tok(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content).strip()


class Agent:
    def __init__(self, hdir: Path, system: str, model, model_name: str, executor: Executor, notifier: Notifier,
                 events: Events, metrics: Metrics, budget: int):
        self.ctx, self.system, self.model, self.model_name = Path(hdir) / "context.md", system, model, model_name
        self.executor, self.notifier, self.events, self.metrics, self.budget = executor, notifier, events, metrics, budget
        self.state, self.task, self.rendered = "idle", None, None
        self.messages: list[Msg] = []
        self.wake = asyncio.Event()
        if self.ctx.exists():
            text = self.ctx.read_text()
            try:
                self.messages, self.rendered = parse(text), text
            except ParseError as e:
                self.ctx.rename(self.ctx.with_suffix(".md.broken"))
                events.emit("error", text=f"context.md unreadable at startup ({e}); moved to context.md.broken")
        if notifier.has_pending():
            self.wake.set()

    def poke(self) -> None:
        self.wake.set()

    def stop(self) -> None:
        if self.task:
            self.task.cancel()

    def _set(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.events.emit("state", state=state)
            self.metrics.set(state=state)

    def save(self) -> None:
        self.rendered = render(self.messages)
        self.ctx.write_text(self.rendered)
        self.metrics.set(context=tokens(self.system + self.rendered))

    async def run_forever(self) -> None:
        while True:
            await self.wake.wait()
            self.wake.clear()
            if not self.notifier.has_pending():
                continue
            self.messages.append(Msg("user", "\n".join(self.notifier.deliver())))
            self.task = asyncio.create_task(self.work())
            try:
                await self.task
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
            except Exception as e:
                self.events.emit("error", text=f"{type(e).__name__}: {e}")
            finally:
                self.task = None
                self._set("idle")
                self.save()
            if self.notifier.has_pending():
                self.wake.set()

    async def work(self) -> None:
        while True:
            self.save()
            self._set("thinking")
            t = time.time()
            resp = await self.model.ainvoke(to_langchain(self.system, self.messages))
            u = resp.usage_metadata or {}
            self.metrics.usage("main", self.model_name, u.get("input_tokens", 0), u.get("output_tokens", 0),
                               (u.get("input_token_details") or {}).get("cache_read", 0) or 0, time.time() - t)
            text = _text(resp.content)
            if text:
                self.events.emit("llm_reasoning", text=text)
            if not resp.tool_calls:
                self.messages.append(Msg("assistant", text))
                return
            call = Msg("assistant", text, resp.tool_calls[0]["args"].get("code", ""))
            self.events.emit("code", code=call.code)
            self._set("executing")
            try:
                out = (await self.executor.run(call.code)).rstrip()
            except asyncio.CancelledError:
                self.messages += [call, Msg("tool", STOPPED)]
                raise
            self.events.emit("run_result", output=out)
            receipt = self._sync_edit()
            self.messages.append(call)
            n = tokens(self.system + render(self.messages) + out)
            body = f"{out}\n\n[context] {fmt_tok(n)} / {fmt_tok(self.budget)} tokens ({100 * n // self.budget}%)"
            body += "".join(f"\n{s}" for s in self.notifier.deliver())
            if receipt:
                body += f"\n{receipt}"
            self.messages.append(Msg("tool", body))

    def _sync_edit(self) -> str:
        """If the agent edited context.md during the run, adopt it (or reject it if unparseable)."""
        text = self.ctx.read_text() if self.ctx.exists() else self.rendered
        if text == self.rendered:
            return ""
        before, n_before = tokens(self.rendered or ""), len(self.messages)
        try:
            new = parse(text)
        except ParseError as e:
            self.events.emit("context_edit", ok=False, receipt=f"rejected: {e}")
            return f"[context edit rejected: {e}; previous context kept]"
        self.messages, after = new, tokens(render(new))
        receipt = f"[context edited] {n_before} → {len(new)} turns · {fmt_tok(before)} → {fmt_tok(after)} tokens"
        self.metrics.bump(edits=1, freed=max(0, before - after))
        self.events.emit("context_edit", ok=True, receipt=receipt, before=before, after=after)
        return receipt
