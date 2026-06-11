"""Abuse + cost guards for the live /api/run endpoint.

The hosted real-mode demo burns real Gemini tokens per examination, so the
public RUN button needs two limits. Both are in-memory: the service runs with
--max-instances 1 (single-operator design, documented), so a process-global
counter is the whole fleet.

1. Per-IP token bucket (SILENTBREAK_RUNS_PER_IP_PER_HOUR, default 12): a single
   visitor cannot drain the budget. Over the limit => 429 rate_limited.
2. Daily global run budget (SILENTBREAK_DAILY_RUN_BUDGET, default 200, per UTC
   day): once exhausted, runs STILL work but are forced onto the deterministic
   engine with an honest label — the demo never goes dark, it degrades visibly.

Cloud Run terminates TLS at the GFE; the caller IP is the first value of
X-Forwarded-For. Falls back to the socket peer for local runs.
"""
from __future__ import annotations

import datetime as dt
import os
import threading
import time


def _int_env(name: str, default: int) -> int:
    try:
        val = int(os.getenv(name, str(default)))
        return val if val > 0 else default
    except (TypeError, ValueError):
        return default


def runs_per_ip_per_hour() -> int:
    return _int_env("SILENTBREAK_RUNS_PER_IP_PER_HOUR", 12)


def daily_run_budget() -> int:
    return _int_env("SILENTBREAK_DAILY_RUN_BUDGET", 200)


def client_ip(request) -> str:
    """First X-Forwarded-For hop (the real client on Cloud Run); else peer."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


class RateLimiter:
    """Per-IP sliding-window counter over the trailing hour. Thread-safe."""

    WINDOW_SECONDS = 3600

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, ip: str, limit: int | None = None) -> bool:
        limit = runs_per_ip_per_hour() if limit is None else limit
        now = time.monotonic()
        cutoff = now - self.WINDOW_SECONDS
        with self._lock:
            hits = [t for t in self._hits.get(ip, ()) if t > cutoff]
            if len(hits) >= limit:
                self._hits[ip] = hits
                return False
            hits.append(now)
            self._hits[ip] = hits
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class DailyBudget:
    """Global run counter that rolls over at UTC midnight. Thread-safe.

    consume() returns True while budget remains (and increments); once the
    day's budget is spent it returns False — the caller then forces the
    deterministic engine instead of refusing the run.
    """

    def __init__(self) -> None:
        self._day: str | None = None
        self._count = 0
        self._lock = threading.Lock()

    @staticmethod
    def _utc_day() -> str:
        return dt.datetime.now(dt.timezone.utc).date().isoformat()

    def consume(self, budget: int | None = None) -> bool:
        budget = daily_run_budget() if budget is None else budget
        today = self._utc_day()
        with self._lock:
            if self._day != today:
                self._day = today
                self._count = 0
            if self._count >= budget:
                return False
            self._count += 1
            return True

    def reset(self) -> None:
        with self._lock:
            self._day = None
            self._count = 0


# Process-global instances (single instance = whole fleet under max-instances 1).
rate_limiter = RateLimiter()
daily_budget = DailyBudget()
