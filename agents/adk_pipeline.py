"""ADK engine — Gemini 3.5 Flash reasoning over the official Elastic MCP server.

Four google-adk LlmAgents (Sentinel, RootCause, Guardian, Scribe) in a
SequentialAgent. Sentinel/RootCause/Scribe get the Elastic MCP toolset
(streamable HTTP, read tools only: esql / get_mappings / search /
list_indices). Guardian gets exactly ONE tool: our token-gated
quarantine_and_flip function, which pauses at the shared ApprovalRegistry
until the operator's press-and-hold APPROVE stamp mints a single-use token.
ADK's request_confirmation is deliberately not used.

Selected only when MODE=real and GOOGLE_API_KEY is set and google-adk imports;
every import is lazy so mock mode never needs the package. Any runtime failure
raises AdkRunError and app/main.py falls back to the deterministic engine for
that run (labeled in the SSE stream).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Awaitable, Callable

import config
from agents import guardian, rootcause, scribe, sentinel
from integrations.approval import registry

Emit = Callable[[str, dict], Awaitable[None]]


class AdkRunError(Exception):
    """The ADK engine could not complete the run; caller falls back."""


def availability() -> tuple[bool, str]:
    """(usable, reason). Usable iff MODE=real + GOOGLE_API_KEY + importable."""
    if config.MODE != "real":
        return False, "mock mode"
    if not config.GOOGLE_API_KEY:
        return False, "no GOOGLE_API_KEY"
    try:
        _import_adk()
    except ImportError as exc:
        return False, f"google-adk not importable: {exc}"
    return True, "ok"


def _import_adk() -> dict[str, Any]:
    from google.adk.agents import LlmAgent, SequentialAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    try:
        from google.adk.tools.mcp_tool import McpToolset
    except ImportError:  # spelling differs across ADK versions
        from google.adk.tools.mcp_tool import MCPToolset as McpToolset
    try:
        from google.adk.tools.mcp_tool import StreamableHTTPConnectionParams
    except ImportError:
        from google.adk.tools.mcp_tool.mcp_session_manager import (
            StreamableHTTPConnectionParams,
        )
    from google.genai import types

    return {
        "LlmAgent": LlmAgent, "SequentialAgent": SequentialAgent, "Runner": Runner,
        "InMemorySessionService": InMemorySessionService, "McpToolset": McpToolset,
        "StreamableHTTPConnectionParams": StreamableHTTPConnectionParams, "types": types,
    }


def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of an LLM reply (fences and prose tolerated)."""
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


