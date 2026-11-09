import { describe, expect, test } from "vitest";
import { chatItems, fmtCost, fmtDur, fmtTok, mergeEvents, runningJobs, type Ev } from "./derive";

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
});

test("chat shows user and agent messages only, with opened markers", () => {
  const items = chatItems([
    { seq: 1, type: "user_message", id: 1, text: "hi", attachments: ["a.csv"] },
    { seq: 2, type: "notification", nseq: 1, text: "[notify] …" },
    { seq: 3, type: "llm_reasoning", text: "thinking" },
    { seq: 4, type: "message_opened", id: 1 },
    { seq: 5, type: "agent_message", id: 1, backend: "markdown", text: "**yo**" },
    { seq: 6, type: "user_message", id: 2, text: "more", attachments: [] },
  ]);
  expect(items.map((i) => [i.type, i.id])).toEqual([["user_message", 1], ["agent_message", 1], ["user_message", 2]]);
  expect(items[0]).toMatchObject({ opened: true });
  expect(items[2]).toMatchObject({ opened: false });
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
