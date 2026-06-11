"""Guardian — the actuator (the consequential, token-gated action).

Quarantines the corrupt rows, flips the alias off the poisoned index so every
downstream consumer instantly stops reading bad data, then re-runs the
downstream revenue query THROUGH the alias to verify the fix. Every step is a
real, reversible Elastic state change — and none of it happens without a valid
single-use token from the ApprovalRegistry (the HITL gate).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import config


class ApprovalDenied(Exception):
    """Raised when the approval token is missing, expired, reused, or wrong."""


@dataclass
class GuardianResult:
    quarantined: int
    q_index: str
    alias: str
    alias_was: str | None
    alias_now: str
    verify: dict
    actions: list = field(default_factory=list)
    repaired_index: str = ""
    repaired_docs: int = 0
    repair_rename: str = ""


def build_plan(today_index: str, yesterday_index: str, day: str,
               repair_from: str = "gross_amount") -> list[str]:
    """The exact action plan shown to the operator before the APPROVE stamp.
    One approval covers everything below — including the recovery reindex."""
    rf = config.REVENUE_FIELD
    return [
        f"reindex rows missing `{rf}` from {today_index} into {config.quarantine_index(day)}",
        f"atomic update_aliases: flip {config.ALIAS} from {today_index} to {yesterday_index} (last known good)",
        (f"recovery reindex: {config.quarantine_index(day)} -> {today_index}-repaired "
         f"with painless rename {repair_from}->{rf} (staged for validation; alias not re-pointed)"),
        (f"trade-off: flipping to {yesterday_index} serves stale-but-correct data "
         "until the repaired index is validated and re-pointed (or re-ingestion lands)"),
        f"verify downstream: re-run the revenue ES|QL through {config.ALIAS}",
        "reversible: one atomic update_aliases flips it back",
    ]


def act(client, today_index: str, yesterday_index: str, day: str, *,
        registry=None, run_id: str | None = None, token: str | None = None,
        repair_from: str = "gross_amount",
        on_action: Callable[[dict], None] | None = None) -> GuardianResult:
    """Execute the plan. If a registry is supplied, the token MUST consume cleanly
    before any write happens (both engines and the CLI demo pass through this)."""
    if registry is not None and not registry.consume(run_id, token):
        raise ApprovalDenied("approval token invalid, expired, or already used — no writes performed")

    rf = config.REVENUE_FIELD
    q_index = config.quarantine_index(day)

    # 1) quarantine the poisoned rows (reindex aside; source cleaned)
    n = client.quarantine_missing_field(today_index, q_index, rf)
    if on_action:
        on_action({"step": "quarantine", "detail": f"moved {n} corrupt rows -> {q_index}",
                   "rows": n})

    # 2) THE VISIBLE ACTION: one atomic alias flip to the last-known-good index
    alias_was = client.update_alias(config.ALIAS, yesterday_index)
    if on_action:
        on_action({"step": "alias_flip",
                   "detail": f"update_aliases: {config.ALIAS} {alias_was} -> {yesterday_index}",
                   "alias": config.ALIAS, "from": alias_was, "to": yesterday_index})

    # 3) REPAIR ("who fixes the data"): recovery reindex of the quarantined
    #    rows into {today}-repaired, renaming the mutated field back. Covered
    #    by the SAME approved plan/token — no second approval, no alias change:
    #    the alias stays on last-known-good (stale-but-correct) until the
    #    repaired index is validated and re-pointed by a human.
    repaired_index = f"{today_index}-repaired"
    repair_rename = f"{repair_from}->{rf}"
    repaired_docs = client.repair_reindex(q_index, repaired_index, repair_from, rf)
    if on_action:
        on_action({"step": "repair", "repaired_index": repaired_index,
                   "docs": repaired_docs, "rename": repair_rename,
                   "detail": (f"recovery reindex {q_index} -> {repaired_index} "
                              f"({repaired_docs} docs, {repair_rename})")})

    # 4) verify downstream: the same revenue stats, read THROUGH the alias
    verify = verify_downstream(client)

    return GuardianResult(
        quarantined=n, q_index=q_index, alias=config.ALIAS,
        alias_was=alias_was, alias_now=yesterday_index, verify=verify,
        repaired_index=repaired_index, repaired_docs=repaired_docs,
        repair_rename=repair_rename,
        actions=[
            f"reindex {n} corrupt rows -> {q_index}",
            f"update_aliases: {config.ALIAS} {alias_was} -> {yesterday_index}",
            (f"recovery reindex: {repaired_docs} rows {repair_rename} -> {repaired_index} "
             f"(staged for validation; alias stays on {yesterday_index})"),
            (f"verify via {config.ALIAS}: null_rate={verify['null_rate']:.3f} "
             f"row_count={verify['row_count']} avg_amount={verify['avg_amount']:.2f}"),
        ],
    )


def verify_downstream(client) -> dict:
    """Re-run the downstream revenue query through the alias."""
    stats = client.esql_stats(config.ALIAS)
    return {
        "alias": config.ALIAS,
        "target": client.alias_target(config.ALIAS),
        "row_count": stats["row_count"],
        "null_rate": stats["null_rate"],
        "avg_amount": stats["avg_amount"],
    }


def reverse(client, to_index: str) -> str | None:
    """Flip the alias back (one atomic update_aliases). Returns previous target."""
    return client.update_alias(config.ALIAS, to_index)


def rollback(client, alias_was: str | None) -> None:
    """Reversibility — flip the alias back (proves it is a real state change)."""
    if alias_was:
        client.update_alias(config.ALIAS, alias_was)
