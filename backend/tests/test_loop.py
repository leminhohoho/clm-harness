import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from backend.context import Msg, parse
from backend.executor import Executor
from backend.loop import Agent
from conftest import until


def say(text=""):
    return AIMessage(text, usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110,
                                           "input_token_details": {"cache_read": 40}})


def call(code, text=""):
    m = say(text)
    m.tool_calls = [{"name": "python", "args": {"code": code}, "id": "x"}]
    return m


class FakeModel:
    def __init__(self, *script):
        self.script, self.inputs = list(script), []

    async def ainvoke(self, messages):
        self.inputs.append(messages)
        r = self.script.pop(0)
        if callable(r):
            r = await r()
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def mk(parts):
    agents = []

    def make(*script, timeout=30.0):
        ex = Executor(parts.agent, parts.notifier, parts.events, parts.metrics, timeout=timeout)
        a = Agent(parts.hdir, "SYSTEM", FakeModel(*script), "m", ex, parts.notifier, parts.events, parts.metrics, 1000)
        parts.notifier.on_new = a.poke
        a.runner = asyncio.create_task(a.run_forever())
        agents.append(a)
        return a

    yield make
    for a in agents:
        a.runner.cancel()


async def idle_after(a, n_calls):
    assert await until(lambda: len(a.model.inputs) >= n_calls and a.state == "idle" and not a.notifier.has_pending())


async def test_idle_until_notified_then_stops_without_tool_call(parts, mk):
    a = mk(say("noted"))
    await asyncio.sleep(0.2)
    assert a.model.inputs == []
    parts.notifier.add("[notify] msg #1")
    await idle_after(a, 1)
    sent = a.model.inputs[0]
    assert sent[0].content == "SYSTEM" and isinstance(sent[1], HumanMessage) and sent[1].content == "[notify] msg #1"
    assert a.messages == [Msg("user", "[notify] msg #1"), Msg("assistant", "noted")]
    assert parse((parts.hdir / "context.md").read_text()) == a.messages
    assert [e["state"] for e in parts.of("state")] == ["thinking", "idle"]
    assert parts.of("llm_reasoning")[0]["text"] == "noted"
    assert parts.metrics.snapshot()["calls"]["main"] == 1 and parts.metrics.snapshot()["tokens"]["cached"] == 40


async def test_tool_call_runs_code_and_returns_result_with_readout(parts, mk):
    a = mk(call("print(6*7)", "computing"), say("done"))
    parts.notifier.add("[notify] go")
    await idle_after(a, 2)
    tool = a.model.inputs[1][-1]
    assert isinstance(tool, ToolMessage) and tool.content.startswith("exit 0")
    assert "42" in tool.content and "[context] " in tool.content and "/ 1.0k tokens" in tool.content
    assert [m.role for m in a.messages] == ["user", "assistant", "tool", "assistant"]
    assert a.messages[1].code == "print(6*7)"
    assert parts.of("code")[0]["code"] == "print(6*7)" and "42" in parts.of("run_result")[0]["output"]
    assert [e["state"] for e in parts.of("state")] == ["thinking", "executing", "thinking", "idle"]


async def test_notification_during_run_is_delivered_in_tool_result(parts, mk):
    a = mk(call("import time; time.sleep(1)"), say("ok"))
    parts.notifier.add("[notify] first")
    assert await until(lambda: a.state == "executing")
    parts.notifier.add("[notify] second")
    await idle_after(a, 2)
    assert "[notify] second" in a.messages[2].content
    assert [m.role for m in a.messages].count("user") == 1


async def test_notification_during_final_call_wakes_agent_again(parts, mk):
    async def slow():
        parts.notifier.add("[notify] late")
        return say("first done")
    a = mk(slow, say("second done"))
    parts.notifier.add("[notify] early")
    await idle_after(a, 2)
    assert [m.content for m in a.messages if m.role == "user"] == ["[notify] early", "[notify] late"]


