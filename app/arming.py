"""Server-side auto-arm for the real-mode hosted demo.

A judge who clicks RUN EXAMINATION must ALWAYS get the full examination, even
after a previous remediation consumed the poison (the alias is then on
last-known-good and the world looks healthy). Before each real-mode run we
check whether the world is in the poisoned-demo state and, if not, (re)arm it
by reusing the same write-side setup the CLI uses: reset -> seed -> inject.

Re-arming is destructive to the sales/quarantine/baseline/status world but
DELIBERATELY preserves `silentbreak-incidents`: the EXAMINER RECALLS beat
depends on prior incidents surviving across runs. Arming runs INSIDE the run
lifecycle, behind the singleton run lock, so two concurrent /api/run cannot
double-seed.

The CLI scripts (scripts/seed_baseline.py, inject_drift.py, reset_world.py)
keep their `__main__` entrypoints; this module imports their functions.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from typing import Any, Awaitable, Callable

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import config

Emit = Callable[[str, dict], Awaitable[None]]

# Days of healthy baseline to land when arming (same default as `make seed`).
ARM_SEED_DAYS = int(os.getenv("SILENTBREAK_SEED_DAYS", "14"))


def _today() -> str:
    return dt.date.today().isoformat()


def is_armed(es) -> bool:
    """True iff the world is already in the poisoned-demo state for TODAY.

    Armed means: the consumer alias points at today's partition, that partition
    carries the corruption shape (`gross_amount`, no `amount` field), and a
    SUCCESS status doc exists for today (the green lie). Any other state — a
    healthy alias after a prior remediation, yesterday's leftover poison, or a
    blank cluster — is treated as NOT armed so the run reseeds.
    """
    from seed_baseline import partition

    today = _today()
    today_index = partition(today)
    try:
        if _alias_target(es, config.ALIAS) != today_index:
            return False
        if not es.indices.exists(index=today_index):
            return False
        fields = _field_names(es, today_index)
        if config.REVENUE_FIELD in fields or "gross_amount" not in fields:
            return False
        status_id = f"{config.index_prefix()}{today}"
        return bool(es.exists(index=config.STATUS_INDEX, id=status_id))
    except Exception:
        # Any probe error => can't prove armed => reseed (idempotent, safe).
        return False


def arm(es) -> dict[str, Any]:
    """(Re)create the poisoned-demo world for today; preserve incident memory.

    Resets sales partitions, quarantine indices, baselines, and status docs
    (NOT incidents), reseeds healthy baseline days, then injects today's silent
    drift and flips the alias onto it. Returns the inject summary.
    """
    from inject_drift import inject
    from reset_world import reset
    from seed_baseline import seed

    # include_system=True wipes baselines + status (stale baselines would skew
    # z-scores) but reset_world also deletes incidents under include_system, so
    # arm never calls reset that way; we reset only sales + quarantine + the
    # baseline/status docs by hand, leaving silentbreak-incidents untouched.
    reset(es, include_system=False)  # sales partitions + quarantine indices
    _clear_world_scoped(es, config.BASELINE_INDEX)
    _clear_world_scoped(es, config.STATUS_INDEX)

    seed(es, days=ARM_SEED_DAYS)
    return inject(es)


# --- internals (defensive direct-ES reads; arm runs write-side, no MCP) ---
def _alias_target(es, alias: str) -> str | None:
    resp = es.options(ignore_status=404).indices.get_alias(name=alias)
    keys = [k for k in dict(resp).keys() if not str(k).startswith("error")]
    return sorted(keys)[0] if keys else None


def _field_names(es, index: str) -> set[str]:
    raw = es.indices.get_mapping(index=index)
    for body in dict(raw).values():
        props = (body.get("mappings", {}) or {}).get("properties", {})
        if props:
            return set(props.keys())
    return set()


def _clear_world_scoped(es, index: str) -> None:
    """Delete only this world's docs (scope == current prefix) from a shared
    system index, so a smoke world's baselines/status are never collateral."""
    try:
        es.delete_by_query(
            index=index,
            query={"term": {"scope": config.index_prefix()}},
            refresh=True,
            ignore_unavailable=True,
            conflicts="proceed",
        )
    except Exception:
        pass
