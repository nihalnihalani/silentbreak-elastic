"""SilentBreak FastAPI app — the Polygraph's backend.

    uvicorn app.main:app --port 8000

Serves the UI, drives a run (deterministic engine always; ADK/Gemini engine
when MODE=real + GOOGLE_API_KEY + google-adk importable), streams the typed
SSE events the UI renders, and exposes the HITL endpoints: the press-and-hold
APPROVE stamp posts /api/approve which mints the single-use token the Guardian
consumes before any Elastic write; /api/reverse proves the alias flip is a
real, reversible state change.
"""
from __future__ import annotations

import asyncio
import os
import random
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import config
from agents import adk_pipeline, guardian, orchestrator
from app.events import EventBus
from integrations.approval import registry
from integrations.elastic_client import ElasticClient

# ---------------------------------------------------------------- run state
PHASE_BY_EVENT = {
    "run_started": "detect",
    "memory": "detect",
    "sentinel_check": "detect",
    "contradiction": "diagnose",
    "diagnosis": "diagnose",
    "awaiting_approval": "awaiting_approval",
    "approved": "quarantine",
    "guardian_action": "quarantine",
    "repair": "quarantine",
    "verify": "verify",
}


@dataclass
class RunRecord:
    run_id: str
    bus: EventBus
    engine: str
    phase: str = "idle"
    task: asyncio.Task | None = None
    result: dict | None = None
    today_index: str | None = None
    yesterday_index: str | None = None
    reversed: bool = False
    incident: dict | None = None
    started_at: float = field(default_factory=time.monotonic)
    last_event_at: float = field(default_factory=time.monotonic)


# On Cloud Run, CPU is throttled when no request is in flight, so a run whose
# spectator closed the tab can freeze mid-loop and wedge the singleton run
# state for the next visitor. A fresh /api/run reaps any active run older
# than this instead of returning 409. Reaping is safe pre-approval (no token,
# no writes); post-approval the loop runs to completion in seconds.
STALE_RUN_SECONDS = 180


RUNS: dict[str, RunRecord] = {}
RUN_ORDER: list[str] = []
_client: ElasticClient | None = None
_client_error: str | None = None

app = FastAPI(title="SilentBreak — Polygraph")


def get_client() -> ElasticClient:
    global _client, _client_error
    if _client is None:
        try:
            _client = ElasticClient()
            _client_error = None
        except Exception as exc:
            _client_error = str(exc)
            raise
    return _client


def select_engine() -> tuple[str, str | None]:
    usable, reason = adk_pipeline.availability()
    if usable:
        return "adk", None
    return "deterministic", reason


def active_record() -> RunRecord | None:
    for run_id in reversed(RUN_ORDER):
        rec = RUNS[run_id]
        if rec.task is not None and not rec.task.done():
            return rec
    return None


def reap_or_409(rec: RunRecord) -> JSONResponse | None:
    """409 while a fresh run is active; cancel and clear a stale one."""
    if time.monotonic() - rec.started_at < STALE_RUN_SECONDS:
        return JSONResponse({"error": "run_active", "run_id": rec.run_id}, status_code=409)
    rec.task.cancel()
    rec.phase = "vetoed"
    return None


def latest_record() -> RunRecord | None:
    return RUNS[RUN_ORDER[-1]] if RUN_ORDER else None


def _tracking_emit(rec: RunRecord):
    async def emit(type_: str, data: dict) -> None:
        rec.last_event_at = time.monotonic()
        if type_ in PHASE_BY_EVENT:
            rec.phase = PHASE_BY_EVENT[type_]
        elif type_ == "run_complete":
            rec.phase = {"remediated": "resolved", "green": "green"}.get(
                data.get("result", ""), "vetoed")
        await rec.bus.emit(type_, data)
    return emit


class EngineHung(Exception):
    pass


# A Gemini/MCP call with no client timeout can hang silently forever — it
# never raises, so the fallback path never fires. The watchdog declares the
# engine hung when nothing has been emitted for this long, EXCEPT while the
# run is parked at the human approval gate (operator thinking time is not a
# hang; the gate has its own TTL).
ENGINE_IDLE_LIMIT_SECONDS = 120.0


