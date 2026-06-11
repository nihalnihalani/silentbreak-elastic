"""ApprovalRegistry — the single HITL gate both engines pass through.

Nothing mutates Elastic state without a token minted here. The web UI's
press-and-hold APPROVE stamp calls POST /api/approve which mints a single-use,
TTL-bound token; the Guardian consumes it immediately before any write. The
CLI demo and the smoke test go through the exact same gate with an
auto-approver that mints a real token (and says so).

Pure stdlib: asyncio + secrets + time.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field

import config


@dataclass
class _Entry:
    plan: list[str]
    state: str = "awaiting"  # awaiting | approved | rejected
    token: str | None = None
    expires_at: float = 0.0
    consumed: bool = False
    decided: asyncio.Event = field(default_factory=asyncio.Event)


class ApprovalRegistry:
    def __init__(self, ttl_seconds: int | None = None):
        self._ttl = config.APPROVAL_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        self._runs: dict[str, _Entry] = {}

    def request(self, run_id: str, plan: list[str]) -> None:
        """Mark a run as awaiting an operator decision."""
        self._runs[run_id] = _Entry(plan=list(plan))

    async def wait(self, run_id: str, timeout: float) -> str | None:
        """Await the operator decision. Returns the token, or None on reject/timeout."""
        entry = self._runs.get(run_id)
        if entry is None:
            return None
        try:
            await asyncio.wait_for(entry.decided.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        return entry.token if entry.state == "approved" else None

    def approve(self, run_id: str) -> str:
        """Mint a single-use token (TTL-bound) and release the waiting run."""
        entry = self._runs.get(run_id)
        if entry is None:
            raise KeyError(run_id)
        if entry.state != "awaiting":
            raise ValueError(f"run {run_id} is {entry.state}, not awaiting")
        entry.token = secrets.token_hex(16)
        entry.expires_at = time.monotonic() + self._ttl
        entry.state = "approved"
        entry.decided.set()
        return entry.token

    def reject(self, run_id: str) -> None:
        entry = self._runs.get(run_id)
        if entry is None:
            raise KeyError(run_id)
        if entry.state != "awaiting":
            raise ValueError(f"run {run_id} is {entry.state}, not awaiting")
        entry.state = "rejected"
        entry.decided.set()

    def consume(self, run_id: str, token: str | None) -> bool:
        """Single-use validation. False on mismatch, expiry, or reuse."""
        entry = self._runs.get(run_id)
        if entry is None or not token or entry.token is None:
            return False
        if entry.consumed or token != entry.token:
            return False
        if time.monotonic() > entry.expires_at:
            return False
        entry.consumed = True
        return True

    def state(self, run_id: str) -> str:
        entry = self._runs.get(run_id)
        if entry is None:
            return "idle"
        return entry.state if entry.state != "awaiting" else "awaiting"

    def plan(self, run_id: str) -> list[str]:
        entry = self._runs.get(run_id)
        return list(entry.plan) if entry else []


# One global gate, imported by the app and both engines.
registry = ApprovalRegistry()
