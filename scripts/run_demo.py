"""End-to-end SilentBreak demo — runs the full loop on the Python stdlib only.

    python scripts/run_demo.py

Generates a healthy 30-day baseline, lands a corrupted partition (an upstream
column rename that silently nulls revenue), then runs the agent graph:
detect contradiction -> diagnose the mutation -> quarantine + flip the alias ->
record the incident -> prove reversibility.
"""
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from integrations.elastic_client import ElasticClient
from agents import orchestrator
from agents import guardian

random.seed(42)
HEALTHY_DAYS = [f"2026-05-{d:02d}" for d in range(1, 31)]
YESTERDAY = "2026-05-31"
TODAY = "2026-06-01"


def make_healthy_partition(n=10000):
    rows = []
    for i in range(n):
        rows.append({
            "order_id": i,
            "sku": f"SKU-{random.randint(1, 500)}",
            "amount": round(random.gauss(43, 12), 2),   # the loader-mapped revenue field
        })
    # ~0.2% naturally-null revenue rows
    for r in random.sample(rows, k=max(1, n // 500)):
        r["amount"] = None
    return rows


def make_corrupted_partition(n=10000):
    """Upstream renamed `amount` -> `gross_amount`. The loader still maps `amount`,
    so every row's `amount` is null while `gross_amount` carries the value. The
    pipeline still exits 0 — a textbook silent failure."""
    rows = []
    for i in range(n):
        val = round(random.gauss(43, 12), 2)
        rows.append({"order_id": i, "sku": f"SKU-{random.randint(1, 500)}",
                     "gross_amount": val})   # note: NO `amount` key -> loader nulls revenue
    return rows


def build_baseline(client):
    null_rates, avgs, distincts = [], [], []
    for day in HEALTHY_DAYS:
        rows = make_healthy_partition()
        client.index_rows(config.partition_index(day), rows)
        s = client.esql_stats(config.partition_index(day))
        null_rates.append(s["null_rate"]); avgs.append(s["avg_amount"]); distincts.append(s["distinct_sku"])
    # Floor std to a realistic measurement-noise level so z-scores read believably
    # (a zero-variance baseline would otherwise produce an absurd z).
    client.put_baseline("null_rate", statistics.mean(null_rates), max(statistics.pstdev(null_rates), 0.025))
    client.put_baseline("avg_amount", statistics.mean(avgs), max(statistics.pstdev(avgs), 0.5))
    client.put_baseline("distinct_sku", statistics.mean(distincts), max(statistics.pstdev(distincts), 5))


def main():
    print("=" * 68)
    print("  SilentBreak — green pipeline, broken data")
    print("=" * 68)
    client = ElasticClient(mode="mock")

    build_baseline(client)
    client.index_rows(config.partition_index(YESTERDAY), make_healthy_partition())
    client.set_alias(config.ALIAS, config.partition_index(YESTERDAY))

    # land today's corrupted partition; the pipeline reports SUCCESS
    today_index = config.partition_index(TODAY)
    client.index_rows(today_index, make_corrupted_partition())
    client.set_alias(config.ALIAS, today_index)  # consumers now read the poison

    print(f"\n[pipeline] run #4471 — SUCCESS ✓   (alias {config.ALIAS} -> {today_index})")
    print(f"[finance ] dashboard reads revenue from `{config.REVENUE_FIELD}` via the alias...\n")

    event = {"day": TODAY, "today_index": today_index,
             "yesterday_index": config.partition_index(YESTERDAY),
             "pipeline_status": "SUCCESS"}
    out = orchestrator.run(client, event)

    if out["result"] == "green":
        print(out["message"]); return

    c, d, g, inc = out["contradiction"], out["diagnosis"], out["guardian"], out["incident"]
    print("[Sentinel ] " + c.headline())
    print("[RootCause] " + d.sentence)
    print("[Guardian ] action:")
    for a in g.actions:
        print("            - " + a)
    print(f"[finance  ] alias now -> {client.alias_target(config.ALIAS)} (last-known-good, clean)")
    print(f"[Scribe   ] incident {inc['incident_id']} -> {inc['status']}")

    # Prove reversibility (it's a real state change, not a canned video)
    print("\n[verify   ] flipping the alias BACK to prove it's a real, reversible state change...")
    guardian.rollback(client, g.alias_was)
    print(f"[verify   ] alias -> {client.alias_target(config.ALIAS)}  (reversible ✓)")
    print("\nDone. This is the loop the 3-min demo dramatizes on real Elastic data.")


if __name__ == "__main__":
    main()
