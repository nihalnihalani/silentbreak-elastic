"""Reset the real-mode world: delete every SilentBreak index and the alias.

Removes sales partitions, quarantine indices, baselines, status docs, and
incidents so `make seed` starts from a clean slate. Mock mode has no state.

Importable API (used by scripts/smoke_real.py):
    reset(es, prefix=None) -> list[str]   # the index names deleted
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from seed_baseline import BASELINE_INDEX, STATUS_INDEX, connect, index_prefix

QUARANTINE_PATTERN = config.quarantine_index("*")


def _resolve(es, pattern: str) -> list[str]:
    """Expand a wildcard to concrete index names (wildcard deletes can be disabled)."""
    from elasticsearch import NotFoundError
    try:
        return sorted(es.indices.get(index=pattern).keys())
    except NotFoundError:
        return []


def reset(es, prefix: str | None = None, include_system: bool = True) -> list[str]:
    prefix = prefix or index_prefix()
    patterns = [f"{prefix}*", QUARANTINE_PATTERN]
    if include_system:
        patterns += [BASELINE_INDEX, STATUS_INDEX, config.INCIDENT_INDEX]
    deleted: list[str] = []
    for pattern in patterns:
        for name in _resolve(es, pattern):
            es.options(ignore_status=404).indices.delete(index=name)
            deleted.append(name)
    return deleted


def main() -> None:
    if config.MODE != "real":
        print("MODE=mock: nothing to reset (mock state lives in-memory).")
        return
    es = connect()
    deleted = reset(es)
    if deleted:
        for name in deleted:
            print(f"[reset] deleted {name}")
    else:
        print("[reset] nothing to delete — already clean.")
    print(f"[reset] done ({len(deleted)} indices removed).")


if __name__ == "__main__":
    main()
