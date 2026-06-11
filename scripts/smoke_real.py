"""End-to-end real-mode smoke test (run by scripts/smoke.sh).

Asserts the full loop against the docker-compose stack:
MCP tool discovery -> seed -> inject drift -> detect -> diagnose -> token-gated
quarantine + alias flip -> verify -> incident -> reverse -> cleanup.

Uses the isolated index prefix `sales-smoke-` so it never collides with demo
data. Exit 0 and a final `SMOKE PASS` line mean the stack is healthy.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
import urllib.request
from typing import Any

PREFIX = "sales-smoke-"
os.environ["SILENTBREAK_MODE"] = "real"
os.environ["SILENTBREAK_INDEX_PREFIX"] = PREFIX

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from seed_baseline import BASELINE_INDEX, STATUS_INDEX, connect, partition, seed
from inject_drift import inject

_CURRENT_STEP = "init"


def step(name: str) -> None:
    global _CURRENT_STEP
    _CURRENT_STEP = name
    print(f"\n=== [smoke] {name} ===")


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)
    print(f"[smoke] OK: {msg}")


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def http_ping(mcp_url: str) -> str | None:
    """Liveness probe on the MCP server's plain-HTTP /ping (returns 'Ready')."""
    base = mcp_url.rsplit("/mcp", 1)[0]
    try:
        with urllib.request.urlopen(f"{base}/ping", timeout=5) as resp:
            return resp.read().decode().strip()
    except Exception:
        return None


def alias_target_direct(es, alias: str) -> str | None:
    resp = es.options(ignore_status=404).indices.get_alias(name=alias)
    keys = [k for k in resp.keys() if not k.startswith("error")]
    return keys[0] if keys else None


def null_rate_direct(es, index: str, field: str) -> float:
    total = es.count(index=index)["count"] or 1
    missing = es.count(index=index, query={
        "bool": {"must_not": {"exists": {"field": field}}}})["count"]
    return missing / total


