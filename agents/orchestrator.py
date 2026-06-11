"""Orchestrator — the deterministic engine for the SilentBreak loop.

Sentinel -> RootCause -> [HITL approval gate] -> Guardian -> verify -> Scribe.

Two entry points:
  run()        sync, print-friendly — used by scripts/run_demo.py and the smoke
               test. The default approver mints a REAL token through the same
               ApprovalRegistry the UI uses (and says so).
  run_async()  async, event-emitting — used by app/main.py to drive the SSE
               stream; pauses at the ApprovalRegistry until the operator's
               press-and-hold APPROVE stamp (or REJECT) decides.

The ADK/Gemini engine (agents/adk_pipeline.py) mirrors the same event sequence.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Awaitable, Callable

import config
from agents import guardian, rootcause, scribe, sentinel
from integrations.approval import registry

Emit = Callable[[str, dict], Awaitable[None]]


# ---------------------------------------------------------------- sync (CLI)
def _cli_auto_approver(run_id: str, plan: list[str]) -> str | None:
    print("[approval ] operator pressed APPROVE (auto-approved in CLI demo)")
    return registry.approve(run_id)


def run(client, event: dict, approver: Callable[[str, list[str]], str | None] | None = None) -> dict:
    """event = {"day", "today_index", "yesterday_index", "pipeline_status"}"""
    today = event["today_index"]
    yesterday = event["yesterday_index"]
    day = event["day"]

    # Memory layer: recall prior incidents for this pipeline before examining.
    memory = sentinel.recall_memory(client)
    prior_count = int(memory.get("prior_count", 0))

    status = sentinel.read_status(client, day, fallback=event.get("pipeline_status", "UNKNOWN"))

    contradiction = sentinel.detect(client, today, status["status"])
    if contradiction is None:
        return {"result": "green", "message": "No contradiction. Pipeline data is healthy.",
                "memory": memory}

    diagnosis = rootcause.diagnose(client, today, yesterday, contradiction)
    repair_from = sorted(diagnosis.added)[0] if diagnosis.added else "gross_amount"

    run_id = uuid.uuid4().hex[:12]
    plan = guardian.build_plan(today, yesterday, day, repair_from=repair_from)
    registry.request(run_id, plan)
    token = (approver or _cli_auto_approver)(run_id, plan)
    if not token:
        return {"result": "vetoed", "reason": "operator_rejected", "memory": memory,
                "contradiction": contradiction, "diagnosis": diagnosis}

    try:
        result = guardian.act(client, today, yesterday, day,
                              registry=registry, run_id=run_id, token=token,
                              repair_from=repair_from)
    except guardian.ApprovalDenied as exc:
        return {"result": "vetoed", "reason": str(exc), "memory": memory,
                "contradiction": contradiction, "diagnosis": diagnosis}

    incident = scribe.record(client, contradiction, diagnosis, result, day,
                             engine="deterministic", approved_token_id=token[:8],
                             prior_count=prior_count)
    return {
        "result": "remediated",
        "memory": memory,
        "contradiction": contradiction,
        "diagnosis": diagnosis,
        "guardian": result,
        "incident": incident,
    }


# ------------------------------------------------------------- async (SSE)
async def run_async(client, event: dict, emit: Emit, run_id: str, *,
                    engine_name: str = "deterministic", engine_reason: str | None = None,
                    pace: float = 0.0, approval_timeout: float = 300.0) -> dict:
    """Drive the full loop, emitting the canonical SSE event sequence."""
    t0 = time.monotonic()
    today = event["today_index"]
    yesterday = event["yesterday_index"]
    day = event["day"]

    def done(result: str, **extra) -> dict:
        return {"result": result, "duration_ms": int((time.monotonic() - t0) * 1000), **extra}

    await emit("run_started", {
        "run_id": run_id, "mode": client.mode, "engine": engine_name, "day": day,
        "alias": config.ALIAS,
        "alias_target": await asyncio.to_thread(client.alias_target, config.ALIAS),
    })
    engine_data: dict = {"name": engine_name}
    if engine_name == "adk":
        engine_data["model"] = config.GEMINI_MODEL
    if engine_reason:
        engine_data["reason"] = engine_reason
    await emit("engine", engine_data)

    # Memory layer: recall prior incidents for this pipeline (Elastic incident
    # index = the agent's long-term memory) right after run start.
    memory = await asyncio.to_thread(sentinel.recall_memory, client)
    prior_count = int(memory.get("prior_count", 0))
    await emit("memory", {"agent": "sentinel", **memory})

    # 1) the pipeline's own story: green
    await emit("teletype", {"line": f"> GET {config.STATUS_INDEX}/_search  day={day}"})
    status = await asyncio.to_thread(
        sentinel.read_status, client, day, event.get("pipeline_status", "UNKNOWN"))
    await emit("pipeline_status", {
        "status": status.get("status", "UNKNOWN"),
        "run_number": status.get("run", "#4471"), "day": day,
    })

    # 2) Sentinel: trailing healthy days first (the strip chart draws live),
    #    then today's partition where the contradiction lives.
    history: list[str] = list(event.get("history_days", []))
    sweep = [(d, config.partition_index(d)) for d in history] + [(day, today)]
    contradiction: sentinel.Contradiction | None = None
    for sweep_day, index in sweep:
        try:
            checks = await asyncio.to_thread(sentinel.check_index, client, index)
        except Exception:
            continue  # a missing history partition never kills the run
        if index == today:
            await emit("teletype", {
                "line": (f"> FROM {index} | STATS COUNT(*), "
                         f"COUNT_DISTINCT(sku), AVG({config.REVENUE_FIELD})")})
        for check in checks:
            await emit("sentinel_check", check.as_event())
            if index == today and check.breach and contradiction is None:
                contradiction = sentinel.Contradiction(
                    metric=check.metric, value=check.value,
                    baseline_mean=check.baseline_mean, z=check.z,
                    pipeline_status=status.get("status", "UNKNOWN"),
                )
        if pace:
            await asyncio.sleep(pace)

    if contradiction is None:
        result = done("green")
        await emit("run_complete", result)
        return result

    await emit("contradiction", {
        "headline": contradiction.headline(), "metric": contradiction.metric,
        "value": contradiction.value, "baseline_mean": contradiction.baseline_mean,
        "z": contradiction.z, "pipeline_status": contradiction.pipeline_status,
    })

    # 3) RootCause: mapping diff + estimated impact
    await emit("teletype", {"line": f"> GET {today}/_mapping   vs   GET {yesterday}/_mapping"})
    diagnosis = await asyncio.to_thread(rootcause.diagnose, client, today, yesterday, contradiction)
    await emit("diagnosis", diagnosis.as_event())

    # 4) HITL: nothing below this line happens without the operator's stamp
    repair_from = sorted(diagnosis.added)[0] if diagnosis.added else "gross_amount"
    plan = guardian.build_plan(today, yesterday, day, repair_from=repair_from)
    registry.request(run_id, plan)
    await emit("awaiting_approval", {
        "action": "quarantine", "plan": plan, "index": today, "target_index": yesterday,
        "estimated_impact_usd": round(diagnosis.estimated_impact_usd, 2),
    })
    token = await registry.wait(run_id, approval_timeout)
    if not token:
        reason = "operator_rejected" if registry.state(run_id) == "rejected" else "timeout"
        result = done("vetoed", reason=reason)
        await emit("run_complete", result)
        return result

    # 5) Guardian (token consumed inside; no token, no writes)
    try:
        gres = await asyncio.to_thread(
            guardian.act, client, today, yesterday, day,
            registry=registry, run_id=run_id, token=token, repair_from=repair_from)
    except guardian.ApprovalDenied as exc:
        result = done("vetoed", reason=str(exc))
        await emit("run_complete", result)
        return result

    await emit("guardian_action", {
        "step": "quarantine", "rows": gres.quarantined,
        "detail": f"moved {gres.quarantined} corrupt rows -> {gres.q_index}",
    })
    if pace:
        await asyncio.sleep(pace)
    await emit("guardian_action", {
        "step": "alias_flip", "alias": gres.alias, "from": gres.alias_was, "to": gres.alias_now,
        "detail": f"update_aliases: {gres.alias} {gres.alias_was} -> {gres.alias_now}",
    })

    # 5b) REPAIR: recovery reindex already ran inside guardian.act (same
    # approved token); surface it. Alias stays on last-known-good by design.
    await emit("teletype", {
        "line": (f"> _reindex {gres.q_index} -> {gres.repaired_index}  "
                 f"painless: {gres.repair_rename}  ({gres.repaired_docs} docs)")})
    await emit("repair", {
        "agent": "guardian", "repaired_index": gres.repaired_index,
        "docs": gres.repaired_docs, "rename": gres.repair_rename,
    })

    # 6) verify downstream THROUGH the alias
    v = gres.verify
    await emit("teletype", {
        "line": (f"> FROM {config.ALIAS} | STATS COUNT(*), AVG({config.REVENUE_FIELD})")})
    await emit("verify", v)
    await emit("teletype", {
        "line": (f"  -> target={v['target']}  rows={v['row_count']}  "
                 f"null_rate={v['null_rate']:.3f}  avg={v['avg_amount']:.2f}  OK")})

    # 7) Scribe
    incident = await asyncio.to_thread(
        lambda: scribe.record(client, contradiction, diagnosis, gres, day,
                              engine=engine_name, approved_token_id=token[:8],
                              prior_count=prior_count))
    await emit("incident", {"doc": incident})

    result = done("remediated", today_index=today, yesterday_index=yesterday,
                  incident=incident)
    await emit("run_complete", {"result": "remediated", "duration_ms": result["duration_ms"]})
    return result
