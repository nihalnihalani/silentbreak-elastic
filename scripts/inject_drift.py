"""Inject the silent schema drift into today's partition (the demo villain).

Simulates the "Vertex AI SDK migration commit": upstream renamed `amount` to
`gross_amount`, so today's partition lands with revenue under the wrong field
name while the pipeline status doc still says SUCCESS, and the consumer alias
is flipped onto the poisoned index. Mock users get this automatically inside
run_demo.py; this script is for the real-mode live demo.

Importable API (used by scripts/smoke_real.py):
    inject(es, prefix=None, day=None) -> dict
"""
from __future__ import annotations

import datetime as dt
import os
import random
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from seed_baseline import (
    CATALOG, SALES_PROPERTIES, bulk_rows, connect, index_prefix, partition,
    put_status, recreate_index, set_alias,
)

CORRUPT_PROPERTIES: dict[str, Any] = {
    k: v for k, v in SALES_PROPERTIES.items() if k != config.REVENUE_FIELD
}
CORRUPT_PROPERTIES["gross_amount"] = {"type": "double"}


def make_corrupted_rows(day: str, n: int = 10000) -> list[dict[str, Any]]:
    """Same orders, but revenue lands as `gross_amount` and `amount` never exists.
    The loader still maps `amount`, so downstream revenue is silently null."""
    rng = random.Random(f"drift-{day}")
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
            "gross_amount": round(qty * item["unit_price"] * (1 - discount), 2),
            "customer_id": f"C-{rng.randrange(1, 40000):05d}",
            "channel": rng.choices(["web", "mobile", "store"], weights=[55, 35, 10])[0],
        })
    return rows


def inject(es, prefix: str | None = None, day: str | None = None,
           rows_per_day: int = 10000) -> dict[str, Any]:
    """Land the corrupted partition, flip the alias onto it, report SUCCESS."""
    prefix = prefix or index_prefix()
    day = day or dt.date.today().isoformat()
    idx = partition(day, prefix)

    rows = make_corrupted_rows(day, rows_per_day)
    recreate_index(es, idx, CORRUPT_PROPERTIES)
    bulk_rows(es, idx, rows)
    set_alias(es, config.ALIAS, idx)            # consumers now read the poison
    put_status(es, day, "#4471", prefix)        # ...and the pipeline says SUCCESS

    print(f"[inject] {idx}: {len(rows)} rows landed with `gross_amount` "
          f"(no `{config.REVENUE_FIELD}` field — loader nulls revenue)")
    print(f"[inject] alias {config.ALIAS} -> {idx}")
    print(f"[inject] status doc: run #4471 SUCCESS ✓  (the lie)")
    return {"day": day, "index": idx, "rows": len(rows), "prefix": prefix}


def main() -> None:
    if config.MODE != "real":
        print("MODE=mock: drift is injected inside `python scripts/run_demo.py`.")
        return
    es = connect()
    inject(es)
    print("[inject] done. The pipeline is green; the data is not.")


if __name__ == "__main__":
    main()
