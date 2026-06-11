"""EventBus — per-run event buffer + live SSE fan-out.

Every event is wrapped in the envelope {"seq", "ts", "type", "data"} and kept
in order, so a UI that connects after POST /api/run (or reconnects) replays
the whole story before streaming live. SSE wire format:

    event: <type>
    data: {"seq": 3, "ts": "...", "type": "<type>", "data": {...}}

A heartbeat comment line `: hb` goes out every 15s of silence.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import AsyncIterator


class EventBus:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.events: list[dict] = []
        self._cond = asyncio.Condition()
        self._seq = 0

    async def emit(self, type_: str, data: dict) -> dict:
        async with self._cond:
            self._seq += 1
            envelope = {
                "seq": self._seq,
                "ts": datetime.now(timezone.utc).isoformat(),
                "type": type_,
                "data": data,
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
