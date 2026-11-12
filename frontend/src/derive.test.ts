import { describe, expect, test } from "vitest";
import { chatItems, contextEdits, firstIndex, fmtCost, fmtDur, fmtSize, fmtTok, foldDiff, isImage, mergeEvents, openedIds, rawUrl, runningJobs, timeOf, traceCards, type DiffLine, type Ev } from "./derive";

describe("formatters", () => {
  test("tokens", () => {
    expect(fmtTok(950)).toBe("950");
    expect(fmtTok(12400)).toBe("12.4k");
    expect(fmtTok(1_210_000)).toBe("1.21M");
  });
  test("cost: unpriced shows dash, small amounts keep precision", () => {
    expect(fmtCost(null)).toBe("—");
    expect(fmtCost(0.42)).toBe("$0.42");
    expect(fmtCost(0.0042)).toBe("$0.004");
  });
  test("durations", () => {
    expect(fmtDur(2.43)).toBe("2.4s");
    expect(fmtDur(192)).toBe("3m12s");
    expect(fmtDur(11520)).toBe("3h12m");
  });
  test("file sizes", () => {
    expect(fmtSize(0)).toBe("0 B");
    expect(fmtSize(1023)).toBe("1023 B");
    expect(fmtSize(1536)).toBe("1.5 KB");
    expect(fmtSize(2.5 * 1024 * 1024)).toBe("2.5 MB");
    expect(fmtSize(3 * 1024 ** 3)).toBe("3.0 GB");
  });
  test("bubble time is local HH:MM, blank without ts", () => {
    expect(timeOf(new Date(2026, 0, 1, 9, 5).getTime() / 1000)).toBe("09:05");
    expect(timeOf(undefined)).toBe("");
  });
});

test("chat shows user and agent messages only; opened from the REST flag or a live message_opened", () => {
  const evs: Ev[] = [
    { seq: 1, type: "user_message", id: 1, text: "hi", attachments: ["a.csv"] },
    { seq: 2, type: "notification", nseq: 1, text: "[notify] …" },
    { seq: 3, type: "llm_reasoning", text: "thinking" },
    { seq: 4, type: "message_opened", id: 1 },
    { seq: 5, type: "agent_message", id: 1, text: "**yo**", files: [{ name: "a.png", size: 3 }] },
    { seq: 6, type: "user_message", id: 2, text: "more", attachments: [] },
    { seq: 7, type: "user_message", id: 3, text: "paged", attachments: [], opened: true },
    { seq: 8, type: "user_message", id: 4, text: "paged", attachments: [], opened: false },
  ];
  const items = chatItems(evs, openedIds([...evs, { seq: 9, type: "message_opened", id: 4 }]));
  expect(items.map((i) => [i.type, i.id])).toEqual([["user_message", 1], ["agent_message", 1], ["user_message", 2], ["user_message", 3], ["user_message", 4]]);
  expect(items.map((i) => i.type === "user_message" && i.opened)).toEqual([true, false, false, true, true]);
  expect(items[1]).toMatchObject({ files: [{ name: "a.png", size: 3 }] });
});

describe("traceCards", () => {
  const usage = (seq: number, scope = "main"): Ev =>
    ({ seq, type: "usage", scope, model: "m", tin: 10, tout: 2, cached: 0, cost: null, latency: 1 });

  test("pairs main usage with reasoning and code with its result; state is not a card", () => {
    const cards = traceCards([
      { seq: 1, type: "state", state: "thinking" },
      usage(2),
      { seq: 3, type: "llm_reasoning", text: "plan" },
      { seq: 4, type: "code", code: "x" },
      { seq: 5, type: "state", state: "executing" },
      usage(6, "sub"),
      { seq: 7, type: "run_result", output: "ok" },
      usage(8),
      { seq: 9, type: "code", code: "y" },
    ]);
    expect(cards).toEqual([
      { kind: "thinking", seq: 2, usage: usage(2), text: "plan" },
      { kind: "exec", seq: 4, code: "x", output: "ok" },
      { kind: "event", seq: 6, e: usage(6, "sub") },
      { kind: "thinking", seq: 8, usage: usage(8) },
      { kind: "exec", seq: 9, code: "y" },
    ]);
  });

  test("reasoning after a texted or missing thinking card starts its own; results go to the latest open exec", () => {
    const cards = traceCards([
      { seq: 1, type: "run_result", output: "orphan" },
      { seq: 2, type: "llm_reasoning", text: "a" },
      { seq: 3, type: "llm_reasoning", text: "b" },
      { seq: 4, type: "code", code: "p" },
      { seq: 5, type: "code", code: "q" },
      { seq: 6, type: "run_result", output: "for q" },
      { seq: 7, type: "run_result", output: "for p" },
      { seq: 8, type: "message_opened", id: 1 },
    ]);
    expect(cards.map((c) => [c.kind, c.seq])).toEqual([["exec", 1], ["thinking", 2], ["thinking", 3], ["exec", 4], ["exec", 5], ["event", 8]]);
    expect(cards[0]).toEqual({ kind: "exec", seq: 1, output: "orphan" });
    expect(cards.slice(1, 5).map((c) => ("text" in c ? c.text : "output" in c && c.output))).toEqual(["a", "b", "for p", "for q"]);
  });
});

