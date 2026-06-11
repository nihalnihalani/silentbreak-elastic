"""EventBus — per-run event buffer + live SSE fan-out.

Every event is wrapped in the envelope {"seq", "ts", "type", "agent", "data"}
and kept in order, so a UI that connects after POST /api/run (or reconnects)
replays the whole story before streaming live. SSE wire format:

    event: <type>
    data: {"seq": 3, "ts": "...", "type": "<type>", "agent": "sentinel", "data": {...}}

The `agent` field ("sentinel"|"rootcause"|"guardian"|"scribe"|"system") lets
the UI light an agent rail; it is injected centrally here (additive: it also
mirrors into data["agent"], never replacing existing payload keys).

A heartbeat comment line `: hb` goes out every 15s of silence.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import AsyncIterator

# Which agent each event type belongs to (data["agent"] overrides per-event).
AGENT_BY_EVENT = {
    "run_started": "system",
    "engine": "system",
    "teletype": "system",
    "pipeline_status": "sentinel",
    "sentinel_check": "sentinel",
    "memory": "sentinel",
    "contradiction": "sentinel",
    "diagnosis": "rootcause",
    "awaiting_approval": "guardian",
    "approved": "system",
    "rejected": "system",
    "guardian_action": "guardian",
    "repair": "guardian",
    "verify": "guardian",
    "reversed": "guardian",
    "incident": "scribe",
    "run_complete": "system",
    "error": "system",
}


class EventBus:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.events: list[dict] = []
        self._cond = asyncio.Condition()
        self._seq = 0

    async def emit(self, type_: str, data: dict) -> dict:
        agent = data.get("agent") or AGENT_BY_EVENT.get(type_, "system")
        payload = dict(data)
        payload.setdefault("agent", agent)
        async with self._cond:
            self._seq += 1
            envelope = {
                "seq": self._seq,
                "ts": datetime.now(timezone.utc).isoformat(),
                "type": type_,
                "agent": agent,
                "data": payload,
            }
            self.events.append(envelope)
            self._cond.notify_all()
        return envelope

    @staticmethod
    def to_sse(envelope: dict) -> str:
        return f"event: {envelope['type']}\ndata: {json.dumps(envelope)}\n\n"

    async def stream(self, heartbeat: float = 15.0) -> AsyncIterator[str]:
        """Replay the buffer, then stream live events (heartbeats while idle)."""
        cursor = 0
        while True:
            async with self._cond:
                if cursor >= len(self.events):
                    try:
                        await asyncio.wait_for(self._cond.wait(), timeout=heartbeat)
                    except asyncio.TimeoutError:
                        pass
                fresh = self.events[cursor:]
                cursor = len(self.events)
            if fresh:
                for envelope in fresh:
                    yield self.to_sse(envelope)
            else:
                yield ": hb\n\n"
