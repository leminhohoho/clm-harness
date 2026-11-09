"""LLM usage → cost, session aggregates (persisted in state.json) and the live metrics snapshot."""

import time

from .events import Events, State

_AGG = dict(cost_main=0.0, cost_sub=0.0, unpriced=0, tin=0, tout=0, cached=0, calls_main=0, calls_sub=0,
            last_latency=None, runs=0, detached=0, edits=0, freed=0)


def cost(prices: dict, model: str, tin: int, tout: int, cached: int) -> float | None:
    """USD; prices are per 1M tokens. `tin` includes cached input tokens."""
    p = prices.get(model)
    if not p:
        return None
    return ((tin - cached) * p["in"] + cached * p.get("cached", p["in"]) + tout * p["out"]) / 1e6


class Metrics:
    def __init__(self, state: State, events: Events, budget: int, prices: dict):
        self.state, self.events, self.budget, self.prices = state, events, budget, prices
        self.agg = {**_AGG, **state.data.get("metrics", {})}
        self.live = {"state": "idle", "context": 0, "jobs_running": 0}
        self.started = time.time()

    def _changed(self, persist: bool = True) -> None:
        if persist:
            self.state.data["metrics"] = self.agg
            self.state.save()
        self.events.publish(self.snapshot())

    def usage(self, scope: str, model: str, tin: int, tout: int, cached: int, latency: float) -> None:
        c = cost(self.prices, model, tin, tout, cached)
        a = self.agg
        a[f"calls_{scope}"] += 1
        a["tin"] += tin
        a["tout"] += tout
        a["cached"] += cached
        a["last_latency"] = latency
        if c is None:
            a["unpriced"] += 1
        else:
            a[f"cost_{scope}"] += c
        self.events.emit("usage", scope=scope, model=model, tin=tin, tout=tout, cached=cached, cost=c, latency=latency)
        self._changed()

    def bump(self, **inc: int) -> None:
        for k, v in inc.items():
            self.agg[k] += v
        self._changed()

    def set(self, **live) -> None:
        self.live.update(live)
        self._changed(persist=False)

    def snapshot(self) -> dict:
        a, l = self.agg, self.live
        return {
            "type": "metrics",
            "context": {"tokens": l["context"], "budget": self.budget,
                        "pct": round(100 * l["context"] / self.budget, 1) if self.budget else 0,
                        "edits": a["edits"], "freed": a["freed"]},
            "cost": {"total": a["cost_main"] + a["cost_sub"], "main": a["cost_main"], "sub": a["cost_sub"],
                     "unpriced_calls": a["unpriced"]},
            "tokens": {"in": a["tin"], "out": a["tout"], "cached": a["cached"],
                       "cache_rate": a["cached"] / a["tin"] if a["tin"] else 0},
            "calls": {"main": a["calls_main"], "sub": a["calls_sub"], "last_latency": a["last_latency"]},
            "runs": {"total": a["runs"], "detached": a["detached"], "jobs_running": l["jobs_running"]},
            "agent": {"state": l["state"], "uptime": time.time() - self.started},
        }