async def test_context_edit_replaces_messages_with_receipt(parts, mk):
    edit = ("from pathlib import Path\n"
            "Path('.harness/context.md').write_text('[[CTX_TURN 0 role=user]]\\nsummary: user wants X\\n')")
    a = mk(call("print('x' * 4000)"), call(edit), say("fin"))
    parts.notifier.add("[notify] go")
    await idle_after(a, 3)
    assert a.messages[0] == Msg("user", "summary: user wants X")
    assert [m.role for m in a.messages] == ["user", "assistant", "tool", "assistant"]
    assert "[context edited] 3 → 1 turns" in a.messages[2].content
    ce = parts.of("context_edit")[0]
    assert ce["ok"] and ce["before"] > ce["after"] and ce["n"] == 1
    snaps = parts.hdir / "context-edits"
    assert (snaps / "1.after.md").read_text() == "[[CTX_TURN 0 role=user]]\nsummary: user wants X\n"  # raw, not re-rendered
    before = (snaps / "1.before.md").read_text()
    assert before.startswith("[[CTX_TURN 0 role=user]]\n[notify] go") and "x" * 4000 in before
    assert parts.metrics.snapshot()["context"]["edits"] == 1 and parts.metrics.snapshot()["context"]["freed"] > 900


async def test_broken_context_edit_is_discarded(parts, mk):
    (parts.hdir / "context-edits").mkdir(parents=True)
    (parts.hdir / "context-edits/1.after.md").write_text("from a previous process")
    a = mk(call("open('.harness/context.md', 'w').write('garbage')"), say("fin"))
    parts.notifier.add("[notify] go")
    await idle_after(a, 2)
    assert a.messages[0] == Msg("user", "[notify] go")
    assert "[context edit rejected:" in a.messages[2].content
    assert parse((parts.hdir / "context.md").read_text()) == a.messages
    ce = parts.of("context_edit")[0]
    assert not ce["ok"] and ce["n"] == 2  # numbering survives restarts, counts rejected edits too
    assert (parts.hdir / "context-edits/2.after.md").read_text() == "garbage"
    assert parse((parts.hdir / "context-edits/2.before.md").read_text()) == [Msg("user", "[notify] go")]


async def test_stop_cancels_run_and_goes_idle(parts, mk):
    a = mk(call("import time; time.sleep(2)"))
    parts.notifier.add("[notify] go")
    assert await until(lambda: a.state == "executing")
    a.stop()
    assert await until(lambda: a.state == "idle")
    assert [m.role for m in a.messages] == ["user", "assistant", "tool"]
    assert a.messages[2].content.startswith("[stopped by user]")
    assert await until(parts.notifier.has_pending, tick=a.executor.poll)  # run continued as a job


async def test_model_error_emits_error_and_goes_idle(parts, mk):
    a = mk(RuntimeError("context length exceeded"), say("recovered"))
    parts.notifier.add("[notify] go")
    assert await until(lambda: parts.of("error"))
    assert "context length exceeded" in parts.of("error")[0]["text"]
    assert await until(lambda: a.state == "idle")
    parts.notifier.add("[notify] again")
    await idle_after(a, 2)
    assert a.messages[-1] == Msg("assistant", "recovered")


async def test_restart_restores_context_and_delivers_pending(parts, mk):
    (parts.hdir / "context.md").write_text("[[CTX_TURN 0 role=user]]\nold\n\n[[CTX_TURN 1 role=assistant]]\nok\n")
    parts.notifier.add("[notify] while down")
    a = mk(say("back"))
    await idle_after(a, 1)
    assert [m.content for m in a.messages] == ["old", "ok", "[notify] while down", "back"]


async def test_invalid_tool_call_is_reported_back_not_silently_idled(parts, mk):
    bad = say("let me run")
    bad.invalid_tool_calls = [{"type": "invalid_tool_call", "name": "python", "args": '{"code": "print(1)',
                               "id": "x", "error": "Unterminated string"}]
    a = mk(bad, say("ok"))
    parts.notifier.add("[notify] go")
    await idle_after(a, 2)
    tool = a.model.inputs[1][-1]
    assert isinstance(tool, ToolMessage) and tool.content.startswith("[invalid tool call]")
    assert "Unterminated string" in tool.content
    assert parts.of("error") and not (parts.hdir / "runs").exists()
    assert a.messages[-1] == Msg("assistant", "ok")
