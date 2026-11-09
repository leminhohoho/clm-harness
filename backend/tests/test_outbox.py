import json

from backend.events import State, tail
from backend.outbox import Outbox, themed
from conftest import until

NB = '''import marimo
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    mo.md("# hi")
    return

if __name__ == "__main__":
    app.run()
'''


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
    return Outbox(parts.agent, parts.state, parts.events, parts.notifier)


async def test_markdown_send_emits_agent_message_once(parts):
    import harness
    harness.send("**hi**")
    ob(parts).poll()
    ob(parts).poll()
    msgs = parts.of("agent_message")
    assert len(msgs) == 1 and msgs[0]["id"] == 1 and msgs[0]["backend"] == "markdown" and msgs[0]["text"] == "**hi**"


async def test_marimo_send_exports_html(parts):
    import harness
    (parts.agent / "nb.py").write_text(NB)
    harness.send("nb.py", backend="marimo")
    o = ob(parts)
    o.poll()
    assert await until(lambda: parts.of("agent_message"), timeout=180)
    m = parts.of("agent_message")[0]
    assert m["backend"] == "marimo" and m["url"] == "/api/outbox/1.html" and m["path"] == "nb.py"
    assert "<marimo-code" in (parts.hdir / "outbox/1.html").read_text()


async def test_marimo_export_failure_notifies_agent(parts):
    import harness
    harness.send("missing.py", backend="marimo")
    o = ob(parts)
    o.poll()
    assert await until(parts.notifier.has_pending, timeout=180)
    stub = parts.notifier.deliver()[0]
    assert stub.startswith("[notify] send #1 failed · marimo export: ") and len(stub) > 45
    assert parts.of("error")[0]["send"] == 1
    assert not parts.of("agent_message")


def test_themed_sets_mode_and_injects_css():
    html = '<html lang="en"><head><title>x</title></head><body><marimo-user-config data-config=\'{"display": {"theme": "light"}}\'></body></html>'
    out = themed(html, "/*CSS*/", "dark")
    assert '<html data-theme="dark" lang="en">' in out
    assert '"theme": "dark"' in out and '"theme": "light"' not in out
    assert "<style>/*CSS*/</style></head>" in out
    assert '"theme": "light"' in themed(html, "", "light")
