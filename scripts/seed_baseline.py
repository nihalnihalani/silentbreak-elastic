"""Seed a healthy multi-day sales baseline into Elasticsearch (real mode).

Writes N days of believable e-commerce partitions (`sales-YYYY-MM-DD`), computes
per-metric baselines into `silentbreak-baselines`, writes a SUCCESS status doc per
day into `silentbreak-status`, and points the `revenue_current` alias at
yesterday's (last healthy) partition.

This is a pure write-side setup tool: it talks to Elasticsearch directly via
elasticsearch-py (the official Elastic MCP server exposes no write tools; the
agent's reads go through MCP). Mock mode needs none of this — `run_demo.py`
seeds in-memory.

Importable API (used by scripts/smoke_real.py):
    connect() -> Elasticsearch
    seed(es, days=14, prefix=None, rows_per_day=10000) -> dict
"""
from __future__ import annotations

import datetime as dt
import os
import random
import statistics
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

DEFAULT_PREFIX = "sales-"
STATUS_INDEX: str = getattr(config, "STATUS_INDEX", "silentbreak-status")
BASELINE_INDEX: str = config.BASELINE_INDEX

# A fixed catalog so day-to-day stats are stable (same price book every day).
_CATALOG_RNG = random.Random(7)
CATEGORIES = ["apparel", "home", "outdoors", "electronics", "beauty"]
CATALOG: list[dict[str, Any]] = [
    {
        "sku": f"SKU-{i}",
        "category": CATEGORIES[i % len(CATEGORIES)],
        "unit_price": round(_CATALOG_RNG.uniform(6.0, 110.0), 2),
    }
    for i in range(1, 501)
]

SALES_PROPERTIES: dict[str, Any] = {
    "order_id": {"type": "keyword"},
    "ts": {"type": "date"},
    "sku": {"type": "keyword"},
    "category": {"type": "keyword"},
    "qty": {"type": "integer"},
    "unit_price": {"type": "float"},
    "amount": {"type": "double"},
    "customer_id": {"type": "keyword"},
    "channel": {"type": "keyword"},
}


def index_prefix() -> str:
    """Partition prefix; SILENTBREAK_INDEX_PREFIX lets the smoke test isolate itself."""
    return os.getenv("SILENTBREAK_INDEX_PREFIX", DEFAULT_PREFIX)


def partition(day: str, prefix: str | None = None) -> str:
    return f"{prefix or index_prefix()}{day}"


def day_strings(days: int, end: dt.date | None = None) -> list[str]:
    """The `days` calendar days ending yesterday (all healthy)."""
    today = end or dt.date.today()
    return [(today - dt.timedelta(days=d)).isoformat() for d in range(days, 0, -1)]


def connect():
    """Direct elasticsearch-py client from env/config (writes never go through MCP)."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    from elasticsearch import Elasticsearch

    cloud_id = getattr(config, "ELASTIC_CLOUD_ID", None) or os.getenv("ELASTIC_CLOUD_ID")
    api_key = getattr(config, "ELASTIC_API_KEY", None) or os.getenv("ELASTIC_API_KEY")
    if cloud_id and api_key:
        return Elasticsearch(cloud_id=cloud_id, api_key=api_key, request_timeout=60)
    es_url = getattr(config, "ES_URL", None) or os.getenv("ES_URL", "http://localhost:9200")
    if api_key:
        return Elasticsearch(es_url, api_key=api_key, request_timeout=60)
    return Elasticsearch(es_url, request_timeout=60)


def make_healthy_rows(day: str, n: int = 10000) -> list[dict[str, Any]]:
    """One day of believable orders. Deterministic per day (idempotent reseed)."""
    rng = random.Random(f"healthy-{day}")
    rows: list[dict[str, Any]] = []
    for i in range(n):
        item = CATALOG[rng.randrange(len(CATALOG))]
        qty = rng.choices([1, 2, 3], weights=[78, 17, 5])[0]
        discount = rng.choice([0.0, 0.0, 0.0, 0.05, 0.10, 0.15])
        rows.append({
            "order_id": f"{day}-{i:06d}",
            "ts": f"{day}T{rng.randrange(24):02d}:{rng.randrange(60):02d}:{rng.randrange(60):02d}Z",
            "sku": item["sku"],
            "category": item["category"],
            "qty": qty,
            "unit_price": item["unit_price"],
            "amount": round(qty * item["unit_price"] * (1 - discount), 2),
            "customer_id": f"C-{rng.randrange(1, 40000):05d}",
            "channel": rng.choices(["web", "mobile", "store"], weights=[55, 35, 10])[0],
        })
    # ~0.2% naturally-null revenue rows (payment-capture lag) so the baseline is honest.
    for r in rng.sample(rows, k=max(1, n // 500)):
        r["amount"] = None
    return rows


def local_stats(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Client-side mirror of the agent's esql_stats over generated rows."""
    n = len(rows) or 1
    vals = [r["amount"] for r in rows if r.get("amount") is not None]
    return {
        "row_count": float(len(rows)),
        "null_rate": sum(1 for r in rows if r.get("amount") is None) / n,
        "distinct_sku": float(len({r["sku"] for r in rows})),
        "avg_amount": (sum(vals) / len(vals)) if vals else 0.0,
    }


