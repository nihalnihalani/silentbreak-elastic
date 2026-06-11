"""EventBus — buffered replay for late subscribers, JSON-safe envelopes.

Assertions are tolerant of ADDITIVE envelope/data fields (e.g. a future
`agent` key): they check required keys are present, never exact dict equality.
"""
import asyncio
import json

from app.events import EventBus


def run(coro):
    return asyncio.run(coro)


def test_late_subscriber_gets_buffered_replay():
    async def scenario():
        bus = EventBus("run-1")
        await bus.emit("sentinel.check", {"metric": "null_rate", "breach": True})
        await bus.emit("rootcause.diagnosis", {"sentence": "upstream rename"})
        # Subscribe AFTER the events were emitted; replay must deliver both.
        stream = bus.stream(heartbeat=0.05)
        first = await asyncio.wait_for(anext(stream), timeout=2)
        second = await asyncio.wait_for(anext(stream), timeout=2)
        await stream.aclose()
        return first, second

    first, second = run(scenario())
    for sse in (first, second):
        assert sse.startswith("event: ")
    env1 = json.loads(first.split("data: ", 1)[1].strip())
    env2 = json.loads(second.split("data: ", 1)[1].strip())
    assert env1["type"] == "sentinel.check"
    assert env1["data"]["metric"] == "null_rate"
    assert env2["type"] == "rootcause.diagnosis"
    assert env2["seq"] > env1["seq"]


def test_envelope_is_json_serializable_with_required_keys():
    async def scenario():
        bus = EventBus("run-2")
        return await bus.emit("guardian.action", {"action": "alias flip", "agent": "guardian"})

    envelope = run(scenario())
    # Round-trips through JSON (the SSE wire format depends on this).
    decoded = json.loads(json.dumps(envelope))
    assert {"seq", "ts", "type", "data"} <= set(decoded)
    assert decoded["type"] == "guardian.action"
    # Additive data fields (like `agent`) must pass through untouched.
    assert decoded["data"]["action"] == "alias flip"


def test_seq_is_monotonic_and_buffer_ordered():
    async def scenario():
        bus = EventBus("run-3")
        for i in range(5):
            await bus.emit("tick", {"i": i})
        return bus.events

    events = run(scenario())
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == 5
    assert [e["data"]["i"] for e in events] == [0, 1, 2, 3, 4]


def test_idle_stream_emits_heartbeat():
    async def scenario():
        bus = EventBus("run-4")
        stream = bus.stream(heartbeat=0.05)
        hb = await asyncio.wait_for(anext(stream), timeout=2)
        await stream.aclose()
        return hb

    assert run(scenario()).startswith(":")