test("firstIndex counts down for rows older than the first live seq only", () => {
  const rows = (...seqs: number[]) => seqs.map((seq) => ({ seq }));
  const base = firstIndex(rows(10, 12), 10);
  expect(firstIndex(rows(10, 12, 13), 10)).toBe(base); // live append: no shift
  expect(firstIndex(rows(3, 5, 10, 12), 10)).toBe(base - 2); // two older rows prepended
  expect(firstIndex(rows(3, 5), 10)).toBe(base - 2); // view has no live rows yet
  expect(firstIndex(rows(1, 2), 0)).toBe(base); // empty live at seed: nothing is older
});

test("images are recognised by extension, case-insensitively", () => {
  expect(["a.png", "B.JPG", "c.jpeg", "d.gif", "e.webp", "f.svg"].every(isImage)).toBe(true);
  expect(["a.csv", "png", "x.png.txt", "y.svgz"].some(isImage)).toBe(false);
});

test("rawUrl encodes each path segment but keeps the slashes", () => {
  expect(rawUrl("p1", ".harness/outbox/3/a b#1.png")).toBe("/api/p/p1/raw/.harness/outbox/3/a%20b%231.png");
  expect(rawUrl("p1", "x?.csv", true)).toBe("/api/p/p1/raw/x%3F.csv?download=1");
});

test("running jobs follow job updates", () => {
  const ids = runningJobs([
    { seq: 1, type: "job_update", id: 3, status: "running" },
    { seq: 2, type: "job_update", id: 4, status: "detached" },
    { seq: 3, type: "job_update", id: 5, status: "running" },
    { seq: 4, type: "job_update", id: 3, status: "finished" },
    { seq: 5, type: "job_update", id: 5, status: "killed" },
  ]);
  expect(ids).toEqual([4]);
});

test("mergeEvents appends a batch, dropping already-seen seqs (reconnect overlap)", () => {
  const ev = (seq: number): Ev => ({ seq, type: "state", state: "idle" });
  const evs = mergeEvents([], [ev(1), ev(2)]);
  expect(mergeEvents(evs, [ev(2)])).toBe(evs);
  expect(mergeEvents(evs, [ev(2), ev(3), ev(3), ev(4)]).map((e) => e.seq)).toEqual([1, 2, 3, 4]);
});

test("contextEdits lists numbered edits newest first, rejected ones included", () => {
  const edits = contextEdits([
    { seq: 1, type: "context_edit", ok: true, receipt: "old event without n" },
    { seq: 2, type: "context_edit", ok: true, receipt: "a", n: 1 },
    { seq: 3, type: "state", state: "idle" },
    { seq: 4, type: "context_edit", ok: false, receipt: "rejected: x", n: 2 },
  ]);
  expect(edits.map((e) => e.n)).toEqual([2, 1]);
});

describe("foldDiff", () => {
  const run = (op: DiffLine["op"], n: number, tag: string = op): DiffLine[] =>
    Array.from({ length: n }, (_, i) => ({ op, text: `${tag}${i}` }));

  test("long unchanged runs keep 3 lines of context on each side", () => {
    const lines = [...run("+", 1), ...run(" ", 10), ...run("+", 1, "b")];
    const out = foldDiff(lines);
    expect(out.map((r) => ("fold" in r ? `fold${r.fold}` : r.text))).toEqual(
      ["+0", " 0", " 1", " 2", "fold4", " 7", " 8", " 9", "b0"]);
    const fold = out[4];
    expect("fold" in fold && fold.lines.map((l) => l.text)).toEqual([" 3", " 4", " 5", " 6"]);
  });

  test("runs of up to 8 unchanged lines stay unfolded", () => {
    const lines = run(" ", 8);
    expect(foldDiff(lines)).toEqual(lines);
  });

  test("long removed runs show their head, then a fold", () => {
    const out = foldDiff(run("-", 10));
    expect(out.slice(0, 3)).toEqual(run("-", 3));
    expect(out[3]).toEqual({ fold: 7, lines: run("-", 10).slice(3) });
    expect(out).toHaveLength(4);
  });

  test("added lines are never folded", () => {
    const lines = run("+", 30);
    expect(foldDiff(lines)).toEqual(lines);
  });
});
