"""CLM context file: render the editable message list to text and parse it back."""

import re
from dataclasses import dataclass

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

ROLES = ("user", "assistant", "tool")
_HEADER = re.compile(r"^\[\[CTX_TURN \d+ role=(\w+)\]\][ \t]*$\n?", re.M)
_CODE = re.compile(r"\A(.*?)\n?^\[\[CODE\]\]\n(.*)\n\[\[/CODE\]\]\Z", re.S | re.M)
_MARK = r"\[\[(?:CTX_TURN|/?CODE\]\])"


class ParseError(ValueError):
    pass


@dataclass
class Msg:
    role: str
    content: str
    code: str | None = None  # assistant only: the python tool call

    def __post_init__(self):
        self.content = self.content.strip("\n")
        if self.code is not None:
            self.code = self.code.strip("\n")


def _esc(s: str) -> str:
    return re.sub(rf"^(\\*{_MARK})", r"\\\1", s, flags=re.M)


def _unesc(s: str) -> str:
    return re.sub(rf"^\\(\\*{_MARK})", r"\1", s, flags=re.M)


def render(msgs: list[Msg]) -> str:
    out = []
    for i, m in enumerate(msgs):
        body = _esc(m.content)
        if m.code is not None:
            body = f"{body}\n[[CODE]]\n{_esc(m.code)}\n[[/CODE]]".lstrip("\n")
        out.append(f"[[CTX_TURN {i} role={m.role}]]\n{body}\n")
    return "\n".join(out)


def _fold(m: Msg) -> Msg:
    return Msg("assistant", f"{m.content}\n\n```python\n{m.code}\n```".strip("\n"))


def parse(text: str) -> list[Msg]:
    parts = _HEADER.split(text)
    if parts[0].strip():
        raise ParseError("text before the first [[CTX_TURN i role=…]] header")
    msgs = []
    for role, body in zip(parts[1::2], parts[2::2]):
        if role not in ROLES:
            raise ParseError(f"unknown role {role!r} (expected user, assistant or tool)")
        body = body.strip("\n")
        m = _CODE.match(body) if role == "assistant" else None
        msgs.append(Msg(role, _unesc(m[1]), _unesc(m[2])) if m else Msg(role, _unesc(body)))
    # keep a valid chat sequence: fold unanswered tool calls, drop orphan tool results
    res: list[Msg] = []
    for m in msgs:
        open_call = bool(res) and res[-1].role == "assistant" and res[-1].code is not None
        if m.role == "tool" and not open_call:
            continue
        if open_call and m.role != "tool":
            res[-1] = _fold(res[-1])
        res.append(m)
    if res and res[-1].code is not None:
        res[-1] = _fold(res[-1])
    return res


def tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token)."""
    return len(text) // 4


def to_langchain(system: str, msgs: list[Msg]) -> list[BaseMessage]:
    out: list[BaseMessage] = [SystemMessage(system)]
    if not msgs or msgs[0].role != "user":
        out.append(HumanMessage("[context start]"))
    for i, m in enumerate(msgs):
        if m.role == "user":
            out.append(HumanMessage(m.content))
        elif m.role == "tool":
            out.append(ToolMessage(m.content or "(no output)", tool_call_id=f"call_{i - 1}"))
        elif m.code is not None:
            out.append(AIMessage(m.content, tool_calls=[{"name": "python", "args": {"code": m.code}, "id": f"call_{i}"}]))
        else:
            out.append(AIMessage(m.content))
    return out