async def run_adk(client, event: dict, emit: Emit, run_id: str, *,
                  approval_timeout: float = 300.0, pace: float = 0.0) -> dict:
    """Drive the loop with Gemini + ADK, emitting the same SSE sequence as the
    deterministic engine. Raises AdkRunError on any failure (caller falls back)."""
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")
    os.environ.setdefault("GOOGLE_API_KEY", config.GOOGLE_API_KEY or "")
    try:
        adk = _import_adk()
    except ImportError as exc:
        raise AdkRunError(f"google-adk import failed: {exc}") from exc

    t0 = time.monotonic()
    today = event["today_index"]
    yesterday = event["yesterday_index"]
    day = event["day"]

    await emit("run_started", {
        "run_id": run_id, "mode": client.mode, "engine": "adk", "day": day,
        "alias": config.ALIAS,
        "alias_target": await asyncio.to_thread(client.alias_target, config.ALIAS),
    })
    await emit("engine", {"name": "adk", "model": config.GEMINI_MODEL})

    status = await asyncio.to_thread(
        sentinel.read_status, client, day, event.get("pipeline_status", "UNKNOWN"))
    await emit("pipeline_status", {
        "status": status.get("status", "UNKNOWN"),
        "run_number": status.get("run", "#4471"), "day": day,
    })

    # Strip-chart context: replay the trailing healthy days deterministically
    # (cheap MCP reads), then let Gemini examine today's partition itself.
    for hday in event.get("history_days", []):
        try:
            checks = await asyncio.to_thread(
                sentinel.check_index, client, config.partition_index(hday))
        except Exception:
            continue
        for check in checks:
            await emit("sentinel_check", check.as_event())
        if pace:
            await asyncio.sleep(pace)

    baselines = {
        m: await asyncio.to_thread(client.get_baseline, m) for m in sentinel.METRICS
    }
    ctx: dict[str, Any] = {}

    # --- Guardian's only tool: the token-gated consequential action ---
    async def quarantine_and_flip(day: str, today_index: str, yesterday_index: str) -> dict:
        """Quarantine rows missing the revenue field from today_index and atomically
        flip the read alias to yesterday_index. Pauses for human approval; performs
        NO writes unless the operator approves within the timeout."""
        plan = guardian.build_plan(today_index, yesterday_index, day)
        registry.request(run_id, plan)
        diagnosis = ctx.get("diagnosis")
        await emit("awaiting_approval", {
            "action": "quarantine", "plan": plan,
            "index": today_index, "target_index": yesterday_index,
            "estimated_impact_usd": round(diagnosis.estimated_impact_usd, 2) if diagnosis else 0.0,
        })
        token = await registry.wait(run_id, approval_timeout)
        if not token:
            reason = "operator_rejected" if registry.state(run_id) == "rejected" else "timeout"
            ctx["vetoed"] = reason
            return {"status": "vetoed", "reason": reason}
        try:
            gres = await asyncio.to_thread(
                guardian.act, client, today_index, yesterday_index, day,
                registry=registry, run_id=run_id, token=token)
        except guardian.ApprovalDenied as exc:
            ctx["vetoed"] = str(exc)
            return {"status": "vetoed", "reason": str(exc)}
        ctx["guardian_result"] = gres
        ctx["token_id"] = token[:8]
        await emit("guardian_action", {
            "step": "quarantine", "rows": gres.quarantined,
            "detail": f"moved {gres.quarantined} corrupt rows -> {gres.q_index}"})
        await emit("guardian_action", {
            "step": "alias_flip", "alias": gres.alias,
            "from": gres.alias_was, "to": gres.alias_now,
            "detail": f"update_aliases: {gres.alias} {gres.alias_was} -> {gres.alias_now}"})
        v = gres.verify
        await emit("verify", v)
        await emit("teletype", {
            "line": (f"  -> target={v['target']}  rows={v['row_count']}  "
                     f"null_rate={v['null_rate']:.3f}  avg={v['avg_amount']:.2f}  OK")})
        return {"status": "ok", "quarantined": gres.quarantined,
                "alias_now": gres.alias_now, "verify": v}

    mcp_kwargs: dict[str, Any] = {"url": config.MCP_URL}
    if config.MCP_AUTH_HEADER:
        mcp_kwargs["headers"] = {"Authorization": config.MCP_AUTH_HEADER}
    read_tools = adk["McpToolset"](
        connection_params=adk["StreamableHTTPConnectionParams"](**mcp_kwargs),
        tool_filter=["list_indices", "get_mappings", "search", "esql"],
    )

    base = (f"You are part of SilentBreak, a data-integrity examiner. Context: "
            f"day={day}, today_index={today}, yesterday_index={yesterday}, "
            f"read alias={config.ALIAS}, revenue field=`{config.REVENUE_FIELD}`, "
            f"z threshold={config.Z_THRESHOLD}. Pipeline status doc says "
            f"{status.get('status')!r}. Baselines (mean/std): "
            + ", ".join(f"{m}={b['mean']:.4f}/{b['std']:.4f}" for m, b in baselines.items())
            + ". Reply with ONLY one JSON object, no markdown fences.")

    sentinel_agent = adk["LlmAgent"](
        name="sentinel", model=config.GEMINI_MODEL, tools=[read_tools],
        output_key="sentinel_findings",
        instruction=base + (
            f" TASK: examine {today}. Use the esql tool "
            f"(FROM {today} | STATS ...) for row_count and COUNT_DISTINCT(sku); use the "
            f"search tool with a bool must_not exists query on `{config.REVENUE_FIELD}` "
            f"(size 0, track_total_hits true) to count rows missing revenue; compute "
            f"null_rate = missing/row_count. Do NOT run AVG on a field that may not "
            f"exist; if `{config.REVENUE_FIELD}` is absent from get_mappings, avg_amount=0. "
            f"z = abs(value-mean)/std per metric. Output JSON: "
            '{"checks": [{"metric": str, "value": num, "baseline_mean": num, '
            '"baseline_std": num, "z": num, "breach": bool}], '
            '"contradiction": null or {"metric": str, "value": num, '
            '"baseline_mean": num, "z": num}}'))
    rootcause_agent = adk["LlmAgent"](
        name="rootcause", model=config.GEMINI_MODEL, tools=[read_tools],
        output_key="rootcause_diagnosis",
        instruction=base + (
            f" TASK: sentinel found {{sentinel_findings}}. If contradiction is null, "
            'output {"skip": true}. Otherwise call get_mappings on both '
            f"{today} and {yesterday}, diff the field names, estimate nulled_rows "
            f"(null_rate x row_count) and estimated_impact_usd (nulled_rows x baseline "
            f"avg_amount mean). Output JSON: "
            '{"sentence": str, "removed_fields": [str], "added_fields": [str], '
            '"nulled_rows": int, "estimated_impact_usd": num}'))
    guardian_agent = adk["LlmAgent"](
        name="guardian", model=config.GEMINI_MODEL, tools=[quarantine_and_flip],
        output_key="guardian_result",
        instruction=base + (
            " TASK: diagnosis was {rootcause_diagnosis}. If it says skip, output "
            '{"skip": true}. Otherwise call quarantine_and_flip(day='
            f'"{day}", today_index="{today}", yesterday_index="{yesterday}") exactly '
            "once and report its result as JSON: "
            '{"status": str, "quarantined": int, "alias_now": str}'))
    scribe_agent = adk["LlmAgent"](
        name="scribe", model=config.GEMINI_MODEL, tools=[read_tools],
        output_key="incident_report",
        instruction=base + (
            " TASK: write the incident examiner report from sentinel_findings="
            "{sentinel_findings}, diagnosis={rootcause_diagnosis}, guardian="
            "{guardian_result}. Output JSON: "
            '{"report": str} where report is a terse typewriter-style examination '
            "record: FINDING / CAUSE / EXPOSURE / ACTION / VERIFICATION / DISPOSITION "
            "lines separated by newlines."))

    pipeline = adk["SequentialAgent"](
        name="silentbreak_pipeline",
        sub_agents=[sentinel_agent, rootcause_agent, guardian_agent, scribe_agent],
    )
    session_service = adk["InMemorySessionService"]()
    await session_service.create_session(
        app_name="silentbreak", user_id="operator", session_id=run_id)
    runner = adk["Runner"](agent=pipeline, app_name="silentbreak",
                           session_service=session_service)
    message = adk["types"].Content(role="user", parts=[adk["types"].Part(
        text=f"Examine pipeline day {day}. Status doc claims SUCCESS. Begin.")])

    contradiction_obj: sentinel.Contradiction | None = None
    try:
        async for ev in runner.run_async(user_id="operator", session_id=run_id,
                                         new_message=message):
            for fc in (ev.get_function_calls() or []):
                args = json.dumps(fc.args or {})[:140]
                await emit("teletype", {"line": f"> {fc.name} {args}"})
            if not (ev.is_final_response() and ev.content and ev.content.parts):
                continue
            text = "".join(p.text or "" for p in ev.content.parts if p.text)
            if not text.strip():
                continue
            payload = _extract_json(text)
            author = getattr(ev, "author", "")
            if author == "sentinel" and payload:
                for c in payload.get("checks", []):
                    await emit("sentinel_check", {
                        "metric": c.get("metric"), "value": c.get("value"),
                        "baseline_mean": c.get("baseline_mean"),
                        "baseline_std": c.get("baseline_std"),
                        "z": c.get("z"), "breach": bool(c.get("breach"))})
                con = payload.get("contradiction")
                if con:
                    contradiction_obj = sentinel.Contradiction(
                        metric=str(con["metric"]), value=float(con["value"]),
                        baseline_mean=float(con["baseline_mean"]), z=float(con["z"]),
                        pipeline_status=str(status.get("status", "UNKNOWN")))
                    await emit("contradiction", {
                        "headline": contradiction_obj.headline(),
                        "metric": contradiction_obj.metric,
                        "value": contradiction_obj.value,
                        "baseline_mean": contradiction_obj.baseline_mean,
                        "z": contradiction_obj.z,
                        "pipeline_status": contradiction_obj.pipeline_status})
            elif author == "rootcause" and payload and not payload.get("skip"):
                ctx["diagnosis"] = rootcause.Diagnosis(
                    removed=set(payload.get("removed_fields", [])),
                    added=set(payload.get("added_fields", [])),
                    sentence=str(payload.get("sentence", "")),
                    nulled_rows=int(payload.get("nulled_rows", 0)),
                    estimated_impact_usd=float(payload.get("estimated_impact_usd", 0.0)))
                await emit("diagnosis", ctx["diagnosis"].as_event())
            elif author == "scribe" and payload:
                ctx["report"] = str(payload.get("report", text))
            elif text.strip():
                await emit("teletype", {"line": text.strip()[:200]})
    except Exception as exc:
        raise AdkRunError(f"ADK runtime failure: {exc}") from exc
    finally:
        close = getattr(read_tools, "close", None)
        if close:
            try:
                result = close()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass

    duration_ms = int((time.monotonic() - t0) * 1000)
    if ctx.get("vetoed"):
        out = {"result": "vetoed", "reason": ctx["vetoed"], "duration_ms": duration_ms}
        await emit("run_complete", out)
        return out
    if contradiction_obj is None:
        out = {"result": "green", "duration_ms": duration_ms}
        await emit("run_complete", out)
        return out
    gres = ctx.get("guardian_result")
    diagnosis = ctx.get("diagnosis")
    if gres is None or diagnosis is None:
        raise AdkRunError("ADK run produced no actionable guardian/diagnosis output")
    incident = await asyncio.to_thread(
        scribe.record, client, contradiction_obj, diagnosis, gres, day,
        engine="adk", approved_token_id=ctx.get("token_id"),
        report=ctx.get("report"), report_author=config.GEMINI_MODEL)
    await emit("incident", {"doc": incident})
    out = {"result": "remediated", "today_index": today, "yesterday_index": yesterday,
           "incident": incident, "duration_ms": duration_ms}
    await emit("run_complete", {"result": "remediated", "duration_ms": duration_ms})
    return out