async def _with_watchdog(rec: RunRecord, coro) -> dict | None:
    task = asyncio.create_task(coro)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=10)
            if done:
                return task.result()
            idle = time.monotonic() - rec.last_event_at
            if rec.phase != "awaiting_approval" and idle > ENGINE_IDLE_LIMIT_SECONDS:
                raise EngineHung(
                    f"engine emitted nothing for {int(idle)}s (phase={rec.phase})")
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass


# ------------------------------------------------- mock data (same story as
# scripts/run_demo.py: 30 healthy days, then a corrupted partition that the
# pipeline status doc still calls SUCCESS)
MOCK_HEALTHY_DAYS = [f"2026-05-{d:02d}" for d in range(1, 31)]
MOCK_YESTERDAY = "2026-05-31"
MOCK_TODAY = "2026-06-01"


def _healthy_partition(rng: random.Random, n: int = 10000) -> list[dict]:
    rows = [{"order_id": i, "sku": f"SKU-{rng.randint(1, 500)}",
             "amount": round(rng.gauss(43, 12), 2)} for i in range(n)]
    for r in rng.sample(rows, k=max(1, n // 500)):
        r["amount"] = None
    return rows


def _corrupted_partition(rng: random.Random, n: int = 10000) -> list[dict]:
    return [{"order_id": i, "sku": f"SKU-{rng.randint(1, 500)}",
             "gross_amount": round(rng.gauss(43, 12), 2)} for i in range(n)]


async def _seed_mock(client: ElasticClient, emit) -> dict:
    rng = random.Random(42)
    await emit("teletype", {"line": "> landing 30 healthy partitions (mock Elastic, in-memory)"})
    null_rates, avgs, distincts = [], [], []
    for day in MOCK_HEALTHY_DAYS:
        client.index_rows(config.partition_index(day), _healthy_partition(rng))
        s = client.esql_stats(config.partition_index(day))
        null_rates.append(s["null_rate"])
        avgs.append(s["avg_amount"])
        distincts.append(s["distinct_sku"])
    client.put_baseline("null_rate", statistics.mean(null_rates),
                        max(statistics.pstdev(null_rates), 0.025))
    client.put_baseline("avg_amount", statistics.mean(avgs),
                        max(statistics.pstdev(avgs), 0.5))
    client.put_baseline("distinct_sku", statistics.mean(distincts),
                        max(statistics.pstdev(distincts), 5))
    client.index_rows(config.partition_index(MOCK_YESTERDAY), _healthy_partition(rng))

    today_index = config.partition_index(MOCK_TODAY)
    client.index_rows(today_index, _corrupted_partition(rng))
    client.set_alias(config.ALIAS, today_index)
    client.index_status({"day": MOCK_TODAY, "run": "#4471", "status": "SUCCESS"})
    await emit("teletype", {
        "line": f"> pipeline run #4471 exit 0 — alias {config.ALIAS} -> {today_index}"})
    return {
        "day": MOCK_TODAY,
        "today_index": today_index,
        "yesterday_index": config.partition_index(MOCK_YESTERDAY),
        "pipeline_status": "SUCCESS",
        # exactly nine trailing healthy days (incl. yesterday), same as real mode
        "history_days": MOCK_HEALTHY_DAYS[-8:] + [MOCK_YESTERDAY],
    }


def _real_event(client: ElasticClient) -> dict | None:
    """Derive today/yesterday from the alias target (seeded by scripts/)."""
    target = client.alias_target(config.ALIAS)
    prefix = os.getenv("SILENTBREAK_INDEX_PREFIX", "sales-")
    if not target or not target.startswith(prefix):
        return None
    day_str = target[len(prefix):]
    try:
        day = date.fromisoformat(day_str)
    except ValueError:
        return None
    yesterday = (day - timedelta(days=1)).isoformat()
    # exactly nine trailing healthy days (incl. yesterday), same as mock mode
    history = [(day - timedelta(days=i)).isoformat() for i in range(9, 0, -1)]
    return {
        "day": day_str,
        "today_index": target,
        "yesterday_index": config.partition_index(yesterday),
        "pipeline_status": "SUCCESS",
        "history_days": history,
    }


async def _execute_run(rec: RunRecord, event: dict | None, engine: str,
                       engine_reason: str | None) -> None:
    client = get_client()
    emit = _tracking_emit(rec)
    pace = 0.45 if client.mode == "mock" else 0.15
    try:
        if event is None:  # default scenario: land the drama first (mock)
            rec.phase = "landing"
            event = await _seed_mock(client, emit)
        rec.today_index = event["today_index"]
        rec.yesterday_index = event["yesterday_index"]

        if engine == "adk":
            try:
                rec.result = await _with_watchdog(rec, adk_pipeline.run_adk(
                    client, event, emit, rec.run_id, pace=pace))
            except Exception as exc:
                if registry.state(rec.run_id) == "approved":
                    # The approval token was already minted (and possibly
                    # consumed): Elastic writes may have happened. NEVER
                    # re-run the loop on top of a half-applied remediation —
                    # report the error and end the run cleanly instead.
                    rec.phase = "vetoed"
                    reason = f"adk_failed_after_approval: {exc}"
                    await emit("error", {
                        "message": (f"ADK engine failed AFTER approval — writes may "
                                    f"have occurred; not re-running. {exc}")})
                    rec.result = {"result": "error", "reason": reason}
                    await emit("run_complete",
                               {"result": "error", "reason": reason, "duration_ms": 0})
                else:
                    # Failure before any approval: zero writes, safe to fall back.
                    await emit("error", {"message": f"ADK engine failed: {exc}"})
                    await emit("engine", {"name": "deterministic",
                                          "reason": f"adk_runtime_error: {exc}"})
                    rec.engine = "deterministic"
                    rec.result = await orchestrator.run_async(
                        client, event, emit, rec.run_id,
                        engine_name="deterministic",
                        engine_reason=f"adk_runtime_error: {exc}", pace=pace)
        else:
            rec.result = await orchestrator.run_async(
                client, event, emit, rec.run_id,
                engine_name="deterministic", engine_reason=engine_reason, pace=pace)
        if rec.result and rec.result.get("incident"):
            rec.incident = rec.result["incident"]
    except Exception as exc:  # never crash the server; tell the stream
        rec.phase = "vetoed"
        await rec.bus.emit("error", {"message": str(exc)})
        await rec.bus.emit("run_complete",
                           {"result": "vetoed", "reason": f"error: {exc}", "duration_ms": 0})


def _start_run(event: dict | None) -> RunRecord:
    engine, reason = select_engine()
    run_id = uuid.uuid4().hex[:12]
    rec = RunRecord(run_id=run_id, bus=EventBus(run_id), engine=engine)
    RUNS[run_id] = rec
    RUN_ORDER.append(run_id)
    rec.task = asyncio.create_task(_execute_run(rec, event, engine, reason))
    return rec


# ------------------------------------------------------------------- routes
# Google's frontend reserves /healthz on run.app domains and never forwards
# it to the container, so the canonical health route lives under /api/.
@app.get("/api/healthz")
@app.get("/healthz")
def healthz():
    engine, _ = select_engine()
    return {"ok": True, "mode": config.MODE, "engine": engine}


@app.get("/api/state")
def api_state():
    engine, _ = select_engine()
    rec = active_record() or latest_record()
    alias_target = None
    last_incident = None
    try:
        client = get_client()
        alias_target = client.alias_target(config.ALIAS)
        incidents = client.list_incidents()
        last_incident = incidents[0] if incidents else None
    except Exception:
        pass
    active = active_record()
    return {
        "mode": config.MODE,
        "engine": rec.engine if rec else engine,
        "phase": rec.phase if rec else "idle",
        "alias": config.ALIAS,
        "alias_target": alias_target,
        "active_run_id": active.run_id if active else None,
        "last_incident": last_incident,
    }


@app.post("/api/run")
async def api_run(request: Request):
    if (rec := active_record()) is not None and (resp := reap_or_409(rec)) is not None:
        return resp
    try:
        client = get_client()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    event: dict | None = None
    if client.mode == "real":
        event = _real_event(client)
        if event is None:
            return JSONResponse(
                {"error": "alias not seeded — run `make seed && make inject` first"},
                status_code=409)
    rec = _start_run(event)
    return JSONResponse({"run_id": rec.run_id, "mode": config.MODE, "engine": rec.engine},
                        status_code=202)


@app.post("/pipeline-completed")
async def pipeline_completed(request: Request):
    """Compat webhook: a pipeline reports completion; SilentBreak examines it."""
    if (rec := active_record()) is not None and (resp := reap_or_409(rec)) is not None:
        return resp
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    missing = [k for k in ("day", "today_index", "yesterday_index") if k not in body]
    if missing:
        return JSONResponse({"error": "missing_fields", "fields": missing}, status_code=422)
    event = {
        "day": body["day"],
        "today_index": body["today_index"],
        "yesterday_index": body["yesterday_index"],
        "pipeline_status": body.get("pipeline_status", "SUCCESS"),
        "history_days": body.get("history_days", []),
    }
    rec = _start_run(event)
    return JSONResponse({"run_id": rec.run_id}, status_code=202)


@app.get("/api/events")
async def api_events(run_id: str | None = None):
    rec = RUNS.get(run_id) if run_id else latest_record()
    if rec is None:
        return JSONResponse({"error": "no_such_run"}, status_code=404)
    return StreamingResponse(
        rec.bus.stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _decision(request: Request, decide: str):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    run_id = body.get("run_id") or (active_record().run_id if active_record() else None)
    rec = RUNS.get(run_id) if run_id else None
    if rec is None:
        return JSONResponse({"error": "no_such_run"}, status_code=404)
    if registry.state(rec.run_id) != "awaiting":
        return JSONResponse({"error": "not_awaiting"}, status_code=409)
    if decide == "approve":
        token = registry.approve(rec.run_id)
        await rec.bus.emit("approved", {"token_id": token[:8]})
        rec.phase = "quarantine"
        return {"status": "approved", "token_id": token[:8]}
    registry.reject(rec.run_id)
    await rec.bus.emit("rejected", {"reason": "operator_rejected"})
    rec.phase = "vetoed"
    return {"status": "rejected"}


@app.post("/api/approve")
async def api_approve(request: Request):
    return await _decision(request, "approve")


@app.post("/api/reject")
async def api_reject(request: Request):
    return await _decision(request, "reject")


@app.post("/api/reverse")
async def api_reverse(request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    run_id = body.get("run_id") or (RUN_ORDER[-1] if RUN_ORDER else None)
    rec = RUNS.get(run_id) if run_id else None
    if rec is None:
        return JSONResponse({"error": "no_such_run"}, status_code=404)
    if (rec.result is None or rec.result.get("result") != "remediated"
            or rec.reversed or not rec.today_index):
        return JSONResponse({"error": "nothing_to_reverse"}, status_code=409)
    client = get_client()
    await asyncio.to_thread(guardian.reverse, client, rec.today_index)
    rec.reversed = True
    await rec.bus.emit("reversed", {"alias": config.ALIAS, "now": rec.today_index})
    await rec.bus.emit("teletype", {
        "line": f"> update_aliases: {config.ALIAS} -> {rec.today_index}  (reversed — reversibility proven)"})
    return {"status": "reversed", "alias": config.ALIAS, "now": rec.today_index}


@app.get("/api/incidents")
def api_incidents():
    try:
        return {"incidents": get_client().list_incidents()}
    except Exception:
        return {"incidents": []}


# Static UI last so /api/* and /healthz win the route match.
_UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui")
if os.path.isdir(_UI_DIR):
    app.mount("/", StaticFiles(directory=_UI_DIR, html=True), name="ui")
else:
    @app.get("/")
    def root():
        return {"app": "SilentBreak", "note": "ui/ not built yet; API is live",
                "try": ["/api/healthz", "/api/state", "POST /api/run", "/api/events"]}