def main() -> None:
    # ---- a. MCP tool discovery (locks the unverified tool schemas) ----
    step("mcp discovery")
    from integrations.mcp_client import McpReader
    reader = McpReader()
    pong = http_ping(getattr(config, "MCP_URL", "http://localhost:8080/mcp"))
    # The official server answers "Ready" on /ping (observed; docs said "pong").
    check(pong in ("Ready", "pong"), f"MCP /ping is live (got {pong!r})")
    tools = reader.list_tools()
    print(f"[smoke] MCP tools: {tools}")
    for required in ("esql", "get_mappings"):
        check(required in tools, f"MCP server exposes `{required}`")
    if hasattr(reader, "tool_input_properties"):
        for name in ("esql", "get_mappings", "search", "list_indices"):
            print(f"[smoke] inputSchema {name}: {sorted(reader.tool_input_properties(name))}")

    # ---- b. seed 30 healthy smoke days ----
    step("seed")
    es = connect()
    print(f"[smoke] ES: {es.info()['version']['number']}")
    # The alias name is shared with the demo world; remember where it pointed
    # so cleanup can put it back (smoke must never wreck a seeded demo).
    prior_alias_target = alias_target_direct(es, config.ALIAS)
    if prior_alias_target:
        print(f"[smoke] noting prior alias target {prior_alias_target} (restored at cleanup)")
    seeded = seed(es, days=30, prefix=PREFIX, rows_per_day=2000)
    yesterday_index = seeded["yesterday_index"]
    check(es.count(index=yesterday_index)["count"] == 2000,
          f"{yesterday_index} holds 2000 rows")

    # ---- c. inject the silent drift ----
    step("inject drift")
    injected = inject(es, prefix=PREFIX, rows_per_day=2000)
    day, today_index = injected["day"], injected["index"]
    check(alias_target_direct(es, config.ALIAS) == today_index,
          f"alias {config.ALIAS} points at poisoned {today_index}")
    check(null_rate_direct(es, today_index, config.REVENUE_FIELD) > 0.99,
          "poisoned partition has ~100% null revenue")

    # ---- d. detect + diagnose + token-gated act (deterministic engine) ----
    step("agent loop")
    from integrations.elastic_client import ElasticClient
    from integrations import approval
    from agents import orchestrator
    registry = getattr(approval, "registry", None) or approval.ApprovalRegistry()
    client = ElasticClient(mode="real")

    def auto_approver(run_id: str, *_args: Any, **_kwargs: Any) -> str:
        token = registry.approve(run_id)
        print(f"[smoke] auto-approver pressed APPROVE "
              f"(token {token[:8]}… minted via ApprovalRegistry — same gate, test-labeled)")
        return token

    event = {"day": day, "today_index": today_index,
             "yesterday_index": yesterday_index, "pipeline_status": "SUCCESS"}
    out = orchestrator.run(client, event, approver=auto_approver)

    # ---- e. assertions on the run ----
    step("assertions")
    check(out["result"] == "remediated", f"run result is remediated ({out['result']})")
    c, d = out["contradiction"], out["diagnosis"]
    check(_field(c, "metric") == "null_rate",
          f"contradiction metric is null_rate (z={_field(c, 'z')})")
    check(float(_field(c, "z") or 0) >= config.Z_THRESHOLD,
          f"z {_field(c, 'z')} >= threshold {config.Z_THRESHOLD}")
    sentence = str(_field(d, "sentence"))
    check("gross_amount" in sentence, f"diagnosis names gross_amount: {sentence!r}")
    check(alias_target_direct(es, config.ALIAS) == yesterday_index,
          f"alias flipped to last-known-good {yesterday_index}")
    nr = null_rate_direct(es, config.ALIAS, config.REVENUE_FIELD)
    check(nr < 0.01, f"downstream null_rate through alias = {nr:.4f} < 0.01")
    es.indices.refresh(index=config.INCIDENT_INDEX)
    incidents = es.search(index=config.INCIDENT_INDEX, size=100,
                          query={"match_all": {}})["hits"]["hits"]
    smoke_incidents = [h for h in incidents if PREFIX in json.dumps(h["_source"])]
    check(len(smoke_incidents) >= 1, f"incident doc persisted ({len(smoke_incidents)} found)")

    # ---- f. reverse (prove the flip is a real, reversible state change) ----
    step("reverse")
    client.update_alias(config.ALIAS, today_index)
    check(alias_target_direct(es, config.ALIAS) == today_index,
          f"alias reversed back to {today_index}")
    q_index = config.quarantine_index(day)
    es.indices.refresh(index=q_index)
    q_count = es.count(index=q_index)["count"]
    check(q_count > 0, f"quarantine index {q_index} holds {q_count} rows")

    # ---- g. cleanup ----
    step("cleanup")
    from reset_world import _resolve
    for pattern in (f"{PREFIX}*", q_index):
        for name in _resolve(es, pattern):
            es.options(ignore_status=404).indices.delete(index=name)
            print(f"[smoke] deleted index {name}")
    for metric in ("null_rate", "avg_amount", "distinct_sku"):
        es.options(ignore_status=404).delete(index=BASELINE_INDEX, id=f"{PREFIX}{metric}")
    for status_day in seeded["days"] + [day]:
        es.options(ignore_status=404).delete(index=STATUS_INDEX, id=f"{PREFIX}{status_day}")
    for hit in smoke_incidents:
        es.options(ignore_status=404).delete(index=config.INCIDENT_INDEX, id=hit["_id"])
    print("[smoke] smoke docs removed from baselines/status/incidents")
    if prior_alias_target and es.indices.exists(index=prior_alias_target):
        es.indices.update_aliases(actions=[
            {"add": {"index": prior_alias_target, "alias": config.ALIAS}}])
        print(f"[smoke] alias {config.ALIAS} restored -> {prior_alias_target}")

    print("\nSMOKE PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        print(f"\nSMOKE FAIL at step: {_CURRENT_STEP}")
        sys.exit(1)
