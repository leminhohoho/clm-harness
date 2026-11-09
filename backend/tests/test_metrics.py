import pytest

from backend.events import Events, State
from backend.metrics import Metrics, cost

PRICES = {"m": {"in": 3, "out": 15, "cached": 0.3}}


def test_cost_splits_cached_input():
    # 1M in of which 400k cached, 100k out
    assert cost(PRICES, "m", 1_000_000, 100_000, 400_000) == pytest.approx(0.6 * 3 + 0.4 * 0.3 + 0.1 * 15)


def test_cost_unpriced_model_is_none():
    assert cost(PRICES, "other", 10, 10, 0) is None


@pytest.fixture
def parts(tmp_path):
    st, ev = State(tmp_path / "s.json"), Events(tmp_path / "e.jsonl")
    return st, ev


async def test_usage_aggregates_and_snapshot(parts, tmp_path):
    st, ev = parts
    q = ev.subscribe()
    m = Metrics(st, ev, budget=1000, prices=PRICES)
    m.usage("main", "m", 1_000_000, 0, 500_000, 2.5)
    m.usage("sub", "other", 100, 10, 0, 0.5)
    m.bump(runs=2, detached=1, edits=1, freed=300)
    m.set(state="thinking", context=250, jobs_running=1)
    s = m.snapshot()
    assert s["type"] == "metrics"
    assert s["context"] == {"tokens": 250, "budget": 1000, "pct": 25.0, "edits": 1, "freed": 300}
    assert s["cost"]["main"] == pytest.approx(0.5 * 3 + 0.5 * 0.3) and s["cost"]["sub"] == 0
    assert s["cost"]["total"] == pytest.approx(s["cost"]["main"]) and s["cost"]["unpriced_calls"] == 1
    assert s["tokens"]["in"] == 1_000_100 and s["tokens"]["cached"] == 500_000
    assert s["tokens"]["cache_rate"] == pytest.approx(500_000 / 1_000_100)
    assert s["calls"] == {"main": 1, "sub": 1, "last_latency": 0.5}
    assert s["runs"] == {"total": 2, "detached": 1, "jobs_running": 1}
    assert s["agent"]["state"] == "thinking" and s["agent"]["uptime"] >= 0
    usage = [e for e in ev.since(0) if e["type"] == "usage"]
    assert usage[0]["scope"] == "main" and usage[0]["cost"] == pytest.approx(s["cost"]["main"])
    assert usage[1]["cost"] is None and usage[1]["latency"] == 0.5
    published = [q.get_nowait() for _ in range(q.qsize())]
    assert published[-1]["type"] == "metrics"
    # aggregates survive restart
    m2 = Metrics(State(tmp_path / "s.json"), ev, budget=1000, prices=PRICES)
    assert m2.snapshot()["calls"]["main"] == 1 and m2.snapshot()["runs"]["total"] == 2