def recreate_index(es, name: str, properties: dict[str, Any]) -> None:
    es.options(ignore_status=404).indices.delete(index=name)
    es.indices.create(index=name, mappings={"properties": properties})


def bulk_rows(es, index: str, rows: list[dict[str, Any]]) -> None:
    from elasticsearch.helpers import bulk
    bulk(es, ({"_index": index, "_id": r["order_id"], "_source": r} for r in rows),
         chunk_size=2000)
    es.indices.refresh(index=index)


def set_alias(es, alias: str, target: str) -> None:
    """One atomic update_aliases: remove from everything, add to the target."""
    es.indices.update_aliases(actions=[
        {"remove": {"index": "*", "alias": alias, "must_exist": False}},
        {"add": {"index": target, "alias": alias}},
    ])


def put_baseline(es, metric: str, mean: float, std: float, prefix: str) -> None:
    es.index(index=BASELINE_INDEX, id=f"{prefix}{metric}", document={
        "metric": metric, "mean": mean, "std": max(std, 1e-9),
        "scope": prefix, "computed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    })


def put_status(es, day: str, run: str, prefix: str, status: str = "SUCCESS") -> None:
    es.index(index=STATUS_INDEX, id=f"{prefix}{day}", document={
        "day": day, "run": run, "status": status, "scope": prefix,
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
    })


def seed(es, days: int = 14, prefix: str | None = None,
         rows_per_day: int = 10000) -> dict[str, Any]:
    """Seed `days` healthy partitions ending yesterday; return what was written."""
    prefix = prefix or index_prefix()
    healthy_days = day_strings(days)
    null_rates: list[float] = []
    avgs: list[float] = []
    distincts: list[float] = []

    for i, day in enumerate(healthy_days):
        idx = partition(day, prefix)
        rows = make_healthy_rows(day, rows_per_day)
        recreate_index(es, idx, SALES_PROPERTIES)
        bulk_rows(es, idx, rows)
        s = local_stats(rows)
        null_rates.append(s["null_rate"])
        avgs.append(s["avg_amount"])
        distincts.append(s["distinct_sku"])
        put_status(es, day, f"#{4470 - (len(healthy_days) - 1 - i)}", prefix)
        print(f"[seed] {idx}: {len(rows)} rows  null_rate={s['null_rate']:.4f}  "
              f"avg_amount={s['avg_amount']:.2f}  distinct_sku={int(s['distinct_sku'])}")

    # Floor std to a realistic measurement-noise level so z-scores read believably.
    put_baseline(es, "null_rate", statistics.mean(null_rates),
                 max(statistics.pstdev(null_rates), 0.025), prefix)
    put_baseline(es, "avg_amount", statistics.mean(avgs),
                 max(statistics.pstdev(avgs), 0.5), prefix)
    put_baseline(es, "distinct_sku", statistics.mean(distincts),
                 max(statistics.pstdev(distincts), 5), prefix)
    es.indices.refresh(index=f"{BASELINE_INDEX},{STATUS_INDEX}")

    yesterday = healthy_days[-1]
    set_alias(es, config.ALIAS, partition(yesterday, prefix))
    print(f"[seed] baselines -> {BASELINE_INDEX}; status docs -> {STATUS_INDEX}")
    print(f"[seed] alias {config.ALIAS} -> {partition(yesterday, prefix)}")
    return {"days": healthy_days, "yesterday": yesterday, "prefix": prefix,
            "yesterday_index": partition(yesterday, prefix)}


def main() -> None:
    if config.MODE != "real":
        print("MODE=mock: run `python scripts/run_demo.py` instead (no seeding needed).")
        return
    es = connect()
    info = es.info()
    print(f"[seed] connected: {info['cluster_name']} (es {info['version']['number']})")
    seed(es, days=int(os.getenv("SILENTBREAK_SEED_DAYS", "14")))
    print("[seed] done.")


if __name__ == "__main__":
    main()
