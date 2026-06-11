"""Abuse + cost guards on the live /api/run endpoint.

The hosted real-mode RUN button burns real Gemini tokens, so it needs a per-IP
rate limit (429) and a daily global budget that, once spent, forces the labeled
deterministic engine instead of refusing the run.
"""
import os

import pytest

os.environ.setdefault("SILENTBREAK_MODE", "mock")

from fastapi.testclient import TestClient  # noqa: E402

from app import guards  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    # Each test starts from clean counters (single-operator process globals).
    guards.rate_limiter.reset()
    guards.daily_budget.reset()
    with TestClient(app) as c:
        yield c
    guards.rate_limiter.reset()
    guards.daily_budget.reset()


# ---- RateLimiter unit ----
def test_rate_limiter_blocks_over_limit():
    rl = guards.RateLimiter()
    assert [rl.allow("1.1.1.1", 3) for _ in range(4)] == [True, True, True, False]


def test_rate_limiter_is_per_ip():
    rl = guards.RateLimiter()
    assert rl.allow("a", 1) is True
    assert rl.allow("a", 1) is False  # a is now over its limit
    assert rl.allow("b", 1) is True   # b has its own bucket


# ---- DailyBudget unit ----
def test_daily_budget_exhausts_then_blocks():
    b = guards.DailyBudget()
    assert [b.consume(2) for _ in range(3)] == [True, True, False]


def test_daily_budget_rolls_over_on_new_utc_day():
    b = guards.DailyBudget()
    assert b.consume(1) is True
    assert b.consume(1) is False
    b._day = "1999-01-01"  # simulate the clock crossing UTC midnight
    assert b.consume(1) is True


# ---- client_ip ----
def test_client_ip_prefers_first_forwarded_hop():
    class Req:
        headers = {"x-forwarded-for": "203.0.113.7, 10.0.0.1"}
        client = None
    assert guards.client_ip(Req()) == "203.0.113.7"


def test_client_ip_falls_back_to_peer():
    class Peer:
        host = "127.0.0.1"

    class Req:
        headers = {}
        client = Peer()
    assert guards.client_ip(Req()) == "127.0.0.1"


# ---- endpoint: rate limit -> 429 ----
def test_api_run_rate_limited_returns_429(client, monkeypatch):
    monkeypatch.setattr(guards, "runs_per_ip_per_hour", lambda: 2)
    hdr = {"X-Forwarded-For": "198.51.100.5"}
    assert client.post("/api/run", headers=hdr).status_code == 202
    assert client.post("/api/run", headers=hdr).status_code in (202, 409)
    # By the third request the per-IP bucket is drained regardless of run state.
    resp = client.post("/api/run", headers=hdr)
    assert resp.status_code == 429
    assert resp.json()["error"] == "rate_limited"


def test_rate_limit_check_precedes_run_lock(client, monkeypatch):
    # A flooder over the limit gets 429, not a 409 run_active — the guard is
    # evaluated before the singleton run lock.
    monkeypatch.setattr(guards, "runs_per_ip_per_hour", lambda: 1)
    hdr = {"X-Forwarded-For": "198.51.100.9"}
    assert client.post("/api/run", headers=hdr).status_code == 202
    assert client.post("/api/run", headers=hdr).status_code == 429


# ---- endpoint: daily budget exhausted -> still runs, deterministic label ----
def test_api_run_budget_exhausted_forces_deterministic(client, monkeypatch):
    # Budget 0 => every run is forced deterministic; mock engine is already
    # deterministic, so this asserts the run still starts and is labeled honestly.
    monkeypatch.setattr(guards, "daily_run_budget", lambda: 0)
    resp = client.post("/api/run")
    assert resp.status_code == 202
    body = resp.json()
    assert body["engine"] == "deterministic"


def test_budget_exhausted_run_emits_honest_label(monkeypatch):
    # The forced-deterministic run must NARRATE the reason: a teletype budget
    # note plus the engine event carrying reason="daily_gemini_budget_reached".
    # We drive the real _execute_run lifecycle but feed it a pre-built event (so
    # the heavy mock seed is skipped) and a stub orchestrator that just echoes
    # the engine_reason it was handed — this asserts the lifecycle threads the
    # forced reason through to the engine label, fast and gate-free.
    import asyncio

    import app.main as main
    from app.events import EventBus

    async def fake_run_async(client, event, emit, run_id, *, engine_name="deterministic",
                             engine_reason=None, **_kw):
        data = {"name": engine_name}
        if engine_reason:
            data["reason"] = engine_reason
        await emit("engine", data)
        return {"result": "green", "duration_ms": 0}

    monkeypatch.setattr(main.orchestrator, "run_async", fake_run_async)

    event = {"day": "2026-06-01", "today_index": "sales-2026-06-01",
             "yesterday_index": "sales-2026-05-31", "pipeline_status": "SUCCESS",
             "history_days": []}

    async def drive():
        rec = main.RunRecord(
            run_id="budgettest", bus=EventBus("budgettest"), engine="deterministic",
            forced_deterministic_reason="daily_gemini_budget_reached")
        await main._execute_run(rec, event, "deterministic",
                                "daily_gemini_budget_reached")
        return [(e["type"], e["data"]) for e in rec.bus.events]

    events = asyncio.run(drive())
    engine_data = next(d for t, d in events if t == "engine")
    assert engine_data.get("name") == "deterministic"
    assert engine_data.get("reason") == "daily_gemini_budget_reached"
    teletype_lines = " ".join(d.get("line", "") for t, d in events if t == "teletype")
    assert "daily Gemini budget" in teletype_lines
