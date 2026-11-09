import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from backend.context import Msg, ParseError, parse, render, to_langchain

TRICKY = "line\n[[CTX_TURN 9 role=user]]\n\\[[CODE]]\n[[/CODE]]\nend"


def sample():
    return [
        Msg("user", "[notify] msg #1 from user"),
        Msg("assistant", "Let me look.", "print('[[CODE]]')\n[[/CODE]]\nx = 1"),
        Msg("tool", TRICKY),
        Msg("assistant", "", "pass"),
        Msg("tool", ""),
        Msg("assistant", "Done."),
    ]


def test_round_trip_is_identity():
    msgs = sample()
    assert parse(render(msgs)) == msgs
    assert render(parse(render(msgs))) == render(msgs)


def test_content_is_newline_trimmed():
    assert Msg("tool", "\nout\n\n") == Msg("tool", "out")


def test_parse_renumbers_and_ignores_indices():
    text = "[[CTX_TURN 7 role=user]]\nhi\n\n[[CTX_TURN 3 role=assistant]]\nok\n"
    assert parse(text) == [Msg("user", "hi"), Msg("assistant", "ok")]
    assert render(parse(text)).startswith("[[CTX_TURN 0 role=user]]")


def test_orphan_tool_is_dropped():
    msgs = [Msg("user", "a"), Msg("tool", "orphan"), Msg("assistant", "b")]
    assert parse(render(msgs)) == [Msg("user", "a"), Msg("assistant", "b")]


def test_orphan_tool_call_is_folded_into_text():
    msgs = [Msg("user", "a"), Msg("assistant", "try", "print(1)"), Msg("user", "b"), Msg("assistant", "x", "y")]
    out = parse(render(msgs))
    assert [m.role for m in out] == ["user", "assistant", "user", "assistant"]
    assert out[1].code is None and "print(1)" in out[1].content and "try" in out[1].content
    assert out[3].code is None and "y" in out[3].content


def test_empty_file_is_empty_context():
    assert parse("") == [] and parse("\n  \n") == []


@pytest.mark.parametrize("text", ["just text", "junk\n[[CTX_TURN 0 role=user]]\nhi", "[[CTX_TURN 0 role=system]]\nx"])
def test_invalid_text_raises(text):
    with pytest.raises(ParseError):
        parse(text)


def test_to_langchain_pairs_tool_calls():
    out = to_langchain("SYS", sample())
    assert isinstance(out[0], SystemMessage) and out[0].content == "SYS"
    assert isinstance(out[1], HumanMessage)
    ai, tool = out[2], out[3]
    assert isinstance(ai, AIMessage) and ai.tool_calls[0]["args"] == {"code": sample()[1].code}
    assert isinstance(tool, ToolMessage) and tool.tool_call_id == ai.tool_calls[0]["id"]
    assert out[4].tool_calls[0]["id"] == out[5].tool_call_id != tool.tool_call_id
    assert isinstance(out[6], AIMessage) and not out[6].tool_calls


def test_to_langchain_starts_with_user_turn():
    out = to_langchain("S", [Msg("assistant", "a", "c"), Msg("tool", "r")])
    assert isinstance(out[1], HumanMessage) and isinstance(out[2], AIMessage)
